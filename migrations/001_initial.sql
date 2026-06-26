-- Canopy initial schema migration
-- Run against a Supabase (PostgreSQL 15+) database.
-- PostGIS must be enabled: CREATE EXTENSION IF NOT EXISTS postgis;

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Enums ──────────────────────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE risk_level AS ENUM ('normal', 'watch', 'warning', 'severe');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE alert_status AS ENUM ('pending', 'sent', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── zones ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS zones (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    grid_code       VARCHAR(64) NOT NULL UNIQUE,
    name            TEXT,
    country_code    CHAR(2)  NOT NULL,
    admin1          TEXT,
    admin2          TEXT,
    -- GEOMETRY allows both POLYGON and MULTIPOLYGON (non-contiguous GADM regions)
    geom            GEOMETRY(GEOMETRY, 4326) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS zones_country_code_idx  ON zones (country_code);
CREATE INDEX IF NOT EXISTS zones_geom_idx          ON zones USING GIST (geom);

-- ── farmers ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS farmers (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zone_id             UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    phone_e164          VARCHAR(20) NOT NULL UNIQUE,
    preferred_language  VARCHAR(10) NOT NULL DEFAULT 'en',
    whatsapp_opt_in     BOOLEAN NOT NULL DEFAULT FALSE,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS farmers_zone_id_idx ON farmers (zone_id);

-- ── risk_scores ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS risk_scores (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zone_id                 UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    observed_at             TIMESTAMPTZ NOT NULL,
    window_start            DATE NOT NULL,
    window_end              DATE NOT NULL,

    -- Raw satellite indicators
    ndvi_mean               REAL,
    ndvi_baseline_mean      REAL,
    ndvi_baseline_std       REAL,
    ndvi_anomaly            REAL,

    rainfall_mm             REAL,
    rainfall_baseline_mm    REAL,
    rainfall_deficit        REAL,   -- (baseline - observed) / baseline, 0..1

    soil_moisture           REAL,   -- m³/m³
    soil_moisture_percentile REAL,  -- 0-100

    -- Fused risk outputs
    ndvi_risk               REAL NOT NULL DEFAULT 0,
    rainfall_risk           REAL NOT NULL DEFAULT 0,
    soil_moisture_risk      REAL NOT NULL DEFAULT 0,
    score                   REAL NOT NULL,          -- 0-100
    level                   risk_level NOT NULL,
    confidence              REAL NOT NULL,          -- 0-1

    quality                 JSONB NOT NULL DEFAULT '{}',
    model_version           VARCHAR(32) NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS risk_scores_zone_id_idx     ON risk_scores (zone_id);
CREATE INDEX IF NOT EXISTS risk_scores_observed_at_idx ON risk_scores (observed_at DESC);
CREATE INDEX IF NOT EXISTS risk_scores_level_idx       ON risk_scores (level);
CREATE INDEX IF NOT EXISTS risk_scores_score_idx       ON risk_scores (score DESC);

-- Latest score per zone (very common query pattern)
CREATE INDEX IF NOT EXISTS risk_scores_zone_observed_idx
    ON risk_scores (zone_id, observed_at DESC);

-- ── alerts ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    farmer_id           UUID NOT NULL REFERENCES farmers(id) ON DELETE CASCADE,
    zone_id             UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    risk_score_id       UUID NOT NULL REFERENCES risk_scores(id) ON DELETE CASCADE,
    channel             VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
    threshold           INTEGER NOT NULL,
    message             TEXT NOT NULL,
    status              alert_status NOT NULL DEFAULT 'pending',
    provider_message_id TEXT,
    provider_response   JSONB,
    error               TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    sent_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS alerts_farmer_id_idx  ON alerts (farmer_id);
CREATE INDEX IF NOT EXISTS alerts_zone_id_idx    ON alerts (zone_id);
CREATE INDEX IF NOT EXISTS alerts_status_idx     ON alerts (status);
CREATE INDEX IF NOT EXISTS alerts_sent_at_idx    ON alerts (sent_at DESC);

COMMIT;
