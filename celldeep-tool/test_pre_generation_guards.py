"""The pre-generation guards (synthetic data only). They only add stops:

1. Date guard: an entered Collected date that differs from the date the lab prints stops with both dates ("Use the
   lab date" / "Stop, I will fix the entry"); the typed date never silently replaces the printed one.
2. Name guard: an entered name that does not match the printed one (first name plus last name or last initial) stops
   ("Use as entered" / "Stop, I will fix"). No DOB or ID matching.
3. One pre-generation line: name entered vs printed, the bloodwork Collected date, the DEXA scan chosen."""

import threading
import time
import uuid

import pytest

import pipeline
from synthetic_fixtures import deterministic_fixtures as fx

DRAW = "03/02/2026"


def _labs(path):
    items = fx.section_preamble("SYN-GUARD-1", DRAW) + fx.header_line(100)
    items += fx.row(128, "TSH", "1.9", units="uIU/mL", lab_range="0.40-4.50")
    return fx.write_lab_pdf(path, [items])


def _run(tmp_path, monkeypatch, labs, answer=True, name=fx.PATIENT, entered=DRAW, sex="male"):
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda _name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda _name: str(tmp_path / "diag"))
    screens, banners = [], []

    def confirm(screen):
        screens.append(screen)
        return answer(screen) if callable(answer) else answer

    pipeline.run(str(labs), [], None, name, 44, sex, str(tmp_path / "report.pdf"), vitality_index={},
                 collected_date=entered, confirm=confirm, notify=banners.append)
    return screens, banners, (tmp_path / "review.txt").read_text(encoding="utf-8")


# --- 0. nothing to stop for --------------------------------------------------------------------------------------

def test_a_matching_name_and_date_add_no_screen_only_the_one_line_check(tmp_path, monkeypatch):
    screens, banners, review = _run(tmp_path, monkeypatch, _labs(tmp_path / "labs.pdf"))
    assert screens == []
    assert banners == [f"Name entered: {fx.PATIENT}; printed on the lab: Synthetic, Pat | Bloodwork collected: {DRAW}"]
    assert f"PRE-GENERATION CHECK: {banners[0]}" in review
    assert (tmp_path / "report.pdf").exists()


# --- 1. date guard -------------------------------------------------------------------------------------------------

def test_an_entered_date_that_differs_from_the_printed_date_stops_with_both_dates(tmp_path, monkeypatch):
    labs = _labs(tmp_path / "labs.pdf")
    with pytest.raises(pipeline.GenerationAborted):
        screens, _, _ = _run(tmp_path, monkeypatch, labs, answer="stop", entered="03/03/2026")
    assert not (tmp_path / "report.pdf").exists()
    seen = []
    screens, _, review = _run(tmp_path, monkeypatch, labs, entered="03/03/2026",
                              answer=lambda screen: seen.append(screen) or screen.choices[0][0])
    assert seen[0].kind == "date" and "(03/03/2026)" in seen[0].message and f"({DRAW})" in seen[0].message
    assert [label for _, label in seen[0].choices] == ["Use the lab date", "Stop, I will fix the entry"]
    assert f"DATE CHECK: staff entered 03/03/2026; the lab prints {DRAW}; staff chose the lab date" in review
    with pytest.raises(pipeline.DateConflict, match="03/03/2026"):  # no one to ask: nothing is built
        pipeline.run(str(labs), [], None, fx.PATIENT, 44, "male", str(tmp_path / "x.pdf"), collected_date="03/03/2026")


def _female_undated(tmp_path, monkeypatch, entered=None):
    from synthetic_fixtures import scenarios

    labs, dexa, reads, options = scenarios.build("female_chl_scanned_undated", tmp_path)
    vision = scenarios.ScriptedVision(reads)
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kw: vision)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda _name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda _name: str(tmp_path / "diag"))
    screens = []
    pipeline.run(labs, dexa, None, options["patient"], options["age"], "female", str(tmp_path / "r.pdf"),
                 vitality_index={}, collected_date=entered or options["collected_date"],
                 confirm=lambda screen: screens.append(screen) or True)
    return screens, (tmp_path / "review.txt").read_text(encoding="utf-8"), options["collected_date"]


def test_undated_scanned_pages_dated_from_the_historical_column_keep_the_date_guard_on(tmp_path, monkeypatch):
    # The entered date is the digital report's (it dates the scans only through the Historical-column match).
    screens, review, printed = _female_undated(tmp_path, monkeypatch)
    assert "date" not in [screen.kind for screen in screens] and "DATE CHECK" not in review
    record = {"lab_printed_dates": [printed, "01/05/2026"], "entered_date_dates_scans": False}
    assert pipeline.date_conflict(record, "01/05/2026") == ("01/05/2026", printed)  # an older draw's date stops


def test_undated_scanned_pages_dated_by_the_entered_date_are_unchanged_and_noted(tmp_path, monkeypatch):
    # An entered date that matches no printed date dates the undated scanned pages, as before (a later scanned draw
    # is entered this way); the QA file names both dates.
    _, review, printed = _female_undated(tmp_path, monkeypatch, entered="06/30/2026")
    assert f"DATE CHECK: the scanned pages print no Collected date; the entered date 06/30/2026 dates them (the " \
           f"digital report prints {printed})" in review


def test_with_no_printed_date_the_entered_date_is_used_and_noted():
    assert pipeline.date_conflict({"lab_printed_dates": []}, "03/03/2026") is None
    assert pipeline.date_conflict({"lab_printed_dates": ["Collected: 03/02/2026"], "entered_date_dates_scans": True},
                                  "03/03/2026") is None  # undated scanned pages are dated by the entered date
    assert pipeline.date_conflict({"lab_printed_dates": ["03/02/2026", "02/01/2026"]}, "3/2/2026") is None
    assert pipeline.date_conflict({"lab_printed_dates": ["03/02/2026"]}, "03/03/2026") == ("03/03/2026", "03/02/2026")


# --- 2. name guard -------------------------------------------------------------------------------------------------

def test_an_entered_name_that_does_not_match_the_printed_name_stops(tmp_path, monkeypatch):
    labs = _labs(tmp_path / "labs.pdf")
    with pytest.raises(pipeline.GenerationAborted):
        _run(tmp_path, monkeypatch, labs, answer="stop", name="Pat Synthetix")
    assert not (tmp_path / "report.pdf").exists()
    seen = []
    _, banners, review = _run(tmp_path, monkeypatch, labs, name="Pat Synthetix",
                              answer=lambda screen: seen.append(screen) or "use_as_entered")
    assert seen[0].kind == "name" and "(Pat Synthetix)" in seen[0].message and "Synthetic, Pat" in seen[0].message
    assert [label for _, label in seen[0].choices] == ["Use as entered", "Stop, I will fix"]
    assert "NAME CHECK:" in review
    assert any(line.startswith("Name entered: Pat Synthetix; printed on the lab: Synthetic, Pat | Bloodwork collected: "
                               f"{DRAW}") for line in banners)


def test_first_name_and_last_initial_still_match():
    assert pipeline.name_conflicts({"printed_names": [("lab PDF text pages", None, "SYNTHETIC, PAT")]}, "Pat S") == []
    assert pipeline.name_conflicts({"printed_names": [("lab PDF text pages", None, "SYNTHETIC, ROBIN")]}, "Robyn S") == [
        "SYNTHETIC, ROBIN"]


def test_the_pre_generation_line_names_the_dexa_scan_chosen_and_each_scan_left_out():
    extracted = {"printed_names": [("lab PDF text pages", None, "SYNTHETIC, PAT")], "collected_date": DRAW,
                 "dexa_history": [{"date_display": "01/10/2026", "body_fat_pct": 20.0}],
                 "dexa_pairing": {"current": "02/20/2026", "excluded": ["06/01/2026"]},
                 "dexa_exclusions": [(("dexa.pdf", 3), "prints a different name")], "dexa_info": {}}
    assert pipeline.pre_generation_line(extracted, "Pat Synthetic") == (
        f"Name entered: Pat Synthetic; printed on the lab: SYNTHETIC, PAT | Bloodwork collected: {DRAW} | "
        "DEXA scan: 02/20/2026; excluded: 06/01/2026 (dated after the paired scan), file dexa.pdf page 3 "
        "(prints a different name)")
    assert "no name read from the lab - confirm" in pipeline.pre_generation_line({"printed_names": []}, "Pat S")


# --- the app: the screen's groups and choices ----------------------------------------------------------------------

def test_the_app_shows_the_groups_and_accepts_only_the_screens_choices(staff_client, tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(app, "JOBS_DIR", tmp_path)
    job_id = uuid.uuid4().hex
    (tmp_path / job_id).mkdir()
    screen = pipeline.PreflightScreen("date", pipeline.DATE_CHOICES, message="dates differ")
    result = {}
    slot = threading.Lock()
    slot.acquire()  # the job holds the one-report slot while it runs; the screen releases it while waiting
    monkeypatch.setattr(app, "_REPORT_SLOT", slot)
    worker = threading.Thread(target=lambda: result.update(action=app._confirm_with_staff(tmp_path / job_id, screen)),
                              daemon=True)
    worker.start()
    for _ in range(250):
        if job_id in app._DECISIONS and (tmp_path / job_id / "status.json").exists():
            break
        time.sleep(0.02)
    assert job_id in app._DECISIONS
    status = staff_client.get(f"/generate/status/{job_id}").get_json()
    assert status["status"] == "confirm" and status["kind"] == "date" and status["message"] == "dates differ"
    assert [choice["action"] for choice in status["choices"]] == ["use_lab_date", "stop"]
    assert staff_client.post(f"/generate/decision/{job_id}", data={"action": "use_lab_date"}).status_code == 200
    worker.join(10)
    assert not worker.is_alive()
    assert result["action"] == "use_lab_date"
