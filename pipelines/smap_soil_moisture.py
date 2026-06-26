"""NASA SMAP L4 soil moisture ingestion via Google Earth Engine.

SMAP L4 (SPL4SMGP) provides 3-hourly surface and rootzone soil moisture at 9 km,
with ~2-day latency. We use the surface soil moisture band and compute a historical
percentile so the output is scale-independent across soil types.

Outputs soil_moisture (raw m³/m³) and soil_moisture_percentile (0–100).
Percentile 0 = driest on record for that zone/season; 100 = wettest.
"""

import json
from dataclasses import dataclass
from datetime import date

import ee


@dataclass(frozen=True)
class SoilMoistureObservation:
    grid_code: str
    soil_moisture: float | None             # m³/m³ surface layer
    soil_moisture_percentile: float | None  # 0–100, vs. historical same-season window
    window_start: date
    window_end: date


_SMAP_COLLECTION = "NASA/SMAP/SPL4SMGP/007"
_SM_BAND = "sm_surface"


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
    baseline_start_year: int = 2015,
    baseline_end_year: int = 2023,
    scale_m: int = 11000,
) -> list[SoilMoistureObservation]:
    """Compute mean soil moisture per zone and its historical percentile rank."""
    features = [
        ee.Feature(
            ee.Geometry(f["geometry"]),
            {"grid_code": f["properties"]["grid_code"]},
        )
        for f in zones_geojson["features"]
    ]
    zones = ee.FeatureCollection(features)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)

    current_mean = (
        _smap_collection(start, end, zones.geometry())
        .mean()
        .rename("soil_moisture")
    )

    # Build historical images for the same calendar window
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
        historical_images.append(
            _smap_collection(ys.isoformat(), ye.isoformat(), zones.geometry()).mean()
        )

    historical_stack = ee.ImageCollection(historical_images)
    n_years = baseline_end_year - baseline_start_year + 1

    # Percentile rank: count how many historical years are <= current, normalised to 0..100
    def count_below(hist_image: ee.Image) -> ee.Image:
        return hist_image.lte(current_mean).rename("below")

    rank_sum = historical_stack.map(count_below).sum()
    percentile = rank_sum.divide(n_years).multiply(100).rename("soil_moisture_percentile")

    output = current_mean.addBands(percentile)
    stats = output.reduceRegions(
        collection=zones,
        reducer=ee.Reducer.mean(),
        scale=scale_m,
        tileScale=4,
    ).getInfo()

    return [
        SoilMoistureObservation(
            grid_code=item["properties"]["grid_code"],
            soil_moisture=item["properties"].get("soil_moisture"),
            soil_moisture_percentile=item["properties"].get("soil_moisture_percentile"),
            window_start=start_date,
            window_end=end_date,
        )
        for item in stats["features"]
    ]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SMAP soil moisture aggregation")
    parser.add_argument("--zones", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--baseline-start-year", type=int, default=2015)
    parser.add_argument("--baseline-end-year", type=int, default=2023)
    args = parser.parse_args()

    ee.Initialize(project=args.project)
    with open(args.zones, encoding="utf-8") as fh:
        zones_geojson = json.load(fh)
    for obs in aggregate_zones(
        zones_geojson,
        args.start,
        args.end,
        args.baseline_start_year,
        args.baseline_end_year,
    ):
        print(json.dumps(obs.__dict__, default=str))
