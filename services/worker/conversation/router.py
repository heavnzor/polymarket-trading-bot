"""Natural-language conversation router for Telegram and dashboard chat."""

from __future__ import annotations

import asyncio
import os
import json
import logging
import re
import time
import uuid
from collections import Counter, deque
from pathlib import Path
from typing import Any

from db import store
from learning.risk_officer import RiskOfficerAgent
from learning.strategist import StrategistAgent

logger = logging.getLogger(__name__)

BOOL_TRUE = {"1", "true", "yes", "on", "active", "enabled"}
BOOL_FALSE = {"0", "false", "no", "off", "inactive", "disabled"}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_ACTIONS = {
    "none",
    "pause",
    "resume",
    "force_cycle",
    "kill",
    "restart",
    "stop_process",
    "start_process",
    "update_settings",
    "analyze_logs",
}


class ConversationRouter:
    """Route free-text messages to operational handlers and optional agents."""

    def __init__(
        self,
        pm_client,
        portfolio_manager,
        risk_manager,
        performance_tracker,
        trading_config,
        anthropic_config=None,
        force_cycle_event=None,
        stop_callback=None,
        stop_process_callback=None,
        start_process_callback=None,
    ):
        self.pm_client = pm_client
        self.portfolio = portfolio_manager
        self.risk = risk_manager
        self.performance = performance_tracker
        self.config = trading_config
        self.anthropic = anthropic_config
        self.force_cycle_event = force_cycle_event
        self.stop_callback = stop_callback
        self.stop_process_callback = stop_process_callback
        self.start_process_callback = start_process_callback
        self._bot_log_path = Path(os.getenv("BOT_LOG_FILE", str(PROJECT_ROOT / "logs" / "bot.log")))

        self._pending_actions: dict[str, dict[str, Any]] = {}
        self._last_action_by_source: dict[str, str] = {}
        self._action_ttl_seconds = 15 * 60

        # Optional LLM agents for deep Q&A.
        self._risk_agent: RiskOfficerAgent | None = None
        self._strategist_agent: StrategistAgent | None = None
        if self._anthropic_ready:
            self._risk_agent = RiskOfficerAgent(self._call_claude, self.pm_client, self.config)
            self._strategist_agent = StrategistAgent(self._call_claude, self.pm_client, self.config)

    @property
    def _anthropic_ready(self) -> bool:
        return bool(self.anthropic and getattr(self.anthropic, "api_key", ""))

    async def handle_message(self, message: str, source: str = "telegram") -> dict[str, Any]:
        """Route one user message and persist both user/agent turns."""
        text = (message or "").strip()
        if not text:
            return {"agent": "general", "response": "Message vide.", "action_taken": None}

        await self._persist_turn(source, "user", "general", text)

        settings = await self._get_settings_values()
        conversation_enabled = self._conversation_enabled(settings)
        normalized = self._normalize(text)

        # Allow explicit re-activation even if conversation setting is off.
        if not conversation_enabled and self._looks_like_enable_conversation(normalized):
            result = await self._execute_action(
                {
                    "source": source,
                    "type": "update_settings",
                    "payload": {"updates": {"conversation_enabled": "true"}},
                    "description": "Activer l'interface conversationnelle",
                }
            )
            response = (
                "Interface conversationnelle reactivee."
                if result.get("success")
                else f"Echec activation: {result.get('error', 'inconnu')}"
            )
            out = {"agent": "manager", "response": response, "action_taken": None}
            await self._persist_turn(source, "agent", out["agent"], out["response"])
            return out

        if not conversation_enabled:
            out = {
                "agent": "manager",
                "response": (
                    "Interface conversationnelle desactivee. "
                    "Pour l'activer, ecris: 'active conversation'."
                ),
                "action_taken": None,
            }
            await self._persist_turn(source, "agent", out["agent"], out["response"])
            return out

        result = await self._route(text, normalized, source, settings)
        action = result.get("action_taken")
        await self._persist_turn(
            source,
            "agent",
            result.get("agent", "general"),
            result.get("response", ""),
            action,
        )
        return result

    async def execute_confirmed_action(self, action_id: str) -> dict[str, Any]:
        """Execute an action queued by a previous conversation turn."""
        self._prune_actions()
        action = self._pending_actions.get(action_id)
        if not action:
            return {"success": False, "error": "Action introuvable ou expiree."}

        result = await self._execute_action(action)
        if result.get("success"):
            self._pending_actions.pop(action_id, None)
            source = action.get("source", "telegram")
            self._last_action_by_source.pop(source, None)
            await self._persist_turn(
                source,
                "agent",
                "manager",
                f"Action executee: {action.get('description', action.get('type', 'action'))}",
                {"executed_action_id": action_id, "result": result},
            )
        return result

    async def _route(
        self,
        original_text: str,
        text: str,
        source: str,
        settings: dict[str, str],
    ) -> dict[str, Any]:
        self._prune_actions()

        confirm_id = self._extract_confirm_id(text, source)
        if confirm_id:
            action = self._pending_actions.get(confirm_id)
            if not action:
                return {
                    "agent": "manager",
                    "response": "Action introuvable ou expiree.",
                    "action_taken": None,
                }
            result = await self._execute_action(action)
            if result.get("success"):
                self._pending_actions.pop(confirm_id, None)
                self._last_action_by_source.pop(source, None)
            if result.get("success"):
                return {
                    "agent": "manager",
                    "response": result.get("message", "Action confirmee et executee."),
                    "action_taken": None,
                }
            return {
                "agent": "manager",
                "response": f"Confirmation echouee: {result.get('error', 'erreur inconnue')}",
                "action_taken": None,
            }

        cancel_id = self._extract_cancel_id(text, source)
        if cancel_id:
            action = self._pending_actions.pop(cancel_id, None)
            if action:
                self._last_action_by_source.pop(source, None)
                return {
                    "agent": "manager",
                    "response": f"Action annulee: {action.get('description', action.get('type'))}",
                    "action_taken": None,
                }
            return {"agent": "manager", "response": "Aucune action a annuler.", "action_taken": None}

        if self._is_help_query(text):
            return {"agent": "manager", "response": self._help_text(), "action_taken": None}

        claude_routed = await self._route_with_claude(original_text, source, settings)
        if claude_routed is not None:
            return claude_routed

        # Operational commands.
        if self._is_pause_command(text):
            result = await self._execute_action(
                {"source": source, "type": "pause", "description": "Mettre le trading en pause"}
            )
            if result.get("success"):
                return {"agent": "manager", "response": result["message"], "action_taken": None}
            return {"agent": "manager", "response": result.get("error", "Erreur pause"), "action_taken": None}

        if self._is_resume_command(text):
            result = await self._execute_action(
                {"source": source, "type": "resume", "description": "Reprendre le trading"}
            )
            if result.get("success"):
                return {"agent": "manager", "response": result["message"], "action_taken": None}
            return {"agent": "manager", "response": result.get("error", "Erreur reprise"), "action_taken": None}

        if self._is_force_cycle_command(text):
            result = await self._execute_action(
                {"source": source, "type": "force_cycle", "description": "Forcer un cycle d'analyse"}
            )
            if result.get("success"):
                return {"agent": "manager", "response": result["message"], "action_taken": None}
            return {"agent": "manager", "response": result.get("error", "Force indisponible"), "action_taken": None}

        setting_updates = self._parse_setting_updates(text)
        if setting_updates:
            return self._queue_confirmation(
                source=source,
                action_type="update_settings",
                description=f"Appliquer les reglages: {self._format_updates(setting_updates)}",
                payload={"updates": setting_updates},
                agent="manager",
            )

        if self._is_kill_command(text):
            return self._queue_confirmation(
                source=source,
                action_type="kill",
                description="Annuler tous les ordres et mettre le bot en pause",
                payload={},
                agent="risk_officer",
            )

        if self._is_restart_command(text):
            return self._queue_confirmation(
                source=source,
                action_type="restart",
                description="Redemarrer le bot",
                payload={},
                agent="manager",
            )

        if self._is_stop_process_command(text):
            return self._queue_confirmation(
                source=source,
                action_type="stop_process",
                description="Stopper le processus bot",
                payload={},
                agent="manager",
            )

        if self._is_start_process_command(text):
            return self._queue_confirmation(
                source=source,
                action_type="start_process",
                description="Demarrer le processus bot",
                payload={},
                agent="manager",
            )

        if self._is_logs_diagnostic_request(text):
            return {
                "agent": "manager",
                "response": await self._logs_diagnostic_text(
                    use_claude=self._mentions_claude_analysis(text),
                ),
                "action_taken": None,
            }

        # Data snapshots.
        if self._is_mm_query(text):
            return {"agent": "manager", "response": await self._mm_status_text(), "action_taken": None}
        if self._is_cd_query(text):
            return {"agent": "manager", "response": await self._cd_status_text(), "action_taken": None}
        if self._is_positions_query(text):
            return {"agent": "manager", "response": await self._positions_text(), "action_taken": None}
        if self._is_performance_query(text):
            return {"agent": "manager", "response": await self._performance_text(), "action_taken": None}
        if self._is_settings_query(text):
            return {"agent": "manager", "response": await self._settings_text(settings), "action_taken": None}
        if self._is_status_query(text):
            return {"agent": "manager", "response": await self._status_text(), "action_taken": None}

        # Agent-specific Q&A.
        if self._is_risk_question(text):
            return {
                "agent": "risk_officer",
                "response": await self._answer_risk_question(original_text, source),
                "action_taken": None,
            }
        if self._is_strategy_question(text):
            return {
                "agent": "strategist",
                "response": await self._answer_strategy_question(original_text, source),
                "action_taken": None,
            }
        # Default fallback: concise status + capabilities.
        status_text = await self._status_text()
        return {
            "agent": "general",
            "response": (
                f"{status_text}\n\n"
                "Commandes NL utiles: status, positions, performance, mm, cd, reglages, "
                "pause, resume, kill, restart."
            ),
            "action_taken": None,
        }

    async def _persist_turn(
        self,
        source: str,
        role: str,
        agent_name: str,
        message: str,
        action_taken: dict[str, Any] | None = None,
    ) -> None:
        try:
            await store.insert_conversation_turn(
                {
                    "source": source,
                    "role": role,
                    "agent_name": agent_name,
                    "message": message,
                    "action_taken": json.dumps(action_taken, ensure_ascii=False) if action_taken else None,
                }
            )
        except Exception as exc:
            logger.debug(f"Failed to persist conversation turn: {exc}")

    async def _get_settings_values(self) -> dict[str, str]:
        try:
            return await store.get_settings_values()
        except Exception as exc:
            logger.debug(f"Cannot read settings values: {exc}")
            return {}

    def _conversation_enabled(self, settings: dict[str, str]) -> bool:
        raw = settings.get("conversation_enabled")
        if raw is None:
            return bool(getattr(self.config, "conversation_enabled", True))
        return self._as_bool(raw, default=True)

    def _conversation_history_limit(self, settings: dict[str, str]) -> int:
        raw = settings.get("conversation_max_history")
        if raw is None:
            return int(getattr(self.config, "conversation_max_history", 20))
        try:
            return max(5, min(100, int(float(raw))))
        except (TypeError, ValueError):
            return int(getattr(self.config, "conversation_max_history", 20))

    def _queue_confirmation(
        self,
        source: str,
        action_type: str,
        description: str,
        payload: dict[str, Any],
        agent: str = "manager",
    ) -> dict[str, Any]:
        action_id = uuid.uuid4().hex[:8]
        action = {
            "id": action_id,
            "source": source,
            "type": action_type,
            "description": description,
            "payload": payload,
            "created_at": time.time(),
        }
        self._pending_actions[action_id] = action
        self._last_action_by_source[source] = action_id

        if source == "telegram":
            confirmation_line = "Confirme cette action avec le bouton ci-dessous."
        else:
            confirmation_line = f"Confirme en envoyant: confirmer {action_id}"

        return {
            "agent": agent,
            "response": (
                f"Action sensible detectee: {description}.\n"
                f"ID action: {action_id}\n"
                f"{confirmation_line}"
            ),
            "action_taken": {
                "id": action_id,
                "type": action_type,
                "description": description,
                "requires_confirmation": True,
            },
        }

    async def _execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = action.get("type")
        payload = action.get("payload") or {}

        try:
            if action_type == "pause":
                if not self.risk:
                    return {"success": False, "error": "Risk manager indisponible."}
                self.risk.is_paused = True
                return {"success": True, "message": "Trading mis en pause."}

            if action_type == "resume":
                if not self.risk:
                    return {"success": False, "error": "Risk manager indisponible."}
                self.risk.resume_trading()
                if self.force_cycle_event:
                    self.force_cycle_event.set()
                return {"success": True, "message": "Trading repris."}

            if action_type == "force_cycle":
                if not self.force_cycle_event:
                    return {
                        "success": False,
                        "error": "Force cycle non branche dans ce runtime.",
                    }
                self.force_cycle_event.set()
                return {"success": True, "message": "Cycle force demande."}

            if action_type == "kill":
                cancelled = await asyncio.to_thread(self.pm_client.cancel_all_orders)
                if self.risk:
                    self.risk.is_paused = True
                if not cancelled:
                    return {"success": False, "error": "Echec cancel_all_orders()."}
                return {
                    "success": True,
                    "message": "Kill switch execute: ordres annules et trading en pause.",
                }

            if action_type == "restart":
                if not self.stop_callback:
                    return {"success": False, "error": "Callback de restart indisponible."}
                await self.stop_callback()
                return {"success": True, "message": "Redemarrage du bot lance."}

            if action_type == "stop_process":
                if not self.stop_process_callback:
                    return {"success": False, "error": "Commande stop_process indisponible."}
                await self._invoke_optional_source_callback(
                    self.stop_process_callback, action.get("source", "conversation")
                )
                return {"success": True, "message": "Processus bot stoppe."}

            if action_type == "start_process":
                if not self.start_process_callback:
                    return {"success": False, "error": "Commande start_process indisponible."}
                await self._invoke_optional_source_callback(
                    self.start_process_callback, action.get("source", "conversation")
                )
                return {"success": True, "message": "Processus bot demarre."}

            if action_type == "update_settings":
                updates = payload.get("updates") or {}
                if not updates:
                    return {"success": False, "error": "Aucun reglage a mettre a jour."}
                normalized = {k: str(v) for k, v in updates.items()}
                await store.update_settings(normalized)
                self._apply_runtime_updates(normalized)
                return {
                    "success": True,
                    "message": f"Reglages appliques: {self._format_updates(normalized)}",
                }

            return {"success": False, "error": f"Type d'action inconnu: {action_type}"}
        except Exception as exc:
            logger.error(f"Action execution failed ({action_type}): {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    def _apply_runtime_updates(self, updates: dict[str, str]) -> None:
        for key, raw_value in updates.items():
            if not hasattr(self.config, key):
                continue
            current = getattr(self.config, key)
            new_value: Any = raw_value
            try:
                if isinstance(current, bool):
                    new_value = self._as_bool(raw_value, default=current)
                elif isinstance(current, int) and not isinstance(current, bool):
                    new_value = int(float(raw_value))
                elif isinstance(current, float):
                    new_value = float(raw_value)
                setattr(self.config, key, new_value)
            except (TypeError, ValueError):
                logger.debug(f"Runtime update skipped for {key}={raw_value}")

    async def _status_text(self) -> str:
        if not self.portfolio:
            return "Etat indisponible: portfolio manager non initialise."

        state = await self.portfolio.get_portfolio_state()
        paused = bool(self.risk and self.risk.is_paused)
        status_label = "EN PAUSE" if paused else "ACTIF"

        bot_status = await store.get_bot_status()
        mm_cycle = bot_status.get("mm_cycle", "N/A")
        cd_cycle = bot_status.get("cd_cycle", "N/A")
        mm_exposure = await store.get_mm_total_exposure()

        return (
            f"Etat bot: {status_label}\n"
            f"USDC disponible: {float(state.get('available_usdc', 0)):.2f}$\n"
            f"On-chain: {float(state.get('onchain_balance') or 0):.2f}$\n"
            f"Positions ouvertes: {int(state.get('positions_count', 0))}\n"
            f"P&L du jour: {float(state.get('daily_pnl', 0)):+.2f}$\n"
            f"Exposition MM: {mm_exposure:.2f}$\n"
            f"Cycles: MM={mm_cycle} | CD={cd_cycle}"
        )

    async def _positions_text(self) -> str:
        if not self.portfolio:
            return "Positions indisponibles: portfolio manager non initialise."

        state = await self.portfolio.get_portfolio_state()
        positions = state.get("positions", []) or []
        if not positions:
            return "Aucune position ouverte."

        lines = [f"Positions ouvertes: {len(positions)}"]
        for pos in positions[:8]:
            market = (pos.get("market_question") or pos.get("market_id") or "?")[:70]
            outcome = pos.get("outcome", "?")
            size = float(pos.get("size", 0))
            avg = float(pos.get("avg_price", 0))
            current = pos.get("current_price")
            current_txt = f"{float(current):.3f}" if current is not None else "n/a"
            pnl = float(pos.get("pnl_unrealized") or 0)
            lines.append(
                f"- {market}\n"
                f"  {outcome}: {size:.2f} shares @ {avg:.3f} (now {current_txt}) | PnL {pnl:+.2f}$"
            )
        return "\n".join(lines)

    async def _performance_text(self) -> str:
        if not self.performance:
            return "Performance indisponible: tracker non initialise."

        stats = await self.performance.get_stats()
        resolved = int(stats.get("resolved_trades", 0))
        total = int(stats.get("total_trades", 0))
        pending = int(stats.get("pending_resolution", 0))
        if resolved == 0:
            return (
                f"Performance: {total} trades total, {pending} en attente de resolution.\n"
                "Pas encore assez de trades resolus pour des stats robustes."
            )

        return (
            f"Trades resolus: {resolved}/{total}\n"
            f"Win rate: {float(stats.get('hit_rate', 0)) * 100:.1f}%\n"
            f"P&L total: {float(stats.get('total_pnl', 0)):+.2f}$\n"
            f"ROI: {float(stats.get('roi_percent', 0)):+.2f}%\n"
            f"Serie: {stats.get('current_streak', 0)} {stats.get('streak_type', '')}\n"
            f"En attente: {pending}"
        )

    async def _mm_status_text(self) -> str:
        quotes = await store.get_active_mm_quotes()
        inventory = await store.get_mm_inventory()
        exposure = await store.get_mm_total_exposure()
        status = await store.get_bot_status()

        lines = [
            "Market Making:",
            f"- Quotes actives: {len(quotes)}",
            f"- Marches inventaire: {len(inventory)}",
            f"- Exposition totale: {exposure:.2f}$",
            f"- Cycle MM: {status.get('mm_cycle', 'N/A')}",
            f"- PnL MM realise: {float(status.get('mm_realized_pnl', 0)):+.4f}$",
        ]
        return "\n".join(lines)

    async def _cd_status_text(self) -> str:
        signals = await store.get_recent_cd_signals(limit=10)
        status = await store.get_bot_status()
        active = [s for s in signals if s.get("action") in ("trade", "confirming")]

        lines = [
            "Crypto Directional:",
            f"- Cycle CD: {status.get('cd_cycle', 'N/A')}",
            f"- Marches scannes: {status.get('cd_markets_scanned', 0)}",
            f"- Signaux actifs: {status.get('cd_active_signals', 0)}",
        ]
        if active:
            lines.append("- Derniers signaux:")
            for sig in active[:5]:
                lines.append(
                    f"  {sig.get('coin', '?')} {sig.get('strike', 0):,.0f}$ "
                    f"edge={float(sig.get('edge_pts', 0)):.1f}pts [{sig.get('action', '?')}]"
                )
        return "\n".join(lines)

    async def _settings_text(self, settings: dict[str, str]) -> str:
        conv_enabled = self._conversation_enabled(settings)
        conv_history = self._conversation_history_limit(settings)
        return (
            "Reglages actifs:\n"
            f"- stop_loss_percent: {float(getattr(self.config, 'stop_loss_percent', 0)):.1f}%\n"
            f"- drawdown_stop_loss_percent: {float(getattr(self.config, 'drawdown_stop_loss_percent', 0)):.1f}%\n"
            f"- capital: on-chain balance (API Polymarket)\n"
            f"- heartbeat_enabled: {bool(getattr(self.config, 'heartbeat_enabled', False))}\n"
            f"- conversation_enabled: {conv_enabled}\n"
            f"- conversation_max_history: {conv_history}"
        )

    async def _route_with_claude(
        self,
        original_text: str,
        source: str,
        settings: dict[str, str],
    ) -> dict[str, Any] | None:
        """Claude-first routing: Claude decides reply + command intent."""
        if not self._anthropic_ready:
            return None

        try:
            context = await self._build_claude_router_context(source, settings)
            system = (
                "You are the dedicated expert operator for THIS Polymarket bot running on a live VPS.\n"
                "You know this bot has MM, CD, guard, heartbeat, and Telegram control.\n"
                "Your job: answer in French and decide the best action for this bot only.\n"
                "Return STRICT JSON only:\n"
                "{\n"
                '  "agent": "manager|risk_officer|strategist|general",\n'
                '  "response": "message for user in French",\n'
                '  "action": {\n'
                '    "type": "none|pause|resume|force_cycle|kill|restart|stop_process|start_process|update_settings|analyze_logs",\n'
                '    "requires_confirmation": true,\n'
                '    "description": "short human description",\n'
                '    "payload": {}\n'
                "  }\n"
                "}\n"
                "Rules:\n"
                "- For log diagnostics and improvement proposals, use action type analyze_logs.\n"
                "- Destructive actions must require confirmation.\n"
                "- Do not ask the user to confirm in response text; use action.requires_confirmation only.\n"
                "- Be concrete and specialized on this bot architecture."
            )

            prompt = (
                f"USER_MESSAGE:\n{original_text}\n\n"
                "LIVE_CONTEXT_JSON:\n"
                f"{json.dumps(context, ensure_ascii=False, default=str)[:12000]}"
            )

            raw = await asyncio.to_thread(self._call_claude, system, prompt, 1100)
            decision = self._extract_json(raw)
            if not decision:
                return None
            return await self._apply_claude_decision(decision, original_text, source)
        except Exception as exc:
            logger.warning(f"Claude routing failed, fallback to local rules: {exc}")
            return None

    async def _build_claude_router_context(
        self,
        source: str,
        settings: dict[str, str],
    ) -> dict[str, Any]:
        portfolio_state = await self.portfolio.get_portfolio_state() if self.portfolio else {}
        performance_stats = await self.performance.get_stats() if self.performance else {}
        bot_status = await store.get_bot_status()

        hist_limit = min(12, self._conversation_history_limit(settings))
        history = await store.get_recent_conversations(source, limit=hist_limit)
        history_items = [
            {
                "role": row.get("role"),
                "agent": row.get("agent_name"),
                "message": (row.get("message") or "")[:220],
            }
            for row in history[-hist_limit:]
        ]

        return {
            "source": source,
            "settings": {
                "conversation_enabled": self._conversation_enabled(settings),
                "conversation_max_history": self._conversation_history_limit(settings),
                "heartbeat_enabled": bool(getattr(self.config, "heartbeat_enabled", False)),
                "capital_source": "on-chain balance",
                "stop_loss_percent": float(getattr(self.config, "stop_loss_percent", 0)),
                "drawdown_stop_loss_percent": float(getattr(self.config, "drawdown_stop_loss_percent", 0)),
            },
            "portfolio": {
                "available_usdc": float(portfolio_state.get("available_usdc", 0) or 0),
                "onchain_balance": float(portfolio_state.get("onchain_balance", 0) or 0),
                "positions_count": int(portfolio_state.get("positions_count", 0) or 0),
                "daily_pnl": float(portfolio_state.get("daily_pnl", 0) or 0),
            },
            "performance": {
                "resolved_trades": int(performance_stats.get("resolved_trades", 0) or 0),
                "total_trades": int(performance_stats.get("total_trades", 0) or 0),
                "roi_percent": float(performance_stats.get("roi_percent", 0) or 0),
                "hit_rate": float(performance_stats.get("hit_rate", 0) or 0),
            },
            "bot_status": {
                "mm_cycle": bot_status.get("mm_cycle"),
                "cd_cycle": bot_status.get("cd_cycle"),
                "mm_total_exposure": bot_status.get("mm_total_exposure"),
                "maintenance_cycle": bot_status.get("maintenance_cycle"),
            },
            "recent_conversation": history_items,
        }

    async def _apply_claude_decision(
        self,
        decision: dict[str, Any],
        user_text: str,
        source: str,
    ) -> dict[str, Any]:
        action = decision.get("action") or {}
        action_type = str(action.get("type", "none")).strip().lower()
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        requires_confirmation = bool(action.get("requires_confirmation", False))
        response = str(decision.get("response") or "").strip()
        agent = str(decision.get("agent") or "general").strip().lower()
        if agent not in {"manager", "risk_officer", "strategist", "general"}:
            agent = "general"

        normalized_user = self._normalize(user_text)
        if self._is_logs_diagnostic_request(normalized_user) and action_type == "none":
            action_type = "analyze_logs"
            payload = {
                "with_claude": True,
                "focus": user_text,
                **payload,
            }
            requires_confirmation = False

        if action_type not in CLAUDE_ACTIONS:
            return {
                "agent": "general",
                "response": response or f"Action Claude non supportee: {action_type}",
                "action_taken": None,
            }

        if action_type in {"none", ""}:
            return {"agent": agent, "response": response or "OK.", "action_taken": None}

        description = str(action.get("description") or "").strip() or self._default_action_description(
            action_type, payload
        )

        if action_type == "analyze_logs":
            lines = int(payload.get("lines", 240) or 240)
            lines = max(80, min(600, lines))
            focus = str(payload.get("focus") or user_text)
            with_claude = bool(payload.get("with_claude", True))
            diag = await self._logs_diagnostic_text(
                use_claude=with_claude,
                focus=focus,
                max_lines=lines,
            )
            merged = f"{response}\n\n{diag}".strip() if response else diag
            return {"agent": "developer", "response": merged, "action_taken": None}

        if action_type in {"kill", "restart", "stop_process", "start_process", "update_settings"}:
            requires_confirmation = True

        action_record = {
            "source": source,
            "type": action_type,
            "description": description,
            "payload": payload,
        }

        if requires_confirmation:
            return self._queue_confirmation(
                source=source,
                action_type=action_type,
                description=description,
                payload=payload,
                agent=agent,
            )

        result = await self._execute_action(action_record)
        if result.get("success"):
            msg = response or result.get("message") or f"Action executee: {description}"
            return {"agent": agent, "response": msg, "action_taken": None}
        msg = response or f"Echec action: {result.get('error', 'inconnu')}"
        return {"agent": agent, "response": msg, "action_taken": None}

    @staticmethod
    def _default_action_description(action_type: str, payload: dict[str, Any]) -> str:
        if action_type == "update_settings":
            updates = payload.get("updates") if isinstance(payload.get("updates"), dict) else {}
            if updates:
                return "Mettre a jour les reglages: " + ", ".join(f"{k}={v}" for k, v in updates.items())
        if action_type == "analyze_logs":
            return "Analyser les logs du bot"
        mapping = {
            "pause": "Mettre le trading en pause",
            "resume": "Reprendre le trading",
            "force_cycle": "Forcer un cycle d'analyse",
            "kill": "Kill switch",
            "restart": "Redemarrer le bot",
            "stop_process": "Stopper le processus bot",
            "start_process": "Demarrer le processus bot",
        }
        return mapping.get(action_type, f"Action {action_type}")

    async def _logs_diagnostic_text(
        self,
        use_claude: bool = False,
        focus: str = "",
        max_lines: int = 240,
    ) -> str:
        try:
            if not self._bot_log_path.exists():
                return f"Fichier log introuvable: {self._bot_log_path}"

            with self._bot_log_path.open("r", encoding="utf-8", errors="ignore") as f:
                tail_lines = list(deque(f, maxlen=max_lines))
            if not tail_lines:
                return "Aucun log disponible pour diagnostic."

            level_counts: Counter[str] = Counter()
            module_counts: Counter[str] = Counter()
            error_samples: list[str] = []
            warning_samples: list[str] = []
            level_re = re.compile(r"\[([A-Z]+)\]")
            module_re = re.compile(r"\[[A-Z]+\]\s+([^:]+):")

            for raw in tail_lines:
                line = raw.strip()
                if not line:
                    continue

                m_lvl = level_re.search(line)
                if m_lvl:
                    level_counts[m_lvl.group(1)] += 1

                m_mod = module_re.search(line)
                if m_mod:
                    module_counts[m_mod.group(1)] += 1

                if "[ERROR]" in line and len(error_samples) < 5:
                    error_samples.append(line[-220:])
                if "[WARNING]" in line and len(warning_samples) < 5:
                    warning_samples.append(line[-220:])

            lines = [
                f"Diagnostic logs ({len(tail_lines)} lignes recentes):",
                f"- ERROR: {level_counts.get('ERROR', 0)}",
                f"- WARNING: {level_counts.get('WARNING', 0)}",
                f"- INFO: {level_counts.get('INFO', 0)}",
            ]
            if module_counts:
                tops = ", ".join(f"{name}({count})" for name, count in module_counts.most_common(5))
                lines.append(f"- Modules les plus actifs: {tops}")

            if error_samples:
                lines.append("Erreurs recentes:")
                for e in error_samples[:3]:
                    lines.append(f"  - {e}")
            elif warning_samples:
                lines.append("Warnings recents:")
                for w in warning_samples[:3]:
                    lines.append(f"  - {w}")

            response = "\n".join(lines)

            if use_claude:
                if not self._anthropic_ready:
                    response += "\n\nAnalyse Claude demandee mais ANTHROPIC_API_KEY non configuree."
                else:
                    logs_blob = "\n".join(tail_lines[-120:])
                    system = (
                        "You are a dedicated expert for this Polymarket bot. "
                        "Analyze logs and propose concrete technical improvements."
                    )
                    prompt = (
                        f"FOCUS USER: {focus}\n\n"
                        "Analyse ces logs et propose:\n"
                        "1) causes racines prioritaires\n"
                        "2) correctifs code immediats\n"
                        "3) ameliorations monitoring\n"
                        "4) impacts sur PnL/risque\n\n"
                        f"{logs_blob}"
                    )
                    claude_resp = await asyncio.to_thread(self._call_claude, system, prompt, 900)
                    if claude_resp:
                        response += f"\n\nAnalyse Claude:\n{self._sanitize_response(claude_resp)}"

            return response
        except Exception as exc:
            logger.error(f"Logs diagnostic failed: {exc}", exc_info=True)
            return f"Echec diagnostic logs: {exc}"

    async def _answer_risk_question(self, question: str, source: str) -> str:
        if not bool(getattr(self.config, "risk_officer_enabled", True)):
            return "Risk Officer desactive par configuration."

        if not self._risk_agent:
            return await self._risk_fallback_text()

        context = await self._build_agent_context(source)
        response = await self._risk_agent.answer_question(question, context)
        return self._sanitize_response(response)

    async def _answer_strategy_question(self, question: str, source: str) -> str:
        if not bool(getattr(self.config, "strategist_enabled", True)):
            return "Strategist desactive par configuration."

        if not self._strategist_agent:
            return await self._strategy_fallback_text()

        context = await self._build_agent_context(source)
        response = await self._strategist_agent.answer_question(question, context)
        return self._sanitize_response(response)

    async def _risk_fallback_text(self) -> str:
        if not self.portfolio:
            return "Risk summary indisponible."
        state = await self.portfolio.get_portfolio_state()
        invested = float(state.get("total_invested", 0))
        onchain = float(state.get("onchain_balance", 0) or 0)
        total_capital = invested + onchain
        concentration = (invested / total_capital * 100) if total_capital > 0 else 0
        return (
            "Risk Officer (mode local):\n"
            f"- Investi: {invested:.2f}$ / Balance on-chain: {onchain:.2f}$ ({concentration:.1f}% deploye)\n"
            f"- Stop-loss journalier: {float(getattr(self.config, 'stop_loss_percent', 0)):.1f}%\n"
            f"- Stop-loss drawdown: {float(getattr(self.config, 'drawdown_stop_loss_percent', 0)):.1f}%\n"
            "Pour une analyse qualitative detaillee, configure Anthropic API."
        )

    async def _strategy_fallback_text(self) -> str:
        if not self.performance:
            return "Strategist summary indisponible."
        stats = await self.performance.get_stats()
        roi = float(stats.get("roi_percent", 0))
        hit = float(stats.get("hit_rate", 0)) * 100
        trend = "stable"
        if roi > 5:
            trend = "favorable"
        elif roi < -5:
            trend = "degrade"
        return (
            "Strategist (mode local):\n"
            f"- ROI courant: {roi:+.2f}%\n"
            f"- Hit rate: {hit:.1f}%\n"
            f"- Regime estime: {trend}\n"
            "Pour des recommandations plus fines, active Anthropic API."
        )

    async def _build_agent_context(self, source: str) -> dict[str, Any]:
        history_limit = self._conversation_history_limit(await self._get_settings_values())
        history = await store.get_recent_conversations(source, limit=history_limit)
        portfolio_state = await self.portfolio.get_portfolio_state() if self.portfolio else {}
        performance_stats = await self.performance.get_stats() if self.performance else {}
        bot_status = await store.get_bot_status()
        return {
            "portfolio_state": portfolio_state,
            "performance_stats": performance_stats,
            "bot_status": bot_status,
            "current_config": {
                "capital_source": "on-chain balance (Polymarket API)",
                "stop_loss_percent": getattr(self.config, "stop_loss_percent", None),
                "drawdown_stop_loss_percent": getattr(self.config, "drawdown_stop_loss_percent", None),
                "conversation_max_history": getattr(self.config, "conversation_max_history", None),
            },
            "recent_conversation": [
                {
                    "role": row.get("role", "user"),
                    "agent": row.get("agent_name", "general"),
                    "message": row.get("message", ""),
                }
                for row in history[-history_limit:]
            ],
        }

    def _call_claude(self, system_prompt: str, user_prompt: str, max_tokens: int = 1200) -> str:
        if not self._anthropic_ready:
            raise RuntimeError("Anthropic API key missing")

        import anthropic

        kwargs = {"api_key": self.anthropic.api_key}
        base_url = getattr(self.anthropic, "base_url", "")
        if base_url:
            kwargs["base_url"] = base_url

        client = anthropic.Anthropic(**kwargs)
        response = client.messages.create(
            model=getattr(self.anthropic, "model", "claude-opus-4-1"),
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if not getattr(response, "content", None):
            return ""
        chunks = []
        for block in response.content:
            txt = getattr(block, "text", None)
            if txt:
                chunks.append(txt)
        return "\n".join(chunks).strip()

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{[\s\S]*\}", raw or "")
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {}

    @staticmethod
    def _sanitize_response(text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return "Aucune reponse."
        if len(cleaned) > 3200:
            return cleaned[:3197] + "..."
        return cleaned

    def _prune_actions(self) -> None:
        cutoff = time.time() - self._action_ttl_seconds
        expired = [aid for aid, item in self._pending_actions.items() if item.get("created_at", 0) < cutoff]
        for aid in expired:
            self._pending_actions.pop(aid, None)

    @staticmethod
    async def _invoke_optional_source_callback(callback, source: str) -> None:
        try:
            await callback(source=source)
        except TypeError:
            await callback()

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    @staticmethod
    def _as_bool(raw: Any, default: bool = False) -> bool:
        if raw is None:
            return default
        value = str(raw).strip().lower()
        if value in BOOL_TRUE:
            return True
        if value in BOOL_FALSE:
            return False
        return default

    @staticmethod
    def _format_updates(updates: dict[str, Any]) -> str:
        return ", ".join(f"{k}={v}" for k, v in updates.items())

    @staticmethod
    def _looks_like_enable_conversation(text: str) -> bool:
        return any(
            k in text
            for k in (
                "active conversation",
                "active le chat",
                "active interface conversation",
                "active l'interface conversation",
                "enable conversation",
                "conversation on",
            )
        )

    def _extract_confirm_id(self, text: str, source: str) -> str | None:
        match = re.search(r"(?:confirmer?|confirm)(?:\s+action)?\s*[:#]?\s*([a-f0-9]{6,32})", text)
        if match:
            return match.group(1)
        if text in {"confirme", "confirm", "oui", "ok", "yes"}:
            return self._last_action_by_source.get(source)
        return None

    def _extract_cancel_id(self, text: str, source: str) -> str | None:
        match = re.search(r"(?:annuler?|cancel)(?:\s+action)?\s*[:#]?\s*([a-f0-9]{6,32})", text)
        if match:
            return match.group(1)
        if text in {"annule", "cancel", "non", "stop"}:
            return self._last_action_by_source.get(source)
        return None

    @staticmethod
    def _is_help_query(text: str) -> bool:
        return any(k in text for k in ("help", "aide", "commandes", "que peux-tu", "que peux tu"))

    @staticmethod
    def _is_status_query(text: str) -> bool:
        return any(
            k in text
            for k in ("status", "etat", "état", "dashboard", "overview", "situation globale")
        )

    @staticmethod
    def _is_positions_query(text: str) -> bool:
        return any(k in text for k in ("positions", "position ouverte", "exposition", "inventory"))

    @staticmethod
    def _is_performance_query(text: str) -> bool:
        return any(k in text for k in ("performance", "pnl", "roi", "win rate", "hit rate"))

    @staticmethod
    def _is_mm_query(text: str) -> bool:
        return bool(re.search(r"\bmm\b", text)) or any(
            k in text for k in ("market making", "market-making", "maker")
        )

    @staticmethod
    def _is_cd_query(text: str) -> bool:
        return bool(re.search(r"\bcd\b", text)) or any(
            k in text for k in ("crypto directional", "student-t")
        )

    @staticmethod
    def _is_settings_query(text: str) -> bool:
        return any(
            k in text
            for k in ("reglage", "reglages", "réglage", "réglages", "settings", "configuration", "config")
        )

    @staticmethod
    def _is_pause_command(text: str) -> bool:
        return bool(re.search(r"\b(pause|pauser|mets en pause|mettre en pause)\b", text))

    @staticmethod
    def _is_resume_command(text: str) -> bool:
        return bool(re.search(r"\b(reprendre|reprend|reprends|resume trading|resume bot|unpause)\b", text))

    @staticmethod
    def _is_kill_command(text: str) -> bool:
        return any(
            k in text
            for k in ("kill", "kill switch", "annule tous les ordres", "cancel all orders", "cancel all")
        )

    @staticmethod
    def _is_force_cycle_command(text: str) -> bool:
        return any(k in text for k in ("force cycle", "forcer cycle", "lance un cycle", "run cycle now"))

    @staticmethod
    def _is_restart_command(text: str) -> bool:
        return any(k in text for k in ("restart", "redemarre", "redemarrer", "/restart"))

    @staticmethod
    def _is_stop_process_command(text: str) -> bool:
        return any(
            k in text
            for k in ("stopbot", "stop process", "arrete le bot", "arrête le bot", "arreter le bot", "arrêter le bot")
        )

    @staticmethod
    def _is_start_process_command(text: str) -> bool:
        return any(
            k in text
            for k in ("startbot", "start process", "demarre le bot", "démarre le bot", "demarrer le bot", "démarrer le bot")
        )

    @staticmethod
    def _is_risk_question(text: str) -> bool:
        return any(k in text for k in ("risk officer", "risque", "drawdown", "stop-loss", "stop loss"))

    @staticmethod
    def _is_strategy_question(text: str) -> bool:
        return any(
            k in text for k in ("strategist", "strategie", "stratégie", "allocation", "optimiser", "rendement")
        )

    @staticmethod
    def _is_logs_diagnostic_request(text: str) -> bool:
        return any(
            k in text
            for k in (
                "diagnostic logs",
                "diagnostique logs",
                "analyse logs",
                "analyser les logs",
                "analyser les derniers logs",
                "derniers logs",
            )
        )

    @staticmethod
    def _mentions_claude_analysis(text: str) -> bool:
        return any(k in text for k in ("claude", "llm", "ia", "ai"))

    @staticmethod
    def _help_text() -> str:
        return (
            "Interface conversationnelle active.\n"
            "Exemples:\n"
            "- status / positions / performance\n"
            "- mm / cd / reglages\n"
            "- diagnostic logs / analyser les logs dans Claude\n"
            "- pause / reprendre\n"
            "- kill (demande confirmation)\n"
            "- restart (demande confirmation)\n"
            "- risk: 'quel est le risque actuel ?'\n"
            "- strategy: 'comment ameliorer le rendement ?'"
        )

    def _parse_setting_updates(self, text: str) -> dict[str, str]:
        # Requires an explicit update verb to reduce false positives.
        if not re.search(r"\b(set|mets?|regle|change|modifie|update|active|desactive|désactive)\b", text):
            return {}

        updates: dict[str, str] = {}
        aliases = {
            "stop_loss_percent": ("stop_loss_percent", "stop loss", "stop-loss"),
            "drawdown_stop_loss_percent": (
                "drawdown_stop_loss_percent",
                "drawdown stop loss",
                "drawdown",
            ),
            "conversation_max_history": ("conversation_max_history", "historique conversation", "chat history"),
            "conversation_enabled": ("conversation_enabled", "conversation enabled", "chat nl"),
        }

        for key, key_aliases in aliases.items():
            if not any(alias in text for alias in key_aliases):
                continue

            if key == "conversation_enabled":
                if any(flag in text for flag in (" on", " true", " act", " enable")):
                    updates[key] = "true"
                    continue
                if any(flag in text for flag in (" off", " false", " desact", " disable")):
                    updates[key] = "false"
                    continue

            number_match = re.search(r"(-?\d+(?:[.,]\d+)?)", text)
            if number_match:
                updates[key] = number_match.group(1).replace(",", ".")

        return updates
