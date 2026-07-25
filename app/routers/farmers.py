import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Farmer, Zone
from app.schemas import FarmerOut, FarmerRegisterRequest

router = APIRouter(tags=["farmers"])


@router.post("/farmers/register", response_model=FarmerOut)
async def register_farmer(
    request: FarmerRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a farmer by district name (and optionally state/admin1).

    Matches against Zone.admin2 (district) and Zone.name, case-insensitively.
    If multiple zones share a district name across different states, pass
    `state` to disambiguate.
    """
    query = select(Zone).where(
        or_(
            func.lower(Zone.admin2) == request.district.strip().lower(),
            func.lower(Zone.name) == request.district.strip().lower(),
        )
    )
    if request.state:
        query = query.where(func.lower(Zone.admin1) == request.state.strip().lower())

    zones = list((await db.scalars(query)).all())

    if not zones:
        raise HTTPException(
            404,
            f"No district matching '{request.district}' found. "
            "Try including `state` to disambiguate, or check spelling.",
        )
    if len(zones) > 1:
        matches = [f"{z.admin2} ({z.admin1})" for z in zones]
        raise HTTPException(
            409,
            f"Multiple districts match '{request.district}': {matches}. "
            "Please specify `state` to disambiguate.",
        )

    zone = zones[0]

    existing = await db.scalar(select(Farmer).where(Farmer.email == request.email))
    if existing:
        raise HTTPException(409, f"A farmer with email {request.email} is already registered.")

    farmer = Farmer(
        id=uuid.uuid4(),
        zone_id=zone.id,
        name=request.name,
        email=request.email,
        preferred_language=request.preferred_language or "en",
        email_opt_in=True,
        active=True,
    )
    db.add(farmer)
    await db.commit()
    await db.refresh(farmer)

    return FarmerOut(
        id=farmer.id,
        name=farmer.name,
        email=farmer.email,
        zone_id=zone.id,
        district=zone.admin2,
        state=zone.admin1,
        preferred_language=farmer.preferred_language,
        email_opt_in=farmer.email_opt_in,
        active=farmer.active,
    )


@router.get("/farmers/{farmer_id}", response_model=FarmerOut)
async def get_farmer(farmer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    farmer = await db.get(Farmer, farmer_id)
    if not farmer:
        raise HTTPException(404, "Farmer not found")
    zone = await db.get(Zone, farmer.zone_id)
    return FarmerOut(
        id=farmer.id,
        name=farmer.name,
        email=farmer.email,
        zone_id=farmer.zone_id,
        district=zone.admin2 if zone else None,
        state=zone.admin1 if zone else None,
        preferred_language=farmer.preferred_language,
        email_opt_in=farmer.email_opt_in,
        active=farmer.active,
    )


@router.delete("/farmers/{farmer_id}", status_code=204)
async def unregister_farmer(farmer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Soft-delete: marks the farmer inactive so they stop receiving alerts."""
    farmer = await db.get(Farmer, farmer_id)
    if not farmer:
        raise HTTPException(404, "Farmer not found")
    farmer.active = False
    farmer.email_opt_in = False
    await db.commit()