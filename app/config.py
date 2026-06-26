from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://canopy:canopy@localhost:5432/canopy"

    # ── Google Earth Engine ───────────────────────────────────────────────────
    gee_project: str = ""           # GCP project ID with Earth Engine enabled

    # ── WhatsApp Cloud API ────────────────────────────────────────────────────
    whatsapp_api_version: str = "v23.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_template_name: str = "canopy_risk_alert"
    whatsapp_template_language: str = "en"

    # ── Alert thresholds ──────────────────────────────────────────────────────
    alert_threshold: int = 70       # risk score (0-100) that triggers an alert
    alert_cooldown_hours: int = 168  # 7 days between repeat alerts for same zone

    # ── Scheduler ─────────────────────────────────────────────────────────────
    schedule_day_of_week: str = "mon"
    schedule_hour: int = 2
    schedule_minute: int = 0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
