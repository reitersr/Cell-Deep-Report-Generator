"""Limited male panel (Access Medical layout), round 1 fixes. Synthetic fixtures only.

1. The OUT OF RANGE SUMMARY is ignored silently (no confirmation item, no INCOMPLETE notice, no staff note), even when
   its heading is worded differently or not recognized at all; a summary row with no matching table row warns.
2. "Fasting: N": glucose is shown as "Glucose (non-fasting)" (lab-reported, the lab's range and flag, a one-line note),
   never as "Glucose (fasting)" and never scored; "Fasting: Y" or no Fasting line keeps the scored fasting glucose.
3. Cortisol keeps "(AM)" only when the printed collection time is inside the lab's printed morning window; the
   collection time is shown next to the value; still unscored with no range printed.
4. Lab-reported rows show the lab's printed unit, and none when none is printed.
5. "FOUND IN SOURCE BUT MISSING" fires only for names printed as result rows.
6. The STAFF CHECK "Scored markers" count equals the report's own scored count."""

import re

import fitz
import pytest

import access_medical
import pipeline
from schema import Marker
from synthetic_fixtures import access_medical_lab as am
from synthetic_fixtures.scenarios import run_scenario


def _parse(tmp_path, page_items, name="labs.pdf"):
    audit, excluded, notes = [], [], []
    with fitz.open(am.write(tmp_path / name, page_items)) as document:
        occurrences, unrecognized, info = access_medical.parse(list(document), row_audit=audit, review_notes=notes,
                                                               exclusions=excluded, sex="male")
    return occurrences, unrecognized, info, excluded, notes


def _lab_reported(unrecognized):
    return {item["name"]: item for item in pipeline.lab_reported.build(unrecognized)[0]}


# --- 1. the summary block --------------------------------------------------------------------------------

@pytest.mark.parametrize("heading", ["OUT OF RANGE SUMMARY", "Out of Range Results", "OUT OF RANGE", None])
def test_the_summary_above_the_column_header_is_silent_whatever_its_heading(tmp_path, heading):
    _, _, _, excluded, notes = _parse(tmp_path, am.limited_pages(summary_heading=heading))
    assert excluded == []
    assert not any("SUMMARY" in note for note in notes)


def _without_heading(pages):
    return [[item for item in page if item[2] != "OUT OF RANGE SUMMARY"] for page in pages]


def test_summary_rows_below_the_column_header_with_no_heading_are_silent_repeats(tmp_path):
    occurrences, unrecognized, _, excluded, notes = _parse(tmp_path, _without_heading(am.pages()))
    assert excluded == [] and not any("SUMMARY" in note for note in notes)
    # Never counted twice: one MCV, one Estradiol.
    assert sum(u["raw_name"] == "MCV" for u in unrecognized) == 1
    assert sum(o["name"] == "Estradiol" for o in occurrences) == 1


def test_a_row_under_no_section_that_repeats_nothing_is_excluded(tmp_path):
    summary = [*am.SUMMARY, ("Zinc", "55 L", "60 - 130", "ug/dL")]
    _, _, _, excluded, _ = _parse(tmp_path, _without_heading(am.pages(summary=summary)))
    assert excluded == [{"page": 1, "section": "no section",
                         "reason": "row 'Zinc' is printed under no section title; not read"}]


def test_a_summary_row_with_no_table_row_warns_in_the_staff_notes_only(tmp_path):
    summary = [*am.LIMITED_SUMMARY, ("Ferritin", "12 L", "38 - 380", "ng/mL")]
    _, _, _, excluded, notes = _parse(tmp_path, am.limited_pages(summary=summary))
    assert excluded == []
    assert [note for note in notes if "SUMMARY" in note] == [
        "OUT OF RANGE SUMMARY: 'Ferritin 12 L 38 - 380 ng/mL' (page 1) matches no row in the result tables; not used - "
        "check the PDF"]


# --- 2. non-fasting glucose ------------------------------------------------------------------------------

def test_non_fasting_glucose_is_lab_reported_not_scored(tmp_path):
    occurrences, unrecognized, _, _, notes = _parse(tmp_path, am.pages(fasting="N"))
    assert "Glucose (fasting)" not in {o["name"] for o in occurrences}
    glucose = _lab_reported(unrecognized)["Glucose (non-fasting)"]
    assert glucose["group"] == "Chemistry"
    assert glucose["results"] == [{"date_display": "08/07/2026", "disp_value": "95", "lab_flag": None,
                                   "lab_range": "65 - 99", "unit": "mg/dL"}]
    assert glucose["note"] == "This sample was drawn without fasting, so it is shown as reported and not scored."
    assert any(note.startswith("NON-FASTING GLUCOSE: the lab header prints 'Fasting: N'; Glucose '95'") for note in notes)
    # Urine glucose ("Negative") is a different test and is untouched.
    assert "Urinalysis — Glucose" in _lab_reported(unrecognized)


@pytest.mark.parametrize("fasting", ["Y", None])
def test_fasting_or_unknown_keeps_the_scored_fasting_glucose(tmp_path, fasting):
    occurrences, unrecognized, _, _, notes = _parse(tmp_path, am.pages(fasting=fasting))
    assert [o["disp_value"] for o in occurrences if o["name"] == "Glucose (fasting)"] == ["95"]
    assert "Glucose (non-fasting)" not in _lab_reported(unrecognized)
    assert not any("NON-FASTING" in note for note in notes)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return run_scenario("access_medical", tmp_path_factory.mktemp("round1") / "run")


def test_the_report_shows_the_non_fasting_glucose_with_its_note(report):
    text = report["text"]
    assert ("Glucose (non-fasting) This sample was drawn without fasting, so it is shown as reported and not scored. "
            "65 - 99 — 95 mg/dL August 7, 2026") in text
    assert "Glucose (fasting)" not in text
    assert "NON-FASTING GLUCOSE" in report["review"] and "NON-FASTING GLUCOSE" not in text


# --- 3. cortisol label -----------------------------------------------------------------------------------

def _cortisol(time="07:45", window=(6, 10)):
    marker = Marker(name="Cortisol, Total (AM)", category="Hormones", unit="", kind="range", disp_range="",
                    now=14.1, disp_now="14.1", now_tier="unscored")
    header = {"collection_time": time, **({"morning_window": window} if window else {})}
    return marker, pipeline.label_cortisol([marker], header)


def test_cortisol_keeps_am_inside_the_printed_morning_window():
    marker, notes = _cortisol("07:45")
    assert (marker.display_name, marker.value_note, notes) == ("Cortisol, Total (AM)", "collected 07:45", [])


@pytest.mark.parametrize(("time", "window", "why"), [
    ("14:30", (6, 10), "collected 14:30, outside the lab's morning window 6-10"),
    ("2:30 PM", (6, 10), "collected 2:30 PM, outside the lab's morning window 6-10"),
    ("07:45", None, "no morning window printed"),
    (None, (6, 10), "no collection time printed"),
])
def test_cortisol_drops_am_otherwise(time, window, why):
    marker, notes = _cortisol(time, window)
    assert marker.display_name == "Cortisol, Total"
    assert notes == [f"CORTISOL LABEL: shown as 'Cortisol, Total' without '(AM)' ({why})"]
    assert marker.value_note == (f"collected {time}" if time else None)


def test_cortisol_label_is_unchanged_for_layouts_without_a_collection_time():
    marker = Marker(name="Cortisol, Total (AM)", category="Hormones", unit="", kind="range", disp_range="",
                    now=14.1, disp_now="14.1")
    assert pipeline.label_cortisol([marker], {}) == [] and marker.display_name is None


def test_cortisol_in_the_report(report, tmp_path):
    assert "Cortisol, Total (AM) Not scored, no range printed no range printed — 14.1 August 7, 2026 collected 07:45" \
        in report["text"]
    afternoon = run_scenario("access_medical_limited", tmp_path / "limited")  # no morning window printed
    assert re.search(r"Cortisol, Total Not scored, no range printed .*— 12\.0 August 7, 2026 collected 07:45",
                     afternoon["text"])
    assert "Cortisol, Total (AM)" not in afternoon["text"]


# --- 4. units in the lab-reported section ----------------------------------------------------------------

def test_lab_reported_rows_show_the_printed_unit_and_never_a_guessed_one(report):
    text = report["text"]
    assert "MCV 80 - 100 — 101 H fL August 7, 2026" in text
    assert "Sodium 135 - 146 — 140 mmol/L August 7, 2026" in text
    assert "Free Testosterone 5.7 - 17.9 — 11.2 August 7, 2026" in text  # no unit printed: none shown
    assert "Urinalysis — Color Yellow — Yellow August 7, 2026" in text


def test_a_printed_unit_on_free_testosterone_is_shown(tmp_path):
    pages = am.limited_pages()
    pages[1] = [(x, y, words) for x, y, words in pages[1]]
    free_t = next(item for item in pages[1] if item[2] == "Testosterone, Free")
    pages[1].append((am.X["units"], free_t[1], "ng/dL"))
    _, unrecognized, _, _, _ = _parse(tmp_path, pages)
    assert _lab_reported(unrecognized)["Free Testosterone"]["results"][0]["unit"] == "ng/dL"
    assert "unit" not in _lab_reported(unrecognized)["Bioavailable Testosterone"]["results"][0]


# --- 5. completeness check -------------------------------------------------------------------------------

def _completeness(lab_text, source_rows=()):
    extracted = {"marker_occurrences": [], "source_rows": [{"name": name} for name in source_rows]}
    return pipeline.verify_extraction_completeness(extracted, lab_text=lab_text).other_notes


def test_completeness_fires_only_for_names_printed_as_result_rows():
    assert _completeness("Creatinine, Serum 0.98 0.60 - 1.24 mg/dL\nBUN 14 7 - 25 mg/dL", ["Creatinine, Serum", "BUN"]) == []
    assert _completeness("URINALYSIS GROSS EXAMINATION\nOccult blood Negative Negative", ["Occult blood"]) == []
    assert _completeness("Magnesium is reported by another lab.\nResults in mg/dL") == []
    assert _completeness("Fibrinogen 288 mg/dL") == [
        "WARNING: MARKER 'Fibrinogen' FOUND IN SOURCE BUT MISSING FROM EXTRACTION - NEEDS HUMAN REVIEW"]


def test_no_false_completeness_warning_on_the_access_report(report):
    assert "FOUND IN SOURCE BUT MISSING" not in report["review"]


# --- 6. scored-marker count ------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["access_medical", "access_medical_limited", "chl_digital"])
def test_staff_check_scored_count_matches_the_report(tmp_path, name):
    result = run_scenario(name, tmp_path / name)
    staff = int(re.search(r"Scored markers: (\d+);", result["review"])[1])
    shown = re.search(r"Of your (\d+) scored markers|Your (1) scored marker is", result["text"])
    assert staff == int(shown[1] or shown[2])
