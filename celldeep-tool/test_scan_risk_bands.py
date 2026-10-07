"""Scanned risk-category pages (Optimal / Moderate / High columns) and microscopy counts: a result is consistent
with the band its flag or pill points to, never tested against the Optimal band as if it were the lab's normal
range; whole-number count ranges ("0-5", "6-10") are text results. Real contradictions are still excluded.
Invented values."""

import pytest

import pipeline
import scan_bloodwork as scan


def _row(value, flag=None, reference=None, column="in_range"):
    return {"name": "TEST", "result_text": value, "flag": flag, "reference_range": reference, "lab_code": None,
            "column": column, "page": 1, "illegible": False, "section": "LIPIDS"}


@pytest.mark.parametrize("value,flag,reference", [
    ("3.4", "H", ">3.0"),      # hs-CRP in the printed High band
    ("214", "H", "≥200"),      # total cholesterol at/above ">=200"
    ("200", "H", ">= 200"),
    ("139", "H", "≥130"),
    ("27.1", "L", "<29.2"),    # HDL-P in the printed low (High risk) band
])
def test_a_value_in_the_band_its_flag_points_to_is_consistent(value, flag, reference):
    assert scan._numeric_flag(_row(value, flag, reference, "out_of_range"), pipeline._CELL_VALUE_RE)


def test_a_non_optimal_pill_with_no_letter_is_consistent_with_the_optimal_band():
    assert scan._numeric_flag(_row("214", None, "<200", "out_of_range"), pipeline._CELL_VALUE_RE)


@pytest.mark.parametrize("row", [
    _row("2.4", "H", ">3.0", "out_of_range"),     # flagged H but below the High band
    _row("214", "L", "≥200", "out_of_range"),     # flag points the other way
    _row("214", None, "<200", "in_range"),        # an in-range result outside its range
    _row("50", "H", "10-100", "out_of_range"),    # H inside an ordinary normal range
    _row("1.0", None, ">=5", "in_range"),
])
def test_real_contradictions_are_still_excluded(row):
    assert not scan._numeric_flag(row, pipeline._CELL_VALUE_RE)


def test_count_ranges_are_text_results_but_a_copied_reference_range_is_not():
    for value in ("0-5", "0-2", "6-10", "6 - 10"):
        assert scan.is_text_result(value, "<=5")
    assert not scan.is_text_result("0-5", "0-5")       # the row's own reference range copied into the result
    assert not scan.is_text_result("0.40-4.50", None)  # a decimal range is never a count
    assert scan.is_text_result("Trace") and scan.is_text_result("1+") and not scan.is_text_result("12.4")


def test_the_staff_identified_gate_keeps_agreeing_count_and_band_rows():
    reads = [[{"page": 1, "specimen_id": None, "collected": None, "footer": None, "patient_name": None,
               "date_of_birth": None, "illegible": False, "out_of_range_summary": None,
               "rows": [dict(_row("0-5", None, "≤5"), name="WBC", section="URINALYSIS"),
                        dict(_row("3.4", "H", ">3.0", "out_of_range"), name="hs-CRP")]}] * 2]
    accepted, notes = scan.gate_staff_identified_reads(reads, "01/02/2026", pipeline._CELL_VALUE_RE, "Synthetic, Pat")
    assert sorted(row["result_text"] for row in accepted) == ["0-5", "3.4"]
