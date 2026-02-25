"""CD exit monitor: automatic stop-loss, take-profit, and edge reversal exits.

Runs as an independent loop (default every 120s) alongside the CD entry loop.
For each open CD position, evaluates exit conditions and places SELL orders
when triggered.
"""

import asyncio
import logging

from config import AppConfig
from executor.client import PolymarketClient
from strategy.crypto_directional import (
    get_spot_price,
    fetch_price_history,
    compute_ewma_vol,
    student_t_prob,
    detect_edge,
)
from db import store

logger = logging.getLogger(__name__)


async def check_cd_exits(config: AppConfig, client: PolymarketClient) -> list[dict]:
    """Check all open CD positions and trigger exits when conditions are met.

    Returns a list of exit actions taken (for logging/notification).
    """
    cd_cfg = config.cd
    positions = await store.get_open_cd_positions()
    if not positions:
        return []

    exits = []

    for pos in positions:
        market_id = pos["market_id"]
        token_id = pos["token_id"]
        coin = pos["coin"]
        entry_price = float(pos["entry_price"])
        shares = float(pos["shares"])
        strike = float(pos["strike"])
        direction = pos["direction"]

        try:
            # 1. Get current market price
            current_price = await asyncio.to_thread(client.get_midpoint, token_id)
            if current_price is None:
                logger.debug(f"CD exit: no midpoint for {coin} {direction} ${strike}")
                continue

            current_price = float(current_price)

            # 2. Check stop-loss
            loss_pts = (entry_price - current_price) * 100
            if loss_pts >= cd_cfg.cd_exit_stop_loss_pts:
                exit_action = await _execute_exit(
                    client, pos, current_price, "stopped",
                    f"stop-loss triggered (loss={loss_pts:.1f}pts >= {cd_cfg.cd_exit_stop_loss_pts}pts)"
                )
                if exit_action:
                    exits.append(exit_action)
                continue

            # 3. Check take-profit
            profit_pts = (current_price - entry_price) * 100
            if profit_pts >= cd_cfg.cd_exit_take_profit_pts:
                exit_action = await _execute_exit(
                    client, pos, current_price, "took_profit",
                    f"take-profit triggered (profit={profit_pts:.1f}pts >= {cd_cfg.cd_exit_take_profit_pts}pts)"
                )
                if exit_action:
                    exits.append(exit_action)
                continue

            # 4. Check edge reversal via model recalculation
            expiry_days = float(pos.get("expiry_days") or 30.0)
            # Degrade expiry by time elapsed since position opened
            created_at_str = pos.get("created_at")
            if created_at_str:
                try:
                    from datetime import datetime, timezone
                    created_at = datetime.fromisoformat(created_at_str)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    elapsed_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400
                    expiry_days = max(expiry_days - elapsed_days, 1.0)
                except Exception:
                    pass

            edge_now = await _recalculate_edge(
                coin, strike, direction, cd_cfg,
                token_id=token_id, client=client, expiry_days=expiry_days,
            )
            if edge_now is not None and edge_now <= cd_cfg.cd_exit_edge_reversal_pts:
                # AI confirmation for edge-reversal exits (Haiku)
                should_exit = True
                if cd_cfg.cd_exit_ai_confirm_enabled:
                    should_exit = await _confirm_edge_reversal(
                        config.anthropic, coin, strike, direction,
                        entry_price, current_price, edge_now,
                    )

                if should_exit:
                    exit_action = await _execute_exit(
                        client, pos, current_price, "closed",
                        f"edge reversal (edge={edge_now:.1f}pts <= {cd_cfg.cd_exit_edge_reversal_pts}pts)"
                    )
                    if exit_action:
                        exits.append(exit_action)
                else:
                    logger.info(
                        f"CD exit AI: skipping edge reversal for {coin} {direction} ${strike} "
                        f"(edge={edge_now:.1f}pts, classified as noise)"
                    )

        except Exception as e:
            logger.error(
                f"CD exit check error for {coin} {direction} ${strike}: {e}",
                exc_info=True
            )

    return exits


async def _execute_exit(
    client: PolymarketClient,
    pos: dict,
    current_price: float,
    exit_status: str,
    reason: str,
) -> dict | None:
    """Place a SELL order and update the DB for a position exit."""
    token_id = pos["token_id"]
    market_id = pos["market_id"]
    shares = float(pos["shares"])
    coin = pos["coin"]
    strike = float(pos["strike"])
    direction = pos["direction"]
    entry_price = float(pos["entry_price"])

    # Get best bid for SELL pricing
    sell_price = current_price
    try:
        book_summary = await asyncio.to_thread(client.get_book_summary, token_id)
        if book_summary:
            best_bid = float(book_summary.get("best_bid", 0) or 0)
            if best_bid > 0:
                sell_price = best_bid
    except Exception:
        pass

    sell_price = max(sell_price, 0.01)

    # Place SELL order
    resp = await asyncio.to_thread(
        client.place_limit_order,
        token_id, sell_price, shares, "SELL", "GTC", False
    )

    if not resp:
        logger.warning(
            f"CD exit SELL failed for {coin} {direction} ${strike} "
            f"({reason})"
        )
        return None

    order_id = resp.get("orderID") or resp.get("order_id")
    pnl = (sell_price - entry_price) * shares

    logger.info(
        f"CD exit: SELL {shares} shares @ {sell_price:.2f} "
        f"for {coin} {direction} ${strike} — {reason} "
        f"(entry={entry_price:.2f}, pnl={pnl:+.2f})"
    )

    # Update cd_positions table
    await store.close_cd_position(
        market_id=market_id,
        token_id=token_id,
        exit_price=sell_price,
        exit_reason=exit_status,
        exit_order_id=order_id,
    )

    # Update generic positions table
    try:
        await store.reduce_position(market_id, token_id, shares, sell_price)
    except Exception:
        pass

    return {
        "market_id": market_id,
        "token_id": token_id,
        "coin": coin,
        "strike": strike,
        "direction": direction,
        "exit_reason": exit_status,
        "exit_price": sell_price,
        "entry_price": entry_price,
        "shares": shares,
        "pnl": round(pnl, 4),
        "order_id": order_id,
        "detail": reason,
    }


# Coin name -> CoinGecko ID mapping (mirrors crypto_directional.py)
_COIN_TO_COINGECKO = {
    "BTC": "bitcoin",
    "Bitcoin": "bitcoin",
    "ETH": "ethereum",
    "Ethereum": "ethereum",
}


async def _recalculate_edge(
    coin: str,
    strike: float,
    direction: str,
    cd_cfg,
    token_id: str | None = None,
    client=None,
    expiry_days: float = 30.0,
) -> float | None:
    """Recalculate the Student-t edge for a position's market parameters.

    Uses the actual CLOB midpoint as p_market (instead of a fixed 0.5 baseline)
    and the position's remaining expiry days (instead of a hardcoded 30 days).
    """
    coingecko_id = _COIN_TO_COINGECKO.get(coin)
    if not coingecko_id:
        return None

    try:
        spot = await asyncio.to_thread(
            get_spot_price, coingecko_id, cd_cfg.cd_coingecko_api
        )
        if spot is None:
            return None

        prices = await asyncio.to_thread(
            fetch_price_history, coingecko_id, cd_cfg.cd_ewma_span,
            cd_cfg.cd_coingecko_api
        )
        if len(prices) < 5:
            return None

        vol = compute_ewma_vol(prices, cd_cfg.cd_ewma_lambda)
        if vol <= 0:
            return None

        p_model = await asyncio.to_thread(
            student_t_prob, spot, strike, expiry_days,
            vol, cd_cfg.cd_student_t_nu, direction
        )

        # Get actual market price from CLOB midpoint
        p_market = 0.5  # fallback
        if token_id and client:
            try:
                mid = await asyncio.to_thread(client.get_midpoint, token_id)
                if mid is not None:
                    p_market = float(mid)
            except Exception:
                pass  # fall back to 0.5

        edge_pts = detect_edge(p_model, p_market)
        return edge_pts

    except Exception as e:
        logger.debug(f"CD edge recalculation error for {coin} ${strike}: {e}")
        return None


async def _confirm_edge_reversal(
    anthropic_config,
    coin: str,
    strike: float,
    direction: str,
    entry_price: float,
    current_price: float,
    edge_now: float,
) -> bool:
    """Ask Haiku to confirm if an edge reversal is fundamental or noise.

    Returns True if the exit should proceed, False if it looks like noise.
    Fail-safe: returns True (proceed with exit) on any error.
    """
    try:
        from ai.claude_caller import call_claude_json, ModelTier

        system_prompt = (
            "You are a risk classifier for a crypto prediction market trading bot. "
            "Given a position's context, determine if an edge reversal signal is a "
            "fundamental shift or temporary noise. Be conservative: when in doubt, "
            "confirm the exit to protect capital."
        )

        user_prompt = (
            f"Position: {coin} {direction} ${strike:,.0f}\n"
            f"Entry price: {entry_price:.2f}\n"
            f"Current price: {current_price:.2f}\n"
            f"Current edge: {edge_now:.1f} pts (negative = model now disagrees)\n\n"
            f"Is this edge reversal likely a fundamental shift or temporary noise?\n\n"
            f"Respond in JSON:\n"
            f'{{"confirm_exit": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}}\n\n'
            f"confirm_exit=true means proceed with exit, false means skip this cycle."
        )

        result = await call_claude_json(
            anthropic_config, ModelTier.HAIKU, user_prompt, system_prompt, max_tokens=256
        )

        if result and isinstance(result.get("confirm_exit"), bool):
            confirm = result["confirm_exit"]
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")
            logger.info(
                f"CD exit AI confirm: {coin} {direction} ${strike} -> "
                f"{'EXIT' if confirm else 'SKIP'} (confidence={confidence:.2f}, reason={reason})"
            )
            return confirm

        # Could not parse response -> fail-safe: proceed with exit
        logger.warning("CD exit AI confirm: unparseable response, defaulting to exit")
        return True

    except Exception as e:
        logger.warning(f"CD exit AI confirm failed: {e}, defaulting to exit")
        return True


async def cd_exit_loop(config: AppConfig, client: PolymarketClient, risk=None):
    """CD exit monitoring loop.

    Runs every cd_exit_check_seconds (default 120s):
    1. Load open CD positions from DB
    2. For each position, evaluate exit conditions
    3. Place SELL orders if triggered
    4. Update DB

    NOTE: No pause gate here — exits protect capital and must ALWAYS run.
    """
    cd_cfg = config.cd
    if not cd_cfg.cd_exit_enabled:
        logger.info("CD exit loop disabled")
        return

    cycle = 0
    logger.info(
        f"CD exit loop started (cycle={cd_cfg.cd_exit_check_seconds}s, "
        f"SL={cd_cfg.cd_exit_stop_loss_pts}pts, "
        f"TP={cd_cfg.cd_exit_take_profit_pts}pts, "
        f"edge_rev={cd_cfg.cd_exit_edge_reversal_pts}pts)"
    )

    while True:
        try:
            cycle += 1
            exits = await check_cd_exits(config, client)

            if exits:
                logger.info(
                    f"CD exit cycle {cycle}: {len(exits)} exits executed"
                )
                for ex in exits:
                    logger.info(
                        f"  {ex['coin']} {ex['direction']} ${ex['strike']}: "
                        f"{ex['exit_reason']} @ {ex['exit_price']:.2f} "
                        f"(pnl={ex['pnl']:+.4f})"
                    )

                # Update bot status
                await store.update_bot_status({
                    "cd_exit_cycle": cycle,
                    "cd_exit_last_exits": len(exits),
                })

            elif cycle % 30 == 0:
                # Log periodic status every ~60 min
                open_count = len(await store.get_open_cd_positions())
                logger.info(
                    f"CD exit cycle {cycle}: monitoring {open_count} open positions"
                )

        except Exception as e:
            logger.error(f"CD exit loop error in cycle {cycle}: {e}", exc_info=True)

        await asyncio.sleep(cd_cfg.cd_exit_check_seconds)
