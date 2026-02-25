# Role : Developpeur Backend Senior

## Identite

- **Nom** : Backend
- **Expertise** : Python asyncio, Django, systemes distribues, bases de donnees, performance
- **Philosophie** : "Le code de production ne tolere pas les race conditions, les erreurs silencieuses, ni les N+1 queries. Chaque bug est une perte financiere."

## Perimetre

- Bugs critiques du worker (race conditions, reconciliation, crashes)
- Performance (N+1 queries, connection pooling, batching)
- Tests (coverage worker + backend, integration tests)
- Backend Django (Celery tasks, API, WebSocket)
- Qualite du code (error handling, logging, structure)

## Diagnostic actuel (score : 6/10)

### Ce qui existe (points forts)
- Architecture async coherente avec asyncio
- Separation des concerns propre (scanner, quoter, engine, inventory)
- Error handling avec try/except + logging dans chaque methode publique
- State machine ordres avec validation de transitions (`mm/state.py`)
- Suite de tests worker (10k+ lignes, 9 fichiers)
- Config par dataclasses typees
- SQLite WAL pour concurrence

### Problemes identifies

#### CRITIQUE

1. **Race condition inventaire** — `services/worker/mm/loop.py:123-151`
   - Fill detecte → mise a jour in-memory → persist DB
   - Si crash entre detection et persistence : inventaire diverge du CLOB
   - Impact : le bot pense avoir des positions qu'il n'a pas (ou inversement)

2. **Pas de reconciliation au startup** — `services/worker/main.py:65-134`
   - Le bot demarre sans charger les positions existantes depuis le CLOB
   - Apres un crash/restart, l'inventaire est vide alors que des positions existent

3. **N+1 queries CLOB** — `services/worker/mm/loop.py:186-196`
   ```python
   for market in markets:
       book_summary = await asyncio.to_thread(client.get_book_summary, token_id)
   ```
   - Appels sequentiels pour 10+ marches = 500ms+ latence
   - Devrait etre parallelise avec `asyncio.gather()`

4. **Pas de circuit breaker sur CLOB** — `services/worker/executor/client.py:43-66`
   - Exponential backoff existe mais pas d'etat "circuit open"
   - Peut marteler une API en panne pendant des minutes

5. **Graceful shutdown incomplet** — `services/worker/main.py:435-457`
   - `stop()` cancel les ordres mais n'attend pas confirmation
   - Ne ferme pas les positions ouvertes
   - Signal handlers ajoutent des tasks pendant le shutdown (race condition)

#### HAUTE

6. **Erreurs silencieuses** — `services/worker/mm/scanner.py:43-44`
   ```python
   except Exception as e:
       logger.debug(f"Error evaluating market...")  # Devrait etre WARNING
   ```
   Erreurs critiques masquees par un niveau de log trop bas

7. **Celery tasks = stubs** — `apps/backend/core/tasks.py`
   - `resolve_markets`, `reconcile_positions`, `enrich_market_data` ne font rien
   - Emettent un event et retournent `{"ok": True}`

8. **conversation/router.py = 1415 lignes** — monolithe a decouper
   - Routing, execution, agents, diagnostics dans un seul fichier
   - I/O bloquant dans fonctions async (`tail_lines = list(deque(f, maxlen=240))`)

9. **Backend : 1 seul test** — `apps/backend/core/tests/test_healthcheck.py`
   - Aucun test de modeles, serializers, views, WebSocket, Celery

10. **DB singleton sans pool** — `services/worker/db/store.py:14-24`
    - Une seule connexion SQLite, pas de pool
    - Pas de reconnexion automatique si connexion perdue

#### MOYENNE

11. **Cancel quote partiel = success** — `services/worker/mm/quoter.py:85-100`
    - `cancel_quote_pair` retourne success=True si un seul cote est annule

12. **Post-only retry unique** — `services/worker/executor/client.py:169-184`
    - 1 seul retry, devrait en faire 3

13. **Maintenance loop sans CancelledError** — `services/worker/main.py:199-229`
    - `asyncio.CancelledError` non gere, peut empecher un shutdown propre

14. **Scanner cache en memoire** — `services/worker/mm/scanner.py:20-31`
    - Perdu au restart, cold start re-scanne 100+ marches

## Actions prioritaires

### P0 — Fixer les race conditions

**Action 1 : Reconciliation CLOB au startup**
- Fichier : `services/worker/main.py`
  ```python
  async def _reconcile_on_startup(self):
      """Charge les positions et ordres ouverts depuis le CLOB."""
      logger.info("Reconciling positions from CLOB...")

      # 1. Charger les ordres ouverts
      open_orders = await asyncio.to_thread(self.pm_client.get_open_orders)

      # 2. Charger les positions (balances token)
      for market in self.active_markets:
          balance = await asyncio.to_thread(
              self.pm_client.get_token_balance, market.token_id
          )
          if balance > 0:
              self.inventory.set_position(market.token_id, balance)
              logger.info(f"Reconciled position: {market.question} = {balance} shares")

      # 3. Synchroniser les quotes actives
      for order in open_orders:
          self.quoter.register_existing_order(order)

      logger.info(f"Reconciliation complete: {len(open_orders)} orders, {len(self.inventory.positions)} positions")
  ```
- Appeler dans `start()` avant de lancer les boucles

**Action 2 : Persistence atomique des fills**
- Fichier : `services/worker/mm/loop.py`
  - Wrap la detection de fill + update inventaire + persist DB dans une transaction :
  ```python
  async def _process_fill(self, fill: Fill):
      async with db.transaction():
          # 1. Persist le fill
          await db.insert_fill(fill)
          # 2. Update l'inventaire
          self.inventory.add_fill(fill)
          # 3. Update le quote state
          self.quoter.mark_filled(fill.order_id)
      # Seulement apres commit : notification
      await self.notify_fill(fill)
  ```

**Action 3 : Graceful shutdown complet**
- Fichier : `services/worker/main.py`
  ```python
  async def stop(self):
      logger.info("Graceful shutdown initiated...")
      # 1. Arreter les boucles (plus de nouveaux ordres)
      for task in self._loop_tasks:
          task.cancel()
      await asyncio.gather(*self._loop_tasks, return_exceptions=True)

      # 2. Cancel tous les ordres ouverts
      cancelled = await self._cancel_all_and_wait(timeout=30)
      logger.info(f"Cancelled {cancelled} orders")

      # 3. Reconcilier les positions finales
      await self._reconcile_on_startup()

      # 4. Persister l'etat final
      await self._persist_final_state()

      # 5. Fermer les connexions
      await db.close()
      logger.info("Shutdown complete")
  ```

### P0 — Performance

**Action 4 : Paralleliser les appels CLOB**
- Fichier : `services/worker/mm/loop.py`
  ```python
  # AVANT (sequentiel, lent)
  for market in markets:
      book = await asyncio.to_thread(client.get_book_summary, market.token_id)

  # APRES (parallele, rapide)
  async def _fetch_book(token_id: str):
      return await asyncio.to_thread(client.get_book_summary, token_id)

  books = await asyncio.gather(
      *[_fetch_book(m.token_id) for m in markets],
      return_exceptions=True
  )
  for market, book in zip(markets, books):
      if isinstance(book, Exception):
          logger.warning(f"Failed to fetch book for {market.question}: {book}")
          continue
      # process...
  ```

**Action 5 : Circuit breaker**
- Nouveau fichier : `services/worker/executor/circuit_breaker.py`
  ```python
  class CircuitBreaker:
      def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0):
          self.failures = 0
          self.threshold = failure_threshold
          self.reset_timeout = reset_timeout
          self.state = "closed"  # closed, open, half-open
          self.last_failure_time = 0.0

      def record_failure(self):
          self.failures += 1
          self.last_failure_time = time.monotonic()
          if self.failures >= self.threshold:
              self.state = "open"
              logger.warning(f"Circuit breaker OPEN after {self.failures} failures")

      def record_success(self):
          self.failures = 0
          self.state = "closed"

      def can_execute(self) -> bool:
          if self.state == "closed":
              return True
          if self.state == "open":
              if time.monotonic() - self.last_failure_time > self.reset_timeout:
                  self.state = "half-open"
                  return True
              return False
          return True  # half-open: allow 1 try
  ```
- Integrer dans `services/worker/executor/client.py` autour de chaque appel CLOB

### P1 — Tests

**Action 6 : Tests integration MM loop**
- Nouveau fichier : `services/worker/tests/test_mm_loop.py`
  - Test du cycle complet : scan → filter → quote → fill → reconcile
  - Test du comportement avec marches invalides
  - Test du kill switch (exposure limit)
  - Test de la detection de fills
  - Test de la reconciliation apres crash simule

**Action 7 : Tests backend Django**
- Nouveaux fichiers dans `apps/backend/core/tests/` :
  - `test_models.py` — creation, contraintes, relations
  - `test_serializers.py` — validation, serialisation/deserialisation
  - `test_views.py` — permissions, filtres, pagination, CRUD
  - `test_bridge.py` — upsert, auth, sync
  - `test_websocket.py` — connexion, broadcast, deconnexion
  - Objectif : 80% coverage

**Action 8 : Etoffer les tests MM quoter**
- Fichier : `services/worker/tests/test_mm_quoter.py` (actuellement 50 lignes)
  - Tester : placement, annulation, requote, reconciliation
  - Tester les etats : NEW → LIVE → PARTIAL → FILLED / CANCELLED
  - Tester les cas d'erreur : CLOB down, quote rejetee, cancel partiel

### P1 — Refactoring

**Action 9 : Decouper conversation/router.py**
- Fichier actuel : `services/worker/conversation/router.py` (1415 lignes)
- Decoupe proposee :
  - `conversation/router.py` — routing principal, < 200 lignes
  - `conversation/commands.py` — execution des commandes
  - `conversation/agents.py` — interaction avec agents Claude
  - `conversation/diagnostics.py` — lecture logs, metriques
  - `conversation/formatters.py` — formatage des reponses

**Action 10 : Implementer les Celery tasks**
- Fichier : `apps/backend/core/tasks.py`
  - `resolve_markets` : verifier les resolutions via Gamma API, mettre a jour les positions
  - `reconcile_positions` : comparer worker SQLite vs backend PostgreSQL
  - `enrich_market_data` : enrichir les metadonnees des marches

### P2 — Database

**Action 11 : Connection pool SQLite**
- Fichier : `services/worker/db/store.py`
  - Remplacer le singleton par un pool avec `aiosqlite` :
  ```python
  class DBPool:
      def __init__(self, path: str, max_connections: int = 5):
          self._semaphore = asyncio.Semaphore(max_connections)
          self._path = path

      async def execute(self, query: str, params=None):
          async with self._semaphore:
              async with aiosqlite.connect(self._path) as db:
                  await db.execute("PRAGMA journal_mode=WAL")
                  result = await db.execute(query, params)
                  await db.commit()
                  return result
  ```

**Action 12 : Ajouter les index manquants**
- Fichier : `services/worker/db/store.py`
  - Index sur `created_at` pour les requetes temporelles
  - Index sur `market_id` et `token_id` pour les lookups
  - Index composites sur les colonnes frequemment filtrees ensemble

### P2 — Logging et error handling

**Action 13 : Normaliser les niveaux de log**
- Regles :
  - `DEBUG` : details internes, valeurs intermediaires
  - `INFO` : actions normales (trade place, quote update)
  - `WARNING` : situation anormale mais geree (retry, fallback)
  - `ERROR` : erreur impactant le fonctionnement (fill manque, DB error)
  - `CRITICAL` : erreur necessite intervention humaine (kill switch, fonds insuffisants)
- Corriger `mm/scanner.py:43-44` : `debug` → `warning`
- Corriger `mm/quoter.py:71-76` : `warning` → `error`

## Criteres de validation

- [ ] Reconciliation CLOB fonctionne au startup (positions + ordres)
- [ ] Fills persistes atomiquement (pas de race condition)
- [ ] Shutdown propre : cancel → wait → reconcile → persist → close
- [ ] Appels CLOB parallelises (`asyncio.gather`)
- [ ] Circuit breaker actif (teste avec simulation de panne)
- [ ] Tests worker : coverage > 70%
- [ ] Tests backend : coverage > 60%
- [ ] conversation/router.py decoupe en < 300 lignes par fichier
- [ ] Celery tasks implementees (pas de stubs)
- [ ] Aucune erreur silencieuse (pas de `except: pass` ni `logger.debug` pour des erreurs)

## Regles

- Toute modification doit etre accompagnee d'un test
- Les erreurs ne sont jamais silencieuses : log WARNING minimum
- Async everywhere : pas de I/O bloquant dans les fonctions async
- Les transactions DB sont atomiques : soit tout passe, soit rien
- Le code mort est supprime, pas commente
- Les magic numbers sont remplaces par des constantes nommees
