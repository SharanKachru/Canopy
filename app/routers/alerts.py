import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Alert, AlertStatus
from app.schemas import AlertOut, SendAlertRequest, SendAlertResult
from app.services.alerts import trigger_alerts

router = APIRouter(tags=["alerts"])


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    status: AlertStatus | None = None,
    zone_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    query = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if status:
        query = query.where(Alert.status == status)
    if zone_id:
        query = query.where(Alert.zone_id == zone_id)
    return list((await db.scalars(query)).all())


@router.post("/send-alert", response_model=SendAlertResult)
async def send_alert(
    request: SendAlertRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        return await trigger_alerts(
            db, settings, request.risk_score_id, request.threshold, request.dry_run
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

