"""Crypto directional loop: periodic Student-t edge detection on BTC/ETH markets."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from config import AppConfig
from executor.client import PolymarketClient
from strategy.crypto_directional import (
    fetch_crypto_threshold_markets,
    fetch_raw_crypto_markets,
    parse_markets_batch,
    fetch_price_history,
    get_spot_price,
    compute_ewma_vol,
    student_t_prob,
    detect_edge,
    kelly_size,
)
from db import store

logger = logging.getLogger(__name__)


async def cd_loop(config: AppConfig, client: PolymarketClient, risk=None):
    """Crypto directional loop. Runs every cd_cycle_minutes.

    Cycle:
    1. Fetch active BTC/ETH price threshold markets from Gamma API
    2. For each market: extract strike K and expiry T
    3. Fetch spot price + compute EWMA vol from CoinGecko
    4. Compute p_model via Student-t
    5. Compare to market price -> edge
    6. If edge >= threshold stable over N cycles -> signal
    7. Size via fractional Kelly, place order
    8. Log to cd_signals table
    """
    cd_cfg = config.cd
    if not cd_cfg.cd_enabled:
        logger.info("CD loop disabled")
        return

    cycle = 0
    logger.info(
        f"CD loop started (cycle={cd_cfg.cd_cycle_minutes}min, "
        f"min_edge={cd_cfg.cd_min_edge_pts}pts)"
    )

    # Cache for price histories {coingecko_id: (prices, timestamp)}
    price_cache: dict[str, tuple[list[float], float]] = {}
    CACHE_TTL = 300  # 5 minutes

    while True:
        try:
            cycle += 1

            # Risk gate: skip cycle if trading is paused
            if risk and risk.is_paused:
                if cycle % 4 == 1:
                    logger.info("CD loop: trading paused by risk manager")
                await asyncio.sleep(cd_cfg.cd_cycle_minutes * 60)
                continue

            # Position count limit
            open_positions = await store.get_open_cd_positions()
            if len(open_positions) >= cd_cfg.cd_max_concurrent_positions:
                if cycle % 4 == 1:
                    logger.info(
                        f"CD: max positions reached ({len(open_positions)}/"
                        f"{cd_cfg.cd_max_concurrent_positions})"
                    )
                await asyncio.sleep(cd_cfg.cd_cycle_minutes * 60)
                continue

            # 1. Fetch crypto threshold markets
            if cd_cfg.cd_nl_parsing_enabled:
                raw_markets = await asyncio.to_thread(fetch_raw_crypto_markets)
                markets = await parse_markets_batch(raw_markets, config.anthropic)
            else:
                markets = await asyncio.to_thread(fetch_crypto_threshold_markets)

            if not markets:
                logger.debug("No crypto threshold markets found")
                await asyncio.sleep(cd_cfg.cd_cycle_minutes * 60)
                continue

            logger.info(f"CD cycle {cycle}: evaluating {len(markets)} crypto markets")

            signals = []

            for market in markets:
                coin_id = market["coingecko_id"]
                strike = market["strike"]
                days_to_expiry = market["days_to_expiry"]
                p_market = market["p_market"]
                direction = market["direction"]

                # 2. Get spot price
                spot = await asyncio.to_thread(
                    get_spot_price, coin_id, cd_cfg.cd_coingecko_api
                )
                if spot is None:
                    continue

                # 3. Get price history (cached)
                import time
                now = time.time()
                cached = price_cache.get(coin_id)
                if cached and (now - cached[1]) < CACHE_TTL:
                    prices = cached[0]
                else:
                    prices = await asyncio.to_thread(
                        fetch_price_history, coin_id, cd_cfg.cd_ewma_span,
                        cd_cfg.cd_coingecko_api
                    )
                    if prices:
                        price_cache[coin_id] = (prices, now)

                if len(prices) < 5:
                    continue

                # 4. Compute EWMA vol
                vol = compute_ewma_vol(prices, cd_cfg.cd_ewma_lambda)
                if vol <= 0:
                    continue

                # 5. Compute model probability
                p_model = await asyncio.to_thread(
                    student_t_prob, spot, strike, days_to_expiry,
                    vol, cd_cfg.cd_student_t_nu, direction
                )

                # 6. Detect edge
                edge_pts = detect_edge(p_model, p_market)

                # Log signal regardless of size
                signal = {
                    "market_id": market["market_id"],
                    "token_id": market.get("token_id", ""),
                    "coin": market["coin"],
                    "strike": strike,
                    "expiry_days": days_to_expiry,
                    "spot_price": spot,
                    "vol_ewma": round(vol, 6),
                    "p_model": round(p_model, 4),
                    "p_market": round(p_market, 4),
                    "edge_pts": round(edge_pts, 2),
                }

                # 7. Check confirmation (edge stable over N cycles)
                confirmation = await store.get_cd_signal_confirmation(
                    market["market_id"], cd_cfg.cd_min_edge_pts
                )

                if edge_pts >= cd_cfg.cd_min_edge_pts:
                    signal["confirmation_count"] = confirmation + 1

                    if confirmation + 1 >= cd_cfg.cd_confirmation_cycles:
                        # Edge confirmed! Size using on-chain balance as capital
                        balance = await asyncio.to_thread(client.get_onchain_balance)
                        if balance is None or balance < 1.0:
                            signal["action"] = "no_balance"
                            signals.append(signal)
                            await store.insert_cd_signal(signal)
                            continue

                        size_usdc = kelly_size(
                            edge_pts=edge_pts,
                            p_model=p_model,
                            capital=balance,
                            kelly_fraction=cd_cfg.cd_kelly_fraction,
                            max_position_pct=cd_cfg.cd_max_position_pct / 100,
                        )

                        if size_usdc >= 1.0:
                            token_id = market.get("token_id", "")
                            if not token_id:
                                signal["action"] = "no_token_id"
                                logger.warning(
                                    f"CD: no token_id for {market['coin']} {direction} ${strike}"
                                )
                                signals.append(signal)
                                await store.insert_cd_signal(signal)
                                continue

                            # Risk validation
                            if risk:
                                ok, reason = await risk.validate_cd_trade(
                                    {"size_usdc": size_usdc, "edge_pts": edge_pts}, balance
                                )
                                if not ok:
                                    signal["action"] = "risk_rejected"
                                    logger.info(f"CD trade rejected by risk: {reason}")
                                    signals.append(signal)
                                    await store.insert_cd_signal(signal)
                                    continue

                                # Global exposure check
                                within_limit, exp_pct = await risk.check_global_exposure(balance)
                                if not within_limit:
                                    signal["action"] = "exposure_limit"
                                    logger.info(f"CD: global exposure {exp_pct:.1f}% exceeds limit")
                                    signals.append(signal)
                                    await store.insert_cd_signal(signal)
                                    continue

                            # Pre-trade AI validation (Haiku)
                            if cd_cfg.cd_pretrade_ai_enabled:
                                ai_result = await _pretrade_validate(
                                    anthropic_config=config.anthropic,
                                    coin=market["coin"],
                                    strike=strike,
                                    direction=direction,
                                    edge_pts=edge_pts,
                                    p_model=p_model,
                                    p_market=p_market,
                                    vol=vol,
                                    spot=spot,
                                    days_to_expiry=days_to_expiry,
                                    open_positions=open_positions,
                                    balance=balance,
                                    size_usdc=size_usdc,
                                )
                                ai_ok = ai_result.get("trade", True) if ai_result else True
                                signal["ai_validation"] = json.dumps(ai_result) if ai_result else None
                                if not ai_ok:
                                    signal["action"] = "ai_rejected"
                                    logger.info(
                                        f"CD pre-trade AI rejected: {market['coin']} "
                                        f"{direction} ${strike} (edge={edge_pts:.1f}pts, "
                                        f"reason={ai_result.get('reason', 'unknown')})"
                                    )
                                    signals.append(signal)
                                    await store.insert_cd_signal(signal)
                                    continue

                            signal["action"] = "trade"
                            signal["size_usdc"] = size_usdc

                            order_price = round(p_market, 2)
                            min_shares = 5.0

                            if cd_cfg.cd_post_only:
                                book_summary = await asyncio.to_thread(
                                    client.get_book_summary, token_id
                                )
                                if book_summary:
                                    best_bid = float(book_summary.get("best_bid", 0) or 0)
                                    if best_bid > 0:
                                        order_price = min(order_price, round(best_bid, 2))
                                    min_shares = max(
                                        5.0,
                                        float(book_summary.get("min_order_size", 5.0) or 5.0),
                                    )

                            order_price = max(order_price, 0.01)
                            shares = round(size_usdc / order_price, 1) if order_price > 0 else 0
                            if shares >= min_shares:
                                resp = await asyncio.to_thread(
                                    client.place_limit_order,
                                    token_id, order_price, shares, "BUY", "GTC", cd_cfg.cd_post_only
                                )
                                if resp:
                                    order_id = resp.get("orderID") or resp.get("order_id")
                                    signal["order_id"] = order_id
                                    signal["order_price"] = order_price
                                    logger.info(
                                        f"CD trade: BUY {shares} shares @ {order_price:.2f} "
                                        f"on {market['coin']} {direction} ${strike} "
                                        f"(edge={edge_pts:.1f}pts)"
                                    )

                                    # Persist CD position for exit monitoring
                                    try:
                                        await store.insert_cd_position({
                                            "market_id": market["market_id"],
                                            "token_id": token_id,
                                            "coin": market["coin"],
                                            "strike": strike,
                                            "direction": direction,
                                            "entry_price": order_price,
                                            "shares": shares,
                                            "expiry_days": days_to_expiry,
                                            "order_id": order_id,
                                        })
                                        # Also upsert into generic positions table
                                        await store.upsert_position({
                                            "market_id": market["market_id"],
                                            "token_id": token_id,
                                            "market_question": market.get("question", ""),
                                            "outcome": "Yes",
                                            "size": shares,
                                            "avg_price": order_price,
                                            "current_price": p_market,
                                            "category": "crypto",
                                            "strategy": "cd",
                                        })
                                    except Exception as pos_err:
                                        logger.warning(f"CD position tracking error: {pos_err}")
                                else:
                                    logger.warning(
                                        f"CD order failed: {market['coin']} {direction} ${strike} "
                                        f"shares={shares} price={order_price:.2f}"
                                    )
                            else:
                                signal["action"] = "shares_too_small"
                                logger.warning(
                                    f"CD shares_too_small: {shares:.1f} < min {min_shares:.1f} for "
                                    f"{market['coin']} {direction} ${strike} "
                                    f"(size_usdc=${size_usdc:.2f}, price={order_price:.2f})"
                                )
                        else:
                            signal["action"] = "too_small"
                    else:
                        signal["action"] = "confirming"
                        logger.debug(
                            f"CD confirming: {market['coin']} {direction} ${strike} "
                            f"edge={edge_pts:.1f}pts ({confirmation+1}/{cd_cfg.cd_confirmation_cycles})"
                        )
                else:
                    signal["action"] = "no_edge"

                signals.append(signal)

                # Persist signal
                await store.insert_cd_signal(signal)

            # Update bot status
            active_signals = [s for s in signals if s.get("action") in ("trade", "confirming")]
            await store.update_bot_status({
                "cd_cycle": cycle,
                "cd_markets_scanned": len(markets),
                "cd_active_signals": len(active_signals),
                "cd_last_cycle": datetime.now(timezone.utc).isoformat(),
            })

            if cycle % 4 == 0:
                logger.info(
                    f"CD cycle {cycle}: {len(markets)} markets scanned, "
                    f"{len(active_signals)} active signals"
                )

        except Exception as e:
            logger.error(f"CD loop error in cycle {cycle}: {e}", exc_info=True)

        await asyncio.sleep(cd_cfg.cd_cycle_minutes * 60)


async def _pretrade_validate(
    anthropic_config,
    coin: str,
    strike: float,
    direction: str,
    edge_pts: float,
    p_model: float,
    p_market: float,
    vol: float,
    spot: float = 0.0,
    days_to_expiry: float = 30.0,
    open_positions: list[dict] | None = None,
    balance: float = 0.0,
    size_usdc: float = 0.0,
) -> dict | None:
    """Ask Haiku to validate a CD trade signal before execution.

    Provides enriched context: portfolio state, volatility regime, spot vs strike,
    and recent position overlap.

    Returns the full AI response dict (with "trade": bool, "reason": str,
    "confidence": float) or None on error.
    Fail-safe: returns {"trade": true} on any error (proceed with trade).
    """
    try:
        from ai.claude_caller import call_claude_json, ModelTier

        # --- Build portfolio context ---
        open_positions = open_positions or []
        n_open = len(open_positions)
        coins_held = set()
        total_exposure_usd = 0.0
        for pos in open_positions:
            coins_held.add(pos.get("coin", ""))
            total_exposure_usd += float(pos.get("entry_price", 0)) * float(pos.get("shares", 0))

        exposure_pct = (total_exposure_usd / balance * 100) if balance > 0 else 0
        already_in_coin = coin in coins_held

        # --- Volatility regime classification ---
        # daily vol thresholds (annualized ~16% = daily ~1%, ~32% = ~2%, ~64% = ~4%)
        if vol < 0.015:
            vol_regime = "low"
        elif vol < 0.035:
            vol_regime = "normal"
        else:
            vol_regime = "high"

        # --- Spot vs strike distance ---
        distance_pct = ((spot - strike) / strike * 100) if strike > 0 else 0

        system_prompt = (
            "You are a quantitative risk filter for a crypto prediction market bot. "
            "Evaluate the proposed trade considering model edge, portfolio exposure, "
            "volatility regime, and time to expiry. Be conservative: reject trades "
            "with weak edges in high-vol regimes, excessive concentration, or poor "
            "risk/reward. Respond ONLY with a JSON object."
        )

        user_prompt = (
            f"=== SIGNAL ===\n"
            f"Coin: {coin} | Direction: {direction} | Strike: ${strike:,.0f}\n"
            f"Spot price: ${spot:,.0f} (distance: {distance_pct:+.1f}% from strike)\n"
            f"Days to expiry: {days_to_expiry:.0f}\n\n"
            f"=== MODEL ===\n"
            f"P(model): {p_model:.3f} | P(market): {p_market:.3f}\n"
            f"Edge: {edge_pts:.1f} pts | EWMA daily vol: {vol:.4f} ({vol_regime})\n\n"
            f"=== PORTFOLIO ===\n"
            f"Open positions: {n_open} | Exposure: ${total_exposure_usd:.0f} ({exposure_pct:.1f}%)\n"
            f"Balance: ${balance:.0f} | Proposed size: ${size_usdc:.0f}\n"
            f"Already exposed to {coin}: {'YES' if already_in_coin else 'NO'}\n\n"
            f"=== DECISION ===\n"
            f"Should the bot take this trade? Consider:\n"
            f"- Is the edge large enough given current volatility?\n"
            f"- Is portfolio concentration acceptable?\n"
            f"- Is time-to-expiry sufficient for the thesis to play out?\n\n"
            f"Respond JSON:\n"
            f'{{"trade": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}}'
        )

        result = await call_claude_json(
            anthropic_config, ModelTier.HAIKU, user_prompt, system_prompt, max_tokens=300
        )

        if result and isinstance(result.get("trade"), bool):
            trade = result["trade"]
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")
            logger.info(
                f"CD pre-trade AI: {coin} {direction} ${strike} -> "
                f"{'TRADE' if trade else 'SKIP'} "
                f"(confidence={confidence:.2f}, vol={vol_regime}, reason={reason})"
            )
            return result

        logger.warning("CD pre-trade AI: unparseable response, defaulting to trade")
        return {"trade": True, "reason": "unparseable_response", "confidence": 0.0}

    except Exception as e:
        logger.warning(f"CD pre-trade AI failed: {e}, defaulting to trade")
        return {"trade": True, "reason": f"error: {str(e)[:100]}", "confidence": 0.0}
