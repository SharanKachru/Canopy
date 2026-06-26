import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models import AlertStatus, RiskLevel


class ZoneOut(BaseModel):
    id: uuid.UUID
    grid_code: str
    name: str | None
    country_code: str
    admin1: str | None
    admin2: str | None
    geometry: dict


class RiskScoreOut(BaseModel):
    id: uuid.UUID
    zone_id: uuid.UUID
    observed_at: datetime
    window_start: date
    window_end: date

    # Raw indicators
    ndvi_mean: float | None
    ndvi_baseline_mean: float | None
    ndvi_baseline_std: float | None
    ndvi_anomaly: float | None

    rainfall_mm: float | None
    rainfall_baseline_mm: float | None
    rainfall_deficit: float | None

    soil_moisture: float | None
    soil_moisture_percentile: float | None

    # Fused outputs
    ndvi_risk: float
    rainfall_risk: float
    soil_moisture_risk: float
    score: float
    level: RiskLevel
    confidence: float
    quality: dict
    model_version: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertOut(BaseModel):
    id: uuid.UUID
    farmer_id: uuid.UUID
    zone_id: uuid.UUID
    risk_score_id: uuid.UUID
    channel: str
    threshold: int
    status: AlertStatus
    message: str
    attempts: int
    sent_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SendAlertRequest(BaseModel):
    risk_score_id: uuid.UUID
    threshold: int = Field(default=70, ge=0, le=100)
    dry_run: bool = False


class SendAlertResult(BaseModel):
    eligible: int
    sent: int
    failed: int
    skipped_no_crossing: bool = False
