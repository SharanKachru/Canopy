"""Alert triggering service.

Logic:
1. Load the RiskScore.
2. Check threshold crossing (current >= threshold AND previous < threshold).
3. Also deduplicate by cooldown window — skip farmers who already got an
   alert for this zone within the last alert_cooldown_hours hours.
4. Send email alert via Resend; persist Alert row with outcome either way.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Alert, AlertStatus, Farmer, RiskScore, Zone
from app.schemas import SendAlertResult
from app.services.email import render_alert_text, send_email_alert

log = logging.getLogger(__name__)


async def trigger_alerts(
    db: AsyncSession,
    settings: Settings,
    risk_score_id: uuid.UUID,
    threshold: int,
    dry_run: bool,
) -> SendAlertResult:
    """Send email alerts to all opted-in, active farmers in the zone if threshold crossed."""
    current = await db.get(RiskScore, risk_score_id)
    if current is None:
        raise LookupError(f"RiskScore {risk_score_id} not found")

    # Check threshold crossing: score must go FROM below TO at/above threshold
    previous = await db.scalar(
        select(RiskScore)
        .where(
            RiskScore.zone_id == current.zone_id,
            RiskScore.observed_at < current.observed_at,
        )
        .order_by(RiskScore.observed_at.desc())
        .limit(1)
    )
    crossed = current.score >= threshold and (previous is None or previous.score < threshold)
    if not crossed:
        log.info(
            "No threshold crossing for zone %s (score=%.1f, prev=%.1f, threshold=%d)",
            current.zone_id,
            current.score,
            previous.score if previous else 0,
            threshold,
        )
        return SendAlertResult(eligible=0, sent=0, failed=0, skipped_no_crossing=True)

    zone = await db.get(Zone, current.zone_id)
    farmers = list(
        (
            await db.scalars(
                select(Farmer).where(
                    Farmer.zone_id == current.zone_id,
                    Farmer.active.is_(True),
                    Farmer.email_opt_in.is_(True),
                    Farmer.email.isnot(None),
                )
            )
        ).all()
    )
    log.info(
        "Threshold crossed for zone %s (score=%.1f) — %d eligible farmers",
        zone.grid_code,
        current.score,
        len(farmers),
    )

    # Cooldown window: don't spam farmers who already got an alert recently
    cooldown_cutoff = datetime.now(UTC) - timedelta(hours=settings.alert_cooldown_hours)
    recently_alerted = set(
        (
            await db.scalars(
                select(Alert.farmer_id).where(
                    Alert.zone_id == current.zone_id,
                    Alert.status == AlertStatus.sent,
                    Alert.sent_at >= cooldown_cutoff,
                )
            )
        ).all()
    )
    log.info(
        "%d farmer(s) skipped due to cooldown (%dh)",
        len(recently_alerted),
        settings.alert_cooldown_hours,
    )

    sent = failed = skipped_cooldown = 0

    for farmer in farmers:
        if farmer.id in recently_alerted:
            skipped_cooldown += 1
            continue

        message = render_alert_text(
            farmer.name,
            zone.name or zone.grid_code,
            current.score,
            current.level.value,
            farmer.preferred_language,
        )

        alert = Alert(
            id=uuid.uuid4(),
            farmer_id=farmer.id,
            zone_id=current.zone_id,
            risk_score_id=current.id,
            channel="email",
            threshold=threshold,
            message=message,
            status=AlertStatus.pending,
            attempts=0,
            created_at=datetime.now(UTC),
        )
        db.add(alert)

        if dry_run:
            log.info("[DRY RUN] Would email %s (%s)", farmer.name, farmer.email)
            alert.status = AlertStatus.pending
            await db.commit()
            sent += 1
            continue

        alert.attempts += 1
        try:
            result = await send_email_alert(
                settings,
                farmer.email,
                farmer.name,
                zone.name or zone.grid_code,
                current.score,
                current.level.value,
                farmer.preferred_language,
            )
            alert.status = AlertStatus.sent
            alert.provider_message_id = result.message_id
            alert.provider_response = result.response
            alert.sent_at = datetime.now(UTC)
            log.info("Email alert sent to %s (id=%s)", farmer.email, result.message_id)
            sent += 1
        except Exception as exc:
            alert.status = AlertStatus.failed
            alert.error = str(exc)[:2000]
            log.error("Failed to email %s: %s", farmer.email, exc)
            failed += 1

        await db.commit()

    return SendAlertResult(
        eligible=len(farmers) - skipped_cooldown,
        sent=sent,
        failed=failed,
    )