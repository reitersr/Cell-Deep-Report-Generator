"""The column-role model: every table column gets a role from its printed header, by x-position only (test name,
current result and its sub-columns, one Historical column per printed date or "/ /" slot, reference range, risk
thresholds, units, lab code, flag, comments). Only Current and Historical columns hold results. Synthetic pages that
reproduce a real multi-draw layout (synthetic_fixtures/column_roles.py); invented values and dates."""

from pathlib import Path

import fitz
import pytest

import pipeline
from synthetic_fixtures import column_roles as cr

LATEST = "03/10/2026"
REAL = Path(__file__).parent / "real_fixtures" / "extensive_male_lab.pdf"


def _parse(tmp_path, pages, name="labs.pdf"):
    exclusions, notes, info = [], [], {}
    with fitz.open(cr.write(tmp_path / name, pages)) as document:
        occurrences, unknown = pipeline._parse_bloodwork_tables(list(document), review_notes=notes,
                                                                exclusions=exclusions, draw_info=info)
    return occurrences, unknown, exclusions, notes, info


def _found(occurrences):
    return {(o["name"], o["date_display"]): (o["disp_value"], o["lab_flag"]) for o in occurrences
            if o["value"] is not None or o["disp_value"]}


# --- risk-category tables: thresholds are never results; one Historical column per printed date ----------------

RISK_ROWS = [
    ("Lp-PLA2 Activity", "118", None, ("<=123", "N/A", ">123"), "U/L", ("429.6H", "60L")),
    ("hs-CRP", None, "3.4", ("<1.0", "1.0-3.0", ">3.0"), "mg/L", ("2.2", "<0.3")),
    ("Myeloperoxidase", "208", None, ("<470", "470-539", ">539"), "pmol/L", ("256", "171")),
    ("VLDL Size", None, "47.6", ("<47.1", "47.1-49.0", ">49.0"), "nm", ("48.2", "55.6")),
]


def test_threshold_columns_are_never_results_and_each_date_is_its_own_column(tmp_path):
    occurrences, unknown, exclusions, _, _ = _parse(tmp_path, [cr.risk_table_page(LATEST, (cr.SECOND, cr.FIRST),
                                                                                  RISK_ROWS)])
    assert exclusions == []
    assert _found(occurrences) == {
        ("Lp-PLA2 Activity", LATEST): ("118", ""), ("Lp-PLA2 Activity", cr.SECOND): ("429.6", "H"),
        ("Lp-PLA2 Activity", cr.FIRST): ("60", "L"),
        ("hs-CRP", LATEST): ("3.4", ""), ("hs-CRP", cr.SECOND): ("2.2", ""), ("hs-CRP", cr.FIRST): ("<0.3", ""),
        ("Myeloperoxidase", LATEST): ("208", ""), ("Myeloperoxidase", cr.SECOND): ("256", ""),
        ("Myeloperoxidase", cr.FIRST): ("171", "")}
    # The unaliased row is lab-reported with the lab's printed risk categories as its range, never a threshold value.
    vldl = next(item for item in unknown if item["raw_name"] == "VLDL Size")
    assert [(c["date_display"], c["disp_value"]) for c in vldl["cells"]] == [
        (LATEST, "47.6"), (cr.SECOND, "48.2"), (cr.FIRST, "55.6")]
    assert vldl["raw_range"] == "Optimal <47.1; Moderate 47.1-49.0; High >49.0" and vldl["raw_unit"] == "nm"
    threshold_values = {t for row in RISK_ROWS for t in row[3]}
    assert not threshold_values & {o["disp_value"] for o in occurrences}


def test_empty_historical_slots_hold_nothing_and_raise_nothing(tmp_path):
    rows = [(name, optimal, non_optimal, tiers, units) for name, optimal, non_optimal, tiers, units, _ in RISK_ROWS]
    occurrences, _, exclusions, _, _ = _parse(tmp_path, [cr.risk_table_page(LATEST, ("/ /", "/ /"), rows)])
    assert exclusions == []
    assert {date for _, date in _found(occurrences)} == {LATEST}


def test_test_name_and_column_labels_are_never_tests(tmp_path):
    occurrences, unknown, _, _, _ = _parse(tmp_path, [cr.risk_table_page(LATEST, (cr.SECOND, cr.FIRST), RISK_ROWS)])
    names = {o["name"] for o in occurrences} | {u["raw_name"] for u in unknown}
    assert not any(word in name for name in names for word in ("Test Name", "Optimal", "Result", "Units"))


def test_a_header_date_under_no_historical_column_leaves_out_only_that_table(tmp_path):
    bad = cr.risk_table_page(LATEST, (cr.SECOND, cr.FIRST), RISK_ROWS[:1])
    bad.append((420, 196, "01/02/2025"))  # a date printed under the Units label: roles ambiguous
    good = cr.panel_table_page(LATEST, ("/ /", "/ /"), [("Sodium", "140", "136-145", "mmol/L"),
                                                         ("Glucose", "81", "65-99", "mg/dL")])
    occurrences, unknown, exclusions, _, _ = _parse(tmp_path, [bad, good])
    assert [(e["page"], e["reason"]) for e in exclusions] == [
        (1, "table not read: the header date 01/02/2025 sits under no Historical column; the table's columns cannot "
            "be told apart")]
    assert "Lp-PLA2 Activity" not in {o["name"] for o in occurrences}
    assert [c["disp_value"] for u in unknown if u["raw_name"] == "Sodium" for c in u["cells"] if c["present"]] == ["140"]


# --- single-line panel headers with "In Range / Out of Range" and Historical dates below ------------------------

def test_panel_table_reads_both_current_sub_columns_and_each_historical_date(tmp_path):
    rows = [("Glucose", "81", "65-99", "mg/dL", {"hist": ("60L", "88")}),
            ("Sodium", "135 L", "136-145", "mmol/L", {"out_of_range": True, "hist": ("140", None)}),
            ("Bacteria", "None Seen", "None seen", "/HPF", {"hist": ("0-2", None)})]
    occurrences, unknown, exclusions, _, _ = _parse(tmp_path, [cr.panel_table_page(LATEST, (cr.SECOND, cr.FIRST),
                                                                                   rows)])
    assert exclusions == []
    assert _found(occurrences) == {("Glucose (fasting)", LATEST): ("81", ""),
                                   ("Glucose (fasting)", cr.SECOND): ("60", "L"),
                                   ("Glucose (fasting)", cr.FIRST): ("88", "")}
    cells = {u["raw_name"]: [(c["date_display"], c["disp_value"], c["lab_flag"]) for c in u["cells"] if c["present"]]
             for u in unknown}
    assert cells["Sodium"] == [(LATEST, "135", "L"), (cr.SECOND, "140", None)]
    assert cells["Bacteria"] == [(LATEST, "None Seen", None), (cr.SECOND, "0-2", None)]


# --- a standalone report wins; a blank Historical cell is not a mismatch --------------------------------------

def test_a_blank_historical_cell_never_contradicts_the_full_report(tmp_path):
    older = cr.panel_table_page(cr.SECOND, ("/ /", "/ /"), [("Glucose", "81", "65-99", "mg/dL")], order_id="SYN-P-0")
    newer = cr.panel_table_page(LATEST, (cr.SECOND, "/ /"), [("Glucose", "90", "65-99", "mg/dL"),
                                                             ("Sodium", "140", "136-145", "mmol/L", {"hist": ("139",
                                                                                                             None)})])
    occurrences, _, exclusions, notes, _ = _parse(tmp_path, [older, newer])
    assert exclusions == [] and not any("HISTORICAL VALUE DIFFERS" in note for note in notes)
    assert _found(occurrences)[("Glucose (fasting)", cr.SECOND)] == ("81", "")


def test_one_result_printed_in_two_tables_keeps_the_printed_range_and_flag(tmp_path):
    risk = cr.risk_table_page(LATEST, ("/ /", "/ /"), [("Glucose", None, "60", ("65-99", "100-125", ">125"), "mg/dL")])
    panel = cr.panel_table_page(LATEST, ("/ /", "/ /"), [("Glucose", "60L", "65-99", "mg/dL")], order_id="SYN-R-1")
    occurrences, _, exclusions, _, _ = _parse(tmp_path, [risk, panel])
    glucose = [o for o in occurrences if o["name"] == "Glucose (fasting)"]
    assert exclusions == [] and len(glucose) == 1
    assert (glucose[0]["disp_value"], glucose[0]["lab_flag"], glucose[0]["lab_range_display"]) == ("60", "L", "65-99")


# --- trend pages restate other reports: not read, listed for staff ---------------------------------------------

def test_trend_pages_and_their_continuations_are_not_read_and_are_listed(tmp_path):
    full = cr.risk_table_page(LATEST, (cr.SECOND, cr.FIRST), RISK_ROWS[1:2])
    occurrences, _, exclusions, notes, _ = _parse(tmp_path, [full, cr.trend_page(LATEST),
                                                             cr.trend_page(LATEST, title=False)])
    assert exclusions == []
    assert _found(occurrences)[("hs-CRP", LATEST)] == ("3.4", "")  # never the trend page's 9.9
    assert ("TREND PAGES NOT READ: lab PDF page(s) 2, 3 hold the lab's progress/trend summary, which restates results "
            "printed in each draw's own report; those reports are read instead") in notes


def test_a_dates_on_the_header_line_table_without_a_trend_title_is_read_by_column(tmp_path):
    occurrences, _, exclusions, _, _ = _parse(tmp_path, [cr.trend_page(LATEST, title=False)])
    assert exclusions == []
    assert _found(occurrences) == {("hs-CRP", LATEST): ("9.9", ""), ("hs-CRP", cr.SECOND): ("8.8", ""),
                                   ("hs-CRP", cr.FIRST): ("7.7", "")}


# --- per-draw header fields ------------------------------------------------------------------------------------

def test_fasting_fasting_and_collection_times_after_a_comma(tmp_path):
    *_, info = _parse(tmp_path, [cr.risk_table_page(LATEST, ("/ /", "/ /"), RISK_ROWS[:1])])
    assert info["fasting"] == {LATEST: "Y"} and info["times"] == {LATEST: "7:40 AM"}
    assert pipeline._normalize_fasting({"NON-FASTING"}) == "N"
    assert pipeline._is_placeholder_time("00:01 AM") and not pipeline._is_placeholder_time("07:40 AM")


# --- ruled "Test Name | Result | Comments" tables, Symbol-font glyphs ------------------------------------------

def test_a_ruled_result_and_comments_table_reads_text_results(tmp_path):
    page = [(34, 40, "Order ID: SYN-G-1"), (34, 52, f"Collected: {LATEST}"),
            (34, 120, "GENETIC CARDIOVASCULAR MARKERS"),
            (37, 134, "Test Name"), (226, 134, "Result"), (352, 134, "Comments (See Guidance Statements)"),
            (59, 153, "Genotype"), (231, 153, "3/3"), (278, 153, "Most common genotype. See Guidance Statements."),
            (37, 162, "ApoE")]
    scored = cr.panel_table_page(LATEST, ("/ /", "/ /"), [("Glucose", "81", "65-99", "mg/dL")], order_id="SYN-G-1")
    path = cr.write(tmp_path / "genetic.pdf", [page, scored])
    with fitz.open(path) as document:
        for y in (146, 170):  # row rules start left of the "Test Name" label, as printed
            document[0].draw_line((28, y), (207, y))
            document[0].draw_line((213, y), (264, y))
        document.saveIncr()
    exclusions = []
    with fitz.open(path) as document:
        _, unknown = pipeline._parse_bloodwork_tables(list(document), exclusions=exclusions)
    assert exclusions == []
    assert [(u["raw_name"], [c["disp_value"] for c in u["cells"]]) for u in unknown] == [("Genotype ApoE", ["3/3"])]


def test_symbol_font_glyphs_are_decoded_only_in_that_font():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((300, 100), "!115", fontsize=9, fontname="symb")
    page.insert_text((300, 120), "!115", fontsize=9, fontname="helv")
    words = sorted(pipeline._page_words(page), key=lambda w: w[1])
    assert [w[4] for w in words] == ["≥115", "!115"]


# --- the real de-identified file (local only: real_fixtures/ is git-ignored) -----------------------------------

@pytest.mark.skipif(not REAL.is_file(), reason="Local real multi-draw fixture is absent")
def test_real_multi_draw_report_reads_every_result_table(tmp_path):
    extracted = pipeline.extract(str(REAL), [], None, patient_name=None, audit_root=str(tmp_path), sex="male")
    assert extracted["parse_exclusions"] == [] and extracted["blocked"] is None
    printed = extracted["draw_info"]["collected_dates"]
    accepted = {o["date_display"] for o in extracted["marker_occurrences"] if o["value"] is not None or o["disp_value"]}
    assert len(printed) == 3 and set(printed) <= accepted
    names = {o["name"] for o in extracted["marker_occurrences"]} | {u["raw_name"] for u in
                                                                    extracted["unrecognized_markers"]}
    assert not any("test name" in name.lower() for name in names)
    assert any(note.startswith("TREND PAGES NOT READ") for note in extracted["other_notes"])
