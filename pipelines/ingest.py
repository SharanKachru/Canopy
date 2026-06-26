"""Full ingestion pipeline: GEE → risk fusion → RiskScore rows in PostgreSQL.

This is the central orchestration module. It:
1. Fetches NDVI anomaly from Sentinel-2 (via GEE)
2. Fetches rainfall deficit from CHIRPS (via GEE)
3. Fetches soil moisture percentile from SMAP (via GEE)
4. Fuses all three signals using risk.py
5. Upserts RiskScore rows to PostgreSQL (Supabase)

Run directly:
    python -m pipelines.ingest \\
        --zones zones.geojson \\
        --start 2026-06-01 \\
        --end 2026-06-15 \\
        --project your-gcloud-project

Or import run_ingestion() from the scheduler (worker.py).
"""

import argparse
import json
import logging
import uuid
from datetime import UTC, datetime, date

import ee
import sqlalchemy
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import RiskScore, Zone
from app.risk import fuse_risk
from pipelines import chirps_rainfall, sentinel2_ndvi, smap_soil_moisture

log = logging.getLogger(__name__)

MODEL_VERSION = "fusion-v1"


def _build_engine(database_url: str):
    return create_async_engine(database_url, pool_pre_ping=True)


async def run_ingestion(
    zones_geojson: dict,
    start: str,
    end: str,
    gee_project: str,
    database_url: str,
    dry_run: bool = False,
) -> dict:
    """Fetch all indicators, fuse risk, and persist RiskScore rows.

    Returns a summary dict with counts of inserted/skipped/failed zones.
    """
    log.info("Initialising Earth Engine project=%s", gee_project)
    ee.Initialize(project=gee_project)

    log.info("Fetching NDVI anomalies (Sentinel-2)…")
    ndvi_obs = sentinel2_ndvi.aggregate_zones(zones_geojson, start, end)
    ndvi_by_code = {o.grid_code: o for o in ndvi_obs}

    log.info("Fetching rainfall deficit (CHIRPS)…")
    rain_obs = chirps_rainfall.aggregate_zones(zones_geojson, start, end)
    rain_by_code = {o.grid_code: o for o in rain_obs}

    log.info("Fetching soil moisture percentile (SMAP)…")
    sm_obs = smap_soil_moisture.aggregate_zones(zones_geojson, start, end)
    sm_by_code = {o.grid_code: o for o in sm_obs}

    all_codes = {f["properties"]["grid_code"] for f in zones_geojson["features"]}

    engine = _build_engine(database_url)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    inserted = skipped = failed = 0

    async with SessionLocal() as db:
        # Pre-fetch zone id map: grid_code → uuid
        zone_rows = (await db.scalars(select(Zone))).all()
        zone_id_map: dict[str, uuid.UUID] = {z.grid_code: z.id for z in zone_rows}
        missing_zones = all_codes - zone_id_map.keys()
        if missing_zones:
            log.warning(
                "Zones in GeoJSON not found in DB (run seed_zones.py first): %s",
                missing_zones,
            )

        window_start = date.fromisoformat(start)
        window_end = date.fromisoformat(end)
        now = datetime.now(UTC)

        for grid_code in all_codes:
            if grid_code not in zone_id_map:
                skipped += 1
                continue

            ndvi = ndvi_by_code.get(grid_code)
            rain = rain_by_code.get(grid_code)
            sm = sm_by_code.get(grid_code)

            ndvi_anomaly = ndvi.ndvi_anomaly if ndvi else None
            rainfall_deficit = rain.rainfall_deficit if rain else None
            sm_percentile = sm.soil_moisture_percentile if sm else None

            if ndvi_anomaly is None and rainfall_deficit is None and sm_percentile is None:
                log.warning("No indicators available for zone %s — skipping", grid_code)
                skipped += 1
                continue

            try:
                quality = {
                    "ndvi_clear_pixels": ndvi.clear_pixel_count if ndvi else 0,
                    "ndvi_coverage": min(1.0, (ndvi.clear_pixel_count or 0) / 1000)
                    if ndvi else 0.0,
                }
                fused = fuse_risk(
                    ndvi_anomaly=ndvi_anomaly,
                    rainfall_deficit=rainfall_deficit,
                    soil_moisture_percentile=sm_percentile,
                    quality=quality,
                )
            except Exception as exc:
                log.error("Risk fusion failed for zone %s: %s", grid_code, exc)
                failed += 1
                continue

            if dry_run:
                log.info(
                    "[DRY RUN] %s score=%.1f level=%s confidence=%.2f",
                    grid_code, fused.score, fused.level, fused.confidence,
                )
                inserted += 1
                continue

            score_row = RiskScore(
                id=uuid.uuid4(),
                zone_id=zone_id_map[grid_code],
                observed_at=now,
                window_start=window_start,
                window_end=window_end,
                ndvi_mean=ndvi.ndvi_mean if ndvi else None,
                ndvi_baseline_mean=ndvi.ndvi_baseline_mean if ndvi else None,
                ndvi_baseline_std=ndvi.ndvi_baseline_std if ndvi else None,
                ndvi_anomaly=ndvi_anomaly,
                rainfall_mm=rain.rainfall_mm if rain else None,
                rainfall_baseline_mm=rain.rainfall_baseline_mm if rain else None,
                rainfall_deficit=rainfall_deficit,
                soil_moisture=sm.soil_moisture if sm else None,
                soil_moisture_percentile=sm_percentile,
                ndvi_risk=fused.ndvi_risk,
                rainfall_risk=fused.rainfall_risk,
                soil_moisture_risk=fused.soil_moisture_risk,
                score=fused.score,
                level=fused.level,
                confidence=fused.confidence,
                quality=quality,
                model_version=MODEL_VERSION,
                created_at=now,
            )
            try:
                db.add(score_row)
                await db.commit()
                log.info(
                    "Inserted RiskScore for %s: score=%.1f level=%s",
                    grid_code, fused.score, fused.level,
                )
                inserted += 1
            except sqlalchemy.exc.IntegrityError:
                await db.rollback()
                log.info("Duplicate risk score for %s at %s — skipping", grid_code, now)
                skipped += 1
            except Exception as exc:
                await db.rollback()
                log.error("DB write failed for zone %s: %s", grid_code, exc)
                failed += 1

    await engine.dispose()
    summary = {"inserted": inserted, "skipped": skipped, "failed": failed}
    log.info("Ingestion complete: %s", summary)
    return summary


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run full Canopy ingestion pipeline")
    parser.add_argument("--zones", required=True, help="GeoJSON FeatureCollection path")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD exclusive")
    parser.add_argument("--project", required=True, help="GCP project with Earth Engine access")
    parser.add_argument("--dry-run", action="store_true", help="Skip DB writes")
    args = parser.parse_args()

    settings = get_settings()
    with open(args.zones, encoding="utf-8") as fh:
        zones_geojson = json.load(fh)

    summary = asyncio.run(
        run_ingestion(
            zones_geojson=zones_geojson,
            start=args.start,
            end=args.end,
            gee_project=args.project or settings.gee_project,
            database_url=settings.database_url,
            dry_run=args.dry_run,
        )
    )
    print(json.dumps(summary))
