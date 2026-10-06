"""Which DEXA pages belong to the staff-entered patient, and which values reach the report.

Synthetic model of the clinic's 7-page DEXA PDF (names redacted on every page, one foreign profile page):
  (a) image page with two scan dates and a printed age;
  (b) "Segmental Analysis" pages: a body-composition history table, NO printed age;
  (c) "Abdomen Composition" page: a date table with an Age column;
  (d) VAT/SAT trend page: dates and ages;
  plus one page from another profile (age 34.x) whose scans are later than the patient's.
Rules: a different printed name is always excluded; an unnamed page is validated by its printed age (cluster
median and staff age, 2-year tolerance) or, with no age, only when every scan date it shows appears on a
validated page. Printed body fat % is used whenever printed. Invented values and dates only; mocked reads."""

import copy
import re

import fitz
import pytest

import pipeline
import scan_dexa
from synthetic_fixtures import deterministic_fixtures as labs_fx
from synthetic_fixtures import dexa as fx
from unknown_marker_policy import without_staff_check

OTHER = "Other, Person"


def _same(*scans, **kwargs):
    return (fx.read(list(scans), **kwargs), fx.read(copy.deepcopy(list(scans)), **kwargs))


def _scan(date, total, fat, lean, pct, vat=None, area=None, age=None):
    return fx.scan(date, total, fat, lean, pct, vat, area, age=age)


# The patient's four scans. Printed %Fat deliberately differs from fat/total, so a computed value shows.
S1 = ("11/03/2025", "180.4", "44.6", "129.9", "25.6 %")   # fat/total would be 24.7
S2 = ("01/12/2026", "176.0", "38.9", "131.4", "22.9 %")
S3 = ("03/02/2026", "172.2", "31.7", "134.8", "19.1 %")
S4 = ("04/20/2026", "169.8", "25.3", "138.6", "15.6 %")   # fat/total would be 14.9
VAT = {S1[0]: ("1.10", "120.0", "44.9"), S2[0]: ("0.98", "108.0", "45.1"),
       S3[0]: ("0.85", "96.0", "45.2"), S4[0]: ("0.71", "84.0", "45.4")}
# The foreign profile: later dates, so if accepted it would become "Where you are now".
F1 = ("06/15/2026", "150.5", "51.2", "94.3", "35.2 %")
F2 = ("07/27/2026", "149.0", "49.5", "94.5", "34.6 %")
OTHER_TEXT = ["35.2", "34.6", "150.5", "149.0", "51.2", "49.5", "94.3", "94.5", "June 15, 2026",
              "July 27, 2026", "06/15/2026", "07/27/2026"]


def _composition(*scans):
    return [_scan(*scan) for scan in scans]


def _vat_rows(*scans):
    return [fx.scan(scan[0], vat=VAT[scan[0]][0], area=VAT[scan[0]][1], age=VAT[scan[0]][2]) for scan in scans]


PAGES = {
    "a-image": _same(*_composition(S1, S4), patient_name=None, age="45.4"),
    "b-segmental-s4": _same(*_composition(S1, S2, S3, S4), patient_name=None),
    "c-abdomen": _same(*_vat_rows(S1, S2, S3, S4), patient_name=None),
    "d-vat-trend": _same(*_vat_rows(S1, S2, S3, S4), patient_name=None),
    "b-segmental-s3": _same(*_composition(S1, S2, S3), patient_name=None),
    "foreign": _same(*[dict(_scan(*F1), age="34.0"), dict(_scan(*F2), age="34.1")], patient_name=None),
    "b-segmental-s4-again": _same(*_composition(S1, S2, S3, S4), patient_name=None),
    # extra pages for the other cases
    "named": _same(*_composition(S1, S4), age="45.4"),
    "named-other": _same(*_composition(F1, F2), patient_name=OTHER),
    "foreign-no-age": _same(*_composition(F1, F2), patient_name=None),
}
CLINIC_FILE = ["a-image", "b-segmental-s4", "c-abdomen", "d-vat-trend", "b-segmental-s3", "foreign",
               "b-segmental-s4-again"]  # the foreign profile is page 6


def _generate(tmp_path, monkeypatch, labels, staff_age=None, lab_date="05/04/2026", pages=None):
    folder = tmp_path / ("run-" + str(len(list(tmp_path.glob("run-*")))))
    folder.mkdir()
    path = fx.write_pdf(folder / "dexa.pdf", labels)
    pages = pages or {label: PAGES[label] for label in labels}
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: fx.DexaReader(path, labels, pages))
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(folder / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(folder / "diag"))
    labs = labs_fx.write_lab_pdf(folder / "labs.pdf", [labs_fx.section_preamble("SYN-D", lab_date)
                                                       + labs_fx.header_line(100) + labs_fx.row(130, "TSH", "1.0")])
    out = folder / "report.pdf"
    pipeline.run(str(labs), [str(path)], None, fx.PATIENT, staff_age, "male", str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    raw = out.with_suffix(".html").read_text(encoding="utf-8")
    html = " ".join(re.sub(r"<[^>]*>", " ", raw).split())  # visible text: SVG coordinates are not values
    review = (folder / "review.txt").read_text(encoding="utf-8")
    return text, html, review, raw


def _assert_absent(text, html, values):
    for value in values:
        assert value not in text, value
        assert value not in html, value


def _history(labels, staff_age=None, pages=None):
    keyed = [((1, n), (pages or PAGES)[label]) for n, label in enumerate(labels, start=1)]
    excluded = []
    history, _, summary = scan_dexa.gate(copy.deepcopy(keyed), fx.PATIENT, excluded_sink=excluded, staff_age=staff_age)
    return history, summary, excluded


def _attribute(labels, staff_age=None):
    return scan_dexa.attribute_pages([((1, n), PAGES[label]) for n, label in enumerate(labels, start=1)],
                                     fx.PATIENT, staff_age)


# --- the clinic's file: all four page types plus the foreign profile ---------------------------------------

@pytest.mark.parametrize("staff_age", [None, 45, 46])
def test_clinic_file_keeps_every_real_scan_with_printed_body_fat(tmp_path, monkeypatch, staff_age):
    text, html, review, raw = _generate(tmp_path, monkeypatch, CLINIC_FILE, staff_age)
    # All four of the patient's scans survive, oldest to newest, each with its PRINTED body fat %.
    for date, pct in (("November 3, 2025", "25.6%"), ("January 12, 2026", "22.9%"), ("March 2, 2026", "19.1%"),
                      ("April 20, 2026", "15.6%")):
        assert date in text and pct in text, (date, pct)
    assert "24.7%" not in text and "14.9%" not in text and "computed" not in text  # nothing computed
    # "When you came in" is the first scan, "Where you are now" the latest accepted one.
    assert re.search(r"When you came in · November 3, 2025 25\.6% body fat", text, re.I)
    assert re.search(r"Where you are now · April 20, 2026 15\.6% body fat", text, re.I)
    # The summary line and headline use the same printed values.
    assert "Body fat 25.6% on 11/03/2025 to 15.6% on 04/20/2026." in text
    assert "Body fat 15.6% on 04/20/2026." in text
    # The foreign profile appears nowhere: history, current, summary, headline.
    _assert_absent(text, html, OTHER_TEXT)
    assert 'class="dexa-scan-img"' in raw  # page 1 is the patient's (validated by its printed age)
    notes = without_staff_check(review).splitlines()
    assert notes[0] == ("INCOMPLETE - pages excluded: DEXA file 1 page 6 not confirmed as this patient's; their "
                        "scans are NOT in this report (history, current values or summary) - confirm which "
                        "patient they belong to")
    assert notes[1].startswith("  - DEXA file 1 page 6: no patient name printed and it prints age 34.1; ")
    assert notes[1].endswith("scan dates 06/15/2026, 07/27/2026")
    assert "DEXA - 7 page(s) read twice; 4 scan date(s) kept" in review


def test_staff_check_lists_accepted_and_excluded_scans(tmp_path, monkeypatch):
    _, _, review, _ = _generate(tmp_path, monkeypatch, CLINIC_FILE, 46)
    block = review.split("(end of STAFF CHECK)")[0].splitlines()
    assert block[0] == "STAFF CHECK - confirm before sending this report"
    assert f"  Patient name (entered): {fx.PATIENT}" in block
    assert ("  DEXA scan dates accepted: 11/03/2025, 01/12/2026, 03/02/2026, 04/20/2026; ages printed on accepted "
            "pages: 45.4") in block
    assert any(line.startswith("  DEXA EXCLUDED file 1 page 6: no patient name printed and it prints age 34.1")
               for line in block)
    assert "  Name mismatches: none" in block


def test_foreign_page_first_never_supplies_the_scan_image(tmp_path, monkeypatch):
    labels = ["foreign", *[label for label in CLINIC_FILE if label != "foreign"]]
    text, html, _, raw = _generate(tmp_path, monkeypatch, labels)
    assert 'class="dexa-scan-img"' not in raw and "15.6%" in text
    _assert_absent(text, html, OTHER_TEXT)


# --- the identity rule, case by case ------------------------------------------------------------------------

def test_no_age_pages_are_accepted_when_every_date_is_on_a_validated_page():
    accepted, unnamed, excluded = _attribute(CLINIC_FILE)
    assert accepted == [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 7)]
    assert [key for key, _ in excluded] == [(1, 6)]


def test_no_age_page_with_a_date_found_nowhere_else_is_excluded():
    history, _, excluded = _history(["a-image", "c-abdomen", "foreign-no-age"])
    assert [key for key, _ in excluded] == [(1, 3)]
    assert excluded[0][1] == ("no patient name and no readable age printed (or the two reads differ), and scan date(s) "
                              "06/15/2026, 07/27/2026 appear on no page validated by name or age; cannot confirm "
                              "it is this patient's")
    assert {reading["date_display"] for reading in history} == {S1[0], S2[0], S3[0], S4[0]}


def test_dates_found_only_on_an_age_inconsistent_page_are_excluded():
    # The foreign page (age 34.x) is excluded, so its dates cannot corroborate the no-age foreign page either.
    _, _, excluded = _history(["a-image", "c-abdomen", "foreign", "foreign-no-age"])
    assert [key for key, _ in excluded] == [(1, 3), (1, 4)]


def test_page_printing_another_name_is_always_excluded(tmp_path, monkeypatch):
    text, html, review, _ = _generate(tmp_path, monkeypatch, ["named", "c-abdomen", "named-other"], 46)
    _assert_absent(text, html, OTHER_TEXT)
    notes = without_staff_check(review).splitlines()
    assert notes[1] == ("  - DEXA file 1 page 3: prints patient name 'Other, Person', not the staff-entered patient; "
                        "scan dates 06/15/2026, 07/27/2026")
    assert "STAFF REVIEW - DEXA PATIENT NAME MISMATCH: DEXA file 1 page 3" in review
    staff_check = review.split("(end of STAFF CHECK)")[0]
    assert "DEXA EXCLUDED file 1 page 3: prints a different patient name" in staff_check


def test_staff_age_that_disagrees_with_the_cluster_leaves_nothing_to_corroborate():
    accepted, _, excluded = _attribute(CLINIC_FILE, staff_age=49)
    assert accepted == []  # every aged page disagrees with the form, so no page is validated: nothing guessed
    assert any(reason.startswith("no patient name printed and it prints age 45.4; the staff-entered age is 49")
               for _, reason in excluded)


def test_pages_without_a_clear_age_cluster_are_not_attributed():
    pages = {"young-1": _same(_scan(*S1, age="34.0"), patient_name=None),
             "young-2": _same(_scan(*S2, age="34.1"), patient_name=None),
             "old-1": _same(_scan(*S3, age="45.2"), patient_name=None),
             "old-2": _same(_scan(*S4, age="45.4"), patient_name=None)}
    history, _, excluded = _history(list(pages), pages=pages)
    assert history == [] and len(excluded) == 4
    assert "form no clear cluster" in excluded[0][1]


def test_unnamed_pages_with_no_age_and_nothing_to_corroborate_are_flagged(tmp_path, monkeypatch):
    text, _, review, _ = _generate(tmp_path, monkeypatch, ["b-segmental-s4", "b-segmental-s3"])
    assert "STAFF REVIEW - DEXA AGE NOT PRINTED: DEXA file 1 page 1" in review
    assert "INCOMPLETE - pages excluded: DEXA file 1 page 1, file 1 page 2" in review
    assert "15.6%" not in text  # no page validated by name or age: nothing from these pages is used


def test_name_matching_pages_validate_and_corroborate_no_age_pages():
    no_age_same_dates = _same(*_composition(S1, S4), patient_name=None)
    accepted, unnamed, excluded = scan_dexa.attribute_pages(
        [((1, 1), PAGES["named"]), ((1, 2), no_age_same_dates)], fx.PATIENT)
    assert accepted == [(1, 1), (1, 2)] and unnamed == [(1, 2)] and excluded == []
    # One date the named page does not show is enough to leave a no-age page unattributed.
    accepted, _, excluded = _attribute(["named", "b-segmental-s4"])
    assert accepted == [(1, 1)] and "01/12/2026, 03/02/2026 appear on no page validated" in excluded[0][1]


def test_reads_that_disagree_on_the_name_exclude_the_page():
    _, _, excluded = scan_dexa.attribute_pages(
        [((1, 1), [fx.read([fx.BASELINE]), fx.read([fx.BASELINE], patient_name=OTHER)])], fx.PATIENT)
    assert excluded[0][1].startswith("prints patient name 'Synthetic, Pat' / 'Other, Person', not the staff-entered")


# --- values: printed body fat %, contested values, computed label, stale scan ----------------------------

def test_printed_body_fat_is_used_and_never_replaced_by_a_computed_one():
    history, _, _ = _history(["a-image", "b-segmental-s4", "c-abdomen"])
    readings = [pipeline.DexaReading(**pipeline.scoring.normalize_dexa_body_fat(dict(r))) for r in history]
    assert [(r.date_display, r.body_fat_pct, r.computed) for r in readings] == [
        ("11/03/2025", "25.6%", []), ("01/12/2026", "22.9%", []), ("03/02/2026", "19.1%", []),
        ("04/20/2026", "15.6%", [])]


def test_body_fat_printed_differently_on_two_pages_is_withheld_not_computed():
    pages = {"a": _same(_scan(*S4), patient_name=None, age="45.4"),
             "b": _same(_scan(S4[0], S4[1], S4[2], S4[3], "15.9 %"), patient_name=None, age="45.4")}
    history, _, _ = _history(list(pages), pages=pages)
    reading = pipeline.scoring.normalize_dexa_body_fat(dict(history[0]))
    assert reading["body_fat_pct"] is None and "computed" not in reading


def test_body_fat_computed_only_when_never_printed_and_labelled(tmp_path, monkeypatch):
    unprinted = {"a": _same(_scan(S1[0], S1[1], S1[2], S1[3], None), _scan(S4[0], S4[1], S4[2], S4[3], None),
                            patient_name=None, age="45.4")}
    text, _, review, _ = _generate(tmp_path, monkeypatch, ["a"], pages=unprinted)
    assert "24.7% computed" in text and "14.9% computed" in text
    assert "DEXA scan dates accepted: 11/03/2025 (body fat % computed), 04/20/2026 (body fat % computed)" in review


@pytest.mark.parametrize(("lab_date", "warned"), [("07/30/2026", True), ("05/04/2026", False)])
def test_staff_warning_when_the_current_scan_is_old(tmp_path, monkeypatch, lab_date, warned):
    _, _, review, _ = _generate(tmp_path, monkeypatch, ["a-image", "c-abdomen"], lab_date=lab_date)
    note = ("DEXA SCAN OLDER THAN BLOODWORK: \"Where you are now\" shows the DEXA scan of 04/20/2026, 101 days "
            "before the latest bloodwork draw (07/30/2026); confirm no newer DEXA scan was left out")
    assert (note in review) is warned


@pytest.mark.parametrize(("printed", "expected"), [
    ("45.2", (45.2, "45.2")), ("46", (46.0, "46")), ("45 yrs", (45.0, "45")), ("Age: 45.3", (45.3, "45.3")),
    (None, None),
])
def test_page_age_reads_decimal_ages(printed, expected):
    assert scan_dexa.page_age([fx.read([], age=printed), fx.read([], age=printed)]) == expected


def test_page_age_reads_per_row_ages_and_takes_the_latest():
    assert scan_dexa.page_age(PAGES["foreign"]) == (34.1, "34.1")
    assert scan_dexa.page_age([fx.read([], age="45.2"), fx.read([], age="45.3")]) is None
