import logging

from celery import shared_task

from core.services import emit_realtime_event

logger = logging.getLogger(__name__)


@shared_task(name="core.tasks.resolve_markets")
def resolve_markets() -> dict:
    logger.info("resolve_markets task triggered")
    event = emit_realtime_event(
        "job.resolve_markets",
        {
            "status": "scheduled",
        },
    )
    return {"ok": True, "event": event}


@shared_task(name="core.tasks.reconcile_positions")
def reconcile_positions() -> dict:
    logger.info("reconcile_positions task triggered")
    event = emit_realtime_event(
        "job.reconcile_positions",
        {
            "status": "scheduled",
        },
    )
    return {"ok": True, "event": event}


@shared_task(name="core.tasks.enrich_market_data")
def enrich_market_data() -> dict:
    logger.info("enrich_market_data task triggered")
    event = emit_realtime_event(
        "job.enrich_market_data",
        {
            "status": "scheduled",
        },
    )
    return {"ok": True, "event": event}
