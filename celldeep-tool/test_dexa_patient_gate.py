"""A DEXA page is used only when both reads print the staff-entered patient's name: another patient's page,
or a page with no readable name, is excluded from history, current values and summary and listed under
INCOMPLETE in the staff notes. Synthetic data and mocked reads only."""

import copy
import re

import fitz
import pytest

import pipeline
import scan_dexa
from synthetic_fixtures import deterministic_fixtures as labs_fx
from synthetic_fixtures import dexa as fx

OTHER = "Other, Person"
# Patient B's invented scans: values and dates that appear nowhere in patient A's fixtures.
OTHER_EARLY = fx.scan("06/10/2026", "181.2", "62.9", "77.3", "41.7 %", "1.94", "141.0")
OTHER_LATE = fx.scan("07/22/2026", "179.8", "60.1", "78.6", "39.9 %", "1.81", "133.0")
NAMELESS = fx.scan("08/05/2026", "150.4", "45.5", "99.2", "28.8 %", "0.55", "61.0")
OTHER_TEXT = ["41.7", "39.9", "77.3", "78.6", "181.2", "179.8", "62.9", "60.1", "141", "133",
              "June 10, 2026", "July 22, 2026", "06/10/2026", "07/22/2026"]
NAMELESS_TEXT = ["28.8", "150.4", "99.2", "45.5", "August 5, 2026", "08/05/2026"]


def _same(*scans, **kwargs):
    return (fx.read(list(scans), **kwargs), fx.read(copy.deepcopy(list(scans)), **kwargs))


PAGES = {"summary": _same(fx.FOLLOW_UP), "trend": _same(fx.BASELINE, fx.FOLLOW_UP),
         "other": _same(OTHER_EARLY, OTHER_LATE, patient_name=OTHER),
         "nameless": _same(NAMELESS, patient_name=None)}


def _generate(tmp_path, monkeypatch, labels):
    path = fx.write_pdf(tmp_path / "dexa.pdf", labels)
    pages = {label: PAGES[label] for label in labels}
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: fx.DexaReader(path, labels, pages))
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    labs = labs_fx.write_lab_pdf(tmp_path / "labs.pdf", [labs_fx.section_preamble("SYN-D", "02/03/2026")
                                                         + labs_fx.header_line(100) + labs_fx.row(130, "TSH", "1.0")])
    out = tmp_path / "report.pdf"
    pipeline.run(str(labs), [str(path)], None, fx.PATIENT, 44, "male", str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    # Visible HTML text only: drawing coordinates in SVG attributes are not report values.
    html = " ".join(re.sub(r"<[^>]*>", " ", out.with_suffix(".html").read_text(encoding="utf-8")).split())
    review = (tmp_path / "review.txt").read_text(encoding="utf-8")
    return text, html, review


@pytest.fixture
def report(tmp_path, monkeypatch, capsys):
    return (*_generate(tmp_path, monkeypatch, ["summary", "trend", "other", "nameless"]), capsys.readouterr().out)


@pytest.mark.parametrize("labels, embedded", [
    (["summary", "trend", "other"], True),   # page 1 is the patient's: its image is the report's scan image
    (["other", "summary", "trend"], False),  # page 1 is another patient's: never embedded
    (["nameless", "summary", "trend"], False),
])
def test_scan_image_comes_only_from_a_confirmed_page(tmp_path, monkeypatch, labels, embedded):
    _, html, _ = _generate(tmp_path, monkeypatch, labels)
    raw = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert ('class="dexa-scan-img"' in raw) is embedded
    assert "33.7%" in html  # the patient's own scans are reported either way


def test_other_patients_values_appear_nowhere_in_the_report(report):
    text, html, _, _ = report
    assert "33.7%" in text and "30.4%" in text  # patient A's own scans are still reported
    for value in OTHER_TEXT + NAMELESS_TEXT:
        assert value not in text, value
        assert value not in html, value
    assert OTHER not in text and OTHER not in html


def test_staff_notes_open_with_the_incomplete_notice(report):
    _, _, review, _ = report
    lines = review.splitlines()
    assert lines[0] == ("INCOMPLETE - pages excluded: DEXA file 1 page 3, file 1 page 4 not confirmed as this "
                        "patient's; their scans are NOT in this report (history, current values or summary) - "
                        "confirm which patient they belong to")
    assert lines[1] == "  - DEXA file 1 page 3: prints patient name 'Other, Person', not the staff-entered patient"
    assert lines[2] == ("  - DEXA file 1 page 4: no readable patient name (read 1: none / read 2: none); not assumed "
                        "to be this patient")
    assert "DEXA - 4 page(s) read twice; 2 scan date(s) kept" in review
    assert "STAFF REVIEW - DEXA PATIENT NAME MISMATCH: DEXA file 1 page 3" in review
    assert "STAFF REVIEW - DEXA PATIENT NAME UNREADABLE: DEXA file 1 page 4" in review


def test_logs_name_the_pages_but_never_the_printed_name(report):
    _, _, _, log = report
    assert "source=dexa file=1 page=3" in log and "verdict=excluded" in log
    assert OTHER not in log and "Other" not in log


def _gate(reads, patient=fx.PATIENT):
    excluded = []
    history, _, summary = scan_dexa.gate([((1, 1), list(reads))], patient, excluded_sink=excluded)
    return history, summary, excluded


def test_name_printed_in_only_one_read_is_not_enough():
    history, _, excluded = _gate((fx.read([fx.BASELINE]), fx.read([fx.BASELINE], patient_name=None)))
    assert history == []
    assert excluded == [((1, 1), "no readable patient name (read 1: 'Synthetic, Pat' / read 2: none); not assumed "
                                 "to be this patient")]


def test_reads_that_disagree_on_the_name_exclude_the_page():
    history, _, excluded = _gate((fx.read([fx.BASELINE]), fx.read([fx.BASELINE], patient_name=OTHER)))
    assert history == [] and "'Synthetic, Pat' / 'Other, Person'" in excluded[0][1]


def test_name_order_and_case_still_match():
    history, _, excluded = _gate(_same(fx.BASELINE, patient_name="PAT  SYNTHETIC"))
    assert [h["date_display"] for h in history] == ["05/12/2025"] and excluded == []


def test_punctuation_only_name_counts_as_unreadable():
    history, _, excluded = _gate(_same(fx.BASELINE, patient_name="--"))
    assert history == [] and excluded[0][1].startswith("no readable patient name")
