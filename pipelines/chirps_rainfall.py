"""CHIRPS v2.0 rainfall ingestion — uses GEE batch export."""

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date

import ee

from pipelines.gee_export import build_ee_feature, export_table_to_gcs, float_or_none

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RainfallObservation:
    grid_code: str
    rainfall_mm: float | None
    rainfall_baseline_mm: float | None
    rainfall_deficit: float | None
    window_start: date
    window_end: date


def _chirps_collection(start: str, end: str, region: ee.Geometry) -> ee.ImageCollection:
    return (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterBounds(region)
        .filterDate(start, end)
        .select("precipitation")
    )


def aggregate_zones(
    zones_geojson: dict,
    start: str,
    end: str,
    baseline_start_year: int = 2001,
    baseline_end_year: int = 2023,
    scale_m: int = 5000,
) -> list[RainfallObservation]:
    features = zones_geojson["features"]
    ee_features = [build_ee_feature(f) for f in features]
    zones = ee.FeatureCollection(ee_features)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)

    current_col = _chirps_collection(start, end, zones.geometry())
    current_total = ee.Image(
        ee.Algorithms.If(
            current_col.size().gt(0),
            current_col.sum(),
            ee.Image.constant(0).rename("precipitation").selfMask(),
        )
    ).rename("rainfall_mm")

    yearly_totals = []
    for year in range(baseline_start_year, baseline_end_year + 1):
        try:
            ys = start_date.replace(year=year)
        except ValueError:
            ys = start_date.replace(year=year, day=28)
        try:
            ye = end_date.replace(year=year)
        except ValueError:
            ye = end_date.replace(year=year, day=28)
        col = _chirps_collection(ys.isoformat(), ye.isoformat(), zones.geometry())
        yearly_totals.append(ee.Algorithms.If(col.size().gt(0), col.sum(), None))

    valid_baseline = ee.ImageCollection(ee.List(yearly_totals).removeAll([None]))
    baseline_mean = valid_baseline.mean().rename("rainfall_baseline_mm")
    deficit = (
        baseline_mean.subtract(current_total)
        .divide(baseline_mean.max(1.0))
        .max(0)
        .rename("rainfall_deficit")
    )

    output = current_total.addBands([baseline_mean, deficit])
    stats_fc = output.reduceRegions(
        collection=zones,
        reducer=ee.Reducer.mean(),
        scale=scale_m,
        tileScale=4,
    )

    rows = export_table_to_gcs(stats_fc, description=f"chirps-{start}-{end}")

    results = []
    for row in rows:
        if not row.get("grid_code"):
            continue
        base_mm = float_or_none(row.get("rainfall_baseline_mm"))
        deficit_val = float_or_none(row.get("rainfall_deficit"))
        if base_mm is not None and base_mm < 1.0:
            deficit_val = 0.0
        results.append(RainfallObservation(
            grid_code=row["grid_code"],
            rainfall_mm=float_or_none(row.get("rainfall_mm")),
            rainfall_baseline_mm=base_mm,
            rainfall_deficit=deficit_val,
            window_start=start_date,
            window_end=end_date,
        ))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--zones", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    ee.Initialize(project=args.project)
    with open(args.zones, encoding="utf-8") as fh:
        zones_geojson = json.load(fh)
    for obs in aggregate_zones(zones_geojson, args.start, args.end):
        print(json.dumps(obs.__dict__, default=str))