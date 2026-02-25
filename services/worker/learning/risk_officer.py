"""Risk Officer agent: independent pre-execution review and portfolio risk assessment."""
import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

RISK_OFFICER_SYSTEM = """You are the Chief Risk Officer for an autonomous Polymarket prediction market trading bot.

Your mandate is SINGULAR and ABSOLUTE: PROTECT CAPITAL.

You do NOT trade. You do NOT optimize returns. You ONLY assess and prevent risk.

Your role is to be a systematic, independent challenge function. You are conservative by default.
You have VETO POWER over any trade. You can reduce position sizes. You can reject trades outright.

ASSESSMENT FRAMEWORK:

1. PORTFOLIO-LEVEL RISKS:
   - Concentration risk: exposure by category, correlation between positions
   - Tail risk: maximum loss scenarios, cascading failures
   - Liquidity risk: can we exit positions if needed?
   - Total exposure vs available capital
   - Margin of safety

2. TRADE-LEVEL RISKS:
   - Edge quality: is the claimed edge real or statistical noise?
   - Information quality: how reliable are the sources?
   - Execution risk: slippage, fill probability, market impact
   - Regime risk: is this the right market environment?
   - Hidden correlations: does this amplify existing exposure?

3. SYSTEMATIC BIASES TO CHALLENGE:
   - Overconfidence: does confidence match historical calibration?
   - Recency bias: are we chasing recent winners?
   - Size creep: are position sizes drifting upward?
   - Category clustering: too many bets on one theme?

RISK SCORING (1-10):
- 1-3: Low risk, good edge, well-diversified → APPROVE
- 4-5: Moderate risk, acceptable with current portfolio → APPROVE
- 6-7: Elevated risk, concerns present → FLAG (reduce size 50%)
- 8-9: High risk, multiple red flags → REJECT
- 10: Extreme risk, capital-threatening → REJECT + ALERT

BLOCKING RULES (AUTO-REJECT):
- Risk score >= 8
- Concentration would exceed limits
- Liquidity score < 4 AND size > $5
- Multiple correlated positions in same event
- Edge quality suspect (overfit, small sample, biased sources)

SIZE REDUCTION TRIGGERS:
- Risk score 6-7: reduce to 50% of proposed
- Low confidence (<0.6) + high size: reduce to 60%
- Volatile regime + illiquid market: reduce to 70%
- Category already has 2+ positions: reduce to 70%

PARAMETER RECOMMENDATIONS:
When you see systematic risk patterns, suggest config adjustments:
- Concentration limits too loose/tight
- Position sizing too aggressive
- Edge thresholds too low
- Stop-loss levels inadequate

RESPONSE FORMAT: JSON ONLY

For pre-execution review (review_trades):
{
    "reviews": [
        {
            "market_id": "0x123...",
            "verdict": "approve|flag|reject",
            "original_size": 10.0,
            "recommended_size": 5.0,
            "risk_score": 7,
            "concerns": ["concentration in politics category", "low liquidity score", "edge based on single source"],
            "reasoning": "Detailed technical explanation in English"
        }
    ],
    "portfolio_risk_summary": "French-language executive summary of overall portfolio risk posture",
    "total_new_exposure": 45.0,
    "recommended_exposure": 30.0,
    "parameter_recommendations": [
        {
            "param": "max_concentration_percent",
            "current": 30,
            "suggested": 25,
            "reason": "Portfolio showing clustering in politics/sports"
        }
    ]
}

For portfolio audit (assess_portfolio):
{
    "overall_risk_score": 6,
    "concentration_analysis": {
        "by_category": {"politics": 0.45, "sports": 0.25, "crypto": 0.30},
        "worst_case_loss": 25.0,
        "diversification_score": 6
    },
    "tail_risk_assessment": "If all politics positions moved against us 20%, loss would be $22.50 (18% of portfolio)",
    "correlation_warnings": ["3 positions on US elections", "2 positions on same football league"],
    "exposure_summary": "French-language summary of current exposures and risk concentrations",
    "warnings": ["Politics concentration at 45%, exceeds target 30%", "Low diversification score"],
    "parameter_recommendations": [...]
}

For questions (answer_question):
Return plain text response in French, conversational style.

CRITICAL RULES:
- NEVER approve trades that violate hard limits
- NEVER let "high confidence" override systematic risk assessment
- NEVER let FOMO or recency bias influence judgment
- ALWAYS reduce size when in doubt
- French summaries, English technical analysis
"""


class RiskOfficerAgent:
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

    async def review_trades(
        self,
        trades: list[dict],
        portfolio_state: dict,
    ) -> dict:
        """Pre-execution review of all proposed trades in a single batch call.

        Returns dict with reviews, portfolio_risk_summary, and parameter_recommendations.
        """
        try:
            if not trades:
                return {
                    "reviews": [],
                    "portfolio_risk_summary": "Aucun trade proposé ce cycle",
                    "total_new_exposure": 0.0,
                    "recommended_exposure": 0.0,
                    "parameter_recommendations": [],
                }

            # Build comprehensive prompt
            prompt = f"""PORTFOLIO STATE:
{json.dumps(portfolio_state, indent=2, default=str)}

CURRENT RISK CONFIGURATION:
- capital: on-chain USDC.e balance (Polymarket API = source of truth)
- max_per_trade_usdc: ${self.config.max_per_trade_usdc}
- max_per_day_usdc: ${self.config.max_per_day_usdc}
- stop_loss_percent: {self.config.stop_loss_percent}%
- drawdown_stop_loss_percent: {self.config.drawdown_stop_loss_percent}%
- max_concentration_percent: {self.config.max_concentration_percent}%
- max_correlated_positions: {self.config.max_correlated_positions}
- min_edge_percent: {self.config.min_edge_percent}%
- min_net_edge_percent: {self.config.min_net_edge_percent}%
- max_slippage_bps: {self.config.max_slippage_bps}
- min_source_quality: {self.config.min_source_quality}

PROPOSED TRADES ({len(trades)}):
{json.dumps(trades, indent=2, default=str)}

Review EACH trade independently and assess portfolio-level risk.
For each trade, provide: verdict, risk_score, concerns, reasoning, and recommended_size.
Apply the blocking rules and size reduction triggers from your mandate.
Provide a French summary of overall portfolio risk and any parameter recommendations."""

            response = await asyncio.to_thread(
                self._call_claude, RISK_OFFICER_SYSTEM, prompt
            )

            data = self._extract_json(response)
            if not data or "reviews" not in data:
                logger.warning("[RISK_OFFICER] Failed to parse review response, approving all by default")
                # Conservative fallback: approve all but with reduced sizes
                return {
                    "reviews": [
                        {
                            "market_id": t.get("market_id", "unknown"),
                            "verdict": "approve",
                            "original_size": t.get("size_usdc", 0),
                            "recommended_size": t.get("size_usdc", 0) * 0.8,
                            "risk_score": 5,
                            "concerns": ["Parse failure, conservative sizing applied"],
                            "reasoning": "Fallback approval with 20% size reduction",
                        }
                        for t in trades
                    ],
                    "portfolio_risk_summary": "Erreur de parsing, approbation conservatrice",
                    "total_new_exposure": sum(t.get("size_usdc", 0) for t in trades) * 0.8,
                    "recommended_exposure": sum(t.get("size_usdc", 0) for t in trades) * 0.8,
                    "parameter_recommendations": [],
                }

            # Log summary
            reviews = data.get("reviews", [])
            approved = sum(1 for r in reviews if r.get("verdict") == "approve")
            flagged = sum(1 for r in reviews if r.get("verdict") == "flag")
            rejected = sum(1 for r in reviews if r.get("verdict") == "reject")

            logger.info(
                f"[RISK_OFFICER] Reviewed {len(trades)} trades: "
                f"{approved} approved, {flagged} flagged, {rejected} rejected"
            )

            # Log any high-risk rejections
            for review in reviews:
                if review.get("verdict") == "reject" and review.get("risk_score", 0) >= 8:
                    logger.warning(
                        f"[RISK_OFFICER] HIGH RISK REJECTION: {review.get('market_id', 'unknown')[:16]}... "
                        f"score={review.get('risk_score')}, concerns={review.get('concerns')}"
                    )

            return data

        except Exception as e:
            logger.error(f"[RISK_OFFICER] Review error: {e}", exc_info=True)
            # Ultra-conservative fallback on exception
            return {
                "reviews": [
                    {
                        "market_id": t.get("market_id", "unknown"),
                        "verdict": "flag",
                        "original_size": t.get("size_usdc", 0),
                        "recommended_size": t.get("size_usdc", 0) * 0.5,
                        "risk_score": 7,
                        "concerns": [f"Risk officer error: {str(e)}"],
                        "reasoning": "Exception in risk review, halving all sizes",
                    }
                    for t in trades
                ],
                "portfolio_risk_summary": f"Erreur système, tailles réduites de 50%: {str(e)}",
                "total_new_exposure": sum(t.get("size_usdc", 0) for t in trades) * 0.5,
                "recommended_exposure": sum(t.get("size_usdc", 0) for t in trades) * 0.5,
                "parameter_recommendations": [],
            }

    async def assess_portfolio(self, portfolio_state: dict) -> dict:
        """Post-cycle portfolio risk audit.

        Returns dict with overall_risk_score, concentration_analysis, exposure_summary, warnings.
        """
        try:
            prompt = f"""PORTFOLIO STATE:
{json.dumps(portfolio_state, indent=2, default=str)}

CURRENT RISK CONFIGURATION:
- capital: on-chain USDC.e balance (Polymarket API = source of truth)
- max_concentration_percent: {self.config.max_concentration_percent}%
- max_correlated_positions: {self.config.max_correlated_positions}
- stop_loss_percent: {self.config.stop_loss_percent}%
- drawdown_stop_loss_percent: {self.config.drawdown_stop_loss_percent}%

Perform a comprehensive portfolio risk audit:
1. Analyze concentration by category
2. Identify correlated positions and tail risk scenarios
3. Assess total exposure vs available capital
4. Calculate worst-case loss scenarios
5. Provide warnings and parameter recommendations

Respond with JSON containing: overall_risk_score, concentration_analysis, tail_risk_assessment,
correlation_warnings, exposure_summary (French), warnings, parameter_recommendations."""

            response = await asyncio.to_thread(
                self._call_claude, RISK_OFFICER_SYSTEM, prompt
            )

            data = self._extract_json(response)
            if not data:
                logger.warning("[RISK_OFFICER] Failed to parse portfolio assessment")
                return {
                    "overall_risk_score": 5,
                    "concentration_analysis": {},
                    "tail_risk_assessment": "Unable to assess",
                    "correlation_warnings": [],
                    "exposure_summary": "Erreur d'évaluation du portefeuille",
                    "warnings": ["Portfolio assessment parse failure"],
                    "parameter_recommendations": [],
                }

            risk_score = data.get("overall_risk_score", 5)
            warnings = data.get("warnings", [])

            logger.info(
                f"[RISK_OFFICER] Portfolio risk score: {risk_score}/10, "
                f"{len(warnings)} warnings"
            )

            if risk_score >= 8:
                logger.warning(
                    f"[RISK_OFFICER] HIGH PORTFOLIO RISK: score={risk_score}, "
                    f"warnings={warnings}"
                )

            return data

        except Exception as e:
            logger.error(f"[RISK_OFFICER] Portfolio assessment error: {e}", exc_info=True)
            return {
                "overall_risk_score": 7,
                "concentration_analysis": {},
                "tail_risk_assessment": "Error during assessment",
                "correlation_warnings": [],
                "exposure_summary": f"Erreur: {str(e)}",
                "warnings": [f"Assessment exception: {str(e)}"],
                "parameter_recommendations": [],
            }

    async def answer_question(self, question: str, context: dict) -> str:
        """Answer a natural language question about risk.

        Args:
            question: User's question
            context: Dict with portfolio_state, recent_trades, current_config, etc.

        Returns:
            French-language response string
        """
        try:
            prompt = f"""QUESTION: {question}

CONTEXT:
{json.dumps(context, indent=2, default=str)}

Answer the question in French. Be precise, cite specific numbers, and explain your reasoning.
This is a conversational response, not JSON. Write 2-4 paragraphs."""

            response = await asyncio.to_thread(
                self._call_claude, RISK_OFFICER_SYSTEM, prompt
            )

            # For questions, response is plain text, not JSON
            logger.info(f"[RISK_OFFICER] Answered question: {question[:50]}...")
            return response.strip()

        except Exception as e:
            logger.error(f"[RISK_OFFICER] Question answering error: {e}", exc_info=True)
            return f"Erreur lors de l'analyse de la question: {str(e)}"

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Extract JSON from Claude response with regex fallback."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Try to find JSON block in markdown
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {}
