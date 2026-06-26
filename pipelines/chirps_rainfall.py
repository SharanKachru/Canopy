"""CHIRPS v2.0 daily rainfall ingestion via Google Earth Engine.

CHIRPS (Climate Hazards Group InfraRed Precipitation with Station data) provides
~5 km rainfall estimates from 1981 to near-present, updated with ~2-week lag.

Outputs rainfall_mm (observed) and rainfall_deficit (fraction of baseline missing).
Deficit formula: (baseline_mm - observed_mm) / baseline_mm, clamped to [0, 1].
Negative deficits (surplus) are returned as 0.
"""

import json
from dataclasses import dataclass
from datetime import date

import ee


@dataclass(frozen=True)
class RainfallObservation:
    grid_code: str
    rainfall_mm: float | None
    rainfall_baseline_mm: float | None
    rainfall_deficit: float | None          # (baseline - observed) / baseline, 0..1
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
    """Compute total rainfall per zone and compare to same-period climatological mean."""
    features = [
        ee.Feature(
            ee.Geometry(f["geometry"]),
            {"grid_code": f["properties"]["grid_code"]},
        )
        for f in zones_geojson["features"]
    ]
    zones = ee.FeatureCollection(features)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)

    # Current window total
    current_total = (
        _chirps_collection(start, end, zones.geometry())
        .sum()
        .rename("rainfall_mm")
    )

    # Baseline: same calendar window across prior years
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
        yearly_totals.append(
            _chirps_collection(ys.isoformat(), ye.isoformat(), zones.geometry()).sum()
        )
    baseline_mean = ee.ImageCollection(yearly_totals).mean().rename("rainfall_baseline_mm")

    # Deficit fraction: (baseline - observed) / baseline, floor at 0
    deficit = (
        baseline_mean.subtract(current_total)
        .divide(baseline_mean.max(1.0))   # avoid /0 in arid zones
        .max(0)
        .rename("rainfall_deficit")
    )

    output = current_total.addBands([baseline_mean, deficit])
    stats = output.reduceRegions(
        collection=zones,
        reducer=ee.Reducer.mean(),
        scale=scale_m,
        tileScale=4,
    ).getInfo()

    results = []
    for item in stats["features"]:
        p = item["properties"]
        obs_mm = p.get("rainfall_mm")
        base_mm = p.get("rainfall_baseline_mm")
        deficit_val = p.get("rainfall_deficit")
        # If baseline is ~0 (hyper-arid), treat as no deficit
        if base_mm is not None and base_mm < 1.0:
            deficit_val = 0.0
        results.append(
            RainfallObservation(
                grid_code=p["grid_code"],
                rainfall_mm=obs_mm,
                rainfall_baseline_mm=base_mm,
                rainfall_deficit=deficit_val,
                window_start=start_date,
                window_end=end_date,
            )
        )
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CHIRPS rainfall aggregation")
    parser.add_argument("--zones", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--baseline-start-year", type=int, default=2001)
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
