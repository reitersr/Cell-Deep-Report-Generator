"""Regression coverage for all configurations that rely on a printed lab range."""

import pytest

import pipeline
import template
from markers_reference import MARKER_LIBRARY, has_missing_thresholds, resolve_marker_config


def _fallback_cases():
    cases = []
    for sex, bhrt in (("male", False), ("female", False), ("female", True), (None, False)):
        for name, config in MARKER_LIBRARY.items():
            if has_missing_thresholds(resolve_marker_config(name, config, sex, bhrt)):
                cases.append((name, sex, bhrt))
    return cases


@pytest.mark.parametrize(("name", "sex", "bhrt"), _fallback_cases())
def test_printed_range_with_current_and_prior_values_is_scored_and_rendered(name, sex, bhrt):
    record, _ = pipeline.score_and_build_record({
        "name": "Range Fallback Test", "sex": sex,
        "provider_note_raw": "Postmenopausal and on BHRT." if bhrt else "",
        "markers": [{
            "name": name, "then": 1.0, "now": 1.0, "disp_then": "1.0", "disp_now": "1.0",
            "lab_range_then_lo": 0.0, "lab_range_then_hi": 2.0, "lab_range_then_display": "0.0 - 2.0",
            "lab_range_now_lo": 0.0, "lab_range_now_hi": 2.0, "lab_range_now_display": "0.0 - 2.0",
        }],
    })
    marker = record.markers[0]
    assert marker.range_source == "lab"
    assert marker.now_tier == "optimal"
    assert marker.then_tier == "optimal"
    assert "Not retested" not in template.bio_row_tr(marker, {})