"""The ranges audit document is generated, read-only, and never invents thresholds."""

import ranges_audit
from markers_reference import MARKER_LIBRARY


def test_committed_audit_matches_the_library():
    assert ranges_audit.DOC.read_text(encoding="utf-8") == ranges_audit.render(), (
        "docs/ranges_audit.md is stale: run `python ranges_audit.py --write`")


def test_known_missing_male_thresholds_are_reported_not_filled():
    missing = ranges_audit.missing("male")
    for name in ("Cortisol, Total (AM)", "DHEA-S", "FSH", "LH"):
        assert name in missing
        assert MARKER_LIBRARY[name]["lo"] is None and MARKER_LIBRARY[name]["hi"] is None
    text = ranges_audit.render()
    assert text.count("clinic input needed") >= len(missing)


def test_every_marker_has_a_source_for_each_sex():
    rows = ranges_audit.marker_rows()
    assert {row[0] for row in rows} == set(MARKER_LIBRARY)
    for _, _, _, male, female, _ in rows:
        for cell in (male, female):
            assert cell.startswith(("CellDeep-calibrated: ", "Lab-printed fallback", "Missing - "))
