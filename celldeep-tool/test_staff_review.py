"""Staff review notes lead with scan outcomes; staff QA text never reaches the patient PDF."""

import copy

import fitz
import pytest

import pipeline
import template
from synthetic_fixtures import deterministic_fixtures as fx
from test_scan_bloodwork import MockClient, payloads
from unknown_marker_policy import STAFF_NOTE_MARKERS

DATE = "04/14/2026"


def _lab_pdf(tmp_path):
    digital = (fx.section_preamble("SYN-REVIEW", "02/03/2026") + fx.header_line(100)
               + fx.row(130, "TSH", "1.0", lab_range="0.4-4.5")
               + fx.row(144, "Novel Synthetic Assay", "3.0"))
    return fx.write_lab_pdf(tmp_path / "labs.pdf", [digital, [], []])


def _scan_reads():
    pages = payloads()
    reads = [copy.deepcopy(page) for page in pages for _ in (1, 2)]
    reads[1]["rows"][0]["result_text"] = "58"          # page 2 reads disagree on IRON, TOTAL
    reads[2]["patient_name"] = "Different, Person"      # page 3 prints another name
    return reads


@pytest.fixture
def run_report(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: MockClient(_scan_reads()))
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    out = tmp_path / "report.pdf"
    review_path = pipeline.run(str(_lab_pdf(tmp_path)), [], None, "Synthetic, Pat", 44, "male", str(out),
                               collected_date=DATE)
    with fitz.open(out) as document:
        patient_text = "\n".join(page.get_text() for page in document)
    return patient_text, (tmp_path / "review.txt").read_text(encoding="utf-8"), review_path


def test_patient_pdf_contains_no_staff_note_markers(run_report):
    patient_text, review, _ = run_report
    flat = " ".join(patient_text.split())
    for marker in STAFF_NOTE_MARKERS:
        assert marker not in flat, marker
    assert "Different" not in flat and "excluded" not in flat
    # The staff notes did contain staff material, so the check above is meaningful.
    assert "source=scan" in review and "PATIENT NAME MISMATCH" in review


def test_staff_notes_open_with_scan_batch_outcomes(run_report):
    _, review, _ = run_report
    lines = review.splitlines()
    start = lines.index("This report generated successfully. A few items need a quick human check:") + 2
    assert lines[start] == (f"SCANNED BLOODWORK - pages 2, 3 - identified by staff-entered patient name and "
                            f"Collected {DATE}")
    block = "\n".join(lines[start:lines.index("OTHER REVIEW ITEMS")])
    assert "Kept: 85 results (page 2: 53, page 3: 32)" in block
    assert "Excluded: 1 results" in block
    assert "page 2: 'IRON, TOTAL' - agreement gate" in block
    assert "PATIENT NAME MISMATCH: source=scan page 3" in block
    assert block.index("Excluded") < block.index("PATIENT NAME MISMATCH")
    assert "Unrecognized marker \"Novel Synthetic Assay\"" in review.split("OTHER REVIEW ITEMS", 1)[1]


def _dexa_extracted():
    return {"dexa_history": [{"date_display": "05/12/2025", "body_fat_pct": "33.7%"}]}


def test_text_dexa_value_missing_from_source_is_still_flagged():
    notice = pipeline.verify_extraction_completeness(
        _dexa_extracted(), dexa_text="Scan date 05/12/2025 Body Fat 31.0%")
    assert any("POSSIBLE HALLUCINATION" in note for note in notice.other_notes)


def test_render_refuses_to_write_a_patient_pdf_containing_staff_text(tmp_path):
    record, _ = pipeline.score_and_build_record({"name": "Synthetic Leak", "marker_occurrences": [
        {"name": "TSH", "date_display": DATE, "source_label": "x", "status": "final", "value": 1.0,
         "disp_value": "1.0", "is_good": None, "lab_range_lo": 0, "lab_range_hi": 0, "lab_range_display": ""}]})
    leaked = pipeline.build_copy(record)
    leaked["optimization_summary_bullets"] = ["source=scan page 2 excluded 'TSH': agreement gate"]
    out = tmp_path / "leak.pdf"
    with pytest.raises(template.StaffContentLeak):
        template.render(record, leaked, str(out))
    assert not out.exists()
