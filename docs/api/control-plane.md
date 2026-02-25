# Control Plane API

Base path: `/api/v1`

## Core

- `GET /health/`
- `GET /overview/`
- `GET /trades/`
- `GET /positions/`
- `GET /performance/`
- `GET /order-events/`
- `GET /status/`
- `GET /settings/`
- `GET|POST /commands/`

## Hybrid Trading Endpoints

- `GET /mm-quotes/` (`?status=active|...`)
- `GET /mm-inventory/`
- `GET /mm-metrics/`
- `GET /cd-signals/`

## Bridge Endpoints

Bridge ingestion endpoints under `/bridge/*` for worker sync:

- trades, positions, status, settings
- performance and order events
- commands pending/result
- risk-officer reviews upsert (`/bridge/risk-reviews/upsert/`)
- strategist assessments upsert (`/bridge/strategist/upsert/`)
- learning/chat/audit upserts (legacy compatibility)

## Realtime

- websocket: `/ws/control-plane/`
