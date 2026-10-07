"""The synthetic fixtures that stand in, in CI, for the clinic's three verified patient types (Quest layout, Access
Medical Laboratories limited male, Cleveland HeartLab extensive male): each reproduces its layout's structure with
invented values, and its expected report data (synthetic_fixtures/expected/*.json) holds the structures' outcomes.
These tests fail if a fixture stops exercising a structure; test_regression_check.py fails on any changed value.
Also: an unrecognized test is listed once in the staff notes with all its values and dates."""

import json
from pathlib import Path

import pytest

from synthetic_fixtures import access_medical_lab, chl_extensive
from synthetic_fixtures.scenarios import run_scenario
from unknown_marker_policy import ExtractionReviewNotice, UnrecognizedMarker, format_review_notice

EXPECTED = Path(__file__).parent / "synthetic_fixtures" / "expected"


def _rows(name):
    return json.loads((EXPECTED / f"{name}.json").read_text())["rows"]


@pytest.fixture(scope="module")
def extensive(tmp_path_factory):
    return run_scenario("chl_extensive", tmp_path_factory.mktemp("chl_extensive"))


def test_every_verified_patient_type_has_a_synthetic_fixture_with_score_outputs():
    for name in ("quest_digital", "access_medical_limited", "chl_extensive"):
        rows = _rows(name)
        assert any(row.startswith("M |") for row in rows)
        assert any(row.startswith("S |") for row in rows) and any(row.startswith("O |") for row in rows)
        assert any(row.startswith("D |") for row in rows) or name == "quest_digital"


def test_the_cleveland_heartlab_fixture_exercises_its_layout(extensive):
    rows = set(_rows("chl_extensive"))
    # Historical columns with attached flags; the draw's own report wins over a later Historical copy.
    assert {"M | Lp-PLA2 Activity | 07/08/2025 | 60 | L | optimal",
            "M | Lp-PLA2 Activity | 11/04/2025 | 429.6 | H | history"} <= rows
    # Threshold columns are never results.
    thresholds = {t for row in chl_extensive.RISK_ROWS for t in row[3]}
    assert not any(f"| {t} |" in row for row in rows for t in thresholds)
    # Unknown-fasting draw: glucose and insulin lab-reported, never scored; the fasting draw is scored.
    assert {"L | Chemistry | Glucose (non-fasting) | 03/10/2026 | 95 |  | 65-99",
            "L | Hormones | Insulin (non-fasting) | 03/10/2026 | 6.1 |  | 2.0-19.6",
            "M | Glucose (fasting) | 11/04/2025 | 88 |  | optimal"} <= rows
    # Changed assay: the dialysis draw is lab-reported with its own range; the basis-assay draw is scored.
    assert {"L | Hormones | Free Testosterone | 03/10/2026 | 120 |  | 35-155",
            "M | Free Testosterone | 11/04/2025 | 95 |  | optimal"} <= rows
    # DEXA: printed, computed and "(e)" body fat; the regional Android Fat value never becomes fat mass.
    assert {"D | 04/14/2025 | total 205.6 | fat 68.2 | lean 130.1 | body fat 33.2% (printed) | VAT lb 4.12 | VAT cm2 ",
            "D | 10/20/2025 | total 190.2 | fat 51.7 | lean 131 | body fat 28.3% (computed) | VAT lb 2.95 | VAT cm2 ",
            "D | 03/02/2026 | total 182.4 | fat 44.6 | lean 130.4 | body fat 24.5% (estimated) | VAT lb 2.61 | VAT cm2 "
            } <= rows
    review = extensive["review"]
    assert "TREND PAGES NOT READ: lab PDF page(s) 6" in review
    assert "LAB RESULT EXCLUDED page 2" not in review  # "/ /" Historical slots hold nothing and raise nothing
    assert "'12.34.5' runs result digits together" in review  # one unreadable cell, only that cell left out


def test_the_access_fixture_prints_a_summary_block_that_is_ignored():
    assert any(item[2] == "OUT OF RANGE SUMMARY" for page in access_medical_lab.limited_pages() for item in page
               if isinstance(item, tuple) and len(item) == 3)
    assert any(row.startswith("L | ") and "(non-fasting)" in row for row in _rows("access_medical_limited"))


def test_an_unrecognized_test_is_listed_once_with_every_value_and_date(extensive):
    lines = [line for line in extensive["review"].splitlines() if "Unrecognized marker \"Chol/HDL-C\"" in line]
    assert len(lines) == 1
    assert "(4.1 ratio on 07/08/2025; 2.7 ratio on 11/04/2025; 3.3 ratio on 03/10/2026)" in lines[0]


def test_the_grouped_line_keeps_units_ranges_flags_and_where_the_values_went():
    def cell(date, value, flag=None):
        return {"date_display": date, "disp_value": value, "lab_flag": flag, "present": True}

    notice = ExtractionReviewNotice(unrecognized_markers=[
        UnrecognizedMarker("TG/HDL-C", "3.7", "ratio", "<2.0", cells=[cell("05/06/2024", "3.7", "H")],
                           shown_as_lab_reported=True),
        UnrecognizedMarker("TG/HDL-C", "1.9 | 3.7", "ratio", "<2.0", cells=[
            cell("09/10/2024", "1.9"), cell("05/06/2024", "3.7", "H"), {"date_display": "01/01/2025", "present": False,
                                                                       "disp_value": ""}], shown_as_lab_reported=True),
        UnrecognizedMarker("TG/HDL-C", "2.4", "ratio", "<2.5", cells=[cell("01/14/2025", "2.4")],
                           shown_as_lab_reported=True),
        UnrecognizedMarker("Novel Assay", "7", None, None)])
    text = format_review_notice(notice)
    tg = [line for line in text.splitlines() if "\"TG/HDL-C\"" in line]
    assert tg == ["  - Unrecognized marker \"TG/HDL-C\" (3.7 H ratio on 05/06/2024; 1.9 ratio on 09/10/2024; 2.4 ratio "
                  "on 01/14/2025), reference ranges on source: <2.0 / <2.5 — shown in the report as lab-reported, as "
                  "printed and not scored. Add an alias to score it or group it in future reports."]
    assert "  - Unrecognized marker \"Novel Assay\" (7) — not included in this report." in text
