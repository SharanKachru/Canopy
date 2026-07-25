"""NDVI aggregation using MODIS MOD13A2 (1km, 16-day composite).

Replaces Sentinel-2 which was too slow for district-scale India runs.
MODIS is pre-computed, no cloud masking needed, jobs finish in 2-3 minutes.
Geometries simplified locally before sending to GEE to avoid 10MB payload limit.
"""

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta

import ee

from pipelines.gee_export import build_ee_feature, export_table_to_gcs, float_or_none, int_or_zero

log = logging.getLogger(__name__)

_MODIS_COLLECTION = "MODIS/061/MOD13A2"
_NDVI_BAND = "NDVI"
_SCALE_FACTOR = 0.0001


@dataclass(frozen=True)
class NdviObservation:
    grid_code: str
    ndvi_mean: float | None
    ndvi_baseline_mean: float | None
    ndvi_baseline_std: float | None
    ndvi_anomaly: float | None
    clear_pixel_count: int
    window_start: date
    window_end: date


def initialize_ee(project: str) -> None:
    ee.Initialize(project=project)


def _modis_ndvi_collection(start: str, end: str, region: ee.Geometry) -> ee.ImageCollection:
    return (
        ee.ImageCollection(_MODIS_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end)
        .select(_NDVI_BAND)
        .map(lambda img: img.multiply(_SCALE_FACTOR)
             .rename("ndvi")
             .copyProperties(img, ["system:time_start"]))
    )


def _safe_mean(collection: ee.ImageCollection) -> ee.Image:
    placeholder = ee.Image.constant(0).rename("ndvi").selfMask().toFloat()
    return ee.Image(
        ee.Algorithms.If(collection.size().gt(0), collection.mean(), placeholder)
    )


def aggregate_zones(
    zones_geojson: dict,
    start: str,
    end: str,
    baseline_start_year: int = 2018,
    baseline_end_year: int = 2023,
    scale_m: int = 1000,
    test_zones: int | None = None,
) -> list[NdviObservation]:
    all_features = zones_geojson["features"]
    features = all_features[:test_zones] if test_zones else all_features
    log.info("Processing %d zones with MODIS NDVI at %dm scale", len(features), scale_m)

    # Simplify geometries locally before sending to GEE — fixes 10MB payload limit
    ee_features = [build_ee_feature(f) for f in features]
    zones = ee.FeatureCollection(ee_features)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)

    extended_end = (date.fromisoformat(end) + timedelta(days=16)).isoformat()
    current = _safe_mean(
        _modis_ndvi_collection(start, extended_end, zones.geometry())
    ).rename("ndvi_mean")

    yearly_composites = []
    for year in range(baseline_start_year, baseline_end_year + 1):
        try:
            hs = start_date.replace(year=year)
        except ValueError:
            hs = start_date.replace(year=year, day=28)
        try:
            he = end_date.replace(year=year) + timedelta(days=16)
        except ValueError:
            he = end_date.replace(year=year, day=28) + timedelta(days=16)
        yearly_composites.append(
            _safe_mean(_modis_ndvi_collection(hs.isoformat(), he.isoformat(), zones.geometry()))
        )

    baseline = ee.ImageCollection(yearly_composites)
    baseline_mean = baseline.mean().rename("ndvi_baseline_mean")
    baseline_std = baseline.reduce(ee.Reducer.stdDev()).rename("ndvi_baseline_std")
    anomaly = (
        current.subtract(baseline_mean)
        .divide(baseline_std.max(0.02))
        .rename("ndvi_anomaly")
    )

    output = current.addBands([baseline_mean, baseline_std, anomaly])
    stats_fc = output.reduceRegions(
        collection=zones,
        reducer=ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True),
        scale=scale_m,
        tileScale=4,
    )

    rows = export_table_to_gcs(stats_fc, description=f"ndvi-{start}-{end}")
    if rows:
      log.info("NDVI CSV columns: %s", list(rows[0].keys()))

    return [
        NdviObservation(
            grid_code=row["grid_code"],
            ndvi_mean=float_or_none(row.get("ndvi_mean_mean")),
            ndvi_baseline_mean=float_or_none(row.get("ndvi_baseline_mean_mean")),
            ndvi_baseline_std=float_or_none(row.get("ndvi_baseline_std_mean")),
            ndvi_anomaly=float_or_none(row.get("ndvi_anomaly_mean")),
            clear_pixel_count=int_or_zero(row.get("ndvi_mean_count")),
            window_start=start_date,
            window_end=end_date,
        )
        for row in rows
        if row.get("grid_code")
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zones", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--baseline-start-year", type=int, default=2018)
    parser.add_argument("--baseline-end-year", type=int, default=2023)
    parser.add_argument("--test-zones", type=int, default=None)
    args = parser.parse_args()
    initialize_ee(args.project)
    with open(args.zones, encoding="utf-8") as fh:
        zones_geojson = json.load(fh)
    for obs in aggregate_zones(
        zones_geojson, args.start, args.end,
        args.baseline_start_year, args.baseline_end_year,
        test_zones=args.test_zones,
    ):
        print(json.dumps(obs.__dict__, default=str))


if __name__ == "__main__":
    main()