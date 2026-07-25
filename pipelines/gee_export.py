"""
Shared GEE batch export utility for Canopy pipelines.

Replaces .getInfo() with ee.batch.Export.table.toCloudStorage(),
which runs on Google's backend with no client-side timeout and a
much larger quota — suitable for all 676 Indian districts at once.
"""

import csv
import io
import json
import logging
import time
import uuid

import ee
from google.cloud import storage
from shapely.geometry import shape, mapping
from shapely.validation import make_valid

log = logging.getLogger(__name__)

GCS_BUCKET = "canopy-gee-exports"
POLL_INTERVAL_S = 15
MAX_WAIT_S = 7200  # 2 hours


def simplify_feature(feature: dict, tolerance: float = 0.01) -> dict:
    """
    Simplify a GeoJSON feature's geometry locally using Shapely.
    tolerance=0.01 degrees ≈ 1km — invisible at district scale.
    Reduces payload size by ~80%, fixing the 10MB GEE API limit.
    """
    try:
        geom = shape(feature["geometry"])
        geom = make_valid(geom)
        geom = geom.simplify(tolerance, preserve_topology=True)
        return {**feature, "geometry": mapping(geom)}
    except Exception as e:
        log.warning("Could not simplify geometry for feature: %s", e)
        return feature


def build_ee_feature(feature: dict) -> ee.Feature:
    """Build an EE Feature from a simplified GeoJSON feature."""
    simplified = simplify_feature(feature)
    return ee.Feature(
        ee.Geometry(simplified["geometry"]),
        {"grid_code": feature["properties"]["grid_code"]},
    )


def export_table_to_gcs(
    feature_collection: ee.FeatureCollection,
    description: str,
    folder: str = "canopy",
) -> list[dict]:
    """
    Export a GEE FeatureCollection to GCS as CSV and return rows as dicts.

    Steps:
    1. Submit ee.batch.Export.table.toCloudStorage job
    2. Poll until COMPLETED (or raise on FAILED/CANCELLED)
    3. Download CSV from GCS and parse into list[dict]
    4. Delete the temp CSV from GCS
    """
    file_prefix = f"{folder}/{description}-{uuid.uuid4().hex[:8]}"

    task = ee.batch.Export.table.toCloudStorage(
        collection=feature_collection,
        description=description,
        bucket=GCS_BUCKET,
        fileNamePrefix=file_prefix,
        fileFormat="CSV",
    )
    task.start()
    log.info("GEE batch job submitted: %s (task id: %s)", description, task.id)

    waited = 0
    while waited < MAX_WAIT_S:
        status = task.status()
        state = status["state"]
        if state == "COMPLETED":
            log.info("GEE job completed: %s", description)
            break
        elif state in ("FAILED", "CANCELLED"):
            raise RuntimeError(
                f"GEE export job {description} ended with state={state}: "
                f"{status.get('error_message', 'no message')}"
            )
        log.info("GEE job %s state=%s, waiting %ds...", description, state, POLL_INTERVAL_S)
        time.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
    else:
        task.cancel()
        raise TimeoutError(f"GEE export job {description} did not complete within {MAX_WAIT_S}s")

    gcs = storage.Client()
    bucket = gcs.bucket(GCS_BUCKET)
    blob_name = f"{file_prefix}.csv"
    blob = bucket.blob(blob_name)
    csv_bytes = blob.download_as_bytes()

    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))

    try:
        blob.delete()
    except Exception:
        pass

    return rows


def float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def int_or_zero(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0