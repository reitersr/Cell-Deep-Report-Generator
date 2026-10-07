"""A synthetic stand-in for the clinic's verified extensive-male run (Cleveland HeartLab + Quest chemistry, three
draws in one lab PDF, three DEXA scans), for the regression check in CI (scenario "chl_extensive"). It reproduces the
layout's structure only; every name, value, date and ID is invented:

Lab PDF
  1 a risk-category table: Current (Optimal / Non-Optimal sub-columns), a threshold column (Optimal / Moderate / High,
    never results), Units, two Historical date columns, an attached flag ("52.3H"), a censored value
  2 the same table on the second draw's report with "/ /" Historical slots
  3 a Quest chemistry panel: Test Name | Current Result (In Range / Out of Range) | Reference Range | Units | Lab |
    Historical Results
  4 the newest draw's report (synthetic_fixtures/chl_multi_draw.py): repeated column-header lines, "Fasting: Unknown",
    a placeholder 00:01 time, an unreadable Historical cell, Free Testosterone on a changed assay (35-155)
  5 the second draw's report: "Fasting: Y", Free Testosterone on the CellDeep basis assay (46-224)
  6 a lab progress/trend page (restates other reports; not read)

DEXA PDF (names redacted, as in the clinic's files)
  summary page   three scans' whole-body composition and the patient's age (pages are validated by age); the first prints %Fat, the second none, the newest "(e)"
  history page   the same three scans again, no %Fat on the second
  abdomen page   the three dates with an Android Fat column read as fat mass (a regional table): the scan's own
                 masses (total = fat + lean + bone mineral) decide, never the regional value
  vat page       the VAT trend: VAT mass per scan
"""

import copy

from synthetic_fixtures import chl_multi_draw as chl
from synthetic_fixtures import column_roles as cr
from synthetic_fixtures import deterministic_fixtures as fx
from synthetic_fixtures import dexa as dexa_fx

LATEST, SECOND, FIRST = chl.LATEST, chl.SECOND, chl.FIRST
PATIENT = "Synthetic, Pat"
AGE = 52

RISK_ROWS = [  # Lp-PLA2 with attached-flag Historical cells is on the newest draw's report (chl_multi_draw.latest_page)
    ("Myeloperoxidase", "233", None, ("<470", "470-539", ">539"), "pmol/L", ("281", "164")),
    ("Oxidized LDL", None, "61", ("<60", "60-69", ">69"), "U/L", ("58", "<10")),
    ("VLDL Size", None, "46.9", ("<47.1", "47.1-49.0", ">49.0"), "nm", ("48.6", "52.3H")),
    ("Chol/HDL-C", "3.3", None, ("<3.5", "3.5-5.0", ">5.0"), "ratio", ("2.7", "4.1")),
]
SECOND_RISK_ROWS = [("Myeloperoxidase", "281", None, ("<470", "470-539", ">539"), "pmol/L"),
                    ("Chol/HDL-C", "2.7", None, ("<3.5", "3.5-5.0", ">5.0"), "ratio")]
PANEL_ROWS = [("Sodium", "140", "135-146", "mmol/L", {"hist": ("138", None)}),
              ("Glucose", "95", "65-99", "mg/dL", {"hist": ("88", None)}),
              ("ALT", "52", "9-46", "U/L", {"out_of_range": True, "hist": ("41", None)}),
              ("Creatinine", "1.12", "0.70-1.33", "mg/dL", {"hist": ("1.05", None)})]


def _risk_page(preamble, slots, rows):
    items = preamble + cr.risk_header(170, slots) + [(34, 210, "INFLAMMATION")]
    for index, row in enumerate(rows):
        items += cr.risk_row(230 + 24 * index, *row)
    return items


def _panel_page(preamble, slots, rows):
    items = preamble + cr.panel_header(170, slots) + [(38, 196, "Comprehensive Metabolic Panel")]
    for index, row in enumerate(rows):
        items += cr.panel_row(214 + 16 * index, *row[:4], **row[4])
    return items


def lab_pages():
    latest = chl._preamble("SYN-M-300", LATEST, "00:01", "Unknown")
    second = chl._preamble("SYN-M-200", SECOND, "07:30", "Y")
    return [_risk_page(latest, (SECOND, FIRST), RISK_ROWS),
            _risk_page(second, ("/ /", "/ /"), SECOND_RISK_ROWS),
            _panel_page(latest, (SECOND, "/ /"), PANEL_ROWS),
            chl.latest_page(),
            chl.second_page(),
            chl._preamble("SYN-M-300", LATEST, "00:01", "Unknown") + cr.trend_page(LATEST)[3:]]


def write_labs(path):
    return fx.write_lab_pdf(path, lab_pages())


# DEXA: date, total, fat, lean, printed %Fat (None = not printed), VAT lb, Android fat lb
SCANS = [("04/14/2025", "205.6", "68.2", "130.1", "33.2", "4.12", "7.4"),
         ("10/20/2025", "190.2", "51.7", "131.0", None, "2.95", "4.9"),  # computed: 51.7 / 182.7 = 28.3%
         ("03/02/2026", "182.4", "44.6", "130.4", "24.5 (e)", "2.61", "4.1")]
DEXA_LABELS = ["summary", "history", "abdomen", "vat"]


def _same(scans, age=None):
    return [dexa_fx.read(scans, patient_name=None, age=age),
            dexa_fx.read(copy.deepcopy(scans), patient_name=None, age=age)]


def dexa_pages():
    whole = [dexa_fx.scan(d, total, fat, lean, pct) for d, total, fat, lean, pct, _, _ in SCANS]
    history = [dexa_fx.scan(d, total, fat, lean, pct if pct and "(e)" not in pct else None)
               for d, total, fat, lean, pct, _, _ in SCANS]
    abdomen = [dexa_fx.scan(d, total, android) for d, total, _, _, _, _, android in SCANS]
    vat = [dexa_fx.scan(d, vat=v) for d, _, _, _, _, v, _ in SCANS]
    return {"summary": _same(whole, age="52.3"), "history": _same(history), "abdomen": _same(abdomen), "vat": _same(vat)}

