"""Age from the printed date of birth and the collection date; disagreeing DOBs mean no age."""

import copy
import json
import re

import fitz
import pytest

import pipeline
from synthetic_fixtures import deterministic_fixtures as fx
from test_scan_bloodwork import MockClient, payloads

COLLECTED = "04/14/2026"


def _lab_pages(dob=None, collected=COLLECTED):
    page = fx.section_preamble("SYN-AGE", collected) + fx.header_line(100) + fx.row(130, "TSH", "1.9")
    if dob:
        page.append((300, 54, f"DOB: {dob}"))
    return page


def _run(tmp_path, monkeypatch, dob=None, staff_age=None, scan_dob=None, scan_dob_second=None):
    pages = [_lab_pages(dob, collected="02/03/2026" if scan_dob is not None else COLLECTED)]
    reads = None
    if scan_dob is not None:
        pages += [[], []]
        reads = []
        for page in payloads():
            for reading in (0, 1):
                read = copy.deepcopy(page)
                read["date_of_birth"] = scan_dob if reading == 0 or scan_dob_second is None else scan_dob_second
                reads.append(read)
        monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: MockClient(reads))
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", pages)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    out = tmp_path / "report.pdf"
    pipeline.run(str(labs), [], None, "Synthetic, Pat", staff_age, "male", str(out),
                 collected_date=COLLECTED if scan_dob is not None else None)
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return text, (tmp_path / "review.txt").read_text(encoding="utf-8")


def test_age_is_computed_from_printed_dob_and_collection_date(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, dob="03/15/1982")
    assert "AGE 44" in text
    assert "By 45" in text  # the age at the next birthday, matching the hero's target
    assert "1982" not in review


def test_birthday_not_yet_reached_on_the_collection_date(tmp_path, monkeypatch):
    text, _ = _run(tmp_path, monkeypatch, dob="06/01/1982")
    assert "AGE 43" in text and "By 44" in text


def test_staff_age_that_differs_is_noted_and_the_printed_dob_wins(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, dob="03/15/1982", staff_age=40)
    assert "AGE 44" in text
    assert ("AGE CHECK: staff-entered age 40 differs from the age computed from the printed date of birth and "
            "collection date (44); the report uses 44") in review


def test_disagreeing_dob_sources_give_no_age_and_a_staff_notice(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, dob="03/15/1982", staff_age=44, scan_dob="03/15/1983")
    assert not re.search(r"AGE \d", text) and "By 4" not in text
    assert "By your next birthday" in text
    assert ("DOB CONFLICT: the printed dates of birth differ between sources (lab page 1, scanned lab page 2, "
            "scanned lab page 3)") in review
    assert "1982" not in review and "1983" not in review


def test_scan_reads_that_disagree_on_dob_are_not_a_source(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, dob="03/15/1982", scan_dob="03/15/1983",
                        scan_dob_second="03/15/1982")
    assert "AGE 44" in text and "DOB CONFLICT" not in review


def test_matching_scan_dob_confirms_the_age(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, dob="03/15/1982", scan_dob="03/15/1982")
    assert "AGE 44" in text and "DOB CONFLICT" not in review


def test_without_a_printed_dob_the_staff_age_is_used(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, staff_age=50)
    assert "AGE 50" in text and "By 51" in text and "AGE CHECK" not in review


def test_dob_after_collection_date_gives_no_age(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, dob="05/01/2026", staff_age=44)
    assert not re.search(r"AGE \d", text) and "DOB CONFLICT" in review


def test_dob_is_never_stored_in_extracted_data(tmp_path):
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [_lab_pages("03/15/1982")])
    extracted = pipeline.extract(str(labs), [], None, patient_name="Synthetic, Pat", audit_root=str(tmp_path))
    assert extracted["age_from_dob"] == 44
    assert "1982" not in json.dumps(extracted, default=str)
    for audit in tmp_path.rglob("*.json"):
        assert "1982" not in audit.read_text(encoding="utf-8")


@pytest.mark.parametrize(("dexa_dob", "conflict"), [("03/15/1982", False), ("03/15/1983", True)])
def test_dexa_dob_is_another_source(tmp_path, dexa_dob, conflict):
    from synthetic_fixtures import dexa as dexa_fx
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [_lab_pages("03/15/1982")])
    dexa = dexa_fx.write_pdf(tmp_path / "dexa.pdf", ["summary"])
    read = dexa_fx.read([dexa_fx.BASELINE], date_of_birth=dexa_dob)
    reader = dexa_fx.DexaReader(dexa, ["summary"], {"summary": (read, copy.deepcopy(read))})
    extracted = pipeline.extract(str(labs), [str(dexa)], None, patient_name=dexa_fx.PATIENT, client=reader,
                                 audit_root=str(tmp_path))
    assert extracted["age_from_dob"] == (None if conflict else 44)
    assert bool(extracted.get("dob_conflict")) is conflict
