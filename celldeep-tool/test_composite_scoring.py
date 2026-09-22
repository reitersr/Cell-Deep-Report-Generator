import pytest

from dexa_reference import dexa_percent_optimized
import scoring


@pytest.mark.parametrize(("body_fat", "sex", "expected"), [
    (9.9, "female", 20),
    (10, "female", 75),
    (13.9, "female", 75),
    (14, "female", 96),
    (24.9, "female", 96),
    (25, "female", 50),
    (32, "female", 20),
    (1.9, "male", 20),
    (2, "male", 75),
    (17.9, "male", 96),
    (18, "male", 50),
    (25, "male", 20),
])
def test_ace_body_fat_boundaries(body_fat, sex, expected):
    assert dexa_percent_optimized(body_fat, None, sex) == expected


@pytest.mark.parametrize(("vat_area", "expected"), [(99.9, 96), (100, 50), (159.9, 50), (160, 20)])
def test_vat_area_boundaries(vat_area, expected):
    assert dexa_percent_optimized(None, vat_area, "male") == expected


def test_dexa_averages_present_metrics_only():
    assert dexa_percent_optimized("15%", 170, "male") == 58
    assert dexa_percent_optimized("15%", None, "male") == 96
    assert dexa_percent_optimized(None, None, "male") is None


def test_symptom_score_rescales_and_ignores_physical_performance():
    scores = {"energy": 0, "sleep": 1, "mental_clarity_focus": 2}
    assert scoring.symptom_percent_optimized(scores) == 50
    assert scoring.symptom_percent_optimized({**scores, "physical_performance": 2}) == 50


def test_composite_presence_branches():
    assert scoring.overall_percent_optimized(80, 50, 96) == pytest.approx(72.6)
    assert scoring.overall_percent_optimized(80, None, 96) == pytest.approx((60 / 70) * 80 + (10 / 70) * 96)
    assert scoring.overall_percent_optimized(80, 50, None) == pytest.approx((60 / 90) * 80 + (30 / 90) * 50)
    assert scoring.overall_percent_optimized(80, None, None) == 80
    with pytest.raises(ValueError, match="bloodwork_pct is required"):
        scoring.overall_percent_optimized(None, 50, 96)


def test_unassessed_and_absent_vitality_values_normalize_to_null():
    normalized = scoring.normalize_vitality_index({"Energy": "Not Assessed"})
    assert set(normalized) == set(scoring.VITALITY_DOMAINS)
    assert all(value is None for value in normalized.values())