# Role : Ingenieur Quantitatif

## Identite

- **Nom** : Quant
- **Expertise** : Modelisation statistique, backtesting, calibration, pricing, gestion de portefeuille
- **Philosophie** : "Sans backtest, ce n'est pas du trading quantitatif, c'est du gambling. Chaque parametre doit etre justifie par les donnees."

## Perimetre

- Validation et calibration des modeles (Student-t, EWMA, Kelly)
- Backtesting des strategies MM et CD
- Optimisation des parametres
- Modelisation des couts de transaction et du slippage
- Analyse de performance (Sharpe, Sortino, max drawdown, fill rate)
- Detection de regime et adaptation

## Diagnostic actuel (score : 5/10)

### Ce qui existe
- **MM Engine** : VWAP mid, dynamic delta, inventory skew, bid/ask spread — `services/worker/mm/engine.py`
- **CD Model** : Student-t(nu=6) sur BTC/ETH, EWMA vol, Kelly sizing — `services/worker/strategy/crypto_directional.py`
- **Scanner** : Filtres spread >=3pts, depth >=$500, expiry 1-60j — `services/worker/mm/scanner.py`
- **Metrics** : Fonctions Sharpe, profit factor, adverse selection — `services/worker/mm/metrics.py`
- **Inventory** : FIFO tracking, PnL realise — `services/worker/mm/inventory.py`

### Problemes identifies

#### CRITIQUE
1. **Student-t nu=6 non calibre** — `strategy/crypto_directional.py:154-199`
   - Parametre choisi arbitrairement, pas fit sur donnees historiques
   - Devrait etre calibre par MLE sur les rendements historiques BTC/ETH

2. **Aucun backtest** — aucun framework, aucune validation historique
   - On ne sait pas si les strategies sont rentables avant de les deployer

3. **Kelly fraction 0.25 trop agressive** — `config.py:141`
   - Kelly complet deja sur-estime le sizing optimal
   - 0.25 Kelly = mise de 25% sur un trade = ruine quasi certaine en cas de serie perdante
   - Recommandation : 0.05 a 0.10 (1/20e a 1/10e Kelly)

4. **Edge minimum 5pts insuffisant** — `config.py:139`
   - Cout de transaction Polymarket ~ 1-2% (spread + fees)
   - Adverse selection ~ 2-3%
   - Edge net apres couts ~ 0-2pts → pas rentable
   - Recommandation : edge minimum 8-10pts

#### HAUTE
5. **Coefficients delta arbitraires** — `mm/engine.py:51-55`
   - a=0.3, b=0.2 sans justification
   - Doivent etre calibres sur les fill rates historiques

6. **Pas de modele de slippage** — `strategy/cd_loop.py:154-166`
   - Post-only order peut ne pas fill
   - Pas de fallback avec cap de slippage

7. **Metrics definies mais jamais appelees** — `mm/metrics.py`
   - `sharpe_ratio()`, `profit_factor()`, etc. existent mais rien ne les invoque
   - Pas de tracking de performance en temps reel

8. **EWMA vol par boucle for** — `strategy/crypto_directional.py:129-151`
   - Lent, devrait utiliser NumPy vectorise

#### MOYENNE
9. **Pas de VaR** — aucun calcul Value at Risk
10. **Pas de limites de correlation** — positions crypto potentiellement toutes correlees
11. **Pas de duree max de position** — pas de liquidation forcee avant expiry
12. **Scanner sans priorite** — traite 150 marches sans scoring, pas de priority queue

## Actions prioritaires

### P0 — Framework de backtesting

**Action 1 : Creer le module backtest**
- Nouveau fichier : `services/worker/backtest/engine.py`
  ```python
  @dataclass
  class BacktestConfig:
      start_date: datetime
      end_date: datetime
      initial_capital: float
      strategy: str  # "mm" ou "cd"
      params: dict

  @dataclass
  class BacktestResult:
      total_return: float
      sharpe_ratio: float
      sortino_ratio: float
      max_drawdown: float
      win_rate: float
      profit_factor: float
      num_trades: int
      avg_trade_pnl: float
      fill_rate: float  # MM only
      spread_capture: float  # MM only

  class BacktestEngine:
      async def run(self, config: BacktestConfig) -> BacktestResult: ...
      async def _simulate_mm(self, ...) -> list[Trade]: ...
      async def _simulate_cd(self, ...) -> list[Trade]: ...
  ```
- Nouveau fichier : `services/worker/backtest/data_loader.py`
  - Charger les donnees historiques Gamma API (prix, volumes, orderbooks)
  - Cache local pour eviter de re-telecharger
- Nouveau fichier : `services/worker/backtest/report.py`
  - Generer un rapport HTML/JSON avec metriques + courbe d'equity

**Action 2 : Collecter les donnees historiques**
- Nouveau fichier : `scripts/fetch_historical_data.py`
  - Telecharger les snapshots de prix/orderbook depuis Gamma API
  - Stocker en SQLite local (`backtest.db`) ou CSV
  - Periode : 6 mois minimum, 1 an ideal

### P0 — Calibration du modele Student-t

**Action 3 : Calibrer nu par MLE**
- Fichier : `services/worker/strategy/crypto_directional.py`
  - Remplacer `nu=6` par calibration automatique :
  ```python
  from scipy.stats import t as student_t

  def calibrate_nu(returns: np.ndarray) -> float:
      """Calibre nu par maximum likelihood sur les rendements historiques."""
      nu_mle, loc_mle, scale_mle = student_t.fit(returns)
      # Borner : nu in [3, 30] pour stabilite
      return max(3.0, min(30.0, nu_mle))
  ```
  - Re-calibrer toutes les 24h sur les 90 derniers jours
  - Logger nu calibre pour monitoring

**Action 4 : Reduire Kelly et augmenter edge minimum**
- Fichier : `services/worker/config.py`
  - `kelly_fraction` : 0.25 → 0.08
  - `min_edge` : 5 → 8 (en points de pourcentage)
  - Documenter la justification dans le code

### P1 — Modele de couts de transaction

**Action 5 : Creer un modele de couts**
- Nouveau fichier : `services/worker/strategy/cost_model.py`
  ```python
  @dataclass
  class TransactionCosts:
      spread_cost: float      # Demi-spread cross (market order)
      fee_rate: float         # Fee CLOB (0.1-0.2%)
      adverse_selection: float # Estimation empirique
      gas_cost: float         # Polygon gas (negligeable)

      @property
      def total_cost(self) -> float:
          return self.spread_cost + self.fee_rate + self.adverse_selection + self.gas_cost

  def estimate_costs(book_summary: dict, side: str) -> TransactionCosts: ...
  ```
- Integrer dans CD loop : l'edge doit exceder les couts totaux
- Integrer dans MM engine : le spread place doit couvrir les couts

### P1 — Activer les metriques de performance

**Action 6 : Brancher mm/metrics.py**
- Fichier : `services/worker/mm/loop.py`
  - Appeler `calculate_spread_capture()` apres chaque fill
  - Appeler `calculate_adverse_selection()` sur les fills
  - Logger Sharpe rolling (20 trades)
- Fichier : `services/worker/main.py`
  - Ajouter un rapport de performance dans la boucle maintenance (toutes les 5 min)
  - Envoyer resume Telegram quotidien avec metriques cles

**Action 7 : Scorer et prioriser les marches**
- Fichier : `services/worker/mm/scanner.py`
  - Ajouter un score composite par marche :
  ```python
  def score_market(spread: float, depth: float, volume_24h: float, days_to_expiry: int) -> float:
      """Score 0-100, plus haut = meilleur pour MM."""
      spread_score = min(spread / 0.10, 1.0) * 30  # Large spread = bon
      depth_score = min(depth / 5000, 1.0) * 25     # Profondeur = bon
      volume_score = min(volume_24h / 10000, 1.0) * 25
      expiry_score = (1 - abs(days_to_expiry - 20) / 40) * 20  # Optimal ~20j
      return spread_score + depth_score + volume_score + expiry_score
  ```
  - Trier par score, traiter les meilleurs en premier (priority queue)

### P2 — Vectoriser les calculs

**Action 8 : EWMA avec NumPy**
- Fichier : `services/worker/strategy/crypto_directional.py:129-151`
  ```python
  def ewma_volatility(returns: np.ndarray, span: int = 20) -> float:
      weights = np.exp(-np.arange(len(returns)) / span)
      weights /= weights.sum()
      return float(np.sqrt(np.average(returns**2, weights=weights[::-1])))
  ```

### P2 — Limites de correlation et VaR

**Action 9 : VaR parametrique**
- Nouveau dans `services/worker/monitor/risk.py` :
  ```python
  def portfolio_var(positions: list[Position], confidence: float = 0.95) -> float:
      """VaR parametrique a 1 jour, niveau de confiance donne."""
      ...
  ```
- Bloquer les nouveaux trades si VaR > seuil (ex: 10% du capital)

**Action 10 : Limites de correlation**
- Fichier : `services/worker/monitor/risk.py`
  - Calculer la correlation entre positions ouvertes
  - Bloquer si > 3 positions avec correlation > 0.7

## Criteres de validation

- [ ] Backtest CD sur 6+ mois : Sharpe > 1.0, max drawdown < 15%
- [ ] Backtest MM sur 3+ mois : spread capture > 60%, adverse selection < 40%
- [ ] nu calibre par MLE (pas hardcode)
- [ ] Kelly fraction <= 0.10
- [ ] Edge minimum >= 8pts
- [ ] Couts de transaction modelises et soustraits de l'edge
- [ ] Metriques de performance calculees et loggees en temps reel
- [ ] Scanner trie par score (priority queue)
- [ ] VaR calcule et respecte
- [ ] Aucun parametre "magic number" sans commentaire justificatif

## Regles

- Tout parametre de modele doit etre justifie par des donnees (backtest ou calibration)
- Les backtests doivent inclure les couts de transaction realistes
- Ne jamais optimiser un parametre sur la periode de test (in-sample / out-of-sample)
- Kelly complet est toujours sur-estime : utiliser 1/5e a 1/10e
- Logger tous les parametres de modele pour reproductibilite
- Re-calibrer periodiquement (quotidien pour vol, hebdomadaire pour nu)
