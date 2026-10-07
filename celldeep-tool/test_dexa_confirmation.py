"""DEXA measurements printed in several places (composition history, segmental "Total" row, abdomen composition,
VAT/SAT trend): a value is accepted when both reads of a page agree, or when an independent printed location
confirms it; one table's read never blanks a value another location confirms. Pages with a text layer are checked
against it. Invented values and dates only."""

import copy
from pathlib import Path

import fitz

import pipeline
import scoring
from synthetic_fixtures import dexa as fx

SCAN = fx.scan("06/02/2025", "190.4", "52.1", "131.9", None, "2.40", "120.0")  # body fat not printed on any page
LATER = fx.scan("10/06/2025", "184.0", "45.5", "132.0", "25.7 %", "2.10", "104.0")


def _run(tmp_path, labels, pages, name="dexa.pdf", writer=None):
    path = (writer or fx.write_pdf)(tmp_path / name, labels)
    reader = fx.DexaReader(path, labels, pages)
    notes = []
    history, notes, summary = pipeline._extract_dexa_with_claude(reader, [str(path)], fx.PATIENT)
    return history, notes, summary


def _same(*scans, **kwargs):
    return (fx.read(list(scans), **kwargs), fx.read(copy.deepcopy(list(scans)), **kwargs))


def _by_date(history):
    return {h["date_display"]: scoring.normalize_dexa_body_fat(dict(h)) for h in history}


def test_a_date_listed_in_two_tables_of_one_page_is_merged_not_dropped(tmp_path):
    """A page printing the composition history and the VAT trend lists each date twice; the whole-body values are
    kept from the composition row and VAT from the trend row (before: the whole scan was dropped on that page)."""
    composition = fx.scan(SCAN["date"], SCAN["total_mass"], SCAN["fat_mass"], SCAN["lean_mass"])
    trend = fx.scan(SCAN["date"], vat=SCAN["vat_mass"], area=SCAN["vat_area"])
    history, notes, _ = _run(tmp_path, ["history-and-trend"], {"history-and-trend": _same(composition, trend)})
    scan = _by_date(history)["06/02/2025"]
    assert (scan["total_mass_lb"], scan["fat_mass_lb"], scan["lean_mass_lb"]) == (190.4, 52.1, 131.9)
    assert (scan["vat_fat_mass_lb"], scan["visceral_fat_area_cm2"]) == (2.4, 120.0)
    # Never "—" when fat and lean are available: 52.1 / (52.1 + 131.9) = 28.3%, labelled computed.
    assert scan["body_fat_pct"] == "28.3%" and scan["computed"] == ["body_fat_pct"]
    assert not any("whole scan excluded" in note for note in notes)


def test_two_tables_on_one_page_printing_different_values_leave_out_only_that_field(tmp_path):
    composition = fx.scan(SCAN["date"], SCAN["total_mass"], SCAN["fat_mass"], SCAN["lean_mass"])
    regional = fx.scan(SCAN["date"], fat="9.8", vat=SCAN["vat_mass"])  # an abdomen table's own fat mass
    pages = {"page": _same(composition, regional)}
    history, notes, _ = _run(tmp_path, ["page"], pages)
    scan = _by_date(history)["06/02/2025"]
    assert scan["fat_mass_lb"] is None and scan["total_mass_lb"] == 190.4 and scan["vat_fat_mass_lb"] == 2.4
    assert "DEXA file 1 page 1: scan 06/02/2025 fat mass (lb) excluded - printed with different values in two tables " \
           "of this page" in notes


def test_a_value_confirmed_on_two_pages_wins_over_one_page_that_disagrees(tmp_path):
    whole = fx.scan(SCAN["date"], SCAN["total_mass"], SCAN["fat_mass"], SCAN["lean_mass"])
    # 53.1 also balances (bone 5.4 lb), so the mass check cannot decide: the page count does.
    regional = fx.scan(SCAN["date"], fat="53.1", vat=SCAN["vat_mass"])
    pages = {"summary": _same(whole), "segmental": _same(whole), "abdomen": _same(regional)}
    history, notes, _ = _run(tmp_path, ["summary", "segmental", "abdomen"], pages)
    assert _by_date(history)["06/02/2025"]["fat_mass_lb"] == 52.1
    assert any("print different values; 52.1 is confirmed on 2 pages and is used" in note for note in notes)


def test_one_page_against_one_page_is_still_withheld(tmp_path):
    whole = fx.scan(SCAN["date"], SCAN["total_mass"], SCAN["fat_mass"], SCAN["lean_mass"])
    other = fx.scan(SCAN["date"], SCAN["total_mass"], "53.1", SCAN["lean_mass"])
    history, notes, _ = _run(tmp_path, ["a", "b"], {"a": _same(whole), "b": _same(other)})
    assert _by_date(history)["06/02/2025"]["fat_mass_lb"] is None  # no independent confirmation: never guessed
    assert any("print different values" in note and "excluded" in note for note in notes)


def test_reads_that_disagree_on_one_page_are_confirmed_by_another_page(tmp_path):
    whole = fx.scan(LATER["date"], LATER["total_mass"], LATER["fat_mass"], LATER["lean_mass"], LATER["body_fat_pct"])
    misread = dict(whole, fat_mass="46.5")
    misread_other = dict(whole, fat_mass="48.5")
    pages = {"a": (fx.read([whole]), fx.read([misread])), "b": (fx.read([whole]), fx.read([misread_other]))}
    history, notes, _ = _run(tmp_path, ["a", "b"], pages)
    assert _by_date(history)["10/06/2025"]["fat_mass_lb"] == 45.5
    assert any("no page's two reads agreed, but 2 independent pages read this value" in note for note in notes)


def _text_pdf(rows):
    def writer(path, labels):
        document = fitz.open()
        for label in labels:
            page = document.new_page()
            page.insert_text((40, 60), f"Synthetic DEXA report page: {label}", fontsize=12)
            page.insert_text((40, 90), "Date Total (lb) Fat (lb) Lean (lb) %Fat", fontsize=10)
            for index, row in enumerate(rows):
                page.insert_text((40, 110 + 14 * index), "  ".join(row), fontsize=10)
        document.save(path)
        document.close()
        return path
    return writer


def test_a_page_with_a_text_layer_decides_between_disagreeing_reads(tmp_path):
    writer = _text_pdf([(LATER["date"], "184.0", "45.5", "132.0", "25.7"),
                        ("04/07/2025", "193.2", "55.0", "131.4", "29.5")])
    whole = fx.scan(LATER["date"], LATER["total_mass"], LATER["fat_mass"], LATER["lean_mass"], LATER["body_fat_pct"])
    misread = dict(whole, fat_mass="46.5")
    history, notes, _ = _run(tmp_path, ["printed"], {"printed": (fx.read([whole]), fx.read([misread]))},
                             writer=writer)
    assert _by_date(history)["10/06/2025"]["fat_mass_lb"] == 45.5
    assert any("the reads differed; the page's text layer prints this value" in note for note in notes)


def test_a_text_layer_never_overrules_reads_that_agree(tmp_path):
    """DEXA text layers are often OCR: noisy, partial and out of order. Agreeing reads stand even when the OCR text
    misses the number; the text only breaks a tie between reads that differ."""
    writer = _text_pdf([(LATER["date"], "184.0", "45.S", "132.0", "25.7"),  # OCR misread "45.5" as "45.S"
                        ("04/07/2025", "193.2", "55.0", "131.4", "29.5")])
    history, _, _ = _run(tmp_path, ["printed"], {"printed": _same(LATER)}, writer=writer)
    assert _by_date(history)["10/06/2025"]["fat_mass_lb"] == 45.5


def test_a_tie_the_text_layer_cannot_break_stays_empty(tmp_path):
    writer = _text_pdf([(LATER["date"], "184.0", "45.S", "132.0", "25.7"),
                        ("04/07/2025", "193.2", "55.0", "131.4", "29.5")])
    misread = dict(LATER, fat_mass="46.5")
    history, _, _ = _run(tmp_path, ["printed"], {"printed": (fx.read([LATER]), fx.read([misread]))}, writer=writer)
    scan = _by_date(history)["10/06/2025"]
    assert scan["fat_mass_lb"] is None and scan["body_fat_pct"] == "25.7%"


def test_printed_body_fat_wins_and_estimated_is_kept(tmp_path):
    estimated = dict(LATER, body_fat_pct="25.7 (e)")
    history, _, _ = _run(tmp_path, ["summary"], {"summary": _same(estimated)})
    scan = _by_date(history)["10/06/2025"]
    assert scan["body_fat_pct"] == "25.7%" and "body_fat_pct" in scan["estimated"] and not scan.get("computed")


def test_the_prompt_keeps_whole_body_fields_out_of_regional_tables():
    import scan_dexa
    assert "whole-body values only" in scan_dexa.DEXA_PROMPT and "never from a regional table" in scan_dexa.DEXA_PROMPT
    assert '"Android Fat" column is\nnot fat_mass' in scan_dexa.DEXA_PROMPT and "SAT rows have every measurement null" \
        in scan_dexa.DEXA_PROMPT
    assert Path(scan_dexa.__file__).read_text().count("_merge_same_date") >= 2


def test_pages_tied_on_fat_mass_are_decided_by_the_scans_other_printed_masses(tmp_path):
    """Two pages print the whole-body fat mass, two others print a regional table's fat (an android column misread as
    fat mass): a 2-vs-2 tie. Total and lean are settled, so only one candidate leaves a bone-mineral remainder in range
    (190.4 - 131.9 - 52.1 = 6.4 lb); the other (190.4 - 131.9 - 8.2 = 50.3 lb) is impossible."""
    whole = fx.scan(SCAN["date"], SCAN["total_mass"], SCAN["fat_mass"], SCAN["lean_mass"])
    regional = fx.scan(SCAN["date"], SCAN["total_mass"], "8.2", SCAN["lean_mass"])
    labels = ["summary", "segmental", "abdomen", "android"]
    pages = {"summary": _same(whole), "segmental": _same(whole), "abdomen": _same(regional),
             "android": _same(regional)}
    history, notes, _ = _run(tmp_path, labels, pages)
    scan = _by_date(history)["06/02/2025"]
    assert scan["fat_mass_lb"] == 52.1 and scan["body_fat_pct"] == "28.3%" and scan["computed"] == ["body_fat_pct"]
    assert any("52.1 is the one the scan's other printed masses confirm (total = fat + lean + bone mineral); 8.2 not "
               "used" in note for note in notes)


def test_mass_balance_never_picks_when_both_candidates_fit_or_the_others_are_unsettled(tmp_path):
    whole = fx.scan(SCAN["date"], SCAN["total_mass"], SCAN["fat_mass"], SCAN["lean_mass"])
    close = fx.scan(SCAN["date"], SCAN["total_mass"], "53.1", SCAN["lean_mass"])  # both leave 5.4 / 6.4 lb: no pick
    history, _, _ = _run(tmp_path, ["a", "b"], {"a": _same(whole), "b": _same(close)})
    assert _by_date(history)["06/02/2025"]["fat_mass_lb"] is None
    unsettled = fx.scan(SCAN["date"], "199.0", "8.2", SCAN["lean_mass"])  # total also disputed: nothing to check against
    history, _, _ = _run(tmp_path, ["a", "b"], {"a": _same(whole), "b": _same(unsettled)}, name="second.pdf")
    scan = _by_date(history)["06/02/2025"]
    assert scan["fat_mass_lb"] is None and scan["total_mass_lb"] is None
