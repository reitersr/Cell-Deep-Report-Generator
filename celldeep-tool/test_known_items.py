"""The known-items allowlist (config/known_items.json), behind clinic_config.KNOWN_ITEMS_AUTOPROCEED (on by owner
decision; synthetic data, and the four locked real cases only where real_fixtures/ is present).

- Flag off: the allowlist is never read and every case's stop screens and job-page notices are exactly
  the ones recorded before the allowlist existed (synthetic_fixtures/expected/stop_screens.json; real_fixtures/
  stop_screens.json locally). The QA file has no "Auto-accepted (known)" section.
- Flag on: when every stop item is known (exact names, case and whitespace only) and no hard stop applies, the report
  is built with a banner; any other item still stops, listed first ("Needs review"), known ones after ("Known
  (auto-handled)"). With an empty allowlist the flag changes nothing. A near-miss name never matches."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

import clinic_config
import known_items
import pipeline
from synthetic_fixtures import deterministic_fixtures as fx

DRAW = "03/02/2026"
UNKNOWN = "Zylotrin Index"  # made up: no lab prints it
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "regression_check.py"
EMPTY = {"lab_reported": set(), "excluded": set(), "excluded_when_result": {}}


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("regression_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["regression_check"] = module
    spec.loader.exec_module(module)
    return module


def _baseline(path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))


# --- flag off: byte-identical stop screens ----------------------------------------------------------------------

def test_the_flag_is_on():
    assert clinic_config.KNOWN_ITEMS_AUTOPROCEED is True


def test_with_the_flag_off_every_synthetic_cases_stop_screens_are_unchanged(check, monkeypatch):
    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", False)
    monkeypatch.setattr(known_items, "load", lambda *a, **k: pytest.fail("the allowlist was read with the flag off"))
    assert check.stop_screens() == _baseline(check.SCREENS_FILE)


def test_with_the_flag_off_the_four_locked_real_cases_stop_screens_are_unchanged(check, monkeypatch):
    if not check.REAL_SCREENS_FILE.is_file():
        pytest.skip(check.missing_note(check.real_missing()) + "; stop-screen baseline not present")
    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", False)
    monkeypatch.setattr(known_items, "load", lambda *a, **k: pytest.fail("the allowlist was read with the flag off"))
    got = check.stop_screens(real=True)
    if not got:
        pytest.skip(check.missing_note(check.real_missing()))
    assert set(got) == {f"real:{name}" for name in check.REAL_CASES}
    assert got == _baseline(check.REAL_SCREENS_FILE)


def test_with_the_flag_off_the_qa_file_has_no_auto_accepted_section(tmp_path, monkeypatch):
    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", False)
    screens, banners, review = _run(tmp_path, monkeypatch, _labs(tmp_path / "labs.pdf"))
    assert [screen.kind for screen in screens] == ["review"]  # the original screen: MTHFR is an unrecognized name
    assert "Auto-accepted (known)" not in review
    assert not any("known items handled automatically" in line for line in banners)


# --- flag on: anything not on the list still stops ---------------------------------------------------------------

def test_with_the_flag_on_and_an_empty_allowlist_every_stop_screen_is_unchanged(check, monkeypatch):
    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", True)
    monkeypatch.setattr(known_items, "load", lambda *a, **k: EMPTY)
    got = check.stop_screens()
    expected = _baseline(check.SCREENS_FILE)
    assert {name: [s["items"] for s in case["screens"]] for name, case in got.items()} == \
        {name: [s["items"] for s in case["screens"]] for name, case in expected.items()}
    assert {name: case["notices"] for name, case in got.items()} == \
        {name: case["notices"] for name, case in expected.items()}
    if check.REAL_SCREENS_FILE.is_file() and (real := check.stop_screens(real=True)):
        expected = _baseline(check.REAL_SCREENS_FILE)
        assert {name: [s["items"] for s in case["screens"]] for name, case in real.items()} == \
            {name: [s["items"] for s in case["screens"]] for name, case in expected.items()}


_SCANNED_ROW = re.compile(r"Lab PDF page \d+ \(scanned\): result (.+?) excluded - .*; reads: (.+)$")


def _unknown_names(line):
    marker = "with a test name the tool does not recognize"
    return line.split("listed in the staff notes: ", 1)[1].split(", ") if marker in line else None


def test_with_the_flag_on_every_item_not_on_the_list_still_stops(check, monkeypatch):
    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", True)
    known = known_items.load()
    listed = known["lab_reported"] | known["excluded"]
    for real in (False, True):
        path = check.REAL_SCREENS_FILE if real else check.SCREENS_FILE
        if real and not path.is_file():
            continue
        got, expected = check.stop_screens(real=real), _baseline(path)
        for name, case in expected.items():
            before = [item for screen in case["screens"] if screen["kind"] == "review" for item in screen["items"]]
            after = [item for screen in got[name]["screens"] if screen["kind"] == "review" for item in screen["items"]]
            for item in before:
                names = _unknown_names(item)
                if names is None:  # a page, row or DEXA line: only a scanned row named on the list may leave
                    row = _SCANNED_ROW.match(item)
                    if row and known_items.handling(row[1], known_items.EXCLUDED, row[2].split(" / ")):
                        continue
                    assert item in after, (name, item)
                    continue
                for test in names:
                    if known_items.normalize(test) not in listed:
                        assert any(test in (_unknown_names(line) or []) for line in after), (name, test)
            assert [s["kind"] for s in got[name]["screens"] if s["kind"] != "review"] == \
                [s["kind"] for s in case["screens"] if s["kind"] != "review"]  # date/name screens never skipped


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", True)


def _labs(path, extra=(), gender=None):
    items = fx.section_preamble("SYN-KNOWN-1", DRAW)
    if gender:
        items.append((fx.LAB_X["name"], 82, f"Gender: {gender}"))
    items += fx.header_line(100)
    rows = [("TSH", "1.9", "uIU/mL", "0.40-4.50"), ("Chol/HDL-C", "2.4", "", "Optimal <=3.5"),
            ("MTHFR Mutation", "Detected", "", ""), *extra]
    for index, (name, value, units, lab_range) in enumerate(rows):
        items += fx.row(128 + 13 * index, name, value, units=units, lab_range=lab_range)
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


# --- with the flag on: the allowlist at work ------------------------------------------------------------------------------------------------

def test_when_every_item_is_known_the_report_is_built_with_a_banner(tmp_path, monkeypatch, flag_on):
    screens, banners, review = _run(tmp_path, monkeypatch, _labs(tmp_path / "labs.pdf"))
    assert screens == []  # nothing to ask: no blocking screen
    assert "2 known items handled automatically - listed in staff notes" in banners
    assert (tmp_path / "report.pdf").exists()
    section = review[review.index("Auto-accepted (known)"):]
    assert "  - Chol/HDL-C: lab-reported (page 1)" in section and "  - MTHFR Mutation: excluded (page 1)" in section


def test_one_unknown_name_brings_back_the_full_screen_with_it_listed_first(tmp_path, monkeypatch, flag_on):
    labs = _labs(tmp_path / "labs.pdf", extra=[(UNKNOWN, "4.2", "U/L", "1.0-5.0")])
    screens, banners, review = _run(tmp_path, monkeypatch, labs)
    assert [screen.kind for screen in screens] == ["review"]
    screen = screens[0]
    assert len(screen.needs_review) == 1 and UNKNOWN in screen.needs_review[0]
    assert screen.known == ["Chol/HDL-C - lab-reported (page 1)", "MTHFR Mutation - excluded (page 1)"]
    assert list(screen) == [*screen.needs_review, *screen.known]  # unknown first
    assert not any("known items handled automatically" in line for line in banners)
    assert "Auto-accepted (known)" in review


def test_stop_at_the_full_screen_builds_nothing(tmp_path, monkeypatch, flag_on):
    labs = _labs(tmp_path / "labs.pdf", extra=[(UNKNOWN, "4.2", "U/L", "1.0-5.0")])
    with pytest.raises(pipeline.GenerationAborted):
        _run(tmp_path, monkeypatch, labs, answer=False)
    assert not (tmp_path / "report.pdf").exists()


@pytest.mark.parametrize("near_miss", ["Chol/HDL-Cx", "Chol/HDL-C.", "Chol HDL-C", "Chol/HDL C", "Cho1/HDL-C"])
def test_a_near_miss_name_never_matches_the_allowlist(near_miss, tmp_path, monkeypatch, flag_on):
    assert known_items.handling(near_miss, known_items.LAB_REPORTED) is None
    assert known_items.handling(" chol/hdl-c ", known_items.LAB_REPORTED) == known_items.LAB_REPORTED  # case/space only
    if near_miss == "Chol/HDL-Cx":
        labs = _labs(tmp_path / "labs.pdf", extra=[(near_miss, "2.4", "", "Optimal <=3.5")])
        screens, _, _ = _run(tmp_path, monkeypatch, labs)
        assert screens and any(near_miss in line for line in screens[0].needs_review)


@pytest.mark.parametrize("reads, reason", [
    ("NO CULTURE INDICATED / not read / not read", "agreement gate"),        # missing reads
    ("NO CULTURE INDICATED / NO CULTURE / NO CULTURE INDICATED", "agreement gate"),  # reads disagree
    ("SEE NOTE: / SEE NOTE: / 14", "agreement gate"),
])
def test_a_listed_scanned_row_the_reads_did_not_agree_on_still_needs_review(reads, reason):
    known = known_items.load()
    for name in ("REFLEXIVE URINE CULTURE", "BUN/CREATININE RATIO"):
        item = {"name": name, "page": 2, "reads": reads, "reason": reason}
        entries = pipeline.preflight_entries([], [], [item], [], {}, [], known)
        assert [entry["known"] for entry in entries] == [None], (name, reads)
    agreed = {"name": "BUN/CREATININE RATIO", "page": 3, "reads": "SEE NOTE: / SEE NOTE:",
              "reason": "format gate: unsupported printed result"}
    assert pipeline.preflight_entries([], [], [agreed], [], {}, [], known)[0]["known"] == {
        "name": "BUN/CREATININE RATIO", "handling": "excluded", "page": 3}


def test_bun_creatinine_ratio_is_known_only_when_it_prints_see_note():
    assert known_items.handling("BUN/CREATININE RATIO", known_items.EXCLUDED, ["SEE NOTE:", "SEE NOTE:"]) == "excluded"
    assert known_items.handling("BUN/CREATININE RATIO", known_items.EXCLUDED, ["SEE NOTE:", "not read"]) is None
    assert known_items.handling("BUN/CREATININE RATIO", known_items.EXCLUDED, ["14", "14"]) is None


def test_female_reports_are_never_waved_through_while_female_ranges_are_unconfirmed(tmp_path, monkeypatch, flag_on):
    monkeypatch.setattr(clinic_config, "FEMALE_RANGES_CONFIRMED", False)
    screens, _, _ = _run(tmp_path, monkeypatch, _labs(tmp_path / "labs.pdf", gender="Female"), sex="female")
    assert [screen.kind for screen in screens] == ["review"]
    assert any("female ranges are not yet confirmed" in line for line in screens[0].needs_review)
    assert screens[0].known  # the known items are still shown, grouped




# --- flag on: nothing known is hidden; female reports unchanged -------------------------------------------------

def test_every_auto_accepted_item_is_listed_in_the_qa_file(tmp_path, monkeypatch):
    from synthetic_fixtures import scenarios
    from unknown_marker_policy import without_staff_check

    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", True)
    original, seen = pipeline.extract, []
    monkeypatch.setattr(pipeline, "extract", lambda *a, **k: seen.append(original(*a, **k)) or seen[-1])
    accepted_somewhere = 0
    for name in scenarios.SCENARIOS:
        seen.clear()
        scenarios.run_scenario(name, tmp_path / name, monkeypatch)
        review = without_staff_check((tmp_path / name / "review.txt").read_text(encoding="utf-8"))
        section = review[review.index("\nAuto-accepted (known)\n"):].splitlines()[2:]
        expected = [f"  - {item['name']}: {item['handling']}" + (f" (page {item['page']})" if item.get("page") else "")
                    for item in seen[-1]["auto_accepted"]]
        assert section == (expected or ["  none"]), name
        accepted_somewhere += len(expected)
    assert accepted_somewhere  # the scenarios do auto-accept items (chl_extensive, quest urinalysis)


def test_female_reports_keep_the_draft_mark_and_staff_check_line_with_the_flag_on(tmp_path, monkeypatch):
    import fitz
    from synthetic_fixtures import scenarios

    monkeypatch.setattr(clinic_config, "KNOWN_ITEMS_AUTOPROCEED", True)
    monkeypatch.setattr(clinic_config, "FEMALE_RANGES_CONFIRMED", False)
    scenarios.run_scenario("female_chl_scanned_undated", tmp_path, monkeypatch)
    with fitz.open(tmp_path / "report.pdf") as document:
        pages = [page.get_text() for page in document]
    assert all(clinic_config.FEMALE_DRAFT_MARK in page for page in pages)
    assert (tmp_path / "review.txt").read_text(encoding="utf-8").startswith(
        clinic_config.FEMALE_RANGES_STAFF_CHECK_LINE)


# --- the four locked real cases: patient PDF text (local only) --------------------------------------------------

PDF_TEXT = Path(__file__).resolve().parent / "real_fixtures" / "pdf_text"


@pytest.mark.parametrize("case", ["extensive_male", "female_chl", "limited_male", "chl_quest_male"])
def test_a_locked_real_cases_patient_pdf_text_is_identical(case, check, tmp_path, monkeypatch):
    import json

    import fitz

    spec_file, baseline = check.REAL / f"{case}.expected.json", PDF_TEXT / f"{case}.txt"
    if not spec_file.is_file() or not baseline.is_file():
        pytest.skip(check.missing_note(check.real_missing()) + "; patient PDF text baseline not present")
    spec = json.loads(spec_file.read_text())
    reads = {key: json.loads((check.REAL / spec[key]).read_text())["pages"] if spec.get(key) else None
             for key in ("dexa_reads", "lab_scan_reads")}
    scripted = check.ScriptedReads(reads["dexa_reads"], reads["lab_scan_reads"])
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kw: scripted)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda _n: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda _n: str(tmp_path / "diag"))
    pipeline.run(str(check.REAL / spec["labs"]), [str(check.REAL / d) for d in spec["dexa"]], None, spec["patient"],
                 spec["age"], spec.get("sex", "male"), str(tmp_path / "report.pdf"),
                 vitality_index=check.spec_vitality(spec), collected_date=spec["collected_date"],
                 confirm=lambda screen: True, scan_collected_date=spec.get("scan_collected_date"))
    with fitz.open(tmp_path / "report.pdf") as document:
        text = "\n".join(page.get_text() for page in document)
    assert text == baseline.read_text()
