import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.routers import alerts, farmers, risk_scores, zones

log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Canopy API starting up")
    yield
    log.info("Canopy API shutting down")


app = FastAPI(
    title="Canopy API",
    version="0.1.0",
    description="Satellite-based agricultural early warning.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(zones.router, prefix="/api/v1")
app.include_router(risk_scores.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(farmers.router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(PROJECT_ROOT / "dashboard.html")


@app.get("/signup", include_in_schema=False)
async def signup_page():
    return FileResponse(PROJECT_ROOT / "signup.html")


@app.get("/zones.geojson", include_in_schema=False)
async def zone_boundaries():
    return FileResponse(
        PROJECT_ROOT / "zones.geojson",
        media_type="application/geo+json",
    )


@app.get("/health", tags=["operations"])
async def health():
    return {"status": "ok"}


@app.get("/api/v1/info", tags=["operations"])
async def info():
    return {
        "service": "canopy",
        "version": "0.1.0",
        "docs": "/docs",
    }
