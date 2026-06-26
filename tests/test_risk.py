"""Unit tests for the risk fusion module."""

import pytest

from app.risk import RiskResult, fuse_risk
from app.models import RiskLevel


# ── happy path ────────────────────────────────────────────────────────────

def test_healthy_inputs_are_low_risk():
    result = fuse_risk(ndvi_anomaly=0.2, rainfall_deficit=-0.1, soil_moisture_percentile=60)
    assert result.score == 0
    assert result.level == RiskLevel.normal
    assert result.confidence == 1.0


def test_severe_dry_signals_are_high_risk():
    result = fuse_risk(ndvi_anomaly=-2.0, rainfall_deficit=0.5, soil_moisture_percentile=0)
    assert result.score == 100
    assert result.level == RiskLevel.severe


def test_moderate_stress_is_watch_or_warning():
    result = fuse_risk(ndvi_anomaly=-1.0, rainfall_deficit=0.3, soil_moisture_percentile=20)
    assert result.level in (RiskLevel.watch, RiskLevel.warning)
    assert 30 <= result.score <= 80


# ── missing inputs ────────────────────────────────────────────────────────

def test_missing_soil_moisture_redistributes_weight_and_lowers_confidence():
    result = fuse_risk(ndvi_anomaly=-2.0, rainfall_deficit=0.5, soil_moisture_percentile=None)
    assert result.score == 100
    assert result.confidence == pytest.approx(0.8)


def test_missing_rainfall_still_produces_valid_score():
    result = fuse_risk(ndvi_anomaly=-1.5, rainfall_deficit=None, soil_moisture_percentile=10)
    assert 0 <= result.score <= 100
    assert result.confidence < 1.0


def test_all_inputs_none_returns_zero_confidence():
    result = fuse_risk(ndvi_anomaly=None, rainfall_deficit=None, soil_moisture_percentile=None)
    assert result.score == 0
    assert result.confidence == 0.0
    assert result.level == RiskLevel.normal


# ── boundary values ───────────────────────────────────────────────────────

def test_score_is_clamped_to_0_100():
    result = fuse_risk(ndvi_anomaly=-99.0, rainfall_deficit=1.0, soil_moisture_percentile=0)
    assert 0 <= result.score <= 100


def test_barely_above_threshold_is_watch():
    # Slight NDVI decline only — should not reach warning
    result = fuse_risk(ndvi_anomaly=-0.3, rainfall_deficit=0.05, soil_moisture_percentile=45)
    assert result.level in (RiskLevel.normal, RiskLevel.watch)


def test_quality_weight_reduces_score():
    """Low clear-pixel coverage should reduce effective score."""
    high_q = fuse_risk(
        ndvi_anomaly=-1.5,
        rainfall_deficit=0.4,
        soil_moisture_percentile=5,
        quality={"ndvi_coverage": 1.0},
    )
    low_q = fuse_risk(
        ndvi_anomaly=-1.5,
        rainfall_deficit=0.4,
        soil_moisture_percentile=5,
        quality={"ndvi_coverage": 0.1},
    )
    assert high_q.score >= low_q.score


# ── result structure ──────────────────────────────────────────────────────

def test_result_has_expected_fields():
    result = fuse_risk(ndvi_anomaly=0.0, rainfall_deficit=0.0, soil_moisture_percentile=50)
    assert isinstance(result, RiskResult)
    assert hasattr(result, "ndvi_risk")
    assert hasattr(result, "rainfall_risk")
    assert hasattr(result, "soil_moisture_risk")
    assert hasattr(result, "score")
    assert hasattr(result, "level")
    assert hasattr(result, "confidence")
