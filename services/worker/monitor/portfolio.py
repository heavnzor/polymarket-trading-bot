import asyncio
import logging
import time
from executor.client import PolymarketClient
from db.store import get_open_positions, get_trades, get_daily_traded

logger = logging.getLogger(__name__)

# Rate-limit interval for repeated warnings (seconds)
_WARN_INTERVAL = 60


class PortfolioManager:
    def __init__(self, polymarket_client: PolymarketClient):
        self.pm = polymarket_client
        self._last_onchain_balance: float | None = None

    async def get_portfolio_state(self) -> dict:
        """Get current portfolio state. On-chain balance is the source of truth."""
        positions = await get_open_positions()
        trades = await get_trades(limit=20)
        daily_traded = await get_daily_traded()

        total_invested = sum(p.get("size", 0) * p.get("avg_price", 0) for p in positions)

        pnl = 0.0
        positions_summary_lines = []
        for pos in positions:
            token_id = pos.get("token_id", "")
            current_price = await asyncio.to_thread(self.pm.get_midpoint, token_id)
            if current_price is not None:
                pos_pnl = (current_price - pos["avg_price"]) * pos["size"]
                pnl += pos_pnl
                positions_summary_lines.append(
                    f"  - {pos.get('market_question', 'Unknown')}: "
                    f"{pos['outcome']} x{pos['size']:.1f} @ {pos['avg_price']:.3f} "
                    f"(now {current_price:.3f}, PnL: ${pos_pnl:.2f})"
                )

        positions_summary = "\n".join(positions_summary_lines) if positions_summary_lines else "None"

        # On-chain balance = source of truth for available capital
        onchain = await asyncio.to_thread(self.pm.get_onchain_balance)
        if onchain is not None:
            self._last_onchain_balance = onchain

        available = self._last_onchain_balance or 0.0
        portfolio_value = available + total_invested + pnl

        return {
            "available_usdc": available,
            "positions_count": len(positions),
            "positions": positions,
            "positions_summary": positions_summary,
            "daily_pnl": pnl,
            "daily_traded": daily_traded,
            "total_invested": total_invested,
            "recent_trades": trades,
            "portfolio_value": portfolio_value,
            "onchain_balance": self._last_onchain_balance,
        }
