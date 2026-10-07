"""Regressions for four scanned results dropped in production (Testosterone Total MS, Free
Testosterone, Folate, Insulin). Values are synthetic stand-ins; the print conventions mirror
the Quest scan layout: a Lab column code, an out-of-range list that prints value and flag
together, "<OR=" / "> OR =" reference notation, units in the reference column, and a plain
"INSULIN" test name."""

import copy

import pytest

import pipeline
import scan_bloodwork as scan
from test_scan_bloodwork import MockClient, mixed_pdf  # noqa: F401  (pytest fixture)

DATE = "04/14/2026"


def _row(name, result, flag, reference, column, lab_code=None):
    row = {"name": name, "result_text": result, "flag": flag, "reference_range": reference,
           "column": column, "page": 2, "illegible": False, "section": "ROUTINE PANELS"}
    if "lab_code" in scan.SCAN_SCHEMA["properties"]["rows"]["items"]["properties"]:
        row["lab_code"] = lab_code
    return row


def _pages():
    rows = [
        _row("TESTOSTERONE, TOTAL, MS", "1193", "H", "250-1100 ng/dL", "out_of_range", "AMD"),
        _row("TESTOSTERONE, FREE", "210.0", "H", "35.0-155.0 pg/mL", "out_of_range"),
        _row("FOLATE, SERUM", "6.9", None, "> OR = 5.4 ng/mL", "in_range"),
        _row("INSULIN", "3.1", None, "<OR=18.4 uIU/mL", "in_range"),
        _row("TSH", "1.19", None, "0.40-4.50 mIU/L", "in_range"),
    ]
    summary = [{"name": "TESTOSTERONE, TOTAL, MS", "result_text": "1193", "flag": "H", "illegible": False},
               {"name": "TESTOSTERONE, FREE", "result_text": "210.0", "flag": "H", "illegible": False}]
    base = {"specimen_id": None, "collected": None, "footer": None, "patient_name": None,
            "illegible": False}
    return [{**base, "page": 2, "rows": rows, "out_of_range_summary": None},
            {**base, "page": 3, "rows": [], "out_of_range_summary": summary}]


def _reads(pages, tweak=None, third_read_pages=()):
    """Two independent reads per page (three for the pages in third_read_pages, which the pipeline reads a
    third time because the first two disagree); tweak(page_number, reading, page) edits one read."""
    reads = []
    for page in pages:
        for reading in ((0, 1, 2) if page["page"] in third_read_pages else (0, 1)):
            copied = copy.deepcopy(page)
            if tweak:
                tweak(copied["page"], reading, copied)
            reads.append(copied)
    return reads


def _extract(mixed_pdf, tmp_path, reads):
    return pipeline.extract(str(mixed_pdf), [], None, patient_name="Synthetic, Pat", collected_date=DATE,
                            client=MockClient(reads), audit_root=str(tmp_path))


def _latest(extracted):
    """Values kept for the scanned draw, scored or lab-reported (a Free Testosterone printed with the dialysis range is
    lab-reported: clinic_config.CELLDEEP_RANGE_BASIS)."""
    found = {item["name"]: item["disp_value"] for item in extracted["marker_occurrences"] if item["date_display"] == DATE}
    found.update({item["name"]: result["disp_value"] for item in extracted["lab_reported"]
                  for result in item["results"] if result["date_display"] == DATE and item["name"] == "Free Testosterone"})
    return found


EXPECTED = {"Testosterone, Total": "1193", "Free Testosterone": "210.0", "Folate": "6.9",
            "Fasting Insulin": "3.1", "TSH": "1.19"}


def test_clean_reads_keep_all_four_results(mixed_pdf, tmp_path):
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages()))
    assert _latest(extracted) == EXPECTED
    assert extracted["latest_draw_date"] == DATE
    assert not any(" excluded " in note for note in extracted["other_notes"])


def test_plain_insulin_name_matches_fasting_insulin(mixed_pdf, tmp_path):
    """Cause: alias. Quest prints the test as plain "INSULIN", which had no exact alias, so the
    row became an unrecognized marker and never reached the report."""
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages()))
    assert _latest(extracted)["Fasting Insulin"] == "3.1"
    assert not any(row["raw_name"] == "INSULIN" for row in extracted["unrecognized_markers"])


@pytest.mark.parametrize("readings", [(0, 1), (0,)])
def test_lab_code_printed_beside_name_is_not_part_of_the_name(mixed_pdf, tmp_path, readings):
    """Cause: the Lab column code (AMD) was transcribed into the test name, so the name matched
    no alias (both reads) or the two reads no longer paired (one read)."""
    def tweak(number, reading, page):
        if number == 2 and reading in readings:
            page["rows"][0]["name"] = "TESTOSTERONE, TOTAL, MS AMD"
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages(), tweak))
    assert _latest(extracted)["Testosterone, Total"] == "1193"


@pytest.mark.parametrize("summary_text", ["210.0 H", "210.0  H", "210.0H"])
def test_summary_entry_printing_value_and_flag_together_agrees(mixed_pdf, tmp_path, summary_text):
    """Cause: out-of-range cross-check. The printed list shows "210.0 H" as one string with no
    separate flag, which compared unequal to the row's value "210.0" + flag "H"."""
    def tweak(number, reading, page):
        if number == 3:
            page["out_of_range_summary"][1].update(result_text=summary_text, flag=None)
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages(), tweak))
    assert _latest(extracted)["Free Testosterone"] == "210.0"


@pytest.mark.parametrize(("first", "second"), [
    ("> OR = 5.4 ng/mL", ">OR=5.4 ng/mL"), ("> OR = 5.4 ng/mL", ">= 5.4 ng/mL"),
    ("> OR = 5.4 ng/mL", "> or = 5.4  ng/mL"),
])
def test_reads_spelling_the_same_printed_range_differently_agree(mixed_pdf, tmp_path, first, second):
    """Cause: read disagreement. Both reads copied the same printed "> OR =" range but spaced or
    symbolised it differently, so exact string comparison excluded the row."""
    def tweak(number, reading, page):
        if number == 2:
            page["rows"][2]["reference_range"] = (first, second)[reading]
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages(), tweak))
    assert _latest(extracted)["Folate"] == "6.9"


def test_genuinely_different_ranges_still_exclude_the_row(mixed_pdf, tmp_path):
    def tweak(number, reading, page):  # three reads, three different ranges: no two agree
        if number == 2 and reading:
            page["rows"][2]["reference_range"] = ("> OR = 3.4 ng/mL", "> OR = 4.4 ng/mL")[reading - 1]
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages(), tweak, third_read_pages={2}))
    assert "Folate" not in _latest(extracted)
    assert any("excluded 'FOLATE, SERUM': agreement gate" in note for note in extracted["other_notes"])


def test_a_third_read_that_agrees_with_one_of_two_keeps_the_row(mixed_pdf, tmp_path):
    """Two reads disagree, so the page is read a third time; the value two of the three reads print exactly
    (value, flag and range) is kept, the same in every run."""
    def tweak(number, reading, page):
        if number == 2 and reading == 1:
            page["rows"][3]["result_text"] = "3.4"  # read 2 misreads INSULIN; reads 1 and 3 print 3.1
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages(), tweak, third_read_pages={2}))
    assert _latest(extracted) == EXPECTED
    assert extracted["scan_row_exclusions"] == [] and extracted["preflight"] == []


def test_summary_that_contradicts_value_still_excludes(mixed_pdf, tmp_path):
    def tweak(number, reading, page):
        if number == 3:
            page["out_of_range_summary"][1].update(result_text="201.0 H", flag=None)
    extracted = _extract(mixed_pdf, tmp_path, _reads(_pages(), tweak))
    assert "Free Testosterone" not in _latest(extracted)
    assert any("excluded 'TESTOSTERONE, FREE': summary gate" in note for note in extracted["other_notes"])


@pytest.mark.parametrize(("result", "flag", "reference", "valid"), [
    ("1193", "H", "250-1100 ng/dL", True), ("1193", None, "250-1100 ng/dL", False),
    ("3.1", None, "<OR=18.4 uIU/mL", True), ("19.0", None, "<OR=18.4", False),
    ("6.9", None, "> OR = 5.4 ng/mL", True), ("5.0", None, "> OR = 5.4", False),
])
def test_flag_check_reads_printed_or_equal_notation_and_units(result, flag, reference, valid):
    assert scan._numeric_flag({"result_text": result, "flag": flag, "reference_range": reference},
                              pipeline._CELL_VALUE_RE) is valid


@pytest.mark.parametrize(("name", "expected"), [
    ("TESTOSTERONE, TOTAL, MS AMD", "TESTOSTERONE, TOTAL, MS"),
    ("TESTOSTERONE, TOTAL, MS (AMD)", "TESTOSTERONE, TOTAL, MS"),
    ("ESTROGENS, TOTAL, IA", "ESTROGENS, TOTAL, IA"),
    ("AMD", "AMD"),
    ("TSH", "TSH"),
])
def test_strip_lab_code_only_removes_a_trailing_lab_column_code(name, expected):
    assert scan.strip_lab_code(name, {"AMD", "IA", "EZ"}) == expected
