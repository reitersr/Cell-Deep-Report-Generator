"""Alias matching: printed thyroid names, whitespace and line breaks never cause a miss, lookalike characters are
folded for scanned-page names only (and only when exactly one alias matches), and one test is never shown twice for
the same draw date. Invented values."""

import markers_reference as mr
import pipeline


def test_the_cleveland_heartlab_thyroid_names_are_aliases():
    assert mr.lookup_marker("Thyroxine (T4), Total")[0] == "Total T4"
    assert mr.lookup_marker("Triiodothyronine (T3), Total")[0] == "Total T3"
    assert pipeline._match_row_name("Thyroxine (T4), Total", "THYROID FUNCTION")[0] == "Total T4"


def test_wrapped_names_never_miss():
    for printed in ("TMAO (Trimethylamine N-\noxide)", "TMAO  (Trimethylamine N-oxide)", "Thyroxine (T4),\nTotal",
                    "Thyroxine ( T4 ), Total"):
        assert mr.lookup_marker(printed) is not None, printed
        assert pipeline._match_row_name(printed, None) is not None, printed


def test_lookalike_characters_are_folded_for_scanned_names_only_when_one_alias_matches(monkeypatch):
    assert mr.lookup_marker("Trilodothyronine (T3), Total") is None  # exact matching never folds
    assert pipeline._ocr_folded_name("Trilodothyronine (T3), Total", "THYROID FUNCTION") == "Total T3"
    assert pipeline._ocr_folded_name("C0Q10", None) == "CoQ10"
    assert pipeline._ocr_folded_name("Total T3", None) is None  # an exact match needs no folding
    assert pipeline._ocr_folded_name("Unheard-of Assay", None) is None
    monkeypatch.setattr(mr, "ocr_folded_matches", lambda name: {"Total T3", "Free T3"})
    assert pipeline._ocr_folded_name("Trilodothyronine (T3), Total", None) is None  # two candidates: never a guess


def test_one_test_is_never_shown_twice_for_one_draw_date():
    occurrences = [{"name": "Total T4", "date_display": "03/04/2025", "value": 7.1, "disp_value": "7.1"}]
    items = [{"name": "Thyroxine (T4), Total", "group": "THYROID FUNCTION", "printed_names": ["Thyroxine (T4), Total"],
              "results": [{"date_display": "03/04/2025", "disp_value": "7.1"},
                          {"date_display": "09/09/2025", "disp_value": "6.8"}]},
             {"name": "Urinalysis — Glucose", "group": "Urinalysis", "printed_names": ["Glucose"],
              "results": [{"date_display": "03/04/2025", "disp_value": "Negative"}]}]
    notes = pipeline.drop_duplicate_lab_reported(items, occurrences)
    assert [r["date_display"] for r in items[0]["results"]] == ["09/09/2025"]
    assert items[1]["results"]  # urine glucose is not blood glucose
    assert notes == ["DUPLICATE NOT SHOWN: Thyroxine (T4), Total '7.1' on 03/04/2025 is the same test as Total T4 on "
                     "that date, which is shown once"]
