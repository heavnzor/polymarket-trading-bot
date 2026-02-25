from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from core.models import BotStatus, PerformanceSnapshot, RealtimeEvent


EVENT_GROUP = "bot-events"


def emit_realtime_event(event_type: str, payload: dict | None = None, *, persist: bool = True) -> dict:
    payload = payload or {}
    event_row = None
    if persist:
        event_row = RealtimeEvent.objects.create(event_type=event_type, payload=payload)

    channel_layer = get_channel_layer()
    if channel_layer is not None:
        async_to_sync(channel_layer.group_send)(
            EVENT_GROUP,
            {
                "type": "realtime.event",
                "event_type": event_type,
                "payload": payload,
                "emitted_at": event_row.emitted_at.isoformat() if event_row else None,
            },
        )

    return {
        "event_type": event_type,
        "payload": payload,
        "emitted_at": event_row.emitted_at.isoformat() if event_row else None,
    }


def _bot_status_value(key: str, default=None):
    row = BotStatus.objects.filter(key=key).first()
    if row is None:
        return default
    return row.value


def _as_float(value, default=0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value, default=0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def build_overview() -> dict:
    perf_snapshot = PerformanceSnapshot.objects.filter(snapshot_type="stats").first()
    perf_payload = perf_snapshot.payload if perf_snapshot else {}

    is_paused = bool(_bot_status_value("is_paused", False))
    is_running = bool(_bot_status_value("bot_running", False))
    if is_paused:
        bot_status = "paused"
    elif is_running:
        bot_status = "running"
    else:
        bot_status = "stopped"

    return {
        "available_usdc": _as_float(_bot_status_value("available_usdc", 0)),
        "onchain_balance": _as_float(_bot_status_value("onchain_balance", None), default=0.0)
        if _bot_status_value("onchain_balance", None) is not None
        else None,
        "positions_count": _as_int(_bot_status_value("positions_count", 0)),
        "daily_pnl": _as_float(_bot_status_value("daily_pnl", 0)),
        "daily_traded": _as_float(_bot_status_value("daily_traded", 0)),
        "total_invested": _as_float(_bot_status_value("total_invested", 0)),
        "portfolio_value": _as_float(_bot_status_value("portfolio_value", 0)),
        "total_pnl": _as_float(perf_payload.get("total_pnl", 0)),
        "roi_percent": _as_float(perf_payload.get("roi_percent", 0)),
        "hit_rate": _as_float(perf_payload.get("hit_rate", 0)),
        "total_trades": _as_int(perf_payload.get("total_trades", 0)),
        "bot_status": bot_status,
        "is_paper": bool(_bot_status_value("is_paper", False)),
        "strategy": str(_bot_status_value("strategy", "active")),
        "cycle_number": _as_int(_bot_status_value("cycle_number", 0)),
        "cycle_interval_minutes": _as_int(_bot_status_value("cycle_interval_minutes", 30)),
    }
