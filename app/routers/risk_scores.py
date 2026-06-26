import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RiskLevel, RiskScore
from app.schemas import RiskScoreOut

router = APIRouter(prefix="/risk-scores", tags=["risk scores"])


@router.get("", response_model=list[RiskScoreOut])
async def list_risk_scores(
    zone_id: uuid.UUID | None = None,
    since: datetime | None = None,
    level: RiskLevel | None = None,
    min_score: float | None = Query(None, ge=0, le=100),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """List risk scores with optional filters. Returns newest-first."""
    query = select(RiskScore).order_by(RiskScore.observed_at.desc()).limit(limit)
    if zone_id:
        query = query.where(RiskScore.zone_id == zone_id)
    if since:
        query = query.where(RiskScore.observed_at >= since)
    if min_score is not None:
        query = query.where(RiskScore.score >= min_score)
    if level:
        query = query.where(RiskScore.level == level)
    return list((await db.scalars(query)).all())


@router.get("/latest", response_model=list[RiskScoreOut])
async def latest_by_zone(
    country_code: str | None = None,
    min_score: float | None = Query(None, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Return the single most recent RiskScore per zone."""
    from sqlalchemy import func
    from app.models import Zone

    # Subquery: max observed_at per zone
    subq = (
        select(
            RiskScore.zone_id,
            func.max(RiskScore.observed_at).label("max_observed_at"),
        )
        .group_by(RiskScore.zone_id)
        .subquery()
    )
    query = (
        select(RiskScore)
        .join(
            subq,
            (RiskScore.zone_id == subq.c.zone_id)
            & (RiskScore.observed_at == subq.c.max_observed_at),
        )
        .order_by(RiskScore.score.desc())
    )
    if country_code or min_score is not None:
        query = query.join(Zone, Zone.id == RiskScore.zone_id)
    if country_code:
        query = query.where(Zone.country_code == country_code.upper())
    if min_score is not None:
        query = query.where(RiskScore.score >= min_score)

    return list((await db.scalars(query)).all())


@router.get("/{score_id}", response_model=RiskScoreOut)
async def get_risk_score(score_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a single risk score by ID."""
    row = await db.get(RiskScore, score_id)
    if row is None:
        raise HTTPException(404, "RiskScore not found")
    return row
