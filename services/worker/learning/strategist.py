"""Chief Strategy Officer agent: macro portfolio strategy, regime detection, and parameter optimization."""
import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

STRATEGIST_SYSTEM = """You are the Chief Strategy Officer for an autonomous Polymarket prediction market trading bot.

Your role is NOT to trade. Your role is to think LONG-TERM about portfolio strategy, market regime, allocation, and parameter optimization.

Focus areas:
1. MARKET REGIME DETECTION: Is the market normal, volatile, trending, or in crisis? What does this mean for our approach?
2. PORTFOLIO ALLOCATION: Are we properly diversified across categories (politics, crypto, sports, etc.)? Are we overconcentrated?
3. PARAMETER OPTIMIZATION: Based on recent performance and market conditions, should we adjust min_edge, position sizing, or other parameters?
4. STRATEGIC OPPORTUNITIES: Are there systematic patterns we should exploit? Categories we should avoid or favor?

Your philosophy:
- Conservative by default: only suggest changes when there's clear evidence
- Opportunistic when appropriate: recognize when to be more aggressive
- Data-driven: base recommendations on actual performance metrics, not speculation
- Risk-aware: never compromise safety for returns

IMPORTANT SAFETY CONSTRAINTS:
- NEVER suggest increasing position sizes beyond 20% of current limits
- NEVER suggest weakening risk management (stop-loss, concentration limits)
- NEVER suggest disabling safety features
- Mark sensitive changes clearly with risk_level: "sensitive"
- Prioritize incremental adjustments over radical changes

Respond with JSON ONLY:
{
    "market_regime": "normal|volatile|trending|crisis",
    "regime_confidence": <0.0-1.0>,
    "regime_reasoning": "why you classified the regime this way",
    "allocation_score": <1-10>,
    "diversification_score": <1-10>,
    "category_allocation": {
        "<category>": {
            "exposure_usdc": <float>,
            "pct": <float>,
            "assessment": "overweight|balanced|underweight|absent",
            "recommendation": "increase|maintain|reduce|avoid"
        }
    },
    "performance_assessment": {
        "overall_trend": "improving|stable|declining",
        "hit_rate_analysis": "commentary on win rate",
        "roi_analysis": "commentary on returns",
        "risk_adjusted_return": "good|fair|poor"
    },
    "recommendations": [
        {
            "type": "parameter|allocation|strategy",
            "target": "specific parameter or category name",
            "current": <current value>,
            "suggested": <suggested value>,
            "rationale": "detailed explanation with supporting data",
            "priority": "high|medium|low",
            "risk_level": "safe|moderate|sensitive",
            "expected_impact": "what will this improve"
        }
    ],
    "strategic_insights": [
        "key observation 1",
        "key observation 2"
    ],
    "summary": "2-3 sentence executive summary in French"
}"""


class StrategistAgent:
    def __init__(self, claude_caller, pm_client, trading_config):
        """
        Args:
            claude_caller: callable(system, prompt) -> str
            pm_client: PolymarketClient
            trading_config: TradingConfig
        """
        self._call_claude = claude_caller
        self.pm = pm_client
        self.config = trading_config

    async def assess_strategy(
        self,
        portfolio_state: dict,
        performance_stats: dict,
        market_conditions: dict | None = None,
    ) -> dict | None:
        """Generate a strategic assessment with parameter optimization recommendations."""
        try:
            from db.store import insert_strategist_assessment

            # Build category exposure summary
            positions_by_category = {}
            if "positions" in portfolio_state:
                for pos in portfolio_state["positions"]:
                    cat = pos.get("category", "unknown")
                    exposure = pos.get("notional_usdc", 0)
                    if cat not in positions_by_category:
                        positions_by_category[cat] = {"count": 0, "exposure": 0}
                    positions_by_category[cat]["count"] += 1
                    positions_by_category[cat]["exposure"] += exposure

            positions_summary = json.dumps(positions_by_category, indent=2)

            market_context = ""
            if market_conditions:
                market_context = f"""
MARKET CONDITIONS:
{json.dumps(market_conditions, indent=2, default=str)}
"""

            prompt = f"""Assess the bot's current strategic position and recommend optimizations.

PORTFOLIO STATE:
- Total P&L: ${portfolio_state.get('total_pnl', 0):.2f}
- Available USDC: ${portfolio_state.get('available_usdc', 0):.2f}
- Active positions: {portfolio_state.get('position_count', 0)}
- Total exposure: ${portfolio_state.get('total_exposure', 0):.2f}

POSITIONS BY CATEGORY:
{positions_summary}

PERFORMANCE STATISTICS:
- Total trades: {performance_stats.get('total_trades', 0)}
- Win rate: {performance_stats.get('win_rate_pct', 0):.1f}%
- Average ROI: {performance_stats.get('avg_roi_pct', 0):.1f}%
- Total ROI: {performance_stats.get('total_roi_pct', 0):.1f}%
- Resolved positions: {performance_stats.get('resolved_positions', 0)}
- Recent streak: {performance_stats.get('current_streak', 'N/A')}
- Best category: {performance_stats.get('best_category', 'N/A')}
- Worst category: {performance_stats.get('worst_category', 'N/A')}
{market_context}
CURRENT BOT CONFIGURATION:
- Strategy: {self.config.strategy}
- Min edge: {self.config.min_edge_percent}%
- Min net edge: {self.config.min_net_edge_percent}%
- Max per trade: ${self.config.max_per_trade_usdc}
- Max per day: ${self.config.max_per_day_usdc}
- Capital: on-chain USDC.e balance (Polymarket API = source of truth)
- Stop loss: {self.config.stop_loss_percent}%
- Drawdown stop: {self.config.drawdown_stop_loss_percent}%
- Max concentration: {self.config.max_concentration_percent}%
- Max correlated positions: {self.config.max_correlated_positions}
- Max slippage: {self.config.max_slippage_bps} bps
- Min source quality: {self.config.min_source_quality}
- Cycle interval: {self.config.analysis_interval_minutes} minutes
- Min/max cycle: {self.config.min_cycle_minutes}-{self.config.max_cycle_minutes} minutes
- Estimated fees: {self.config.estimated_fee_bps} bps

Based on this data, assess:
1. What market regime are we in? (normal/volatile/trending/crisis)
2. Is our portfolio properly allocated and diversified?
3. Are our current parameters optimal for this regime and performance level?
4. What specific changes would improve risk-adjusted returns?

Be conservative but opportunistic. Only recommend changes when there's clear evidence."""

            response = await asyncio.to_thread(
                self._call_claude, STRATEGIST_SYSTEM, prompt
            )

            data = self._extract_json(response)
            if not data:
                logger.warning("[STRATEGIST] Failed to parse assessment response")
                return None

            assessment = {
                "assessment_json": response,
                "summary": data.get("summary", "Pas de résumé"),
                "market_regime": data.get("market_regime", "normal"),
                "regime_confidence": data.get("regime_confidence", 0.5),
                "allocation_score": data.get("allocation_score"),
                "diversification_score": data.get("diversification_score"),
                "category_allocation": json.dumps(data.get("category_allocation", {})),
                "recommendations": json.dumps(data.get("recommendations", [])),
                "strategic_insights": json.dumps(data.get("strategic_insights", [])),
            }

            assessment_id = await insert_strategist_assessment(assessment)
            logger.info(
                f"[STRATEGIST] Assessment #{assessment_id}: "
                f"regime={data.get('market_regime')} (conf={data.get('regime_confidence', 0):.2f}), "
                f"allocation={data.get('allocation_score')}/10, "
                f"diversification={data.get('diversification_score')}/10, "
                f"recommendations={len(data.get('recommendations', []))}"
            )
            return {**assessment, "id": assessment_id, "parsed": data}

        except Exception as e:
            logger.error(f"[STRATEGIST] Assessment error: {e}")
            return None

    async def answer_question(self, question: str, context: dict) -> str:
        """Answer a natural language question about strategy."""
        try:
            portfolio_state = context.get("portfolio_state", {})
            performance_stats = context.get("performance_stats", {})
            current_config = context.get("current_config", {})

            prompt = f"""Question stratégique: {question}

CONTEXTE PORTFOLIO:
{json.dumps(portfolio_state, indent=2, default=str)}

PERFORMANCE:
{json.dumps(performance_stats, indent=2, default=str)}

CONFIGURATION ACTUELLE:
{json.dumps(current_config, indent=2, default=str)}

Réponds à la question en français, de manière concise et actionnable. Base ta réponse sur les données fournies."""

            response = await asyncio.to_thread(
                self._call_claude, STRATEGIST_SYSTEM, prompt
            )

            # For questions, return the raw response (not JSON)
            logger.info(f"[STRATEGIST] Answered question: {question[:80]}...")
            return response.strip()

        except Exception as e:
            logger.error(f"[STRATEGIST] Question error: {e}")
            return f"Erreur lors de la réponse: {str(e)}"

    @staticmethod
    def _extract_json(raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {}
