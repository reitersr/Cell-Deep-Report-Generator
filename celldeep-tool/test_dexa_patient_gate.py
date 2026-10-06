"""Which DEXA pages belong to the staff-entered patient. A page printing a different name is excluded from
history, current values and summary and listed under INCOMPLETE. A page with no printed name is accepted
for the staff-entered patient with a staff notice only when the age it prints sits in the cluster of
ages printed across the DEXA pages (and matches the staff-entered age, when entered). Synthetic data and
mocked reads only."""

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
    # The production shape: decimal ages, and on the other profile's page only per scan row.
    **{f"own-{n}": _same(*([fx.BASELINE, fx.FOLLOW_UP] if n == 1 else [fx.FOLLOW_UP]), patient_name=None,
                         age=f"45.{n - 1 if n < 6 else 4}") for n in range(1, 7)},
    "other-rows": _same(dict(OTHER_EARLY, age="34.0"), dict(OTHER_LATE, age="34.1"), patient_name=None),
    "no-age": _same(OTHER_EARLY, OTHER_LATE, patient_name=None),
}
SEVEN = ["own-1", "own-2", "own-3", "own-4", "own-5", "other-rows", "own-6"]  # the other profile is page 6


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
        [((1, 1), [fx.read([fx.BASELINE], age="45.2"), fx.read([fx.BASELINE], patient_name=None, age="45.2")])],
        fx.PATIENT)
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


# --- 3. unnamed pages are attributed by the ages printed across the pages -----------------------------

def _patient_pdf(tmp_path, monkeypatch, labels, staff_age):
    folder = tmp_path / "-".join(labels)[:60]
    folder.mkdir()
    return _generate(folder, monkeypatch, labels, staff_age=staff_age)


@pytest.mark.parametrize("staff_age", [None, 45])
def test_seven_unnamed_pages_exclude_the_other_profile_by_age(tmp_path, monkeypatch, staff_age):
    """(a) no age on the form and (b) an age on the form: page 6 (ages 34.0/34.1 per scan row) is excluded."""
    text, html, review, _ = _patient_pdf(tmp_path, monkeypatch, SEVEN, staff_age)
    assert "33.7%" in text and "30.4%" in text
    _assert_other_values_absent(text, html)
    lines = review.splitlines()
    assert lines[0].startswith("INCOMPLETE - pages excluded: DEXA file 1 page 6 not confirmed as this patient's")
    assert lines[1] == "  - DEXA file 1 page 6: no patient name printed and it prints age 34.1; the DEXA pages center on age 45.2"
    assert "STAFF REVIEW - DEXA AGE MISMATCH: DEXA file 1 page 6" in review
    assert ("DEXA name not printed: DEXA file 1 page 1, file 1 page 2, file 1 page 3, file 1 page 4, file 1 page 5, "
            "file 1 page 7 print") in review
    # Excluded everywhere: the patient PDF (history, current, first visit, summary, image, headline score) is
    # exactly the report built from the patient's six pages alone.
    own_text, own_html, _, _ = _patient_pdf(tmp_path, monkeypatch, [label for label in SEVEN if label != "other-rows"],
                                            staff_age)
    assert text == own_text and html == own_html


def test_excluded_page_one_never_supplies_the_scan_image(tmp_path, monkeypatch):
    _, html, review, raw = _generate(tmp_path, monkeypatch, ["other-rows", *[l for l in SEVEN if l != "other-rows"]],
                                     staff_age=None)
    assert 'class="dexa-scan-img"' not in raw and "33.7%" in html
    assert "DEXA file 1 page 1: no patient name printed and it prints age 34.1" in review


def _attribute(labels, staff_age=None):
    keyed = [((1, number), PAGES[label]) for number, label in enumerate(labels, start=1)]
    return scan_dexa.attribute_pages(keyed, fx.PATIENT, staff_age)


def test_staff_entered_age_also_excludes_pages_that_disagree_with_it():
    accepted, _, excluded = _attribute(SEVEN, staff_age=48)  # the pages cluster at 45.x; the form says 48
    assert accepted == []
    assert [key for key, _ in excluded] == [(1, n) for n in range(1, 8)]
    assert excluded[0][1] == "no patient name printed and it prints age 45.0; the staff-entered age is 48"


def test_pages_without_a_clear_age_cluster_are_all_excluded(tmp_path, monkeypatch):
    """(c) two profiles, two pages each: no age is the patient's, so no unnamed page is accepted."""
    labels = ["own-1", "other-rows", "own-2", "other-unnamed"]
    accepted, _, excluded = _attribute(labels)
    assert accepted == [] and [key for key, _ in excluded] == [(1, 1), (1, 2), (1, 3), (1, 4)]
    assert excluded[1][1] == ("no patient name printed and it prints age 34.1; the DEXA pages' ages (34, 34.1, 45, "
                              "45.1) form no clear cluster, so no unnamed page is attributed")
    text, html, review, _ = _generate(tmp_path, monkeypatch, labels, staff_age=None)
    _assert_other_values_absent(text, html)
    assert "33.7%" not in text and review.startswith("INCOMPLETE - pages excluded: DEXA file 1 page 1, file 1 page 2")


def test_unnamed_page_without_a_printed_age_is_excluded_and_flagged(tmp_path, monkeypatch):
    """(d) no name and no age: nothing ties the page to the patient, so it is never accepted silently."""
    text, html, review, _ = _generate(tmp_path, monkeypatch, ["own-1", "own-2", "no-age"], staff_age=None)
    _assert_other_values_absent(text, html)
    assert "33.7%" in text
    assert ("  - DEXA file 1 page 3: no patient name and no readable age printed (or the two reads differ); cannot "
            "confirm it is this patient's") in review.splitlines()[:3]
    assert "STAFF REVIEW - DEXA AGE NOT PRINTED: DEXA file 1 page 3" in review


def test_named_pages_count_toward_the_cluster_but_are_never_excluded_by_age():
    accepted, unnamed, excluded = _attribute(["summary", "own-1", "other-unnamed"])
    assert accepted == [(1, 1), (1, 2)] and unnamed == [(1, 2)]
    assert excluded == [((1, 3), "no patient name printed and it prints age 34; the DEXA pages center on age 45")]


@pytest.mark.parametrize(("page", "rows", "expected"), [
    ("45.2", (None, None), (45.2, "45.2")), ("46", (None, None), (46.0, "46")), ("45 yrs", (None, None), (45.0, "45")),
    ("Age: 45.3", (None, None), (45.3, "45.3")), (None, ("34.0", "34.1"), (34.1, "34.1")),  # latest age printed
    ("45.x", (None, None), None), (None, (None, None), None),
])
def test_page_age_reads_decimal_and_per_row_ages(page, rows, expected):
    scans = [fx.scan(f"0{n}/01/2026", age=age) for n, age in enumerate(rows, start=1)]
    assert scan_dexa.page_age([fx.read(scans, age=page), fx.read(copy.deepcopy(scans), age=page)]) == expected


def test_page_age_needs_both_reads_to_print_the_same_ages():
    assert scan_dexa.page_age([fx.read([], age="45.2"), fx.read([], age="45.3")]) is None
    assert scan_dexa.page_age([fx.read([], age="45.2"), fx.read([], age=None)]) is None
