import asyncio
import logging
from executor.client import PolymarketClient
from db.store import (
    get_unresolved_market_ids, resolve_performance, get_performance_stats,
    get_calibration_data, update_daily_pnl, close_position, get_open_positions,
)

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """Automated performance tracking: resolves markets, computes stats, detects biases."""

    def __init__(self, polymarket_client: PolymarketClient):
        self.pm = polymarket_client

    async def check_resolutions(self) -> list[dict]:
        """Check all unresolved markets for resolution. Returns list of resolved markets."""
        unresolved_ids = await get_unresolved_market_ids()
        if not unresolved_ids:
            return []

        resolved = []
        for market_id in unresolved_ids:
            result = await asyncio.to_thread(self.pm.check_market_resolved, market_id)
            if result and result.get("resolved") and result.get("outcome"):
                outcome = result["outcome"]
                resolution = await resolve_performance(market_id, outcome)
                count = int((resolution or {}).get("count", 0))
                pnl_net_total = float((resolution or {}).get("pnl_net_total", 0.0))
                if count > 0:
                    logger.info(
                        f"[PERFORMANCE] Market {market_id[:12]}... resolved: "
                        f"outcome={outcome}, {count} trades updated"
                    )
                    resolved.append({
                        "market_id": market_id,
                        "outcome": outcome,
                        "trades_resolved": count,
                    })

                    # Close positions for resolved markets
                    positions = await get_open_positions()
                    for pos in positions:
                        if pos["market_id"] == market_id:
                            await close_position(market_id, pos["token_id"])

                    # Update daily P&L
                    if pnl_net_total != 0:
                        await update_daily_pnl(pnl_net_total)

        if resolved:
            logger.info(f"[PERFORMANCE] Resolved {len(resolved)} markets this check")
        return resolved

    async def get_stats(self) -> dict:
        """Get comprehensive performance statistics."""
        return await get_performance_stats()

    async def get_calibration_report(self) -> dict | None:
        """Analyze systematic biases in the bot's predictions.

        Returns a report with detected biases, or None if insufficient data.
        """
        data = await get_calibration_data()
        if len(data) < 10:
            return None

        # Confidence bucket analysis
        buckets = {
            "high_conf": {"trades": [], "range": (0.7, 1.0)},
            "med_conf": {"trades": [], "range": (0.4, 0.7)},
            "low_conf": {"trades": [], "range": (0.0, 0.4)},
        }
        for row in data:
            conf = row.get("confidence") or 0.5
            for bucket_name, bucket in buckets.items():
                lo, hi = bucket["range"]
                if lo <= conf < hi:
                    bucket["trades"].append(row)
                    break

        # Edge bucket analysis
        edge_accuracy = {"high_edge": [], "low_edge": []}
        for row in data:
            edge = row.get("edge") or 0
            if edge >= 0.15:
                edge_accuracy["high_edge"].append(row)
            else:
                edge_accuracy["low_edge"].append(row)

        def bucket_stats(trades):
            if not trades:
                return {"count": 0, "hit_rate": 0, "avg_pnl": 0}
            wins = sum(1 for t in trades if t.get("was_correct"))
            total_pnl = sum(t.get("pnl_realized", 0) for t in trades)
            return {
                "count": len(trades),
                "hit_rate": round(wins / len(trades), 3),
                "avg_pnl": round(total_pnl / len(trades), 2),
            }

        # Outcome bias (do we favor Yes over No?)
        yes_trades = [t for t in data if t.get("outcome_bet", "").lower() == "yes"]
        no_trades = [t for t in data if t.get("outcome_bet", "").lower() == "no"]

        report = {
            "sample_size": len(data),
            "confidence_buckets": {k: bucket_stats(v["trades"]) for k, v in buckets.items()},
            "edge_buckets": {k: bucket_stats(v) for k, v in edge_accuracy.items()},
            "outcome_bias": {
                "yes_trades": bucket_stats(yes_trades),
                "no_trades": bucket_stats(no_trades),
            },
            "biases_detected": [],
        }

        # Detect biases
        hc = report["confidence_buckets"]["high_conf"]
        lc = report["confidence_buckets"]["low_conf"]
        if hc["count"] >= 5 and lc["count"] >= 5:
            if hc["hit_rate"] < lc["hit_rate"]:
                report["biases_detected"].append(
                    "OVERCONFIDENCE: high-confidence trades perform worse than low-confidence"
                )

        yes_stats = report["outcome_bias"]["yes_trades"]
        no_stats = report["outcome_bias"]["no_trades"]
        if yes_stats["count"] >= 5 and no_stats["count"] >= 5:
            if yes_stats["count"] > no_stats["count"] * 2:
                report["biases_detected"].append(
                    f"YES BIAS: {yes_stats['count']} Yes trades vs {no_stats['count']} No trades"
                )

        he = report["edge_buckets"]["high_edge"]
        le = report["edge_buckets"]["low_edge"]
        if he["count"] >= 5 and le["count"] >= 5:
            if he["hit_rate"] < le["hit_rate"]:
                report["biases_detected"].append(
                    "EDGE ILLUSION: high-edge trades have lower hit rate than low-edge"
                )

        return report

    def format_stats(self, stats: dict) -> str:
        """Format performance stats for display."""
        if stats["resolved_trades"] == 0:
            return (
                f"Total trades: {stats['total_trades']}\n"
                f"Pending resolution: {stats['pending_resolution']}\n"
                f"No resolved trades yet."
            )

        return (
            f"Trades: {stats['resolved_trades']} resolved / {stats['total_trades']} total\n"
            f"Win rate: {stats['hit_rate']:.1%} ({stats['wins']}W / {stats['losses']}L)\n"
            f"Total P&L: ${stats['total_pnl']:+.2f}\n"
            f"ROI: {stats['roi_percent']:+.1f}%\n"
            f"Avg P&L/trade: ${stats['avg_pnl_per_trade']:+.2f}\n"
            f"Best: ${stats['best_trade']:+.2f} | Worst: ${stats['worst_trade']:+.2f}\n"
            f"Streak: {stats['current_streak']} {stats['streak_type']}\n"
            f"Pending: {stats['pending_resolution']} trades"
        )
