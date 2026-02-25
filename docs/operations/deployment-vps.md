# VPS Deployment

## Host

- SSH alias: `your-vps`
- Repo path: `~/polymarket`

## Standard Deploy

```bash
./scripts/deploy_control_plane_vps.sh
```

## Trading Mode

- Worker mode is **real money only**.
- Paper trading runtime toggles and config keys were removed from the codebase.

## Runtime Services

- Django backend (`/api/v1/*`)
- Channels websocket (`/ws/control-plane/*`)
- Next.js frontend under `/bot/*` (Tailwind enabled build)
- worker process (`services/worker/main.py` via PM2)
- bridge sidecar (SQLite -> control-plane sync)

## Required Frontend Env

- `NEXT_BASE_PATH=/bot`
- `NEXT_PUBLIC_CONTROL_PLANE_URL=/bot/api/v1`
- `NEXT_PUBLIC_CONTROL_PLANE_WS_URL=/bot/ws/control-plane/`
- `DASHBOARD_PASSWORD=<required>`

## Quick Checks

```bash
ssh -o ConnectTimeout=10 your-vps "cd ~/polymarket && docker compose -f docker-compose.control-plane.yml ps"
ssh -o ConnectTimeout=10 your-vps "curl --connect-timeout 5 --max-time 10 http://127.0.0.1:8000/api/v1/health/"
ssh -o ConnectTimeout=10 your-vps "pm2 status polybot"
ssh -o ConnectTimeout=10 your-vps "pm2 logs polybot --lines 80 --nostream"
```

## Full Reset (DB + Runtime)

Use this to restart from zero on VPS:

```bash
ssh -o ConnectTimeout=10 your-vps "
  set -euo pipefail
  cd ~/polymarket
  pm2 stop polybot || true
  docker compose -f docker-compose.control-plane.yml down -v --remove-orphans
  rm -f db/polybot.db db/polybot.db-shm db/polybot.db-wal
  rm -f services/worker/.bridge_state.json logs/bot.log
  docker compose -f docker-compose.control-plane.yml up -d --build
  docker compose -f docker-compose.control-plane.yml stop worker-bridge
  touch db/polybot.db && chmod 664 db/polybot.db
  pm2 restart polybot || pm2 start services/worker/run_worker.py --name polybot --interpreter ~/polymarket/.venv/bin/python --cwd ~/polymarket --time
  docker compose -f docker-compose.control-plane.yml start worker-bridge
  pm2 save || true
  docker compose -f docker-compose.control-plane.yml ps
  pm2 status polybot
"
```

If PM2 worker logs show `sqlite3.OperationalError: attempt to write a readonly database`, run:

```bash
ssh -o ConnectTimeout=10 your-vps "
  set -euo pipefail
  cd ~/polymarket
  docker compose -f docker-compose.control-plane.yml stop worker-bridge
  rm -f db/polybot.db db/polybot.db-shm db/polybot.db-wal
  touch db/polybot.db && chmod 664 db/polybot.db
  pm2 restart polybot
  docker compose -f docker-compose.control-plane.yml start worker-bridge
"
```

## Public URLs

- `https://your-domain.com/bot/access`
- `https://your-domain.com/bot/overview`
- `https://your-domain.com/bot/mm`
- `https://your-domain.com/bot/cd`
- `https://your-domain.com/bot/api/v1/health/`
