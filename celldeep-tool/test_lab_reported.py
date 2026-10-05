"""Lab-reported (not scored) results and the no-silent-drop coverage guarantee. Synthetic data only."""

import fitz
import pytest

import lab_reported
import pipeline
import template
from synthetic_fixtures import deterministic_fixtures as fx

DATE = "04/14/2026"


def _panel_pdf(tmp_path):
    page = (fx.section_preamble("SYN-LAB-1", DATE) + fx.header_line(100)
            + fx.row(130, "hs-CRP", "0.4", units="mg/L", lab_range="0.0-3.0")
            + fx.row(144, "C-Reactive Protein", "2.1", units="mg/L", lab_range="0.0-8.0")
            + fx.row(158, "Hemoglobin", "17.6 H", units="g/dL", lab_range="13.2-17.1")
            + fx.row(172, "Hematocrit", "52.3H", units="%", lab_range="39.4-51.1")
            + fx.row(186, "Sodium", "140", units="mmol/L", lab_range="135-146")
            + fx.row(200, "Novel Synthetic Assay", "9.9 H", units="U/L", lab_range="1.0-5.0")
            + fx.row(214, "Unflagged Synthetic Assay", "3.0", units="U/L", lab_range="1.0-5.0")
            + fx.row(228, "Troponin T", "0.01", units="ng/mL"))
    urine = (fx.section_preamble("SYN-LAB-2", DATE) + fx.header_line(100) + [(40, 116, "URINALYSIS")]
             + fx.row(130, "Glucose", "NEGATIVE") + fx.row(144, "WBC", "2"))
    return fx.write_lab_pdf(tmp_path / "panel.pdf", [page, urine])


@pytest.fixture
def extracted(tmp_path):
    return pipeline.extract(str(_panel_pdf(tmp_path)), [], None, patient_name=fx.PATIENT,
                            audit_root=str(tmp_path))


def _items(extracted):
    return {item["name"]: item for item in extracted["lab_reported"]}


def test_listed_tests_show_printed_value_range_and_lab_flag(extracted):
    items = _items(extracted)
    assert items["Hemoglobin"]["results"] == [
        {"date_display": DATE, "disp_value": "17.6", "lab_flag": "H", "lab_range": "13.2-17.1"}]
    assert items["Hematocrit"]["results"][0]["lab_flag"] == "H"
    assert items["Sodium"]["results"][0]["lab_flag"] is None
    assert items["Hemoglobin"]["group"] == "Complete Blood Count"


def test_crp_and_hs_crp_stay_separate_tests(extracted):
    scored = {item["name"] for item in extracted["marker_occurrences"]}
    assert "hs-CRP" in scored
    assert _items(extracted)["C-Reactive Protein"]["results"][0]["disp_value"] == "2.1"


def test_urinalysis_rows_never_map_to_serum(extracted):
    items = _items(extracted)
    assert items["Urinalysis — Glucose"]["results"][0]["disp_value"] == "NEGATIVE"
    assert "WBC" not in items and "White Blood Cell Count" not in items
    assert not any(item["name"] == "Glucose (fasting)" for item in extracted["marker_occurrences"])


def test_any_lab_flagged_result_is_shown_and_still_reviewed(extracted):
    items = _items(extracted)
    assert items["Novel Synthetic Assay"]["group"] == lab_reported.OTHER_FLAGGED
    staff = {item["raw_name"] for item in extracted["unrecognized_markers"]}
    assert "Novel Synthetic Assay" in staff  # shown to the patient AND queued for the library
    assert "Unflagged Synthetic Assay" in staff and "Unflagged Synthetic Assay" not in items


def test_conventional_troponin_is_not_mapped_to_high_sensitivity_troponin(extracted):
    assert pipeline._match_row_name("Troponin T", None) is None
    assert pipeline._match_row_name("Troponin", None) is None
    assert pipeline._match_row_name("Troponin T, High Sensitivity (hs-TnT)", None)[0] == "Troponin T, HS"
    assert "Troponin T" in {item["raw_name"] for item in extracted["unrecognized_markers"]}


def test_patient_pdf_shows_lab_reported_section_without_scores(extracted, tmp_path):
    record, notice = pipeline.score_and_build_record({**extracted, "sex": "male"})
    assert not any("COVERAGE GAP" in note for note in notice.other_notes)
    out = tmp_path / "report.pdf"
    template.render(record, pipeline.build_copy(record), str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    section = text[text.index("LAB-REPORTED RESULTS, NOT SCORED"):]
    for expected in ("Hemoglobin", "17.6 H", "13.2-17.1", "Novel Synthetic Assay", "9.9 H",
                     "C-Reactive Protein", "Urinalysis — Glucose", "NEGATIVE"):
        assert expected in section, expected
    html = out.with_suffix(".html").read_text(encoding="utf-8")
    html_section = html[html.index('class="lab-reported"'):]
    assert not any(word in html_section for word in ("Optimal", "Moderate", "Flagged", "bio-tierchip"))
    assert {marker.name for marker in record.markers}.isdisjoint({item.name for item in record.lab_reported})


def test_coverage_gap_is_reported_when_a_printed_row_reaches_neither_output(extracted):
    record, _ = pipeline.score_and_build_record(extracted)
    record.lab_reported = [item for item in record.lab_reported if item.name != "Sodium"]
    gaps = pipeline.coverage_gaps(extracted["source_rows"], record, set())
    assert any("'Sodium'" in gap for gap in gaps)
    record.markers = [marker for marker in record.markers if marker.name != "hs-CRP"]
    assert any("'hs-CRP'" in gap for gap in pipeline.coverage_gaps(extracted["source_rows"], record, set()))


def test_conflicting_lab_reported_results_on_one_date_are_not_shown():
    rows = [{"raw_name": "Sodium", "raw_range": "135-146", "section_heading": None,
             "cells": [{"date_display": DATE, "disp_value": value, "present": True, "status": "final",
                        "lab_flag": None}]} for value in ("140", "141")]
    items, remaining, notes = lab_reported.build(rows)
    assert items == [] and remaining == []
    assert len(notes) == 1 and notes[0].startswith("LAB-REPORTED CONFLICT: 'Sodium'")


def test_scanned_flags_reach_the_lab_reported_section(tmp_path):
    from test_scan_bloodwork import GOLDEN, MockClient, payloads  # noqa: F401
    path = fx.write_lab_pdf(tmp_path / "mixed.pdf", [
        fx.section_preamble("SYN-SCAN-DIGITAL", "02/03/2026") + fx.header_line(100)
        + fx.row(130, "TSH", "1.0"), [], []])
    reads = [page for page in payloads() for _ in (1, 2)]
    extracted = pipeline.extract(str(path), [], None, patient_name="Synthetic, Pat", collected_date=DATE,
                                 client=MockClient(reads), audit_root=str(tmp_path))
    items = _items(extracted)
    assert items["Hemoglobin"]["results"][0] == {"date_display": DATE, "disp_value": "17.6", "lab_flag": "H",
                                                 "lab_range": "13.2-17.1"}
    assert items["Absolute Lymphocytes"]["results"][0]["lab_flag"] == "L"
    flagged = {name for name, _, flag, _ in GOLDEN if flag}
    shown = {name.casefold() for item in extracted["lab_reported"] for name in item["printed_names"]}
    scored = {item["name"] for item in extracted["marker_occurrences"]}
    for name in flagged:
        assert name.casefold() in shown or pipeline._match_row_name(name, "ROUTINE PANELS")[0] in scored, name


def test_all_caps_text_result_row_is_not_mistaken_for_a_section_heading(tmp_path):
    """Found by the Quest-style fixture: 'COLOR  YELLOW  YELLOW' (all caps, no numbers) ended the
    URINALYSIS section, so urine GLUCOSE NEGATIVE was read as serum glucose."""
    from synthetic_fixtures import layouts
    path = layouts.quest_digital(tmp_path / "quest.pdf")
    extracted = pipeline.extract(str(path), [], None, patient_name=fx.PATIENT, audit_root=str(tmp_path))
    items = _items(extracted)
    assert items["Urinalysis — Color"]["results"][0]["disp_value"] == "YELLOW"
    assert items["Urinalysis — Glucose"]["results"][0]["disp_value"] == "NEGATIVE"
    glucose = [item for item in extracted["marker_occurrences"] if item["name"] == "Glucose (fasting)"]
    assert [item["disp_value"] for item in glucose] == ["82"]
    words = [(40, 100, 70, 110, "COLOR"), (230, 100, 260, 110, "YELLOW"), (380, 100, 410, 110, "YELLOW")]
    assert not pipeline._is_single_phrase(words)
    assert pipeline._is_single_phrase([(40, 100, 90, 110, "CBC"), (93, 100, 160, 110, "(INCLUDES DIFF/PLT)")])
