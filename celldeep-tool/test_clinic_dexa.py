"""The clinic's 7-page DEXA layout end to end (synthetic model in synthetic_fixtures/clinic_dexa.py): names
redacted on every page, all four page types, one foreign profile page (ages 34.0 / 34.1), a latest scan whose
body fat is printed "19.0 (e)" on one page and "19.0" on others, and two scans that print no body fat at all.
Run with and without a staff-entered age. Invented values; mocked reads; no network."""

import re

import pytest

import pipeline
import template
from schema import DexaReading, PatientRecord
from synthetic_fixtures import clinic_dexa as cd
from synthetic_fixtures.scenarios import run_scenario
from unknown_marker_policy import without_staff_check


@pytest.fixture(params=["clinic_dexa", "clinic_dexa_no_age"])
def report(request, tmp_path, monkeypatch):
    return run_scenario(request.param, tmp_path / request.param, monkeypatch)


def test_every_real_scan_is_kept_with_printed_or_correctly_computed_body_fat(report):
    text = report["text"]
    for date, shown in (("September 8, 2025", "24.4% fat"), ("November 17, 2025", "22.2% fat computed"),
                        ("January 26, 2026", "20.4% fat computed"), ("March 30, 2026", "19.0% fat estimated")):
        assert re.search(re.escape(date) + r" [^A-Z]*?" + re.escape(shown), text), (date, shown)
    # fat / (fat + lean), never fat / total mass (21.3% and 19.6% here).
    assert "21.3%" not in text and "19.6%" not in text


def test_the_same_body_fat_value_everywhere(report):
    text = report["text"]
    assert re.search(r"When you came in · September 8, 2025 24\.4% body fat", text, re.I)
    assert re.search(r"Where you are now · March 30, 2026 19\.0% estimated body fat", text, re.I)
    summary = "Body fat 24.4% on 09/08/2025 to 19.0% (estimated) on 03/30/2026."
    assert text.count(summary) == 2  # the summary bullet and the DEXA panel line
    assert "Body fat 19.0% (estimated) on 03/30/2026." in text  # the headline
    assert "— body fat" not in text.casefold() and "—% optimized" not in text.casefold()
    assert re.search(r"\d+\.\d% optimized", text, re.I)  # the DEXA score is shown, from the same 19.0


def test_the_foreign_profile_reaches_nothing(report):
    visible = " ".join(re.sub(r"<[^>]*>", " ", report["html"]).split())
    for value in cd.FOREIGN_TEXT:
        assert value not in report["text"], value
        assert value not in visible, value
    assert 'class="dexa-scan-img"' in report["html"]  # page 1 is the patient's (validated by its printed age)


def test_notices_for_the_excluded_page_and_the_old_scan(report):
    notes = without_staff_check(report["review"]).splitlines()
    assert notes[0].startswith("INCOMPLETE - pages excluded: DEXA file 1 page 6 not confirmed as this patient's")
    assert notes[1] == ("  - DEXA file 1 page 6: no patient name printed and it prints age 34.1; the DEXA pages center "
                        "on age 45.3; scan dates 05/18/2026, 07/06/2026")
    assert ('DEXA SCAN OLDER THAN BLOODWORK: "Where you are now" shows the DEXA scan of 03/30/2026, 77 days before '
            "the latest bloodwork draw (06/15/2026)") in report["review"]
    assert 'marked "(e)" on some pages only; shown as estimated' in report["review"]
    # The confirmation step lists exactly the excluded page, with its age, dates and reason.
    assert report["confirmation"] == [notes[1].strip().removeprefix("- ")]


def _panel(sex, body_fat="19.0%", area=None):
    reading = DexaReading(date_display="03/30/2026", total_mass_lb=166.2, fat_mass_lb=30.4, lean_mass_lb=129.6,
                          body_fat_pct=body_fat, vat_fat_mass_lb=0.86, visceral_fat_area_cm2=area)
    record = PatientRecord(name="Synthetic, Pat", age=45, sex=sex, dexa_history=[reading])
    roll, _, _, _, _ = template.build_rollups(record, None, None, False)
    return template.dexa_panel(record, {"headlines": {}}, roll, None), pipeline.dexa_score_notes(record)


def test_no_dexa_score_is_never_shown_as_a_dash():
    html, notes = _panel(sex=None)  # no sex: no body fat range, no VAT area: nothing to score
    assert "Optimized" not in html and "—%" not in html
    assert notes == ['DEXA SCORE NOT SHOWN: the patient report shows no DEXA "% optimized" for the scan of '
                     "03/30/2026 (no sex entered for the body fat ranges; no VAT area printed for that scan); the "
                     "Structure score is left out of the overall score"]
    html, notes = _panel(sex="male")
    assert "Optimized" in html and notes == []
