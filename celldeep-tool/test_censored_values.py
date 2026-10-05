"""Censored results ("<0.7", ">2000"): shown exactly as printed with the lab's flag, never scored."""

import fitz
import pytest

import pipeline
import scoring
import template
from synthetic_fixtures import deterministic_fixtures as fx

DATE = "04/14/2026"


@pytest.fixture
def record_and_notice(tmp_path):
    page = (fx.section_preamble("SYN-CENSORED", DATE) + fx.header_line(100)
            + fx.row(130, "Vitamin B12", ">2000 H", units="pg/mL", lab_range="200-1100")
            + fx.row(144, "FSH", "<0.7 L", units="mIU/mL", lab_range="1.4-12.8")
            + fx.row(158, "hs-CRP", "<0.2", units="mg/L")
            + fx.row(172, "TSH", "1.9", units="uIU/mL"))
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [page])
    extracted = pipeline.extract(str(labs), [], None, patient_name=fx.PATIENT, audit_root=str(tmp_path))
    return pipeline.score_and_build_record({**extracted, "sex": "male"})


def _marker(record, name):
    return next(m for m in record.markers if m.name == name)


def test_censored_values_stay_exactly_as_printed_with_the_lab_flag(record_and_notice):
    record, _ = record_and_notice
    b12, fsh, crp = (_marker(record, name) for name in ("Vitamin B12", "FSH", "hs-CRP"))
    assert (b12.now, b12.disp_now, b12.lab_flag_now) == (None, ">2000", "H")
    assert (fsh.now, fsh.disp_now, fsh.lab_flag_now) == (None, "<0.7", "L")
    assert (crp.disp_now, crp.lab_flag_now) == ("<0.2", None)
    for marker in (b12, fsh, crp):
        assert marker.now_tier is None and marker.now_pct is None


def test_censored_values_are_excluded_from_every_score(record_and_notice):
    record, _ = record_and_notice
    with_censored = template.build_rollups(record, None, None, False)
    record.markers = [m for m in record.markers if not scoring.is_censored(m.disp_now, m.now)]
    without_censored = template.build_rollups(record, None, None, False)
    assert with_censored[2] == without_censored[2]  # overall score
    assert {k: v["now"] for k, v in with_censored[0].items()} == {k: v["now"] for k, v in without_censored[0].items()}


def test_patient_report_shows_printed_value_flag_and_a_separate_count(record_and_notice, tmp_path):
    record, _ = record_and_notice
    copy = pipeline.build_copy(record)
    bullet = next(b for b in copy["optimization_summary_bullets"] if b.startswith("<b>Reported as a limit"))
    assert "3 results were reported by the lab as a limit" in bullet
    assert "Vitamin B12" in bullet and "FSH" in bullet and "hs-CRP" in bullet
    out = tmp_path / "report.pdf"
    template.render(record, copy, str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    assert ">2000 - lab flag High" in text
    assert "<0.7 - lab flag Low" in text
    assert "Reported as a limit, not scored" in text
    assert "needs review" not in text


def test_staff_notes_list_censored_results(record_and_notice):
    _, notice = record_and_notice
    line = next(note for note in notice.other_notes if note.startswith("CENSORED RESULTS"))
    assert "Vitamin B12 '>2000' on 04/14/2026 (lab flag H)" in line
    assert "FSH '<0.7' on 04/14/2026 (lab flag L)" in line
    assert "hs-CRP '<0.2' on 04/14/2026" in line


@pytest.mark.parametrize(("disp", "value", "censored"), [
    ("<0.7", None, True), (">2000", None, True), ("<= 5", None, True), ("≥90", None, True),
    ("0.7", 0.7, False), ("NEGATIVE", None, False), ("", None, False), (None, None, False),
])
def test_is_censored(disp, value, censored):
    assert scoring.is_censored(disp, value) is censored


def test_scanned_censored_result_keeps_the_agreed_lab_flag(tmp_path):
    from test_scan_bloodwork import MockClient, payloads
    path = fx.write_lab_pdf(tmp_path / "mixed.pdf", [
        fx.section_preamble("SYN-SCAN-DIGITAL", "02/03/2026") + fx.header_line(100)
        + fx.row(130, "TSH", "1.0"), [], []])
    extracted = pipeline.extract(str(path), [], None, patient_name="Synthetic, Pat", collected_date=DATE,
                                 client=MockClient([page for page in payloads() for _ in (1, 2)]),
                                 audit_root=str(tmp_path))
    b12 = next(o for o in extracted["marker_occurrences"] if o["name"] == "Vitamin B12" and o["date_display"] == DATE)
    assert (b12["value"], b12["disp_value"], b12["lab_flag"]) == (None, ">2000", "H")
