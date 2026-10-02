"""Deterministic parsing / template-fill tests. Every value, name, date, and Order ID is invented to
reproduce a mechanical layout pattern a real document can produce - no real or real-derived patient data."""

import json
import re
import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).parent / "synthetic_fixtures"))
import deterministic_fixtures as fx  # noqa: E402

import pipeline  # noqa: E402
from extraction_prompt import EXTRACTION_OUTPUT_SCHEMA  # noqa: E402
from generation_prompt import build_copy, select_priority_marker  # noqa: E402
from schema import Marker, PatientRecord  # noqa: E402


def _parse(tmp_path, pages, name="labs.pdf"):
    path = fx.write_lab_pdf(tmp_path / name, pages)
    with fitz.open(path) as document:
        return pipeline._parse_bloodwork_tables(list(document))


def _by_key(occurrences):
    return {(occ["name"], occ["date_display"]): occ for occ in occurrences}


def _words(items, height=9.0):
    """Prepared (x0, y0, x1, y1, text) words, for glyphs the base-14 PDF fonts can't encode (≤, ≥)."""
    return [(x, y - height, x + 6.0 * len(text), y, text) for x, y, text in items]


# (1) current and historical values run together with no delimiter ---------------------------------

def test_concatenated_columns_split_by_declared_column_count(tmp_path):
    page = (fx.section_preamble("SYN100", "04/24/2026")
            + fx.header_line(100, ("01/27/2026", "01/07/2026"))
            + fx.row(130, "hs-CRP", "0.36L0.41", hist2="1.2", units="mg/L", lab_range="0.0-3.0")
            + fx.row(144, "FSH", "<0.7<0.5", hist2="<0.3", units="mIU/mL"))
    occurrences, _ = _parse(tmp_path, [page])
    found = _by_key(occurrences)

    assert found[("hs-CRP", "04/24/2026")]["value"] == 0.36
    assert found[("hs-CRP", "01/27/2026")]["value"] == 0.41
    assert found[("hs-CRP", "01/07/2026")]["value"] == 1.2
    assert [found[("FSH", d)]["disp_value"] for d in ("04/24/2026", "01/27/2026", "01/07/2026")] == \
        ["<0.7", "<0.5", "<0.3"]


def test_digits_running_into_digits_raise_instead_of_guessing_a_boundary(tmp_path):
    page = (fx.section_preamble("SYN101", "04/24/2026")
            + fx.header_line(100, ("01/27/2026",))
            + fx.row(130, "TMAO", "29.012.4", units="uM"))
    with pytest.raises(pipeline.BloodworkParseError, match="runs result digits together"):
        _parse(tmp_path, [page])


def test_ordinary_unflagged_rows_with_range_between_columns_parse_cleanly(tmp_path):
    # Shape "88 73-135 ng/mL 70": two plain numbers, no flag, no inequality, range printed mid-row.
    page = (fx.section_preamble("SYN102", "04/24/2026")
            + [(40, 100, "Test Name"), (200, 100, "Current"), (250, 100, "Reference Range"),
               (330, 100, "Units"), (390, 100, "Historical"), (390, 111, "01/27/2026")]
            + [(40, 130, "SDMA"), (200, 130, "88"), (250, 130, "73-135"), (330, 130, "ng/mL"), (390, 130, "70")]
            + [(40, 144, "Glucose (fasting)"), (200, 144, "92"), (250, 144, "65-99"), (330, 144, "mg/dL"),
               (390, 144, "88")]
            + [(40, 158, "TSH"), (200, 158, "1.9"), (250, 158, "0.40-4.50"), (330, 158, "uIU/mL"), (390, 158, "2.4")])
    occurrences, _ = _parse(tmp_path, [page])

    assert [(o["name"], o["date_display"], o["value"], o["disp_value"], o["lab_range_lo"], o["lab_range_hi"])
            for o in occurrences] == [
        ("SDMA", "04/24/2026", 88.0, "88", 73.0, 135.0), ("SDMA", "01/27/2026", 70.0, "70", 73.0, 135.0),
        ("Glucose (fasting)", "04/24/2026", 92.0, "92", 65.0, 99.0),
        ("Glucose (fasting)", "01/27/2026", 88.0, "88", 65.0, 99.0),
        ("TSH", "04/24/2026", 1.9, "1.9", 0.40, 4.50), ("TSH", "01/27/2026", 2.4, "2.4", 0.40, 4.50)]


# (2) directly-appended H/L flag -----------------------------------------------------------------

def test_appended_and_standalone_flags_are_stripped_before_parsing(tmp_path):
    page = (fx.section_preamble("SYN110", "04/24/2026")
            + fx.header_line(100, ("01/27/2026",))
            + fx.row(130, "Testosterone, Total", "3173H", "1846", units="ng/dL", lab_range="250-1100")
            + fx.row(144, "HDL Cholesterol", "38L", units="mg/dL")
            + fx.row(158, "LDL Cholesterol", "245", units="mg/dL") + [(fx.LAB_X["current"] + 22, 158, "H")])
    found = _by_key(_parse(tmp_path, [page])[0])

    assert (found[("Testosterone, Total", "04/24/2026")]["value"],
            found[("Testosterone, Total", "04/24/2026")]["disp_value"]) == (3173.0, "3173")
    assert found[("Testosterone, Total", "01/27/2026")]["value"] == 1846.0
    assert (found[("HDL Cholesterol", "04/24/2026")]["value"],
            found[("HDL Cholesterol", "04/24/2026")]["disp_value"]) == (38.0, "38")
    assert found[("LDL Cholesterol", "04/24/2026")]["value"] == 245.0
    assert found[("Testosterone, Total", "04/24/2026")]["lab_range_display"] == "250-1100"


# (3) inequality-prefixed values -----------------------------------------------------------------

def test_inequality_prefixed_cells_keep_null_value_and_full_printed_string(tmp_path):
    page = (fx.section_preamble("SYN120", "04/24/2026")
            + fx.header_line(100, ("01/27/2026",))
            + fx.row(130, "hs-CRP", ">20.0", "<0.3", units="mg/L"))
    found = _by_key(_parse(tmp_path, [page])[0])
    assert (found[("hs-CRP", "04/24/2026")]["value"], found[("hs-CRP", "04/24/2026")]["disp_value"]) == (None, ">20.0")
    assert (found[("hs-CRP", "01/27/2026")]["value"], found[("hs-CRP", "01/27/2026")]["disp_value"]) == (None, "<0.3")
    assert found[("hs-CRP", "04/24/2026")]["status"] == "final"

    x = fx.LAB_X
    words = _words([(x["name"], 40, "Order"), (x["name"] + 30, 40, "ID:"), (x["name"] + 50, 40, "SYN121"),
                    (x["name"], 60, "Collected:"), (x["name"] + 60, 60, "04/24/2026"),
                    (x["name"], 100, "Test"), (x["name"] + 25, 100, "Name"), (x["current"], 100, "Current"),
                    (x["hist1"], 100, "Historical"), (x["hist1"], 111, "01/27/2026"),
                    (x["name"], 130, "eGFR"), (x["current"], 130, "≥90"), (x["hist1"], 130, "≤59")])
    occurrences, _ = pipeline._parse_bloodwork_tables([words])
    assert [(o["date_display"], o["value"], o["disp_value"]) for o in occurrences] == [
        ("04/24/2026", None, "≥90"), ("01/27/2026", None, "≤59")]


# (4) Test Not Performed / blank-where-a-date-exists vs. genuinely absent from the panel ------------

def test_not_performed_and_blank_dated_cells_differ_from_a_marker_absent_from_the_panel(tmp_path):
    current = (fx.section_preamble("SYN130", "04/24/2026")
               + fx.header_line(100, ("01/27/2026",))
               + fx.row(130, "TMAO", "TNP", "5.1", units="uM")
               + fx.row(144, "Myeloperoxidase", "Test Not Performed")
               + fx.row(158, "hs-CRP", "0.3", units="mg/L"))
    prior = (fx.section_preamble("SYN129", "01/27/2026")
             + fx.header_line(100)
             + fx.row(130, "Ferritin", "85", units="ng/mL"))
    occurrences, _ = _parse(tmp_path, [current, prior])
    found = _by_key(occurrences)

    for key in (("TMAO", "04/24/2026"), ("Myeloperoxidase", "04/24/2026"), ("hs-CRP", "01/27/2026")):
        assert (found[key]["status"], found[key]["value"], found[key]["disp_value"]) == ("not_performed", None, "")
    assert found[("TMAO", "01/27/2026")]["value"] == 5.1
    # Ferritin was not part of the 04/24 draw's panel at all: nothing is reported for that date.
    assert [occ["date_display"] for occ in occurrences if occ["name"] == "Ferritin"] == ["01/27/2026"]

    reconciled = {m["name"]: m for m in pipeline.reconcile_marker_occurrences(occurrences)}
    assert reconciled["hs-CRP"]["now"] == 0.3 and reconciled["hs-CRP"]["then"] is None
    assert reconciled["TMAO"]["now"] is None and reconciled["TMAO"]["then"] == 5.1


# (5) zero / one / several historical columns, colored or plain, one rule -------------------------

def test_one_rule_handles_zero_one_and_several_historical_columns(tmp_path):
    standalone = (fx.section_preamble("SYN301", "04/24/2026")
                  + fx.header_line(100)
                  + fx.row(130, "PSA Total", "2.10", units="ng/mL", lab_range="0.0-4.0")
                  + fx.row(144, "Occult Blood", "Negative"))
    one_historical = ([(40, 40, "Collected: 01/27/2026"), (40, 54, "Order ID: SYN302")]
                      + fx.header_line(100, ("10/15/2025",), dates_below=False)
                      + fx.row(130, "TMAO", "6.5", "7.0", units="uM"))
    tiered = ([(40, 40, "Order ID: SYN303"), (40, 54, "Collected: 01/07/2026"),
               (40, 100, "Test Name"), (200, 100, "Optimal"), (245, 100, "Moderate"), (295, 100, "High"),
               (340, 100, "Historical"), (340, 111, "10/15/2025"), (410, 100, "Historical"),
               (410, 111, "07/01/2025"), (480, 100, "Units"),
               (40, 130, "hs-CRP"), (295, 130, "4.2"), (340, 130, "3.9"), (410, 130, "2.8"), (480, 130, "mg/L"),
               (40, 144, "LDL Cholesterol"), (200, 144, "88"), (340, 144, "91"), (480, 144, "mg/dL")])
    occurrences, _ = _parse(tmp_path, [standalone, one_historical, tiered])
    found = _by_key(occurrences)

    assert found[("PSA Total", "04/24/2026")]["value"] == 2.10
    occult = found[("Urinalysis \u2014 Occult Blood", "04/24/2026")]
    assert (occult["value"], occult["disp_value"], occult["is_good"]) == (None, "Negative", True)
    assert found[("TMAO", "01/27/2026")]["value"] == 6.5
    assert found[("TMAO", "10/15/2025")]["value"] == 7.0
    assert [found[("hs-CRP", d)]["value"] for d in ("01/07/2026", "10/15/2025", "07/01/2025")] == [4.2, 3.9, 2.8]
    assert found[("LDL Cholesterol", "01/07/2026")]["value"] == 88.0
    assert found[("LDL Cholesterol", "07/01/2025")]["status"] == "not_performed"
    assert {occ["source_label"] for occ in occurrences} == {
        "Order SYN301 (collected 04/24/2026)", "Order SYN302 (collected 01/27/2026)",
        "Order SYN303 (collected 01/07/2026)"}


# (6) a Progress-Summary trend table is ignored; a genetics section doesn't split the section --------

def test_progress_summary_is_ignored_and_genetics_page_does_not_interrupt_sections(tmp_path):
    first = (fx.section_preamble("SYN400", "04/24/2026")
             + fx.header_line(100, ("01/27/2026",))
             + fx.row(130, "hs-CRP", "0.3", "0.9", units="mg/L"))
    genetics = [(40, 60, "Cardiovascular Genetics Detail Report"),
                (40, 90, "ApoE Genotype E3/E4"),
                (40, 104, "Guidance: carriers of 2 copies may benefit from earlier lipid review.")]
    continued = ([(40, 40, "Order ID: SYN400")] + fx.header_line(100)
                 + fx.row(130, "TMAO", "6.0", units="uM"))
    summary = ([(40, 60, "Cardiometabolic Patient Progress Summary")]
               + fx.header_line(100, ("01/27/2026", "01/07/2026"))
               + fx.row(130, "hs-CRP", "9.9", "8.8", "7.7", units="mg/L"))
    occurrences, unrecognized = _parse(tmp_path, [first, genetics, continued, summary])

    assert sorted((o["name"], o["date_display"], o["value"]) for o in occurrences) == [
        ("TMAO", "04/24/2026", 6.0), ("hs-CRP", "01/27/2026", 0.9), ("hs-CRP", "04/24/2026", 0.3)]
    assert unrecognized == []


# Unrecognized lab layouts are a hard, named failure ------------------------------------------------

def _free_text_lab_pdf(path, collected_line):
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(36, 36, 560, 780),
                        f"Patient: Pat Synthetic\n{collected_line}\nhs-CRP 0.3 mg/L (ref 0.0-3.0)\n"
                        "TSH 1.9 uIU/mL\nComments: specimen received ambient.\n", fontsize=10)
    document.save(path)
    document.close()
    return path


def test_unrecognized_layout_without_collected_line_raises(tmp_path):
    path = _free_text_lab_pdf(tmp_path / "labs.pdf", "Draw Date: 04/24/2026")
    with fitz.open(path) as document, pytest.raises(pipeline.BloodworkParseError, match="no recognizable 'Collected:'"):
        pipeline._parse_bloodwork_tables(list(document))


def test_collected_line_but_no_matching_table_raises_instead_of_empty_report(tmp_path):
    path = _free_text_lab_pdf(tmp_path / "labs.pdf", "Collected: 04/24/2026")
    out = tmp_path / "report.pdf"
    with pytest.raises(pipeline.BloodworkParseError, match="zero recognized marker rows"):
        pipeline.run(str(path), [], None, fx.PATIENT, 44, "male", str(out))
    assert not out.exists()


def test_parsed_occurrences_keep_the_extraction_schema_shape(tmp_path):
    page = (fx.section_preamble("SYN500", "04/24/2026") + fx.header_line(100, ("01/27/2026",))
            + fx.row(130, "hs-CRP", "0.3", "TNP", units="mg/L", lab_range="0.0-3.0")
            + fx.row(144, "Occult Blood", "Negative"))
    occurrences, _ = _parse(tmp_path, [page])
    item_schema = EXTRACTION_OUTPUT_SCHEMA["properties"]["marker_occurrences"]["items"]
    python_types = {"string": str, "number": (int, float), "boolean": bool, "null": type(None)}
    for occurrence in occurrences:
        assert set(occurrence) == set(item_schema["required"]) == set(item_schema["properties"])
        for key, spec in item_schema["properties"].items():
            allowed = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
            assert isinstance(occurrence[key], tuple(python_types[t] for t in allowed)), (key, occurrence[key])
    record, _ = pipeline.score_and_build_record({"name": "Shape", "marker_occurrences": occurrences})
    assert {m.name for m in record.markers} == {"hs-CRP", "Urinalysis \u2014 Occult Blood"}


# DEXA stays on the original Claude extraction path ------------------------------------------------

CLAUDE_DEXA_HISTORY = [
    {"date_display": "05/21/2025", "total_mass_lb": 170.5, "fat_mass_lb": 56.5, "lean_mass_lb": 107.6,
     "body_fat_pct": "34.4%", "vat_fat_mass_lb": 1.01, "visceral_fat_area_cm2": 82.4},
    {"date_display": "01/27/2026", "total_mass_lb": 165.0, "fat_mass_lb": 49.5, "lean_mass_lb": 109.0,
     "body_fat_pct": "30.0%", "vat_fat_mass_lb": 0.85, "visceral_fat_area_cm2": 70.1},
]


class _FakeClaude:
    calls = []

    def __init__(self, **kwargs):
        self.timeout = kwargs.get("timeout")
        self.messages = self

    def create(self, **kwargs):
        _FakeClaude.calls.append(kwargs)
        payload = json.dumps({"marker_occurrences": [], "dexa_history": CLAUDE_DEXA_HISTORY})
        return type("Response", (), {"content": [type("Block", (), {"text": payload})()]})()


def _dexa_pdf(path):
    document = fitz.open()
    document.new_page().insert_text((40, 60), "Synthetic DEXA report for Pat Synthetic", fontsize=10)
    document.save(path)
    document.close()
    return path


def test_dexa_pdfs_go_through_the_original_claude_extraction_request(tmp_path):
    from extraction_prompt import EXTRACTION_SYSTEM_PROMPT

    _FakeClaude.calls = []
    dexa = _dexa_pdf(tmp_path / "dexa.pdf")
    extracted = pipeline.extract(None, [str(dexa)], None, patient_name=fx.PATIENT, client=_FakeClaude())

    assert extracted["dexa_history"] == CLAUDE_DEXA_HISTORY
    [call] = _FakeClaude.calls
    assert call["model"] == pipeline.MODEL
    assert call["system"] == EXTRACTION_SYSTEM_PROMPT
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": EXTRACTION_OUTPUT_SCHEMA}}
    content = call["messages"][0]["content"]
    assert content[0] == pipeline._pdf_content_block(str(dexa))


def test_reports_without_dexa_make_no_claude_call(tmp_path, monkeypatch):
    _FakeClaude.calls = []
    monkeypatch.setattr(pipeline, "Anthropic", _FakeClaude)
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [
        fx.section_preamble("SYN902", "04/24/2026") + fx.header_line(100) + fx.row(130, "TSH", "1.9")])
    pipeline.run(str(labs), [], None, fx.PATIENT, 44, "male", str(tmp_path / "report.pdf"))
    assert _FakeClaude.calls == []


# Template fill, priority, provider notes, end to end ----------------------------------------------

def _marker(name, category, now_tier, then_tier=None, now=1.0, then=None, now_pct=None, then_pct=None,
            now_date="04/24/2026", then_date="01/07/2026"):
    retested = now is not None
    return Marker(name=name, category=category, unit="", kind="range", disp_range="",
                  now=now, disp_now=str(now) if retested else "", now_date_display=now_date if retested else "",
                  now_tier=now_tier, now_pct=now_pct,
                  then=then, disp_then=str(then) if then is not None else None,
                  then_date_display=then_date if then is not None else "", then_tier=then_tier, then_pct=then_pct)


def test_select_priority_marker_follows_fixed_precedence():
    optimal = _marker("Vitamin D", "Foundational", "optimal")
    moderate = _marker("TSH", "Thyroid", "moderate", then_tier="flag", then=9.0)
    worsening = _marker("HbA1c", "Metabolic", "moderate", then_tier="optimal", then=5.2)
    flagged_hormone = _marker("Estradiol", "Hormones", "flag")
    flagged_inflammation = _marker("hs-CRP", "Inflammation", "flag")
    not_retested = _marker("Ferritin", "Foundational", None, then_tier="flag", now=None, then=400.0)

    pool = [optimal, moderate, worsening, flagged_hormone, flagged_inflammation, not_retested]
    assert select_priority_marker(pool) is not_retested
    assert select_priority_marker(pool) is select_priority_marker(list(pool))
    assert select_priority_marker(pool[:-1]) is flagged_inflammation  # system order breaks the tie
    assert select_priority_marker(pool[:3]) is worsening
    assert select_priority_marker(pool[:2]) is moderate
    assert select_priority_marker([optimal]) is None


def test_every_number_and_date_in_filled_copy_comes_from_the_record():
    markers = [
        _marker("hs-CRP", "Inflammation", "flag", then_tier="moderate", now=4.4, then=2.2),
        _marker("TSH", "Thyroid", "optimal", now=1.9),
        _marker("Ferritin", "Foundational", None, then_tier="flag", now=None, then=400.0),
    ]
    copy = build_copy(PatientRecord(name="Copy Facts", markers=markers))
    text = json.dumps(copy)
    record_dates = {"04/24/2026", "01/07/2026"}
    record_values = {"4.4", "2.2", "1.9", "400.0"}
    assert set(re.findall(r"\d{2}/\d{2}/\d{4}", text)) <= record_dates
    assert set(re.findall(r"\d+\.\d+", text)) <= record_values
    assert copy["next_30_label"] == "Ferritin"
    assert copy["next_30_sub"] == "Not retested since 01/07/2026"


STRUCTURED_NOTE = """# CellDeep Provider Notes

## Consultation Note
Patient is on testosterone replacement therapy.

## Patient Concerns
- Afternoon energy dips | Systems: Repair, Pace

## Protocol
- BPC-157 | Cadence: daily
- Unlisted Compound

## Marker Targets
- TSH: 1.0-2.0

## Vitality Index
- Energy: Some Concern
- Sleep: No Concern
"""


def test_structured_provider_note_is_read_field_by_field():
    note = pipeline.parse_provider_note(STRUCTURED_NOTE)
    assert note["accepted"] is True
    assert note["pain_points"] == [{"text": "Afternoon energy dips", "categories": ["Repair", "Pace"]}]
    assert [(p["name"], p["cadence"]) for p in note["protocol"]] == [("BPC-157", "daily"), ("Unlisted Compound", None)]
    assert note["marker_overrides"] == [{"marker": "TSH", "lo": 1.0, "hi": 2.0}]
    assert note["vitality_index"]["Energy"] == "Some Concern"
    assert note["vitality_index"]["Cravings"] == "Not Assessed"


def test_unstructured_provider_note_is_rejected_and_never_read():
    note = pipeline.parse_provider_note("Pt doing well. Started BPC-157 daily, wants more energy.")
    assert note["accepted"] is False
    assert note["protocol"] == [] and note["pain_points"] == [] and note["marker_overrides"] == []
    assert any("PROVIDER NOTE REJECTED" in line for line in note["other_notes"])


def test_end_to_end_run_is_deterministic_and_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "Anthropic", _FakeClaude)
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [
        fx.section_preamble("SYN900", "04/24/2026") + fx.header_line(100, ("01/07/2026",))
        + fx.row(130, "hs-CRP", "4.4H", "2.2", units="mg/L", lab_range="0.0-3.0")
        + fx.row(144, "TSH", "1.9", "2.4", units="uIU/mL")])
    dexa = _dexa_pdf(tmp_path / "dexa.pdf")
    out = tmp_path / "report.pdf"

    review_path = pipeline.run(str(labs), [str(dexa)], STRUCTURED_NOTE, fx.PATIENT, 44, "male", str(out))

    with fitz.open(out) as rendered:
        text = "\n".join(page.get_text() for page in rendered)
    assert "hs-CRP moved from 2.2 mg/L on 01/07/2026 to 4.4 mg/L on 04/24/2026" in text.replace("\n", " ")
    assert "Afternoon energy dips" in text
    assert "34.4%" in text and "30.0%" in text
    assert "PROVIDER NOTE REJECTED" not in Path(review_path).read_text(encoding="utf-8")


def test_end_to_end_run_flags_rejected_note_for_manual_entry(tmp_path):
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [
        fx.section_preamble("SYN901", "04/24/2026") + fx.header_line(100)
        + fx.row(130, "TSH", "1.9", units="uIU/mL")])
    out = tmp_path / "report.pdf"
    review_path = pipeline.run(str(labs), [], "Started BPC-157 daily.", fx.PATIENT, 44, "male", str(out))
    review = Path(review_path).read_text(encoding="utf-8")
    assert "PROVIDER NOTE REJECTED" in review
    with fitz.open(out) as rendered:
        assert "BPC-157" not in "\n".join(page.get_text() for page in rendered)
