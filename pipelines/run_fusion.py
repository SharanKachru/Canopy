"""Manual fusion runner — useful for backfills and one-off re-scoring.

For regular weekly runs use worker.py instead.

Usage (dry-run, just prints scores):
    python -m pipelines.run_fusion \\
        --zones zones.geojson \\
        --start 2026-05-01 \\
        --end 2026-05-15 \\
        --project your-gcp-project \\
        --dry-run

Usage (write to DB):
    python -m pipelines.run_fusion \\
        --zones zones.geojson \\
        --start 2026-05-01 \\
        --end 2026-05-15 \\
        --project your-gcp-project
"""

import argparse
import asyncio
import json
import logging

from app.config import get_settings
from pipelines.ingest import run_ingestion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Canopy fusion pipeline for a date window")
    parser.add_argument("--zones", required=True, help="Path to zones.geojson")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD (exclusive)")
    parser.add_argument("--project", default=None, help="GCP project (falls back to GEE_PROJECT env)")
    parser.add_argument("--dry-run", action="store_true", help="Skip DB writes, just log scores")
    args = parser.parse_args()

    settings = get_settings()
    gee_project = args.project or settings.gee_project
    if not gee_project:
        raise SystemExit("ERROR: --project or GEE_PROJECT env var required")

    with open(args.zones, encoding="utf-8") as fh:
        zones_geojson = json.load(fh)

    summary = asyncio.run(
        run_ingestion(
            zones_geojson=zones_geojson,
            start=args.start,
            end=args.end,
            gee_project=gee_project,
            database_url=settings.database_url,
            dry_run=args.dry_run,
        )
    )
    print(json.dumps(summary, indent=2))
