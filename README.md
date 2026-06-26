# Canopy starter

Canopy turns Earth-observation and weather indicators into zone-level drought alerts.

## Architecture

```text
Sentinel-2 / CHIRPS / SMAP / ERA5
              |
        ingestion jobs
              |
      normalized indicators
              |
          risk fusion
              |
     PostgreSQL + PostGIS
          |         |
      FastAPI   alert service -> WhatsApp Cloud API
          |
    React analyst dashboard
```

This starter implements the API, database schema, Sentinel-2 NDVI ingestion,
risk fusion, and WhatsApp delivery. In production, run ingestion as a scheduled
worker (Cloud Run Job, Kubernetes CronJob, or Celery), not inside the API process.

## Quick start

1. Copy `.env.example` to `.env` and fill in credentials.
2. Start PostGIS: `docker compose up -d db`.
3. Create a virtual environment and install: `pip install -e .[dev]`.
4. Apply the schema: `psql "$DATABASE_URL" -f migrations/001_initial.sql`.
5. Start the API: `uvicorn app.main:app --reload`.
6. Open `http://localhost:8000/docs`.

For Earth Engine, authenticate locally with `earthengine authenticate`; in a
deployed job, use a service account that has Earth Engine access and set
`GOOGLE_APPLICATION_CREDENTIALS` and `GEE_PROJECT`.

The NDVI job builds a cloud-masked current-window composite, compares it with
the same seasonal window across baseline years, and emits current mean,
baseline mean/standard deviation, anomaly z-score, and valid pixel count. Run:

`python -m pipelines.sentinel2_ndvi --zones zones.geojson --start 2026-06-01 --end 2026-06-15 --project your-project`

## Risk interpretation

- `0–29`: normal
- `30–49`: watch
- `50–69`: warning
- `70–100`: severe

An alert is created only on an upward threshold crossing (previous score below
the threshold, new score at or above it). A database uniqueness constraint also
prevents duplicate messages for the same farmer, score, and threshold.
