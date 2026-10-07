"""Quest's scanned urinalysis prints its rows under the full panel heading "URINALYSIS, COMPLETE W/REFLEX TO CULTURE".
Those rows are urinalysis rows: urine GLUCOSE NEGATIVE never becomes blood glucose (which used to collide with the
blood result and stop the job), and OCCULT BLOOD is the lab-reported "Urinalysis — Blood". Synthetic data only
(synthetic_fixtures/quest_urinalysis.py)."""

import fitz

import lab_reported
import pipeline
from synthetic_fixtures import quest_urinalysis as qu
from synthetic_fixtures.scenarios import ScriptedVision


def _run(tmp_path, monkeypatch):
    labs = qu.write_labs(tmp_path / "labs.pdf")
    vision = ScriptedVision({(str(labs), number): [page, page] for number, page in qu.scan_reads().items()})
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kw: vision)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    captured = {}
    original = pipeline.template.render
    monkeypatch.setattr(pipeline.template, "render", lambda record, copy, out, **kw: (
        captured.update(record=record), original(record, copy, out, **kw)))
    pipeline.run(str(labs), [], None, qu.PATIENT, 44, "male", str(tmp_path / "report.pdf"), vitality_index={},
                 collected_date=qu.COLLECTED, confirm=lambda items: True)
    with fitz.open(tmp_path / "report.pdf") as document:
        text = " ".join(page.get_text() for page in document)
    return captured["record"], text


def test_a_heading_that_contains_urinalysis_is_a_urinalysis_section():
    for heading in ("URINALYSIS", "Urinalysis", qu.URINALYSIS, "MICROSCOPIC URINALYSIS"):
        assert lab_reported.is_urinalysis(heading), heading
        assert pipeline._match_row_name("GLUCOSE", heading) is None, heading
        assert lab_reported.lookup("GLUCOSE", heading) == ("Urinalysis — Glucose", lab_reported.URINALYSIS)
        assert lab_reported.lookup("OCCULT BLOOD", heading) == ("Urinalysis — Blood", lab_reported.URINALYSIS)
    for heading in (None, qu.CMP, "CBC (INCLUDES DIFF/PLT)"):
        assert not lab_reported.is_urinalysis(heading), heading
    assert pipeline._match_row_name("GLUCOSE", qu.CMP)[0] == "Glucose (fasting)"


def test_urine_glucose_under_the_quest_heading_never_collides_with_blood_glucose(tmp_path, monkeypatch):
    record, text = _run(tmp_path, monkeypatch)  # used to raise BloodworkHardStop (conflicting Glucose results)
    blood = [(m.disp_then, m.disp_now) for m in record.markers if m.name == "Glucose (fasting)"]
    blood += [(r["disp_value"],) for item in record.lab_reported if item.name.startswith("Glucose")
              for r in item.results]
    assert blood and all(set(values) - {None} == {qu.BLOOD_GLUCOSE} for values in blood)
    urine = {item.name: [r["disp_value"] for r in item.results] for item in record.lab_reported
             if item.group == lab_reported.URINALYSIS}
    assert urine["Urinalysis — Glucose"] == ["NEGATIVE"]
    assert urine["Urinalysis — Blood"] == ["NEGATIVE"]
    assert urine["Urinalysis — Ketones"] == ["1+"]
    assert not any(m.name == "Urinalysis — Occult Blood" for m in record.markers)
