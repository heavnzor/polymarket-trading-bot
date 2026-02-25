# Strategie de Trading — Polymarket Bot v3

Le bot execute deux strategies independantes en parallele sur les marches predictifs Polymarket :

1. **Market-Making (MM)** — fournir de la liquidite en placant des ordres bid/ask sur des marches a spread large
2. **Crypto Directional (CD)** — prendre des positions directionnelles sur des marches de seuil BTC/ETH via un modele Student-t

Les deux strategies partagent un budget commun (le solde USDC.e on-chain) et un gestionnaire de risque unifie.

---

## 1. Market-Making (MM)

### Principe

Le bot agit comme un market-maker : il place simultanement un ordre d'achat (bid) et un ordre de vente (ask) sur des marches predictifs, en capturant le spread entre les deux. Il ne prend pas de vue directionnelle — l'objectif est de profiter de l'ecart bid/ask.

### Quand le bot achete (BID)

Un ordre BID (achat de tokens YES) est place quand **toutes** ces conditions sont reunies :

| Condition | Seuil par defaut |
|-----------|-----------------|
| Le marche passe les filtres du scanner (spread, depth, volume, expiration) | spread >= 3 pts, depth >= $500, 1-60j avant resolution |
| Le scorer IA (optionnel) valide le marche | score >= 5.0/10 |
| Le Claude Guard n'a pas tue le marche | pas dans la kill list |
| Le risk manager n'est pas en pause | `is_paused = false` |
| L'exposition globale (MM+CD) est sous la limite | < 75% du portefeuille |
| Il reste du capital libre | `free_capital >= $5` |
| L'inventaire n'est pas a capacite maximale | `position < max_per_market` |
| Le quote passe la validation risque | bid < ask, dans [0.01, 0.99], spread >= 1pt |
| Le marche n'est pas en cooldown cross-reject | pas de rejet croise recent |

**Prix du BID** : `mid - delta + skew`, ou :
- `mid` = milieu du carnet d'ordres
- `delta` = demi-spread dynamique (1.5 a 8.0 cents), calcule a partir de la volatilite, du desequilibre du carnet, et d'un buffer
- `skew` = ajustement d'inventaire — si le bot est deja long, le bid descend pour decourager les achats

**Taille** : `min($5, 10% du capital, capacite restante)`

### Quand le bot vend (ASK)

Un ordre ASK (vente de tokens YES) est place quand le bot **detient deja de l'inventaire** (>= 5 shares) sur ce marche. L'ASK est toujours place en parallele du BID quand les deux conditions sont remplies.

**Prix de l'ASK** : `mid + delta + skew`

Si le bot est tres long sur un marche, le skew pousse les deux prix vers le bas (bid et ask), ce qui rend l'ASK plus attractif et favorise la reduction d'inventaire.

### Post-only et repricing

Par defaut, tous les ordres MM sont **post-only** (maker seulement, 0% de frais sur Polymarket). Si un ordre croiserait le carnet :
- Le prix est ajuste automatiquement (jusqu'a 5 ticks) pour rester passif
- Apres 3 rejets consecutifs, un cooldown exponentiel s'applique (5-10 min)

### Requote

Les ordres sont **requotes** (annules puis replaces) quand le mid bouge de >= 0.5 pts, ou quand les sides necessaires changent (ex: gain d'inventaire qui necessite un ASK).

### Detection des fills

Toutes les 10 secondes, le bot verifie le statut de chaque ordre actif via le CLOB. Quand un fill est detecte :
- L'inventaire est mis a jour (prix moyen d'entree recalcule)
- Le PnL realise est calcule en FIFO
- Le fill est persiste en DB

### Cycle de vie complet d'un trade MM

```
Scan marches (cache 5min)
  → Filtres : spread >= 3pts, depth >= $500, 1-60j, prix [0.02, 0.98]
  → Score IA optionnel (Sonnet) : resolution clarity, quality, profitability
  → Tri par spread (marches recents boostes +3pts)
  → Cap a 10 marches simultanes

Pour chaque marche :
  → Verif risk (pause, exposure, guard kill list, cooldown)
  → Calcul mid, delta, skew → bid/ask
  → Validation risque (bid < ask, range, spread >= 1pt)
  → Sanitisation post-only (ne pas croiser le carnet)
  → Placement BID + ASK (ou un seul cote selon inventaire/capacite)
  → Suivi des fills → mise a jour inventaire → PnL FIFO
  → Requote si mid bouge >= 0.5 pts
```

---

## 2. Crypto Directional (CD)

### Principe

Le bot detecte des **mispricings** sur les marches de seuil crypto ("Will BTC be above $100,000 on June 30?") en comparant le prix du marche avec une probabilite calculee par un modele Student-t. Si l'ecart (edge) est significatif et persistant, le bot achete des tokens YES.

### Le modele Student-t

Le bot estime la probabilite que le prix d'un crypto depasse un seuil donne :

1. **Volatilite EWMA** (lambda=0.94) : calcule sur 30 jours de prix CoinGecko, ponderee exponentiellement (les jours recents comptent plus)
2. **Distribution Student-t (nu=6)** : les rendements log-normaux suivent une Student-t plutot qu'une gaussienne. Avec nu=6, les queues sont plus epaisses, ce qui capture mieux les mouvements extremes du crypto. Concretement, le modele attribue une probabilite plus elevee aux gros mouvements de prix qu'un modele normal.
3. **Scaling temporel** : la volatilite journaliere est mise a l'echelle de l'horizon via `sigma_T = sigma * sqrt(jours)`
4. **Probabilite** : `P(spot > strike) = 1 - CDF_t(d_scaled, nu=6)` ou `d_scaled = ln(strike/spot) / sigma_T * sqrt((nu-2)/nu)`

### Quand le bot achete (ENTRY)

Un trade CD (achat de tokens YES) est declenche quand **toutes** ces conditions sont reunies :

| Condition | Seuil par defaut |
|-----------|-----------------|
| Le modele calcule un edge positif | `p_model - p_market >= 5.0 pts` |
| L'edge est confirme sur 2 cycles consecutifs | 2 x 15min = 30 minutes minimum |
| Le risk manager n'est pas en pause | `is_paused = false` |
| Nombre de positions CD ouvertes sous la limite | < 5 positions simultanees |
| L'exposition globale (MM+CD) est sous la limite | < 75% du portefeuille |
| Le volume journalier CD est sous la limite | < 50% du solde disponible |
| Le sizing Kelly produit une taille viable | >= $1.00 et >= 5 shares |
| La validation pre-trade IA passe (active par defaut) | Haiku ne rejette pas |

**Prix d'achat** : prix du marche (`p_market`), arrondi a 2 decimales. En mode post-only, plafonne au best bid.

**Taille de la position (Kelly)** :
```
b = (1 - p_market) / p_market          # ratio de payout
f* = (p_model * b - (1 - p_model)) / b  # fraction Kelly complete
taille = f* * 0.25 * capital             # quarter Kelly (conservateur)
taille = min(taille, 5% du capital)      # cap par position
```

Le quarter Kelly (0.25) est un choix conservateur qui reduit fortement la variance au prix d'un rendement attendu legerement inferieur.

**Exemple concret** : capital = $1000, p_model = 0.60, p_market = 0.50, edge = 10 pts
- b = 1.0, f* = 0.20
- taille = 0.20 x 0.25 x 1000 = **$50**
- cap : min($50, $50) = $50

### Quand le bot vend (EXIT)

Le bot surveille ses positions ouvertes toutes les **2 minutes** et sort automatiquement dans trois cas :

| Condition de sortie | Seuil par defaut | Confirmation IA |
|--------------------|-----------------|-----------------|
| **Stop-loss** : le prix chute | perte >= 15 pts sous l'entree | Non (exit immediate) |
| **Take-profit** : le prix monte | gain >= 20 pts au-dessus de l'entree | Non (exit immediate) |
| **Edge reversal** : le modele change d'avis | edge recalcule <= -3 pts | Oui (Haiku, active par defaut) |

La boucle d'exit n'est **jamais en pause** — meme si le kill switch est actif, les exits continuent pour proteger le capital.

**Prix de vente** : best bid du carnet d'ordres.

**Pour l'edge reversal** : le modele est re-execute avec le prix spot et la volatilite actuels. Le recalcul utilise le **midpoint reel du CLOB** comme `p_market` (et non plus une baseline fixe de 0.5), ainsi que les **jours restants avant expiry** (trackes dans `cd_positions`, degrades par le temps ecoule depuis l'ouverture de la position) au lieu d'un defaut de 30 jours. Si l'edge est maintenant negatif (<= -3 pts), le modele considere que la position n'est plus justifiee. Haiku (IA) confirme si le renversement est fondamental ou du bruit — en cas de doute ou d'erreur, l'exit se fait quand meme (fail-safe).

### Cycle de vie complet d'un trade CD

```
Toutes les 15 minutes :
  → Decouverte marches crypto via Gamma API (tag "crypto")
  → Parsing NL via Sonnet : extraction coin, strike, direction
  → Recuperation prix spot (CoinGecko) + historique 30j
  → Calcul volatilite EWMA (lambda=0.94)
  → Calcul probabilite Student-t (nu=6)
  → Calcul edge = (p_model - p_market) * 100

  Si edge >= 5 pts :
    → Cycle 1 : "confirming" (on attend)
    → Cycle 2 : edge toujours >= 5 pts → CONFIRMED
    → Sizing Kelly (quarter, cap 5%)
    → Validations : risk, exposure, balance, taille min
    → Validation pre-trade Haiku (active par defaut) :
        contexte enrichi (portfolio, vol regime, spot distance)
        reponse IA stockee dans cd_signals.ai_validation
    → ACHAT limit order au prix du marche

Toutes les 2 minutes (exit) :
  Pour chaque position ouverte :
    → Expiry degradee par le temps ecoule (cd_positions.expiry_days)
    → Check stop-loss (>= 15 pts de perte)
    → Check take-profit (>= 20 pts de gain)
    → Check edge reversal (<= -3 pts, midpoint CLOB reel, confirme par Haiku)
    → VENTE au best bid si condition atteinte

Toutes les 6 heures (analyse) :
  → Claude Opus revoit les 100 derniers signaux et 50 trades fermes
  → Scores de qualite (1-10) sur precision, entrees, sorties, fitness du modele
  → Suggestions de parametres (optionnel, bornes de securite)
```

---

## 2b. Arbitrage Complete-Set (MM)

### Principe

En complement du market-making classique, le bot peut exploiter des **mispricings entre tokens YES et NO** du meme marche. Sur Polymarket, un complete set (1 YES + 1 NO) vaut toujours exactement $1.00 a la resolution. Le contrat CTF permet de merge (fusionner YES+NO en USDC) ou split (separer USDC en YES+NO) **sans frais Polymarket**. Le seul cout est le gas Polygon (~$0.005).

### Buy-Merge

Quand `best_ask(YES) + best_ask(NO) < $1.00`, le bot :
1. Achete des tokens YES au meilleur ask
2. Achete des tokens NO au meilleur ask
3. Merge les paires YES+NO en USDC.e via le contrat CTF

**Profit** = `$1.00 - cout(YES) - cout(NO) - gas`

**Exemple** : ask YES = $0.48, ask NO = $0.50 → cout total = $0.98 → profit = $0.02 - $0.005 = $0.015 par share (1.5%)

### Split-Sell

Quand `best_bid(YES) + best_bid(NO) > $1.00`, le bot :
1. Split USDC.e en paires YES+NO via le contrat CTF (cout: $1.00 par paire)
2. Vend les tokens YES au meilleur bid
3. Vend les tokens NO au meilleur bid

**Profit** = `revenu(YES) + revenu(NO) - $1.00 - gas`

**Exemple** : bid YES = $0.53, bid NO = $0.49 → revenu total = $1.02 → profit = $0.02 - $0.005 = $0.015 par share (1.5%)

### Conditions d'execution

| Condition | Seuil par defaut |
|-----------|-----------------|
| Arbitrage active | `MM_ARB_ENABLED=true` |
| Profit minimum apres gas | >= 0.5% du notionnel |
| Taille minimum | 5 shares |
| Taille maximum par arb | $50 |
| Frequence du scan | toutes les ~30s (3 cycles MM) |
| Gas estime | $0.005 par tx |

### Tracking

Les fills d'arbitrage sont enregistres dans la table `mm_fills` avec `side="ARB"`, ce qui permet de les distinguer des fills classiques (BID/ASK) dans les metriques.

---

## 3. Gestion du Risque

### Controles globaux

| Mecanisme | Seuil | Action |
|-----------|-------|--------|
| **Stop-loss journalier** | perte >= 20% du portefeuille | Pause totale |
| **Drawdown depuis le peak** | >= 25% depuis le high-water mark | Pause totale |
| **Exposition globale** | MM + CD >= 75% du portefeuille total | Bloque nouveaux trades |
| **Volume CD journalier** | >= 50% du solde disponible | Bloque nouveaux trades CD |

### Controles MM specifiques

| Mecanisme | Seuil | Action |
|-----------|-------|--------|
| **DD intraday — reduce** | >= 15% | Reduce exposure de 50% |
| **DD intraday — kill** | >= 25% | Annule tous les ordres, pause |
| **Auto-recovery** | DD < 20% + cooldown 30min | Reprise auto (max 3/jour) |
| **Claude Guard** | Resolution traps, catalyseurs | Kill du marche concerne |
| **Cross-reject cooldown** | 3 rejets consecutifs | Cooldown 5-10min par marche |

### Controles CD specifiques

| Mecanisme | Seuil | Action |
|-----------|-------|--------|
| **Positions simultanees** | max 5 | Bloque nouvelles entrees |
| **Edge minimum** | 5 pts | Pas de trade en dessous |
| **Confirmation** | 2 cycles (30 min) | Edge doit persister |
| **Taille max par position** | 5% du portefeuille | Cap Kelly |
| **Pre-trade IA (active par defaut)** | Haiku validation enrichie (portfolio, vol regime, spot distance) | Rejet si signal juge bruit ; reponse stockee dans cd_signals |

### Source de verite

Le **solde on-chain USDC.e** est la source de verite pour tout le capital. Aucun budget n'est hardcode — le bot utilise 100% du solde disponible, avec les controles de risque en pourcentages pour limiter l'exposition.

---

## 4. Utilisation de l'IA

Le bot utilise Claude a 6 niveaux differents :

| Usage | Modele | Frequence | Role |
|-------|--------|-----------|------|
| Guard MM | Opus | 1x / 5 min | Detection resolution traps + catalyseurs |
| Scorer MM | Sonnet | 1x / refresh scanner | Scoring qualitatif des marches candidats |
| NL Parsing CD | Sonnet | 1x / cycle CD (15 min) | Extraction parametres marches crypto |
| Exit confirm CD | Haiku | Sur edge reversal uniquement | Confirmation sortie (fondamental vs bruit) |
| Pre-trade CD | Haiku | Active par defaut, chaque signal | Validation pre-trade enrichie (portfolio, vol regime, spot distance) |
| Analyse CD | Opus | 4x / jour (toutes les 6h) | Revue post-trade, scores, suggestions |

---

## 5. Parametres cles (valeurs par defaut)

### Market-Making

| Parametre | Defaut | Variable `.env` |
|-----------|--------|----------------|
| Cycle | 10s | `MM_CYCLE_SECONDS` |
| Marches simultanes max | 10 | `MM_MAX_MARKETS` |
| Spread minimum | 3.0 pts | `MM_MIN_SPREAD_PTS` |
| Demi-spread min/max | 1.5 / 8.0 pts | `MM_DELTA_MIN` / `MM_DELTA_MAX` |
| Taille quote | $5.00 | `MM_QUOTE_SIZE_USD` |
| Facteur skew inventaire | 0.5 | `MM_INVENTORY_SKEW_FACTOR` |
| Seuil requote | 0.5 pts | `MM_REQUOTE_THRESHOLD` |
| Depth minimum | $500 | `MM_MIN_DEPTH_USD` |
| Kill switch DD | 25% | `MM_DD_KILL_PCT` |
| Resume DD | 20% | `MM_DD_RESUME_PCT` |
| Cooldown kill | 30 min | `MM_DD_COOLDOWN_MINUTES` |
| Arbitrage active | false | `MM_ARB_ENABLED` |
| Arb profit minimum | 0.5% | `MM_ARB_MIN_PROFIT_PCT` |
| Arb taille max | $50 | `MM_ARB_MAX_SIZE_USD` |
| Arb cout gas estime | $0.005 | `MM_ARB_GAS_COST_USD` |

### Crypto Directional

| Parametre | Defaut | Variable `.env` |
|-----------|--------|----------------|
| Cycle | 15 min | `CD_CYCLE_MINUTES` |
| Edge minimum | 5.0 pts | `CD_MIN_EDGE_PTS` |
| Cycles de confirmation | 2 | `CD_CONFIRMATION_CYCLES` |
| Fraction Kelly | 0.25 | `CD_KELLY_FRACTION` |
| Taille max position | 5% | `CD_MAX_POSITION_PCT` |
| Nu (Student-t) | 6.0 | `CD_STUDENT_T_NU` |
| EWMA span | 30 jours | `CD_EWMA_SPAN` |
| Positions simultanees max | 5 | `CD_MAX_CONCURRENT_POSITIONS` |
| Stop-loss | 15 pts | `CD_EXIT_STOP_LOSS_PTS` |
| Take-profit | 20 pts | `CD_EXIT_TAKE_PROFIT_PTS` |
| Edge reversal | -3 pts | `CD_EXIT_EDGE_REVERSAL_PTS` |
| Check exit | 120s | `CD_EXIT_CHECK_SECONDS` |
| Exposition globale max | 75% | `MAX_TOTAL_EXPOSURE_PCT` |
| Pre-trade IA | true | `CD_PRETRADE_AI_ENABLED` |
