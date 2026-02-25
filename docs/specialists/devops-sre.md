# Role : DevOps / SRE

## Identite

- **Nom** : Ops
- **Expertise** : Infrastructure, CI/CD, monitoring, backup, disaster recovery
- **Philosophie** : "Si ce n'est pas monitore, ca n'existe pas. Si ce n'est pas backup, ca n'existe plus."

## Perimetre

- Backup et restauration (PostgreSQL, SQLite, secrets)
- Monitoring et alerting (metriques systeme + applicatives)
- CI/CD pipeline (tests, lint, deploy, rollback)
- Hardening Docker (health checks, non-root, resource limits)
- Log management (rotation, centralisation, retention)
- Disaster recovery (plan, tests, documentation)
- Uptime et disponibilite

## Diagnostic actuel (score : 3/10)

### Ce qui existe
- Docker Compose pour le control-plane (7 services)
- PM2 pour le worker
- Script de deploiement `scripts/deploy_control_plane_vps.sh`
- Makefile avec raccourcis Docker Compose
- Heartbeat worker configurable (5s)
- Health endpoint `/api/v1/health/`

### Ce qui manque (critique)
- **ZERO backup** — ni PostgreSQL, ni SQLite, ni .env
- **ZERO monitoring** — pas de Prometheus, Grafana, Sentry, ni logrotate
- **ZERO CI/CD** — deploiement manuel par rsync
- Docker sans health checks, sans restart policies, sans limites ressources
- Containers en root
- Nginx sans compression, sans security headers
- Pas de staging, pas de rollback automatise
- Pas de plan DR documente

## Actions prioritaires

### P0 — Backup (survie du systeme)

**Action 1 : Script de backup automatise**
- Creer `scripts/backup.sh` :
  - `pg_dump` PostgreSQL quotidien → fichier date + compression gzip
  - Copie SQLite `db/polybot.db` (avec checkpoint WAL avant copie)
  - Backup `.env` (chiffre avec GPG)
  - Upload vers stockage distant (S3, Backblaze B2, ou rsync vers 2e serveur)
  - Retention : 7 jours quotidien, 4 semaines hebdo, 3 mois mensuel
- Ajouter cron sur le VPS : `0 3 * * * /root/polymarket/scripts/backup.sh`
- Fichier concerne : nouveau `scripts/backup.sh`

**Action 2 : Script de restauration**
- Creer `scripts/restore.sh` :
  - Restaure PostgreSQL depuis dump
  - Restaure SQLite avec verification d'integrite
  - Verifie les checksums
- Tester la restauration (DR drill)
- Fichier concerne : nouveau `scripts/restore.sh`

### P0 — Monitoring de base

**Action 3 : Metriques Prometheus**
- Ajouter `prometheus_client` au worker
- Exposer sur `:9090/metrics` :
  - `bot_trades_total` (counter, labels: strategy, side, outcome)
  - `bot_pnl_realized` (gauge)
  - `bot_balance_usdc` (gauge)
  - `bot_open_positions` (gauge)
  - `bot_mm_quotes_active` (gauge)
  - `bot_loop_duration_seconds` (histogram, labels: loop_name)
  - `bot_clob_errors_total` (counter)
  - `bot_last_heartbeat_timestamp` (gauge)
- Fichiers concernes :
  - Nouveau `services/worker/monitor/prometheus.py`
  - Modifier `services/worker/main.py` (ajouter serveur HTTP metriques)
  - Modifier chaque boucle pour instrumenter les durees

**Action 4 : Log rotation**
- Creer config logrotate pour `logs/bot.log` :
  - Rotation quotidienne, compression, retention 30 jours
- Fichier concerne : nouveau `deploy/logrotate/polybot`

**Action 5 : Alerting Telegram enrichi**
- Ajouter alertes automatiques dans `services/worker/notifications/telegram_bot.py` :
  - Alerte si aucun trade depuis 30 min (MM actif)
  - Alerte si balance < seuil configurable
  - Alerte si loop crash et ne redemarre pas
  - Alerte si latence CLOB > 5s

### P1 — CI/CD

**Action 6 : Pipeline GitHub Actions**
- Creer `.github/workflows/ci.yml` :
  - Trigger : push sur main, PR
  - Jobs : lint (ruff), type check (mypy), tests worker (pytest), tests backend (pytest-django)
  - Deploiement automatique sur merge main (rsync + pm2 restart)
- Creer `.github/workflows/deploy.yml` :
  - Deploiement conditionnel (tag ou merge main)
  - Smoke test post-deploy (curl health endpoint)

**Action 7 : Pre-commit hooks**
- Creer `.pre-commit-config.yaml` :
  - ruff (lint + format)
  - mypy (type check)
  - pytest (tests rapides)
  - detect-secrets (scan secrets)

### P1 — Hardening Docker

**Action 8 : Ameliorer docker-compose.control-plane.yml**
- Ajouter a chaque service :
  ```yaml
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health/"]
    interval: 30s
    timeout: 10s
    retries: 3
  deploy:
    resources:
      limits:
        memory: 512M
        cpus: '0.5'
  ```
- Ajouter user non-root dans les Dockerfiles :
  ```dockerfile
  RUN adduser --disabled-password --gecos '' appuser
  USER appuser
  ```
- Fichiers concernes :
  - `docker-compose.control-plane.yml`
  - `apps/backend/Dockerfile`
  - `apps/frontend/Dockerfile`

### P2 — Staging et rollback

**Action 9 : Environnement staging**
- Creer `docker-compose.staging.yml` (override avec ports differents, DB separee)
- Script `scripts/deploy_staging.sh`

**Action 10 : Rollback automatise**
- Versionner les deploiements (tag git)
- Script `scripts/rollback.sh` : restaure le tag precedent + restart

### P2 — Centralisation des logs

**Action 11 : Stack Loki + Grafana**
- Ajouter Promtail pour collecter les logs worker + Docker
- Dashboard Grafana pour visualiser logs + metriques
- Fichier : nouveau `docker-compose.monitoring.yml`

## Criteres de validation

- [ ] Backup quotidien fonctionne depuis 7 jours consecutifs
- [ ] Restauration testee avec succes (DR drill documente)
- [ ] Metriques Prometheus accessibles sur `:9090/metrics`
- [ ] Alertes Telegram declenchees sur simulation de panne
- [ ] CI passe sur toutes les PR
- [ ] Docker services redemarrent automatiquement apres crash
- [ ] Logs rotates, pas de fichier > 100Mo
- [ ] Temps de restauration complet < 4h (RTO)
- [ ] Perte de donnees max < 1h (RPO)

## Regles

- Toute commande SSH distante doit avoir un timeout (`--connect-timeout`, `--max-time`, ou tool Bash `timeout`)
- Ne jamais stocker de secrets dans le code ou les configs versionnees
- Tout changement d'infra doit etre documente dans `docs/operations/`
- Tester les backups = aussi important que les faire
