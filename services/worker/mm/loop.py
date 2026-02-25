"""Main market-making loop: scan -> filter -> quote -> reconcile -> repeat."""

import asyncio
import json
import logging
import time as _time
from datetime import datetime, timezone

from config import AppConfig
from executor.client import PolymarketClient
from mm.scanner import MarketScanner
from mm.quoter import Quoter
from mm.inventory import InventoryManager
from mm.engine import (
    compute_weighted_mid,
    compute_dynamic_delta,
    compute_skew,
    compute_bid_ask,
    compute_quote_size,
    should_requote,
    round_to_tick,
    TICK_SIZE,
    VolTracker,
    StaleTracker,
)
from mm.as_engine import compute_as_quotes, estimate_time_remaining, KappaEstimator, ASParams
from mm.state import QuotePair, OrderState
from mm.arbitrage import scan_for_arbitrage, execute_arbitrage
from db import store

logger = logging.getLogger(__name__)


def _clamp_price(price: float) -> float:
    return max(0.01, min(0.99, round(price, 2)))


def _sanitize_post_only_quotes(
    bid: float,
    ask: float,
    best_bid: float,
    best_ask: float,
) -> tuple[float, float]:
    """Keep quotes safely maker-only relative to current top-of-book."""
    bid = _clamp_price(round_to_tick(bid))
    ask = _clamp_price(round_to_tick(ask))

    if best_ask > 0:
        bid_cap = _clamp_price(round_to_tick(best_ask - TICK_SIZE))
        bid = min(bid, bid_cap)
    if best_bid > 0:
        ask_floor = _clamp_price(round_to_tick(best_bid + TICK_SIZE))
        ask = max(ask, ask_floor)

    if bid >= ask:
        if best_bid > 0 and best_ask > best_bid:
            bid = _clamp_price(round_to_tick(best_bid))
            ask = _clamp_price(round_to_tick(best_ask))
            if bid >= ask:
                bid = _clamp_price(round_to_tick(ask - TICK_SIZE))
        else:
            mid = (bid + ask) / 2
            bid = _clamp_price(round_to_tick(mid - TICK_SIZE))
            ask = _clamp_price(round_to_tick(mid + TICK_SIZE))
            if bid >= ask:
                ask = _clamp_price(round_to_tick(bid + TICK_SIZE))

    return bid, ask


def _is_cross_reject_failure(failure: dict | None) -> bool:
    if not failure:
        return False
    for side_key in ("bid_error", "ask_error"):
        err = failure.get(side_key)
        if isinstance(err, dict) and err.get("code") == "post_only_cross":
            return True
    return False


def _cooldown_seconds_for_streak(
    streak: int,
    threshold: int,
    base_seconds: int,
    max_seconds: int,
) -> int:
    threshold = max(1, threshold)
    if streak < threshold:
        return 0
    level = 1 + max(0, (streak - threshold) // threshold)
    return min(max_seconds, base_seconds * level)


def _compute_locked_capital(active_quotes: dict[str, QuotePair]) -> float:
    """Compute USDC.e locked in live open orders (BID only).

    ASK orders sell tokens we already hold — they don't lock USDC.
    """
    locked = 0.0
    for pair in active_quotes.values():
        if pair.bid_order_id and pair.bid_state in (OrderState.LIVE, OrderState.PARTIAL, OrderState.NEW):
            locked += pair.bid_size * pair.bid_price  # BUY locks shares × price
    return locked


def _should_cancel_for_requote(
    pair: QuotePair,
    new_mid: float,
    mm_cfg,
) -> bool:
    """Anti-churn: only requote if price moved enough AND quote is old enough."""
    # Never requote if quote is too young
    if pair.age_seconds() < mm_cfg.mm_min_quote_lifetime_seconds:
        return False
    # Check if mid moved enough
    return should_requote(pair, new_mid, mm_cfg.mm_requote_threshold)


async def _rebuild_active_quotes_from_clob(
    client: PolymarketClient, inventory: InventoryManager,
) -> dict[str, QuotePair]:
    """Rebuild active_quotes dict from CLOB open orders after restart.

    Matches CLOB open orders against DB quotes. Cancels orphan orders
    (present on CLOB but not in our DB).
    """
    active_quotes: dict[str, QuotePair] = {}
    try:
        live_orders = await asyncio.to_thread(client.get_open_orders)
        if not live_orders:
            return active_quotes

        db_quotes = await store.get_active_mm_quotes()
        # Index DB quotes by order ID for fast lookup
        db_by_bid = {}
        db_by_ask = {}
        for q in db_quotes:
            if q.get("bid_order_id"):
                db_by_bid[q["bid_order_id"]] = q
            if q.get("ask_order_id"):
                db_by_ask[q["ask_order_id"]] = q

        orphan_ids = []
        recovered = {}  # market_id -> QuotePair

        for order in live_orders:
            oid = order.get("id") or order.get("order_id")
            if not oid:
                continue

            db_q = db_by_bid.get(oid) or db_by_ask.get(oid)
            if db_q is None:
                orphan_ids.append(oid)
                continue

            market_id = db_q["market_id"]
            if market_id not in recovered:
                recovered[market_id] = QuotePair(
                    market_id=market_id,
                    token_id=db_q.get("token_id", ""),
                    bid_price=float(db_q.get("bid_price", 0)),
                    ask_price=float(db_q.get("ask_price", 0)),
                    size=float(db_q.get("size", 0)),
                    db_id=db_q.get("id"),
                    quoted_mid=float(db_q.get("mid_price", 0)),
                )
            pair = recovered[market_id]
            if oid == db_q.get("bid_order_id"):
                pair.bid_order_id = oid
                pair.bid_state = OrderState.LIVE
            elif oid == db_q.get("ask_order_id"):
                pair.ask_order_id = oid
                pair.ask_state = OrderState.LIVE

        # Cancel orphan orders
        for oid in orphan_ids:
            try:
                await asyncio.to_thread(client.cancel_order, oid)
            except Exception:
                pass
        if orphan_ids:
            logger.warning(f"CLOB reconcil: cancelled {len(orphan_ids)} orphan orders")

        active_quotes = recovered
        if recovered:
            logger.info(f"CLOB reconcil: recovered {len(recovered)} active quote pairs")

    except Exception as e:
        logger.warning(f"CLOB reconciliation failed: {e}")

    return active_quotes


async def mm_loop(config: AppConfig, client: PolymarketClient, risk=None):
    """Fast market-making loop. Runs every mm_cycle_seconds.

    Cycle:
    1. Scan markets (cached, refreshed every mm_scanner_refresh_minutes)
    2. Reconcile existing quotes (detect fills, update inventory)
    3. For each active market: fetch mid, compute delta, compute bid/ask
    4. Place/update quotes
    5. Persist to DB
    6. Sleep
    """
    mm_cfg = config.mm
    if not mm_cfg.mm_enabled:
        logger.info("MM loop disabled")
        return

    scanner = MarketScanner(mm_cfg, config.polymarket)
    quoter = Quoter(client, mm_cfg)
    inventory = InventoryManager(mm_cfg)
    vol_tracker = VolTracker(halflife=20)
    stale_tracker = StaleTracker(threshold_seconds=mm_cfg.mm_stale_threshold_seconds)
    kappa_estimator = KappaEstimator(
        window_minutes=mm_cfg.mm_as_kappa_window_minutes,
        default_kappa=mm_cfg.mm_as_kappa_default,
    )
    as_params = ASParams(
        gamma_base=mm_cfg.mm_as_gamma_base,
        gamma_alpha=mm_cfg.mm_as_gamma_alpha,
        kappa=mm_cfg.mm_as_kappa_default,
        min_spread_pts=mm_cfg.mm_delta_min * 2,  # min spread = 2x min delta
        max_spread_pts=mm_cfg.mm_max_spread_pts,
    )

    # Scorer: qualitative market evaluation via Sonnet (opt-in)
    scorer = None
    if mm_cfg.mm_scorer_enabled:
        from mm.scorer import MarketScorer
        scorer = MarketScorer(mm_cfg, config.anthropic)
        logger.info("MM scorer enabled (Sonnet, min_score=%.1f)", mm_cfg.mm_scorer_min_score)

    # Load existing inventory from DB
    try:
        db_inv = await store.get_mm_inventory()
        inventory.load_from_db(db_inv)
    except Exception as e:
        logger.warning(f"Failed to load inventory from DB: {e}")

    # Rebuild active quotes from CLOB (post-restart reconciliation)
    active_quotes = await _rebuild_active_quotes_from_clob(client, inventory)
    market_cross_reject_streak: dict[str, int] = {}
    market_cooldown_until: dict[str, float] = {}
    split_failed_markets: set[str] = set()  # markets where split_position failed
    market_error_count: dict[str, int] = {}
    market_circuit_cooldown: dict[str, float] = {}  # market_id -> cooldown_until (monotonic)

    cycle = 0
    logger.info(f"MM loop started (cycle={mm_cfg.mm_cycle_seconds}s)")

    while True:
        try:
            cycle += 1

            diag = cycle <= 3 or cycle % 100 == 1  # diagnostic logging

            # Risk gate: skip cycle if trading is paused
            if risk and risk.is_paused:
                if diag:
                    logger.info("MM loop: trading paused by risk manager")
                await asyncio.sleep(mm_cfg.mm_cycle_seconds)
                continue

            cycle_start = asyncio.get_event_loop().time()
            cooldown_threshold = max(1, mm_cfg.mm_cross_reject_threshold)

            # Reduce mode: halve capacity when risk manager signals "reduce"
            effective_max_markets = mm_cfg.mm_max_markets
            effective_quote_size = mm_cfg.mm_quote_size_usd
            if risk and hasattr(risk, 'risk_mode') and risk.risk_mode == "reduce":
                effective_max_markets = max(1, mm_cfg.mm_max_markets // 2)
                effective_quote_size = mm_cfg.mm_quote_size_usd / 2.0
                if diag:
                    logger.warning("MM loop: running in REDUCE mode (halved capacity)")

            def register_quote_failure(market_id: str, failure: dict | None) -> None:
                # Circuit breaker: count all failures
                err_count = market_error_count.get(market_id, 0) + 1
                market_error_count[market_id] = err_count
                if err_count >= mm_cfg.mm_circuit_breaker_threshold:
                    market_circuit_cooldown[market_id] = _time.monotonic() + mm_cfg.mm_circuit_breaker_cooldown
                    logger.warning(
                        f"Circuit breaker: {market_id[:16]} paused for {mm_cfg.mm_circuit_breaker_cooldown}s "
                        f"after {err_count} errors"
                    )

                if not _is_cross_reject_failure(failure):
                    market_cross_reject_streak.pop(market_id, None)
                    return
                streak = market_cross_reject_streak.get(market_id, 0) + 1
                market_cross_reject_streak[market_id] = streak
                cooldown_seconds = _cooldown_seconds_for_streak(
                    streak=streak,
                    threshold=cooldown_threshold,
                    base_seconds=mm_cfg.mm_cross_cooldown_seconds,
                    max_seconds=mm_cfg.mm_cross_cooldown_max_seconds,
                )
                if cooldown_seconds <= 0:
                    return
                cooldown_end = cycle_start + cooldown_seconds
                market_cooldown_until[market_id] = max(
                    market_cooldown_until.get(market_id, 0.0),
                    cooldown_end,
                )
                if streak % cooldown_threshold == 0:
                    logger.warning(
                        f"MM cooldown set on market {market_id}: "
                        f"{streak} cross rejects, cooldown={cooldown_seconds}s"
                    )

            # 1. Scan markets (uses cache internally)
            markets = await scanner.scan(client)

            if not markets:
                logger.debug("No MM-viable markets found")
                await asyncio.sleep(mm_cfg.mm_cycle_seconds)
                continue

            # Reduce mode: limit markets
            if effective_max_markets < mm_cfg.mm_max_markets:
                markets = markets[:effective_max_markets]

            # 1b. Score and filter markets via Sonnet (if enabled)
            pre_score_count = len(markets)
            if scorer:
                markets = await scorer.score_and_filter(markets)
                if not markets:
                    logger.debug("No markets passed scorer filter")
                    await asyncio.sleep(mm_cfg.mm_cycle_seconds)
                    continue

            active_market_ids = {m["market_id"] for m in markets}

            # 2. Cancel quotes for markets no longer in scan
            for mid in list(active_quotes.keys()):
                if mid not in active_market_ids:
                    pair = active_quotes.pop(mid)
                    await asyncio.to_thread(quoter.cancel_quote_pair, pair)
                    if pair.db_id:
                        await store.update_mm_quote_status(pair.db_id, "cancelled")
                    market_cross_reject_streak.pop(mid, None)
                    market_cooldown_until.pop(mid, None)
                    vol_tracker.reset(mid)
                    stale_tracker.reset(mid)
                    logger.info(f"Cancelled quotes for removed market {mid[:16]}")

            # 3. Reconcile existing quotes
            for market_id, pair in list(active_quotes.items()):
                fills = await asyncio.to_thread(quoter.reconcile_quote, pair)

                for fill in fills:
                    # Update inventory
                    inventory.process_fill(
                        market_id=market_id,
                        token_id=pair.token_id,
                        side=fill["side"],
                        price=fill["price"],
                        size=fill["size_matched"],
                    )
                    # Persist fill
                    mid_at_fill = pair.mid
                    await store.insert_mm_fill({
                        "quote_id": pair.db_id,
                        "order_id": fill["order_id"],
                        "side": fill["side"],
                        "price": fill["price"],
                        "size": fill["size_matched"],
                        "fee": fill.get("meta", {}).get("fees_paid", 0),
                        "mid_at_fill": mid_at_fill,
                    })
                    # Persist inventory
                    await store.upsert_mm_inventory(
                        market_id, pair.token_id,
                        fill["size_matched"] if fill["side"] == "BUY" else -fill["size_matched"],
                        fill["price"],
                    )

                    logger.info(
                        f"MM fill: {fill['side']} {fill['size_matched']} @ {fill['price']:.2f} "
                        f"on {market_id[:16]}"
                    )
                    kappa_estimator.record_fill(market_id)

                # Remove terminal quotes
                if pair.is_terminal:
                    active_quotes.pop(market_id, None)
                    if pair.db_id:
                        await store.update_mm_quote_status(pair.db_id, "filled")

            # 3b. Read guard kill list and evict killed markets
            try:
                guard_kill_json = await store.get_bot_status_field("guard_kill_list")
                guard_kill_set = set(json.loads(guard_kill_json)) if guard_kill_json else set()
            except Exception:
                guard_kill_set = set()

            for mid in list(active_quotes.keys()):
                if mid in guard_kill_set:
                    pair = active_quotes.pop(mid)
                    await asyncio.to_thread(quoter.cancel_quote_pair, pair)
                    if pair.db_id:
                        await store.update_mm_quote_status(pair.db_id, "killed_by_guard")
                    logger.warning(f"MM: evicted killed market {mid[:16]} per guard")

            # 4. Check available balance (on-chain = source of truth)
            total_exposure = inventory.get_total_exposure()
            available_balance = await asyncio.to_thread(client.get_onchain_balance)
            if available_balance is None:
                logger.warning("Could not fetch on-chain balance, skipping cycle")
                await asyncio.sleep(mm_cfg.mm_cycle_seconds)
                continue

            # Compute free capital after deducting locked orders
            locked_capital = _compute_locked_capital(active_quotes)
            free_capital = max(0.0, available_balance - locked_capital)

            # Dynamic per-market: distribute FREE capital across remaining market slots
            active_count = len(active_quotes)
            remaining_slots = max(1, effective_max_markets - active_count)
            max_per_market = free_capital / max(1, remaining_slots)

            # Global exposure check
            if risk:
                within_limit, exp_pct = await risk.check_global_exposure(available_balance)
                if not within_limit:
                    if diag:
                        logger.warning(f"MM: global exposure {exp_pct:.1f}% exceeds limit")
                    await asyncio.sleep(mm_cfg.mm_cycle_seconds)
                    continue

            if free_capital < effective_quote_size:
                if diag:
                    logger.warning(
                        f"Free capital ${free_capital:.2f} too low to quote "
                        f"(on-chain=${available_balance:.2f}, locked=${locked_capital:.2f})"
                    )
                await asyncio.sleep(mm_cfg.mm_cycle_seconds)
                continue

            # 5. Quote each market (two-sided with split/merge)
            capital_committed = 0.0
            for market in markets:
                market_id = market["market_id"]
                token_id = market["token_id"]
                no_token_id = market.get("no_token_id")
                condition_id = market.get("condition_id")

                # Skip markets killed by guard
                if market_id in guard_kill_set:
                    continue

                cooldown_end = market_cooldown_until.get(market_id)
                if cooldown_end and cycle_start < cooldown_end:
                    if cycle % 10 == 0:
                        remaining = int(cooldown_end - cycle_start)
                        logger.info(
                            f"MM cooldown active for {market_id}: skip {remaining}s remaining"
                        )
                    continue
                if cooldown_end and cycle_start >= cooldown_end:
                    market_cooldown_until.pop(market_id, None)
                    market_cross_reject_streak.pop(market_id, None)

                # Circuit breaker: skip markets with too many consecutive errors
                cb_until = market_circuit_cooldown.get(market_id, 0)
                if cb_until and _time.monotonic() < cb_until:
                    continue
                elif cb_until:
                    market_circuit_cooldown.pop(market_id, None)
                    market_error_count.pop(market_id, None)

                inventory_snapshot = inventory.get(market_id)
                net_position = inventory_snapshot.net_position  # YES tokens
                no_position = inventory_snapshot.no_position    # NO tokens

                # Get current book data
                book_summary = await asyncio.to_thread(
                    client.get_book_summary, token_id
                )
                if not book_summary or book_summary.get("mid") is None:
                    if diag:
                        logger.warning(
                            f"MM skip {market_id[:16]}: book_summary={'None' if not book_summary else 'mid=None'}"
                        )
                    continue

                mid = compute_weighted_mid(book_summary) or book_summary["mid"]

                # Skip extreme-probability markets (scanner cache may be stale)
                if mid < 0.02 or mid > 0.98:
                    if diag:
                        logger.info(f"MM skip {market_id[:16]}: extreme mid={mid:.4f}")
                    continue

                imbalance = book_summary.get("imbalance", 0)
                spread_pts = book_summary.get("spread", 0) * 100
                best_bid = float(book_summary.get("best_bid", 0) or 0)
                best_ask = float(book_summary.get("best_ask", 0) or 0)

                # Update stale tracker
                stale_tracker.update_if_changed(market_id, mid)

                # Check capacity using mid as fallback
                at_capacity = inventory.is_at_capacity(market_id, max_per_market, mid)

                # Update vol tracker with new mid observation
                tracked_vol = vol_tracker.update(market_id, mid)

                # ── Pricing engine selection ──
                stale = stale_tracker.get_staleness(market_id)

                if mm_cfg.mm_pricing_engine == "as":
                    # Avellaneda-Stoikov pricing
                    days_to_resolution = market.get("days_to_resolution", 30)
                    T = estimate_time_remaining(days_to_resolution)
                    kappa = kappa_estimator.get_kappa(market_id)
                    as_params.kappa = kappa
                    as_params.T = T
                    avg_entry = inventory_snapshot.avg_entry_price
                    vol_for_as = tracked_vol if tracked_vol > 0 else max(spread_pts * 0.5, 1.0)
                    bid, ask = compute_as_quotes(
                        mid=mid,
                        inventory=net_position,
                        max_inventory=max_per_market / mid if mid > 0 else 100,
                        vol_pts=vol_for_as,
                        T=T,
                        params=as_params,
                        avg_entry_price=avg_entry,
                    )
                else:
                    # Legacy pricing
                    vol_proxy = max(spread_pts * 0.5, 1.0)
                    delta = compute_dynamic_delta(
                        vol_short=vol_proxy,
                        book_imbalance=imbalance,
                        stale_risk=stale,
                        delta_min=mm_cfg.mm_delta_min,
                        delta_max=mm_cfg.mm_delta_max,
                        tracked_vol=tracked_vol,
                    )
                    urgency = inventory.get_unwind_urgency(market_id)
                    skew_factor = mm_cfg.mm_inventory_skew_factor + urgency * 0.3
                    inv_skew = compute_skew(
                        inventory.get_skew_direction(market_id, max_per_market) * max_per_market,
                        max_per_market,
                        skew_factor=skew_factor,
                    )
                    bid, ask = compute_bid_ask(mid, delta, inv_skew)

                if risk:
                    ok, reason = risk.validate_mm_quote(bid, ask, mid, mm_cfg.mm_delta_max)
                    if not ok:
                        if diag:
                            logger.warning(
                                f"MM skip {market_id[:16]}: risk rejected — {reason} "
                                f"(bid={bid:.4f} ask={ask:.4f} mid={mid:.4f})"
                            )
                        continue

                if mm_cfg.mm_post_only:
                    bid, ask = _sanitize_post_only_quotes(bid, ask, best_bid, best_ask)

                min_shares = max(5.0, float(book_summary.get("min_order_size", 5.0) or 5.0))

                # ── Two-sided quoting logic ──
                # With split/merge: always place both sides if capital allows
                # BID = BUY YES at bid price
                # ASK = SELL YES at ask price (requires YES inventory from split or fills)
                place_bid = not at_capacity
                place_ask = net_position >= min_shares  # Can sell YES if we have them

                # If two-sided is enabled and we have no YES to sell,
                # try to split USDC into YES+NO tokens (skip markets that failed before)
                if (mm_cfg.mm_two_sided and mm_cfg.mm_use_split_merge
                        and not place_ask and condition_id
                        and market_id not in split_failed_markets
                        and free_capital - capital_committed >= mm_cfg.mm_split_size_usd):
                    split_amount = mm_cfg.mm_split_size_usd
                    logger.info(
                        f"Splitting ${split_amount:.2f} for two-sided quoting on {market_id[:16]}"
                    )
                    split_ok = await asyncio.to_thread(
                        client.split_position, condition_id, split_amount
                    )
                    if not split_ok:
                        split_failed_markets.add(market_id)
                        logger.warning(f"Split failed for {market_id[:16]}, will not retry until restart")
                    if split_ok and no_token_id:
                        inventory.process_split(
                            market_id, split_amount,
                            yes_token_id=token_id,
                            no_token_id=no_token_id,
                        )
                        capital_committed += split_amount
                        net_position = inventory.get(market_id).net_position
                        no_position = inventory.get(market_id).no_position
                        place_ask = net_position >= min_shares

                if not place_bid and not place_ask:
                    if diag:
                        logger.warning(
                            f"MM skip {market_id[:16]}: no sides to quote "
                            f"(at_capacity={at_capacity}, net_pos={net_position:.1f}, "
                            f"min_shares={min_shares})"
                        )
                    continue

                # Compute BID size
                bid_shares = 0.0
                if place_bid:
                    size = compute_quote_size(
                        capital=free_capital - capital_committed,
                        max_per_market=max_per_market,
                        current_inventory_usdc=abs(net_position) * mid,
                        max_inventory=max_per_market,
                        base_size_usd=effective_quote_size,
                    )
                    if size <= 0:
                        place_bid = False
                    else:
                        bid_shares = round(size / mid, 1) if mid > 0 else 0
                        if bid_shares < min_shares:
                            place_bid = False
                            bid_shares = 0

                # ASK size: sell from YES inventory
                ask_shares = 0.0
                if place_ask:
                    ask_shares = round(min(net_position, max_per_market / mid if mid > 0 else net_position), 1)
                    if ask_shares < min_shares:
                        place_ask = False
                        ask_shares = 0

                if not place_bid and not place_ask:
                    if diag:
                        logger.warning(
                            f"MM skip {market_id[:16]}: sizes too small "
                            f"(bid_shares={bid_shares:.1f}, ask_shares={ask_shares:.1f}, "
                            f"min_shares={min_shares})"
                        )
                    continue

                # Use larger of the two for the shared size field (backward compat)
                shares = max(bid_shares, ask_shares) if (bid_shares > 0 or ask_shares > 0) else 0
                if shares < min_shares:
                    if diag:
                        logger.warning(
                            f"MM skip {market_id[:16]}: shares={shares:.1f} < min_shares={min_shares}"
                        )
                    continue

                # Budget checks
                if place_bid and bid_shares > 0:
                    bid_cost = bid_shares * bid
                    if capital_committed + bid_cost > free_capital:
                        place_bid = False
                        if not place_ask:
                            continue

                # Check if we need to requote (with anti-churn)
                existing = active_quotes.get(market_id)
                # Clean zombie quotes (not active but not terminal — stuck in UNKNOWN)
                if existing and not existing.is_active and not existing.is_terminal:
                    await asyncio.to_thread(quoter.cancel_quote_pair, existing)
                    if existing.db_id:
                        await store.update_mm_quote_status(existing.db_id, "cancelled")
                    active_quotes.pop(market_id, None)
                    existing = None

                if existing and existing.is_active:
                    existing_has_ask = bool(
                        existing.ask_order_id
                        and existing.ask_state in (OrderState.NEW, OrderState.LIVE, OrderState.PARTIAL)
                    )
                    existing_has_bid = bool(
                        existing.bid_order_id
                        and existing.bid_state in (OrderState.NEW, OrderState.LIVE, OrderState.PARTIAL)
                    )
                    should_refresh_sides = (
                        place_ask != existing_has_ask
                        or place_bid != existing_has_bid
                    )

                    if _should_cancel_for_requote(existing, mid, mm_cfg) or should_refresh_sides:
                        new_pair = await asyncio.to_thread(
                            quoter.requote, existing, bid, ask, shares, place_ask, place_bid
                        )
                        if new_pair:
                            new_pair.bid_size = bid_shares
                            new_pair.ask_size = ask_shares
                            new_pair.no_token_id = no_token_id
                            new_pair.condition_id = condition_id
                            new_pair.quoted_mid = mid
                            db_id = await store.insert_mm_quote({
                                "market_id": market_id,
                                "token_id": token_id,
                                "bid_order_id": new_pair.bid_order_id,
                                "ask_order_id": new_pair.ask_order_id,
                                "bid_price": bid,
                                "ask_price": ask,
                                "mid_price": mid,
                                "size": shares,
                            })
                            new_pair.db_id = db_id
                            active_quotes[market_id] = new_pair
                            market_cross_reject_streak.pop(market_id, None)
                            market_error_count.pop(market_id, None)
                            market_cooldown_until.pop(market_id, None)
                            if existing.db_id:
                                await store.update_mm_quote_status(existing.db_id, "replaced")
                            if new_pair.bid_order_id:
                                capital_committed += bid_shares * bid
                        else:
                            register_quote_failure(market_id, quoter.get_last_quote_failure())
                else:
                    if diag:
                        logger.info(
                            f"MM diag {market_id[:16]}: placing quote "
                            f"bid={bid:.2f}x{bid_shares:.1f} ask={ask:.2f}x{ask_shares:.1f} "
                            f"place_bid={place_bid} place_ask={place_ask}"
                        )
                    pair = await asyncio.to_thread(
                        quoter.place_quote_pair, token_id, market_id, bid, ask, shares, place_ask, place_bid
                    )
                    if pair:
                        pair.bid_size = bid_shares
                        pair.ask_size = ask_shares
                        pair.no_token_id = no_token_id
                        pair.condition_id = condition_id
                        pair.quoted_mid = mid
                        db_id = await store.insert_mm_quote({
                            "market_id": market_id,
                            "token_id": token_id,
                            "bid_order_id": pair.bid_order_id,
                            "ask_order_id": pair.ask_order_id,
                            "bid_price": bid,
                            "ask_price": ask,
                            "mid_price": mid,
                            "size": shares,
                        })
                        pair.db_id = db_id
                        active_quotes[market_id] = pair
                        market_cross_reject_streak.pop(market_id, None)
                        market_error_count.pop(market_id, None)
                        market_cooldown_until.pop(market_id, None)
                        if pair.bid_order_id:
                            capital_committed += bid_shares * bid
                        logger.info(f"MM quote placed on {market_id[:16]}: bid={bid:.2f} ask={ask:.2f}")
                    else:
                        failure = quoter.get_last_quote_failure()
                        logger.warning(
                            f"MM quote FAILED for {market_id[:16]}: {failure}"
                        )
                        register_quote_failure(market_id, failure)

            # 5b. Periodic merge: convert YES+NO pairs back to USDC
            if mm_cfg.mm_use_split_merge and cycle % 6 == 0:  # Every ~60s
                for market_id_m in list(active_quotes.keys()):
                    merge_amount = inventory.get_merge_amount(market_id_m)
                    cond_id = active_quotes[market_id_m].condition_id
                    if merge_amount >= mm_cfg.mm_merge_threshold and cond_id:
                        logger.info(
                            f"Merging {merge_amount:.1f} pairs on {market_id_m[:16]}"
                        )
                        merge_ok = await asyncio.to_thread(
                            client.merge_positions, cond_id, merge_amount
                        )
                        if merge_ok:
                            inventory.process_merge(market_id_m, merge_amount)

            # 5c. Complete-set arbitrage scan (every ~30s = 3 cycles)
            if mm_cfg.mm_arb_enabled and mm_cfg.mm_use_split_merge and cycle % 3 == 0:
                for market in markets:
                    m_id = market["market_id"]
                    yes_tid = market.get("token_id", "")
                    no_tid = market.get("no_token_id")
                    cond_id = market.get("condition_id")
                    if not no_tid or not cond_id:
                        continue

                    try:
                        yes_book = await asyncio.to_thread(
                            client.get_book_summary, yes_tid
                        )
                        no_book = await asyncio.to_thread(
                            client.get_book_summary, no_tid
                        )
                        opp = scan_for_arbitrage(
                            yes_book=yes_book,
                            no_book=no_book,
                            market_id=m_id,
                            condition_id=cond_id,
                            yes_token_id=yes_tid,
                            no_token_id=no_tid,
                            gas_cost_usd=mm_cfg.mm_arb_gas_cost_usd,
                            min_profit_pct=mm_cfg.mm_arb_min_profit_pct,
                        )
                        if opp:
                            arb_result = await execute_arbitrage(
                                opp, client, inventory, mm_cfg,
                            )
                            if arb_result.get("success"):
                                await store.insert_mm_fill({
                                    "quote_id": None,
                                    "order_id": f"arb_{opp.arb_type}_{m_id[:16]}",
                                    "side": "ARB",
                                    "price": opp.yes_price + opp.no_price,
                                    "size": arb_result.get("merged", arb_result.get("yes_sold", 0)),
                                    "fee": mm_cfg.mm_arb_gas_cost_usd,
                                    "mid_at_fill": 1.0,
                                })
                                logger.info(
                                    f"ARB executed: {opp.arb_type} on {m_id[:16]} "
                                    f"profit=${arb_result.get('profit_usd', 0):.4f}"
                                )
                    except Exception as arb_err:
                        logger.debug(f"ARB scan error for {m_id[:16]}: {arb_err}")

            # 5d. Adverse selection feedback (every 30 cycles ~5min)
            if mm_cfg.mm_as_feedback_enabled and cycle % 30 == 0 and mm_cfg.mm_pricing_engine == "as":
                try:
                    from mm.metrics_collector import MetricsCollector
                    mc = MetricsCollector(client)
                    rolling_as = await mc.get_rolling_adverse_selection()
                    if rolling_as > mm_cfg.mm_as_feedback_threshold_bps:
                        gamma_mult = 1.0 + (rolling_as - mm_cfg.mm_as_feedback_threshold_bps) / 200.0
                        as_params.gamma_base = mm_cfg.mm_as_gamma_base * gamma_mult
                        logger.info(
                            f"AS feedback: rolling AS={rolling_as:.1f}bps > threshold, "
                            f"gamma adjusted to {as_params.gamma_base:.3f} (mult={gamma_mult:.2f})"
                        )
                    else:
                        as_params.gamma_base = mm_cfg.mm_as_gamma_base
                except Exception as as_err:
                    logger.debug(f"AS feedback error: {as_err}")

            # 6. Update bot status
            await store.update_bot_status({
                "mm_cycle": cycle,
                "mm_active_markets": len(active_quotes),
                "mm_total_exposure": round(total_exposure, 2),
                "mm_realized_pnl": round(inventory.get_total_realized_pnl(), 4),
                "mm_last_cycle": datetime.now(timezone.utc).isoformat(),
            })

            # Log cycle summary
            elapsed = asyncio.get_event_loop().time() - cycle_start
            if cycle % 10 == 0:
                early_count = sum(1 for m in markets if m.get("is_early"))
                score_info = f" ({len(markets)} after scoring)" if scorer else ""
                logger.info(
                    f"MM cycle {cycle}: {len(active_quotes)} markets "
                    f"({early_count} early){score_info}, "
                    f"${total_exposure:.2f} exposure, "
                    f"balance=${available_balance:.2f}, locked=${locked_capital:.2f}, "
                    f"free=${free_capital:.2f}, "
                    f"PnL ${inventory.get_total_realized_pnl():.4f}, "
                    f"{elapsed:.1f}s"
                )

            # Periodic reconciliation (~10 min = 60 cycles at 10s)
            if cycle % 60 == 0:
                try:
                    db_inv = await store.get_mm_inventory()
                    divergences = inventory.reconcile_with_clob(db_inventory=db_inv)
                    if divergences:
                        logger.warning(f"Reconciliation: {len(divergences)} divergences corrected")
                        for div in divergences:
                            logger.warning(
                                f"  {div['market_id'][:16]}: mem={div['mem_pos']:.1f} "
                                f"-> db={div['db_pos']:.1f}"
                            )

                    # Phantom order detection
                    live_orders = await asyncio.to_thread(client.get_open_orders)
                    live_ids = {o.get("id") for o in live_orders if o}
                    for mid, pair in active_quotes.items():
                        if (pair.bid_order_id and pair.bid_order_id not in live_ids
                                and pair.bid_state == OrderState.LIVE):
                            logger.warning(f"Phantom bid on {mid[:16]}: {pair.bid_order_id}")
                        if (pair.ask_order_id and pair.ask_order_id not in live_ids
                                and pair.ask_state == OrderState.LIVE):
                            logger.warning(f"Phantom ask on {mid[:16]}: {pair.ask_order_id}")
                except Exception as recon_err:
                    logger.debug(f"Reconciliation error: {recon_err}")

        except Exception as e:
            logger.error(f"MM loop error in cycle {cycle}: {e}", exc_info=True)

        await asyncio.sleep(mm_cfg.mm_cycle_seconds)
