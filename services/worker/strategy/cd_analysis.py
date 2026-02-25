"""CD post-trade analysis loop: periodic Claude Opus review of CD trade quality.

Runs every cd_analysis_interval_hours (default 6h). Collects recent signals,
closed positions, and open positions, then calls Claude Opus for a structured
analysis. Results are stored in the cd_trade_analyses table.

Suggestions are logged but NEVER auto-applied.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from config import AppConfig
from db import store

logger = logging.getLogger(__name__)


async def run_cd_analysis(config: AppConfig) -> dict | None:
    """Run a single CD trade analysis using Claude Opus.

    Collects context from DB, calls Opus, stores results.
    Returns the analysis dict or None on failure.
    """
    from ai.claude_caller import call_claude_json, ModelTier

    try:
        # 1. Collect data
        recent_signals = await store.get_recent_cd_signals(limit=100)
        closed_positions = await store.get_closed_cd_positions(limit=50)
        open_positions = await store.get_open_cd_positions()

        if not recent_signals and not closed_positions:
            logger.info("CD analysis: no data to analyze yet")
            return None

        # 2. Build context for Claude
        signals_summary = [
            {
                "coin": s.get("coin"),
                "strike": s.get("strike"),
                "edge_pts": s.get("edge_pts"),
                "p_model": s.get("p_model"),
                "p_market": s.get("p_market"),
                "action": s.get("action"),
                "created_at": s.get("created_at"),
            }
            for s in recent_signals[:50]
        ]

        closed_summary = [
            {
                "coin": p.get("coin"),
                "strike": p.get("strike"),
                "direction": p.get("direction"),
                "entry_price": p.get("entry_price"),
                "exit_price": p.get("exit_price"),
                "exit_reason": p.get("exit_reason"),
                "pnl_realized": p.get("pnl_realized"),
                "shares": p.get("shares"),
            }
            for p in closed_positions
        ]

        open_summary = [
            {
                "coin": p.get("coin"),
                "strike": p.get("strike"),
                "direction": p.get("direction"),
                "entry_price": p.get("entry_price"),
                "shares": p.get("shares"),
            }
            for p in open_positions
        ]

        winning = len([p for p in closed_positions if (p.get("pnl_realized") or 0) > 0])
        losing = len([p for p in closed_positions if (p.get("pnl_realized") or 0) < 0])

        context = json.dumps({
            "recent_signals": signals_summary,
            "closed_positions": closed_summary,
            "open_positions": open_summary,
            "stats": {
                "total_signals": len(recent_signals),
                "total_closed": len(closed_positions),
                "total_open": len(open_positions),
                "winning_trades": winning,
                "losing_trades": losing,
            }
        }, indent=2)

        system_prompt = (
            "You are a quantitative analyst reviewing a crypto directional trading strategy. "
            "The strategy uses a Student-t(nu=6) model with EWMA volatility on BTC/ETH price "
            "threshold markets on Polymarket. Analyze the recent performance and provide "
            "structured feedback. Summary in French."
        )

        user_prompt = f"""Analyze this CD (Crypto Directional) trading data:

{context}

Provide a structured JSON analysis:
{{
  "accuracy_score": 1-10,       // How accurate were the model's probability estimates?
  "entry_quality_score": 1-10,  // Quality of entry timing and sizing
  "exit_quality_score": 1-10,   // Quality of exit decisions (SL/TP/edge reversal)
  "model_fitness_score": 1-10,  // How well does the Student-t model fit current market regime?
  "overall_score": 1-10,        // Overall strategy health
  "parameter_suggestions": {{
    "cd_min_edge_pts": null or suggested value,
    "cd_kelly_fraction": null or suggested value,
    "cd_student_t_nu": null or suggested value,
    "cd_exit_stop_loss_pts": null or suggested value,
    "cd_exit_take_profit_pts": null or suggested value
  }},
  "insights": [
    "insight 1",
    "insight 2"
  ],
  "patterns_detected": ["pattern description"],
  "summary": "Brief French summary of findings"
}}

Be honest and precise. If there isn't enough data for a reliable assessment, say so.
Use null for parameter suggestions when no change is needed."""

        # 3. Call Claude Opus
        result = await call_claude_json(
            config.anthropic, ModelTier.OPUS, user_prompt, system_prompt, max_tokens=2048
        )

        if not result:
            logger.warning("CD analysis: Claude returned no parseable result")
            return None

        # 4. Store in DB
        analysis = {
            "analysis_type": "periodic",
            "signals_analyzed": len(recent_signals),
            "positions_analyzed": len(closed_positions),
            "accuracy_score": result.get("accuracy_score"),
            "entry_quality_score": result.get("entry_quality_score"),
            "exit_quality_score": result.get("exit_quality_score"),
            "model_fitness_score": result.get("model_fitness_score"),
            "overall_score": result.get("overall_score"),
            "parameter_suggestions": result.get("parameter_suggestions", {}),
            "insights": result.get("insights", []),
            "summary": result.get("summary", ""),
            "raw_response": json.dumps(result),
        }

        await store.insert_cd_trade_analysis(analysis)

        # 5. Log results
        summary = result.get("summary", "No summary")
        overall = result.get("overall_score", "?")
        logger.info(f"CD analysis complete (score={overall}/10): {summary}")

        suggestions = result.get("parameter_suggestions", {})
        active_suggestions = {k: v for k, v in suggestions.items() if v is not None}
        if active_suggestions:
            logger.info(f"CD analysis parameter suggestions: {active_suggestions}")

        # Auto-apply suggestions if enabled (with safety bounds)
        if config.cd.cd_analysis_auto_apply and active_suggestions:
            _auto_apply_suggestions(config.cd, active_suggestions)

        for insight in result.get("insights", [])[:5]:
            logger.info(f"CD insight: {insight}")

        return analysis

    except Exception as e:
        logger.error(f"CD analysis failed: {e}", exc_info=True)
        return None


# Safety bounds for auto-apply suggestions
_PARAM_BOUNDS = {
    "cd_min_edge_pts": (3.0, 15.0),
    "cd_kelly_fraction": (0.10, 0.50),
    "cd_student_t_nu": (4.0, 10.0),
    "cd_exit_stop_loss_pts": (8.0, 25.0),
    "cd_exit_take_profit_pts": (10.0, 40.0),
}


def _auto_apply_suggestions(cd_config, suggestions: dict):
    """Apply parameter suggestions from Opus analysis with safety bounds.

    Only applies values that fall within predefined bounds.
    """
    for param, value in suggestions.items():
        if value is None:
            continue
        bounds = _PARAM_BOUNDS.get(param)
        if bounds is None:
            logger.debug(f"CD auto-apply: unknown param {param}, skipping")
            continue

        try:
            value = float(value)
        except (ValueError, TypeError):
            logger.debug(f"CD auto-apply: non-numeric value for {param}: {value}")
            continue

        low, high = bounds
        if low <= value <= high:
            old_value = getattr(cd_config, param, None)
            setattr(cd_config, param, value)
            logger.info(f"CD auto-apply: {param} {old_value} -> {value} (bounds [{low}, {high}])")
        else:
            logger.info(
                f"CD auto-apply: {param}={value} outside bounds [{low}, {high}], skipping"
            )


async def cd_analysis_loop(config: AppConfig):
    """CD post-trade analysis loop.

    Runs every cd_analysis_interval_hours. First run is delayed by one full
    interval to allow data accumulation.
    """
    cd_cfg = config.cd
    if not cd_cfg.cd_analysis_enabled:
        logger.info("CD analysis loop disabled")
        return

    interval_seconds = cd_cfg.cd_analysis_interval_hours * 3600
    logger.info(
        f"CD analysis loop started (interval={cd_cfg.cd_analysis_interval_hours}h). "
        f"First analysis in {cd_cfg.cd_analysis_interval_hours}h."
    )

    # Wait one full interval before first analysis (accumulate data)
    await asyncio.sleep(interval_seconds)

    cycle = 0
    while True:
        try:
            cycle += 1
            logger.info(f"CD analysis cycle {cycle} starting...")

            analysis = await run_cd_analysis(config)

            if analysis:
                await store.update_bot_status({
                    "cd_analysis_cycle": cycle,
                    "cd_analysis_last": datetime.now(timezone.utc).isoformat(),
                    "cd_analysis_score": analysis.get("overall_score"),
                })

        except Exception as e:
            logger.error(f"CD analysis loop error in cycle {cycle}: {e}", exc_info=True)

        await asyncio.sleep(interval_seconds)
