# Worker Service

This worker executes **real-money trading only** (paper trading removed).

## Conversation Interface

- Natural-language chat is enabled for Telegram free-text messages and dashboard `chat_message` commands.
- Routing is Claude-first when `ANTHROPIC_API_KEY` is configured; Claude decides replies and bot actions.
- Runtime router: `services/worker/conversation/router.py`
- Messages and agent replies are persisted to `conversations` table, then synced to backend by `bridge.py`.
- Sensitive actions (`kill`, `restart`, process start/stop, setting changes) require explicit confirmation.

## Developer Live Actions (optional)

- To allow approved "code change + redeploy" requests from chat, configure `DEVELOPER_HOOK_CMD`.
- The same hook is used when a manager critique is approved (`approve_critique` command).
- The hook is called by worker maintenance on `developer_request` commands with one JSON argument:
  - `{"request": "...", "source": "...", "requested_at": "..."}`
- Recommended hook file in this repo: `scripts/developer_live_hook.sh`.
- Optional shell command mode (`cmd: ...`) is disabled by default; enable with `DEVELOPER_ALLOW_CMD=true`.
- If `DEVELOPER_HOOK_CMD` is unset, developer live actions are rejected safely.

## `run_worker.py`
Runs the extracted trading engine from `services/worker/main.py`.

## `bridge.py`
Bridge sidecar for sync between worker SQLite state and Django control-plane.

### Usage

```bash
python services/worker/run_worker.py
python services/worker/bridge.py
```

Required environment variables:
- `CONTROL_PLANE_URL`
- `CONTROL_PLANE_BRIDGE_TOKEN`
- `WORKER_SQLITE_PATH` (optional, defaults to `db/polybot.db`)
