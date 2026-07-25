import enum
import uuid
from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RiskLevel(str, enum.Enum):
    normal = "normal"
    watch = "watch"
    warning = "warning"
    severe = "severe"


class AlertStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    grid_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str | None]
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    admin1: Mapped[str | None]
    admin2: Mapped[str | None]
    # MULTIPOLYGON allows GADM regions that are archipelagos / non-contiguous
    geom: Mapped[object] = mapped_column(Geometry("GEOMETRY", srid=4326))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )


class Farmer(Base):
    __tablename__ = "farmers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("zones.id"), index=True)
    name: Mapped[str]
    phone_e164: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(10), default="en")
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    email_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )


class RiskScore(Base):
    __tablename__ = "risk_scores"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("zones.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)

    # Raw indicator values
    ndvi_mean: Mapped[float | None] = mapped_column(Float)
    ndvi_baseline_mean: Mapped[float | None] = mapped_column(Float)
    ndvi_baseline_std: Mapped[float | None] = mapped_column(Float)
    ndvi_anomaly: Mapped[float | None] = mapped_column(Float)

    rainfall_mm: Mapped[float | None] = mapped_column(Float)
    rainfall_baseline_mm: Mapped[float | None] = mapped_column(Float)
    rainfall_deficit: Mapped[float | None] = mapped_column(Float)

    soil_moisture: Mapped[float | None] = mapped_column(Float)
    soil_moisture_percentile: Mapped[float | None] = mapped_column(Float)

    # Fused output
    ndvi_risk: Mapped[float] = mapped_column(Float)
    rainfall_risk: Mapped[float] = mapped_column(Float)
    soil_moisture_risk: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, index=True)
    level: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel, name="risk_level"), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    quality: Mapped[dict] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    farmer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("farmers.id"), index=True)
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("zones.id"), index=True)
    risk_score_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risk_scores.id"))
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    threshold: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status"), index=True
    )
    provider_message_id: Mapped[str | None]
    provider_response: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )