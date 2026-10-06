"""Which DEXA pages belong to the staff-entered patient. A page printing a different name is excluded from
history, current values and summary and listed under INCOMPLETE. A page with no printed name is accepted
for the staff-entered patient with a staff notice, unless the age printed on it is more than a year from
the patient's. Synthetic data and mocked reads only."""

import copy
import re

import fitz
import pytest

import pipeline
import scan_dexa
from synthetic_fixtures import deterministic_fixtures as labs_fx
from synthetic_fixtures import dexa as fx

OTHER = "Other, Person"
# Another patient's invented scans: values and dates that appear nowhere in the patient's fixtures.
OTHER_EARLY = fx.scan("06/10/2026", "181.2", "62.9", "77.3", "41.7 %", "1.94", "141.0")
OTHER_LATE = fx.scan("07/22/2026", "179.8", "60.1", "78.6", "39.9 %", "1.81", "133.0")
OTHER_TEXT = ["41.7", "39.9", "77.3", "78.6", "181.2", "179.8", "62.9", "60.1", "141", "133",
              "June 10, 2026", "July 22, 2026", "06/10/2026", "07/22/2026"]


def _same(*scans, **kwargs):
    return (fx.read(list(scans), **kwargs), fx.read(copy.deepcopy(list(scans)), **kwargs))


PAGES = {
    "summary": _same(fx.FOLLOW_UP, age="46"), "trend": _same(fx.BASELINE, fx.FOLLOW_UP, age="46"),
    "other": _same(OTHER_EARLY, OTHER_LATE, patient_name=OTHER, age="46"),
    # Name redacted on every page, as on the patient's real report: accepted for the staff-entered patient.
    "summary-unnamed": _same(fx.FOLLOW_UP, patient_name=None, age="46"),
    "trend-unnamed": _same(fx.BASELINE, fx.FOLLOW_UP, patient_name=None, age="45 yrs"),
    # Another profile's page with the name redacted too: only its printed age tells it apart.
    "other-unnamed": _same(OTHER_EARLY, OTHER_LATE, patient_name=None, age="34"),
}


def _generate(tmp_path, monkeypatch, labels, staff_name=fx.PATIENT, staff_age=46):
    path = fx.write_pdf(tmp_path / "dexa.pdf", labels)
    pages = {label: PAGES[label] for label in labels}
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: fx.DexaReader(path, labels, pages))
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    labs = labs_fx.write_lab_pdf(tmp_path / "labs.pdf", [labs_fx.section_preamble("SYN-D", "02/03/2026")
                                                         + labs_fx.header_line(100) + labs_fx.row(130, "TSH", "1.0")])
    out = tmp_path / "report.pdf"
    pipeline.run(str(labs), [str(path)], None, staff_name, staff_age, "male", str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    # Visible HTML text only: drawing coordinates in SVG attributes are not report values.
    raw = out.with_suffix(".html").read_text(encoding="utf-8")
    html = " ".join(re.sub(r"<[^>]*>", " ", raw).split())
    review = (tmp_path / "review.txt").read_text(encoding="utf-8")
    return text, html, review, raw


def _assert_other_values_absent(text, html):
    for value in OTHER_TEXT:
        assert value not in text, value
        assert value not in html, value


# --- 1. pages with no printed name are accepted, with a staff notice -----------------------------------

def test_unnamed_pages_are_accepted_for_the_staff_entered_patient(tmp_path, monkeypatch, capsys):
    text, html, review, raw = _generate(tmp_path, monkeypatch, ["summary-unnamed", "trend-unnamed"])
    assert "33.7%" in text and "30.4%" in text  # the DEXA section is back
    assert 'class="dexa-scan-img"' in raw  # page 1 is accepted, so its image is used
    assert ("STAFF REVIEW - DEXA name not printed: DEXA file 1 page 1, file 1 page 2 print no patient name "
            "confirmed by both reads; accepted for the staff-entered patient - confirm they are this patient's"
            ) in review
    assert "INCOMPLETE" not in review and "STAFF REVIEW" not in text
    assert "DEXA - 2 page(s) read twice; 2 scan date(s) kept" in review
    assert "verdict=kept" in capsys.readouterr().out


def test_name_printed_in_only_one_read_is_accepted_as_unnamed():
    accepted, unnamed, excluded = scan_dexa.attribute_pages(
        [((1, 1), [fx.read([fx.BASELINE]), fx.read([fx.BASELINE], patient_name=None)])], fx.PATIENT)
    assert (accepted, unnamed, excluded) == ([(1, 1)], [(1, 1)], [])


# --- 2. a page printing a different name is excluded and listed under INCOMPLETE ----------------------

def test_page_printing_another_name_is_excluded_everywhere(tmp_path, monkeypatch, capsys):
    text, html, review, _ = _generate(tmp_path, monkeypatch, ["summary", "trend", "other"])
    log = capsys.readouterr().out
    assert "33.7%" in text and "30.4%" in text
    _assert_other_values_absent(text, html)
    assert OTHER not in text and OTHER not in html
    lines = review.splitlines()
    assert lines[0] == ("INCOMPLETE - pages excluded: DEXA file 1 page 3 not confirmed as this patient's; their "
                        "scans are NOT in this report (history, current values or summary) - confirm which "
                        "patient they belong to")
    assert lines[1] == "  - DEXA file 1 page 3: prints patient name 'Other, Person', not the staff-entered patient"
    assert "STAFF REVIEW - DEXA PATIENT NAME MISMATCH: DEXA file 1 page 3" in review
    assert "DEXA name not printed" not in review  # every kept page printed the patient's name
    assert "source=dexa file=1 page=3" in log and OTHER not in log and "Other" not in log


def test_reads_that_disagree_on_the_name_exclude_the_page():
    _, _, excluded = scan_dexa.attribute_pages(
        [((1, 1), [fx.read([fx.BASELINE]), fx.read([fx.BASELINE], patient_name=OTHER)])], fx.PATIENT)
    assert excluded == [((1, 1), "prints patient name 'Synthetic, Pat' / 'Other, Person', not the staff-entered "
                                 "patient")]


# --- 3. an unnamed page whose printed age is more than a year off is excluded --------------------------

def test_unnamed_page_with_another_age_is_excluded(tmp_path, monkeypatch):
    text, html, review, _ = _generate(tmp_path, monkeypatch, ["summary-unnamed", "trend-unnamed", "other-unnamed"])
    assert "33.7%" in text and "30.4%" in text
    _assert_other_values_absent(text, html)
    lines = review.splitlines()
    assert lines[0].startswith("INCOMPLETE - pages excluded: DEXA file 1 page 3 not confirmed as this patient's")
    assert lines[1] == ("  - DEXA file 1 page 3: no patient name printed and it prints age 34; the staff-entered "
                        "age is 46")
    assert "STAFF REVIEW - DEXA AGE MISMATCH: DEXA file 1 page 3" in review
    assert "DEXA name not printed: DEXA file 1 page 1, file 1 page 2 print" in review


def _attribute(pages, staff_age=None):
    keyed = [((1, number), PAGES[label]) for number, label in enumerate(pages, start=1)]
    return scan_dexa.attribute_pages(keyed, fx.PATIENT, staff_age)


def test_name_matched_pages_set_the_age_before_the_staff_entry():
    accepted, unnamed, excluded = _attribute(["summary", "trend-unnamed", "other-unnamed"], staff_age=34)
    assert accepted == [(1, 1), (1, 2)] and unnamed == [(1, 2)]
    assert excluded == [((1, 3), "no patient name printed and it prints age 34; the name-matched pages print 46")]


def test_without_any_reference_age_disagreeing_unnamed_pages_are_all_excluded():
    accepted, _, excluded = _attribute(["summary-unnamed", "trend-unnamed", "other-unnamed"])
    assert accepted == []  # nothing says which age is the patient's; none is guessed
    assert [key for key, _ in excluded] == [(1, 1), (1, 2), (1, 3)]
    assert excluded[2][1] == ("no patient name printed and its age (34) disagrees with other unnamed pages "
                              "(ages 34, 45, 46); not attributed")


def test_unnamed_pages_within_a_year_of_each_other_need_no_reference():
    accepted, unnamed, excluded = _attribute(["summary-unnamed", "trend-unnamed"])
    assert accepted == unnamed == [(1, 1), (1, 2)] and excluded == []


@pytest.mark.parametrize(("printed", "expected"), [
    (("46", "46"), 46), (("46 yrs", "46"), 46), (("Age 46", "46"), None), (("46", "47"), None),
    ((None, "46"), None), ((None, None), None),
])
def test_page_age_needs_both_reads_to_print_the_same_age(printed, expected):
    assert scan_dexa.page_age([fx.read([], age=age) for age in printed]) == expected
