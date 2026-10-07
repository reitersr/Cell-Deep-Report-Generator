"""Two live-read failures on a real scanned Quest draw, rebuilt from synthetic data (synthetic_fixtures/
scan_mixed_reads.py, synthetic_fixtures/quest_urinalysis.py):

1. Mixed reads: a row printed by one read and simply absent from the others ("not read / 1214 H / not read") is a
   missing read, not a disagreement. The page is read again, targeted at the missing rows, up to
   scan_bloodwork.SCAN_TARGETED_MAX_READS reads in all; the row is kept once two reads print it identically, never
   from a single read, and excluded with the usual notice when that never happens.
2. Section carry-over: Quest prints the urinalysis heading at the foot of one page and the rest of the urine rows on
   the next page with no heading. Those rows are urinalysis rows (lab-reported "Urinalysis — ..."), never blood tests;
   rows printed after the urine block stay blood tests. Printed labels with no result are not unrecognized tests."""

import fitz
import pytest

import lab_reported
import pipeline
import scan_bloodwork
from synthetic_fixtures import quest_urinalysis as qu
from synthetic_fixtures import scan_mixed_reads as mixed
from synthetic_fixtures.scenarios import ScriptedVision


def _run(tmp_path, monkeypatch, labs, reads_by_page, collected):
    vision = ScriptedVision({(str(labs), number): reads for number, reads in reads_by_page.items()})
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kw: vision)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    captured = {}
    original = pipeline.template.render
    monkeypatch.setattr(pipeline.template, "render", lambda record, copy, out, **kw: (
        captured.update(record=record), original(record, copy, out, **kw)))
    pipeline.run(str(labs), [], None, layouts_patient(), 44, "male", str(tmp_path / "report.pdf"),
                 vitality_index={}, collected_date=collected, confirm=lambda items: True)
    return captured["record"], (tmp_path / "review.txt").read_text(encoding="utf-8"), vision


def layouts_patient():
    return qu.PATIENT


def _values(record, name):
    for marker in record.markers:
        if marker.name == name:
            return [v for v in (marker.disp_then, marker.disp_now) if v]
    for item in record.lab_reported:
        if item.name == name:
            return [r["disp_value"] for r in item.results]
    return []


# --- 1. mixed reads ------------------------------------------------------------------------------------------------

def test_a_row_missing_from_some_reads_is_read_again_and_kept_once_two_reads_agree(tmp_path, monkeypatch):
    labs = mixed.write_labs(tmp_path / "labs.pdf")
    record, review, vision = _run(tmp_path, monkeypatch, labs, {1: mixed.reads(mixed.RESOLVED)}, mixed.COLLECTED)
    assert len(vision.calls) == 4  # two reads, the usual third, one targeted read: agreement on the fourth
    assert _values(record, "Testosterone, Total") == ["1214"]
    assert any(item.name == "Free Testosterone" and [r["disp_value"] for r in item.results] == ["204.0"]
               for item in record.lab_reported) or _values(record, "Free Testosterone") == ["204.0"]
    assert "row excluded: TESTOSTERONE" not in review
    targeted = vision.calls[3]["messages"][0]["content"][1]["text"]
    assert targeted.startswith("Transcribe PDF page 1") and "TESTOSTERONE, TOTAL, MS" in targeted


def test_a_value_only_one_read_ever_prints_is_never_accepted(tmp_path, monkeypatch):
    labs = mixed.write_labs(tmp_path / "labs.pdf")
    record, review, vision = _run(tmp_path, monkeypatch, labs, {1: mixed.reads(mixed.NEVER)}, mixed.COLLECTED)
    assert len(vision.calls) == scan_bloodwork.SCAN_TARGETED_MAX_READS == 5
    assert _values(record, "Testosterone, Total") == []
    assert ("INCOMPLETE - row excluded: TESTOSTERONE, TOTAL, MS (reads disagree: not read / 1214 H / not read / "
            "not read / not read) - scanned lab page 1; not in this report") in review
    assert _values(record, "PSA Total") == ["0.61"]  # the rows every read agrees on are untouched


def test_targeted_reads_are_only_for_missing_rows_and_disagreeing_values_keep_the_three_read_rule():
    def page(*rows):
        return {"page": 1, "rows": [dict(name=n, result_text=v, flag=None, reference_range=None, lab_code=None,
                                         column="in_range", page=1, illegible=False, section=None) for n, v in rows]}
    missing = [page(("TSH", "1.9")), page(("TSH", "1.9"), ("FERRITIN", "88")), page(("TSH", "1.9"))]
    assert scan_bloodwork.rows_missing_from_reads(missing) == ["FERRITIN"]
    conflicting = [page(("FERRITIN", "88")), page(("FERRITIN", "86")), page(("FERRITIN", "83"))]
    assert scan_bloodwork.rows_missing_from_reads(conflicting) == []  # a value disagreement is not a missing read
    agreed = [page(("FERRITIN", "88")), page(("FERRITIN", "88")), page()]
    assert scan_bloodwork.rows_missing_from_reads(agreed) == []  # two reads already agree
    # A tie between two values each printed twice is no agreement.
    tie = [[r] for r in page(("X", "1"), ("X", "1"), ("X", "2"), ("X", "2"))["rows"]]
    assert scan_bloodwork._agreed(tie, 2) is None


# --- 2. section carry-over ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("heading_on_page_one", [True, False])
def test_urine_rows_on_a_page_with_no_heading_are_urinalysis_rows(tmp_path, monkeypatch, heading_on_page_one):
    labs = qu.write_labs(tmp_path / "labs.pdf")
    reads = {number: [page, page] for number, page in qu.continued_scan_reads(heading_on_page_one).items()}
    record, review, _ = _run(tmp_path, monkeypatch, labs, reads, qu.COLLECTED)  # used to stop or leave rows out
    urine = {item.name: [r["disp_value"] for r in item.results] for item in record.lab_reported
             if item.group == lab_reported.URINALYSIS}
    for name, value in (("Glucose", "NEGATIVE"), ("Blood", "NEGATIVE"), ("Ketones", "1+"), ("pH", "6.0"),
                        ("Hyaline Cast", "NONE SEEN"), ("Reflexive Urine Culture", "NO CULTURE INDICATED")):
        assert urine.get(f"Urinalysis — {name}") == [value], name
    assert len(urine) == len(qu.URINE_ON_PAGE_TWO) + (3 if heading_on_page_one else 0)
    assert _values(record, "Glucose (fasting)") + _values(record, "Glucose (non-fasting)") == [qu.BLOOD_GLUCOSE]
    assert _values(record, "Cortisol, Total (AM)") == ["13.0"]  # printed after the urine block: still a blood test
    assert _values(record, "C-Reactive Protein") == ["<3.0"]
    assert "Unrecognized marker" not in review


def test_carry_over_stops_at_the_first_row_that_is_not_a_urine_test_and_at_a_printed_heading():
    def row(page, name, section=None):
        return {"page": page, "name": name, "section": section}
    rows = [row(1, "GLUCOSE", "COMPREHENSIVE METABOLIC PANEL"), row(1, "COLOR", qu.URINALYSIS),
            row(2, "PH"), row(2, "GLUCOSE"), row(2, "C-REACTIVE PROTEIN"), row(2, "PROTEIN"),
            row(2, "FOLATE", "VITAMIN B12/FOLATE PANEL"), row(3, "BILIRUBIN, TOTAL"), row(5, "PH")]
    out = scan_bloodwork.carry_sections(rows, urine_note_pages=set())
    assert [r["section"] for r in out] == [
        "COMPREHENSIVE METABOLIC PANEL", qu.URINALYSIS,
        qu.URINALYSIS, qu.URINALYSIS, None, None,       # the urine run ends at C-REACTIVE PROTEIN
        "VITAMIN B12/FOLATE PANEL", "VITAMIN B12/FOLATE PANEL",  # page 3 continues page 2's last section
        None]                                           # page 5 does not follow page 4 (no page 4 rows): no carry
    noted = scan_bloodwork.carry_sections([row(7, "GLUCOSE"), row(7, "KETONES"), row(7, "TSH")], urine_note_pages={7})
    assert [r["section"] for r in noted] == ["URINALYSIS", "URINALYSIS", None]


def test_printed_labels_with_no_result_are_not_unrecognized_tests():
    def item(name, cells=(), raw_value=""):
        return {"raw_name": name, "raw_value": raw_value, "cells": [
            {"present": True, "status": status, "disp_value": value} for status, value in cells]}
    rows = [item("CBC with Differential", [("not_performed", "")], "TNP"),
            item("Automated Differential", [("not_performed", "")], "TNP"), item("Comments", [], "SEE NOTE"),
            item("Comments:", [("value", "see below")]), item("Omega-3 total", [("value", "5.3")])]
    results, labels = pipeline.split_no_result_rows(rows)
    assert [r["raw_name"] for r in results] == ["Omega-3 total"]
    assert [r["raw_name"] for r in labels] == ["CBC with Differential", "Automated Differential", "Comments",
                                               "Comments:"]
