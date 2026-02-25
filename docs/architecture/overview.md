# Architecture Overview

## Repository Shape

```
apps/
  backend/      # Django control-plane
  frontend/     # Next.js dashboard (Tailwind enabled)
services/
  worker/       # trading runtime + bridge
```

## Runtime Services

1. `polybot` (worker):
   hybrid trading runtime with MM loop, CD loop, CD exit loop, CD analysis loop, guard loop, maintenance loop.
2. `backend` (Django ASGI):
   API, auth, websocket, command plane, bridge ingestion.
3. `frontend` (Next.js):
   operator dashboard with pages for access, overview, positions, trades, performance, settings, learning, journal, chat, MM, CD.
4. `worker-bridge`:
   SQLite to control-plane synchronization.
5. `postgres` + `redis`:
   control-plane data and queue/pubsub services.
6. `celery-worker` + `celery-beat`:
   async backend jobs.
