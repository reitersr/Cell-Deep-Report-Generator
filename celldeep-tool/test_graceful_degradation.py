"""A page or section that cannot be parsed deterministically is excluded, not fatal; conflicting
duplicates and contradictory identity still stop the report. Synthetic data, mocked reads only."""

import fitz
import pytest

import pipeline
from synthetic_fixtures import deterministic_fixtures as fx
from unknown_marker_policy import without_staff_check

GOOD = (fx.section_preamble("SYN-OK", "04/14/2026") + fx.header_line(100)
        + fx.row(130, "hs-CRP", "0.4", units="mg/L", lab_range="0.0-3.0")
        + fx.row(144, "TSH", "1.9", units="uIU/mL"))
BROKEN_ROW = (fx.section_preamble("SYN-BAD", "04/14/2026") + fx.header_line(100, ("02/03/2026",))
              + fx.row(130, "HbA1c", "5.3", "5.1", units="%")
              + fx.row(144, "TMAO", "29.012.4", units="uM"))  # digits run together: unreadable
UNKNOWN_HEADER = (fx.section_preamble("SYN-ODD", "04/14/2026")
                  + [(40, 100, "Test Name"), (220, 100, "Current"),
                     (300, 100, "Historical 01/13/2026 02/03/2026"), (510, 100, "Range")]
                  + fx.row(130, "Ferritin", "88", "90"))


def _run(tmp_path, monkeypatch, pages):
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", pages)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    out = tmp_path / "report.pdf"
    pipeline.run(str(labs), [], None, fx.PATIENT, 44, "male", str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return text, without_staff_check((tmp_path / "review.txt").read_text(encoding="utf-8")).splitlines()


def test_unparseable_table_on_one_page_is_excluded_and_the_report_still_builds(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, [GOOD, BROKEN_ROW])
    assert review[0] == ("INCOMPLETE - pages/sections excluded: lab PDF page(s) 2 could not be parsed "
                         "deterministically; their results are NOT in this report - review them by hand")
    assert review[1].startswith("  - page 2 (Order SYN-BAD (collected 04/14/2026)): TMAO")
    assert "runs result digits together" in review[1]
    assert "hs-CRP" in text and "TSH" in text
    # The whole table is excluded, including its readable HbA1c row: nothing is partially kept or guessed.
    assert "HbA1c" not in text and "TMAO" not in text and "29.0" not in text
    assert "INCOMPLETE" not in text


def test_unknown_layout_page_is_excluded_and_other_pages_report(tmp_path, monkeypatch):
    text, review = _run(tmp_path, monkeypatch, [GOOD, UNKNOWN_HEADER])
    assert review[0].startswith("INCOMPLETE - pages/sections excluded: lab PDF page(s) 2 ")
    assert review[1].startswith("  - page 2 (whole page): Historical column header prints more than one date")
    assert "Ferritin" not in text and "hs-CRP" in text


def test_excluded_rows_leave_no_audit_or_coverage_trace(tmp_path):
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [GOOD, BROKEN_ROW])
    extracted = pipeline.extract(str(labs), [], None, patient_name=fx.PATIENT, audit_root=str(tmp_path))
    assert {row["name"] for row in extracted["source_rows"]} == {"hs-CRP", "TSH"}
    assert [item["page"] for item in extracted["parse_exclusions"]] == [2]
    record, notice = pipeline.score_and_build_record(extracted)
    assert not any("COVERAGE GAP" in note for note in notice.other_notes)
    assert notice.incomplete[0].startswith("INCOMPLETE - ")


def test_conflicting_duplicate_results_still_stop_the_report(tmp_path, monkeypatch):
    other = (fx.section_preamble("SYN-OK2", "04/14/2026") + fx.header_line(100)
             + fx.row(130, "TSH", "2.6", units="uIU/mL"))
    with pytest.raises(pipeline.BloodworkHardStop, match="TSH on 04/14/2026: conflicting results"):
        _run(tmp_path, monkeypatch, [GOOD, other])
    assert not (tmp_path / "report.pdf").exists()


def test_one_order_printing_two_collected_dates_still_stops(tmp_path, monkeypatch):
    # The same Order ID printed with a different Collected date on one line: an identity contradiction.
    clash = ([(40, 40, "Order ID: SYN-OK   Collected: 04/15/2026")] + fx.header_line(100)
             + fx.row(130, "Ferritin", "88"))
    with pytest.raises(pipeline.BloodworkHardStop, match="prints conflicting Collected: dates"):
        _run(tmp_path, monkeypatch, [GOOD, clash])


def test_scan_result_conflicting_with_a_printed_result_still_stops(tmp_path):
    from test_scan_bloodwork import MockClient, payloads
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [
        fx.section_preamble("SYN-OK", "04/14/2026") + fx.header_line(100) + fx.row(130, "TSH", "9.9"), [], []])
    with pytest.raises(pipeline.BloodworkHardStop, match="TSH on 04/14/2026: conflicting results"):
        pipeline.extract(str(labs), [], None, patient_name="Synthetic, Pat", collected_date="04/14/2026",
                         client=MockClient([page for page in payloads() for _ in (1, 2)]), audit_root=str(tmp_path))


def test_hard_stops_are_still_bloodwork_parse_errors():
    assert issubclass(pipeline.BloodworkHardStop, pipeline.BloodworkParseError)
