"""Two regressions of a live report against its verified version, rebuilt from synthetic data:

1. The upload form's sex was left at "Not specified", so every sex-specific marker fell back to the lab's printed range
   (Testosterone, Total "lab range 250-1100", SHBG "lab range 16.5-55.9") and the DEXA score disappeared. A marker with
   a CellDeep threshold is always scored against it: with no sex on the form, the sex the lab report itself prints is
   used (one sex, every page agreeing; staff note); with neither, the staff notes say so. A lab "H" flag never changes
   a man's testosterone status.
2. A body fat printed "11.3 (e)" is accepted, labelled estimated, and still scored (Structure "% optimized")."""

import fitz
import pytest

import pipeline
import scan_dexa
from synthetic_fixtures import deterministic_fixtures as fx
from synthetic_fixtures import dexa as dexa_fx
from synthetic_fixtures import scan_mixed_reads as mixed
from synthetic_fixtures.scenarios import ScriptedVision

DRAW = "03/02/2026"
SCAN_DAY = "03/20/2026"


def _labs(path, gender="Male"):
    items = fx.section_preamble("SYN-SEX-1", DRAW)
    if gender:
        items.append((fx.LAB_X["name"], 82, f"Gender: {gender}"))
    items += fx.header_line(100)
    items += fx.row(128, "Testosterone, Total", "506", units="ng/dL", lab_range="250-1100")
    items += fx.row(141, "SHBG", "52.0", units="nmol/L", lab_range="16.5-55.9")
    items += fx.row(154, "TSH", "1.9", units="uIU/mL", lab_range="0.40-4.50")
    return fx.write_lab_pdf(path, [items])


def _run(tmp_path, monkeypatch, labs, sex, reads=None, dexa=None, collected=DRAW):
    """reads: {(file, page number): [read 1, read 2, ...]} for the scripted vision model."""
    vision = ScriptedVision(reads or {})
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kw: vision)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    captured = {}
    original = pipeline.template.render
    monkeypatch.setattr(pipeline.template, "render", lambda record, copy, out, **kw: (
        captured.update(record=record, copy=copy), original(record, copy, out, **kw)))
    pipeline.run(str(labs), [str(dexa)] if dexa else [], None, mixed.PATIENT, 46, sex, str(tmp_path / "report.pdf"),
                 vitality_index={}, collected_date=collected, confirm=lambda items: True)
    return captured["record"], captured["copy"], (tmp_path / "review.txt").read_text(encoding="utf-8")


def _marker(record, name):
    return next(m for m in record.markers if m.name == name)


# --- 1. thresholds -------------------------------------------------------------------------------------------------

def test_with_no_sex_on_the_form_the_sex_the_lab_prints_selects_the_celldeep_thresholds(tmp_path, monkeypatch):
    record, _, review = _run(tmp_path, monkeypatch, _labs(tmp_path / "labs.pdf"), None)
    assert record.sex == "male"
    testosterone, shbg = _marker(record, "Testosterone, Total"), _marker(record, "SHBG")
    assert (testosterone.range_source, testosterone.disp_range, testosterone.now_tier) == ("celldeep", "600–900",
                                                                                           "moderate")
    assert (shbg.range_source, shbg.disp_range, shbg.now_tier) == ("celldeep", "20–50", "moderate")
    assert "SEX: male - not selected on the upload form; the lab report prints it" in review


def test_with_no_sex_on_the_form_or_the_lab_report_staff_are_told_to_select_it(tmp_path, monkeypatch):
    record, _, review = _run(tmp_path, monkeypatch, _labs(tmp_path / "labs.pdf", gender=None), None)
    assert record.sex is None
    assert "SEX NOT KNOWN: no sex was selected on the upload form and the lab report prints none" in review


def test_a_lab_h_flag_never_changes_a_mans_testosterone_status(tmp_path, monkeypatch):
    def tier(flag, folder):
        folder.mkdir()
        reads = mixed.reads((True, True))
        for read in reads:
            for row in read["rows"]:
                if row["name"] == mixed.TOTAL_T[0]:
                    row["flag"] = flag
        labs = mixed.write_labs(folder / "labs.pdf")
        record, _, _ = _run(folder, monkeypatch, labs, "male", {(str(labs), 1): reads}, collected=mixed.COLLECTED)
        marker = _marker(record, "Testosterone, Total")
        assert (marker.range_source, marker.disp_range, marker.disp_now) == ("celldeep", "600–900",
                                                                            mixed.TOTAL_T[1])
        return marker.now_tier, marker.lab_flag_now
    flagged, plain = tier("H", tmp_path / "h"), tier(None, tmp_path / "none")
    assert flagged == (plain[0], "H") and plain[1] in ("", None)


# --- 2. "(e)" body fat ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sex", ["male", None])  # None: the lab report prints "Gender: Male"
@pytest.mark.parametrize("second_read", ["11.3 (e)", "11.3"])  # a live read may drop the marker
def test_a_body_fat_printed_with_e_is_estimated_and_still_scored(tmp_path, monkeypatch, second_read, sex):
    labs = _labs(tmp_path / "labs.pdf")
    dexa = dexa_fx.write_pdf(tmp_path / "dexa.pdf", ["segmental"])
    reads = [dexa_fx.read([dexa_fx.scan(SCAN_DAY, "158.4", "17.2", "134.6", body_fat, age="45.4")], patient_name=None)
             for body_fat in ("11.3 (e)", second_read)]
    record, copy, review = _run(tmp_path, monkeypatch, labs, sex, {(str(dexa), 1): reads}, dexa)
    scan = record.dexa_history[-1]
    assert (scan.date_display, scan.body_fat_pct) == (SCAN_DAY, "11.3%")
    assert "body_fat_pct" in scan.estimated and "body_fat_pct" not in scan.computed
    roll = pipeline.template.build_rollups(record, None, None, False)[0]
    assert roll["Structure"]["now"] is not None  # the Structure "% optimized" is shown and scored
    assert "DEXA SCORE NOT SHOWN" not in review
    with fitz.open(tmp_path / "report.pdf") as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    assert "11.3% (estimated)" in text and "OPTIMIZED" in text


def test_the_dexa_prompt_carries_an_e_printed_before_a_row_label_to_that_rows_values():
    assert '"(e) Total"' in scan_dexa.DEXA_PROMPT and "every value of that row" in scan_dexa.DEXA_PROMPT
