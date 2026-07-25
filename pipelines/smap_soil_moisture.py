"""NASA SMAP L4 soil moisture ingestion — uses GEE batch export."""

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date

import ee

from pipelines.gee_export import build_ee_feature, export_table_to_gcs, float_or_none

log = logging.getLogger(__name__)

_SMAP_COLLECTION = "NASA/SMAP/SPL4SMGP/008"
_SM_BAND = "sm_surface"


@dataclass(frozen=True)
class SoilMoistureObservation:
    grid_code: str
    soil_moisture: float | None
    soil_moisture_percentile: float | None
    window_start: date
    window_end: date


def _smap_collection(start: str, end: str, region: ee.Geometry) -> ee.ImageCollection:
    return (
        ee.ImageCollection(_SMAP_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end)
        .select(_SM_BAND)
    )


def aggregate_zones(
    zones_geojson: dict,
    start: str,
    end: str,
    baseline_start_year: int = 2016,
    baseline_end_year: int = 2023,
    scale_m: int = 11000,
) -> list[SoilMoistureObservation]:
    features = zones_geojson["features"]
    ee_features = [build_ee_feature(f) for f in features]
    zones = ee.FeatureCollection(ee_features)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)

    current_col = _smap_collection(start, end, zones.geometry())
    current_mean = ee.Image(
        ee.Algorithms.If(
            current_col.size().gt(0),
            current_col.mean(),
            ee.Image.constant(0).rename(_SM_BAND).selfMask(),
        )
    ).rename("soil_moisture")

    historical_images = []
    for year in range(baseline_start_year, baseline_end_year + 1):
        try:
            ys = start_date.replace(year=year)
        except ValueError:
            ys = start_date.replace(year=year, day=28)
        try:
            ye = end_date.replace(year=year)
        except ValueError:
            ye = end_date.replace(year=year, day=28)
        col = _smap_collection(ys.isoformat(), ye.isoformat(), zones.geometry())
        historical_images.append(ee.Algorithms.If(col.size().gt(0), col.mean(), None))

    valid_historical = ee.ImageCollection(ee.List(historical_images).removeAll([None]))
    n_years = valid_historical.size()
    rank_sum = valid_historical.map(
        lambda img: ee.Image(img).lte(current_mean).rename("below")
    ).sum()
    percentile = rank_sum.divide(n_years).multiply(100).rename("soil_moisture_percentile")

    output = current_mean.addBands(percentile)
    stats_fc = output.reduceRegions(
        collection=zones,
        reducer=ee.Reducer.mean(),
        scale=scale_m,
        tileScale=4,
    )

    rows = export_table_to_gcs(stats_fc, description=f"smap-{start}-{end}")

    return [
        SoilMoistureObservation(
            grid_code=row["grid_code"],
            soil_moisture=float_or_none(row.get("soil_moisture")),
            soil_moisture_percentile=float_or_none(row.get("soil_moisture_percentile")),
            window_start=start_date,
            window_end=end_date,
        )
        for row in rows
        if row.get("grid_code")
    ]


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