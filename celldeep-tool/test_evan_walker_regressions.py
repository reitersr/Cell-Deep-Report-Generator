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
# Issue 4: generated copy text losing spacing mid-document
# ---------------------------------------------------------------------------

def test_corrupted_run_on_text_is_detected():
    corrupted = {
        "marker_what": {"SHBG": "Whatthisis:Sexhormonebindingglobulinaproteinthatcarrieshormones"},
        "marker_notes": {"TSH": "This one is fine, has normal spacing throughout."},
    }
    paths = pipeline._find_corrupted_text_paths(corrupted)
    assert paths == ["marker_what.SHBG"]


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
