import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import alerts, risk_scores, zones

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Canopy API starting up")
    yield
    log.info("Canopy API shutting down")


app = FastAPI(
    title="Canopy API",
    version="0.1.0",
    description=(
        "Satellite-based agricultural early warning. "
        "Detects crop stress 2–6 weeks before it is visible on the ground."
    ),
    lifespan=lifespan,
)

app.include_router(zones.router, prefix="/api/v1")
app.include_router(risk_scores.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")


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
