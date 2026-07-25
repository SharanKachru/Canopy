"""Full ingestion pipeline: GEE → risk fusion → RiskScore rows via Supabase REST API.

Uses Supabase Python client instead of SQLAlchemy direct connection,
bypassing DNS issues with direct PostgreSQL connections.
"""

import argparse
import json
import logging
import uuid
from datetime import UTC, datetime, date

import ee
from supabase import create_client, Client

from app.config import get_settings
from app.risk import fuse_risk
from pipelines import chirps_rainfall, sentinel2_ndvi, smap_soil_moisture

log = logging.getLogger(__name__)

MODEL_VERSION = "fusion-v1"


def get_supabase_client(settings) -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def run_ingestion(
    zones_geojson: dict,
    start: str,
    end: str,
    gee_project: str,
    settings,
    dry_run: bool = False,
    test_zones: int | None = None,
) -> dict:
    log.info("Initialising Earth Engine project=%s", gee_project)
    ee.Initialize(project=gee_project)

    features = zones_geojson["features"]
    if test_zones:
        features = features[:test_zones]
        log.info("TEST MODE — processing first %d zones only", test_zones)

    total = len(features)
    log.info("Processing %d zones", total)
    batch = {"type": "FeatureCollection", "features": features}

    ndvi_by_code: dict = {}
    rain_by_code: dict = {}
    sm_by_code: dict = {}

    try:
        log.info("Fetching NDVI (MODIS) via batch export…")
        for obs in sentinel2_ndvi.aggregate_zones(batch, start, end):
            ndvi_by_code[obs.grid_code] = obs
        log.info("NDVI done — %d zones", len(ndvi_by_code))
    except Exception as exc:
        log.error("NDVI failed: %s", exc)

    try:
        log.info("Fetching rainfall (CHIRPS) via batch export…")
        for obs in chirps_rainfall.aggregate_zones(batch, start, end):
            rain_by_code[obs.grid_code] = obs
        log.info("CHIRPS done — %d zones", len(rain_by_code))
    except Exception as exc:
        log.error("Rainfall failed: %s", exc)

    try:
        log.info("Fetching soil moisture (SMAP) via batch export…")
        for obs in smap_soil_moisture.aggregate_zones(batch, start, end):
            sm_by_code[obs.grid_code] = obs
        log.info("SMAP done — %d zones", len(sm_by_code))
    except Exception as exc:
        log.error("Soil moisture failed: %s", exc)

    all_codes = {f["properties"]["grid_code"] for f in features}
    inserted = skipped = failed = 0

    # Dry-run: skip DB entirely
    if dry_run:
        for grid_code in all_codes:
            ndvi = ndvi_by_code.get(grid_code)
            rain = rain_by_code.get(grid_code)
            sm = sm_by_code.get(grid_code)
            ndvi_anomaly = ndvi.ndvi_anomaly if ndvi else None
            rainfall_deficit = rain.rainfall_deficit if rain else None
            sm_percentile = sm.soil_moisture_percentile if sm else None
            if ndvi_anomaly is None and rainfall_deficit is None and sm_percentile is None:
                skipped += 1
                continue
            try:
                quality = {
                    "ndvi_clear_pixels": ndvi.clear_pixel_count if ndvi else 0,
                    "ndvi_coverage": min(1.0, (ndvi.clear_pixel_count or 0) / 1000) if ndvi else 0.0,
                }
                fused = fuse_risk(
                    ndvi_anomaly=ndvi_anomaly,
                    rainfall_deficit=rainfall_deficit,
                    soil_moisture_percentile=sm_percentile,
                    quality=quality,
                )
                log.info("[DRY RUN] %s score=%.1f level=%s confidence=%.2f ndvi_anom=%s rain_deficit=%s sm_pct=%s",
         grid_code, fused.score, fused.level, fused.confidence,
         ndvi_anomaly, rainfall_deficit, sm_percentile)
                inserted += 1
            except Exception as exc:
                log.error("Risk fusion failed for zone %s: %s", grid_code, exc)
                failed += 1
        summary = {"inserted": inserted, "skipped": skipped, "failed": failed}
        log.info("Dry-run complete: %s", summary)
        return summary

    # Real run: use Supabase REST API
    supabase = get_supabase_client(settings)

    # Fetch zone IDs from Supabase
    log.info("Fetching zone IDs from Supabase...")
    zone_response = supabase.table("zones").select("id,grid_code").execute()
    zone_id_map = {z["grid_code"]: z["id"] for z in zone_response.data}
    log.info("Found %d zones in DB", len(zone_id_map))

    window_start = date.fromisoformat(start).isoformat()
    window_end = date.fromisoformat(end).isoformat()
    now = datetime.now(UTC).isoformat()

    rows_to_insert = []
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
            skipped += 1
            continue

        try:
            quality = {
                "ndvi_clear_pixels": ndvi.clear_pixel_count if ndvi else 0,
                "ndvi_coverage": min(1.0, (ndvi.clear_pixel_count or 0) / 1000) if ndvi else 0.0,
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

        rows_to_insert.append({
            "id": str(uuid.uuid4()),
            "zone_id": zone_id_map[grid_code],
            "observed_at": now,
            "window_start": window_start,
            "window_end": window_end,
            "ndvi_mean": ndvi.ndvi_mean if ndvi else None,
            "ndvi_baseline_mean": ndvi.ndvi_baseline_mean if ndvi else None,
            "ndvi_baseline_std": ndvi.ndvi_baseline_std if ndvi else None,
            "ndvi_anomaly": ndvi_anomaly,
            "rainfall_mm": rain.rainfall_mm if rain else None,
            "rainfall_baseline_mm": rain.rainfall_baseline_mm if rain else None,
            "rainfall_deficit": rainfall_deficit,
            "soil_moisture": sm.soil_moisture if sm else None,
            "soil_moisture_percentile": sm_percentile,
            "ndvi_risk": fused.ndvi_risk,
            "rainfall_risk": fused.rainfall_risk,
            "soil_moisture_risk": fused.soil_moisture_risk,
            "score": fused.score,
            "level": fused.level.value if hasattr(fused.level, 'value') else str(fused.level),
            "confidence": fused.confidence,
            "quality": quality,
            "model_version": MODEL_VERSION,
            "created_at": now,
        })

    # Batch insert in chunks of 100
    CHUNK_SIZE = 100
    for i in range(0, len(rows_to_insert), CHUNK_SIZE):
        chunk = rows_to_insert[i:i + CHUNK_SIZE]
        try:
            supabase.table("risk_scores").insert(chunk).execute()
            inserted += len(chunk)
            log.info("Inserted %d/%d rows", min(i + CHUNK_SIZE, len(rows_to_insert)), len(rows_to_insert))
        except Exception as exc:
            log.error("Batch insert failed: %s", exc)
            failed += len(chunk)

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
    parser.add_argument("--zones", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-zones", type=int, default=None)
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
            settings=settings,
            dry_run=args.dry_run,
            test_zones=args.test_zones,
        )
    )
    print(json.dumps(summary))