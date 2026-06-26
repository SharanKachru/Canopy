import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import JSON, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Zone
from app.schemas import ZoneOut

router = APIRouter(prefix="/zones", tags=["zones"])


def zone_out(zone: Zone, geometry: dict) -> ZoneOut:
    return ZoneOut(
        id=zone.id,
        grid_code=zone.grid_code,
        name=zone.name,
        country_code=zone.country_code,
        admin1=zone.admin1,
        admin2=zone.admin2,
        geometry=geometry,
    )


@router.get("", response_model=list[ZoneOut])
async def list_zones(
    country_code: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    query = select(Zone, func.ST_AsGeoJSON(Zone.geom).cast(JSON)).limit(limit)
    if country_code:
        query = query.where(Zone.country_code == country_code.upper())
    rows = (await db.execute(query)).all()
    return [zone_out(zone, geometry) for zone, geometry in rows]


@router.get("/{zone_id}", response_model=ZoneOut)
async def get_zone(zone_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(Zone, func.ST_AsGeoJSON(Zone.geom).cast(JSON)).where(Zone.id == zone_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Zone not found")
    return zone_out(*row)
