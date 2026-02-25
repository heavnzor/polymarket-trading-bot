import logging
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
)
from config import TelegramConfig

logger = logging.getLogger(__name__)


def _confidence_label(confidence: float) -> str:
    """Renvoie un label francais pour le niveau de confiance (0-100)."""
    if confidence < 50:
        return "faible"
    elif confidence < 70:
        return "moyenne"
    elif confidence < 85:
        return "elevee"
    else:
        return "tres elevee"


def _strategy_label(strategy: str) -> str:
    """Renvoie un label francais descriptif pour la strategie."""
    if strategy and strategy.lower() == "active":
        return "ACTIVE (achat + vente IA)"
    return "ACTIVE (achat + vente IA)"


def _side_label(side: str) -> str:
    return "ACHAT" if side.upper() == "BUY" else "VENTE"


def _days_since(opened_at: str | None) -> int:
    """Calcule le nombre de jours depuis l'ouverture d'une position."""
    if not opened_at:
        return 0
    try:
        opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0, (now - opened).days)
    except (ValueError, TypeError):
        return 0


class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.config = config
        self.app: Application | None = None
        self._trade_confirm_callback = None
        self._trade_reject_callback = None
        self._portfolio_manager = None
        self._risk_manager = None
        self._performance_tracker = None
        self._trading_config = None
        self._force_cycle_event = None
        self._stop_callback = None
        self._stop_process_callback = None
        self._start_process_callback = None
        self._current_strategy = None
        self._strategy_switch_callback = None
        self._learning_journal = None
        self._learning_insights = None
        self._learning_proposals = None
        self._conversation_router = None

    def set_callbacks(self, on_confirm, on_reject):
        self._trade_confirm_callback = on_confirm
        self._trade_reject_callback = on_reject

    def set_managers(self, portfolio_manager, risk_manager):
        self._portfolio_manager = portfolio_manager
        self._risk_manager = risk_manager

    def set_performance_tracker(self, tracker):
        self._performance_tracker = tracker

    def set_trading_config(self, config):
        self._trading_config = config

    def set_bot_controls(
        self,
        force_cycle_event,
        stop_callback,
        stop_process_callback=None,
        start_process_callback=None,
    ):
        self._force_cycle_event = force_cycle_event
        self._stop_callback = stop_callback
        self._stop_process_callback = stop_process_callback
        self._start_process_callback = start_process_callback

    def set_strategy(self, strategy, switch_callback):
        self._current_strategy = strategy
        self._strategy_switch_callback = switch_callback

    def set_learning(self, journal, insights, proposals):
        self._learning_journal = journal
        self._learning_insights = insights
        self._learning_proposals = proposals

    def set_conversation_router(self, router):
        self._conversation_router = router

    async def _persist_runtime_settings(self, updates: dict[str, str]):
        """Persist runtime changes so they survive restarts and stay in sync with dashboard."""
        try:
            from db.store import update_settings
            await update_settings(updates)
        except Exception as e:
            logger.warning(f"Impossible de persister les reglages {updates}: {e}")

    async def initialize(self):
        if not self.config.bot_token:
            logger.warning("Pas de token Telegram configure, notifications desactivees")
            return

        self.app = (
            Application.builder()
            .token(self.config.bot_token)
            .updater(None)  # No polling — send-only mode
            .build()
        )

        await self.app.initialize()
        await self.app.start()
        logger.info("Bot Telegram demarre (send-only, pas de polling)")

    async def shutdown(self):
        if self.app:
            await self.app.stop()
            await self.app.shutdown()

    async def send_message(self, text: str, parse_mode: str = "HTML"):
        if not self.app or not self.config.chat_id:
            return
        await self.app.bot.send_message(
            chat_id=self.config.chat_id,
            text=text,
            parse_mode=parse_mode,
        )

    # ─────────────────────────────────────────────
    # Messages de trading
    # ─────────────────────────────────────────────

    async def send_trade_confirmation(self, trade: dict):
        if not self.app or not self.config.chat_id:
            return

        trade_id = trade["id"]
        side = _side_label(trade["side"])
        price = trade["price"]
        size = trade["size_usdc"]
        edge = (trade.get("edge") or 0) * 100  # stored as 0-1, display as %
        edge_net = trade.get("edge_net")
        edge_net_pct = (edge_net * 100) if edge_net is not None else edge
        confidence = (trade.get("confidence") or 0) * 100  # stored as 0-1
        conf_label = _confidence_label(confidence)
        risk_rating = trade.get("risk_rating")
        source_quality = trade.get("source_quality")
        horizon = str(trade.get("horizon", "normal")).replace("_", " ")
        key_source = trade.get("key_source") or "n/a"
        reasoning = trade.get("reasoning", "Aucune explication fournie")[:200]
        risk_txt = f"{int(risk_rating)}/10" if risk_rating is not None else "n/a"
        sq_txt = f"{float(source_quality):.2f}" if source_quality is not None else "n/a"

        text = (
            f"<b>\U0001f514 Nouvelle operation proposee</b>\n\n"
            f"\U0001f4ca <b>Marche :</b> {trade.get('market_question', trade['market_id'])}\n"
            f"\U0001f4cc <b>Pari :</b> {side} du <b>{trade['outcome']}</b> a {price:.4f}$\n"
            f"\U0001f4b0 <b>Mise :</b> {size:.2f}$\n"
            f"\U0001f4c8 <b>Edge net :</b> +{edge_net_pct:.1f}% (brut +{edge:.1f}%)\n"
            f"\U0001f6e1 <b>Risque :</b> {risk_txt} | "
            f"<b>Qualite source :</b> {sq_txt} | <b>Horizon :</b> {horizon}\n"
            f"\U0001f3af <b>Confiance :</b> {confidence:.0f}% ({conf_label})\n"
            f"\U0001f4da <b>Source cle :</b> {key_source[:120]}\n\n"
            f"\U0001f4a1 <i>{reasoning}</i>"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Valider \u2713", callback_data=f"approve_{trade_id}"),
                InlineKeyboardButton("Refuser \u2717", callback_data=f"reject_{trade_id}"),
            ]
        ])

        await self.app.bot.send_message(
            chat_id=self.config.chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def send_trade_executed(self, trade: dict):
        side = _side_label(trade["side"])
        price = trade["price"]
        size = trade["size_usdc"]
        shares = size / price if price > 0 else 0
        potential_gain = shares - size  # profit si le outcome gagne (token vaut 1$)
        edge = (trade.get("edge") or 0) * 100
        edge_net = trade.get("edge_net")
        edge_net_pct = (edge_net * 100) if edge_net is not None else edge
        cost_bps = trade.get("execution_cost_bps")
        source_quality = trade.get("source_quality")
        key_source = trade.get("key_source") or "n/a"
        strategy = trade.get("strategy", "active")
        strat_label = _strategy_label(strategy)

        cost_txt = f"{float(cost_bps):.0f}bps" if cost_bps is not None else "n/a"
        sq_txt = f"{float(source_quality):.2f}" if source_quality is not None else "n/a"
        text = (
            f"<b>\u2705 Operation executee</b>\n\n"
            f"\U0001f4ca {trade.get('market_question', trade['market_id'])}\n"
            f"\U0001f4cc {side} de <b>{shares:.1f} jetons</b> {trade['outcome']} a {price:.4f}$\n"
            f"\U0001f4b0 Investi : {size:.2f}$ | Gain potentiel : {potential_gain:.2f}$ si {trade['outcome']}\n"
            f"\U0001f4c8 Edge net : +{edge_net_pct:.1f}% (brut +{edge:.1f}%, cout~{cost_txt})\n"
            f"\U0001f4da Source qualite : {sq_txt} | Cle : {key_source[:70]}\n"
            f"\U0001f4cb Strategie : {strat_label}"
        )
        await self.send_message(text)

    async def send_order_fill_update(self, updates: list[dict]):
        """Notifie les changements de statut des ordres."""
        if not updates:
            return
        lines = ["<b>\U0001f4e6 Mise a jour des ordres</b>\n"]
        for u in updates:
            status = u["status"].upper()
            if status in ("MATCHED", "EXECUTED"):
                status_fr = "EXECUTE"
            elif status == "EXPIRED":
                status_fr = "EXPIRE"
            elif status == "CANCELLED":
                status_fr = "ANNULE"
            elif status == "PARTIAL_FILL":
                status_fr = "PARTIEL"
            else:
                status_fr = status
            lines.append(f"  \U0001f4cb Trade #{u['trade_id']} : <b>{status_fr}</b>")
            if u.get("size_matched"):
                lines.append(f"    \u2705 Rempli : {u['size_matched']:.2f} jetons")
            if u.get("avg_fill_price"):
                lines.append(f"    \U0001f4b1 Prix moyen : {u['avg_fill_price']:.4f}$")
            if u.get("slippage_bps") is not None:
                lines.append(f"    \U0001f4ca Slippage : {u['slippage_bps']:.1f} bps")
        await self.send_message("\n".join(lines))

    async def send_resolution_update(self, resolutions: list[dict]):
        """Notifie les resolutions de marches."""
        if not resolutions:
            return
        lines = ["<b>\U0001f3c1 Marches resolus</b>\n"]
        for r in resolutions:
            lines.append(
                f"  \U0001f4ca {r['market_id'][:12]}... : <b>{r['outcome']}</b> "
                f"({r['trades_resolved']} trades concernes)"
            )
        await self.send_message("\n".join(lines))

    async def send_daily_summary(self, summary: dict):
        available = summary.get("available_usdc", 0)
        onchain = summary.get("onchain_balance", 0) or 0
        daily_pnl = summary.get("daily_pnl", 0)
        daily_pnl_sign = "+" if daily_pnl >= 0 else ""
        positions_count = summary.get("positions_count", 0)
        total_invested = summary.get("total_invested", 0)
        daily_traded = summary.get("daily_traded", 0)

        # Calcul du pourcentage P&L
        if total_invested > 0:
            daily_pnl_pct = (daily_pnl / total_invested) * 100
        else:
            daily_pnl_pct = 0.0
        pnl_pct_sign = "+" if daily_pnl_pct >= 0 else ""

        # Budget max du jour
        max_day = self._trading_config.max_per_day_usdc if self._trading_config else 30.0

        # Strategie et cycle
        strat_name = self._current_strategy.name.upper() if self._current_strategy else "ACTIVE"
        cycle_minutes = self._trading_config.analysis_interval_minutes if self._trading_config else 30

        text = (
            f"<b>\U0001f4ca Resume du jour</b>\n\n"
            f"\U0001f4b0 <b>Solde :</b> {available:.2f}$ disponibles\n"
            f"\U0001f3e6 <b>On-chain :</b> {onchain:.2f}$ (total sur le wallet)\n"
            f"\U0001f4c8 <b>Gain du jour :</b> {daily_pnl_sign}{daily_pnl:.2f}$ ({pnl_pct_sign}{daily_pnl_pct:.1f}%)\n"
            f"\U0001f4ca {positions_count} positions ouvertes pour {total_invested:.2f}$ investis\n"
            f"\U0001f504 <b>Volume du jour :</b> {daily_traded:.2f}$ sur {max_day:.0f}$ autorises\n\n"
            f"\U0001f4cb <b>Strategie active :</b> {strat_name}\n"
            f"\u23f1 Prochain cycle dans ~{cycle_minutes} minutes"
        )
        await self.send_message(text)

    async def send_risk_review(self, review: dict, rejected_trades: list[dict] | None = None):
        """Send Risk Officer pre-trade review summary."""
        if not self.app or not self.config.chat_id:
            return

        reviews = review.get("reviews", [])
        approved = sum(1 for r in reviews if r.get("verdict") == "approve")
        flagged = sum(1 for r in reviews if r.get("verdict") == "flag")
        rejected = sum(1 for r in reviews if r.get("verdict") == "reject")

        summary = review.get("portfolio_risk_summary", "")

        text = (
            f"<b>Risk Officer — Revue pre-trade</b>\n\n"
            f"Trades examines : {len(reviews)}\n"
            f"Approuves : {approved} | Reduits : {flagged} | Bloques : {rejected}\n\n"
        )

        if rejected_trades:
            text += "<b>Trades bloques :</b>\n"
            for rt in rejected_trades[:5]:
                text += (
                    f"  - {rt.get('market_question', rt.get('market_id', '?'))[:60]}\n"
                    f"    Score risque: {rt.get('risk_score', '?')}/10 — {rt.get('reasoning', '')[:100]}\n"
                )
            text += "\n"

        if flagged > 0:
            flagged_items = [r for r in reviews if r.get("verdict") == "flag"]
            text += "<b>Trades reduits (x0.5) :</b>\n"
            for fi in flagged_items[:5]:
                text += f"  - {fi.get('market_id', '?')[:40]}: {', '.join(fi.get('concerns', [])[:2])}\n"
            text += "\n"

        if summary:
            text += f"<i>{summary[:300]}</i>"

        if len(text) > 4000:
            text = text[:3997] + "..."

        await self.app.bot.send_message(
            chat_id=self.config.chat_id, text=text, parse_mode="HTML"
        )

    async def send_strategist_assessment(self, assessment: dict):
        """Send Strategist periodic assessment summary."""
        if not self.app or not self.config.chat_id:
            return

        parsed = assessment.get("parsed", assessment)
        regime = parsed.get("market_regime", "normal")
        alloc_score = parsed.get("allocation_score", "?")
        div_score = parsed.get("diversification_score", "?")
        summary_text = parsed.get("summary", "Pas de resume disponible.")
        recommendations = parsed.get("recommendations", [])

        regime_labels = {
            "normal": "Normal",
            "volatile": "Volatile",
            "trending": "Tendanciel",
            "crisis": "CRISE",
        }
        regime_label = regime_labels.get(regime, regime)

        text = (
            f"<b>Strategist — Analyse periodique</b>\n\n"
            f"Regime de marche : <b>{regime_label}</b>\n"
            f"Allocation : {alloc_score}/10 | Diversification : {div_score}/10\n\n"
            f"{summary_text[:400]}\n"
        )

        if recommendations:
            text += "\n<b>Recommandations :</b>\n"
            for rec in recommendations[:5]:
                priority_icon = {"high": "!", "medium": "-", "low": "·"}.get(rec.get("priority", ""), "-")
                text += (
                    f"  {priority_icon} {rec.get('target', '?')}: "
                    f"{rec.get('current', '?')} -> {rec.get('suggested', '?')} "
                    f"({rec.get('risk_level', '?')})\n"
                )

        if len(text) > 4000:
            text = text[:3997] + "..."

        await self.app.bot.send_message(
            chat_id=self.config.chat_id, text=text, parse_mode="HTML"
        )

    async def send_alert(self, title: str, message: str):
        text = f"<b>\u26a0\ufe0f {title}</b>\n\n{message}"
        await self.send_message(text)

    # ─────────────────────────────────────────────
    # Autorisation
    # ─────────────────────────────────────────────

    def _is_authorized(self, update: Update) -> bool:
        chat_id = update.effective_chat.id if update.effective_chat else None
        return str(chat_id) == str(self.config.chat_id)

    # ─────────────────────────────────────────────
    # Callback handler (boutons inline)
    # ─────────────────────────────────────────────

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorized(update):
            await query.answer("Acces non autorise.", show_alert=True)
            return
        await query.answer()

        data = query.data or ""
        try:
            # --- Approbation / rejet de trades ---
            if data.startswith("approve_"):
                trade_id = int(data.replace("approve_", ""))
                if self._trade_confirm_callback:
                    result = await self._trade_confirm_callback(trade_id)
                    status = result.get("status", "inconnu") if result else "introuvable"
                    if status == "executed":
                        await query.edit_message_text(
                            f"\u2705 Trade #{trade_id} : <b>VALIDE ET EXECUTE</b>",
                            parse_mode="HTML",
                        )
                    else:
                        await query.edit_message_text(
                            f"\U0001f4cb Trade #{trade_id} : {status.upper()}",
                            parse_mode="HTML",
                        )

            elif data.startswith("reject_"):
                trade_id = int(data.replace("reject_", ""))
                if self._trade_reject_callback:
                    await self._trade_reject_callback(trade_id)
                    await query.edit_message_text(
                        f"\u274c Trade #{trade_id} : <b>REFUSE</b>",
                        parse_mode="HTML",
                    )

            # --- Changement de strategie via boutons inline ---
            elif data.startswith("strategy_"):
                new_strategy = data.replace("strategy_", "")
                if new_strategy != "active":
                    await query.edit_message_text(
                        "\u2139\ufe0f La strategie est fixe: <b>ACTIVE</b>.",
                        parse_mode="HTML",
                    )
                    return

                if self._strategy_switch_callback and new_strategy == "active":
                    self._strategy_switch_callback("active")
                    from strategy import get_strategy
                    self._current_strategy = get_strategy("active")
                    await self._persist_runtime_settings({"strategy": "active"})

                strat_label = _strategy_label("active")
                await query.edit_message_text(
                    f"\u2139\ufe0f <b>Strategie active : {strat_label}</b>\n\n"
                    f"<i>Le bot fonctionne en strategie unique pour concentrer l'apprentissage.</i>",
                    parse_mode="HTML",
                )

            # --- Confirmation / annulation d'actions conversationnelles ---
            elif data.startswith("confirm_action_"):
                action_id = data.replace("confirm_action_", "")
                if self._conversation_router:
                    result = await self._conversation_router.execute_confirmed_action(action_id)
                    if result.get("success"):
                        await query.edit_message_text(
                            f"\u2705 <b>Action executee</b>\n\n{result.get('message', 'OK')}",
                            parse_mode="HTML",
                        )
                    else:
                        await query.edit_message_text(
                            f"\u274c <b>Echec</b>\n\n{result.get('error', 'Erreur inconnue')}",
                            parse_mode="HTML",
                        )
                else:
                    await query.edit_message_text(
                        "\u274c Routeur conversationnel non disponible.",
                        parse_mode="HTML",
                    )

            elif data.startswith("cancel_action_"):
                action_id = data.replace("cancel_action_", "")
                await query.edit_message_text(
                    "\u274c <b>Action annulee</b>",
                    parse_mode="HTML",
                )

        except (ValueError, TypeError):
            logger.warning(f"Donnees de callback invalides : {data}")
            await query.edit_message_text(
                "\u274c Erreur : donnees invalides. Reessayez.",
                parse_mode="HTML",
            )

    async def _handle_free_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle free-text messages via the conversation router."""
        if not self._is_authorized(update):
            return
        if not self._conversation_router:
            await update.message.reply_text(
                "Interface conversationnelle non active. Utilisez /help pour les commandes.",
                parse_mode="HTML"
            )
            return

        message = update.message.text
        await update.message.chat.send_action("typing")

        try:
            result = await self._conversation_router.handle_message(message, "telegram")
            response = result.get("response", "Pas de reponse.")
            agent = result.get("agent", "general")

            # Format with agent badge
            agent_labels = {
                "risk_officer": "Risk Officer",
                "strategist": "Strategist",
                "manager": "Manager",
                "general": "Assistant",
            }
            label = agent_labels.get(agent, agent.title())

            # Handle confirmation requests
            if result.get("action_taken") and result["action_taken"].get("requires_confirmation"):
                action = result["action_taken"]
                text = (
                    f"<b>{label}</b>\n\n"
                    f"{response}\n\n"
                    f"<b>Action proposee :</b> {action.get('description', 'Modification')}\n"
                    f"Confirmer ?"
                )
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Confirmer", callback_data=f"confirm_action_{action.get('id', 0)}"),
                    InlineKeyboardButton("Annuler", callback_data=f"cancel_action_{action.get('id', 0)}"),
                ]])
                await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
            else:
                text = f"<b>{label}</b>\n\n{response}"
                # Truncate if too long for Telegram (4096 chars max)
                if len(text) > 4000:
                    text = text[:3997] + "..."
                await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Free-text handler error: {e}")
            await update.message.reply_text(
                f"Erreur lors du traitement: {str(e)[:200]}",
                parse_mode="HTML"
            )

    # ─────────────────────────────────────────────
    # Commandes Telegram
    # ─────────────────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        strat = self._current_strategy.name.upper() if self._current_strategy else "ACTIVE"
        await update.message.reply_text(
            f"<b>\U0001f916 Polymarket Trading Bot v2</b>\n\n"
            f"\U0001f4cb Strategie active : <b>{strat}</b>\n"
            f"\U0001f4a1 Tape /help pour voir toutes les commandes disponibles.",
            parse_mode="HTML",
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        text = (
            f"<b>\U0001f4d6 Guide des commandes</b>\n\n"
            "<b>\U0001f4ac Interface conversationnelle</b>\n"
            "Vous pouvez m'ecrire en langage naturel pour parler aux agents :\n"
            "\u2022 Risk Officer (risque, positions, stop-loss)\n"
            "\u2022 Strategist (strategie, allocation, marche)\n"
            "\u2022 Manager (performance, ameliorations)\n"
            "<b>\U0001f50d Surveillance</b>\n"
            "/status \u2014 Etat rapide du bot (solde, positions, P&amp;L)\n"
            "/dashboard \u2014 Tableau de bord complet avec trades recents\n"
            "/positions \u2014 Detail de chaque position ouverte\n"
            "/performance \u2014 Statistiques : taux de reussite, ROI, series, biais\n"
            "/reglages \u2014 Voir la configuration actuelle du bot\n\n"
            "<b>\U0001f4b9 Market-Making / Crypto Directional</b>\n"
            "/mm \u2014 Statut MM : quotes actives, exposition, PnL\n"
            "/cd \u2014 Statut CD : signaux actifs, confirmations\n"
            "/pnl \u2014 Rapport PnL journalier (MM vs CD)\n"
            "/inventory \u2014 Inventaire MM par marche\n"
            "/kill \u2014 Kill switch : annuler tous les ordres\n\n"
            "<b>\U0001f3ae Controle</b>\n"
            "/stopbot — Stopper le processus bot (aucune action)\n"
            "/startbot — Redemarrer le processus bot\n"
            "/pause \u2014 Mettre le trading en pause\n"
            "/resume \u2014 Reprendre le trading\n"
            "/force \u2014 Lancer un cycle d'analyse immediatement\n"
            "/strategy \u2014 Voir la strategie active (mode unique)\n"
            "/learning \u2014 Statut du mode apprentissage\n"
            "/restart \u2014 Redemarrer le bot\n\n"
            "<b>\u2705 Confirmations</b>\n"
            "Les operations au-dessus du seuil necessitent votre approbation.\n"
            "Deux boutons <b>Valider \u2713</b> / <b>Refuser \u2717</b> s'affichent sous chaque proposition.\n\n"
            "<b>\U0001f514 Alertes automatiques</b>\n"
            "\u2022 Execution et expiration des ordres\n"
            "\u2022 Resolution des marches\n"
            "\u2022 Declenchement de stop-loss ou drawdown\n"
            "\u2022 Erreurs du bot"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if not self._portfolio_manager:
            await update.message.reply_text("\U0001f6ab Tableau de bord non disponible.")
            return

        try:
            state = await self._portfolio_manager.get_portfolio_state()

            paused = "\u23f8 EN PAUSE" if (self._risk_manager and self._risk_manager.is_paused) else "\u25b6\ufe0f ACTIF"
            strat = self._current_strategy.name.upper() if self._current_strategy else "ACTIVE"
            lines = [
                f"<b>\U0001f4ca Tableau de bord</b>",
                f"",
                f"\U0001f7e2 <b>Etat :</b> {paused}",
                f"\U0001f4cb <b>Strategie :</b> {strat}",
                f"\U0001f4b0 <b>USDC disponible :</b> {state['available_usdc']:.2f}$",
                f"\U0001f3e6 <b>On-chain :</b> {state.get('onchain_balance') or 0:.2f}$",
                f"\U0001f4b5 <b>Total investi :</b> {state['total_invested']:.2f}$",
                f"\U0001f4c8 <b>P&L du jour :</b> {state['daily_pnl']:+.2f}$",
                f"\U0001f504 <b>Volume du jour :</b> {state['daily_traded']:.2f}$",
                f"\U0001f4ca <b>Positions ouvertes :</b> {state['positions_count']}",
            ]

            positions = state.get("positions", [])
            if positions:
                lines.append("")
                lines.append("<b>\U0001f4bc Positions</b>")
                for pos in positions:
                    question = pos.get("market_question", "?")[:40]
                    outcome = pos.get("outcome", "?")
                    size = pos.get("size", 0)
                    avg = pos.get("avg_price", 0)
                    strategy = pos.get("strategy", "active")
                    pnl = pos.get("pnl_unrealized", 0) or 0
                    pnl_sign = "+" if pnl >= 0 else ""
                    lines.append(
                        f"\U0001f4ca <b>{question}</b>\n"
                        f"  \U0001f4cc {outcome} \u00b7 {size:.1f} jetons a {avg:.3f}$\n"
                        f"  \U0001f4b0 P&L : {pnl_sign}{pnl:.2f}$ \u00b7 {_strategy_label(strategy)}"
                    )

            trades = state.get("recent_trades", [])
            if trades:
                lines.append("")
                lines.append("<b>\U0001f4c3 Trades recents</b>")
                for t in trades[:5]:
                    side = _side_label(t.get("side", "BUY"))
                    outcome = t.get("outcome", "?")
                    size = t.get("size_usdc", 0)
                    status = t.get("status", "?")
                    question = t.get("market_question", "?")[:35]
                    if status == "executed":
                        status_fr = "\u2705 Execute"
                    elif status == "rejected":
                        status_fr = "\u274c Refuse"
                    elif status == "pending":
                        status_fr = "\u23f3 En attente"
                    else:
                        status_fr = status
                    lines.append(
                        f"  {side} {outcome} {size:.2f}$ \u2014 {status_fr}\n"
                        f"  <i>{question}</i>"
                    )

            if not positions and not trades:
                lines.append("")
                lines.append("<i>\U0001f4ad Aucune position ni trade pour le moment.</i>")

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

        except Exception as e:
            logger.error(f"Erreur commande dashboard : {e}")
            await update.message.reply_text("\u274c Erreur interne. Consultez les logs.")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if not self._portfolio_manager:
            await update.message.reply_text("\u23f3 Bot en cours de demarrage, patientez...")
            return
        try:
            state = await self._portfolio_manager.get_portfolio_state()
            is_paused = self._risk_manager and self._risk_manager.is_paused
            paused = "\u23f8 EN PAUSE" if is_paused else "\u25b6\ufe0f ACTIF"
            strat = self._current_strategy.name.upper() if self._current_strategy else "ACTIVE"

            daily_pnl = state['daily_pnl']
            pnl_sign = "+" if daily_pnl >= 0 else ""
            pnl_emoji = "\U0001f7e2" if daily_pnl >= 0 else "\U0001f534"

            text = (
                f"<b>\U0001f916 Etat du bot</b>\n\n"
                f"\U0001f7e2 <b>Statut :</b> {paused}\n"
                f"\U0001f4cb <b>Strategie :</b> {strat}\n"
                f"\U0001f4b0 <b>USDC disponible :</b> {state['available_usdc']:.2f}$\n"
                f"\U0001f3e6 <b>On-chain :</b> {state.get('onchain_balance') or 0:.2f}$\n"
                f"\U0001f4ca <b>Positions :</b> {state['positions_count']}\n"
                f"{pnl_emoji} <b>P&L du jour :</b> {pnl_sign}{daily_pnl:.2f}$\n"
                f"\U0001f504 <b>Volume du jour :</b> {state['daily_traded']:.2f}$\n\n"
                f"<i>\U0001f4a1 Utilisez /positions pour voir le detail, "
                f"/performance pour les statistiques.</i>"
            )
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Erreur commande status : {e}")
            await update.message.reply_text("\u274c Erreur interne. Consultez les logs.")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if not self._portfolio_manager:
            await update.message.reply_text("\u23f3 Donnees non encore disponibles.")
            return
        try:
            state = await self._portfolio_manager.get_portfolio_state()
            positions = state.get("positions", [])
            if not positions:
                await update.message.reply_text(
                    "\U0001f4ad <b>Aucune position ouverte</b>\n\n"
                    "<i>Le bot n'a pas encore pris de position. "
                    "Utilisez /force pour lancer un cycle d'analyse.</i>",
                    parse_mode="HTML",
                )
                return

            total_invested = state.get("total_invested", 0)
            lines = [
                f"<b>\U0001f4bc Positions ouvertes ({len(positions)})</b>",
                f"<i>\U0001f4b5 Total investi : {total_invested:.2f}$</i>\n",
            ]
            for pos in positions:
                question = pos.get("market_question", "?")[:50]
                outcome = pos.get("outcome", "?")
                size = pos.get("size", 0)
                avg = pos.get("avg_price", 0)
                current = pos.get("current_price") or avg
                strategy = pos.get("strategy", "active")
                opened_at = pos.get("opened_at")
                days = _days_since(opened_at)

                # Calcul P&L
                pnl = (current - avg) * size
                pnl_sign = "+" if pnl >= 0 else ""
                pnl_emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"

                lines.append(
                    f"\U0001f4ca <b>{question}</b>\n"
                    f"\U0001f4cc {outcome} \u00b7 {size:.1f} jetons a {avg:.3f}$\n"
                    f"\U0001f4b0 Prix actuel : {current:.3f}$ \u00b7 {pnl_emoji} {pnl_sign}{pnl:.2f}$\n"
                    f"\U0001f4cb Strategie : {strategy.upper()} \u00b7 Depuis {days} jours\n"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Erreur commande positions : {e}")
            await update.message.reply_text("\u274c Erreur interne. Consultez les logs.")

    async def _cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if not self._performance_tracker:
            await update.message.reply_text(
                "\u23f3 Suivi des performances non encore disponible."
            )
            return
        try:
            stats = await self._performance_tracker.get_stats()
            exec_stats = None
            try:
                from db.store import get_execution_quality_stats
                exec_stats = await get_execution_quality_stats(days=7)
            except Exception:
                exec_stats = None

            if stats["resolved_trades"] == 0:
                text = (
                    f"<b>\U0001f4c8 Performance</b>\n\n"
                    f"\U0001f4ca <b>Trades totaux :</b> {stats['total_trades']}\n"
                    f"\u23f3 <b>En attente de resolution :</b> {stats['pending_resolution']}\n\n"
                    f"<i>\U0001f4a1 Aucun trade n'a encore ete resolu. "
                    f"Les statistiques apparaitront apres la premiere resolution de marche.</i>"
                )
            else:
                hit_rate = stats['hit_rate'] * 100
                hr_emoji = "\U0001f7e2" if hit_rate >= 55 else "\U0001f7e1" if hit_rate >= 45 else "\U0001f534"
                pnl = stats['total_pnl']
                pnl_sign = "+" if pnl >= 0 else ""
                pnl_emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"

                text = (
                    f"<b>\U0001f4c8 Performance</b>\n\n"
                    f"\U0001f4ca <b>Trades :</b> {stats['resolved_trades']} resolus / {stats['total_trades']} total\n"
                    f"{hr_emoji} <b>Taux de reussite :</b> {hit_rate:.1f}% "
                    f"({stats['wins']}V / {stats['losses']}D)\n"
                    f"{pnl_emoji} <b>P&L total :</b> {pnl_sign}{pnl:.2f}$\n"
                    f"\U0001f4c8 <b>ROI :</b> {stats['roi_percent']:+.1f}%\n"
                    f"\U0001f4b0 <b>P&L moyen/trade :</b> {stats['avg_pnl_per_trade']:+.2f}$\n"
                    f"\U0001f3c6 <b>Meilleur :</b> {stats['best_trade']:+.2f}$ | "
                    f"<b>Pire :</b> {stats['worst_trade']:+.2f}$\n"
                    f"\U0001f525 <b>Serie en cours :</b> {stats['current_streak']} {stats['streak_type']}\n"
                    f"\u23f3 <b>En attente :</b> {stats['pending_resolution']} trades"
                )

            if exec_stats and exec_stats.get("total_orders", 0) > 0:
                text += (
                    f"\n\n<b>\U0001f4e6 Execution (7j)</b>\n"
                    f"  \u2022 Fill rate : {(exec_stats.get('fill_rate', 0) * 100):.1f}%\n"
                    f"  \u2022 Partial fills : {(exec_stats.get('partial_rate', 0) * 100):.1f}%\n"
                    f"  \u2022 Slippage moyen : {exec_stats.get('avg_slippage_bps', 0):.1f} bps"
                )

            # Ajouter le rapport de calibration si disponible
            cal = await self._performance_tracker.get_calibration_report()
            if cal and cal.get("biases_detected"):
                text += "\n\n<b>\u26a0\ufe0f Biais detectes :</b>\n"
                for bias in cal["biases_detected"]:
                    text += f"  \u2022 {bias}\n"

            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Erreur commande performance : {e}")
            await update.message.reply_text("\u274c Erreur interne. Consultez les logs.")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if self._risk_manager:
            self._risk_manager.is_paused = True
            await update.message.reply_text(
                "\u23f8 <b>Trading mis en pause</b>\n\n"
                "<i>Aucune analyse Claude ne sera lancee tant que vous n'utilisez pas /resume.\n"
                "Utilisez /stopbot pour stopper totalement le processus.</i>",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("\u274c Impossible de mettre en pause.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if self._risk_manager:
            self._risk_manager.is_paused = False
            if self._force_cycle_event:
                self._force_cycle_event.set()
            await update.message.reply_text(
                "\u25b6\ufe0f <b>Trading repris</b>\n\n"
                "<i>Le bot recommence a passer des ordres.\n"
                "Un cycle d'analyse va etre lance.</i>",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("\u274c Impossible de reprendre.")

    async def _cmd_stopbot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if not self._stop_process_callback:
            await update.message.reply_text("\u274c Commande indisponible.")
            return

        await self._stop_process_callback(source="telegram")
        await update.message.reply_text(
            "\U0001f6d1 <b>Processus bot stoppe</b>\n\n"
            "<i>Le bot reste joignable mais n'execute plus de cycle tant que /startbot n'est pas lance.</i>",
            parse_mode="HTML",
        )

    async def _cmd_startbot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if not self._start_process_callback:
            await update.message.reply_text("\u274c Commande indisponible.")
            return

        await self._start_process_callback(source="telegram")
        await update.message.reply_text(
            "\u25b6\ufe0f <b>Processus bot demarre</b>\n\n"
            "<i>Le bot reprend ses cycles d'analyse et de trading.</i>",
            parse_mode="HTML",
        )

    async def _cmd_force(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        if self._force_cycle_event:
            self._force_cycle_event.set()
            await update.message.reply_text(
                "\U0001f504 <b>Cycle d'analyse lance</b>\n\n"
                "<i>Le bot va analyser les marches disponibles et proposer des trades.\n"
                "Cela prend generalement 2-5 minutes.</i>",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("\u274c Impossible de forcer un cycle.")

    async def _cmd_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return

        args = context.args
        if not args:
            # Afficher les boutons inline pour choisir la strategie
            name = self._current_strategy.name if self._current_strategy else "active"
            current_label = _strategy_label(name)

            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
                "\U0001f4c8 ACTIVE \u2014 Trading IA",
                callback_data="strategy_active",
            )]])

            text = (
                f"<b>\U0001f4cb Strategie de trading</b>\n\n"
                f"\U0001f7e2 <b>Strategie active :</b> {current_label}\n\n"
                f"<i>Mode unique active pour un apprentissage plus rapide et plus stable.</i>"
            )
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=keyboard,
            )
            return

        # Gestion par argument texte (compatibilite)
        new_strategy = args[0].lower()
        if new_strategy != "active":
            await update.message.reply_text(
                "\u2139\ufe0f <b>Strategie verrouillee.</b>\n\n"
                "<i>La seule strategie disponible est : active.</i>",
                parse_mode="HTML",
            )
            return

        if self._strategy_switch_callback:
            self._strategy_switch_callback("active")
            from strategy import get_strategy
            self._current_strategy = get_strategy("active")
            await self._persist_runtime_settings({"strategy": "active"})

        strat_label = _strategy_label("active")
        await update.message.reply_text(
            f"\u2139\ufe0f <b>Strategie active : {strat_label}</b>\n\n"
            f"<i>Mode unique applique pour toutes les nouvelles positions.</i>",
            parse_mode="HTML",
        )

    async def _cmd_reglages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Affiche la configuration actuelle du bot."""
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return

        tc = self._trading_config
        if not tc:
            await update.message.reply_text("\u274c Configuration non disponible.")
            return

        strat = self._current_strategy.name.upper() if self._current_strategy else "ACTIVE"
        is_paused = self._risk_manager and self._risk_manager.is_paused
        status = "\u23f8 EN PAUSE" if is_paused else "\u25b6\ufe0f ACTIF"

        text = (
            f"<b>\u2699\ufe0f Reglages du bot</b>\n\n"
            f"\U0001f7e2 <b>Statut :</b> {status}\n"
            f"\U0001f3af <b>Strategie :</b> {strat}\n\n"
            f"<b>\U0001f4b0 Budget</b>\n"
            f"  \u2022 Capital : on-chain balance (API Polymarket)\n"
            f"  \u2022 Max par trade : {tc.max_per_trade_usdc:.0f}$\n"
            f"  \u2022 Max par jour : {tc.max_per_day_usdc:.0f}$\n"
            f"  \u2022 Seuil de confirmation : {tc.confirmation_threshold_usdc:.0f}$\n\n"
            f"<b>\U0001f6e1 Gestion du risque</b>\n"
            f"  \u2022 Edge minimum : {tc.min_edge_percent:.0f}%\n"
            f"  \u2022 Edge net minimum : {tc.min_net_edge_percent:.0f}%\n"
            f"  \u2022 Stop-loss : {tc.stop_loss_percent:.0f}%\n"
            f"  \u2022 Drawdown max : {tc.drawdown_stop_loss_percent:.0f}%\n"
            f"  \u2022 Concentration max : {tc.max_concentration_percent:.0f}%\n"
            f"  \u2022 Positions correlees max : {tc.max_correlated_positions}\n\n"
            f"<b>\U0001f4e6 Execution</b>\n"
            f"  \u2022 Slippage max : {tc.max_slippage_bps:.0f} bps\n"
            f"  \u2022 Qualite source min : {tc.min_source_quality:.2f}\n"
            f"  \u2022 Frais estimes : {tc.estimated_fee_bps:.0f} bps\n\n"
            f"<b>\u23f1 Cycles</b>\n"
            f"  \u2022 Intervalle : {tc.analysis_interval_minutes} min\n"
            f"  \u2022 Plage adaptative : {tc.min_cycle_minutes}-{tc.max_cycle_minutes} min\n"
            f"  \u2022 Timeout ordres : {tc.order_fill_timeout_seconds}s"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_learning(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Affiche le statut du mode apprentissage avec sous-commandes."""
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return

        if not self._learning_journal:
            await update.message.reply_text(
                "\U0001f4d6 <b>Mode apprentissage desactive</b>\n\n"
                "<i>Activez-le avec LEARNING_MODE=true dans .env</i>",
                parse_mode="HTML",
            )
            return

        args = context.args or []
        sub_cmd = args[0].lower() if args else None

        try:
            from db.store import (
                get_journal_entries, get_active_insights,
                get_pending_proposals, get_shadow_records
            )
            import json

            if sub_cmd == "journal":
                await self._learning_journal_detail(update, args, json)

            elif sub_cmd == "insights":
                await self._learning_insights_detail(update)

            elif sub_cmd == "proposals":
                await self._learning_proposals_detail(update)

            elif sub_cmd == "shadow":
                await self._learning_shadow_detail(update)

            else:
                await self._learning_overview(update, json)

        except Exception as e:
            logger.error(f"Erreur commande learning : {e}")
            await update.message.reply_text("\u274c Erreur interne. Consultez les logs.")

    async def _learning_overview(self, update: Update, json):
        """Vue d'ensemble du learning."""
        from db.store import get_journal_entries, get_active_insights, get_pending_proposals

        entries = await get_journal_entries(limit=5)
        insights = await get_active_insights(limit=10)
        proposals = await get_pending_proposals()

        lines = ["<b>\U0001f4d6 Mode apprentissage</b>\n"]

        # Journal
        lines.append(f"<b>\U0001f4d3 Journal ({len(entries)} derniers cycles)</b>")
        if entries:
            for e in entries[:5]:
                retro = {}
                try:
                    retro = json.loads(e.get("retrospective_json") or "{}")
                except (json.JSONDecodeError, TypeError):
                    pass
                quality = retro.get("quality_score", "?")
                conserv = retro.get("conservatism_score", "?")
                diversity = retro.get("diversity_score", "?")
                accuracy = e.get("outcome_accuracy")
                acc_str = f"{accuracy:.0%}" if accuracy is not None else "?"
                lines.append(
                    f"  Cycle {e['cycle_number']}: "
                    f"Q={quality} C={conserv} D={diversity} "
                    f"Acc={acc_str} "
                    f"({e['trades_proposed']}P/{e['trades_executed']}E/{e['trades_skipped']}S)"
                )
        else:
            lines.append("  <i>Aucune entree</i>")

        # Insights
        lines.append(f"\n<b>\U0001f50d Insights actifs ({len(insights)})</b>")
        if insights:
            for i in insights[:5]:
                sev = i.get("severity", "info")
                sev_icon = "\U0001f534" if sev == "critical" else "\U0001f7e1" if sev == "warning" else "\U0001f7e2"
                lines.append(f"  {sev_icon} [{i['insight_type']}] {i['description'][:100]}")
        else:
            lines.append("  <i>Aucun insight</i>")

        # Proposals
        lines.append(f"\n<b>\U0001f4dd Propositions en attente ({len(proposals)})</b>")
        if proposals:
            for p in proposals[:5]:
                risk_icon = "\U0001f7e2" if p["risk_level"] == "safe" else "\U0001f7e1" if p["risk_level"] == "moderate" else "\U0001f534"
                lines.append(
                    f"  {risk_icon} [{p['proposal_type']}] {p['target']}: "
                    f"{p['current_value']} \u2192 {p['proposed_value']}"
                )
        else:
            lines.append("  <i>Aucune proposition</i>")

        lines.append("\n<i>Sous-commandes : /learning journal [N] | insights | proposals | shadow</i>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _learning_journal_detail(self, update: Update, args: list, json):
        """Detail d'un cycle du journal."""
        from db.store import get_journal_entries

        cycle_num = None
        if len(args) > 1:
            try:
                cycle_num = int(args[1])
            except ValueError:
                await update.message.reply_text("\u274c Numero de cycle invalide.")
                return

        entries = await get_journal_entries(limit=50)
        if cycle_num:
            entry = next((e for e in entries if e["cycle_number"] == cycle_num), None)
            if not entry:
                await update.message.reply_text(f"\u274c Cycle #{cycle_num} introuvable.")
                return
            entries = [entry]
        else:
            entries = entries[:1]

        if not entries:
            await update.message.reply_text("\U0001f4ed Aucune entree de journal.")
            return

        e = entries[0]
        retro = {}
        try:
            retro = json.loads(e.get("retrospective_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        lines = [f"<b>\U0001f4d3 Cycle #{e['cycle_number']}</b>\n"]

        # Scores
        lines.append("<b>Scores</b>")
        lines.append(f"  Qualite: {retro.get('quality_score', '?')}/10")
        lines.append(f"  Conservatisme: {retro.get('conservatism_score', '?')}/10")
        lines.append(f"  Diversite: {retro.get('diversity_score', '?')}/10")

        # Stats
        accuracy = e.get("outcome_accuracy")
        acc_str = f"{accuracy:.0%}" if accuracy is not None else "?"
        lines.append(f"\n<b>Statistiques</b>")
        lines.append(f"  {e['trades_proposed']} proposes, {e['trades_executed']} executes, {e['trades_skipped']} sautes")
        lines.append(f"  Precision: {acc_str}")

        # Missed opportunities
        missed = retro.get("missed_opportunities", [])
        if missed:
            lines.append(f"\n<b>\u26a0\ufe0f Opportunites ratees ({len(missed)})</b>")
            for m in missed[:5]:
                lines.append(f"  \u2022 {m.get('market_id', '?')[:30]}: {m.get('reason', '')[:80]}")

        # Questionable trades
        quest = retro.get("questionable_trades", [])
        if quest:
            lines.append(f"\n<b>\u2753 Trades douteux ({len(quest)})</b>")
            for q in quest[:5]:
                lines.append(f"  \u2022 {q.get('market_id', '?')[:30]}: {q.get('concern', '')[:80]}")

        # Bias signals
        biases = retro.get("bias_signals", [])
        if biases:
            lines.append(f"\n<b>\U0001f3af Signaux de biais</b>")
            for b in biases[:5]:
                lines.append(f"  \u2022 {b}")

        # Recommendations
        recos = retro.get("recommendations", [])
        if recos:
            lines.append(f"\n<b>\U0001f4a1 Recommandations</b>")
            for r in recos[:5]:
                lines.append(f"  \u2022 {r}")

        # Summary
        if retro.get("summary"):
            lines.append(f"\n<i>{retro['summary'][:500]}</i>")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _learning_insights_detail(self, update: Update):
        """Liste complete des insights actifs."""
        from db.store import get_active_insights

        insights = await get_active_insights(limit=20)
        if not insights:
            await update.message.reply_text("\U0001f4ed Aucun insight actif.")
            return

        lines = [f"<b>\U0001f50d Insights detectes ({len(insights)})</b>\n"]
        for i in insights:
            sev = i.get("severity", "info")
            sev_icon = "\U0001f534" if sev == "critical" else "\U0001f7e1" if sev == "warning" else "\U0001f7e2"
            lines.append(f"{sev_icon} <b>[{i['insight_type']}]</b>")
            lines.append(f"  {i['description']}")
            if i.get("evidence"):
                lines.append(f"  <i>Evidence: {i['evidence'][:150]}</i>")
            if i.get("proposed_action"):
                lines.append(f"  \u27a4 {i['proposed_action'][:150]}")
            lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _learning_proposals_detail(self, update: Update):
        """Liste complete des propositions en attente."""
        from db.store import get_pending_proposals

        proposals = await get_pending_proposals()
        if not proposals:
            await update.message.reply_text("\U0001f4ed Aucune proposition en attente.")
            return

        lines = [f"<b>\U0001f4dd Propositions en attente ({len(proposals)})</b>\n"]
        for p in proposals[:10]:
            risk_icon = "\U0001f7e2" if p["risk_level"] == "safe" else "\U0001f7e1" if p["risk_level"] == "moderate" else "\U0001f534"
            lines.append(f"{risk_icon} <b>{p['target']}</b> (#{p['id']})")
            lines.append(f"  {p['current_value']} \u2192 {p['proposed_value']}")
            lines.append(f"  <i>{p['rationale'][:200]}</i>")
            lines.append(f"  Type: {p['proposal_type']} | Risque: {p['risk_level']}")
            lines.append("")

        lines.append("<i>\U0001f4a1 Approuvez/rejetez via le dashboard web.</i>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _learning_shadow_detail(self, update: Update):
        """Comparaison A/B shadow mode."""
        from db.store import get_shadow_records

        shadows = await get_shadow_records(limit=10)
        if not shadows:
            await update.message.reply_text("\U0001f4ed Aucun enregistrement shadow.")
            return

        lines = [f"<b>\U0001f3ad A/B Testing Shadow ({len(shadows)} enregistrements)</b>\n"]
        for sh in shadows:
            lines.append(f"<b>Cycle #{sh['cycle_number']}</b> | {(sh.get('market_id') or '')[:25]}")
            lines.append(f"  Actuel: {sh.get('current_decision', 'SKIP')}")
            lines.append(f"  Shadow: {sh.get('shadow_decision', 'SKIP')}")
            if sh.get("outcome_price") is not None:
                lines.append(f"  Prix final: {sh['outcome_price']:.3f}")
            lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    # ─────────────────────────────────────────────
    # Commandes MM / CD
    # ─────────────────────────────────────────────

    async def _cmd_mm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """MM status command."""
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        try:
            from db import store
            quotes = await store.get_active_mm_quotes()
            inventory = await store.get_mm_inventory()
            exposure = await store.get_mm_total_exposure()
            status = await store.get_bot_status()

            lines = ["\U0001f4ca <b>Market-Making Status</b>\n"]
            lines.append(f"Active quotes: {len(quotes)}")
            lines.append(f"Markets with inventory: {len(inventory)}")
            lines.append(f"Total exposure: ${exposure:.2f}")
            lines.append(f"MM cycle: {status.get('mm_cycle', 'N/A')}")
            lines.append(f"Realized PnL: ${float(status.get('mm_realized_pnl', 0)):.4f}")

            if quotes:
                lines.append("\n<b>Active Markets:</b>")
                for q in quotes[:5]:
                    lines.append(
                        f"  \u2022 {q['market_id'][:12]}... "
                        f"B:{q['bid_price']:.2f}/A:{q['ask_price']:.2f}"
                    )

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Erreur commande mm : {e}")
            await update.message.reply_text(f"\u274c Error: {e}")

    async def _cmd_cd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """CD status command."""
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        try:
            from db import store
            signals = await store.get_recent_cd_signals(limit=10)
            status = await store.get_bot_status()

            lines = ["\U0001f4c8 <b>Crypto Directional Status</b>\n"]
            lines.append(f"CD cycle: {status.get('cd_cycle', 'N/A')}")
            lines.append(f"Markets scanned: {status.get('cd_markets_scanned', 0)}")
            lines.append(f"Active signals: {status.get('cd_active_signals', 0)}")

            active = [s for s in signals if s.get("action") in ("trade", "confirming")]
            if active:
                lines.append("\n<b>Recent Signals:</b>")
                for s in active[:5]:
                    lines.append(
                        f"  \u2022 {s.get('coin', '?')} ${s.get('strike', 0):,.0f} "
                        f"edge={s.get('edge_pts', 0):.1f}pts "
                        f"[{s.get('action', '?')}]"
                    )
            else:
                lines.append("\nNo active signals")

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Erreur commande cd : {e}")
            await update.message.reply_text(f"\u274c Error: {e}")

    async def _cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """PnL breakdown command."""
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        try:
            from db import store
            daily = await store.get_mm_daily_metrics(days=7)
            status = await store.get_bot_status()

            lines = ["\U0001f4b0 <b>PnL Report</b>\n"]
            mm_pnl = float(status.get("mm_realized_pnl", 0))
            lines.append(f"MM realized PnL: ${mm_pnl:.4f}")

            if daily:
                lines.append("\n<b>Daily MM Metrics:</b>")
                for d in daily[:7]:
                    lines.append(
                        f"  {d['date']}: fills={d.get('fills_count', 0)}, "
                        f"PnL=${float(d.get('pnl_net', 0)):.4f}"
                    )

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Erreur commande pnl : {e}")
            await update.message.reply_text(f"\u274c Error: {e}")

    async def _cmd_kill(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Emergency kill switch."""
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        try:
            from db import store
            # Cancel all active MM quotes
            quotes = await store.get_active_mm_quotes()
            for q in quotes:
                await store.update_mm_quote_status(q["id"], "killed")

            await update.message.reply_text(
                "\U0001f6d1 <b>KILL SWITCH ACTIVATED</b>\n"
                f"Cancelled {len(quotes)} active quotes\n"
                "All orders cancelled. Trading paused.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Kill switch error: {e}")
            await update.message.reply_text(f"\u274c Kill error: {e}")

    async def _cmd_inventory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show MM inventory."""
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        try:
            from db import store
            inventory = await store.get_mm_inventory()

            if not inventory:
                await update.message.reply_text("\U0001f4e6 No MM inventory")
                return

            lines = ["\U0001f4e6 <b>MM Inventory</b>\n"]
            for inv in inventory:
                pos = float(inv.get("net_position", 0))
                avg = float(inv.get("avg_entry_price", 0))
                rpnl = float(inv.get("realized_pnl", 0))
                lines.append(
                    f"  \u2022 {inv['market_id'][:12]}...\n"
                    f"    Pos: {pos:+.1f} @ {avg:.2f} | PnL: ${rpnl:.4f}"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Erreur commande inventory : {e}")
            await update.message.reply_text(f"\u274c Error: {e}")

    async def _cmd_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("\U0001f6ab Acces non autorise.")
            return
        await update.message.reply_text(
            "\U0001f504 <b>Redemarrage du bot en cours...</b>\n\n"
            "<i>Le bot va s'arreter puis redemarrer automatiquement via PM2.</i>",
            parse_mode="HTML",
        )
        if self._stop_callback:
            await self._stop_callback()
