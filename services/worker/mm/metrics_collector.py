"""MM metrics collector: adverse selection measurement + rolling metrics computation."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from db import store
from mm import metrics as mm_metrics

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects and computes MM performance metrics."""

    def __init__(self, client=None):
        """
        Args:
            client: PolymarketClient instance for book_summary lookups.
        """
        self._client = client
        self._token_cache: dict[int, str] = {}  # quote_id -> token_id
        self._side_cache: dict[int, str] = {}   # quote_id -> side (from mm_quotes doesn't have side, but fills do)

    async def measure_adverse_selection(self):
        """Check pending fills and measure mid movement at T+30s and T+120s.

        Should be called every ~30s from the maintenance loop.
        """
        if not self._client:
            return

        try:
            pending = await store.get_pending_adverse_selection_fills(window_seconds=180)
        except Exception as e:
            logger.debug(f"AS measurement: failed to get pending fills: {e}")
            return

        if not pending:
            return

        now = datetime.now(timezone.utc)
        measured_30 = 0
        measured_120 = 0

        for fill in pending:
            fill_id = fill["id"]
            created_str = fill.get("created_at", "")
            if not created_str:
                continue

            try:
                # Parse fill timestamp
                fill_time = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if fill_time.tzinfo is None:
                    fill_time = fill_time.replace(tzinfo=timezone.utc)
                age_seconds = (now - fill_time).total_seconds()
            except (ValueError, TypeError):
                continue

            # Get token_id for this fill's quote
            token_id = await self._get_token_id(fill.get("quote_id"))
            if not token_id:
                continue

            # Measure mid at T+30s
            if fill.get("mid_at_30s") is None and age_seconds >= 30:
                mid = await self._get_current_mid(token_id)
                if mid is not None:
                    await store.update_mm_fill_adverse_selection(fill_id, mid_at_30s=mid)
                    measured_30 += 1

            # Measure mid at T+120s and calculate AS
            if fill.get("mid_at_120s") is None and age_seconds >= 120:
                mid = await self._get_current_mid(token_id)
                if mid is not None:
                    # Calculate adverse selection
                    as_bps = mm_metrics.adverse_selection(
                        fill_price=fill["price"],
                        mid_at_fill=fill.get("mid_at_fill", fill["price"]),
                        mid_at_later=mid,
                        side=fill["side"],
                    )
                    await store.update_mm_fill_adverse_selection(
                        fill_id, mid_at_120s=mid
                    )
                    # Store AS value directly
                    await self._update_as_value(fill_id, as_bps)
                    measured_120 += 1

        if measured_30 or measured_120:
            logger.info(f"AS measurement: {measured_30} at T+30s, {measured_120} at T+120s")

    async def compute_daily_metrics(self) -> dict:
        """Compute today's aggregated MM metrics.

        Should be called every ~10 min from the maintenance loop.
        Returns dict with all computed metrics.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            all_fills = await store.get_recent_mm_fills(limit=2000)
        except Exception as e:
            logger.debug(f"Metrics computation error: {e}")
            return {}

        today_fills = [
            f for f in all_fills
            if f.get("created_at", "").startswith(today)
        ]

        if not today_fills:
            return {}

        # PnL
        pnl = mm_metrics.compute_pnl(today_fills)

        # Fill quality average
        fq_values = []
        for f in today_fills:
            mid = f.get("mid_at_fill")
            if mid and mid > 0:
                fq = mm_metrics.fill_quality(f["price"], mid, f["side"])
                fq_values.append(fq)
        fq_avg = sum(fq_values) / len(fq_values) if fq_values else 0.0

        # Adverse selection average (only fills with AS measured)
        as_values = [f["adverse_selection"] for f in today_fills if f.get("adverse_selection") is not None]
        as_avg = sum(as_values) / len(as_values) if as_values else 0.0

        # Spread capture (needs quotes)
        try:
            quotes = await store.get_recent_mm_quotes(limit=2000)
            today_quotes = [q for q in quotes if q.get("created_at", "").startswith(today)]
            scr = mm_metrics.spread_capture_rate(today_fills, today_quotes)
        except Exception:
            scr = 0.0

        # Profit factor
        pf = mm_metrics.profit_factor(today_fills)

        # Inventory stats
        try:
            inventory = await store.get_mm_inventory()
            max_inv = max(
                (abs(float(i.get("net_position", 0))) for i in inventory),
                default=0,
            )
        except Exception:
            max_inv = 0

        # Inventory turns
        inv_turns = mm_metrics.inventory_turn_rate(
            fills_count=len(today_fills),
            avg_inventory=max_inv / 2 if max_inv > 0 else 1,
            period_hours=self._hours_since_midnight(),
        )

        # Sharpe from recent daily returns
        sharpe = await self._compute_rolling_sharpe(days=7)

        result = {
            "fills_count": len(today_fills),
            "pnl_gross": pnl["gross_pnl"],
            "pnl_net": pnl["net_pnl"],
            "fill_quality_avg": round(fq_avg, 2),
            "adverse_selection_avg": round(as_avg, 2),
            "spread_capture_rate": round(scr, 4),
            "profit_factor": round(pf, 2) if pf != float("inf") else 999.9,
            "max_inventory": max_inv,
            "inventory_turns": round(inv_turns, 2),
            "sharpe_7d": round(sharpe, 2),
            "portfolio_value": await self._get_portfolio_value(),
        }

        # Persist
        try:
            await store.upsert_mm_daily_metrics(today, result)
        except Exception as e:
            logger.debug(f"Failed to persist daily metrics: {e}")

        return result

    async def get_rolling_adverse_selection(self, market_id: str | None = None, limit: int = 200) -> float:
        """Get rolling average adverse selection from recent fills.

        Args:
            market_id: Filter to specific market (None = all markets).
            limit: Number of recent fills to consider.

        Returns:
            Average adverse selection in bps (basis points).
        """
        try:
            fills = await store.get_recent_mm_fills(limit=limit)
            as_values = [
                f["adverse_selection"] for f in fills
                if f.get("adverse_selection") is not None
                and (market_id is None or f.get("market_id") == market_id)
            ]
            if not as_values:
                return 0.0
            return sum(as_values) / len(as_values)
        except Exception:
            return 0.0

    async def _get_token_id(self, quote_id: int | None) -> str | None:
        """Get token_id for a quote, with caching."""
        if quote_id is None:
            return None
        if quote_id in self._token_cache:
            return self._token_cache[quote_id]
        try:
            db = await store._get_db()
            cursor = await db.execute(
                "SELECT token_id FROM mm_quotes WHERE id=?", (quote_id,)
            )
            row = await cursor.fetchone()
            if row:
                self._token_cache[quote_id] = row["token_id"]
                return row["token_id"]
        except Exception:
            pass
        return None

    async def _get_current_mid(self, token_id: str) -> float | None:
        """Fetch current mid price from order book."""
        try:
            summary = await asyncio.to_thread(
                self._client.get_book_summary, token_id
            )
            if summary:
                return summary.get("mid")
        except Exception:
            pass
        return None

    async def _update_as_value(self, fill_id: int, as_bps: float):
        """Store computed adverse selection value."""
        try:
            db = await store._get_db()
            await db.execute(
                "UPDATE mm_fills SET adverse_selection=? WHERE id=?",
                (as_bps, fill_id)
            )
            await db.commit()
        except Exception as e:
            logger.debug(f"Failed to update AS value for fill {fill_id}: {e}")

    async def _compute_rolling_sharpe(self, days: int = 7) -> float:
        """Compute Sharpe from recent daily percentage returns.

        Uses portfolio_value to compute % returns instead of absolute PnL,
        which makes the Sharpe ratio independent of portfolio size.
        """
        try:
            db = await store._get_db()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor = await db.execute(
                "SELECT pnl_net, portfolio_value FROM mm_daily_metrics WHERE date >= ? ORDER BY date ASC",
                (cutoff,)
            )
            rows = await cursor.fetchall()
            if len(rows) < 2:
                return 0.0
            # Compute percentage returns when portfolio_value is available
            daily_returns = []
            for r in rows:
                pv = r["portfolio_value"] if r["portfolio_value"] else 0
                pnl = r["pnl_net"]
                if pv > 0:
                    daily_returns.append(pnl / pv)
                else:
                    daily_returns.append(pnl)  # Fallback to absolute
            return mm_metrics.sharpe_ratio(daily_returns)
        except Exception:
            return 0.0

    async def _get_portfolio_value(self) -> float:
        """Get current portfolio value (on-chain balance + MM exposure)."""
        if not self._client:
            return 0.0
        try:
            balance = await asyncio.to_thread(self._client.get_onchain_balance)
            if balance is None:
                return 0.0
            mm_exposure = await store.get_mm_total_exposure()
            return balance + mm_exposure
        except Exception:
            return 0.0

    @staticmethod
    def _hours_since_midnight() -> float:
        """Hours elapsed since midnight UTC."""
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return max((now - midnight).total_seconds() / 3600, 0.1)
