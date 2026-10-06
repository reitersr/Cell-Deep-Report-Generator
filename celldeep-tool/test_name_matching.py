"""A staff-entered name matches a printed name when the words are the same in any order and case, or when
staff entered the first name plus the last-name initial, in either order. Only a real mismatch raises a
PATIENT NAME MISMATCH notice. Synthetic names only."""

import copy

import pytest

import pipeline
import scan_bloodwork
import scan_dexa
from synthetic_fixtures import dexa as dexa_fx
from test_scan_bloodwork import payloads


@pytest.mark.parametrize(("staff", "printed"), [
    ("Pat S", "SYNTHETIC, PAT"),          # first name + last initial, printed "LAST, FIRST"
    ("S Pat", "Synthetic, Pat"),          # initial first
    ("Pat S.", "SYNTHETIC, PAT"),         # initial with a period
    ("Pat S", "Pat Synthetic"),           # printed "FIRST LAST"
    ("Pat S", "SYNTHETIC, PAT Q"),        # printed middle initial
    ("Synthetic, Pat", "pat  synthetic"),  # full name, any order and case
])
def test_names_that_match(staff, printed):
    assert scan_bloodwork.names_match(staff, printed)


@pytest.mark.parametrize(("staff", "printed"), [
    ("Pat S", "Other, Person"),           # a genuinely different patient
    ("Pat S", "SAMPLE, PATRICIA"),        # same initial, different first name
    ("Pat Q", "SYNTHETIC, PAT"),          # same first name, different last initial
    ("Pat S", "SYNTHETIC"),               # no first name printed
    ("S", "SYNTHETIC, PAT"),              # an initial alone is not a name
    ("Pat S", "--"),
    ("Pat Sy", "SYNTHETIC, PAT"),         # a partial last name is not an initial
])
def test_names_that_do_not_match(staff, printed):
    assert not scan_bloodwork.names_match(staff, printed)


def test_staff_notes_header_uses_the_same_rule():
    lines = pipeline.name_header("Pat S", [("DEXA", "file 1 page 1", "SYNTHETIC, PAT"),
                                           ("DEXA", "file 1 page 2", "Other, Person")])
    assert "  DEXA (file 1 page 1): SYNTHETIC, PAT - matches" in lines
    assert "  DEXA (file 1 page 2): Other, Person - NAME MISMATCH - confirm this source belongs to the patient" in lines


def test_dexa_short_staff_name_keeps_the_page_without_a_notice():
    pages = [((1, 1), [dexa_fx.read([dexa_fx.BASELINE], patient_name="SYNTHETIC, PAT")] * 2),
             ((1, 2), [dexa_fx.read([dexa_fx.FOLLOW_UP], patient_name="Other, Person")] * 2)]
    excluded = []
    history, _, summary = scan_dexa.gate(copy.deepcopy(pages), "Pat S", excluded_sink=excluded)
    assert [h["date_display"] for h in history] == ["05/12/2025"]
    assert [key for key, _ in excluded] == [(1, 2)]
    mismatch = [line for line in summary if "MISMATCH" in line]
    assert mismatch == ["  STAFF REVIEW - DEXA PATIENT NAME MISMATCH: DEXA file 1 page 2 prints a patient name that "
                        "differs from the staff-entered name; page excluded - confirm which patient it belongs to"]
    assert not any("name not printed" in line for line in summary)


def test_scanned_lab_pages_raise_a_mismatch_only_for_a_different_name():
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for pair in pairs:
        for page in pair:
            page["patient_name"] = "SYNTHETIC, PAT"
    pairs[1][1]["patient_name"] = "Other, Person"
    _, notes = scan_bloodwork.gate_staff_identified_reads(pairs, "04/14/2026", pipeline._CELL_VALUE_RE, "Pat S")
    mismatches = [note for note in notes if note.startswith("STAFF REVIEW - PATIENT NAME MISMATCH")]
    assert [note.split("page ")[1].split()[0] for note in mismatches] == [str(pairs[1][1]["page"])]
