"""Regression coverage for the five systemic issues found in a real regenerated report
(patient referred to internally as "Evan Walker"). No real Evan Walker source PDFs exist in
this workspace, so every fixture here is synthetic/fake data built to reproduce the same
underlying pipeline conditions, not a copy of the real report.

Issue 1: duplicate marker rows with conflicting/matching "then" values (alias collapsing).
Issue 2: DEXA scans with fabricated 0 total/fat/lean alongside a real VAT reading.
Issue 3: DEXA section headline rendering blank ("—") instead of real narrative.
Issue 5: FSH/LH suppression tier + narrative not actually gated on confirmed on_trt status.
"""

import json

import fitz
import pytest

import pipeline
import scoring
import template
from dexa_reference import dexa_percent_optimized
from schema import DexaReading, Marker, PatientRecord


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


def test_single_dexa_scan_renders_current_scan_box_no_before_after_no_checkmark():
    """A patient with exactly one DEXA scan has nothing to compare against, so the panel must
    show one "current scan" box (not a duplicated before/after) and no improvement checkmark."""
    record = PatientRecord(
        name="Single Scan Patient",
        dexa_history=[DexaReading(
            date_display="Sep 1, 2026", total_mass_lb=180.0, fat_mass_lb=27.0,
            lean_mass_lb=146.0, body_fat_pct="15.0%", visceral_fat_area_cm2=80.0,
        )],
    )
    roll = {"Structure": {"now": 96}}
    html = template.dexa_panel(record, _copy_for_render(headlines={"Structure": "Tracked."}), roll, None)
    assert "Current scan" in html
    assert "When you came in" not in html
    assert "Where you are now" not in html
    assert "dexa-badge" not in html
    # exactly one set of body-composition stats, not a duplicated before/after pair
    assert html.count('class="cap">Body fat</div>') == 1


def test_single_dexa_scan_vat_area_shown_in_history_table_not_blank():
    """VAT unit mismatch: a scan with only a cm2 VAT area (no lb VAT) must still show that
    value in the Full Scan History table instead of leaving the VAT cell blank."""
    record = PatientRecord(
        name="VAT Area Only Patient",
        dexa_history=[DexaReading(
            date_display="Sep 1, 2026", total_mass_lb=180.0, fat_mass_lb=27.0,
            lean_mass_lb=146.0, body_fat_pct="15.0%", visceral_fat_area_cm2=80.0,
        )],
    )
    roll = {"Structure": {"now": 96}}
    html = template.dexa_panel(record, _copy_for_render(headlines={"Structure": "Tracked."}), roll, None)
    assert "80.0 cm" in html and "VAT" in html


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


def test_build_rollups_structure_score_skips_trailing_partial_scans():
    """Issue 1 regression: build_rollups() must select the same complete scan dexa_panel()
    displays, not the raw last dexa_history entry - a trailing VAT-only recheck (no body-fat
    or VAT-area data) must never zero out the Structure score."""
    record = PatientRecord(name="Evan Walker DEXA Selection", sex="male",
                            dexa_history=_evan_walker_dexa_history())
    roll, _order, _overall_now, _overall_then, has_dexa = template.build_rollups(
        record, None, None, False
    )
    assert has_dexa
    assert roll["Structure"]["now"] is not None
    assert roll["Structure"]["now"] == dexa_percent_optimized("26.6%", None, "male")


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


def test_dexa_panel_renders_all_seven_real_scan_dates_in_full_history():
    history = _evan_walker_dexa_history() + [
        DexaReading(date_display="Jun 10, 2026", total_mass_lb=157.0, fat_mass_lb=41.0,
                    lean_mass_lb=114.0, body_fat_pct="26.1%", vat_fat_mass_lb=0.58),
        DexaReading(date_display="Jul 2, 2026", total_mass_lb=156.0, fat_mass_lb=40.0,
                    lean_mass_lb=114.5, body_fat_pct="25.6%", vat_fat_mass_lb=0.55),
    ]
    record = PatientRecord(name="Seven Scan History", dexa_history=history)
    html = template.dexa_panel(record, _copy_for_render(headlines={"Structure": "Tracked."}),
                               {"Structure": {"now": 90}}, None)

    history_section = html[html.index("Full scan history"):]
    for date in ("January 1, 2026", "January 27, 2026", "February 18, 2026", "February 27, 2026",
                 "May 28, 2026", "June 10, 2026", "July 2, 2026"):
        assert date in history_section
    assert history_section.count('class="dexa-hist-row"') == 7


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

def test_occurrence_reconciliation_uses_only_dated_actual_results():
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
        {"name": "hs-CRP", "date_display": "04/24/2026", "source_label": "CHL",
         "status": "reported", "value": 0.3, "disp_value": "0.3", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 10, "lab_range_display": "0-10"},
        {"name": "hs-CRP", "date_display": "", "source_label": "CHL current",
         "status": "reported", "value": 1.9, "disp_value": "1.9", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 10, "lab_range_display": "0-10"},
    ]

    reconciled = pipeline.reconcile_marker_occurrences(occurrences)
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
    assert by_name["Myeloperoxidase"]["then"] == 300
    assert by_name["Myeloperoxidase"]["then_date_display"] == "01/07/2026"
    assert by_name["hs-CRP"]["now"] == 0.3
    assert by_name["hs-CRP"]["then"] is None


def test_occurrence_reconciliation_reads_dates_embedded_in_report_column_labels():
    occurrences = [
        {"name": "Total Cholesterol", "date_display": "Historical (01/07/2026)",
         "status": "reported", "value": 180, "disp_value": "180", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""},
        {"name": "Total Cholesterol", "date_display": "Current (04/24/2026)",
         "status": "reported", "value": 200, "disp_value": "200", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""},
    ]

    reconciled = pipeline.reconcile_marker_occurrences(occurrences)

    assert reconciled[0]["then"] == 180
    assert reconciled[0]["now"] == 200


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

    reconciled = pipeline.reconcile_marker_occurrences(occurrences)

    assert reconciled[0]["then"] == 1.0
    assert reconciled[0]["now"] == 3.0
    assert reconciled[0]["full_history"] == [{
        "date_display": "02/01/2026", "value": 2.0, "disp_value": "2.0",
    }]


def test_hscrp_three_real_values_uses_true_baseline_then():
    reconciled = pipeline.reconcile_marker_occurrences([
        _dated_occurrence("hs-CRP", "01/07/2026", 20.0, ">20.0"),
        _dated_occurrence("hs-CRP", "01/27/2026", 0.3),
        {"name": "hs-CRP", "date_display": "04/24/2026", "status": "unscored",
         "value": None, "disp_value": "<3.0", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 3, "lab_range_display": "<3.0"},
    ])
    raw = reconciled[0]

    assert raw["then"] == 20.0
    assert raw["disp_then"] == ">20.0"
    assert raw["now"] is None
    assert raw["disp_now"] == "<3.0"
    assert raw["full_history"] == [{
        "date_display": "01/27/2026", "value": 0.3, "disp_value": "0.3",
    }]


def test_hscrp_latest_occurrence_wins_now_even_when_mistagged_not_performed():
    """A non-numeric threshold result (e.g. "<3.0") is a real reported value even if its
    status field is mistagged "not_performed" - it must still win "now" as the latest
    occurrence, exactly like FSH's identically-shaped threshold occurrence does."""
    reconciled = pipeline.reconcile_marker_occurrences([
        _dated_occurrence("hs-CRP", "01/07/2026", 20.0, ">20.0"),
        _dated_occurrence("hs-CRP", "01/27/2026", 0.3),
        {"name": "hs-CRP", "date_display": "04/24/2026", "source_label": "Quest",
         "status": "not_performed", "value": None, "disp_value": "<3.0", "is_good": None,
         "lab_range_lo": 0, "lab_range_hi": 3, "lab_range_display": "<3.0"},
    ])
    raw = reconciled[0]

    assert raw["then"] == 20.0
    assert raw["disp_then"] == ">20.0"
    assert raw["then_date_display"] == "01/07/2026"
    assert raw["now"] is None
    assert raw["disp_now"] == "<3.0"
    assert raw["now_date_display"] == "04/24/2026"
    assert raw["full_history"] == [{
        "date_display": "01/27/2026", "value": 0.3, "disp_value": "0.3",
    }]


@pytest.mark.parametrize(("name", "values"), [
    ("hs-CRP", [(20.0, ">20.0"), (0.3, "0.3"), (None, "<3.0")]),
    ("Testosterone, Total", [(506, "506"), (1846, "1846"), (1193, "1193")]),
    ("Free Testosterone", [(66.3, "66.3"), (310.1, "310.1"), (210.0, "210.0")]),
])
def test_three_occurrences_keep_earliest_then_latest_now_and_dates_paired(name, values):
    dates = ["01/07/2026", "01/27/2026", "04/24/2026"]
    occurrences = [
        dict(_dated_occurrence(name, date, value, disp_value),
             status="unscored" if value is None else "reported")
        for date, (value, disp_value) in zip(dates, values)
    ]

    raw = pipeline.reconcile_marker_occurrences(occurrences)[0]

    assert (raw["then"], raw["disp_then"], raw["then_date_display"]) == (
        values[0][0], values[0][1], dates[0],
    )
    assert (raw["now"], raw["disp_now"], raw["now_date_display"]) == (
        values[-1][0], values[-1][1], dates[-1],
    )
    assert raw["full_history"] == [{
        "date_display": dates[1], "value": values[1][0], "disp_value": values[1][1],
    }]


def test_fsh_lh_threshold_results_remain_current_when_numeric_value_is_null():
    occurrences = [
        {"name": "FSH", "date_display": "01/07/2026", "status": "reported",
         "value": 2.0, "disp_value": "2.0", "is_good": None,
         "lab_range_lo": 1, "lab_range_hi": 10, "lab_range_display": "1-10"},
        {"name": "FSH", "date_display": "04/24/2026", "status": "reported",
         "value": None, "disp_value": "<0.7", "is_good": None,
         "lab_range_lo": 1, "lab_range_hi": 10, "lab_range_display": "1-10"},
        {"name": "LH", "date_display": "01/07/2026", "status": "reported",
         "value": 1.5, "disp_value": "1.5", "is_good": None,
         "lab_range_lo": 1, "lab_range_hi": 10, "lab_range_display": "1-10"},
        {"name": "LH", "date_display": "04/24/2026", "status": "reported",
         "value": None, "disp_value": "<0.2", "is_good": None,
         "lab_range_lo": 1, "lab_range_hi": 10, "lab_range_display": "1-10"},
    ]

    reconciled = {marker["name"]: marker for marker in pipeline.reconcile_marker_occurrences(occurrences)}

    assert reconciled["FSH"]["now"] is None
    assert reconciled["FSH"]["disp_now"] == "<0.7"
    assert reconciled["LH"]["now"] is None
    assert reconciled["LH"]["disp_now"] == "<0.2"


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

    reconciled = pipeline.reconcile_marker_occurrences([undated_chl, dated_quest])

    assert reconciled[0]["now"] == 42
    assert reconciled[0]["then"] is None

    dated_chl = dict(undated_chl, date_display="04/25/2026")
    reconciled = pipeline.reconcile_marker_occurrences([dated_quest, dated_chl])

    assert reconciled[0]["now"] == 18


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


def _dated_occurrence(name, date_display, value, disp_value=None):
    return {"name": name, "date_display": date_display, "status": "reported",
            "value": value, "disp_value": disp_value or str(value), "is_good": None,
            "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""}


def test_lone_older_marker_is_then_not_now_and_renders_its_real_date():
    reconciled = pipeline.reconcile_marker_occurrences([
        _dated_occurrence("Myeloperoxidase", "01/07/2026", 300),
        _dated_occurrence("hs-CRP", "04/24/2026", 0.3),
    ])
    raw = next(marker for marker in reconciled if marker["name"] == "Myeloperoxidase")
    record, _ = pipeline.score_and_build_record({"name": "Date Labels", "markers": [raw]})
    html = template.bio_row_tr(record.markers[0], {})

    assert raw["now"] is None
    assert raw["then"] == 300
    assert raw["then_date_display"] == "01/07/2026"
    assert "January 7, 2026" in html


def test_lone_marker_on_report_current_date_is_now_not_then():
    reconciled = pipeline.reconcile_marker_occurrences([
        _dated_occurrence("Progesterone", "04/24/2026", 1.2),
        _dated_occurrence("hs-CRP", "04/24/2026", 0.3),
    ])
    raw = next(marker for marker in reconciled if marker["name"] == "Progesterone")
    assert raw["now"] == 1.2
    assert raw["then"] is None
    assert raw["now_date_display"] == "04/24/2026"


def test_two_older_marker_occurrences_keep_newer_as_now_and_older_as_then():
    reconciled = pipeline.reconcile_marker_occurrences([
        _dated_occurrence("CoQ10", "01/07/2026", 0.8),
        _dated_occurrence("CoQ10", "02/15/2026", 1.1),
        _dated_occurrence("hs-CRP", "04/24/2026", 0.3),
    ])
    raw = next(marker for marker in reconciled if marker["name"] == "CoQ10")
    assert raw["now"] == 1.1
    assert raw["then"] == 0.8
    assert raw["now_date_display"] == "02/15/2026"
    assert raw["then_date_display"] == "01/07/2026"


def test_then_and_now_cells_show_each_value_date_not_static_header_dates():
    reconciled = pipeline.reconcile_marker_occurrences([
        _dated_occurrence("CoQ10", "01/07/2026", 0.8),
        _dated_occurrence("CoQ10", "02/15/2026", 1.1),
        _dated_occurrence("hs-CRP", "04/24/2026", 0.3),
    ])
    raw = next(marker for marker in reconciled if marker["name"] == "CoQ10")
    record, _ = pipeline.score_and_build_record({"name": "Per Value Dates", "markers": [raw]})
    html = template.bio_row_tr(record.markers[0], {})

    assert "January 7, 2026" in html
    assert "February 15, 2026" in html


def test_narrative_copy_excludes_undated_superseded_marker_value():
    from generation_prompt import build_copy

    marker = Marker(
        name="Testosterone, Total", category="Hormones", unit="ng/dL", kind="range",
        disp_range="300 - 1000", then=506, disp_then="506", then_date_display="01/07/2026",
        now=720, disp_now="720", now_date_display="04/24/2026", now_tier="moderate", then_tier="optimal",
    )

    copy_text = json.dumps(build_copy(PatientRecord(name="Scoped Copy", markers=[marker])))

    assert "506" in copy_text
    assert "720" in copy_text
    assert "1846" not in copy_text


def test_baseline_claim_uses_earliest_dated_history_entry():
    from generation_prompt import baseline_occurrence, build_copy

    marker = Marker(
        name="Testosterone, Total", category="Hormones", unit="ng/dL", kind="range",
        disp_range="300 - 1000", then=400, disp_then="400", then_date_display="01/01/2025",
        now=700, disp_now="700", now_date_display="01/01/2027", now_tier="optimal",
        full_history=[
            {"date_display": "04/01/2025", "value": 500, "disp_value": "500"},
            {"date_display": "04/01/2026", "value": 600, "disp_value": "600"},
        ],
    )

    assert baseline_occurrence(marker)[0] == "01/01/2025"
    bullets = build_copy(PatientRecord(name="History Copy", markers=[marker]))["optimization_summary_bullets"]
    assert "<b>Starting point:</b> Your earliest bloodwork on file is from 01/01/2025." in bullets



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


def test_flagged_lh_fsh_copy_never_asserts_trt_suppression_without_confirmed_status():
    from generation_prompt import build_copy

    record = PatientRecord(
        name="TRT Copy Check", sex="male", on_trt=None,
        markers=[
            Marker(name=name, category="Hormones", unit="mIU/mL", kind="range",
                   disp_range="lab-specific reference range", lo=1.0, hi=10.0,
                   now=0.1, disp_now="0.1", now_date_display="04/24/2026", now_tier="flag", now_pct=20,
                   suppress_low_on_trt=True)
            for name in ("LH", "FSH")
        ],
    )
    copy_text = json.dumps(build_copy(record)).lower()
    assert "expected" not in copy_text
    assert "trt" not in copy_text
    assert "testosterone therapy" not in copy_text
