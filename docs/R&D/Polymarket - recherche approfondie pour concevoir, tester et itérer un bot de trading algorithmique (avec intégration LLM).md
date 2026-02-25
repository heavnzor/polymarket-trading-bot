# Polymarket : recherche approfondie pour concevoir, tester et itérer un bot de trading algorithmique (avec intégration LLM)

## Synthèse exécutive

Polymarket est aujourd’hui construit autour d’un **CLOB (Central Limit Order Book)** hybride : **matching off-chain** opéré par un “operator” et **settlement on-chain** via un contrat d’échange, avec des ordres **EIP-712 signés** et une **annulation possible on-chain** (filet de sécurité si l’API est indisponible). citeturn6view1turn11view0turn10search8 L’infrastructure “builder” inclut des APIs publiques (Gamma, Data) et une API de trading (CLOB) avec authentification en 2 niveaux (L1 / L2). citeturn10search12turn4view2turn11view0

Le cœur de l’avantage (“edge”) sur un marché de prédiction comme Polymarket provient moins de “bonnes opinions” que de : (1) **microstructure** (spreads, profondeur, latence, rebates/récompenses, risques d’anti-sélection), (2) **mécaniques de tokens** (CTF ERC-1155, split/merge/redeem, negative risk), (3) **qualité du pipeline de décision** (probabilités calibrées + exécution robuste), (4) **gestion du risque** et discipline de test (backtesting rigoureux, contrôle de sur-optimisation). citeturn7search2turn7search25turn6view0turn23search11turn25search0

Points critiques avant même de parler “stratégie gagnante” :
- **Conformité / géoblocage** : à date (doc officielle), **la France est “Blocked”** pour la **prise d’ordres** (l’accès aux données reste possible). Il existe un endpoint de vérification (`/api/geoblock`) et une liste officielle des pays/régions bloqués. citeturn9view0turn9view1  
- **Robustesse opérationnelle** : heartbeats (sinon annulation automatique des ordres), redémarrages hebdomadaires du matching engine (HTTP 425), limites de rate limiting (Cloudflare). citeturn7search7turn11view2turn3view1turn3view4  
- **Risque de résolution/oracle** : UMA Optimistic Oracle, phases de contestation, escalade DVM ; les règles de résolution priment toujours sur l’intuition. citeturn6view2

Éléments non spécifiés par votre demande (donc non optimisés dans ce rapport) : **capital cible**, **tolérance au risque**, **juridictions autorisées** (y compris lieu d’exécution réel), et **capacités exactes du LLM** (latence, tool-use, contexte, fiabilité). citeturn9view0turn9view1

Recommandation par défaut (pragmatique) pour itérer vite : commencer par un bot **microstructure-first** (market making prudent + contrôle d’inventaire + “kill switch”) sur un petit univers de marchés liquides, instrumenté de bout en bout, puis ajouter une couche de **probabilités** (modèles quant + LLM en support) seulement après avoir stabilisé l’exécution, la collecte de données et le backtest. citeturn15view0turn11view2turn25search1

## APIs officielles, authentification, formats et limites

### Cartographie des APIs et endpoints de base

Polymarket expose **plusieurs services** (base URLs) avec périmètres distincts :
- **Gamma API** (découverte : events/markets/tags/sports…) : `https://gamma-api.polymarket.com` citeturn10search12turn16search1  
- **Data API** (positions, trades, open interest, analytics, leaderboards…) : `https://data-api.polymarket.com` citeturn10search12turn10search11  
- **CLOB API** (orderbooks, pricing, spreads, historique, et trading) : `https://clob.polymarket.com` citeturn10search12turn13view2  
- **Bridge API** (dépôts/retraits cross-chain → conversion vers USDC.e sur Polygon) : `https://bridge.polymarket.com` (documenté comme **proxy de fun.xyz**, donc dépendance externe). citeturn10search12turn7search5  
- **Geoblock** (éligibilité IP) : `GET https://polymarket.com/api/geoblock` (sur le domaine polymarket.com, distinct des API servers). citeturn9view0  

Les endpoints “market data” de haut niveau typiques :
- Gamma : `GET /events`, `GET /markets`, `GET /public-search`, `GET /tags`, etc. citeturn16search1turn10search11  
- CLOB (lecture) : `GET /book`, `POST /books`, `GET /price(s)`, `GET /midpoint`, `GET /spread`, `GET /prices-history`. citeturn10search11turn13view2  
- Data : `GET /positions?user=…`, `GET /trades`, `GET /oi`, etc. citeturn10search11turn12search5  

### Authentification : modèle L1/L2 et implications bot

L’API CLOB utilise un schéma en **deux niveaux** :  
- **L1** : signature EIP-712 (preuve de contrôle du wallet), utilisée pour **créer/dériver** des credentials API. citeturn6view1turn4view0  
  - `POST https://clob.polymarket.com/auth/api-key`  
  - `GET  https://clob.polymarket.com/auth/derive-api-key` citeturn4view0  
  - Headers L1 requis : `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_NONCE` (nonce défaut 0). citeturn4view0turn4view2  
- **L2** : signature HMAC-SHA256 (avec `secret`) + headers `POLY_API_KEY` + `POLY_PASSPHRASE`, utilisée pour les endpoints trading (orders/cancels/heartbeat…). citeturn4view2turn19search6  

Point souvent mal compris : **même avec L2**, la création d’un ordre implique encore la **signature EIP-712 de l’ordre** (l’auth L2 authentifie la requête API, pas l’autorisation on-chain de trader). citeturn19search6turn11view0  

Conséquence architecture : les secrets (`PRIVATE_KEY`, `secret`) ne doivent **jamais** être exposés côté client ; la doc recommande explicitement d’implémenter la signature côté serveur et de ne pas commiter les clés. citeturn4view0turn19search6  

### Rate limiting, throttling et erreurs attendues

Les limites sont décrites comme appliquées via entity["company","Cloudflare","edge network security"] : en cas de dépassement, les requêtes sont **throttled (retardées/queued)** plutôt que rejetées immédiatement, avec fenêtres glissantes. citeturn3view1turn8search16  

Quelques limites importantes (extraits) :
- Global : 15 000 req / 10s ; `/ok` 100 req / 10s. citeturn3view1  
- CLOB market data : `/books` 300 / 10s, `/book` 600 / 10s, `/price` 100 / 10s, `/prices` 75 / 10s, etc. citeturn3view2turn3view3  
- Trading (`/order`) : limites “sustained” et “burst”, ex. 3 000 req / 10min (sustained) et 500 / 10s (burst), plus 100 / 10s (throttle) ; batch `/orders` plus bas. citeturn3view4  

Côté résilience, les docs prévoient des modes/erreurs :  
- **429** “Too Many Requests” → backoff exponentiel. citeturn19search25turn11view2  
- **425** pendant redémarrage du matching engine (cf. section microstructure). citeturn11view2  

### WebSockets : flux temps réel essentiels pour bots

Polymarket propose 4 channels WebSocket (dont un RTDS séparé) :  
- Market : `wss://ws-subscriptions-clob.polymarket.com/ws/market` (no auth)  
- User : `wss://ws-subscriptions-clob.polymarket.com/ws/user` (auth)  
- Sports : `wss://sports-api.polymarket.com/ws` (no auth)  
- RTDS : `wss://ws-live-data.polymarket.com` (auth optionnel, selon flux) citeturn5view3turn11view3  

Événements clés sur le “market channel” : `book`, `price_change`, `last_trade_price`, `tick_size_change`, `best_bid_ask` (option “custom_feature_enabled”). citeturn5view2turn5view3  

RTDS fournit notamment des **prix crypto** en temps réel depuis entity["company","Binance","crypto exchange"] et entity["company","Chainlink","oracle network"], sans authentification, ainsi que des streams de commentaires. citeturn10search21turn11view3  

### Formats : snapshot order book et historique de prix

Snapshot order book (CLOB `GET /book`) renvoie typiquement : `bids[{price,size}]`, `asks[...]`, `min_order_size`, `tick_size`, `neg_risk`, `last_trade_price`, etc. citeturn5view1  

Historique de prix (`GET /prices-history`) supporte `startTs`, `endTs`, `interval` (ex. `1m`, `1h`, `1d`, `1w`, `max`, `all`) et renvoie une série `{t,p}`. citeturn13view2  

Pour backtests et analytics on-chain, Polymarket publie aussi des **subgraphs GraphQL** (hébergés par entity["company","Goldsky","graphql hosting provider"]) avec plusieurs endpoints (positions, orderbook, activity, open interest, PNL) et un exemple cURL. citeturn13view0turn16search15  

## Microstructure, types d’ordres, frais, règlement-livraison et oracles

### Actifs, collateral et mécanique CTF

Les positions Polymarket sont tokenisées en **ERC-1155** sur entity["company","Polygon","l2 blockchain"] via la **Conditional Token Framework (CTF)** de entity["organization","Gnosis","web3 organization"]. citeturn7search6turn7search2 Chaque marché binaire crée 2 tokens (“Yes” / “No”) et chaque paire est décrite comme **entièrement collatéralisée** par 1 $ de **USDC.e** verrouillé dans le contrat CTF. citeturn7search2turn7search6turn7search5  

Le collateral de trading est documenté comme **USDC.e sur Polygon**, et la Bridge API convertit des dépôts multi-chaînes vers USDC.e sur Polygon. citeturn7search5turn7search9  

### CLOB hybride : matching off-chain, settlement on-chain, non-custodial

Le CLOB est décrit comme **hybride-décentralisé** : l’operator matche les ordres off-chain et les trades se règlent on-chain (contrat d’échange audité par entity["organization","Chainsecurity","blockchain security auditor"]), avec conservation de la garde (“non-custodial”). citeturn6view1turn11view0 Les ordres sont des messages signés EIP-712, autorisant l’exécution on-chain sans transfert de custody. citeturn11view0turn6view1  

### Ordres, statuts, annulation et pièges “bot”

- Tous les ordres sont des **limit orders** ; un “market order” est un limit à prix “marketable” qui exécute immédiatement contre le meilleur disponible. citeturn5view0turn11view0  
- Types : GTC, GTD (resting), FOK, FAK (exécution immédiate, tout ou partiel). citeturn5view0turn11view0  
- Post-only : si l’ordre croise le spread, il est **rejeté** (garantit “maker”). citeturn11view0turn10search13  
- Annulation : via API instantanée, ou **fallback on-chain** via le contrat d’échange si l’API est indisponible. citeturn11view0  

Un aspect microstructure très concret : **price improvement** bénéficie au taker (si vous achetez à 0,55 et que ça match une ask à 0,52, vous payez 0,52). citeturn11view0  

### Tick sizes, “tick_size_change” et rejets d’ordres

L’incrément minimal de prix (“tick size”) est exposé dans l’orderbook (`tick_size`) et des events WebSocket `tick_size_change` peuvent se produire (déclencheur : prix > 0,96 ou < 0,04). citeturn5view1turn5view2 La doc insiste : si vous continuez à poster avec un ancien tick size, vos ordres seront rejetés. citeturn5view2  

### Heartbeats et redémarrages du matching engine

Deux mécanismes doivent être modélisés explicitement dans un bot :
- **Heartbeat** : si un heartbeat valide n’est pas reçu dans ~10s (avec buffer ~5s), tous les ordres ouverts sont annulés. citeturn7search7turn7search3  
- **Redémarrage weekly** : redémarrage annoncé comme hebdomadaire le lundi 20:00 ET (~90s) ; pendant la fenêtre, l’API renvoie **HTTP 425** sur les endpoints liés aux ordres ; stratégie recommandée : backoff exponentiel. citeturn11view2  

### Frais, rebates et programmes “maker”

Polymarket documente que la majorité des marchés ont **0 frais**, avec une exception structurée : certains marchés (ex. “15-minute crypto”) ont une courbe de taker fees. citeturn2view3turn15view2 Pour le cas “15-minute crypto”, le fee rate est décrit par une formule dépendante du prix : `fee_rate(p)=0.125% / sqrt(p*(1-p))`, avec un pourcentage effectif maximal au voisinage de 0,5. citeturn2view3  

Programme “Maker Rebates” : ces taker fees financent des rebates payés quotidiennement en USDC aux makers dans des marchés éligibles (15-min crypto, 5-min crypto, certains sports), selon une formule “fee-curve weighted”. citeturn15view2turn14search24  

Programme “Liquidity Rewards” : récompenses journalières à minuit UTC aux addresses makers, avec une méthodologie inspirée de entity["organization","dYdX","decentralized exchange"], visant à encourager du quoting passif équilibré proche du midpoint et à décourager des comportements “exploitative”. citeturn15view1  

### Résolution et oracles

La résolution est décrite comme s’appuyant sur entity["organization","UMA","oracle protocol"] (Optimistic Oracle) : n’importe qui peut proposer une issue, n’importe qui peut contester, avec dépôt d’un bond (souvent indiqué ~750 USDC.e), période de challenge (~2 heures), puis escalade possible vers la DVM (vote des détenteurs UMA) si disputes répétées. citeturn6view2turn16search13 C’est une source majeure de risque “non-prix” : lire et encoder les **resolution rules** fait partie de l’edge (et de la protection). citeturn6view2  

### AMM vs order book : réalité Polymarket

Le trading “core” exposé aux builders est largement orienté **order book (CLOB)**. citeturn6view1turn10search11 Néanmoins, les objets “Market” exposent un champ `fpmm` (adresse d’un **Fixed Product Market Maker**), et les subgraphs incluent des données `fpmm/fpmms`, ce qui indique la présence (historique ou parallèle) de composants AMM dans l’écosystème on-chain. citeturn18view0turn13view1turn16search15  

D’un point de vue microstructure, la différence classique est :  
- **AMM** : fournit une contrepartie instantanée mais impose un coût de **slippage** et nécessite des LPs/mécaniques d’incitation. citeturn16search2  
- **Order book** : meilleure efficience quand la liquidité est profonde, mais risque de spreads larges et de trous de carnet en faible liquidité. citeturn11view1turn23search16  

## Mécanique de prix et inférence de probabilités

### Prix ↔ probabilité implicite

Dans Gamma, chaque marché a des tableaux `outcomes` et `outcomePrices`, et la doc explicite que les prix représentent des **probabilités implicites** (ex. Yes=0,20 ↔ 20%). citeturn16search1  

La découverte de prix initiale est décrite via la complémentarité Yes/No : un premier prix “émerge” quand quelqu’un poste un buy Yes à p, un buy No à (1-p), et qu’ils matchent ; 1 $ est converti en 1 token Yes + 1 token No distribués aux acheteurs. citeturn11view1  

### Pourquoi “price ≈ probability” est une approximation

La littérature économique sur les marchés de prédiction soutient que, sous certaines hypothèses, ces marchés agrègent l’information et les prix se comportent comme des prévisions probabilistes. citeturn23search11 Mais dans un carnet d’ordres réel : spread, asymétries d’information, coûts d’exécution et contraintes de liquidité font que **le midpoint** est souvent un meilleur estimateur que le last-trade pour piloter un bot, et qu’une stratégie “à faible edge” peut être mangée par friction et adverse selection. citeturn11view0turn15view0turn23search4  

### Unités “probabilité” vs “log-odds” (utile pour market making)

Pour des contrats binaires, travailler dans l’espace **logit / log-odds** (x = ln(p/(1-p))) peut stabiliser certaines dynamiques (notamment lorsque p est proche de 0 ou 1). Un papier récent sur le market making en “event contracts” propose explicitement une adaptation type Avellaneda–Stoikov **en unités logit**, ce qui est conceptuellement aligné avec le fait qu’un contrat binaire ressemble à une option numérique. citeturn14search3turn23search4  

### Évaluer vos probabilités (et votre LLM) : scoring rules et calibration

Si votre bot produit une probabilité p̂, vous devez mesurer la qualité probabiliste avec des métriques propres :
- **Brier Score** (moyenne de (p̂ − y)²) — référence historique. citeturn24search0  
- **Log score / log loss** et, plus largement, **strictly proper scoring rules** (encouragent la “vraie croyance” en espérance). citeturn24search1  
- **Reliability diagram / calibration** : indispensable si votre edge dépend de petits écarts (ex. p_true − p_mkt = 2–3%). citeturn24search29turn24search1  

En pratique, cela implique de labelliser sur des marchés **résolus**, et de segmenter par catégories (sports, crypto court terme, politique…) car les régimes de liquidité et la vitesse d’incorporation de l’info diffèrent fortement. citeturn6view2turn16search1turn15view3  

## Stratégies potentiellement rentables, pseudocode et arbitrages mécaniques

### Tableau comparatif : trade-offs (latence, capital, complexité, edge attendu)

| Stratégie | Source d’edge principale | Sensibilité latence | Capital requis | Complexité | Edge attendu (qualitatif) | Risques dominants |
|---|---|---:|---:|---:|---|---|
| Market making “prudente” (2-sided, inventory skew) | Capture du spread + incentives (rewards/rebates) | Moyenne→haute (WS) | Moyen | Élevée | Faible→moyen (stable si bien calibré) | Adverse selection, inventory, outages, ticks/heartbeat citeturn15view0turn15view1turn11view2turn23search4 |
| “Incentives harvesting” (marchés éligibles fee/rebate) | Rebates makers + scoring liquidity rewards | Moyenne | Moyen | Moyenne→élevée | Faible→moyen (dépend programme) | Changements de règles/coefficients, spread négatif, abuse detection citeturn15view2turn15view1turn15view3 |
| Arbitrage mécanique split/merge (Yes+No vs 1 USDC.e) | Incohérences carnet / frictions temporaires | Moyenne | Moyen | Moyenne | Faible (rare) mais “propre” | Frais/gas, profondeur insuffisante, exécution partielle citeturn11view1turn7search25turn7search2 |
| Trading “probability model” (stat + LLM) | Écarts p̂ vs prix | Faible→moyenne | Variable | Élevée | Moyen (si modèle calibré) | Overfitting, drift, prompt injection, faux signaux citeturn23search11turn24search1turn26search0 |
| Event-driven (news/polls/annonces) | Vitesse d’interprétation + exécution | Haute | Variable | Élevée | Moyen→élevé sur événements rapides | News latency, slippage, rumeurs, risques éthiques/insider citeturn23search11turn11view1 |
| Hedging multi-marchés / negative risk conversion | Réduction variance + arbitrage de structure | Moyenne | Élevé | Très élevée | Faible→moyen (profil risque amélioré) | Corrélations instables, complexité on-chain, erreurs de conversion citeturn6view0turn7search2turn23search4 |

### Stratégie A : market making à la Avellaneda–Stoikov (adaptée contrats binaires)

Base théorique : Avellaneda–Stoikov modélise des arrivées d’ordres et ajuste un **reservation price** et un **optimal spread** en fonction de l’aversion au risque et de l’inventaire. citeturn23search4 Un travail récent transpose l’idée en “logit space” pour des event contracts (plus naturel quand p→0/1). citeturn14search3 Les docs Polymarket recommandent explicitement : quoting des deux côtés, skew sur inventaire, cancel stale quotes, usage du WebSocket, batch orders, et kill switch (`cancelAll()`). citeturn15view0turn15view1  

Pseudocode (structure, pas “code de prod”) :

```pseudo
loop every Δt (e.g., 500ms–2s):
  ob = get_top_of_book(token_id)         # via WS best_bid_ask or book/price_change
  tick = current_tick_size(token_id)     # update on tick_size_change events
  mid  = (ob.best_bid + ob.best_ask)/2
  inv  = current_inventory(token_id)     # shares YES (or NO) held + open orders reserved
  vol  = est_short_term_volatility(mid_series, window=W)

  # Reservation price (inventory skew)
  # simple form: r = mid - k_inv * inv
  r = mid - k_inv * inv

  # Target half-spread (must cover expected adverse selection + fees + rebates uncertainty)
  half_spread = max(min_half_spread, k_vol * vol, fee_buffer(mid))

  bid = round_down(r - half_spread, tick)
  ask = round_up  (r + half_spread, tick)

  # Safety: avoid crossed market (negative spread)
  if bid >= ask: 
      cancel_all_orders(token_id)
      continue

  # Post-only maker quotes
  post_or_replace_quotes(token_id, bid, ask, size=quote_size(mid, depth), postOnly=true)

  # Operational: heartbeat, 425 handling, and fail-safe
  send_heartbeat()
  if any_error in {425, 429, auth_error, websocket_lag}:
      cancel_all_orders()
      backoff_and_recover()
```

Paramètres raisonnables pour démarrer (à calibrer sur données Polymarket) :
- Δt (re-quote): 0,5–2s (plus rapide si carnet bouge). citeturn15view0turn5view3  
- `min_half_spread`: au moins 2–6 ticks (selon marché), et toujours **>= coût de friction** (fees + adverse selection). citeturn5view1turn15view3turn23search4  
- `k_inv`: choisi pour saturer l’inventaire (ex. à 20–30% du capital alloué, vous décalez fortement les quotes pour réduire le risque). citeturn15view0turn23search4  
- `postOnly=true`: évite de devenir taker involontairement. citeturn10search13  

### Stratégie B : arbitrage mécanique “split/merge” (structure CTF)

Deux opérations on-chain 100% structurelles :
- **Split** : convertir 1 USDC.e en 1 Yes + 1 No (implicitement via la mécanique CTF décrite). citeturn11view1turn7search2  
- **Merge** : convertir 1 Yes + 1 No en 1 USDC.e. citeturn7search25  

Donc, un arbitrage “mécanique” existe si vous parvenez à acheter (Yes+No) **en dessous de 1** (après frictions), ou à vendre (Yes+No) **au-dessus de 1**, en utilisant split/merge comme ancre. Le fait que le carnet puisse présenter des inefficiences temporaires est cohérent avec la recommandation officielle de vérifier la profondeur avant de trader en taille. citeturn11view1turn11view0  

Pseudocode (version “coût complet”) :

```pseudo
for each market condition:
  yes_ob = top_of_book(yes_token)
  no_ob  = top_of_book(no_token)

  # Buy both sides cheaply -> merge -> 1 USDC.e
  cost_buy_pair = yes_ob.best_ask + no_ob.best_ask + fees_and_slippage_estimate()
  if cost_buy_pair < 1 - safety_margin:
      buy_yes_at_ask(size=S)
      buy_no_at_ask(size=S)
      when both filled:
          merge_yes_no_to_usdce(size=S)   # on-chain CTF merge
      record_profit(1 - cost_buy_pair)

  # Sell both overpriced -> split USDC.e -> sell tokens
  revenue_sell_pair = yes_ob.best_bid + no_ob.best_bid - fees_and_slippage_estimate()
  if revenue_sell_pair > 1 + safety_margin:
      split_usdce_to_yes_no(size=S)       # obtain full set
      sell_yes_at_bid(size=S)
      sell_no_at_bid(size=S)
      record_profit(revenue_sell_pair - 1)
```

Si vous êtes en **wallet gasless** (relayer), Polymarket documente une infra relayer (avec conditions de programme builder) qui peut couvrir des opérations CTF (split/merge/redeem). citeturn7search4turn7search16  

### Stratégie C : trading “probabilités” (stat + LLM en support, pas en pilote)

Les marchés de prédiction peuvent être utilisés comme agrégateurs d’info, mais un bot peut viser les écarts p̂ − p_mkt, **à condition** d’avoir un estimateur **calibré** et de passer un protocole de validation strict pour éviter la sur-optimisation. citeturn23search11turn24search1turn25search0  

Une règle simple (binaire) :
- Si acheter Yes au prix p implique un payoff attendu (p̂ − p) supérieur aux frictions → trade, sinon abstention.

Pseudocode (avec seuils) :

```pseudo
p_mkt = midpoint_price(yes_token)
p_hat, conf = model_probability(market_context_features)

edge = p_hat - p_mkt
cost = expected_fees(p_mkt) + expected_slippage + risk_buffer(conf)

if edge > cost:
    place_buy_yes(limit_price = p_mkt + aggressiveness_ticks*tick, size = f(edge, conf))
elif edge < -cost:
    place_buy_no( ... ) or sell_yes(...)
else:
    do_nothing
```

Évaluation : Brier/log loss + calibration (reliability) sur marchés résolus, segmentés par catégorie, puis conversion edge → PnL simulée en prenant un modèle d’exécution réaliste (latence, partial fills). citeturn24search0turn24search1turn25search2  

### Stratégie D : negative risk (multi-outcome) et conversion

Dans des événements multi-issues “neg risk”, un No sur une issue peut être convertible en Yes sur toutes les autres issues via un “Neg Risk Adapter”; l’événement expose `negRisk: true` et les ordres doivent embarquer `negRisk: true`. citeturn6view0turn5view1 Cela ouvre des constructions de hedges et d’arbitrages plus complexes, mais la complexité opérationnelle et les risques d’erreur (mauvais flag, mauvais token set) montent fortement. citeturn6view0turn5view2  

## Architecture bot + LLM, contrôles de risque, sécurité et exploitation

### Architecture recommandée (séparer décision, risque et exécution)

Le principe (production-first) : l’LLM ne doit **jamais** être un “direct order writer” sans garde-fous. Les docs Polymarket encouragent déjà des contrôles (size limits, price guards, kill switch, WS user channel pour fills). citeturn15view0turn15view1  

Diagramme (vue services) :

```mermaid
flowchart LR
  A[Gamma API: markets/events/rules] --> F[Feature Store]
  B[CLOB WebSocket: book/price_change/last_trade] --> F
  C[Data API: trades/positions/oi] --> F
  D[Subgraphs (GraphQL): onchain activity] --> F

  F --> Q[Quant Model: p_hat + uncertainty]
  F --> L[LLM: extraction/raisonnement sur règles & news]

  Q --> G[Decision & Risk Gate]
  L --> G

  G --> O[Order Manager]
  O -->|L1/L2 auth + EIP712| X[CLOB REST: /order /cancel /heartbeat]
  O -->|monitor| M[User WS: fills & order state]

  G --> K[Kill Switch: cancelAll]
  X --> K
  M --> K

  O --> S[Logs/Metrics/Alerts]
  G --> S
```

Sources de données officielles (composants A/B/C + endpoints) : séparation Gamma/Data/CLOB + WS + subgraphs. citeturn10search12turn5view3turn13view0  

### Ingestion temps réel et latence

Polymarket recommande le **WebSocket** pour data low-latency plutôt que du polling REST, et propose `postOrders()` pour batcher. citeturn15view0turn5view3 Le help center mentionne une infra “Primary Servers: eu-west-2” et “Closest Non-Georestricted Region: eu-west-1” (utile pour décider où déployer si vous êtes dans une juridiction autorisée). citeturn9view1  

### LLM : rôle utile, mais borné (et sécurisé)

Un schéma robuste est un LLM “analyst” qui produit :
- un **p̂** (probabilité) + intervalle d’incertitude,  
- une **liste structurée** des hypothèses,  
- et une **explication** pouvant être auditée,  
sans jamais émettre d’ordres directement.

Le paradigme “Reason + Act” (ReAct) décrit comment intercaler raisonnement et actions/outils (RAG, queries), ce qui est pertinent pour un agent qui doit récupérer des preuves (rules, données) avant d’actualiser p̂. citeturn26search1turn26search17  

Sécurité LLM indispensable : OWASP Top 10 LLM (v1.1) met en avant prompt injection, insecure output handling, data poisoning, DoS, supply chain. C’est directement applicable à un bot qui consomme news/comments et agit sur des APIs. citeturn26search0turn26search2  

Minimal prompt “safe-by-design” (structure, pas magique) :
- Input figé et minimal : question, rules, timestamps, features numériques, contraintes “do not trade if…”, et demande de sortie JSON strict.  
- Output validé (schema JSON), bornes [0,1], et **gating** par un moteur de règles/risk manager avant exécution. citeturn26search0turn19search6  

### Contrôles de risque et “garde-fous” obligatoires

Les docs “Market Makers” listent explicitement :
- size limits (balance/allowance),  
- price guards (valider vs midpoint),  
- kill switch `cancelAll()`,  
- monitoring temps réel via user channel. citeturn15view0turn15view1  

À ajouter côté bot (standard en algo) :
- limites d’inventaire par marché + global,  
- limites de drawdown et de perte journalière,  
- limitation du “leverage implicite” (positions corrélées),  
- désactivation automatique sur événements système (425 répétés, WS lag, tick change non traité). citeturn11view2turn5view2turn23search4  

Pour le sizing, le **critère de Kelly** (maximisation du taux de croissance log) donne une base théorique, mais il est notoirement agressif si p̂ est incertain ; en pratique, “fractional Kelly” est courant. citeturn23search1turn23search36  

### Opérations et sécurité “API keys + wallets”

Bonnes pratiques explicitement documentées côté Polymarket : ne jamais commiter la clé privée, utiliser variables d’environnement / KMS, et ne pas exposer les secrets côté client (HMAC L2 côté backend). citeturn4view0turn19search6  

Sur la supply chain : l’écosystème crypto voit régulièrement des packages malveillants ciblant des wallets/keys ; cela doit vous pousser vers (1) allowlist stricte des dépendances, (2) vérification provenance (repos officiels), (3) exécution en environnement isolé. citeturn19search14turn26search0  

## Backtesting, métriques, simulation et feuille de route d’expérimentation

### Données et labellisation

Sources de données exploitables pour backtests :
- Historique de prix CLOB (`/prices-history` + granularités) pour signaux et “mid/last”. citeturn13view2  
- Trades / positions / OI via Data API (`/trades`, `/positions`, `/oi`). citeturn10search11turn12search5  
- Reconstitution microstructure (plus fine) via WebSocket (book/price_change/last_trade_price) et/ou subgraphs orderbook. citeturn5view3turn13view0  
- Labels “vérité” via status de résolution et outcome gagnant (mécanisme UMA) ; attention aux timelines de dispute (le label peut arriver tard). citeturn6view2  

### Simulation d’exécution (ne pas “backtester un mid”)

Pour des stratégies de carnet, un backtest crédible doit simuler :
- fill probabilités (ou replay d’événements si vous stockez le flux WS),  
- partial fills, order cancels,  
- tick_size_change,  
- heartbeats (annulation si heartbeat manquant),  
- maintenance 425 (downtime),  
- fees/rebates (variables par marché). citeturn5view2turn7search7turn11view2turn2view3turn15view2  

### Métriques “trading” et “forecasting”

Trading :
- courbe d’equity, PnL réalisé / non-réalisé,  
- max drawdown, VaR/ES (si vous modélisez),  
- turnover, fill rate, adverse selection proxy,  
- Sharpe/Sortino (avec prudence). citeturn23search2turn25search1  

Forecasting :
- Brier + log loss (strictly proper),  
- calibration (ECE + reliability diagram),  
- slicing par catégories et par régime de liquidité. citeturn24search0turn24search1turn24search29  

### Contrôle de sur-optimisation et validation robuste

Les backtests en finance sont particulièrement sujets à la “sélection du meilleur” et au data snooping. Des travaux de Bailey & López de Prado proposent des outils comme **Deflated Sharpe Ratio** et **Probability of Backtest Overfitting (PBO)** via CSCV, et Hal White formalise un “Reality Check” contre le data snooping. citeturn25search1turn25search0turn25search2  

Conséquence pratique : si vous essayez 200 variantes de paramètres, la probabilité que votre “gagnant” soit un artefact est élevée — donc versionner les expériences, geler les datasets, et imposer des validations out-of-sample strictes. citeturn25search0turn25search2  

### Roadmap priorisée (itération rapide, risques maîtrisés)

**Phase fondation (semaine 1)**  
- Implémenter un collecteur WS (market + user) + persistance (orderbook diffs, trades, best bid/ask, tick size). citeturn5view3turn5view2  
- Ajouter “operational safety” : gestion 425 (retry/backoff), 429 (backoff), heartbeats, kill switch, et circuit breaker sur WS lag. citeturn11view2turn19search25turn7search7turn15view0  
- Implémenter **geoblock check** (fail fast) et bloquer toute exécution si IP non éligible. citeturn9view0turn9view1  

**Phase stratégie minimale (semaine 2)**  
- Déployer un market maker “starter” sur 1–3 marchés, avec quote size petite, post-only, spread conservateur, inventory cap, et logs exhaustifs. citeturn15view0turn11view0  
- Construire un backtest d’exécution simplifié (top-of-book) + stress test (slippage multiplié, latence +1–2s, downtime 425). citeturn11view2turn13view2  
- Ajouter un module “LLM analyst” uniquement pour produire p̂ + justification structurée, puis **gating** strict (pas d’ordres automatiques si incertitude trop haute). citeturn26search1turn26search0  

Critères de réussite (concrets) :
- 0 incident “crossed quotes” (bid≥ask) et 0 ordre rejeté pour tick size périmé sur une période de test. citeturn5view2turn15view3  
- Backtest reproductible (seed + dataset figé) + validation out-of-sample + reporting (PnL, drawdown, fill rate). citeturn25search2turn25search0  
- Observabilité : corrélation entre fills et paramètres (spread, volatility proxy), et alerting sur 425/heartbeat. citeturn11view2turn7search7  

### Gabarit “charts” (à brancher sur vos résultats)

Exemples de graphiques à produire systématiquement : equity curve, drawdown, distribution de returns, fill rate vs spread, et calibration diagram (si vous produisez p̂). citeturn23search2turn24search0turn24search29  

```python
# Template (à exécuter sur vos sorties de backtest)
# df contient: timestamp, equity, returns, drawdown, position_gross, trades_count
import matplotlib.pyplot as plt

fig = plt.figure()
plt.plot(df["timestamp"], df["equity"])
plt.title("Equity Curve")
plt.xlabel("Time")
plt.ylabel("Equity")
plt.show()

fig = plt.figure()
plt.plot(df["timestamp"], df["drawdown"])
plt.title("Drawdown")
plt.xlabel("Time")
plt.ylabel("Drawdown")
plt.show()
```
