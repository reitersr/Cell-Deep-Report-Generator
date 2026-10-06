"""Access Medical Laboratories digital layout (access_medical.py), registered in lab_layouts. Synthetic fixtures
only (synthetic_fixtures/access_medical_lab.py): a fake patient, fake values, the layout's quirks.

Covers: layout selection; every table row read once with the right value, flag, range and unit; explanatory text
(GFR stage table, % Free PSA table, method note, cortisol time-of-day lines) never becomes a result; the OUT OF
RANGE SUMMARY never double counts and a disagreement is a staff notice; the header (patient, DOB, age, two-digit
Coll. Date, Fasting); assays that differ from the CellDeep range basis; word results shown, not scored; an
unreadable page or row excluded with a notice. End to end: a one-draw, one-scan 21-year-old male."""

import re

import fitz
import pytest

import access_medical
import clinic_config
import lab_layouts
import pipeline
from markers_reference import MARKER_LIBRARY, resolve_marker_config
from synthetic_fixtures import access_medical_lab as am
from synthetic_fixtures import layouts
from synthetic_fixtures.scenarios import run_scenario
from unknown_marker_policy import without_staff_check


def _pages(tmp_path, page_items=None, name="labs.pdf"):
    document = fitz.open(am.write(tmp_path / name, page_items))
    return list(document)


def _parse(tmp_path, page_items=None):
    audit, excluded, notes = [], [], []
    occurrences, unrecognized, info = access_medical.parse(_pages(tmp_path, page_items), row_audit=audit,
                                                           review_notes=notes, exclusions=excluded, sex="male")
    return occurrences, unrecognized, info, audit, excluded, notes


def _rows(audit):
    rows = []
    for row in audit:
        for occurrence in row["occurrences"]:
            rows.append((row["section"], row["name"], occurrence["disp_value"], occurrence["lab_flag"] or None,
                         occurrence["lab_range_display"], occurrence["printed_unit"]))
        for unknown in row["unrecognized"]:
            cell = unknown["cells"][0]
            rows.append((row["section"], row["name"], cell["disp_value"], cell["lab_flag"], unknown["raw_range"],
                         unknown["raw_unit"]))
    return rows


# --- layout selection ------------------------------------------------------------------------------------

def test_the_registry_selects_access_medical_only_for_its_own_layout(tmp_path):
    assert lab_layouts.select(_pages(tmp_path)) is access_medical
    for name in ("quest_digital", "chl_digital", "labcorp_digital"):
        builder, _ = layouts.FIXTURES[name]
        with fitz.open(builder(tmp_path / f"{name}.pdf")) as document:
            assert lab_layouts.select(list(document)) is None, name


# --- rows --------------------------------------------------------------------------------------------------

def test_every_row_is_read_once_with_value_flag_range_and_unit(tmp_path):
    _, _, _, audit, excluded, _ = _parse(tmp_path)
    assert _rows(audit) == am.EXPECTED
    assert excluded == []


def test_explanatory_text_never_becomes_a_result(tmp_path):
    occurrences, unrecognized, _, audit, excluded, notes = _parse(tmp_path)
    names = [row["name"] for row in audit]
    for text in ("Stage", "% Free PSA", "10 - 15%", "Morning", "Afternoon", "performed", "Roche", "ECLIA"):
        assert not any(text in name for name in names), text
    values = {o["disp_value"] for o in occurrences} | {u["cells"][0]["disp_value"] for u in unrecognized}
    for value in ("49.2%", "57.5%", ">90", "6.02", "18.4", "2.68", "30 to 59"):
        assert value not in values, value
    assert excluded == []


def test_the_summary_block_is_never_counted_and_is_cross_checked(tmp_path):
    occurrences, unrecognized, _, audit, _, notes = _parse(tmp_path)
    counts = {name: sum(row["name"] == name for row in audit) for name in ("MCV", "BUN/Creat Ratio", "Estradiol")}
    assert counts == {"MCV": 1, "BUN/Creat Ratio": 1, "Estradiol": 1}
    assert not any("OUT OF RANGE SUMMARY" in note for note in notes)  # the summary agrees with the table
    disagreeing = [("MCV", "101 H", "80 - 100", "fL"), ("Estradiol", "50 H", "8 - 35", "pg/mL"),
                   ("Ferritin", "9 L", "38 - 380", "ng/mL"), ("Zinc", "55 L", "60 - 130", "ug/dL")]
    *_, excluded, notes = _parse(tmp_path, am.pages(summary=disagreeing))
    assert ("OUT OF RANGE SUMMARY DISAGREES: 'Estradiol 50 H 8 - 35 pg/mL' (page 1) does not match the result "
            "table (52 H (page 4)); the table value is used - check the PDF") in notes
    assert ("OUT OF RANGE SUMMARY DISAGREES: 'Ferritin 9 L 38 - 380 ng/mL' (page 1) does not match the result "
            "table (120 (page 3)); the table value is used - check the PDF") in notes
    assert ("OUT OF RANGE SUMMARY: 'Zinc 55 L 60 - 130 ug/dL' (page 1) matches no row in the result tables; not "
            "used - check the PDF") in notes
    assert not any("MCV" in note for note in notes if "SUMMARY" in note)  # an agreeing row stays silent
    assert excluded == []  # the summary never reaches the confirmation screen


def test_the_header_supplies_patient_dob_age_two_digit_date_and_fasting(tmp_path):
    _, _, info, _, _, _ = _parse(tmp_path)
    assert {key: info[key] for key in ("patient", "dob", "age", "sex", "collected", "fasting", "accession")} == {
        "patient": "SAMPLE, ALEX", "dob": "01/15/2005", "age": "21", "sex": "M", "collected": "08/07/2026",
        "fasting": "N", "accession": "SYN-0001"}
    occurrences, _, _, _, _, _ = _parse(tmp_path)
    assert {o["date_display"] for o in occurrences} == {"08/07/2026"}
    assert access_medical.full_date("08/07/26") == "08/07/2026" and access_medical.full_date("1/2/2026") == "01/02/2026"


def test_assays_that_differ_are_lab_reported_never_scored(tmp_path):
    occurrences, unrecognized, _, _, _, notes = _parse(tmp_path)
    scored = {o["name"] for o in occurrences}
    assert "Free Testosterone" not in scored and "Bioavailable Testosterone" not in scored
    shown = {u["raw_name"]: u["show_as"] for u in unrecognized if u.get("show_as") and u["raw_name"] != "Glucose"}
    assert shown == {"Testosterone, Free": ("Free Testosterone", "Hormones"),
                     "Bioavailable Testosterone": ("Bioavailable Testosterone", "Hormones")}
    assert sum(note.startswith("ASSAY DIFFERS FROM CELLDEEP RANGE BASIS") for note in notes) == 2
    assert clinic_config.LAB_ASSAY_DIFFERS["access_medical"] == ("Free Testosterone", "Bioavailable Testosterone")


def test_a_range_more_than_five_times_off_the_celldeep_basis_is_not_scored():
    shbg = resolve_marker_config("SHBG", MARKER_LIBRARY["SHBG"], "male")  # CellDeep 20-50
    assert access_medical.assay_differs("SHBG", shbg, "16.5 - 55.9") is None
    assert "by more than 5x" in access_medical.assay_differs("SHBG", shbg, "200 - 600")
    assert "by more than 5x" in access_medical.assay_differs("SHBG", shbg, "1 - 8")


def test_a_printed_unit_other_than_the_celldeep_unit_is_not_scored(tmp_path):
    pages = am.pages()
    page = am.Page(5).title("GENERAL CHEMISTRY").row("Vitamin D", "75", "75 - 250", "nmol/L")
    occurrences, unrecognized, _, _, _, notes = _parse(tmp_path, pages + [page.items])
    assert "Vitamin D" not in {o["name"] for o in occurrences}
    assert any(u.get("show_as") == ("Vitamin D", "Chemistry") for u in unrecognized)
    assert any("Vitamin D '75'" in note and "printed in nmol/L, not the ng/mL" in note for note in notes)


def test_scored_rows_printed_without_units_keep_no_unit(tmp_path):
    occurrences, _, _, _, _, notes = _parse(tmp_path)
    units = {o["name"]: o["printed_unit"] for o in occurrences}
    assert units["Testosterone, Total"] == "" and units["SHBG"] == "" and units["Ferritin"] == "ng/mL"
    assert ("UNITS NOT PRINTED: the lab printed no units for Testosterone, Total, Sex Hormone Bind Globulin, "
            "Testosterone, Free, Bioavailable Testosterone, DHEA-Sulfate, Cortisol; the report shows these results "
            "without units (none are filled in)") in notes


def test_word_results_are_lab_reported_not_scored(tmp_path):
    occurrences, unrecognized, _, _, _, _ = _parse(tmp_path)
    assert not any(o["disp_value"] in ("Negative", "Normal", "Yellow", "Clear", "None seen") for o in occurrences)
    assert "Urinalysis — Occult Blood" not in {o["name"] for o in occurrences}
    items, _, _ = pipeline.lab_reported.build(unrecognized)
    names = {item["name"] for item in items}
    for name in ("Urinalysis — Blood", "Urinalysis — Leukocyte Esterase", "Urinalysis — Urobilinogen",
                 "Urinalysis — Color", "Urinalysis — Bacteria", "Absolute Neutrophils", "Neutrophils %",
                 "Bilirubin, Total", "BUN/Creatinine Ratio", "Carbon Dioxide", "Protein, Total"):
        assert name in names, name


def test_aliases_route_the_listed_names():
    for printed, canonical in (("GFR estimated", "eGFR"), ("Sex Hormone Bind Globulin", "SHBG"),
                               ("DHEA-Sulfate", "DHEA-S")):
        assert pipeline._match_row_name(printed, "ENDOCRINE EVALUATION")[0] == canonical
    import lab_reported
    for printed, heading, canonical in (("Neutrophil #", None, "Absolute Neutrophils"),
                                        ("Basophil %", None, "Basophils %"), ("Bili", None, "Bilirubin, Total"),
                                        ("BUN/Creat Ratio", None, "BUN/Creatinine Ratio"),
                                        ("Leukocytes", "URINALYSIS GROSS EXAMINATION", "Urinalysis — Leukocyte Esterase"),
                                        ("Occult blood", "URINALYSIS GROSS EXAMINATION", "Urinalysis — Blood")):
        assert lab_reported.lookup(printed, heading)[0] == canonical, printed


# --- unreadable parts are excluded with a notice -----------------------------------------------------------

def test_a_page_without_the_column_header_is_excluded_not_guessed(tmp_path):
    pages = am.pages()
    pages[3] = [item for item in pages[3] if item[2] not in ("Test Name", "Results", "Reference Range", "Units")]
    occurrences, _, _, _, excluded, _ = _parse(tmp_path, pages)
    assert excluded == [{"page": 4, "section": "whole page", "reason": "Access Medical Laboratories page without the "
                         "'Test Name / Results / Reference Range / Units' header; nothing on it was read"}]
    assert "Estradiol" not in {o["name"] for o in occurrences}


def test_a_known_row_that_does_not_fit_the_pattern_is_excluded(tmp_path):
    pages = am.pages()
    page = am.Page(5).title("GENERAL CHEMISTRY").row("Ferritin", "120 ng", "38 - 380", "ng/mL")
    _, _, _, _, excluded, _ = _parse(tmp_path, pages + [page.items])
    assert excluded == [{"page": 5, "section": "GENERAL CHEMISTRY", "reason": "row 'Ferritin' not read: result "
                         "'120 ng' is not one printed value with an optional H/L flag"}]


def test_an_unrecognized_layout_is_still_excluded_with_a_notice(tmp_path):
    from synthetic_fixtures import deterministic_fixtures as fx
    path = fx.write_lab_pdf(tmp_path / "unknown.pdf", [[(40, 60, "Some Other Lab"), (40, 100, "Analyte Value"),
                                                       (40, 120, "Ferritin 88 ng/mL")]])
    extracted = pipeline.extract(str(path), [], None, patient_name="Alex S", audit_root=str(tmp_path))
    assert extracted["lab_layout"] is None and extracted["marker_occurrences"] == []
    assert extracted["parse_exclusions"] and extracted["preflight"]


# --- end to end: one draw, one DEXA scan, 21-year-old male -------------------------------------------------

@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return run_scenario("access_medical", tmp_path_factory.mktemp("access") / "run")


def test_report_builds_with_one_draw_and_one_scan(report):
    text = report["text"]
    for claim in ("improved since", "moved from", "down from", "up from", "When you came in"):
        assert claim not in text, claim
    assert re.search(r"Current scan · July 20, 2026 18\.1% body fat", text, re.I)
    assert "Testosterone, Total Optimal 600–900 — 612 August 7, 2026" in text  # no unit filled in
    assert "Bioavailable Testosterone 126 - 412 — 290" in text and "Free Testosterone 5.7 - 17.9 — 11.2" in text
    assert "DHEA-S Optimal lab range 138 - 475" in text  # missing male threshold: the lab's printed range
    assert "Cortisol, Total (AM) Not scored, no range printed" in text


def test_dexa_without_a_vat_page_has_no_visceral_line_and_a_notice(report):
    panel = report["text"].split("STRUCTURE · DEXA BODY COMPOSITION SCAN", 1)[1].split("Interim reference", 1)[0]
    assert "VAT" not in panel and "Visceral" not in panel and "—" not in panel
    assert "DEXA VAT/SAT NOT FOUND" in report["review"]


def test_dexa_pages_without_a_name_are_validated_by_age_and_date(report):
    staff_check = report["review"].split("(end of STAFF CHECK)")[0]
    assert "  DEXA scan dates accepted: 07/20/2026; ages printed on accepted pages: 21.5" in staff_check
    assert "DEXA EXCLUDED" not in staff_check
    assert "DEXA file 1 page 1, file 1 page 2 print no patient name" in report["review"]


def test_header_identity_and_checks(report):
    review = report["review"]
    staff_check = review.split("(end of STAFF CHECK)")[0]
    assert "  Lab layout: Access Medical Laboratories" in staff_check
    assert "  Bloodwork Collected date (entered): 08/07/2026; lab header prints Coll. Date 08/07/2026" in staff_check
    assert "  lab PDF text pages: SAMPLE, ALEX - matches" in review and "  Name mismatches: none" in staff_check
    assert "COLLECTED DATE CHECK" not in review and "AGE CHECK" not in review
    assert ("NON-FASTING GLUCOSE: the lab header prints 'Fasting: N'; Glucose '95' on 08/07/2026 (page 3) is shown as "
            "'Glucose (non-fasting)' with the lab's range (65 - 99) and flag, not scored against the fasting range") in review
    assert "NON-FASTING DRAW" not in review  # nothing is still labelled fasting
    assert "FOUND IN SOURCE BUT MISSING" not in review.replace("'Magnesium' FOUND", "")  # "mg" units: known item


def test_a_staff_date_that_differs_from_the_header_is_a_notice(tmp_path):
    result = run_scenario("access_medical_other_date", tmp_path / "other")
    assert ("COLLECTED DATE CHECK: staff entered 08/14/2026, the lab header prints Coll. Date 08/07/2026; the "
            "results use the printed date - confirm which draw this is") in result["review"]
    assert "August 7, 2026" in result["text"]


def test_age_checks_against_staff_and_dexa():
    info = {"age": "21", "sex": "M"}
    notes = pipeline.lab_header_checks(info, None, 23, "male", {"accepted": [((1, 1), "26.4")]}, [])
    assert "AGE CHECK: the lab header prints Age 21; staff entered 23" in notes
    assert any(note.startswith("AGE CHECK: the lab header prints Age 21; accepted DEXA pages print age(s) 26.4")
               for note in notes)
    assert pipeline.lab_header_checks(info, None, 21, "male", {"accepted": [((1, 1), "21.5")]}, []) == []
    assert any(note.startswith("SEX CHECK") for note in pipeline.lab_header_checks(info, None, None, "female", {}, []))


def test_nothing_staff_only_reaches_the_patient_report(report):
    for marker in ("ASSAY DIFFERS", "UNITS NOT PRINTED", "NON-FASTING", "OUT OF RANGE SUMMARY", "VAT/SAT NOT FOUND",
                   "STAFF CHECK", "INCOMPLETE"):
        assert marker not in report["text"], marker
    assert without_staff_check(report["review"])


# --- the limited male panel of the clinic's first run -------------------------------------------------------

def test_limited_panel_reads_every_row_once_and_the_summary_silently(tmp_path):
    occurrences, unrecognized, _, audit, excluded, notes = _parse(tmp_path, am.limited_pages())
    assert _rows(audit) == am.LIMITED_EXPECTED
    assert excluded == []  # nothing reaches the confirmation screen
    assert not any("SUMMARY" in note for note in notes)  # the summary agrees with the tables: no staff note
    items, remaining, _ = pipeline.lab_reported.build(unrecognized)
    assert remaining == []  # every name is recognized
    assert {"Free PSA", "% Free PSA", "White Blood Cell Count", "Red Blood Cell Count"} <= {i["name"] for i in items}


def test_a_summary_row_missing_from_the_tables_warns(tmp_path):
    summary = [*am.LIMITED_SUMMARY, ("Ferritin", "12 L", "38 - 380", "ng/mL")]
    *_, excluded, notes = _parse(tmp_path, am.limited_pages(summary=summary))
    assert notes.count("OUT OF RANGE SUMMARY: 'Ferritin 12 L 38 - 380 ng/mL' (page 1) matches no row in the result "
                       "tables; not used - check the PDF") == 1
    assert excluded == []


@pytest.mark.parametrize(("printed", "section", "canonical"), [
    ("White Blood Cell", None, "White Blood Cell Count"), ("Red Blood Cell", None, "Red Blood Cell Count"),
    ("PSA, Free", "TUMOR MARKERS", "Free PSA"), ("% Free PSA", "TUMOR MARKERS", "% Free PSA"),
    ("Bili", "URINALYSIS GROSS EXAMINATION", "Urinalysis — Bilirubin"), ("Bili", "GENERAL CHEMISTRY", "Bilirubin, Total"),
])
def test_lab_reported_aliases(printed, section, canonical):
    assert pipeline.lab_reported.lookup(printed, section)[0] == canonical


@pytest.mark.parametrize(("printed", "canonical"), [("Creatinine, Serum", "Creatinine"), ("Estradiol (E2)", "Estradiol")])
def test_scored_aliases(printed, canonical):
    assert pipeline._match_row_name(printed, "GENERAL CHEMISTRY")[0] == canonical


def test_bili_is_resolved_by_section_and_left_out_when_ambiguous(tmp_path):
    occurrences, unrecognized, _, _, excluded, _ = _parse(tmp_path, am.limited_pages())
    shown = {item["name"]: item["results"][0]["disp_value"]
             for item in pipeline.lab_reported.build(unrecognized)[0]}
    assert shown["Urinalysis — Bilirubin"] == "Negative" and shown["Bilirubin, Total"] == "0.7"
    _, unrecognized, _, audit, excluded, _ = _parse(tmp_path, am.limited_pages(bili_section="ENDOCRINE EVALUATION"))
    assert excluded == [{"page": 1, "section": "ENDOCRINE EVALUATION", "reason": "row 'Bili' means a different test "
                         "depending on its section (URINALYSIS, CHEMISTRY, LIVER, HEPATIC); under 'ENDOCRINE EVALUATION' "
                         "it cannot be told apart, so it was not read"}]
    assert "Bilirubin, Total" not in {item["name"] for item in pipeline.lab_reported.build(unrecognized)[0]}


def test_interpretation_text_after_a_result_is_not_a_row(tmp_path):
    _, unrecognized, _, audit, excluded, _ = _parse(tmp_path, am.limited_pages())
    assert sum(row["name"] == "% Free PSA" for row in audit) == 1
    values = {u["cells"][0]["disp_value"] for u in unrecognized}
    assert not values & {"56%", "28%", "0 - 10%", "Probability of", "60 to 89"}
    assert not any(row["name"].startswith(("Stage", "eGFR is", "Interpretation")) for row in audit)


@pytest.fixture(scope="module")
def limited_report(tmp_path_factory):
    return run_scenario("access_medical_limited", tmp_path_factory.mktemp("limited") / "run")


def test_limited_report_safeguards(limited_report):
    text, review = limited_report["text"], limited_report["review"]
    assert limited_report["confirmation"] == []
    # Free/Bioavailable testosterone: lab-reported with the lab's own range, never against a CellDeep range.
    assert "Free Testosterone 5.7 - 17.9 — 9.8" in text and "Bioavailable Testosterone 126 - 412 — 240" in text
    assert "100–180" not in text and "250–500" not in text
    # No printed unit, no unit shown.
    assert "600–900 — 540 August 7, 2026" in text and "measured 540 on 08/07/2026" in text  # "540", no unit
    # No morning window printed on this panel: no "(AM)"; the collection time is shown.
    assert "Cortisol, Total Not scored, no range printed no range printed — 12.0 August 7, 2026 collected 07:45" in text
    # Glucose drawn non-fasting: staff note only.
    assert "NON-FASTING GLUCOSE: the lab header prints 'Fasting: N'; Glucose '101' on 08/07/2026 (page 1)" in review
    assert "NON-FASTING" not in text
    # One draw and one scan: no trend or change claims anywhere.
    for claim in ("improved", "moved from", "down from", "up from", "increased", "decreased", "since your first",
                  "When you came in", " to 0.", "change since"):
        assert claim not in text, claim
    # DEXA with no VAT line: none shown, none invented, a staff notice.
    assert "lb VAT" not in text and "VAT" not in text and "Visceral fat" not in text  # (the disclaimer names
    assert "DEXA VAT/SAT NOT FOUND" in review                                        # "visceral-fat ranges" only)
    assert "OUT OF RANGE SUMMARY" not in review
