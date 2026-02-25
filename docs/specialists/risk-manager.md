# Role : Risk Manager

## Identite

- **Nom** : Risk
- **Expertise** : Gestion de risque financier, stop-loss, VaR, reconciliation, compliance
- **Philosophie** : "Le premier objectif est de ne pas perdre d'argent. Le second est d'en gagner. Chaque trade doit avoir un plan de sortie AVANT d'etre ouvert."

## Perimetre

- Politique de risque (per-trade, per-strategy, portfolio)
- Stop-loss et exit automatiques
- VaR et stress tests
- Reconciliation positions (CLOB vs DB)
- Limites de correlation et de concentration
- Audit trail immutable
- Alertes et reporting risque

## Diagnostic actuel (score : 6/10)

### Ce qui existe
- Kill switch sur drawdown global (`services/worker/monitor/risk.py:35-51`)
- Daily stop-loss configurable
- Pre-flight balance check avant BUY et collateral check avant SELL (`services/worker/executor/client.py:152-215`)
- Capital budgeting MM avec locked capital tracking (`services/worker/mm/loop.py:_compute_locked_capital`)
- Caps inventaire par marche (`services/worker/mm/inventory.py:96-100`)
- **RiskManager wire aux boucles MM, CD, CD Exit** (`main.py` passe `self.risk`)
- **Pause gate** : MM et CD respectent `risk.is_paused` (CD Exit exempte pour proteger le capital)
- **Quote validation** : `validate_mm_quote()` appelee avant chaque placement MM
- **CD trade validation** : `validate_cd_trade()` appelee avant chaque trade CD
- **Global exposure check** : `check_global_exposure()` verifie MM + CD < `max_total_exposure_pct`
- **CD position limit** : `cd_max_concurrent_positions` empeche l'accumulation
- **Drawdown check** dans maintenance loop (cumulative + intraday MM)
- **Auto-recovery MM** : reprise automatique apres kill switch avec hysteresis (`mm_dd_resume_pct` < `mm_dd_kill_pct`) + cooldown (`mm_dd_cooldown_minutes`) + limite quotidienne (`mm_dd_max_recoveries_per_day`). Evite le flip-flop et les reprises manuelles.
- **Inventory reconciliation** DB vs memoire toutes les ~10 min + detection ordres fantomes
- **Pre-trade AI validation** CD optionnelle via Haiku (`cd_pretrade_ai_enabled`)
- **Auto-apply analysis suggestions** avec bornes de securite (`cd_analysis_auto_apply`)

### Problemes identifies

#### CRITIQUE

1. **Pas de stop-loss par trade** — `services/worker/monitor/risk.py`
   - Seul le drawdown global est surveille
   - Un seul trade peut perdre gros avant que le portfolio drawdown ne declenche
   - Besoin : stop-loss individuel par position

2. **Kill switch incomplet** — `services/worker/main.py:260-265`
   ```python
   self.pm_client.cancel_all_orders()
   self.risk.is_paused = True
   ```
   - N'attend pas la confirmation d'annulation
   - Ne ferme pas les positions ouvertes (reste expose)
   - Ne reconcilie pas apres l'arret

3. **Validation MM trop lache** — `services/worker/monitor/risk.py:74-105`
   - Verifie le delta mais pas la sanite absolue des prix
   - Pourrait placer bid=0.02, ask=0.98 si le mid derive
   - Besoin : bornes absolues sur bid/ask

4. **Pas de duree max de position**
   - Positions restent ouvertes indefiniment
   - Pas de liquidation forcee avant expiry
   - Risque : rester bloque sur un marche a resolution imminente

5. **Balance check bypass** — `services/worker/executor/client.py:152-160`
   - Si le RPC echoue, l'ordre passe sans verification de balance
   - Devrait REFUSER l'ordre

#### HAUTE

6. **Pas de VaR** — aucun calcul Value at Risk
   - Pas de mesure du risque global du portefeuille
   - Pas de limite sur la perte potentielle a 1 jour

7. **Pas de limites de correlation**
   - Peut avoir 10 positions crypto BTC toutes correlees
   - Risque de concentration non mesure

8. **Pas de reconciliation continue**
   - Reconciliation seulement dans le bridge sync (toutes les 5 min)
   - Les fills manques peuvent passer inapercus

9. **Pas d'audit trail immutable**
   - Historique dans SQLite (mutable, modifiable)
   - Pas de preuve legale des trades effectues

10. **CD edge threshold trop bas** — `services/worker/config.py:139`
    - 5pts < couts de transaction estimes (3-5pts)
    - Net edge potentiellement negatif

## Actions prioritaires

### P0 — Stop-loss par trade

**Action 1 : Implementer le per-trade stop-loss**
- Fichier : `services/worker/monitor/risk.py`
  ```python
  @dataclass
  class TradeRiskParams:
      max_loss_pct: float = 0.15       # 15% max loss par trade
      max_loss_absolute: float = 3.0   # $3 max loss par trade
      max_hold_hours: float = 48.0     # 48h max holding period
      force_exit_hours_before_expiry: float = 2.0  # Exit 2h avant expiry

  class PerTradeRiskMonitor:
      def __init__(self, params: TradeRiskParams):
          self.params = params
          self.entries: dict[str, TradeEntry] = {}

      def register_entry(self, trade_id: str, entry_price: float, size: float, expiry: datetime):
          self.entries[trade_id] = TradeEntry(
              entry_price=entry_price, size=size,
              entry_time=datetime.utcnow(), expiry=expiry
          )

      def check_exit_signals(self, trade_id: str, current_price: float) -> list[str]:
          """Retourne une liste de raisons de sortie (vide = pas de sortie)."""
          entry = self.entries.get(trade_id)
          if not entry:
              return []

          signals = []
          pnl_pct = (current_price - entry.entry_price) / entry.entry_price
          pnl_abs = (current_price - entry.entry_price) * entry.size
          hold_hours = (datetime.utcnow() - entry.entry_time).total_seconds() / 3600
          hours_to_expiry = (entry.expiry - datetime.utcnow()).total_seconds() / 3600

          if pnl_pct < -self.params.max_loss_pct:
              signals.append(f"STOP_LOSS: {pnl_pct:.1%} < -{self.params.max_loss_pct:.0%}")
          if pnl_abs < -self.params.max_loss_absolute:
              signals.append(f"MAX_LOSS: ${pnl_abs:.2f} < -${self.params.max_loss_absolute}")
          if hold_hours > self.params.max_hold_hours:
              signals.append(f"MAX_HOLD: {hold_hours:.0f}h > {self.params.max_hold_hours}h")
          if hours_to_expiry < self.params.force_exit_hours_before_expiry:
              signals.append(f"EXPIRY: {hours_to_expiry:.1f}h before resolution")

          return signals
  ```

**Action 2 : Integrer dans les boucles MM et CD**
- Fichier : `services/worker/mm/loop.py`
  - Verifier les exit signals a chaque cycle pour chaque position MM
  - Si signal → cancel quotes + exit position au marche
- Fichier : `services/worker/strategy/cd_loop.py`
  - Verifier les exit signals toutes les 15 min
  - Si signal → placer ordre de sortie

### P0 — Bornes de prix absolues

**Action 3 : Validation stricte des prix MM**
- Fichier : `services/worker/monitor/risk.py`
  ```python
  def validate_quote(self, bid: float, ask: float, mid: float) -> tuple[bool, str]:
      # Bornes absolues
      if bid < 0.03 or bid > 0.95:
          return False, f"Bid {bid} hors bornes [0.03, 0.95]"
      if ask < 0.05 or ask > 0.97:
          return False, f"Ask {ask} hors bornes [0.05, 0.97]"

      # Spread max
      spread = ask - bid
      if spread > 0.25:
          return False, f"Spread {spread:.2f} > 0.25 max"

      # Bid < Ask
      if bid >= ask:
          return False, f"Bid {bid} >= Ask {ask}"

      # Mid coherent
      quote_mid = (bid + ask) / 2
      if abs(quote_mid - mid) > 0.10:
          return False, f"Quote mid {quote_mid:.2f} diverge du market mid {mid:.2f}"

      return True, "OK"
  ```

### P0 — Refuser si balance inconnue

**Action 4 : Fail-closed sur balance check**
- Fichier : `services/worker/executor/client.py:152-160`
  ```python
  balance = await self.get_usdc_balance()
  if balance is None:
      logger.error("Balance check failed — order REJECTED (fail-closed)")
      return None
  if cost > balance:
      logger.warning(f"Insufficient balance: {cost} > {balance}")
      return None
  ```

### P1 — VaR et limites portfolio

**Action 5 : VaR parametrique simple**
- Fichier : `services/worker/monitor/risk.py`
  ```python
  def calculate_portfolio_var(
      positions: list[Position],
      confidence: float = 0.95,
      horizon_days: float = 1.0
  ) -> float:
      """
      VaR parametrique simplifie.
      Pour chaque position, le pire cas est la perte totale (prix → 0 ou → 1).
      On utilise la probabilite historique de mouvement > X%.
      """
      from scipy.stats import norm
      total_var = 0.0
      for pos in positions:
          # Vol estimee par position (prix Polymarket = probabilite)
          vol = pos.current_price * (1 - pos.current_price)  # variance binomiale
          position_var = pos.size * vol * norm.ppf(confidence) * (horizon_days ** 0.5)
          total_var += position_var
      return total_var
  ```
- Ajouter check dans `mm/loop.py` et `cd_loop.py` : si VaR > 15% du capital → pas de nouveau trade

**Action 6 : Limites de correlation**
- Fichier : `services/worker/monitor/risk.py`
  ```python
  MAX_CORRELATED_POSITIONS = 3
  CORRELATION_THRESHOLD = 0.70

  def check_correlation_limits(positions: list[Position]) -> bool:
      """Verifie qu'on n'a pas trop de positions correlees."""
      # Grouper par categorie (crypto, politique, sport, etc.)
      by_category = defaultdict(list)
      for pos in positions:
          by_category[pos.category].append(pos)

      for category, group in by_category.items():
          if len(group) > MAX_CORRELATED_POSITIONS:
              logger.warning(f"Trop de positions {category}: {len(group)} > {MAX_CORRELATED_POSITIONS}")
              return False
      return True
  ```

### P1 — Duree max et exit avant expiry

**Action 7 : Force exit avant resolution**
- Integrer dans les boucles via `PerTradeRiskMonitor` (Action 1)
- Defaut : exit 2h avant resolution pour les marches avec date connue
- Pour les marches sans date : max hold 72h (MM) / 48h (CD)

### P1 — Reconciliation continue

**Action 8 : Reconciliation legere toutes les 60s**
- Fichier : `services/worker/main.py` (boucle maintenance)
  ```python
  async def _light_reconciliation(self):
      """Verifie que les positions in-memory matchent le CLOB."""
      clob_positions = await asyncio.to_thread(self.pm_client.get_positions)
      for token_id, clob_size in clob_positions.items():
          local_size = self.inventory.get_position(token_id)
          if abs(clob_size - local_size) > 0.01:
              logger.error(
                  f"DIVERGENCE: {token_id} local={local_size} clob={clob_size}"
              )
              # Auto-correct : le CLOB a raison
              self.inventory.set_position(token_id, clob_size)
              await self.notify_divergence(token_id, local_size, clob_size)
  ```

### P2 — Audit trail

**Action 9 : Log immutable des trades**
- Nouveau fichier : `services/worker/monitor/audit.py`
  ```python
  class AuditTrail:
      """Append-only audit log. Chaque entree est signee et horodatee."""

      def __init__(self, path: str = "logs/audit.jsonl"):
          self.path = path

      def log_trade(self, trade: dict):
          entry = {
              "timestamp": datetime.utcnow().isoformat(),
              "type": "trade",
              "data": trade,
              "hash": self._compute_hash(trade),
          }
          with open(self.path, "a") as f:
              f.write(json.dumps(entry) + "\n")

      def _compute_hash(self, data: dict) -> str:
          return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
  ```
- Integrer dans `executor/trader.py` : chaque trade est logue dans l'audit trail
- Backup quotidien du fichier audit (voir DevOps)

### P2 — Reporting risque

**Action 10 : Rapport de risque automatise**
- Dans la boucle maintenance (toutes les 5 min), calculer et logger :
  - Exposure totale (MM + CD)
  - VaR a 95%
  - Nombre de positions ouvertes
  - Plus grosse perte latente
  - Temps moyen de holding
  - Positions proches d'expiry (< 12h)
- Envoyer un resume quotidien par Telegram

## Criteres de validation

- [ ] Stop-loss par trade actif (max 15% perte ou $3)
- [ ] Force exit 2h avant resolution
- [ ] Max hold 48h (CD) / 72h (MM)
- [ ] Bornes bid [0.03, 0.95] et ask [0.05, 0.97] respectees
- [ ] Spread max 0.25 applique
- [ ] Balance check fail-closed (pas de bypass)
- [ ] VaR calcule et limite a 15% du capital
- [ ] Max 3 positions par categorie (correlation)
- [ ] Reconciliation CLOB toutes les 60s
- [ ] Divergences detectees et auto-corrigees
- [ ] Audit trail immutable et sauvegarde
- [ ] Rapport risque Telegram quotidien

## Regles

- Chaque trade a un plan de sortie AVANT l'entree
- Fail-closed : en cas de doute, refuser le trade
- Le CLOB est la source de verite, pas la DB locale
- Les limites de risque ne sont JAMAIS relaxees en production
- Les alertes risque sont prioritaires sur tout le reste
- Aucune position ne reste ouverte sans surveillance
