"""The first female patient type (Cleveland HeartLab, an undated scanned draw plus a dated digital report, four DEXA
scans): undated scanned pages are dated only by an exact Historical-column match; the DEXA scan paired with the latest
bloodwork; cycle-phase hormones and female results with no CellDeep female threshold are lab-reported, never scored;
the female staff-review flag. Synthetic fixture synthetic_fixtures/female_chl.py, invented values, scripted reads."""

import copy
import io
import time
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

import clinic_config
import pipeline
from synthetic_fixtures import female_chl as fc
from synthetic_fixtures import dexa as dexa_fx
from synthetic_fixtures.scenarios import ScriptedVision, run_scenario
from unknown_marker_policy import without_staff_check


@pytest.fixture(scope="module")
def female(tmp_path_factory):
    return run_scenario("female_chl_scanned_undated", tmp_path_factory.mktemp("female"))


def _run(tmp_path, monkeypatch, scan_reads=None, sex="female", **kwargs):
    labs = fc.write_labs(tmp_path / "labs.pdf")
    dexa = dexa_fx.write_pdf(tmp_path / "dexa.pdf", fc.DEXA_LABELS)
    script = fc.dexa_pages()
    reads = {(str(dexa), number): script[label] for number, label in enumerate(fc.DEXA_LABELS, 1)}
    reads.update({(str(labs), number): [page, page] for number, page in (scan_reads or fc.scan_reads()).items()})
    vision = ScriptedVision(reads)
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kw: vision)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    captured = {}
    original = pipeline.template.render
    monkeypatch.setattr(pipeline.template, "render", lambda record, copy, out, **kw: (
        captured.update(record=record), original(record, copy, out, **kw)))
    pipeline.run(str(labs), [str(dexa)], None, fc.PATIENT, fc.AGE, sex, str(tmp_path / "report.pdf"),
                 vitality_index={}, collected_date=fc.LATEST, confirm=lambda items: True, **kwargs)
    with fitz.open(tmp_path / "report.pdf") as document:
        text = " ".join(page.get_text() for page in document)
    return captured["record"], (tmp_path / "review.txt").read_text(encoding="utf-8"), text


def _values(record):
    found = {}
    for marker in record.markers:
        for date, value in ((marker.then_date_display, marker.disp_then), (marker.now_date_display, marker.disp_now),
                            *((h["date_display"], h["disp_value"]) for h in marker.full_history)):
            if date and value:
                found.setdefault((marker.name, date), set()).add(value)
    for item in record.lab_reported:
        for result in item.results:
            found.setdefault((item.name, result["date_display"]), set()).add(result["disp_value"])
    return found


# --- 1. undated scanned draw ---------------------------------------------------------------------------------------

def test_undated_scanned_pages_are_dated_by_the_historical_column_and_shown_once(female):
    review = without_staff_check(female["review"])
    assert "UNDATED SCANNED DRAW: lab PDF pages 1, 2 print no Collected date; date 06/10/2025 assigned from " \
           "historical-column match (18 markers)" in review
    assert "identified by staff-entered patient name; Collected 06/10/2025 assigned from historical-column match" in review
    assert "scanned lab page: no name printed - confirm" in female["review"]
    assert female["confirmation"] == []


def test_the_dated_draw_values_and_scanned_only_tests(tmp_path, monkeypatch):
    record, review, _ = _run(tmp_path, monkeypatch)
    found = _values(record)
    assert found[("hs-CRP", fc.EARLIER)] == {"2.4"} and found[("hs-CRP", fc.LATEST)] == {"0.9"}
    assert found[("Bioavailable Testosterone", fc.EARLIER)] == {"2.4"}  # printed only on the scanned pages
    hs_crp = next(m for m in record.markers if m.name == "hs-CRP")
    assert [h["date_display"] for h in hs_crp.full_history].count(fc.EARLIER) + [
        hs_crp.then_date_display, hs_crp.now_date_display].count(fc.EARLIER) == 1  # one value, not two
    # Fasting not printed for the undated draw: the non-fasting rule; its collection time is not recorded.
    assert ("Glucose (non-fasting)", fc.EARLIER) in found and ("Glucose (fasting)", fc.EARLIER) not in found
    cortisol = next(item for item in record.lab_reported if item.name == "Cortisol, Total")
    notes = {r["date_display"]: r.get("note", "") for r in cortisol.results}
    assert notes[fc.EARLIER].endswith("time not recorded")
    assert notes[fc.LATEST].endswith("collected 08:40 AM (inside the lab's printed AM window 6-10)")


def test_too_few_matches_stop_the_report_and_name_the_pages(tmp_path, monkeypatch):
    with pytest.raises(pipeline.UndatedScanNotMatched) as blocked:
        _run(tmp_path, monkeypatch, scan_reads=fc.scan_reads(fc.SHARED[:3]))
    message = str(blocked.value)
    assert message.startswith("Report not generated: lab PDF pages 1, 2 are scanned pages that print no Collected date")
    assert "06/10/2025: 9 matching, 0 conflicting" in message and "at least 10 matching results" in message
    assert "Scanned Pages Collected Date" in message
    assert not (tmp_path / "report.pdf").exists()


def test_one_conflicting_result_stops_the_report(tmp_path, monkeypatch):
    shared = copy.deepcopy(fc.SHARED)
    shared[0] = ("hs-CRP", "0.9", "2.5", "mg/L", "0.0-3.0")  # the later report's historical column prints 2.4
    reads = fc.scan_reads(shared)
    with pytest.raises(pipeline.UndatedScanNotMatched, match="1 conflicting"):
        _run(tmp_path, monkeypatch, scan_reads=reads)


def test_a_staff_entered_scanned_pages_date_is_used_as_entered(tmp_path, monkeypatch):
    record, review, _ = _run(tmp_path, monkeypatch, scan_reads=fc.scan_reads(fc.SHARED[:3]),
                             scan_collected_date="06/10/2025")
    assert _values(record)[("hs-CRP", fc.EARLIER)] == {"2.4"}
    assert "UNDATED SCANNED DRAW" not in review
    assert "identified by staff-entered patient name and Collected 06/10/2025" in review


def test_the_undated_rule_needs_the_staff_date_to_be_the_digital_reports_own_date():
    # A scanned draw identified by its own staff-entered date (the "mixed" layout) is unchanged: see its expected file.
    assert pipeline.digital_draw_dates([{"source_label": "Order X (collected 05/12/2026); Lipids"}], []) == {
        (2026, 5, 12)}


# --- 2. DEXA pairing -----------------------------------------------------------------------------------------------

def _scan(date_display, pct="25.0"):
    return {"date_display": date_display, "body_fat_pct": pct, "total_mass_lb": None, "fat_mass_lb": None,
            "lean_mass_lb": None}


def test_the_latest_scan_within_60_days_is_current_and_later_scans_are_left_out():
    history = [_scan("06/12/2025"), _scan("04/20/2026"), _scan("07/25/2026"), _scan("08/30/2026")]
    kept, pairing = pipeline.pair_dexa_with_bloodwork(history, "05/12/2026")
    assert [r["date_display"] for r in kept] == ["06/12/2025", "04/20/2026"]
    assert pairing["current"] == "04/20/2026" and pairing["excluded"] == ["07/25/2026", "08/30/2026"]
    assert "excluded, not in this report" in pairing["line"] and "07/25/2026 (74 days after the bloodwork)" in \
        pairing["line"]


def test_the_latest_scan_inside_the_window_wins_over_a_nearer_earlier_one():
    # 11 days before and 34 days after the draw, both inside the window: the later scan is current, the earlier one
    # is history (the clinic's rule: latest scan within 60 days either side, not the nearest).
    history = [_scan("11/09/2025"), _scan("03/05/2026"), _scan("04/19/2026")]
    kept, pairing = pipeline.pair_dexa_with_bloodwork(history, "03/16/2026")
    assert pairing["current"] == "04/19/2026" and kept == history and pairing["excluded"] == []
    assert "current scan 04/19/2026 (34 days after the latest bloodwork, 03/16/2026" in pairing["line"]
    assert "history: 11/09/2025, 03/05/2026" in pairing["line"]


def test_no_scan_within_60_days_keeps_todays_behaviour_and_a_scan_after_the_draw_can_be_current():
    history = [_scan("01/02/2026"), _scan("09/30/2026")]
    assert pipeline.pair_dexa_with_bloodwork(history, "05/12/2026") == (history, None)
    after = [_scan("04/01/2026"), _scan("05/30/2026")]  # 41 days before / 18 days after: the later one
    kept, pairing = pipeline.pair_dexa_with_bloodwork(after, "05/12/2026")
    assert pairing["current"] == "05/30/2026" and kept == after and pairing["excluded"] == []
    both = [_scan("05/02/2026"), _scan("05/22/2026"), _scan("07/20/2026")]  # 10 before, 10 after, 69 after
    kept, pairing = pipeline.pair_dexa_with_bloodwork(both, "05/12/2026")
    assert pairing["current"] == "05/22/2026" and pairing["excluded"] == ["07/20/2026"]
    edge = [_scan("03/13/2026"), _scan("07/11/2026")]  # exactly 60 days before and 60 days after: both inside
    kept, pairing = pipeline.pair_dexa_with_bloodwork(edge, "05/12/2026")
    assert pairing["current"] == "07/11/2026" and pairing["excluded"] == []


def test_excluded_scans_reach_nothing_in_the_report(female):
    for date in ("07/25/2026", "08/30/2026", "148.0", "146.9"):
        assert date not in female["text"]
    assert "DEXA PAIRING: current scan 04/20/2026 (22 days before the latest bloodwork, 05/12/2026" in female["review"]


# --- 3/4. cycle-phase hormones, female ranges ----------------------------------------------------------------------

def test_cycle_phase_hormones_are_lab_reported_with_no_flag_and_the_printed_ranges_go_to_staff(tmp_path, monkeypatch):
    record, review, text = _run(tmp_path, monkeypatch)
    estradiol = next(item for item in record.lab_reported if item.name == "Estradiol")
    assert all(r["lab_flag"] is None and not r.get("lab_range") for r in estradiol.results)
    assert {r.get("note") for r in estradiol.results} == {clinic_config.CYCLE_PHASE_NOTE}
    assert not any(m.name in clinic_config.CYCLE_PHASE_HORMONES for m in record.markers)
    assert "CYCLE PHASE NOT RECORDED: Estradiol (06/10/2025: 77; 05/12/2026: 142) - reference range depends on " \
           "cycle phase (not recorded); shown lab-reported, not scored, no flag. The lab's printed ranges: " \
           "Follicular phase: 21 - 251 pg/mL; Postmenopausal: <6 - 54 pg/mL" in review
    assert "Follicular phase" not in text  # staff notes only


def test_a_female_result_without_a_celldeep_female_threshold_is_never_scored(tmp_path, monkeypatch):
    record, review, _ = _run(tmp_path, monkeypatch)
    dhea = next(item for item in record.lab_reported if item.name == "DHEA-S")
    assert {r["lab_range"] for r in dhea.results if r["date_display"] == fc.LATEST} == {"57.3-279.2"}
    assert not any(m.name in ("DHEA-S", "SHBG", "Cortisol, Total (AM)") for m in record.markers)
    assert "FEMALE RANGE NOT CONFIRMED: DHEA-S (06/10/2025: 201.7; 05/12/2026: 188.2)" in review
    assert "missing threshold for DHEA-S" not in review  # never scored against the lab's printed range


def test_the_female_staff_review_flag(tmp_path, monkeypatch):
    record, review, text = _run(tmp_path, monkeypatch)
    assert review.splitlines()[0] == "FEMALE RANGES NOT CLINIC-CONFIRMED - STAFF REVIEW ONLY, DO NOT RELEASE"
    assert record.draft_label == "DRAFT - staff review required before release"
    assert "DRAFT - staff review required before release" in text


def test_the_flag_follows_the_printed_lab_sex_and_turns_off_when_confirmed(tmp_path, monkeypatch):
    (tmp_path / "unspecified").mkdir()
    _, review, text = _run(tmp_path / "unspecified", monkeypatch, sex=None)  # form blank, the lab prints Female
    assert review.startswith(clinic_config.FEMALE_RANGES_STAFF_CHECK_LINE) and "DRAFT" in text
    monkeypatch.setattr(clinic_config, "FEMALE_RANGES_CONFIRMED", True)
    (tmp_path / "confirmed").mkdir()
    record, review, text = _run(tmp_path / "confirmed", monkeypatch)
    assert not review.startswith("FEMALE RANGES") and record.draft_label is None and "DRAFT" not in text


def test_male_reports_have_no_female_flag(tmp_path):
    male = run_scenario("quest_digital", tmp_path / "male")
    assert not male["review"].startswith("FEMALE") and "DRAFT" not in male["text"]


def test_the_cortisol_window_printed_as_am_6_10_am_is_read():
    match = pipeline._AM_WINDOW_RE.search("Reference range: AM (6-10 AM) 4.8-19.5 ug/dL")
    assert (match[1], match[2]) == ("6", "10")


def test_the_upload_form_passes_the_scanned_pages_date(staff_client, tmp_path, monkeypatch):
    import app
    captured = {}
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")

    def fake_run(**kwargs):
        captured.update(kwargs)
        Path(kwargs["out_path"]).write_bytes(b"synthetic report PDF")
        (tmp_path / "review.txt").write_text("synthetic review", encoding="utf-8")
        return str(tmp_path / "review.txt")

    with patch.object(app.pipeline, "run", side_effect=fake_run):
        response = staff_client.post("/generate", data={
            "patient_name": "Synthetic, Pat", "collected_date": "2026-05-12", "scan_collected_date": "2025-06-10",
            "labs_pdf": (io.BytesIO(b"synthetic input PDF"), "synthetic-labs.pdf")})
        assert response.status_code == 200
        for _ in range(500):
            if captured:
                break
            time.sleep(0.01)
    assert captured["scan_collected_date"] == "06/10/2025" and captured["collected_date"] == "05/12/2026"


def test_a_scanned_name_misread_with_lookalike_letters_is_matched_once_and_noted(tmp_path, monkeypatch):
    reads = fc.scan_reads()
    reads[2]["rows"].append({"name": "Trilodothyronine (T3), Total", "result_text": "118", "flag": None,
                             "reference_range": "80-200", "lab_code": None, "column": "in_range", "page": 0,
                             "illegible": False, "section": "THYROID FUNCTION"})
    record, review, _ = _run(tmp_path, monkeypatch, scan_reads=reads)
    assert _values(record)[("Total T3", fc.EARLIER)] == {"118"}
    assert "Trilodothyronine" not in {item.name for item in record.lab_reported}
    assert "STAFF REVIEW - name matched after OCR folding: scanned page 2 prints 'Trilodothyronine (T3), Total', " \
           "read as Total T3" in review
