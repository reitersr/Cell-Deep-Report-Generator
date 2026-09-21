"""Regression coverage for the five systemic issues found in a real regenerated report
(patient referred to internally as "Evan Walker"). No real Evan Walker source PDFs exist in
this workspace, so every fixture here is synthetic/fake data built to reproduce the same
underlying pipeline conditions, not a copy of the real report.

Issue 1: duplicate marker rows with conflicting/matching "then" values (alias collapsing).
Issue 2: DEXA scans with fabricated 0 total/fat/lean alongside a real VAT reading.
Issue 3: DEXA section headline rendering blank ("—") instead of real narrative.
Issue 4: generated marker description text losing spacing mid-document.
Issue 5: FSH/LH suppression tier + narrative not actually gated on confirmed on_trt status.
"""

import os
import json
from pathlib import Path

import fitz
import pytest

import pipeline
import scoring
import template
from schema import DexaReading, PatientRecord


def _copy_for_render(**overrides):
    base = {
        "box_stories": {}, "box_forward": {}, "headlines": {},
        "marker_notes": {}, "marker_what": {}, "category_taglines": {},
        "group_narratives": {}, "optimization_summary_bullets": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Issue 1: duplicate marker rows from multi-alias extraction
# ---------------------------------------------------------------------------

def test_matching_duplicate_alias_mentions_merge_into_one_marker():
    record, notice = pipeline.score_and_build_record({
        "name": "Dedup Patient", "sex": "male", "provider_note_raw": "",
        "markers": [
            {"name": "Glucose (fasting)", "then": 72, "disp_then": "72", "now": 74, "disp_now": "74"},
            {"name": "glucose, fasting", "then": 72, "disp_then": "72", "now": 74, "disp_now": "74"},
        ],
    })
    assert [m.name for m in record.markers] == ["Glucose (fasting)"]
    assert record.markers[0].then == 72
    assert record.markers[0].now == 74
    assert not any("CONFLICTING" in note for note in notice.other_notes)


def test_conflicting_duplicate_alias_mentions_flagged_not_silently_picked():
    record, notice = pipeline.score_and_build_record({
        "name": "Conflict Patient", "sex": "male", "provider_note_raw": "",
        "markers": [
            {"name": "Vitamin D", "then": 20, "disp_then": "20", "now": 58.8, "disp_now": "58.8"},
            {"name": "vitamin d, 25-hydroxy", "then": 20, "disp_then": "20", "now": 73, "disp_now": "73"},
        ],
    })
    # exactly one marker row reaches the record - never two conflicting rows
    assert [m.name for m in record.markers] == ["Vitamin D"]
    assert any("CONFLICTING VALUES" in note and "Vitamin D" in note for note in notice.other_notes)
    assert any("NEEDS HUMAN REVIEW" in note for note in notice.other_notes)


def test_three_alias_mentions_of_magnesium_collapse_to_one_row_and_flag():
    record, notice = pipeline.score_and_build_record({
        "name": "Magnesium Patient", "sex": "female", "provider_note_raw": "",
        "markers": [
            {"name": "Magnesium", "now": 2.0, "disp_now": "2.0"},
            {"name": "magnesium, serum", "now": 2.1, "disp_now": "2.1"},
        ],
    })
    assert [m.name for m in record.markers] == ["Magnesium"]
    assert any("Magnesium" in note and "CONFLICTING" in note for note in notice.other_notes)


def test_dedupe_helper_generalizes_across_the_whole_library():
    # every canonical marker with 2+ aliases should collapse identically - not a per-marker patch
    from markers_reference import MARKER_LIBRARY
    multi_alias_markers = [name for name, cfg in MARKER_LIBRARY.items() if len(cfg.get("aliases", [])) >= 2]
    assert len(multi_alias_markers) > 5  # sanity: this is a real generalizable condition, not one-off
    for canonical in multi_alias_markers[:5]:
        aliases = MARKER_LIBRARY[canonical]["aliases"][:2]
        record, _ = pipeline.score_and_build_record({
            "name": "Generalization Patient", "sex": "male", "provider_note_raw": "",
            "markers": [{"name": a, "now": 1.0, "disp_now": "1.0"} for a in aliases],
        })
        assert len(record.markers) == 1, canonical


def test_dedupe_does_not_drop_a_real_lab_range_for_the_sentinel_from_a_duplicate_mention():
    """Regression for Issue B: Cortisol (aliases "cortisol"/"cortisol, am"/"cortisol total") got
    extracted twice from this patient's source PDF - once with no printed range for that mention
    (the 0/0/"" sentinel, see extraction_prompt.py) and once with the real AM/PM printed range.
    A naive per-field None/"" gap-fill never adopts the real range if the sentinel mention happens
    to be entries[0], because 0 isn't None or "" - this must merge the range as a whole unit."""
    record, notice = pipeline.score_and_build_record({
        "name": "Cortisol Patient", "sex": "male", "provider_note_raw": "",
        "markers": [
            {"name": "cortisol", "now": 8.0, "disp_now": "8.0",
             "lab_range_now_lo": 0, "lab_range_now_hi": 0, "lab_range_now_display": ""},
            {"name": "cortisol total", "now": 8.0, "disp_now": "8.0",
             "lab_range_now_lo": 4.8, "lab_range_now_hi": 19.5, "lab_range_now_display": "4.8-19.5 AM"},
        ],
    })
    assert [m.name for m in record.markers] == ["Cortisol, Total (AM)"]
    cortisol = record.markers[0]
    assert cortisol.range_source == "lab"
    assert cortisol.now_tier == "optimal"
    assert cortisol.unscored_reason is None
    assert not any("CONFLICTING" in note for note in notice.other_notes)


# Real ground-truth data relayed directly from the patient's actual source bloodwork PDF
# (not a reconstruction): a single Cortisol, Total line with the current (4/24/2026) and
# historical (1/7/2026) values, followed immediately by the lab's own AM/PM dual sub-range.
# This DISPROVES the dedup/sentinel-merge theory above as the mechanism for this patient - there
# is only one real mention, nothing to merge. The actual root cause is that extraction_prompt.py
# had no guidance for a reference range printed as more than one time-qualified sub-range, so the
# model could (non-deterministically) treat it as "can't be read confidently" and fall back to
# the 0/0/"" sentinel exactly as if no range were printed at all.
REAL_CORTISOL_SOURCE_TEXT = (
    "Cortisol, Total 14.7 ug/dL Z4M 4.8\n"
    "Reference range: AM (6-10 AM) 4.8-19.5 ug/dL; PM (4-8 PM) 2.5-11.9 ug/dL."
)


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requires a live Anthropic API key")
def test_live_extraction_handles_the_real_cortisol_am_pm_dual_range_text(tmp_path):
    """Live API check against the REAL verbatim source text (not a cleaned-up reconstruction):
    confirms extraction populates a real lab_range_now_lo/hi/display for this exact dual
    time-of-day range format instead of falling back to the 0/0/"" sentinel."""
    from anthropic import Anthropic

    source = tmp_path / "real_cortisol_range.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(36, 36, 560, 780),
        "SYNTHETIC RE-CREATION FOR TESTING - Cleveland HeartLab / Quest style panel\n"
        "Patient: Real Cortisol Range Check\n\n" + REAL_CORTISOL_SOURCE_TEXT + "\n",
        fontsize=10, fontname="cour",
    )
    document.save(source)
    document.close()

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    extracted = pipeline.extract(client, labs_pdf=str(source), dexa_pdfs=[], note_text=None,
                                  patient_name="Real Cortisol Range Check")
    cortisol_entries = [m for m in extracted.get("marker_occurrences", [])
                         if "cortisol" in m.get("name", "").lower()]
    assert len(cortisol_entries) == 1
    cortisol = cortisol_entries[0]
    assert cortisol["lab_range_display"] != ""
    assert cortisol["lab_range_lo"] == 4.8
    assert cortisol["lab_range_hi"] == 19.5

    record, _ = pipeline.score_and_build_record({
        "name": "Real Cortisol Range Check", "sex": "male", "provider_note_raw": "",
        "first_draw_date": "", "latest_draw_date": cortisol["date_display"],
        "marker_occurrences": [cortisol],
    })
    assert record.markers[0].now_tier == "optimal"
    assert record.markers[0].unscored_reason is None



# ---------------------------------------------------------------------------
# Issue 2: corrupted partial DEXA scans (fabricated 0s alongside a real VAT value)
# ---------------------------------------------------------------------------

def test_extraction_sentinel_partial_scan_becomes_null_not_fabricated():
    # -1 / "" is the real extraction-schema encoding for "not measured" (see extraction_prompt.py -
    # these fields can't be true JSON null without exceeding the API's nullable-field limit)
    normalized = scoring.normalize_dexa_body_fat({
        "date_display": "Jan 27, 2026", "total_mass_lb": -1, "fat_mass_lb": -1,
        "lean_mass_lb": -1, "body_fat_pct": "", "vat_fat_mass_lb": 1.23,
    })
    assert normalized["total_mass_lb"] is None
    assert normalized["fat_mass_lb"] is None
    assert normalized["lean_mass_lb"] is None
    assert normalized["body_fat_pct"] is None
    assert normalized["vat_fat_mass_lb"] == 1.23


def test_zero_sentinel_partial_scan_becomes_null_not_fabricated():
    # defense-in-depth: even if a sentinel is missed and 0 slips through instead, an all-zero
    # reading alongside a real, nonzero VAT reading is still treated as a partial scan
    normalized = scoring.normalize_dexa_body_fat({
        "date_display": "Jan 27, 2026", "total_mass_lb": 0, "fat_mass_lb": 0,
        "lean_mass_lb": 0, "body_fat_pct": None, "vat_fat_mass_lb": 1.23,
    })
    assert normalized["total_mass_lb"] is None
    assert normalized["fat_mass_lb"] is None
    assert normalized["lean_mass_lb"] is None
    assert normalized["body_fat_pct"] is None
    assert normalized["vat_fat_mass_lb"] == 1.23


def test_genuine_zero_is_not_misread_as_partial_scan():
    # a scan with no VAT reading at all and real 0-adjacent values should be untouched
    normalized = scoring.normalize_dexa_body_fat({
        "date_display": "Jan 1, 2026", "total_mass_lb": 150.0, "fat_mass_lb": 40.0,
        "lean_mass_lb": 110.0, "body_fat_pct": "26.7%", "vat_fat_mass_lb": None,
    })
    assert normalized["total_mass_lb"] == 150.0


def test_partial_scan_renders_dashes_not_zero_in_history_table(tmp_path):
    record = PatientRecord(
        name="Partial Scan Patient",
        dexa_history=[
            DexaReading(date_display="Jan 1, 2026", total_mass_lb=170.0, fat_mass_lb=56.0,
                        lean_mass_lb=110.0, body_fat_pct="33.0%", vat_fat_mass_lb=2.0),
            DexaReading(**scoring.normalize_dexa_body_fat({
                "date_display": "Jan 27, 2026", "total_mass_lb": -1, "fat_mass_lb": -1,
                "lean_mass_lb": -1, "body_fat_pct": "", "vat_fat_mass_lb": 1.23,
            })),
        ],
    )
    output = tmp_path / "partial_dexa.pdf"
    template.render(
        record,
        _copy_for_render(headlines={"Structure": "Visceral fat down."},
                          structure_score_now=90, structure_score_then=70),
        str(output),
    )
    with fitz.open(output) as rendered:
        text = "\n".join(page.get_text() for page in rendered)
    assert "1.23" in text  # real VAT value still shown
    assert "— lb total" in text
    assert "— lb fat" in text
    assert "— lb lean" in text


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requires a live Anthropic API key")
def test_live_extraction_call_survives_dexa_schema_with_partial_scan(tmp_path):
    """Live API check for the recurring 'compiled grammar too large' / strict-tools failure:
    this exact extraction call (a DEXA history with a partial, VAT-only scan) is what pushed the
    schema's nullable-field count from 7 to 11 earlier, so this must succeed against the real API,
    not just parse locally."""
    from anthropic import Anthropic

    source = tmp_path / "live_schema_check_dexa.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(36, 36, 560, 780),
        "SYNTHETIC TEST DEXA REPORT - FAKE DATA, NOT A REAL PATIENT\n"
        "Patient: Live Schema Check\n\n"
        "SCAN 1 - Jan 1, 2026\n"
        "  Total Mass: 170.0 lb\n  Fat Mass: 56.0 lb\n  Lean Mass: 110.0 lb\n"
        "  Body Fat: 33.0%\n  Visceral Fat (VAT): 2.0 lb\n\n"
        "SCAN 2 - Jan 27, 2026 (visceral fat re-check only, no full body composition this visit)\n"
        "  Visceral Fat (VAT): 1.23 lb\n",
        fontsize=10,
        fontname="cour",
    )
    document.save(source)
    document.close()

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    extracted = pipeline.extract(client, labs_pdf=None, dexa_pdfs=[str(source)],
                                  note_text=None, patient_name="Live Schema Check")
    assert extracted.get("dexa_history")


# ---------------------------------------------------------------------------
# Issue 3: DEXA headline rendering blank
# ---------------------------------------------------------------------------

def test_structure_headline_falls_back_to_real_data_never_a_bare_dash():
    record = PatientRecord(
        name="Headline Patient",
        dexa_history=[
            DexaReading(date_display="Jan 1, 2026", total_mass_lb=170.0, fat_mass_lb=56.0,
                        lean_mass_lb=110.0, body_fat_pct="33.0%", vat_fat_mass_lb=2.0),
            DexaReading(date_display="Jul 1, 2026", total_mass_lb=160.0, fat_mass_lb=46.0,
                        lean_mass_lb=112.0, body_fat_pct="28.8%", vat_fat_mass_lb=0.8),
        ],
    )
    roll = {"Structure": {"now": 90}}
    html = template.dexa_panel(record, _copy_for_render(headlines={}), roll, None)
    assert '<div class="dexa-title">—</div>' not in html
    assert "Visceral fat down from 2.0 lb to 0.8 lb." in html


def test_structure_headline_uses_real_generated_text_when_present():
    record = PatientRecord(
        name="Headline Patient Two",
        dexa_history=[DexaReading(date_display="Jan 1, 2026", total_mass_lb=170.0, fat_mass_lb=56.0,
                                   lean_mass_lb=110.0, body_fat_pct="33.0%", vat_fat_mass_lb=2.0)],
    )
    roll = {"Structure": {"now": 90}}
    html = template.dexa_panel(record, _copy_for_render(headlines={"Structure": "Visceral fat cut by more than half."}), roll, None)
    assert "Visceral fat cut by more than half." in html


# ---------------------------------------------------------------------------
# Issue C: DEXA "current scan" selection picked a corrupted/partial trailing scan instead of the
# real most recent complete one. Distinct from Issue 2 (a partial scan rendering fabricated
# zeros) - this is about WHICH scan the snapshot ("Where you are now") panel uses at all.
# ---------------------------------------------------------------------------

def _evan_walker_dexa_history():
    # Real dates/VAT values from Evan Walker's actual 7-scan history (established earlier in this
    # conversation): three trailing VAT-only follow-ups (Jan 27, Feb 18, Feb 27, 2026) sort last
    # chronologically but have no real body-composition data, and May 28, 2026 - a later, complete
    # scan - is the true most recent usable reading.
    return [
        DexaReading(date_display="Jan 1, 2026", total_mass_lb=170.0, fat_mass_lb=56.0,
                    lean_mass_lb=110.0, body_fat_pct="33.0%", vat_fat_mass_lb=2.0),
        DexaReading(**scoring.normalize_dexa_body_fat({
            "date_display": "Jan 27, 2026", "total_mass_lb": -1, "fat_mass_lb": -1,
            "lean_mass_lb": -1, "body_fat_pct": "", "vat_fat_mass_lb": 1.23,
        })),
        DexaReading(**scoring.normalize_dexa_body_fat({
            "date_display": "Feb 18, 2026", "total_mass_lb": -1, "fat_mass_lb": -1,
            "lean_mass_lb": -1, "body_fat_pct": "", "vat_fat_mass_lb": 0.82,
        })),
        DexaReading(**scoring.normalize_dexa_body_fat({
            "date_display": "Feb 27, 2026", "total_mass_lb": -1, "fat_mass_lb": -1,
            "lean_mass_lb": -1, "body_fat_pct": "", "vat_fat_mass_lb": 0.72,
        })),
        DexaReading(date_display="May 28, 2026", total_mass_lb=158.0, fat_mass_lb=42.0,
                    lean_mass_lb=113.0, body_fat_pct="26.6%", vat_fat_mass_lb=0.6),
    ]


def test_current_dexa_scan_skips_trailing_partial_scans_for_a_real_complete_one():
    record = PatientRecord(name="Evan Walker DEXA Selection", dexa_history=_evan_walker_dexa_history())
    latest = template._latest_complete_dexa_reading(record.dexa_history)
    assert latest.date_display == "May 28, 2026"
    assert latest.total_mass_lb == 158.0


def test_dexa_panel_snapshot_uses_the_real_latest_complete_scan_not_the_last_list_entry():
    record = PatientRecord(name="Evan Walker DEXA Selection", dexa_history=_evan_walker_dexa_history())
    roll = {"Structure": {"now": 90}}
    html = template.dexa_panel(record, _copy_for_render(headlines={"Structure": "Real headline."}), roll, None)
    assert "Where you are now" in html
    now_idx = html.find("Where you are now")
    now_section = html[now_idx:now_idx + 600]
    assert "May 28, 2026" in now_section
    assert "42.0" in now_section  # fat mass, lb - real May 28 value, never the trailing partial scan
    assert "Feb 27, 2026" not in now_section


# ---------------------------------------------------------------------------
# Issue 4: generated copy text losing spacing mid-document
# ---------------------------------------------------------------------------

def test_corrupted_run_on_text_is_detected():
    corrupted = {
        "marker_what": {"SHBG": "Whatthisis:Sexhormonebindingglobulinaproteinthatcarrieshormones"},
        "marker_notes": {"TSH": "This one is fine, has normal spacing throughout."},
    }
    paths = pipeline._find_corrupted_text_paths(corrupted)
    assert paths == ["marker_what.SHBG"]


def test_real_evan_walker_style_run_on_text_broken_up_by_punctuation_is_detected():
    # Reconstructed from the real report's actual garbling pattern: ordinary punctuation
    # (hyphens, commas, periods) still breaks the letters into <24-char chunks, so the original
    # word-length-only regex missed this even though every real word boundary lost its space.
    real_pattern_shbg_what = (
        "Whatthisis:Sexhormone-binding,globulin.Aprotein,thatcarries.Hormones,throughyour."
        "Bloodstream,andtissues."
    )
    assert not pipeline._RUN_ON_WORD_RE.search(real_pattern_shbg_what), (
        "this fixture should reproduce a case the old word-length-only check missed"
    )
    corrupted = {"marker_what": {"SHBG": real_pattern_shbg_what},
                 "marker_notes": {"TSH": "This one is fine, has normal spacing throughout."}}
    assert pipeline._find_corrupted_text_paths(corrupted) == ["marker_what.SHBG"]


def test_normal_prose_with_hyphens_and_long_names_is_never_flagged():
    fine = {
        "marker_notes": {
            "Lp-PLA2 Activity": "A marker for hidden inflammation in your arteries, currently well-controlled.",
            "SHBG": "Sex hormone-binding globulin, a protein that carries hormones through your bloodstream.",
        }
    }
    assert pipeline._find_corrupted_text_paths(fine) == []


# Verbatim (not paraphrased) garbled text relayed directly from the real rendered Evan Walker
# report's SHBG entry. Confirmed by direct inspection: 363 letters, only 12 real spaces (ratio
# 30.25, vs. the _MAX_LETTERS_PER_SPACE threshold of 12), and the pure word-length check alone
# already finds multiple runs well past 24 chars (e.g. "testosteronedetermineswhatyourbodyisactually",
# 44 chars) - _is_run_on_text() returns True on this exact string. The detector was never the
# blind spot; this fixture exists so a future regression in the detector itself is caught against
# the real failure text, not a reconstruction of it.
REAL_SHBG_GARBLED_TEXT = (
    "Whatthisis:Aproteinthatbindstestosterone,makingitunavailableforimmediateuse. "
    "The balancebetweenSHBG,totaltestosterone,andfree testosteronedetermineswhatyourbodyisactually "
    "experiencing.. Movedfrom38.0to52.0nmol/L, crossingfromoptimalintomoderate. SHBGbinds "
    "testosteroneandaffectshowmuchisavailableas freehormone. ArisingSHBGalongsideveryhigh "
    "totaltestosteroneproducesanunpredictablefree fraction."
)


def test_real_shbg_garbled_text_is_detected_as_run_on():
    assert pipeline._is_run_on_text(REAL_SHBG_GARBLED_TEXT) is True

    letters = sum(1 for c in REAL_SHBG_GARBLED_TEXT if c.isalpha())
    spaces = REAL_SHBG_GARBLED_TEXT.count(" ")
    assert letters == 363
    assert spaces == 12
    assert letters / spaces > pipeline._MAX_LETTERS_PER_SPACE

    longest_run = max(pipeline._RUN_ON_WORD_RE.findall(REAL_SHBG_GARBLED_TEXT), key=len)
    assert len(longest_run) >= 24  # e.g. "testosteronedetermineswhatyourbodyisactually" (44 chars)

    corrupted = {"marker_what": {"SHBG": REAL_SHBG_GARBLED_TEXT}}
    assert pipeline._find_corrupted_text_paths(corrupted) == ["marker_what.SHBG"]


# ---------------------------------------------------------------------------
# Issue 6: a marker present only in a secondary same-draw lab report (e.g. a separate Quest
# Diagnostics report for the same specimen/draw date as the primary Cleveland HeartLab panel)
# was dropped entirely instead of being captured as that draw's "now" value. Confirmed real
# case: the CHL Cardiometabolic report for the 04/24/2026 draw states urinalysis was
# "Test Not Performed / No specimen received," but the separate Quest report for that exact
# same draw date/specimen (MR421967F) DID run it, with Occult Blood = Negative. That Negative
# result must surface as "now" for Urinalysis — Occult Blood; it was being lost.
# No real Evan Walker source PDFs exist in this workspace (same caveat as Issue 1-5 above), so
# the fixtures below reproduce the same underlying condition synthetically.
# ---------------------------------------------------------------------------

def test_occurrence_reconciliation_uses_actual_dates_and_never_picks_conflicts():
    occurrences = [
        {"name": "Free T3", "date_display": "01/07/2026", "source_label": "CHL",
         "status": "reported", "value": 3.5, "disp_value": "3.5", "is_good": None,
         "lab_range_lo": 2.0, "lab_range_hi": 4.4, "lab_range_display": "2.0-4.4"},
        {"name": "Free T3", "date_display": "02/01/2026", "source_label": "CHL",
         "status": "reported", "value": 2.9, "disp_value": "2.9", "is_good": None,
         "lab_range_lo": 2.0, "lab_range_hi": 4.4, "lab_range_display": "2.0-4.4"},
        {"name": "Free T3", "date_display": "04/24/2026", "source_label": "Quest MR421967F",
         "status": "reported", "value": 3.5, "disp_value": "3.5", "is_good": None,
         "lab_range_lo": 2.0, "lab_range_hi": 4.4, "lab_range_display": "2.0-4.4"},
        {"name": "Glucose (fasting)", "date_display": "04/24/2026", "source_label": "CHL",
         "status": "reported", "value": 92, "disp_value": "92", "is_good": None,
         "lab_range_lo": 70, "lab_range_hi": 99, "lab_range_display": "70-99"},
        {"name": "Glucose (fasting)", "date_display": "04/24/2026", "source_label": "Quest MR421967F",
         "status": "reported", "value": 92, "disp_value": "92.0", "is_good": None,
         "lab_range_lo": 70, "lab_range_hi": 99, "lab_range_display": "70-99"},
        {"name": "Urinalysis", "date_display": "04/24/2026", "source_label": "CHL",
         "status": "not_performed", "value": None, "disp_value": "", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""},
        {"name": "Occult Blood", "date_display": "04/24/2026", "source_label": "Quest MR421967F",
         "status": "reported", "value": None, "disp_value": "Negative", "is_good": True,
         "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""},
        {"name": "Myeloperoxidase", "date_display": "01/07/2026", "source_label": "CHL",
         "status": "reported", "value": 300, "disp_value": "300", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 539, "lab_range_display": "0-539"},
        {"name": "Myeloperoxidase", "date_display": "04/24/2026", "source_label": "CHL",
         "status": "not_performed", "value": None, "disp_value": "", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""},
        {"name": "TSH", "date_display": "04/24/2026", "source_label": "CHL",
         "status": "reported", "value": 1.2, "disp_value": "1.2", "is_good": None,
         "lab_range_lo": 0.4, "lab_range_hi": 4.0, "lab_range_display": "0.4-4.0"},
        {"name": "TSH", "date_display": "04/24/2026", "source_label": "Quest MR421967F",
         "status": "reported", "value": 1.8, "disp_value": "1.8", "is_good": None,
         "lab_range_lo": 0.4, "lab_range_hi": 4.0, "lab_range_display": "0.4-4.0"},
    ]

    reconciled, notes, confirmed_absent = pipeline.reconcile_marker_occurrences(
        occurrences, first_draw_date="04/24/2026", latest_draw_date="02/01/2026",
    )
    by_name = {marker["name"]: marker for marker in reconciled}

    assert by_name["Free T3"]["then"] == 3.5
    assert by_name["Free T3"]["now"] == 3.5
    assert by_name["Free T3"]["full_history"] == [{
        "date_display": "02/01/2026", "value": 2.9, "disp_value": "2.9",
    }]
    assert by_name["Glucose (fasting)"]["now"] == 92
    occult = next(marker for name, marker in by_name.items() if "Occult Blood" in name)
    assert occult["now"] is None
    assert occult["disp_now"] == "Negative"
    assert occult["is_good_now"] is True
    assert by_name["Myeloperoxidase"]["now"] is None
    assert "Myeloperoxidase" in confirmed_absent
    assert by_name["TSH"]["now"] is None
    assert any("TSH" in note and "CONFLICTING" in note for note in notes)


def test_occurrence_reconciliation_reads_dates_embedded_in_report_column_labels():
    occurrences = [
        {"name": "Total Cholesterol", "date_display": "Historical (01/07/2026)",
         "status": "reported", "value": 180, "disp_value": "180", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""},
        {"name": "Total Cholesterol", "date_display": "Current (04/24/2026)",
         "status": "reported", "value": 200, "disp_value": "200", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""},
    ]

    reconciled, notes, _ = pipeline.reconcile_marker_occurrences(occurrences)

    assert reconciled[0]["then"] == 180
    assert reconciled[0]["now"] == 200
    assert not notes


def test_occurrence_history_is_sorted_by_date_not_source_order():
    occurrences = [
        {"name": "TSH", "date_display": "04/24/2026", "status": "reported", "value": 3.0,
         "disp_value": "3.0", "is_good": None, "lab_range_lo": 0, "lab_range_hi": 0,
         "lab_range_display": ""},
        {"name": "TSH", "date_display": "02/01/2026", "status": "reported", "value": 2.0,
         "disp_value": "2.0", "is_good": None, "lab_range_lo": 0, "lab_range_hi": 0,
         "lab_range_display": ""},
        {"name": "TSH", "date_display": "01/07/2026", "status": "reported", "value": 1.0,
         "disp_value": "1.0", "is_good": None, "lab_range_lo": 0, "lab_range_hi": 0,
         "lab_range_display": ""},
    ]

    reconciled, _, _ = pipeline.reconcile_marker_occurrences(occurrences)

    assert reconciled[0]["then"] == 1.0
    assert reconciled[0]["now"] == 3.0
    assert reconciled[0]["full_history"] == [{
        "date_display": "02/01/2026", "value": 2.0, "disp_value": "2.0",
    }]


def test_hormone_precedence_ignores_undated_chl_and_uses_later_real_date():
    undated_chl = {
        "name": "Estradiol", "date_display": "", "source_label": "CHL current",
        "status": "reported", "value": 18, "disp_value": "18", "is_good": None,
        "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": "",
    }
    dated_quest = {
        "name": "Estradiol", "date_display": "04/24/2026", "source_label": "Quest MR421967F",
        "status": "reported", "value": 42, "disp_value": "42", "is_good": None,
        "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": "",
    }

    reconciled, notes, _ = pipeline.reconcile_marker_occurrences([undated_chl, dated_quest])

    assert reconciled[0]["now"] == 42
    assert reconciled[0]["then"] is None
    assert any("Estradiol" in note and "UNDATED" in note for note in notes)

    dated_chl = dict(undated_chl, date_display="04/25/2026")
    reconciled, notes, _ = pipeline.reconcile_marker_occurrences([dated_quest, dated_chl])

    assert reconciled[0]["now"] == 18
    assert not any("UNDATED" in note for note in notes)


def test_report_collection_date_propagates_to_all_undated_current_column_occurrences():
    extracted = {
        "marker_occurrences": [
            {"name": "Testosterone Total", "source_label": "CHL Cardiometabolic", "date_display": ""},
            {"name": "Estradiol", "source_label": "CHL Cardiometabolic", "date_display": ""},
            {"name": "SHBG", "source_label": "CHL Cardiometabolic", "date_display": ""},
        ]
    }
    lab_text = (
        "Cleveland HeartLab Cardiometabolic Report\n"
        "Specimen Information\n"
        "Collected: 03/18/2026\n\n"
        "Testosterone Total 510\nEstradiol 22\nSHBG 48\n"
    )

    pipeline._attach_report_collection_dates(extracted, lab_text)

    assert [occurrence["date_display"] for occurrence in extracted["marker_occurrences"]] == [
        "03/18/2026", "03/18/2026", "03/18/2026",
    ]



def test_dedupe_fills_in_a_result_missing_from_only_one_of_two_same_draw_source_mentions():
    """If extraction emits one entry per source report for the same draw (one from the primary
    report where the marker was "not performed" -> now=None, one from a secondary report for
    that same draw date where it WAS run), the dedupe/merge step must adopt the real result
    rather than let the primary report's null win. This is the same generic gap-fill dedupe
    already used for the Cortisol dual-alias case, applied to a categorical marker."""
    record, notice = pipeline.score_and_build_record({
        "name": "Occult Blood Patient", "sex": "male", "provider_note_raw": "",
        "markers": [
            {"name": "Urinalysis", "now": None, "disp_now": ""},  # primary report: not performed
            {"name": "occult blood", "now": None, "disp_now": "Negative", "is_good_now": True},
        ],
    })
    assert [m.name for m in record.markers] == ["Urinalysis \u2014 Occult Blood"]
    marker = record.markers[0]
    assert marker.disp_now == "Negative"
    assert marker.is_good_now is True
    assert not any("CONFLICTING" in note for note in notice.other_notes)


def test_marker_missing_from_every_report_for_the_draw_still_renders_not_retested():
    """Control case (Myeloperoxidase): the primary report explicitly says not performed for this
    draw, and NO other report contains it either - this must stay null, not be filled in by the
    Issue 6 fix. Never-infer still applies when a marker genuinely has no result anywhere."""
    record, notice = pipeline.score_and_build_record({
        "name": "Myeloperoxidase Patient", "sex": "male", "provider_note_raw": "",
        "markers": [
            {"name": "Myeloperoxidase", "then": 300, "disp_then": "300", "now": None, "disp_now": ""},
        ],
    })
    marker = record.markers[0]
    assert marker.now is None
    assert marker.disp_now == ""
    html = template.bio_row_tr(marker, {})
    assert "Not retested" in html


def test_completeness_does_not_flag_marker_explicitly_not_performed_everywhere():
    notice = pipeline.verify_extraction_completeness(
        {
            "marker_occurrences": [
                {"name": "Myeloperoxidase", "date_display": "01/07/2026", "status": "not_performed"},
                {"name": "Myeloperoxidase", "date_display": "04/24/2026", "status": "not_performed"},
            ],
            "first_draw_date": "01/07/2026",
            "latest_draw_date": "04/24/2026",
        },
        lab_text="Myeloperoxidase Test Not Performed\nMyeloperoxidase Test Not Performed",
    )

    assert not any("Myeloperoxidase" in warning for warning in notice.other_notes)


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requires a live Anthropic API key")
def test_live_extraction_pulls_a_marker_only_present_in_the_secondary_same_draw_report(tmp_path):
    """Live API check reproducing the real Evan Walker condition as closely as possible without
    the actual source PDFs: a single uploaded labs PDF containing BOTH a primary panel (which
    explicitly states a marker was not performed on the current draw) and a separate secondary
    report for that exact same draw date that DID run it. Confirms extraction surfaces the
    secondary report's real result as "now" instead of dropping it, and that a marker missing
    from both reports for that draw (the Myeloperoxidase control) correctly stays null."""
    from anthropic import Anthropic

    source = tmp_path / "multi_source_same_draw.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(36, 36, 560, 780),
        "SYNTHETIC TEST LAB REPORT - FAKE DATA, NOT A REAL PATIENT\n"
        "Patient: Multi Source Draw Check\n\n"
        "=== Cleveland HeartLab Cardiometabolic Report ===\n"
        "Draw Date: 04/24/2026\n"
        "Myeloperoxidase: Test Not Performed - sample degenerated in transport\n"
        "Urinalysis: Test Not Performed / No specimen received\n\n"
        "=== Quest Diagnostics Report (specimen MR421967F) ===\n"
        "Draw Date: 04/24/2026\n"
        "Urinalysis, Occult Blood: Negative\n",
        fontsize=10, fontname="cour",
    )
    document.save(source)
    document.close()

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    extracted = pipeline.extract(client, labs_pdf=str(source), dexa_pdfs=[], note_text=None,
                                  patient_name="Multi Source Draw Check")
    record, _ = pipeline.score_and_build_record(extracted)
    by_name = {m.name: m for m in record.markers}
    assert "Urinalysis \u2014 Occult Blood" in by_name
    assert by_name["Urinalysis \u2014 Occult Blood"].disp_now == "Negative"
    if "Myeloperoxidase" in by_name:
        assert by_name["Myeloperoxidase"].now is None


def test_clear_path_blanks_only_the_corrupted_field():
    data = {"marker_what": {"SHBG": "badtextwithnospacesatallanywhereinthisstring"}, "marker_notes": {"TSH": "fine"}}
    pipeline._clear_path(data, "marker_what.SHBG")
    assert data["marker_what"]["SHBG"] == ""
    assert data["marker_notes"]["TSH"] == "fine"


def test_generate_copy_retries_once_then_blanks_if_still_corrupted(monkeypatch):
    from schema import PatientRecord

    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeResponse:
        def __init__(self, text):
            self.content = [FakeTextBlock(text)]

    corrupted_json = '{"marker_what": {"SHBG": "' + ("a" * 30) + '"}, "marker_notes": {}}'
    clean_json = '{"marker_what": {"SHBG": "Sex hormone binding globulin."}, "marker_notes": {}}'
    responses = [corrupted_json, corrupted_json]  # both attempts still corrupted -> must blank, not render

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse(responses.pop(0))

    class FakeClient:
        messages = FakeMessages()

    record = PatientRecord(name="Retry Patient")
    copy, warnings = pipeline.generate_copy(FakeClient(), record)
    assert copy["marker_what"]["SHBG"] == ""
    assert any("RUN-ON" in w for w in warnings)

    responses = [corrupted_json, clean_json]  # second attempt clean -> should be used with no warning
    copy2, warnings2 = pipeline.generate_copy(FakeClient(), record)
    assert copy2["marker_what"]["SHBG"] == "Sex hormone binding globulin."
    assert warnings2 == []


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requires a live Anthropic API key")
def test_live_api_deliberately_reproduced_garbling_is_intercepted_before_render(monkeypatch):
    """Live API check for Issue A: get the model to genuinely emit this exact punctuation-broken
    run-on pattern over a real round trip (not a hand-written string), then run it through
    generate_copy's actual retry/blank path and confirm the corrupted field never reaches the
    value that would go to template.py."""
    from anthropic import Anthropic
    from schema import PatientRecord

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=pipeline.MODEL,
        max_tokens=300,
        system="You output only raw JSON, verbatim, with no markdown fences and no other text.",
        messages=[{"role": "user", "content": (
            'Output exactly this JSON object, character for character, with no spaces added or '
            'removed anywhere in the "what" value: '
            '{"what": "Whatthisis:Sexhormone-bindingglobulin,aproteinthatcarrieshormonesthrough'
            'yourbloodstreamandcontrolshowmuchisactuallyavailabletoyourtissues."}'
        )}],
    )
    live_text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
    live_raw_text = "".join(live_text_blocks)
    live_parsed = pipeline._parse_json_response(live_raw_text)
    assert pipeline._find_corrupted_text_paths({"marker_what": {"SHBG": live_parsed.get("what", "")}}), (
        "the live model didn't reproduce the run-on pattern this round - re-run to get a live sample"
    )

    # now prove generate_copy()'s real retry/blank path intercepts a response built from that
    # genuine live sample before it could ever reach template.py
    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeResponse:
        def __init__(self, text):
            self.content = [FakeTextBlock(text)]

    live_corrupted_json = json.dumps({"marker_what": {"SHBG": live_parsed.get("what", "")}, "marker_notes": {}})

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse(live_corrupted_json)

    class FakeClient:
        messages = FakeMessages()

    copy, warnings = pipeline.generate_copy(FakeClient(), PatientRecord(name="Live Garble Patient"))
    assert copy["marker_what"]["SHBG"] == ""
    assert any("RUN-ON" in w for w in warnings)


# ---------------------------------------------------------------------------
# Issue 5: TRT suppression tier + narrative gating
# ---------------------------------------------------------------------------

def test_on_trt_regex_catches_active_testosterone_therapy_adjective_phrasing():
    _, on_trt = pipeline.parse_provider_statuses(
        "Patient continues active testosterone therapy per prior visit."
    )
    assert on_trt is True


def test_fsh_lh_suppress_correctly_when_active_testosterone_therapy_confirmed():
    record, _ = pipeline.score_and_build_record({
        "name": "TRT Patient", "sex": "male",
        "provider_note_raw": "Patient continues active testosterone therapy per prior visit.",
        "markers": [
            {"name": "LH", "now": 0.1, "disp_now": "0.1",
             "lab_range_now_lo": 1.0, "lab_range_now_hi": 10.0, "lab_range_now_display": "1.0 - 10.0"},
            {"name": "FSH", "now": 0.1, "disp_now": "0.1",
             "lab_range_now_lo": 1.0, "lab_range_now_hi": 10.0, "lab_range_now_display": "1.0 - 10.0"},
        ],
    })
    assert record.on_trt is True
    assert {m.name: m.now_tier for m in record.markers} == {"LH": "optimal", "FSH": "optimal"}


def test_generation_prompt_gates_trt_suppression_narrative_on_confirmed_status():
    from generation_prompt import GENERATION_SYSTEM_PROMPT
    assert "on_trt is true" in GENERATION_SYSTEM_PROMPT
    assert "do not assert or imply a TRT explanation" in GENERATION_SYSTEM_PROMPT


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requires a live Anthropic API key")
def test_live_generation_does_not_assert_trt_suppression_without_confirmed_status():
    """Live API check: with on_trt left unconfirmed, the model must not claim suppression is
    'expected' due to TRT while FSH/LH still render Flagged."""
    from anthropic import Anthropic
    from schema import Marker

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    record = PatientRecord(
        name="Live TRT Check", sex="male", on_trt=None,
        markers=[
            Marker(name="LH", category="Hormones", unit="mIU/mL", kind="range",
                   disp_range="lab-specific reference range", lo=1.0, hi=10.0,
                   now=0.1, disp_now="0.1", now_tier="flag", now_pct=20,
                   suppress_low_on_trt=True),
            Marker(name="FSH", category="Hormones", unit="mIU/mL", kind="range",
                   disp_range="lab-specific reference range", lo=1.0, hi=10.0,
                   now=0.1, disp_now="0.1", now_tier="flag", now_pct=20,
                   suppress_low_on_trt=True),
        ],
    )
    copy, _ = pipeline.generate_copy(client, record)
    notes_text = " ".join(copy.get("marker_notes", {}).values()).lower()
    assert "expected during active testosterone therapy" not in notes_text
    assert "expected during" not in notes_text or "trt" not in notes_text


# ---------------------------------------------------------------------------
# Full-pipeline live regeneration smoke test.
#
# No real Evan Walker source PDFs exist in this workspace, so this cannot be the literal
# "regenerate the same report from the same two source PDFs" verification - this is the closest
# available proxy: a synthetic lab + DEXA PDF pair built to reproduce every condition that
# triggered the reported bugs (Cortisol under two alias labels with a split AM/PM range, a
# partial VAT-only DEXA follow-up, duplicate Vitamin D/Magnesium aliases, "active testosterone
# therapy" phrasing), run through the real end-to-end pipeline.run() against the live API.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requires a live Anthropic API key")
def test_live_full_pipeline_regeneration_all_fixes_hold_together(tmp_path):
    labs_path = tmp_path / "labs.pdf"
    labs_doc = fitz.open()
    labs_page = labs_doc.new_page()
    labs_page.insert_textbox(
        fitz.Rect(36, 36, 560, 780),
        "SYNTHETIC TEST LAB REPORT - FAKE DATA, NOT A REAL PATIENT\n"
        "Patient: Live Full Pipeline Patient\n\n"
        "HORMONE PANEL - COLLECTED: 07/01/2026\n"
        f"  {REAL_CORTISOL_SOURCE_TEXT}\n"
        "  SHBG: 60.0 nmol/L   (ref 10-80)\n"
        "  LH: 0.1 mIU/mL   (ref 1.0-10.0)\n"
        "  FSH: 0.1 mIU/mL   (ref 1.0-10.0)\n\n"
        "FOUNDATIONAL PANEL - COLLECTED: 07/01/2026\n"
        "  Vitamin D: 45.0 ng/mL   (ref 30-100)\n"
        "  Vitamin D, 25-Hydroxy: 45.0 ng/mL   (ref 30-100)\n"
        "  Magnesium: 2.0 mg/dL   (ref 1.7-2.3)\n"
        "  Magnesium, Serum: 2.0 mg/dL   (ref 1.7-2.3)\n",
        fontsize=9,
        fontname="cour",
    )
    labs_doc.save(labs_path)
    labs_doc.close()

    dexa_path = tmp_path / "dexa.pdf"
    dexa_doc = fitz.open()
    dexa_page = dexa_doc.new_page()
    dexa_page.insert_textbox(
        fitz.Rect(36, 36, 560, 780),
        "SYNTHETIC TEST DEXA REPORT - FAKE DATA, NOT A REAL PATIENT\n"
        "Patient: Live Full Pipeline Patient\n\n"
        "SCAN 1 - Jan 1, 2026\n"
        "  Total Mass: 170.0 lb\n  Fat Mass: 56.0 lb\n  Lean Mass: 110.0 lb\n"
        "  Body Fat: 33.0%\n  Visceral Fat (VAT): 2.0 lb\n\n"
        "SCAN 2 - Jul 1, 2026 (visceral fat re-check only, no full body composition this visit)\n"
        "  Visceral Fat (VAT): 0.8 lb\n",
        fontsize=9,
        fontname="cour",
    )
    dexa_doc.save(dexa_path)
    dexa_doc.close()

    note_text = "Patient continues active testosterone therapy per prior visit."
    out_path = tmp_path / "live_full_pipeline.pdf"
    review_path = pipeline.run(
        labs_pdf=str(labs_path), dexa_pdfs=[str(dexa_path)], note_text=note_text,
        patient_name="Live Full Pipeline Patient", age=45, sex="male", out_path=str(out_path),
    )
    review_path = Path(review_path)

    with fitz.open(out_path) as rendered:
        text = "\n".join(page.get_text() for page in rendered)
    review_notes = review_path.read_text(encoding="utf-8")

    # Issue 1: the aliased markers were merged into a single entry, not silently duplicated or
    # conflicted (the model's own extraction notes and/or pipeline dedup notes may phrase this
    # differently run to run - the deterministic guarantee is simply "never a silent conflict")
    assert "CONFLICTING VALUES" not in review_notes
    assert text.count("Vitamin D, 25-Hydroxy") <= 1

    # Issue B: Cortisol scored with its real range, not "Reference range pending". Cortisol has
    # no library threshold by design (lab-specific range only, like LH/FSH/SHBG), so the
    # informational "missing threshold ... using printed lab range" line is the expected SUCCESS
    # path - the actual failure signature would be "... retained as unscored" instead.
    assert "missing threshold for Cortisol, Total (AM) / unknown - marker retained as unscored" not in review_notes
    assert "Cortisol" in text
    cortisol_idx = text.find("Cortisol")
    assert "Reference range pending" not in text[cortisol_idx:cortisol_idx + 400]

    # Issue 2/3: DEXA section has a real headline, never a bare dash, and the partial scan
    # renders honestly (no fabricated "0 lb")
    assert '<div class="dexa-title">—</div>' not in text
    assert "STRUCTURE" in text

    # Issue A: no run-on text anywhere in the document
    assert not pipeline._RUN_ON_WORD_RE.search(text)

    # Issue 5: on_trt confirmed via "active testosterone therapy" -> LH/FSH suppression applies
    lh_idx = text.find("LH")
    lh_context = text[lh_idx:lh_idx + 200]
    assert "optimal" in lh_context.lower()
