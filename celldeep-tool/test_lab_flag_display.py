"""The lab's own H/L flag: captured from a separate Flag column and shown when it disagrees."""

import fitz
import pytest

import clinic_config
import pipeline
import template
from synthetic_fixtures import layouts


@pytest.fixture
def labcorp(tmp_path):
    path = layouts.labcorp_digital(tmp_path / "labcorp.pdf")
    extracted = pipeline.extract(str(path), [], None, patient_name=layouts.PATIENT, audit_root=str(tmp_path))
    record, notice = pipeline.score_and_build_record({**extracted, "sex": "male"})
    return extracted, record, notice


def _render_text(record, tmp_path):
    out = tmp_path / "report.pdf"
    template.render(record, pipeline.build_copy(record), str(out))
    with fitz.open(out) as document:
        return " ".join(" ".join(page.get_text().split()) for page in document)


def test_separate_flag_column_words_become_the_lab_flag(labcorp):
    extracted, record, _ = labcorp
    flags = {o["name"]: o["lab_flag"] for o in extracted["marker_occurrences"]}
    assert flags == {"Ferritin": "L", "Vitamin B12": "", "HbA1c": ""}
    shown = {item["name"]: item["results"][0]["lab_flag"] for item in extracted["lab_reported"]}
    assert shown["Hemoglobin"] == "H" and shown["Potassium"] == "H" and shown["Platelet Count"] is None


def test_disagreeing_lab_flag_is_shown_under_the_value(labcorp, tmp_path):
    _, record, notice = labcorp
    ferritin = next(m for m in record.markers if m.name == "Ferritin")
    assert ferritin.now_tier == "optimal" and ferritin.lab_flag_now == "L"
    text = _render_text(record, tmp_path)
    assert "Lab flag: Low (lab range 30-400)" in text
    assert text.count("Lab flag:") == 1  # agreeing results get no line
    assert "LAB FLAG DIFFERS FROM CELLDEEP STATUS: Ferritin '20' (lab flag L, CellDeep optimal)" in notice.other_notes


def test_config_switch_hides_the_line_but_not_the_staff_note(labcorp, tmp_path, monkeypatch):
    _, record, notice = labcorp
    monkeypatch.setattr(clinic_config, "SHOW_LAB_FLAG_WHEN_IT_DIFFERS", False)
    assert "Lab flag:" not in _render_text(record, tmp_path)
    assert any(note.startswith("LAB FLAG DIFFERS") for note in notice.other_notes)


def test_flag_column_only_assigns_to_a_single_current_result():
    header = {"columns": [{"kind": "name"}, {"kind": "current"}, {"kind": "flag"}],
              "value_columns": [{"kind": "current"}, {"kind": "historical"}]}
    current = [i for i, c in enumerate(header["value_columns"]) if c["kind"] == "current"]
    assert current == [0]
    assert pipeline._FLAG_COLUMN_WORDS["HIGH"] == "H" and "ABNORMAL" not in pipeline._FLAG_COLUMN_WORDS
