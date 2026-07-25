from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://canopy:canopy@localhost:5432/canopy"

    # ── Supabase REST API ─────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # ── Google Earth Engine ───────────────────────────────────────────────────
    gee_project: str = ""

    # ── Resend Email API ──────────────────────────────────────────────────────
    resend_api_key: str = ""
    resend_from_email: str = "alerts@canopy.example.com"

    # ── Alert thresholds ──────────────────────────────────────────────────────
    alert_threshold: int = 70
    alert_cooldown_hours: int = 168

    # ── Scheduler ─────────────────────────────────────────────────────────────
    schedule_day_of_week: str = "mon"
    schedule_hour: int = 2
    schedule_minute: int = 0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()