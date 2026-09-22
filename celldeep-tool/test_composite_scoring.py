import pytest

from dexa_reference import dexa_percent_optimized
import pipeline
import scoring
import template


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


def test_overall_then_uses_missing_symptom_redistribution_when_baseline_dexa_exists():
    """"YOU WERE" must be computed by the exact same overall_percent_optimized() function as
    "YOU ARE", fed the first-visit inputs - not a separate plain-average formula. A patient with
    a complete baseline DEXA scan but no baseline Vitality Index should route through the
    missing-Symptom 60/70 + 10/70 redistribution, not pure bloodwork_then."""
    record, _ = pipeline.score_and_build_record({
        "name": "Baseline Dexa Patient", "sex": "male", "provider_note_raw": "",
        "markers": [{"name": "hs-CRP", "then": 3.1, "now": 0.7}],
        "dexa_history": [
            {"date_display": "Jan 1, 2026", "total_mass_lb": 170.0, "fat_mass_lb": 56.0,
             "lean_mass_lb": 110.0, "body_fat_pct": "33.0%", "vat_fat_mass_lb": 2.0},
            {"date_display": "Jul 1, 2026", "total_mass_lb": 160.0, "fat_mass_lb": 46.0,
             "lean_mass_lb": 114.0, "body_fat_pct": "28.8%", "vat_fat_mass_lb": 0.8},
        ],
    })
    roll, _order, _overall_now, overall_then, _has_dexa = template.build_rollups(record, None, None, False)
    bloodwork_then = roll["Repair"]["then"]
    structure_then = roll["Structure"]["then"]
    assert bloodwork_then is not None and structure_then is not None
    expected = round(scoring.overall_percent_optimized(bloodwork_then, None, structure_then))
    assert overall_then == expected
    assert overall_then != bloodwork_then  # would be equal only by coincidence, not the correct formula


def test_overall_then_equals_bloodwork_then_with_no_baseline_dexa_or_vitality():
    """Most real patients have no baseline DEXA scan and no historical Vitality Index - for them,
    overall_percent_optimized() falls through to bloodwork alone, so overall_then must still equal
    bloodwork_then exactly (unchanged from prior behavior)."""
    record, _ = pipeline.score_and_build_record({
        "name": "No Baseline Extras Patient", "sex": "male", "provider_note_raw": "",
        "markers": [{"name": "hs-CRP", "then": 3.1, "now": 0.7}],
    })
    roll, _order, _overall_now, overall_then, has_dexa = template.build_rollups(record, None, None, False)
    assert not has_dexa
    bloodwork_then = roll["Repair"]["then"]
    assert bloodwork_then is not None
    assert overall_then == bloodwork_then


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