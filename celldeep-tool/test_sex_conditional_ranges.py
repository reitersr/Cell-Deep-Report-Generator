"""Verification for sex-conditional Testosterone defaults + explicit provider-note overrides.

Exercises pipeline.score_and_build_record directly (the deterministic layer) since the
never-infer discipline for both sex defaults and overrides is enforced there, not in the AI
extraction call itself.
"""

from pathlib import Path

from pipeline import score_and_build_record


def _testosterone_marker(record):
    return next(m for m in record.markers if m.name == "Testosterone, Total")


def test_male_default_range():
    extracted = {
        "name": "Male Default Patient", "sex": "male",
        "markers": [{"name": "Testosterone, Total", "now": 650, "disp_now": "650"}],
    }
    record, _ = score_and_build_record(extracted)
    marker = _testosterone_marker(record)
    assert (marker.lo, marker.hi) == (500, 900)
    assert marker.now_tier == "optimal"
    print("PASS: male default range (500-900) applied")


def test_female_default_range_unchanged():
    extracted = {
        "name": "Female Default Patient", "sex": "female",
        "markers": [{"name": "Testosterone, Total", "now": 24, "disp_now": "24"}],
    }
    record, _ = score_and_build_record(extracted)
    marker = _testosterone_marker(record)
    assert (marker.lo, marker.hi) == (2, 45)
    print("PASS: female default range (2-45) unchanged")


def test_explicit_override_used_and_logged():
    log_path = Path("/tmp/extraction_completeness_log.txt")
    log_path.unlink(missing_ok=True)
    extracted = {
        "name": "Override Patient", "sex": "male",
        "markers": [{"name": "Testosterone, Total", "now": 500, "disp_now": "500"}],
        "marker_overrides": [{"marker": "Testosterone, Total", "lo": 400, "hi": 600}],
    }
    record, _ = score_and_build_record(extracted)
    marker = _testosterone_marker(record)
    assert (marker.lo, marker.hi) == (400, 600)
    log = log_path.read_text(encoding="utf-8")
    assert "OVERRIDE APPLIED: Testosterone, Total range set to 400-600 per provider note" in log
    print("PASS: explicit override applied and logged")


def test_vague_mention_no_override():
    log_path = Path("/tmp/extraction_completeness_log.txt")
    log_path.unlink(missing_ok=True)
    # Simulates extraction correctly NOT emitting an override for a vague note mention
    # ("keep an eye on his testosterone") - marker_overrides key is simply absent/empty.
    extracted = {
        "name": "Vague Mention Patient", "sex": "male",
        "markers": [{"name": "Testosterone, Total", "now": 650, "disp_now": "650"}],
        "marker_overrides": [],
    }
    record, _ = score_and_build_record(extracted)
    marker = _testosterone_marker(record)
    assert (marker.lo, marker.hi) == (500, 900)
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert "OVERRIDE APPLIED" not in log
    print("PASS: vague note mention produces no override; default male range applies")


def test_sex_abbreviation_resolves_to_male():
    # Real source material sometimes states sex as a single letter ("M") rather than "male".
    extracted = {
        "name": "Abbreviated Sex Patient", "sex": "M",
        "markers": [{"name": "Testosterone, Total", "now": 650, "disp_now": "650"}],
    }
    record, _ = score_and_build_record(extracted)
    marker = _testosterone_marker(record)
    assert (marker.lo, marker.hi) == (500, 900)
    print("PASS: sex abbreviation 'M' resolves to the male default range")


def test_missing_threshold_excludes_marker_without_crashing():
    log_path = Path("/tmp/extraction_completeness_log.txt")
    log_path.unlink(missing_ok=True)
    from markers_reference import MARKER_LIBRARY
    broken_name = "__Broken Test Marker__"
    MARKER_LIBRARY[broken_name] = dict(category="Hormones", unit="ng/dL", kind="bounded",
        direction="lower", optimal=None, moderate=100, disp_range="broken", aliases=["broken test marker"])
    try:
        extracted = {
            "name": "Broken Marker Patient", "sex": "male",
            "markers": [
                {"name": broken_name, "now": 50, "disp_now": "50"},
                {"name": "Testosterone, Total", "now": 650, "disp_now": "650"},
            ],
        }
        record, notice = score_and_build_record(extracted)  # must not raise
        assert all(m.name != broken_name for m in record.markers)
        assert _testosterone_marker(record) is not None
        log = log_path.read_text(encoding="utf-8")
        assert f"ERROR: missing threshold for {broken_name} / male - marker excluded from scoring" in log
        assert any("missing threshold" in note for note in notice.other_notes)
        print("PASS: marker with a missing threshold is excluded and logged, report still completes")
    finally:
        del MARKER_LIBRARY[broken_name]


def main():
    test_male_default_range()
    test_female_default_range_unchanged()
    test_explicit_override_used_and_logged()
    test_vague_mention_no_override()
    test_sex_abbreviation_resolves_to_male()
    test_missing_threshold_excludes_marker_without_crashing()


if __name__ == "__main__":
    main()
