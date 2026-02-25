"""Risk management for hybrid MM + CD trading bot."""

import logging
import time
from datetime import datetime, timezone
from config import TradingConfig, MarketMakingConfig
from db.store import (
    get_daily_traded, get_open_positions, get_category_exposure,
    get_high_water_mark, update_high_water_mark,
    get_trades_by_status, get_mm_total_exposure,
    get_open_cd_positions,
)

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, trading_config: TradingConfig, mm_config: MarketMakingConfig = None):
        self.config = trading_config
        self.mm_config = mm_config or MarketMakingConfig()
        self._paused = False
        self._dd_logged = False
        self._kill_logged = False
        self._reduce_logged = False
        # Auto-recovery state
        self._kill_triggered_at: float | None = None
        self._auto_recoveries_today: int = 0
        self._auto_recovery_date: str | None = None
        self._risk_mode: str = "ok"

    # ═══════════════════════════════════════════════════════════════════
    # GENERAL RISK CHECKS
    # ═══════════════════════════════════════════════════════════════════

    async def check_stop_loss(self, total_pnl: float, portfolio_value: float) -> bool:
        """Check daily stop-loss based on portfolio value (on-chain + positions)."""
        if portfolio_value <= 0:
            return False
        loss_pct = abs(total_pnl / portfolio_value) * 100 if total_pnl < 0 else 0
        if loss_pct >= self.config.stop_loss_percent:
            self._paused = True
            logger.warning(f"DAILY STOP-LOSS triggered: {loss_pct:.1f}% loss")
            return True
        return False

    async def check_drawdown_stop_loss(self, portfolio_value: float) -> tuple[bool, float]:
        """Check cumulative drawdown from high-water mark."""
        await update_high_water_mark(portfolio_value)
        hwm = await get_high_water_mark()

        peak = hwm["peak_value"]
        drawdown_pct = (peak - portfolio_value) / peak * 100 if peak > 0 else 0

        if drawdown_pct >= self.config.drawdown_stop_loss_percent:
            self._paused = True
            if not self._dd_logged:
                logger.warning(
                    f"DRAWDOWN STOP-LOSS triggered: {drawdown_pct:.1f}% from peak "
                    f"(peak=${peak:.2f}, current=${portfolio_value:.2f})"
                )
                self._dd_logged = True
            return True, drawdown_pct

        self._dd_logged = False
        return False, drawdown_pct

    def resume_trading(self):
        """Manually resume trading after stop-loss."""
        self._paused = False
        logger.info("Trading resumed manually")

    @property
    def is_paused(self) -> bool:
        return self._paused

    @is_paused.setter
    def is_paused(self, value: bool):
        self._paused = value
        if value:
            logger.info("Trading paused via command")
        else:
            logger.info("Trading resumed via command")

    @property
    def risk_mode(self) -> str:
        """Current risk mode: 'ok', 'reduce', or 'kill'."""
        return self._risk_mode

    # ═══════════════════════════════════════════════════════════════════
    # MM-SPECIFIC RISK CHECKS
    # ═══════════════════════════════════════════════════════════════════

    def validate_mm_quote(
        self, bid: float, ask: float, mid: float, max_delta: float
    ) -> tuple[bool, str]:
        """Validate a market-making quote before placement.

        Checks:
        - bid < ask
        - total spread within 2 * max_delta (allows asymmetric skew)
        - each side within max_delta * 2 (hard safety cap per side)
        - prices in valid range (0.01 - 0.99)
        """
        if self._paused:
            return False, "Trading paused"

        if bid >= ask:
            return False, f"Invalid quote: bid {bid:.2f} >= ask {ask:.2f}"

        if bid < 0.01 or ask > 0.99:
            return False, f"Quote out of range: bid={bid:.2f}, ask={ask:.2f}"

        spread = round((ask - bid) * 100, 2)
        max_spread = min(2 * max_delta + 1.0, self.mm_config.mm_max_spread_pts if self.mm_config else 12.0)
        if spread > max_spread:
            return False, (
                f"Spread too wide: {spread:.1f}pts > {max_spread:.1f}pts"
            )

        # Hard cap per side: no side further than 2x max_delta from mid
        bid_delta = abs(mid - bid) * 100
        ask_delta = abs(ask - mid) * 100
        hard_cap = max_delta * 2
        if bid_delta > hard_cap or ask_delta > hard_cap:
            return False, (
                f"Delta too wide: bid_delta={bid_delta:.1f}pts, "
                f"ask_delta={ask_delta:.1f}pts, hard_cap={hard_cap:.1f}pts"
            )

        if spread < 1.0:
            return False, f"Spread too tight: {spread:.1f}pts < 1.0pts minimum"

        return True, "OK"

    async def check_intraday_dd(self, portfolio_value: float) -> str:
        """Check drawdown from high-water mark for MM kill switch.

        Uses the same HWM as check_drawdown_stop_loss but with tighter
        thresholds specific to MM (reduce at mm_dd_reduce_pct, kill at
        mm_dd_kill_pct). Logs are throttled to avoid spam.

        Returns:
        - 'ok': continue normally
        - 'reduce': reduce exposure by 50%
        - 'kill': stop all MM activity
        """
        if self.mm_config is None:
            return "ok"

        hwm = await get_high_water_mark()
        peak = hwm["peak_value"]
        dd_pct = (peak - portfolio_value) / peak * 100 if peak > 0 else 0

        if dd_pct >= self.mm_config.mm_dd_kill_pct:
            self._paused = True
            if self._kill_triggered_at is None:
                self._kill_triggered_at = time.monotonic()
            if not self._kill_logged:
                logger.critical(
                    f"MM KILL SWITCH: DD {dd_pct:.1f}% >= {self.mm_config.mm_dd_kill_pct}% "
                    f"(peak=${peak:.2f}, current=${portfolio_value:.2f})"
                )
                self._kill_logged = True
            self._risk_mode = "kill"
            return "kill"

        if dd_pct >= self.mm_config.mm_dd_reduce_pct:
            if not self._reduce_logged:
                logger.warning(
                    f"MM REDUCE: DD {dd_pct:.1f}% >= {self.mm_config.mm_dd_reduce_pct}% "
                    f"(peak=${peak:.2f}, current=${portfolio_value:.2f})"
                )
                self._reduce_logged = True
            self._risk_mode = "reduce"
            return "reduce"

        # Back to normal — reset log throttles
        self._kill_logged = False
        self._reduce_logged = False
        self._risk_mode = "ok"
        return "ok"

    async def try_auto_resume(self, portfolio_value: float) -> bool:
        """Try to auto-resume after MM kill switch, with hysteresis + cooldown.

        Conditions for auto-resume:
        1. Bot is paused AND kill was triggered (not manual pause)
        2. DD has recovered below mm_dd_resume_pct
        3. Cooldown period has elapsed since kill trigger
        4. Daily auto-recovery count not exceeded

        Returns True if trading was resumed, False otherwise.
        """
        if not self._paused or self._kill_triggered_at is None:
            return False

        if self.mm_config is None:
            return False

        # Check DD has recovered
        hwm = await get_high_water_mark()
        peak = hwm["peak_value"]
        dd_pct = (peak - portfolio_value) / peak * 100 if peak > 0 else 0

        if dd_pct >= self.mm_config.mm_dd_resume_pct:
            return False

        # Check cooldown elapsed
        elapsed = time.monotonic() - self._kill_triggered_at
        cooldown_seconds = self.mm_config.mm_dd_cooldown_minutes * 60
        if elapsed < cooldown_seconds:
            return False

        # Check daily recovery limit (reset on new day)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._auto_recovery_date != today:
            self._auto_recoveries_today = 0
            self._auto_recovery_date = today

        if self._auto_recoveries_today >= self.mm_config.mm_dd_max_recoveries_per_day:
            return False

        # All conditions met — resume
        self._paused = False
        self._auto_recoveries_today += 1
        self._kill_triggered_at = None
        self._kill_logged = False
        self._reduce_logged = False
        logger.info(
            f"MM AUTO-RESUME: DD {dd_pct:.1f}% < {self.mm_config.mm_dd_resume_pct}% "
            f"after {elapsed / 60:.0f}min cooldown "
            f"(recovery {self._auto_recoveries_today}/{self.mm_config.mm_dd_max_recoveries_per_day} today)"
        )
        return True

    def check_inventory_risk(
        self, net_inventory: float, max_inventory: float
    ) -> tuple[bool, str]:
        """Check if inventory is within acceptable limits."""
        if abs(net_inventory) > max_inventory:
            return False, (
                f"Inventory {net_inventory:.1f} exceeds max {max_inventory:.1f}"
            )
        utilization = abs(net_inventory) / max_inventory if max_inventory > 0 else 0
        if utilization > 0.9:
            return True, f"WARNING: inventory at {utilization:.0%} capacity"
        return True, "OK"

    # ═══════════════════════════════════════════════════════════════════
    # GLOBAL EXPOSURE CHECK
    # ═══════════════════════════════════════════════════════════════════

    async def check_global_exposure(self, onchain_balance: float) -> tuple[bool, float]:
        """Check total exposure (MM + CD) against max_total_exposure_pct.

        Denominator is total portfolio (cash + positions), not just cash.
        This avoids the ratio exploding as capital moves from cash to positions.

        Returns (within_limit, exposure_pct).
        """
        if onchain_balance <= 0:
            return True, 0.0

        mm_exposure = await get_mm_total_exposure()

        cd_positions = await get_open_cd_positions()
        cd_exposure = sum(
            float(p.get("shares", 0)) * float(p.get("entry_price", 0))
            for p in cd_positions
        )

        total_exposure = mm_exposure + cd_exposure
        total_portfolio = onchain_balance + total_exposure
        exposure_pct = (total_exposure / total_portfolio) * 100 if total_portfolio > 0 else 0.0

        within_limit = exposure_pct <= self.config.max_total_exposure_pct
        return within_limit, round(exposure_pct, 1)

    # ═══════════════════════════════════════════════════════════════════
    # CD-SPECIFIC RISK CHECKS
    # ═══════════════════════════════════════════════════════════════════

    async def validate_cd_trade(
        self, trade: dict, available_usdc: float
    ) -> tuple[bool, str]:
        """Validate a crypto directional trade."""
        if self._paused:
            return False, "Trading paused"

        size = trade.get("size_usdc", 0)
        edge = float(trade.get("edge_pts", 0) or 0)

        if size <= 0:
            return False, "Trade size must be positive"

        if size > available_usdc:
            return False, f"Insufficient funds: need ${size:.2f}, have ${available_usdc:.2f}"

        # Daily limit check — no hardcoded budget, use available as reference
        daily_traded = await get_daily_traded()
        if available_usdc > 0 and daily_traded + size > available_usdc * 0.5:
            return False, f"CD daily limit reached: ${daily_traded:.2f} traded"

        # Edge minimum
        if edge < 5.0:
            return False, f"Edge {edge:.1f}pts below 5.0pts minimum"

        return True, "OK"
