"""APScheduler worker — runs ingestion pipeline on a weekly cadence.

Start with:
    python worker.py

The scheduler fires every Monday at 02:00 UTC by default.
Override with env vars:
    CANOPY_SCHEDULE_DAY_OF_WEEK=mon
    CANOPY_SCHEDULE_HOUR=2
    CANOPY_SCHEDULE_MINUTE=0
"""

import asyncio
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import RiskScore
from app.services.alerts import trigger_alerts
from pipelines.ingest import run_ingestion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

ZONES_PATH = os.environ.get("CANOPY_ZONES_PATH", "zones.geojson")
ALERT_THRESHOLD = int(os.environ.get("ALERT_THRESHOLD", "70"))


def _window() -> tuple[str, str]:
    """Return (start, end) for the last 7-day window ending yesterday."""
    today = date.today()
    end = today
    start = today - timedelta(days=7)
    return start.isoformat(), end.isoformat()


async def _send_alerts_for_run(run_started_at: datetime) -> dict:
    """
    Find every risk score inserted during this ingestion run and fire
    threshold-crossing alerts for each zone. Safe to call even if no
    farmers are registered yet — trigger_alerts just sends 0 emails.
    """
    settings = get_settings()
    sent = failed = checked = 0

    async with SessionLocal() as db:
        new_scores = list(
            (
                await db.scalars(
                    select(RiskScore).where(RiskScore.created_at >= run_started_at)
                )
            ).all()
        )
        log.info("Checking %d new risk scores for alert-worthy zones", len(new_scores))

        for rs in new_scores:
            checked += 1
            try:
                result = await trigger_alerts(
                    db, settings, rs.id, ALERT_THRESHOLD, dry_run=False
                )
                sent += result.sent
                failed += result.failed
            except Exception as exc:
                log.error("Alert trigger failed for risk_score %s: %s", rs.id, exc)

    return {"checked": checked, "sent": sent, "failed": failed}


def ingestion_job() -> None:
    settings = get_settings()
    start, end = _window()
    run_started_at = datetime.now(UTC)
    log.info("Running scheduled ingestion window=%s→%s", start, end)

    try:
        with open(ZONES_PATH, encoding="utf-8") as fh:
            zones_geojson = json.load(fh)
    except FileNotFoundError:
        log.error(
            "zones.geojson not found at %s — run scripts/seed_zones.py first", ZONES_PATH
        )
        return

    summary = asyncio.run(
        run_ingestion(
            zones_geojson=zones_geojson,
            start=start,
            end=end,
            gee_project=settings.gee_project,
            settings=settings,
        )
    )
    log.info("Ingestion job done: %s", summary)

    # Now check the freshly-ingested scores and email any farmers in
    # zones that crossed the alert threshold.
    alert_summary = asyncio.run(_send_alerts_for_run(run_started_at))
    log.info("Alert pass done: %s", alert_summary)


if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        ingestion_job,
        trigger=CronTrigger(
            day_of_week=os.environ.get("CANOPY_SCHEDULE_DAY_OF_WEEK", "mon"),
            hour=int(os.environ.get("CANOPY_SCHEDULE_HOUR", "2")),
            minute=int(os.environ.get("CANOPY_SCHEDULE_MINUTE", "0")),
            timezone="UTC",
        ),
        id="ingestion",
        name="Weekly canopy ingestion",
        replace_existing=True,
    )
    log.info("Scheduler started — next run: %s", scheduler.get_jobs()[0].next_run_time)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")