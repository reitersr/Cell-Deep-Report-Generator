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
                   ("Ferritin", "9 L", "38 - 380", "ng/mL")]
    *_, notes = _parse(tmp_path, am.pages(summary=disagreeing))
    assert ("OUT OF RANGE SUMMARY DISAGREES: Estradiol is 50 H in the summary (page 1) but 52 H in the result "
            "table (page 4); the table value is used - check the PDF") in notes
    assert ("OUT OF RANGE SUMMARY DISAGREES: Ferritin is 9 L in the summary (page 1) but 120 in the result table "
            "(page 3); the table value is used - check the PDF") in notes


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
    shown = {u["raw_name"]: u["show_as"] for u in unrecognized if u.get("show_as")}
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
    assert ("NON-FASTING DRAW: the lab header prints 'Fasting: N'; Glucose (fasting) was drawn non-fasting but the "
            "report still labels and scores it as fasting") in review
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
