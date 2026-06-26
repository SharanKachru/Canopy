"""Risk fusion module.

Fuses three satellite-derived hazard signals into a single 0-100 score:
  - NDVI anomaly    (z-score vs. historical baseline)    weight 0.45
  - Rainfall deficit (fraction below baseline)           weight 0.35
  - Soil moisture percentile (vs. historical same-season) weight 0.20

Weights redistribute proportionally when an input is missing.
Confidence scales with: (available weight fraction) × (mean quality of inputs).
"""

from dataclasses import dataclass

from app.models import RiskLevel


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def ndvi_anomaly_to_risk(z_score: float) -> float:
    """Z-score 0 = healthy; -2 or lower = maximal vegetation stress (100)."""
    return clamp(-z_score / 2.0 * 100.0)


def rainfall_deficit_to_risk(deficit_fraction: float) -> float:
    """Deficit fraction (0-1): 50% deficit → 100 risk; surplus → 0."""
    return clamp(deficit_fraction / 0.5 * 100.0)


def soil_moisture_percentile_to_risk(percentile: float) -> float:
    """Historical percentile: 0 = driest (100 risk); ≥40th = no risk."""
    return clamp((40.0 - percentile) / 40.0 * 100.0)


@dataclass(frozen=True)
class RiskResult:
    score: float
    level: RiskLevel
    confidence: float
    ndvi_risk: float
    rainfall_risk: float
    soil_moisture_risk: float


# Keep old name as alias for backwards compatibility
FusionResult = RiskResult


def fuse_risk(
    ndvi_anomaly: float | None,
    rainfall_deficit: float | None,
    soil_moisture_percentile: float | None,
    quality: dict[str, float] | None = None,
) -> RiskResult:
    """Fuse normalised hazards, redistributing weight when an input is missing.

    Args:
        ndvi_anomaly: Z-score deviation from historical NDVI baseline.
                      Negative = stressed vegetation.
        rainfall_deficit: (baseline_mm - observed_mm) / baseline_mm, clamped 0-1.
        soil_moisture_percentile: Historical percentile rank (0-100). Low = dry.
        quality: Optional dict of 0-1 quality weights, e.g. {"ndvi_coverage": 0.8}.

    Returns:
        RiskResult with score, level, confidence, and per-indicator risk values.
    """
    quality = quality or {}
    components: dict[str, tuple[float, float] | None] = {
        "ndvi": (ndvi_anomaly_to_risk(ndvi_anomaly), 0.45)
        if ndvi_anomaly is not None
        else None,
        "rainfall": (rainfall_deficit_to_risk(rainfall_deficit), 0.35)
        if rainfall_deficit is not None
        else None,
        "soil": (soil_moisture_percentile_to_risk(soil_moisture_percentile), 0.20)
        if soil_moisture_percentile is not None
        else None,
    }
    available = {k: v for k, v in components.items() if v is not None}

    # All-None is valid (no satellite data yet) — return zero-confidence neutral
    if not available:
        return RiskResult(
            score=0.0,
            level=RiskLevel.normal,
            confidence=0.0,
            ndvi_risk=0.0,
            rainfall_risk=0.0,
            soil_moisture_risk=0.0,
        )

    weight_sum = sum(w for _, w in available.values())
    score = sum(r * w for r, w in available.values()) / weight_sum

    # Confidence: fraction of total weight available × mean input quality
    weighted_quality = (
        sum(w * clamp(quality.get(k, 1.0), 0, 1) for k, (_, w) in available.items())
        / weight_sum
    )
    confidence = weight_sum * weighted_quality

    if score >= 70:
        level = RiskLevel.severe
    elif score >= 50:
        level = RiskLevel.warning
    elif score >= 30:
        level = RiskLevel.watch
    else:
        level = RiskLevel.normal

    return RiskResult(
        score=round(score, 1),
        level=level,
        confidence=round(confidence, 2),
        ndvi_risk=round(components["ndvi"][0], 1) if components["ndvi"] else 0.0,
        rainfall_risk=round(components["rainfall"][0], 1) if components["rainfall"] else 0.0,
        soil_moisture_risk=round(components["soil"][0], 1) if components["soil"] else 0.0,
    )
