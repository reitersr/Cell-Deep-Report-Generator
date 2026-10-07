"""Extensive male panel (Cleveland HeartLab layout, three draws in one PDF), round 1 fixes. Synthetic fixtures only
(synthetic_fixtures/chl_multi_draw.py); every name, value, date and range is invented.

A1 an unreadable cell excludes only that cell; A2 repeated column-header lines are ignored silently; B3 Historical
cells with attached flags, censored values, count ranges and words; B4 a full report wins over a later Historical
column (warning only on a mismatch); C5/C6 the latest-draw guard and the draw dates on the stop screen; D7 fasting per
draw; E8 cortisol per draw; F9 computed body fat when the printed % is contested; F10 VAT area from the trend table;
G11-14 aliases, panel-scoped albumin/globulin, unaliased rows under the lab's heading, urine microscopy text;
H15 assay change across draws; I16 the sparse-panel line."""

import re

import fitz
import pytest

import clinic_config
import lab_reported
import pipeline
import scan_dexa
import scoring
from synthetic_fixtures import chl_multi_draw as chl
from synthetic_fixtures import deterministic_fixtures as fx


@pytest.fixture(scope="module")
def extracted(tmp_path_factory):
    folder = tmp_path_factory.mktemp("chl")
    return pipeline.extract(str(chl.write(folder / "labs.pdf")), [], None, patient_name=fx.PATIENT,
                            audit_root=str(folder), collected_date=chl.LATEST, sex="male")


def _run(folder, pages=None, collected=chl.LATEST, confirm=None, monkeypatch=None):
    labs = chl.write(folder / "labs.pdf", pages)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(folder / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(folder / "diag"))
    out = folder / "report.pdf"
    pipeline.run(str(labs), [], None, fx.PATIENT, 44, "male", str(out), collected_date=collected, confirm=confirm)
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return text, (folder / "review.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        return _run(tmp_path_factory.mktemp("chl-report"), monkeypatch=monkeypatch)
    finally:
        monkeypatch.undo()


def _occ(extracted, name, date):
    found = [o for o in extracted["marker_occurrences"] if o["name"] == name and o["date_display"] == date]
    assert len(found) <= 1
    return found[0] if found else None


def _items(extracted):
    return {item["name"]: item for item in extracted["lab_reported"]}


# --- A1. one bad cell never drops a page --------------------------------------------------------------------

def test_an_unreadable_historical_cell_excludes_only_that_cell(extracted):
    # The newest draw's page is kept: its Current column and every other readable cell are in the report.
    assert _occ(extracted, "Apolipoprotein B", chl.LATEST)["disp_value"] == "71"
    assert _occ(extracted, "Apolipoprotein B", chl.FIRST)["disp_value"] == "90"
    assert _occ(extracted, "Apolipoprotein B", chl.SECOND) is None  # the unreadable cell: left out, not "not run"
    for name in ("Lp-PLA2 Activity", "TMAO", "hs-CRP", "SHBG", "Cortisol, Total (AM)"):
        assert _occ(extracted, name, chl.LATEST) is not None, name
    assert extracted["parse_exclusions"] == [{
        "page": 2, "section": "Order SYN-M-300 (collected 03/10/2026)", "date": chl.SECOND, "scope": "cell",
        "reason": "Apolipoprotein B (Order SYN-M-300 (collected 03/10/2026)): '12.34.5' runs result digits together "
                  "with no flag or inequality between them, so its column boundary cannot be read"}]


def test_the_staff_notes_say_only_that_cell_was_left_out(report):
    _, review = report
    assert ("INCOMPLETE - results excluded: 1 unreadable cell(s)/row(s) on lab PDF page(s) 2; only those results are "
            "left out, every readable result on those pages is in this report - review them by hand") in review
    assert "INCOMPLETE - pages/sections excluded" not in review
    assert "  Lab pages accepted: 1, 2" in review


def test_an_unreadable_current_cell_keeps_the_historical_cells(tmp_path):
    page = (fx.section_preamble("SYN-A1", chl.LATEST) + fx.header_line(100, (chl.SECOND,))
            + fx.row(130, "TMAO", "12.34.5", "4.0", units="uM") + fx.row(144, "hs-CRP", "0.9", "1.0", units="mg/L"))
    exclusions = []
    with fitz.open(fx.write_lab_pdf(tmp_path / "a1.pdf", [page])) as document:
        occurrences, _ = pipeline._parse_bloodwork_tables(list(document), exclusions=exclusions)
    found = {(o["name"], o["date_display"]): o["disp_value"] for o in occurrences}
    assert found == {("TMAO", chl.SECOND): "4.0", ("hs-CRP", chl.LATEST): "0.9", ("hs-CRP", chl.SECOND): "1.0"}
    assert [item["scope"] for item in exclusions] == ["cell"]


# --- A2. column-header lines inside the table ---------------------------------------------------------------

@pytest.mark.parametrize("text", ["Optimal Moderate High Units Optimal Non-Optimal / / / /",
                                  "In Range Out of Range / / / /"])
def test_repeated_column_header_lines_are_ignored_silently(tmp_path, text):
    page = (fx.section_preamble("SYN-A2", chl.LATEST) + fx.header_line(100, (chl.SECOND,))
            + [(40, 130, text)] + fx.row(144, "hs-CRP", "0.9", "1.0", units="mg/L"))
    path = fx.write_lab_pdf(tmp_path / "a2.pdf", [page])
    with fitz.open(path) as document:
        for y in (120, 137, 151):  # a ruled table: every printed cell counts as a result row
            document[0].draw_line((40, y), (180, y))
            document[0].draw_line((40, y), (560, y))
        document.saveIncr()
    exclusions = []
    with fitz.open(path) as document:
        occurrences, unknown = pipeline._parse_bloodwork_tables(list(document), exclusions=exclusions)
    assert exclusions == [] and unknown == []
    assert {o["disp_value"] for o in occurrences} == {"0.9", "1.0"}
    assert pipeline._is_column_header_row([(0, 0, 1, 1, word) for word in text.split()])


def test_no_confirmation_item_or_staff_note_for_the_header_lines(extracted):
    assert not any("Optimal" in item or "Out of Range" in item for item in extracted["preflight"])
    assert not any("Optimal" in u["raw_name"] for u in extracted["unrecognized_markers"])


# --- B3. Historical cell shapes -----------------------------------------------------------------------------

def test_historical_cells_with_flags_censored_values_ranges_and_words(extracted):
    lp = [(o["date_display"], o["value"], o["disp_value"], o["lab_flag"])
          for o in extracted["marker_occurrences"] if o["name"] == "Lp-PLA2 Activity"]
    assert sorted(lp) == sorted([(chl.LATEST, 118.0, "118", ""), (chl.SECOND, 429.6, "429.6", "H"),
                                 (chl.FIRST, 60.0, "60", "L")])
    tmao = _occ(extracted, "TMAO", chl.SECOND)
    assert (tmao["value"], tmao["disp_value"]) == (None, "<0.3")  # censored: shown as printed, never scored
    items = _items(extracted)
    assert [(r["date_display"], r["disp_value"]) for r in items["Urinalysis — WBC"]["results"]] == [
        (chl.SECOND, "0-5"), (chl.LATEST, "None seen")]
    assert [(r["date_display"], r["disp_value"]) for r in items["Urinalysis — RBC"]["results"]] == [
        (chl.SECOND, "None seen"), (chl.LATEST, "0-2")]


def test_a_historical_cell_carries_no_range_of_its_own(extracted):
    assert _occ(extracted, "hs-CRP", chl.FIRST)["lab_range_display"] == ""
    assert _occ(extracted, "hs-CRP", chl.LATEST)["lab_range_display"] == "0.0-3.0"


def test_a_printed_range_is_never_a_scored_result(tmp_path):
    page = (fx.section_preamble("SYN-B3", chl.LATEST) + fx.header_line(100, (chl.SECOND,))
            + fx.row(130, "hs-CRP", "0.9", "1-3", units="mg/L"))
    exclusions = []
    with fitz.open(fx.write_lab_pdf(tmp_path / "b3.pdf", [page])) as document:
        occurrences, _ = pipeline._parse_bloodwork_tables(list(document), exclusions=exclusions)
    assert [(o["date_display"], o["disp_value"]) for o in occurrences] == [(chl.LATEST, "0.9")]
    assert "'1-3'" in exclusions[0]["reason"] and exclusions[0]["scope"] == "cell"


# --- B4. the draw's own full report wins ---------------------------------------------------------------------

def test_the_full_report_wins_and_a_mismatch_is_a_staff_warning(extracted):
    assert _occ(extracted, "hs-CRP", chl.SECOND)["disp_value"] == "2.2"  # page 1's full report, not page 2's 2.3
    assert ("HISTORICAL VALUE DIFFERS: hs-CRP on 11/04/2025: the full report (page 1) prints '2.2'; a later report's "
            "historical column (page 2) prints '2.3'. The full report's value is used.") in extracted["other_notes"]
    # Matching copies (Lp-PLA2 429.6H on both pages) raise nothing.
    assert sum("HISTORICAL VALUE DIFFERS" in note for note in extracted["other_notes"]) == 1


def test_without_a_full_report_the_historical_value_keeps_its_header_date(extracted):
    assert _occ(extracted, "Apolipoprotein B", chl.FIRST)["disp_value"] == "90"
    assert _occ(extracted, "Lp-PLA2 Activity", chl.FIRST)["source_label"].endswith("pages 1, 2")


def test_two_historical_copies_that_disagree_are_left_out_not_a_stop(tmp_path):
    first = (fx.section_preamble("SYN-B4-1", chl.SECOND) + fx.header_line(100, (chl.FIRST,))
             + fx.row(130, "hs-CRP", "0.9", "1.4", units="mg/L"))
    second = (fx.section_preamble("SYN-B4-2", chl.LATEST) + fx.header_line(100, (chl.FIRST,))
              + fx.row(130, "hs-CRP", "1.0", "1.5", units="mg/L"))
    exclusions = []
    with fitz.open(fx.write_lab_pdf(tmp_path / "b4.pdf", [first, second])) as document:
        occurrences, _ = pipeline._parse_bloodwork_tables(list(document), exclusions=exclusions)
    assert chl.FIRST not in {o["date_display"] for o in occurrences}
    assert "historical columns print different results (1.4 (page 1) / 1.5 (page 2))" in exclusions[0]["reason"]


# --- C5/C6. the latest draw is never replaced by an older one -----------------------------------------------

def test_a_latest_draw_with_no_accepted_result_blocks_the_report(tmp_path, monkeypatch):
    # The newest draw's whole page cannot be read: the report would show the older draw as "now".
    broken = chl.latest_page()[:9] + fx.header_line(400, (chl.SECOND, chl.SECOND)) + [(40, 430, "x")]
    unreadable = [item for item in broken if item[1] < 130] + [
        (40, 120, "Test Name"), (220, 120, "Current"), (300, 120, "Historical 07/08/2025 11/04/2025")] + \
        fx.row(150, "hs-CRP", "0.9", "1.0", units="mg/L")
    with pytest.raises(pipeline.LatestDrawNotAccepted) as stopped:
        _run(tmp_path, [chl.second_page(), unreadable], collected=chl.SECOND, monkeypatch=monkeypatch)
    message = str(stopped.value)
    assert message.startswith("Report not generated: the latest Collected date printed in the lab PDF, 03/10/2026, "
                              "has no accepted result")
    assert "Draw dates with excluded lab pages or results: 03/10/2026 (page 2)." in message
    assert not (tmp_path / "report.pdf").exists()


def test_a_staff_date_with_no_accepted_result_blocks_the_report(tmp_path, monkeypatch):
    with pytest.raises(pipeline.LatestDrawNotAccepted, match="the entered bloodwork Collected date, 03/17/2026, has no "
                                                             "accepted result"):
        _run(tmp_path, collected="03/17/2026", monkeypatch=monkeypatch)


def test_the_stop_screen_lists_draw_dates_with_excluded_results(tmp_path, monkeypatch):
    asked = []
    _run(tmp_path, confirm=lambda items: asked.extend(items) or True, monkeypatch=monkeypatch)
    assert asked[0] == "Draw dates with excluded lab pages or results: 11/04/2025 (page 2)"


def test_the_app_shows_the_block_message_and_logs_no_dates(tmp_path, monkeypatch, capsys):
    import app

    def blocked(*args, **kwargs):
        raise pipeline.LatestDrawNotAccepted("Report not generated: the latest Collected date printed in the lab PDF, "
                                             "03/10/2026, has no accepted result.")

    job = tmp_path / "job"
    job.mkdir()
    app._run_report(job, {"labs_path": None, "dexa_paths": [], "note_text": None, "patient_name": fx.PATIENT,
                          "age": 44, "sex": "male", "vitality_index": None, "collected_date": None}, blocked)
    status = app._read_job_status(job)
    assert status["status"] == "stopped" and "03/10/2026" in status["error"]
    assert "03/10/2026" not in capsys.readouterr().out


# --- D7. fasting per draw -----------------------------------------------------------------------------------

def test_unknown_fasting_draw_is_lab_reported_and_fasting_draw_is_scored(extracted):
    items = _items(extracted)
    assert items["Glucose (non-fasting)"]["results"] == [{
        "date_display": chl.LATEST, "disp_value": "95", "lab_flag": None, "lab_range": "65-99", "unit": "mg/dL",
        "note": clinic_config.FASTING_NOT_CONFIRMED_NOTE}]
    assert items["Insulin (non-fasting)"]["results"][0]["disp_value"] == "6.1"
    assert _occ(extracted, "Glucose (fasting)", chl.SECOND)["disp_value"] == "88"  # "Fasting: Y": scored
    assert _occ(extracted, "Glucose (fasting)", chl.LATEST) is None
    assert _occ(extracted, "Fasting Insulin", chl.LATEST) is None


@pytest.mark.parametrize(("printed", "note"), [("N", clinic_config.NON_FASTING_GLUCOSE_NOTE),
                                               ("", clinic_config.FASTING_NOT_CONFIRMED_NOTE),
                                               ("Unknown", clinic_config.FASTING_NOT_CONFIRMED_NOTE)])
def test_fasting_n_blank_and_unknown_are_never_scored_as_fasting(printed, note):
    occurrences = [{"name": "Glucose (fasting)", "date_display": chl.LATEST, "source_label": "x", "status": "final",
                    "value": 95.0, "disp_value": "95", "is_good": None, "lab_range_lo": 65, "lab_range_hi": 99,
                    "lab_range_display": "65-99", "lab_flag": ""}]
    unknown = []
    status = pipeline._normalize_fasting({printed.upper() or "BLANK"})
    pipeline.apply_fasting_status(occurrences, unknown, {chl.LATEST: status})
    assert occurrences == [] and unknown[0]["show_as"] == ("Glucose (non-fasting)", "Chemistry")
    assert unknown[0]["cells"][0]["note"] == note


def test_a_file_with_no_fasting_line_is_unchanged():
    occurrences = [{"name": "Glucose (fasting)", "date_display": chl.LATEST, "value": 95.0, "disp_value": "95"}]
    assert pipeline.apply_fasting_status(occurrences, [], {}) == [] and len(occurrences) == 1


@pytest.mark.parametrize("fasting", ["Unknown", ""])
def test_access_unknown_or_blank_fasting_is_treated_like_n(tmp_path, fasting):
    import access_medical
    from synthetic_fixtures import access_medical_lab as am

    with fitz.open(am.write(tmp_path / "am.pdf", am.pages(fasting=fasting or " "))) as document:
        occurrences, unknown, info = access_medical.parse(list(document), sex="male")
    assert "Glucose (fasting)" not in {o["name"] for o in occurrences}
    glucose = {item["name"]: item for item in lab_reported.build(unknown)[0]}["Glucose (non-fasting)"]
    assert glucose["note"] == clinic_config.FASTING_NOT_CONFIRMED_NOTE


# --- E8. cortisol per draw ----------------------------------------------------------------------------------

def test_cortisol_drops_am_and_shows_each_draws_time(report):
    text, review = report
    assert re.search(r"Cortisol, Total Not scored.*?13\.2 November 4, 2025 14\.0 µg/dL March 10, 2026 collected "
                     r"07:30 on 11/04/2025; time not recorded on 03/10/2026", text)
    assert "Cortisol, Total (AM)" not in text
    assert "CORTISOL LABEL: shown as 'Cortisol, Total' without '(AM)'" in review


def test_cortisol_keeps_am_when_every_draw_is_inside_the_window():
    from schema import Marker
    marker = Marker(name="Cortisol, Total (AM)", category="Hormones", unit="", kind="range", disp_range="",
                    then=13.2, disp_then="13.2", then_date_display=chl.SECOND,
                    now=14.0, disp_now="14.0", now_date_display=chl.LATEST)
    info = {"times": {chl.SECOND: "07:30", chl.LATEST: "08:10"}, "morning_window": (6, 10)}
    assert pipeline.label_cortisol_draws([marker], info) == []
    assert marker.display_name == "Cortisol, Total (AM)"
    assert marker.value_note == "collected 07:30 on 11/04/2025; collected 08:10 on 03/10/2026"


# --- F9/F10. DEXA ---------------------------------------------------------------------------------------------

def test_contested_printed_body_fat_falls_back_to_computed():
    # The printed % was dropped (reads disagree); fat and lean agree: 40.3 / (40.3 + 130.7) = 23.6%.
    reading = scoring.normalize_dexa_body_fat({"date_display": "01/05/2026", "fat_mass_lb": 40.3,
                                               "lean_mass_lb": 130.7, "body_fat_pct": None,
                                               "withheld": ["body_fat_pct"]})
    assert reading["body_fat_pct"] == "23.6%" and reading["computed"] == ["body_fat_pct"]
    assert clinic_config.DEXA_COMPUTED_LABEL


def test_vat_area_from_the_trend_table_reaches_the_latest_scan():
    assert "VAT trend table" in scan_dexa.DEXA_PROMPT and "Est. VAT Area" in scan_dexa.DEXA_PROMPT
    summary = {"page": 1, "patient_name": fx.PATIENT, "date_of_birth": None, "age": None, "illegible": False,
               "scans": [{"date": "01/05/2026", "age": None, "total_mass": "175.0", "fat_mass": "40.3",
                          "lean_mass": "130.7", "body_fat_pct": None, "vat_mass": None, "vat_area": None}]}
    trend = {"page": 2, "patient_name": fx.PATIENT, "date_of_birth": None, "age": None, "illegible": False,
             "scans": [{"date": "01/05/2026", "age": None, "total_mass": None, "fat_mass": None, "lean_mass": None,
                        "body_fat_pct": None, "vat_mass": "1.10", "vat_area": "88.4 cm²"}]}
    history = scan_dexa.gate([((1, 1), [summary, summary]), ((1, 2), [trend, trend])], fx.PATIENT)[0]
    latest = scoring.normalize_dexa_body_fat(dict(history[-1]))
    assert latest["visceral_fat_area_cm2"] == 88.4
    from dexa_reference import dexa_percent_optimized
    assert dexa_percent_optimized(latest["body_fat_pct"], latest["visceral_fat_area_cm2"], "male") is not None


# --- G11-G14. names and aliases ------------------------------------------------------------------------------

@pytest.mark.parametrize(("printed", "canonical"), [("SEX HORMONE BINDING GLOB", "SHBG"),
                                                    ("Sex Hormone Binding Globulin", "SHBG")])
def test_shbg_spellings(printed, canonical):
    assert pipeline._match_row_name(printed, None)[0] == canonical


@pytest.mark.parametrize(("printed", "canonical"), [
    ("Neutrophil Absolute", "Absolute Neutrophils"), ("Lymphocyte Absolute", "Absolute Lymphocytes"),
    ("Monocyte Absolute", "Absolute Monocytes"), ("Eosinophil Absolute", "Absolute Eosinophils"),
    ("Basophil Absolute", "Absolute Basophils")])
def test_absolute_count_spellings(printed, canonical):
    assert lab_reported.lookup(printed, "CBC")[0] == canonical
    assert lab_reported.lookup(printed + "s", "CBC") is None  # exact only


def test_urinalysis_occult_blood_is_blood(extracted):
    assert lab_reported.lookup("Occult Blood", "URINALYSIS")[0] == "Urinalysis — Blood"
    assert pipeline._match_row_name("Occult Blood", "URINALYSIS") is None
    assert _items(extracted)["Urinalysis — Blood"]["results"][-1]["disp_value"] == "Negative"


def test_testosterone_panel_albumin_and_globulin_are_separate(extracted):
    items = _items(extracted)
    assert items["Albumin"]["results"][0]["disp_value"] == "4.6" and items["Albumin"]["group"] == "Chemistry"
    assert items["Albumin (testosterone panel)"]["results"][0]["disp_value"] == "4.4"
    assert items["Globulin (testosterone panel)"]["results"][0]["disp_value"] == "2.5"
    assert not any("CONFLICT" in note for note in extracted["other_notes"])
    assert lab_reported.panel_scoped("ALBUMIN", ["Z4M"]) is None


def test_unaliased_rows_are_lab_reported_under_the_labs_heading(extracted, report):
    items = _items(extracted)
    assert items["LDL Size"]["results"] == [{"date_display": chl.LATEST, "disp_value": "21.2", "lab_flag": None,
                                             "lab_range": "20.5-23.0", "unit": "nm"}]
    assert items["Apolipoprotein A1"]["group"] == lab_reported.OTHER_RESULTS
    staff = {item["raw_name"] for item in extracted["unrecognized_markers"]}
    assert {"LDL Size", "Apolipoprotein A1"} <= staff  # still on the staff list
    text, review = report
    assert "LDL Size 20.5-23.0 — 21.2 nm March 10, 2026" in text
    assert "Unrecognized marker \"LDL Size\" (21.2 nm), reference range on source: 20.5-23.0 — shown in the report " \
           "as lab-reported" in review


def test_an_unaliased_row_under_a_lab_heading_uses_that_heading():
    unknown = [{"raw_name": "TG/HDL-C", "raw_value": "1.4", "raw_unit": "ratio", "raw_range": "<3.0",
                "source_context": "x", "section_heading": "LIPOPROTEIN FRACTIONATION",
                "cells": [{"kind": "current", "date_display": chl.LATEST, "status": "final", "value": 1.4,
                           "disp_value": "1.4", "present": True, "lab_flag": None}]}]
    items, remaining, _ = lab_reported.build(unknown)
    assert items[0]["group"] == "LIPOPROTEIN FRACTIONATION" and remaining == unknown


def test_urine_microscopy_text_values_show_as_printed(report):
    text, _ = report
    assert "Urinalysis — WBC 0-5 0-5 November 4, 2025 None seen /HPF March 10, 2026" in text
    assert "Urinalysis — RBC 0-2 None seen November 4, 2025 0-2 /HPF March 10, 2026" in text


# --- H15. assay change across draws --------------------------------------------------------------------------

def test_a_draw_on_a_different_assay_is_shown_with_its_range_and_not_scored(extracted, report):
    # The CellDeep basis for Free Testosterone is the 46-224 pg/mL assay (clinic_config.CELLDEEP_RANGE_BASIS); the
    # newest draw is on the 35-155 pg/mL dialysis assay: lab-reported with its own range and flag, never scored.
    assert _occ(extracted, "Free Testosterone", chl.SECOND)["lab_range_display"] == "46-224"  # the basis: scored
    assert _occ(extracted, "Free Testosterone", chl.LATEST) is None
    free_t = _items(extracted)["Free Testosterone"]
    assert free_t["results"] == [{"date_display": chl.LATEST, "disp_value": "120", "lab_flag": None,
                                  "lab_range": "35-155", "note": clinic_config.ASSAY_CHANGED_NOTE}]
    assert extracted["assay_changed"] == {"Free Testosterone": clinic_config.ASSAY_CHANGED_NOTE}
    text, review = report
    assert "assay or range changed - not directly comparable" in text
    assert ("ASSAY OR RANGE CHANGED: Free Testosterone prints different reference ranges across draws (11/04/2025: "
            "46-224; 03/10/2026: 35-155)") in review


def test_the_same_basis_range_in_every_draw_is_unchanged(tmp_path):
    pages = [chl.second_page(), chl.latest_page(free_t_range="46-224")]
    extracted = pipeline.extract(str(chl.write(tmp_path / "same.pdf", pages)), [], None, patient_name=fx.PATIENT,
                                 audit_root=str(tmp_path), collected_date=chl.LATEST, sex="male")
    assert _occ(extracted, "Free Testosterone", chl.LATEST)["disp_value"] == "120"
    assert extracted["assay_changed"] == {}


def test_a_single_draw_on_a_different_assay_is_never_scored(tmp_path):
    """Even when no draw prints the basis range, a draw on another assay is lab-reported, not scored."""
    pages = [chl.second_page(free_t_range="35-155"), chl.latest_page(free_t_range="35-155")]
    extracted = pipeline.extract(str(chl.write(tmp_path / "dialysis.pdf", pages)), [], None, patient_name=fx.PATIENT,
                                 audit_root=str(tmp_path), collected_date=chl.LATEST, sex="male")
    assert not any(o["name"] == "Free Testosterone" and o["disp_value"] for o in extracted["marker_occurrences"])
    assert [r["lab_range"] for r in _items(extracted)["Free Testosterone"]["results"]] == ["35-155", "35-155"]


def test_the_assay_note_is_never_shown_on_a_marker_scored_on_its_celldeep_range():
    extracted = {"name": "Synthetic", "sex": "male", "assay_changed": {"SHBG": clinic_config.ASSAY_CHANGED_NOTE},
                 "marker_occurrences": [
                     {"name": "SHBG", "date_display": d, "source_label": "x", "status": "final", "value": v,
                      "disp_value": str(v), "is_good": None, "lab_range_lo": lo, "lab_range_hi": hi,
                      "lab_range_display": f"{lo}-{hi}", "lab_flag": ""}
                     for d, v, lo, hi in ((chl.FIRST, 51.0, 22, 77), (chl.LATEST, 59.7, 19.3, 76.4))]}
    record, _ = pipeline.score_and_build_record(extracted)
    shbg = next(m for m in record.markers if m.name == "SHBG")
    assert shbg.now_tier in ("optimal", "moderate", "flag") and shbg.value_note is None


def test_a_scored_test_whose_newest_result_is_lab_reported_is_never_called_not_retested(report):
    text, _ = report
    assert ("Shown as lab-reported this round: Glucose (fasting), Fasting Insulin, Free Testosterone.") in text
    assert "Not retested this round" not in text and "was not retested" not in text
    assert "See lab-reported" in text


# --- I16. sparse panel line ----------------------------------------------------------------------------------

def test_the_sparse_panel_line_is_near_your_systems(report):
    text, _ = report
    systems = text.index("SYSTEMS, ATTENTION NEEDED FIRST")
    assert text.index(clinic_config.SPARSE_PANEL_NOTE) - systems < 60


def test_nothing_staff_only_reaches_the_patient_report(report):
    text, _ = report
    for marker in ("INCOMPLETE", "HISTORICAL VALUE DIFFERS", "ASSAY OR RANGE CHANGED", "12.34.5", "Non-Optimal"):
        assert marker not in text
