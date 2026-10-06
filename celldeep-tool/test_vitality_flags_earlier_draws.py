"""Three report rules, end to end on synthetic data:
1. Vitality Index answers come only from the upload form or a read provider note; with neither, the section
   shows "Not provided" and feeds no score (never "No Concern"). The form's "Not Assessed" default no longer
   hides the note's answers; a form/note disagreement leaves that domain out.
2. The lab's own flag is shown under every current result the lab flagged unless CellDeep already calls it
   Flagged.
3. The summary lists the systems whose current results come from a draw earlier than the headline date."""

import re

import fitz
import pytest

import pipeline
import scoring
import template
from schema import Marker
from synthetic_fixtures import deterministic_fixtures as fx

FORM_DEFAULT = {label: "Not Assessed" for label in scoring.VITALITY_LABELS}
NOTE = """## Consultation Note
Synthetic follow-up visit.

## Vitality Index
- Energy: Some Concern
- Sleep: No Concern
"""


def _run(tmp_path, monkeypatch, pages, note=None, vitality=None, name="run"):
    folder = tmp_path / name
    folder.mkdir()
    labs = fx.write_lab_pdf(folder / "labs.pdf", pages)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda _name: str(folder / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda _name: str(folder / "diag"))
    out = folder / "report.pdf"
    pipeline.run(str(labs), [], note, fx.PATIENT, 44, "male", str(out), vitality_index=vitality)
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return text, (folder / "review.txt").read_text(encoding="utf-8")


ONE_DRAW = [fx.section_preamble("SYN-VIT", "04/14/2026") + fx.header_line(100)
            + fx.row(130, "TSH", "1.9", units="uIU/mL", lab_range="0.40-4.50")]


# --- 1. Vitality Index source ----------------------------------------------------------------------------

def test_no_source_shows_not_provided_and_no_score(tmp_path, monkeypatch):
    # The production case: the note was not read and the form was left at its default.
    text, review = _run(tmp_path, monkeypatch, ONE_DRAW, note="Testosterone cypionate 100 mg weekly",
                        vitality=FORM_DEFAULT)
    assert "Not provided NOT PROVIDED. No Vitality Index answers were provided for this report" in text
    assert "No Concern" not in text and "100%" not in text
    staff_check = review.split("(end of STAFF CHECK)")[0]
    assert "  Vitality Index: not provided (no answer on the upload form or in a read provider note)" in staff_check


def test_symptom_score_is_left_out_of_the_overall_score_without_answers():
    record = pipeline.PatientRecord(name="Synthetic", sex="male", vitality_index=scoring.normalize_vitality_index({}))
    assert scoring.symptom_percent_optimized(record.vitality_index) is None
    values, sources, notes = pipeline.resolve_vitality(FORM_DEFAULT, {})
    assert values == {} and sources == {} and notes == []


def test_a_read_note_supplies_answers_the_form_left_at_its_default(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, ONE_DRAW, note=NOTE, vitality=FORM_DEFAULT)
    assert re.search(r"Energy Some Concern .*Sleep No Concern", text)
    assert "Not provided" not in text
    assert "  Vitality Index: 2 of 7 domains from the provider note: Energy Some Concern, Sleep No Concern" in review


def test_form_answers_are_used_and_a_disagreement_is_left_out():
    form = {**FORM_DEFAULT, "Energy": "No Concern", "Cravings": "Some Concern"}
    values, sources, notes = pipeline.resolve_vitality(form, {"Energy": "Some Concern", "Sleep": "No Concern",
                                                              "Cravings": "Not Assessed"})
    assert values == {"Sleep": "No Concern", "Cravings": "Some Concern"}
    assert sources == {"Sleep": "note", "Cravings": "form"}
    assert notes == ["VITALITY INDEX CONFLICT: Energy is 'No Concern' on the upload form and 'Some Concern' in the "
                     "provider note; left out of the report and the score - confirm with the provider"]


def test_all_seven_no_concern_from_the_form_asks_staff_to_confirm(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, ONE_DRAW, vitality={label: "No Concern"
                                                                   for label in scoring.VITALITY_LABELS})
    assert "100%" in text  # staff entered them: they are used
    assert "all seven 'No Concern': confirm these are this patient's answers" in review


def test_the_upload_form_never_restores_a_previous_patient_s_answers():
    from pathlib import Path
    form = (Path(pipeline.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<form action="/generate"[^>]*autocomplete="off"', form)


# --- 2. Lab flag line ------------------------------------------------------------------------------------

def _marker(tier, flag="H", disp="1193", value=1193.0):
    return Marker(name="Testosterone, Total", category="Hormones", unit="ng/dL", kind="range", disp_range="",
                  now=value, disp_now=disp, now_tier=tier, lab_flag_now=flag,
                  lab_range_now={"lo": 250, "hi": 1100, "display": "250-1100"})


@pytest.mark.parametrize(("tier", "shown"), [("optimal", True), ("moderate", True), (None, True), ("flag", False)])
def test_lab_flag_line_unless_celldeep_already_flags_it(tier, shown):
    line = template._lab_flag_line(_marker(tier))
    assert ("Lab flag: High (lab range 250-1100)" in line) is shown


def test_no_line_without_a_lab_flag_or_for_a_censored_result():
    assert template._lab_flag_line(_marker("moderate", flag=None)) == ""
    assert template._lab_flag_line(_marker(None, disp=">1500", value=None)) == ""  # flag shown next to it


# --- 3. Systems using earlier draws ----------------------------------------------------------------------

EARLIER = fx.section_preamble("SYN-EARLY-1", "01/12/2026") + fx.header_line(100) + [
    *fx.row(130, "TSH", "2.6", units="uIU/mL", lab_range="0.40-4.50"),
    *fx.row(144, "HbA1c", "5.4", units="%")]
LATER_TSH = fx.section_preamble("SYN-EARLY-2", "03/02/2026") + fx.header_line(100) + [
    *fx.row(130, "TSH", "2.1", units="uIU/mL", lab_range="0.40-4.50"),
    *fx.row(144, "HbA1c", "5.3", units="%")]
HEADLINE = fx.section_preamble("SYN-EARLY-3", "04/14/2026") + fx.header_line(100) + [
    *fx.row(130, "hs-CRP", "0.4", units="mg/L", lab_range="0.0-3.0")]


def test_summary_lists_systems_using_results_from_earlier_draws(tmp_path, monkeypatch):
    text, _ = _run(tmp_path, monkeypatch, [EARLIER, LATER_TSH, HEADLINE])
    match = re.search(r"Results from earlier draws: (.*?)\.(?: •|$)", text)
    assert match, text
    assert match[1].startswith("These systems use results from draws before 04/14/2026: ")
    assert "TSH (03/02/2026)" in match[1] and "HbA1c (03/02/2026)" in match[1]
    assert "hs-CRP" not in match[1]


def test_no_line_when_every_current_result_is_from_the_headline_draw(tmp_path, monkeypatch):
    text, _ = _run(tmp_path, monkeypatch, ONE_DRAW)
    assert "Results from earlier draws" not in text
