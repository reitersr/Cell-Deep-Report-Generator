"""Synthetic lab PDFs, one per bloodwork layout seen in production, built from invented data.

Every name, value, date, range and ID is made up. PDFs are generated into a temporary directory at
test time and never committed. Scanned pages are image-only; their two vision reads are mocked
with the literal transcriptions in SCAN_PAGES (as if a vision model had read them).

    quest_digital  Quest-style digital report: In Range / Out of Range columns, printed H/L flags,
                   Reference Range and Lab-code columns, a URINALYSIS band
    chl_digital    Cleveland HeartLab-style digital report: Current plus dated Historical columns
    labcorp_digital Labcorp-style digital report: Current Result, a separate Flag column (High/Low),
                   Units and Reference Interval
    scanned        a fully scanned report (image-only pages)
    mixed          digital pages followed by scanned pages of a later draw
"""

import copy
import json
from types import SimpleNamespace

import fitz

from synthetic_fixtures import deterministic_fixtures as fx
from synthetic_fixtures.sdk_contract import check_create_kwargs

PATIENT = "Synthetic, Pat"
SCAN_DATE = "04/14/2026"

QUEST_X = {"name": 40, "in": 230, "out": 300, "range": 380, "lab": 520}


def _quest_page(collected, rows, order_id):
    items = [(40, 40, f"Order ID: {order_id}"), (40, 54, f"Patient: {PATIENT}"),
             (40, 68, f"Collected: {collected}"),
             (QUEST_X["name"], 100, "Test Name"), (QUEST_X["in"], 100, "In Range"),
             (QUEST_X["out"], 100, "Out Of Range"), (QUEST_X["range"], 100, "Reference Range"),
             (QUEST_X["lab"], 100, "Lab")]
    y = 128
    for row in rows:
        if isinstance(row, str):  # a name-only band: section heading
            items.append((QUEST_X["name"], y, row))
        else:
            name, in_range, out_range, reference = row
            items.append((QUEST_X["name"], y, name))
            if in_range:
                items.append((QUEST_X["in"], y, in_range))
            if out_range:
                items.append((QUEST_X["out"], y, out_range))
            if reference:
                items.append((QUEST_X["range"], y, reference))
            items.append((QUEST_X["lab"], y, "SYN"))
        y += 14
    return items


QUEST_ROWS = [
    ("TSH", "1.90", None, "0.40-4.50"),
    ("FERRITIN", None, "20 L", "38-380"),
    ("GLUCOSE", "82", None, "65-99"),
    ("VITAMIN D,25-OH,TOTAL,IA", "48", None, "30-100"),
    ("HEMOGLOBIN", None, "17.6 H", "13.2-17.1"),
    ("PLATELET COUNT", "250", None, "140-400"),
    ("SODIUM", "140", None, "135-146"),
    ("ALT", None, "61 H", "9-46"),
    ("C-REACTIVE PROTEIN", "2.1", None, "<8.0"),
    ("SYNTHETIC ASSAY X", None, "9.9 H", "1.0-5.0"),
    "URINALYSIS",
    ("COLOR", "YELLOW", None, "YELLOW"),
    ("GLUCOSE", "NEGATIVE", None, "NEGATIVE"),
]


def quest_digital(path):
    return fx.write_lab_pdf(path, [_quest_page("03/02/2026", QUEST_ROWS, "SYN-Q-100")])


LABCORP_X = {"name": 40, "result": 230, "flag": 300, "units": 360, "range": 440}
LABCORP_ROWS = [
    ("Ferritin", "20", "Low", "ng/mL", "30-400"),
    ("Vitamin B12", "655", "", "pg/mL", "232-1245"),
    ("Hemoglobin", "17.6", "High", "g/dL", "13.0-17.0"),
    ("Hematocrit", "52.3", "High", "%", "37.5-51.0"),
    ("Platelets", "250", "", "x10E3/uL", "150-450"),
    ("Potassium", "5.6", "High", "mmol/L", "3.5-5.2"),
    ("Hemoglobin A1c", "5.4", "", "%", "4.8-5.6"),
    ("Synthetic Assay Y", "0.2", "Low", "U/L", "0.5-2.0"),
]


def labcorp_digital(path):
    items = [(40, 40, "Specimen ID: SYN-L-300"), (40, 54, f"Patient: {PATIENT}"),
             (40, 68, "Date Collected: 03/02/2026"),
             (LABCORP_X["name"], 100, "Test"), (LABCORP_X["result"], 100, "Current Result"),
             (LABCORP_X["flag"], 100, "Flag"), (LABCORP_X["units"], 100, "Units"),
             (LABCORP_X["range"], 100, "Reference Interval")]
    for index, (name, result, flag, units, reference) in enumerate(LABCORP_ROWS):
        y = 128 + index * 14
        items += [(LABCORP_X["name"], y, name), (LABCORP_X["result"], y, result),
                  (LABCORP_X["units"], y, units), (LABCORP_X["range"], y, reference)]
        if flag:
            items.append((LABCORP_X["flag"], y, flag))
    return fx.write_lab_pdf(path, [items])


def chl_digital(path):
    page = (fx.section_preamble("SYN-C-200", "03/02/2026") + fx.header_line(100, ("11/20/2025",))
            + fx.row(130, "hs-CRP", "1.4H", "2.2", units="mg/L", lab_range="0.0-3.0")
            + fx.row(144, "LDL Cholesterol", "96", "121", units="mg/dL")
            + fx.row(158, "Apolipoprotein B", "71", "88", units="mg/dL")
            + fx.row(172, "HbA1c", "5.3", "5.5", units="%")
            + fx.row(186, "Fasting Insulin", "6.1", "8.4", units="uIU/mL")
            + fx.row(200, "Lp-PLA2 Activity", "118", "131", units="nmol/min/mL")
            + fx.row(214, "Testosterone, Total", "702", "655", units="ng/dL")
            + fx.row(228, "LH", "4.1", "3.9", units="mIU/mL", lab_range="1.5-9.3")
            + fx.row(242, "Homocysteine", "9.8", "11.2", units="umol/L"))
    return fx.write_lab_pdf(path, [page])


def _scan_row(page, name, result, flag, reference, column, section="ROUTINE PANELS", lab_code=None):
    return {"name": name, "result_text": result, "flag": flag, "reference_range": reference,
            "lab_code": lab_code, "column": column, "page": page, "illegible": False, "section": section}


def _scan_pages(first_page):
    one, two = first_page, first_page + 1
    rows_one = [
        _scan_row(one, "TSH", "2.10", None, "0.40-4.50", "in_range"),
        _scan_row(one, "TESTOSTERONE, TOTAL, MS", "812", None, "250-1100 ng/dL", "in_range", lab_code="SYN"),
        _scan_row(one, "TESTOSTERONE, FREE", "190.0", "H", "35.0-155.0 pg/mL", "out_of_range"),
        _scan_row(one, "FOLATE, SERUM", "8.2", None, "> OR = 5.4 ng/mL", "in_range"),
        _scan_row(one, "INSULIN", "4.4", None, "<OR=18.4 uIU/mL", "in_range"),
        _scan_row(one, "WHITE BLOOD CELL COUNT", "5.1", None, "3.8-10.8", "in_range"),
        _scan_row(one, "ABSOLUTE LYMPHOCYTES", "790", "L", "850-3900", "out_of_range"),
    ]
    rows_two = [
        _scan_row(two, "HEMOGLOBIN A1c", "5.2", None, "<5.7", "in_range"),
        _scan_row(two, "ESTRADIOL", "31", None, "<=39", "in_range"),
        _scan_row(two, "GLUCOSE", "NEGATIVE", None, "NEGATIVE", "in_range", section="URINALYSIS"),
        _scan_row(two, "KETONES", "1+", None, "NEGATIVE", "out_of_range", section="URINALYSIS"),
    ]
    summary = [{"name": "TESTOSTERONE, FREE", "result_text": "190.0 H", "flag": None, "illegible": False},
               {"name": "ABSOLUTE LYMPHOCYTES", "result_text": "790", "flag": "L", "illegible": False}]
    base = {"specimen_id": None, "collected": None, "footer": None, "patient_name": PATIENT, "date_of_birth": None,
            "illegible": False}
    return [{**base, "page": one, "rows": rows_one, "out_of_range_summary": None},
            {**base, "page": two, "rows": rows_two, "out_of_range_summary": summary}]


def _image_page(document, text):
    source = fitz.open()
    page = source.new_page()
    page.insert_text((40, 60), text, fontsize=10)
    document.new_page().insert_image(page.rect, pixmap=page.get_pixmap(dpi=60))
    source.close()


def scanned(path):
    document = fitz.open()
    for number in (1, 2):
        _image_page(document, f"Synthetic scanned lab page {number}")
    document.save(path)
    document.close()
    return path


def mixed(path):
    digital = fitz.open(chl_digital(path.with_name(path.stem + "_digital.pdf")))
    for number in (2, 3):
        _image_page(digital, f"Synthetic scanned lab page {number}")
    digital.save(path)
    digital.close()
    return path


class ScanReader:
    """Mocked Anthropic client: two independent, identical literal reads per scanned page."""

    def __init__(self, pages):
        self.responses = iter([copy.deepcopy(page) for page in pages for _ in (1, 2)])
        self.messages = self
        self.timeout = 240.0

    def create(self, **kwargs):
        check_create_kwargs(kwargs)  # the installed SDK must accept this call
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(next(self.responses)))],
                               stop_reason="end_turn")


FIXTURES = {
    "quest_digital": (quest_digital, None),
    "chl_digital": (chl_digital, None),
    "labcorp_digital": (labcorp_digital, None),
    "scanned": (scanned, lambda: ScanReader(_scan_pages(1))),
    "mixed": (mixed, lambda: ScanReader(_scan_pages(2))),
}
