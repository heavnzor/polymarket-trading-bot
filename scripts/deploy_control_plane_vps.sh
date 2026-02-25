#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-your-vps}"
REMOTE_DIR="${REMOTE_DIR:-~/polymarket}"

rsync -avz \
  --delete \
  --exclude '.env' \
  --exclude '.venv/' \
  --exclude '.venv-ci/' \
  --exclude '__pycache__/' \
  --exclude 'db/*.db' \
  --exclude 'db/*.db-shm' \
  --exclude 'db/*.db-wal' \
  --exclude 'logs/' \
  --exclude 'apps/frontend/node_modules/' \
  --exclude 'apps/frontend/.next/' \
  --exclude 'apps/frontend/package-lock.json' \
  --exclude '.git/' \
  ./ "${REMOTE_HOST}:${REMOTE_DIR}/"

ssh -o ConnectTimeout=10 "${REMOTE_HOST}" \
  "cd ${REMOTE_DIR} && docker compose -f docker-compose.control-plane.yml up -d --build"

# Ensure nginx upstream DNS is refreshed after frontend/backend container recreate.
ssh -o ConnectTimeout=10 "${REMOTE_HOST}" \
  "cd ${REMOTE_DIR} && docker compose -f docker-compose.control-plane.yml restart bot-proxy"

ssh -o ConnectTimeout=10 "${REMOTE_HOST}" \
  "cd ${REMOTE_DIR} && rm -rf agent data executor learning monitor notifications strategy dashboard main.py config.py"

ssh -o ConnectTimeout=10 "${REMOTE_HOST}" \
  "cd ${REMOTE_DIR} && if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi && .venv/bin/pip install -r requirements.txt -r services/worker/requirements.txt"

ssh -o ConnectTimeout=10 "${REMOTE_HOST}" \
  "cd ${REMOTE_DIR} && pm2 delete polybot >/dev/null 2>&1 || true && pm2 start services/worker/run_worker.py --name polybot --interpreter ${REMOTE_DIR}/.venv/bin/python --cwd ${REMOTE_DIR} --time && pm2 save"

ssh -o ConnectTimeout=10 "${REMOTE_HOST}" \
  "cd ${REMOTE_DIR} && docker compose -f docker-compose.control-plane.yml ps"

ssh -o ConnectTimeout=10 "${REMOTE_HOST}" \
  "pm2 status polybot"
