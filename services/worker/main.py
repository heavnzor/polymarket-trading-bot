"""Polymarket Hybrid Trading Bot — Main Entry Point.

6 concurrent async loops:
1. MM loop (5-10s): Market-making on all viable markets
2. CD loop (15min): Crypto directional on BTC/ETH threshold markets
3. CD exit loop (2min): Automatic exit monitoring for CD positions (stop-loss, take-profit, edge reversal)
4. CD analysis loop (6h): Post-trade Claude Opus review of CD trade quality
5. Claude guard (5min): Resolution clause checks + catalyst detection
6. Maintenance loop (30s): Reconciliation, metrics, kill switch, commands
"""

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone

from config import AppConfig
from db.store import (
    init_db, close_db, cleanup_old_cache,
    init_settings,
    get_open_positions, update_bot_status,
    get_pending_commands, mark_command_executed, mark_command_failed,
    update_settings,
    get_mm_total_exposure,
)
from conversation.router import ConversationRouter
from executor.client import PolymarketClient
from monitor.risk import RiskManager
from monitor.portfolio import PortfolioManager
from monitor.performance import PerformanceTracker
from notifications.telegram_bot import TelegramNotifier
from mm.loop import mm_loop
from mm.claude_guard import claude_guard_loop
from mm.metrics_collector import MetricsCollector
from strategy.cd_loop import cd_loop
from strategy.cd_exit import cd_exit_loop
from strategy.cd_analysis import cd_analysis_loop

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/bot.log", mode="a"),
    ],
)
# Silence noisy HTTP libraries — they log every request at INFO
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("main")


class TradingBot:
    def __init__(self):
        self.config = AppConfig.load()
        self.running = False
        self._process_stopped = False

        # Components
        self.pm_client = PolymarketClient(self.config.polymarket)
        self.risk = RiskManager(self.config.trading, self.config.mm)
        self.portfolio = PortfolioManager(self.pm_client)
        self.performance = PerformanceTracker(self.pm_client)
        self.telegram: TelegramNotifier | None = None
        self.conversation_router: ConversationRouter | None = None
        self.metrics_collector = MetricsCollector(client=self.pm_client)

    async def start(self):
        """Initialize all components and start concurrent loops."""
        logger.info("=" * 60)
        logger.info("Polymarket Hybrid Bot v3 starting...")
        logger.info(f"  MM enabled: {self.config.mm.mm_enabled}")
        logger.info(f"  CD enabled: {self.config.cd.cd_enabled}")
        logger.info(f"  Guard enabled: {self.config.guard.guard_enabled}")
        logger.info(
            f"  Heartbeat: {'ON' if self.config.trading.heartbeat_enabled else 'OFF'} "
            f"({self.config.trading.heartbeat_interval_seconds}s)"
        )
        logger.info("=" * 60)

        # Initialize DB
        await init_db()
        await init_settings(self.config.trading)

        # Conversation router for Telegram + dashboard chat.
        self.conversation_router = ConversationRouter(
            pm_client=self.pm_client,
            portfolio_manager=self.portfolio,
            risk_manager=self.risk,
            performance_tracker=self.performance,
            trading_config=self.config.trading,
            anthropic_config=self.config.anthropic,
            stop_callback=self.stop,
        )

        # Connect to CLOB
        try:
            self.pm_client.connect()
        except Exception as e:
            logger.error(f"Failed to connect to Polymarket CLOB: {e}")
            raise

        # Check balance
        balance = self.pm_client.get_onchain_balance()
        if balance is not None:
            logger.info(f"On-chain USDC.e balance: ${balance:.2f}")
        else:
            logger.warning("Could not fetch on-chain balance")

        # Start Telegram bot
        if self.config.telegram.bot_token:
            try:
                self.telegram = TelegramNotifier(self.config.telegram)
                self.telegram.set_managers(self.portfolio, self.risk)
                self.telegram.set_performance_tracker(self.performance)
                self.telegram.set_trading_config(self.config.trading)
                self.telegram.set_bot_controls(None, self.stop)
                self.telegram.set_conversation_router(self.conversation_router)
                await self.telegram.initialize()
                await self.telegram.send_message(
                    "🤖 Bot Hybrid v3 démarré\n"
                    f"MM: {'ON' if self.config.mm.mm_enabled else 'OFF'} | "
                    f"CD: {'ON' if self.config.cd.cd_enabled else 'OFF'}\n"
                    f"Balance: ${balance:.2f}" if balance else ""
                )
            except Exception as e:
                logger.error(f"Telegram init failed: {e}")

        await update_bot_status({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "version": "3.0-hybrid",
        })

        self.running = True
        self._dd_notified = False
        self._kill_notified = False

        # Launch concurrent loops
        tasks = []

        if self.config.mm.mm_enabled:
            tasks.append(asyncio.create_task(
                self._safe_loop("mm", mm_loop(self.config, self.pm_client, self.risk))
            ))

        if self.config.cd.cd_enabled:
            tasks.append(asyncio.create_task(
                self._safe_loop("cd", cd_loop(self.config, self.pm_client, self.risk))
            ))

        if self.config.cd.cd_enabled and self.config.cd.cd_exit_enabled:
            tasks.append(asyncio.create_task(
                self._safe_loop("cd_exit", cd_exit_loop(self.config, self.pm_client, self.risk))
            ))

        if self.config.cd.cd_enabled and self.config.cd.cd_analysis_enabled:
            tasks.append(asyncio.create_task(
                self._safe_loop("cd_analysis", cd_analysis_loop(self.config))
            ))

        if self.config.guard.guard_enabled:
            tasks.append(asyncio.create_task(
                self._safe_loop("guard", claude_guard_loop(self.config, self.pm_client))
            ))

        if self.config.trading.heartbeat_enabled:
            tasks.append(asyncio.create_task(
                self._safe_loop("heartbeat", self._heartbeat_loop())
            ))

        tasks.append(asyncio.create_task(
            self._safe_loop("maintenance", self._maintenance_loop())
        ))

        logger.info(f"Started {len(tasks)} concurrent loops")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled")
        except Exception as e:
            logger.error(f"Fatal error in bot: {e}", exc_info=True)

    async def _safe_loop(self, name: str, coro):
        """Wrapper that catches and logs errors without crashing other loops."""
        try:
            await coro
        except asyncio.CancelledError:
            logger.info(f"{name} loop cancelled")
        except Exception as e:
            logger.error(f"FATAL: {name} loop crashed: {e}", exc_info=True)
            if self.telegram:
                try:
                    await self.telegram.send_message(
                        f"⚠️ Loop {name} crashed: {str(e)[:200]}"
                    )
                except Exception:
                    pass

    async def _maintenance_loop(self):
        """Maintenance loop: reconciliation, metrics, commands, kill switch.

        Runs every 30 seconds.
        """
        cycle = 0
        while self.running:
            try:
                cycle += 1

                # 1. Process dashboard commands
                await self._process_commands()

                # 2. Check resolution on open positions
                await self.performance.check_resolutions()

                # 3. Portfolio state snapshot (prices refreshed on-demand)
                positions = await get_open_positions()

                # 4. Exposure monitoring (informational, no hardcoded cap)
                total_exposure = await get_mm_total_exposure()

                # 4b. Cumulative drawdown check
                try:
                    portfolio_state = await self.portfolio.get_portfolio_state()
                    portfolio_value = portfolio_state.get("portfolio_value", 0)
                    triggered, dd_pct = await self.risk.check_drawdown_stop_loss(portfolio_value)
                    if triggered and self.telegram and not self._dd_notified:
                        self._dd_notified = True
                        await self.telegram.send_message(
                            f"DRAWDOWN STOP-LOSS: {dd_pct:.1f}% from peak. Trading paused."
                        )
                    elif not triggered:
                        self._dd_notified = False

                    # MM drawdown check (from HWM, tighter thresholds)
                    dd_action = await self.risk.check_intraday_dd(portfolio_value)
                    if dd_action == "kill":
                        self.pm_client.cancel_all_orders()
                        if self.telegram and not self._kill_notified:
                            self._kill_notified = True
                            await self.telegram.send_message(
                                f"MM KILL SWITCH: DD from peak triggered. All orders cancelled."
                            )
                    elif dd_action == "ok":
                        self._kill_notified = False

                    # Auto-recovery check (hysteresis + cooldown)
                    if self.risk.is_paused:
                        resumed = await self.risk.try_auto_resume(portfolio_value)
                        if resumed:
                            self._kill_notified = False
                            if self.telegram:
                                await self.telegram.send_message(
                                    f"MM AUTO-RESUME: DD recovered below threshold. "
                                    f"Trading resumed automatically."
                                )
                except Exception as e:
                    logger.debug(f"Risk check error: {e}")

                # 5a. Adverse selection measurement (every cycle)
                try:
                    await self.metrics_collector.measure_adverse_selection()
                except Exception as e:
                    logger.debug(f"AS measurement error: {e}")

                # 5. Clean old cache
                if cycle % 120 == 0:  # Every hour
                    await cleanup_old_cache(max_age_hours=12)

                # 6. Daily metrics aggregation (every 10 min)
                if cycle % 20 == 0:
                    try:
                        daily = await self.metrics_collector.compute_daily_metrics()
                        if daily:
                            logger.info(
                                f"MM metrics: PnL={daily.get('pnl_net', 0):.4f} "
                                f"FQ={daily.get('fill_quality_avg', 0):.1f}bps "
                                f"AS={daily.get('adverse_selection_avg', 0):.1f}bps "
                                f"Sharpe={daily.get('sharpe_7d', 0):.2f}"
                            )
                    except Exception as e:
                        logger.debug(f"Daily metrics error: {e}")

                # 7. Update status
                await update_bot_status({
                    "maintenance_cycle": cycle,
                    "maintenance_last": datetime.now(timezone.utc).isoformat(),
                    "positions_count": len(positions),
                })

            except Exception as e:
                logger.error(f"Maintenance error in cycle {cycle}: {e}", exc_info=True)

            await asyncio.sleep(30)

    async def _heartbeat_loop(self):
        """Send periodic CLOB heartbeat to keep orders alive."""
        interval = max(3, int(self.config.trading.heartbeat_interval_seconds))
        consecutive_failures = 0
        max_failures_before_reconnect = 20
        reconnect_count = 0
        while self.running:
            try:
                ok = await asyncio.to_thread(self.pm_client.post_heartbeat, None)
                if ok:
                    if consecutive_failures > 0:
                        logger.info(f"Heartbeat recovered after {consecutive_failures} failures")
                    consecutive_failures = 0
                    reconnect_count = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures % 20 == 0:
                        logger.warning(f"Heartbeat failed ({consecutive_failures} consecutive)")
                    if consecutive_failures >= max_failures_before_reconnect:
                        # Exponential backoff: 30s, 60s, 120s, max 120s
                        backoff = min(120, 30 * (2 ** reconnect_count))
                        logger.warning(
                            f"Heartbeat failed {consecutive_failures}x, "
                            f"reconnecting CLOB client (backoff={backoff}s)..."
                        )
                        await asyncio.sleep(backoff)
                        try:
                            await asyncio.to_thread(self.pm_client.connect)
                            logger.info("CLOB client reconnected successfully")
                            consecutive_failures = 0
                            reconnect_count += 1
                        except Exception as reconn_err:
                            logger.error(f"CLOB reconnect failed: {reconn_err}")
                            consecutive_failures = 0
                            reconnect_count += 1
            except Exception as e:
                consecutive_failures += 1
                logger.error(f"Heartbeat loop error: {e}", exc_info=True)
            await asyncio.sleep(interval)

    async def _process_commands(self):
        """Process commands from dashboard/telegram."""
        commands = await get_pending_commands()
        for cmd in commands:
            try:
                command = cmd["command"]
                payload = cmd.get("payload")

                if command == "pause":
                    self.risk.is_paused = True
                    await mark_command_executed(cmd["id"], {"result": "paused"})

                elif command == "resume":
                    self.risk.resume_trading()
                    await mark_command_executed(cmd["id"], {"result": "resumed"})

                elif command == "kill":
                    # Emergency: cancel all orders
                    self.pm_client.cancel_all_orders()
                    self.risk.is_paused = True
                    await mark_command_executed(cmd["id"], {"result": "killed"})
                    if self.telegram:
                        await self.telegram.send_message("🛑 KILL SWITCH activé — tous ordres annulés")

                elif command == "update_settings":
                    if payload:
                        updates = json.loads(payload) if isinstance(payload, str) else payload
                        await update_settings(updates)
                        await mark_command_executed(cmd["id"], {"result": "settings_updated"})

                elif command == "chat_message":
                    if not self.conversation_router:
                        await mark_command_failed(cmd["id"], "Conversation router unavailable")
                        continue

                    payload_obj = json.loads(payload) if isinstance(payload, str) else (payload or {})
                    message = (payload_obj.get("message") or "").strip()
                    source = payload_obj.get("source", "dashboard")
                    if not message:
                        await mark_command_failed(cmd["id"], "Missing chat message")
                        continue

                    reply = await self.conversation_router.handle_message(message, source)
                    await mark_command_executed(
                        cmd["id"],
                        {
                            "result": "chat_replied",
                            "agent": reply.get("agent", "general"),
                            "response": reply.get("response", ""),
                            "action_taken": reply.get("action_taken"),
                        },
                    )

                else:
                    await mark_command_failed(cmd["id"], f"Unknown command: {command}")

            except Exception as e:
                logger.error(f"Command processing error: {e}")
                await mark_command_failed(cmd["id"], str(e))

    async def stop(self):
        """Graceful shutdown."""
        logger.info("Stopping bot...")
        self.running = False

        # Cancel all open orders
        try:
            self.pm_client.cancel_all_orders()
            logger.info("All orders cancelled")
        except Exception as e:
            logger.error(f"Error cancelling orders on shutdown: {e}")

        # Stop telegram
        if self.telegram:
            try:
                await self.telegram.send_message("🔴 Bot arrêté")
                await self.telegram.shutdown()
            except Exception:
                pass

        await update_bot_status({"status": "stopped"})
        await close_db()
        logger.info("Bot stopped cleanly")


async def main():
    bot = TradingBot()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()
    except Exception as e:
        logger.critical(f"Bot crashed: {e}", exc_info=True)
        await bot.stop()
        raise


if __name__ == "__main__":
    asyncio.run(main())
