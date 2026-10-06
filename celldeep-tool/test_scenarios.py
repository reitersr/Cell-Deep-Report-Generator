"""Every synthetic end-to-end scenario (synthetic_fixtures/scenarios.py): the confirmation step fires only for
genuine items left out of the report (never for a section heading), new layouts and a malformed provider note
never stop the report, and the STAFF CHECK shows what staff must confirm. Invented data; mocked reads."""

import pytest

from synthetic_fixtures import scenarios
from unknown_marker_policy import without_staff_check

# How many items the confirmation step lists per scenario, and why.
EXPECTED_CONFIRMATIONS = {
    "quest_digital": 1,       # one printed result with a test name the tool does not know
    "chl_digital": 0,
    "labcorp_digital": 1,     # one printed result with a test name the tool does not know
    "scanned": 0,
    "mixed": 0,
    "variant": 3,             # a page under an unknown header, a renamed test, a provider note without sections
    "scanned_noisy": 1,       # FERRITIN: three reads, three values (the heading and the 2-of-3 row never count)
    "clinic_dexa": 1,         # the foreign DEXA profile page
    "clinic_dexa_no_age": 1,  # the same page without a staff-entered age
}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scenarios")
    return {name: scenarios.run_scenario(name, folder / name) for name in scenarios.SCENARIOS}


def test_the_confirmation_step_fires_only_for_genuine_items(results):
    assert {name: len(result["confirmation"]) for name, result in results.items()} == EXPECTED_CONFIRMATIONS
    for result in results.values():
        assert not any("CBC (INCLUDES" in item for item in result["confirmation"])


def test_scan_reads_two_of_three_and_headings(results):
    noisy = results["scanned_noisy"]
    assert noisy["reads"] == 6  # both pages disagree somewhere, so each is read a third time
    assert "Fasting Insulin" in noisy["text"] and "4.4" in noisy["text"]  # reads 1 and 3 agree on 4.4
    notes = without_staff_check(noisy["review"])
    assert ("INCOMPLETE - row excluded: FERRITIN (reads disagree: 88 / 86 / 83) - scanned lab page 2; not in this "
            "report") in notes.splitlines()
    assert "INCOMPLETE - row excluded: CBC" not in notes and "INCOMPLETE - row excluded: INSULIN" not in notes
    assert noisy["confirmation"] == ["Lab PDF page 2 (scanned): result FERRITIN excluded - reads disagree: "
                                     "88 / 86 / 83"]
    assert "rows excluded (reads disagree): 1; rows excluded by other checks: 0" in noisy["review"]


def test_a_new_layout_and_a_malformed_note_still_build_the_report(results):
    variant = results["variant"]
    assert "TSH" in variant["text"] and "hs-CRP" in variant["text"]
    for absent in ("Ferritin", "Vitamin B12", "Thyroid Stimulating Hormone Ultra", "512"):
        assert absent not in variant["text"]
    assert variant["confirmation"] == [
        "Lab PDF page 3 (whole page): result rows printed under no recognized table header (expected "
        "'Current'/'Historical' or 'In Range'/'Out of Range'), so they were not read: Ferritin, Vitamin B12",
        "Lab PDF: 1 printed result(s) with a test name the tool does not recognize, left out of the report and "
        "listed in the staff notes: Thyroid Stimulating Hormone Ultra",
        "Provider note: NOT READ - it has no '## ' section headings, so its protocol, concerns, targets and "
        "Vitality Index are not in the report (the staff notes list the required headings)"]
    notes = without_staff_check(variant["review"])
    assert notes.startswith("INCOMPLETE - pages/sections excluded: lab PDF page(s) 3 ")
    assert "'## Consultation Note', '## Treatment Status', '## Patient Concerns', '## Protocol'" in notes
    staff_check = variant["review"].split("(end of STAFF CHECK)")[0]
    assert "  Provider note: NOT READ - no '## ' section headings" in staff_check


def test_staff_check_shows_the_dexa_bloodwork_gap(results):
    staff_check = results["clinic_dexa"]["review"].split("(end of STAFF CHECK)")[0].splitlines()
    assert ("  DEXA scan dates accepted: 09/08/2025, 11/17/2025 (body fat % computed), 01/26/2026 (body fat % "
            "computed), 03/30/2026 (body fat % estimated); ages printed on accepted pages: 45.3") in staff_check
    assert ("  DEXA vs bloodwork: current DEXA scan 03/30/2026 is 77 days before the latest bloodwork draw "
            "(06/15/2026); limit 60 days - OLDER THAN THE LIMIT, see the warning below") in staff_check
    assert "  Name mismatches: none" in staff_check


def test_nothing_staff_only_reaches_a_patient_report(results):
    for name, result in results.items():
        for marker in ("INCOMPLETE", "STAFF CHECK", "source=scan", "reads disagree", "excluded", "estimated: "):
            assert marker not in result["text"], (name, marker)
