"""Sentinel-2 Surface Reflectance NDVI aggregation for PostGIS zones."""

import argparse
import json
from dataclasses import dataclass
from datetime import date

import ee


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
    # Uses Application Default Credentials or credentials created by `earthengine authenticate`.
    ee.Initialize(project=project)


def mask_sentinel2_clouds(image: ee.Image) -> ee.Image:
    """Mask cloud/cirrus using SCL and the Cloud Probability collection."""
    scl = image.select("SCL")
    scl_clear = (
        scl.neq(3)  # cloud shadow
        .And(scl.neq(8))  # cloud, medium probability
        .And(scl.neq(9))  # cloud, high probability
        .And(scl.neq(10))  # cirrus
        .And(scl.neq(11))  # snow/ice
    )
    probability = ee.Image(image.get("cloud_mask")).select("probability")
    return image.updateMask(scl_clear.And(probability.lt(40)))


def ndvi_collection(start: str, end: str, region: ee.Geometry) -> ee.ImageCollection:
    sr = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", 80))
    )
    clouds = (
        ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY")
        .filterBounds(region)
        .filterDate(start, end)
    )
    joined = ee.ImageCollection(
        ee.Join.saveFirst("cloud_mask").apply(
            primary=sr,
            secondary=clouds,
            condition=ee.Filter.equals(leftField="system:index", rightField="system:index"),
        )
    )
    return joined.map(mask_sentinel2_clouds).map(
        lambda image: image.normalizedDifference(["B8", "B4"]).rename("ndvi").copyProperties(
            image, ["system:time_start"]
        )
    )


def aggregate_zones(
    zones_geojson: dict,
    start: str,
    end: str,
    baseline_start_year: int = 2018,
    baseline_end_year: int = 2023,
    scale_m: int = 20,
) -> list[NdviObservation]:
    """Compute median-window NDVI per zone; intended for modest batches.

    For country-scale runs, export the reduceRegions result to Cloud Storage/BigQuery
    with ee.batch.Export.table rather than calling getInfo().
    """
    features = [
        ee.Feature(ee.Geometry(feature["geometry"]), {"grid_code": feature["properties"]["grid_code"]})
        for feature in zones_geojson["features"]
    ]
    zones = ee.FeatureCollection(features)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    current = ndvi_collection(start, end, zones.geometry()).median().rename("ndvi_mean")

    # Compare with the same calendar window in prior years to avoid confusing
    # normal seasonality with crop stress. Pin Feb 29 to Feb 28 in non-leap years.
    yearly_composites = []
    for year in range(baseline_start_year, baseline_end_year + 1):
        try:
            historical_start = start_date.replace(year=year)
        except ValueError:
            historical_start = start_date.replace(year=year, day=28)
        try:
            historical_end = end_date.replace(year=year)
        except ValueError:
            historical_end = end_date.replace(year=year, day=28)
        yearly_composites.append(
            ndvi_collection(
                historical_start.isoformat(), historical_end.isoformat(), zones.geometry()
            ).median()
        )
    baseline = ee.ImageCollection(yearly_composites)
    baseline_mean = baseline.mean().rename("ndvi_baseline_mean")
    baseline_std = baseline.reduce(ee.Reducer.stdDev()).rename("ndvi_baseline_std")
    anomaly = current.subtract(baseline_mean).divide(
        baseline_std.max(0.02)  # Stabilize nearly invariant pixels.
    ).rename("ndvi_anomaly")
    output = current.addBands([baseline_mean, baseline_std, anomaly])

    stats = output.reduceRegions(
        collection=zones,
        reducer=ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True),
        scale=scale_m,
        tileScale=4,
    ).getInfo()
    return [
        NdviObservation(
            grid_code=item["properties"]["grid_code"],
            ndvi_mean=item["properties"].get("ndvi_mean"),
            ndvi_baseline_mean=item["properties"].get("ndvi_baseline_mean"),
            ndvi_baseline_std=item["properties"].get("ndvi_baseline_std"),
            ndvi_anomaly=item["properties"].get("ndvi_anomaly"),
            clear_pixel_count=item["properties"].get("ndvi_mean_count", 0),
            window_start=start_date,
            window_end=end_date,
        )
        for item in stats["features"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zones", required=True, help="GeoJSON FeatureCollection with grid_code")
    parser.add_argument("--start", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Exclusive YYYY-MM-DD")
    parser.add_argument("--project", required=True)
    parser.add_argument("--baseline-start-year", type=int, default=2018)
    parser.add_argument("--baseline-end-year", type=int, default=2023)
    args = parser.parse_args()
    initialize_ee(args.project)
    with open(args.zones, encoding="utf-8") as handle:
        zones_geojson = json.load(handle)
    for observation in aggregate_zones(
        zones_geojson,
        args.start,
        args.end,
        args.baseline_start_year,
        args.baseline_end_year,
    ):
        print(json.dumps(observation.__dict__, default=str))


if __name__ == "__main__":
    main()
