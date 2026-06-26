"""Seed the zones table from a GADM GeoJSON FeatureCollection.

Download GADM data from https://gadm.org/download_country.html
Select a country, choose level 2 (district/tehsil), and export as GeoJSON.

Usage:
    python -m scripts.seed_zones \\
        --geojson gadm41_KEN_2.json \\
        --country-code KE

The script is idempotent — it upserts on grid_code so re-running is safe.

grid_code is derived as: {COUNTRY_ISO3}_{GID_1}_{GID_2}  (e.g. KEN_1_3)
You can override this by setting --grid-code-field to any property in the GeoJSON.
"""

import argparse
import asyncio
import json
import logging
import re
import uuid

from geoalchemy2.shape import from_shape
from shapely.geometry import mapping, shape
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Zone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


def _derive_grid_code(props: dict, country_code: str, grid_code_field: str | None) -> str:
    if grid_code_field and grid_code_field in props:
        raw = str(props[grid_code_field])
        return re.sub(r"[^A-Za-z0-9_\-]", "_", raw)[:64]
    gid1 = str(props.get("GID_1", "")).split(".")[1].lstrip("0") if "GID_1" in props else "0"
    gid2 = str(props.get("GID_2", "")).split(".")[2].lstrip("0") if "GID_2" in props else "0"
    return f"{country_code.upper()}_{gid1}_{gid2}"


async def seed(
    geojson_path: str,
    country_code: str,
    database_url: str,
    grid_code_field: str | None = None,
) -> None:
    with open(geojson_path, encoding="utf-8") as fh:
        gj = json.load(fh)

    features = gj.get("features", [])
    log.info("Loaded %d features from %s", len(features), geojson_path)

    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    inserted = updated = skipped = 0

    async with SessionLocal() as db:
        for feat in features:
            props = feat.get("properties") or {}
            geom = feat.get("geometry")
            if geom is None:
                skipped += 1
                continue

            grid_code = _derive_grid_code(props, country_code, grid_code_field)
            name = (
                props.get("NAME_2")
                or props.get("NAME_1")
                or props.get("name")
                or grid_code
            )
            admin1 = props.get("NAME_1") or props.get("admin1")
            admin2 = props.get("NAME_2") or props.get("admin2")

            try:
                shapely_geom = shape(geom)
                wkb = from_shape(shapely_geom, srid=4326)
            except Exception as exc:
                log.warning("Invalid geometry for %s: %s — skipping", grid_code, exc)
                skipped += 1
                continue

            stmt = (
                pg_insert(Zone)
                .values(
                    id=uuid.uuid4(),
                    grid_code=grid_code,
                    name=name,
                    country_code=country_code.upper(),
                    admin1=admin1,
                    admin2=admin2,
                    geom=wkb,
                )
                .on_conflict_do_update(
                    index_elements=["grid_code"],
                    set_={
                        "name": name,
                        "admin1": admin1,
                        "admin2": admin2,
                        "geom": wkb,
                    },
                )
            )
            try:
                result = await db.execute(stmt)
                if result.rowcount and result.rowcount > 0:
                    inserted += 1
                else:
                    updated += 1
            except Exception as exc:
                log.error("Failed to upsert zone %s: %s", grid_code, exc)
                skipped += 1

        await db.commit()

    await engine.dispose()
    log.info("Seeding complete — inserted=%d updated=%d skipped=%d", inserted, updated, skipped)

    # Write a minimal zones.geojson the pipeline can use directly
    out_path = "zones.geojson"
    slim_features = []
    for feat in features:
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if geom is None:
            continue
        grid_code = _derive_grid_code(props, country_code, grid_code_field)
        slim_features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {"grid_code": grid_code},
        })
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": slim_features}, fh)
    log.info("Wrote %s with %d zones", out_path, len(slim_features))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Canopy zones table from GADM GeoJSON")
    parser.add_argument("--geojson", required=True, help="Path to GADM GeoJSON file")
    parser.add_argument("--country-code", required=True, help="ISO 3166-1 alpha-2 code, e.g. KE")
    parser.add_argument(
        "--grid-code-field",
        default=None,
        help="GeoJSON property to use as grid_code (defaults to GID-derived code)",
    )
    args = parser.parse_args()
    settings = get_settings()
    asyncio.run(
        seed(
            geojson_path=args.geojson,
            country_code=args.country_code,
            database_url=settings.database_url,
            grid_code_field=args.grid_code_field,
        )
    )
