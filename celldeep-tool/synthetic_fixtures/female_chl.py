"""A synthetic stand-in for the clinic's first female patient type (scenario "female_chl_scanned_undated"): a
Cleveland HeartLab-style lab PDF whose first pages are screenshots of an earlier draw (image-only, no name, no
Collected date, no fasting status, no collection time) followed by the later dated digital report, whose Historical
column carries the earlier draw's results; and a DEXA PDF with four scans, two of them taken more than 60 days after
the latest bloodwork. Every name, value, date and ID is invented.

Lab PDF
  1-2  image-only pages of the earlier draw (scripted reads below): results that match the later report's Historical
       column exactly, a test printed only on these pages (bioavailable testosterone) and the cycle-phase hormones
  3    the dated digital report: "Gender: Female", "Fasting: Fasting", collected 08:40 AM, cortisol with the lab's
       printed "AM (6-10 AM)" window, Estradiol/FSH with printed cycle-phase ranges, DHEA-S and SHBG with the lab's
       range, and a Historical column dated EARLIER
DEXA PDF (names redacted)
  summary  the four scans' whole-body composition and the patient's age; %Fat printed for the first two only
  vat      the VAT trend (lb only; area printed in in², never converted)
"""

import copy

import fitz

from synthetic_fixtures import deterministic_fixtures as fx
from synthetic_fixtures import dexa as dexa_fx

PATIENT = "Synthetic, Pat"
AGE = 38
LATEST, EARLIER = "05/12/2026", "06/10/2025"
LATEST_TIME = "08:40 AM"
X = fx.LAB_X

# (printed name, latest value, earlier value, units, lab range): the earlier value is printed in the Historical
# column of the dated report and, identically, on the scanned pages.
SHARED = [
    ("hs-CRP", "0.9", "2.4", "mg/L", "0.0-3.0"), ("Cholesterol, Total", "188", "194", "mg/dL", "<200"),
    ("HDL Cholesterol", "61", "58", "mg/dL", ">=50"), ("Triglycerides", "72", "64", "mg/dL", "<150"),
    ("LDL Cholesterol", "92", "97", "mg/dL", "<100"), ("HbA1c", "5.2", "5.3", "%", "<5.7"),
    ("TSH", "1.84", "2.06", "mIU/L", "0.40-4.50"), ("Ferritin", "41", "29", "ng/mL", "9-150"),
    ("Creatinine", "0.86", "0.91", "mg/dL", "0.50-0.97"), ("Sodium", "139", "141", "mmol/L", "136-145"),
    ("Potassium", "4.3", "4.6", "mmol/L", "3.5-5.1"), ("Calcium, Total", "9.4", "9.1", "mg/dL", "8.5-10.5"),
]
PHASE_TEXT = {"Estradiol": "Female (adults): Follicular phase: 21 - 251 pg/mL; Postmenopausal: <6 - 54 pg/mL.",
              "FSH": "Female (Adults): Follicular phase: 2.9-12.1 mIU/mL; Postmenopausal: 23.1-128.0 mIU/mL."}


def _line(y, text):
    return [(X["name"], y, text)]


def digital_page():
    items = [(X["name"], 30, "Order ID: SYN-F-200"), (X["name"], 42, "Patient: Synthetic, Pat"),
             (X["name"], 54, f"Collected: {LATEST}, {LATEST_TIME}"), (X["name"], 66, "Fasting: Fasting"),
             (X["name"], 78, "Gender: Female")]
    items += fx.header_line(100, (EARLIER,))
    y = 128
    for name, latest, earlier, units, lab_range in SHARED:
        items += fx.row(y, name, latest, earlier, units=units, lab_range=lab_range)
        y += 13
    items += fx.row(y, "Glucose", "84", "88", units="mg/dL", lab_range="65-99")
    y += 13
    items += fx.row(y, "Cortisol, Total", "12.4", "15.0", units="ug/dL")
    y += 11
    items += _line(y, "Reference range: AM (6-10 AM) 4.8-19.5 ug/dL; PM (4-8 PM) 2.5-11.9 ug/dL.")
    y += 13
    items += fx.row(y, "DHEA-S", "188.2", "201.7", units="ug/dL", lab_range="57.3-279.2")
    y += 13
    items += fx.row(y, "Estradiol", "142", "77", units="pg/mL")
    y += 11
    items += _line(y, PHASE_TEXT["Estradiol"])
    y += 13
    items += fx.row(y, "FSH", "6.3", "7.1", units="mIU/mL")
    y += 11
    items += _line(y, PHASE_TEXT["FSH"])
    y += 13
    items += fx.row(y, "SHBG", "66.0", "71.0", units="nmol/L", lab_range="24.6-122.0")
    return items


def _image_page(document, text):
    source = fitz.open()
    page = source.new_page()
    page.insert_text((40, 60), text, fontsize=10)
    document.new_page().insert_image(page.rect, pixmap=page.get_pixmap(dpi=60))
    source.close()


def write_labs(path):
    """Two undated screenshots of the earlier draw, then the dated digital report."""
    document = fitz.open()
    for number in (1, 2):
        _image_page(document, f"Synthetic undated screenshot page {number} of 2")
    digital = fitz.open(fx.write_lab_pdf(path.with_name(path.stem + "_digital.pdf"), [digital_page()]))
    document.insert_pdf(digital)
    digital.close()
    document.save(path)
    document.close()
    return path


def _row(name, value, section, reference=None, flag=None, column="in_range"):
    return {"name": name, "result_text": value, "flag": flag, "reference_range": reference, "lab_code": None,
            "column": column, "page": 0, "illegible": False, "section": section}


def _page(rows):
    # No Collected date, no name, no fasting status, no time: only an order ID and "Page n of 2" in the footer.
    return {"page": 0, "specimen_id": None, "collected": None, "footer": "ORDER ID: SYN-F-100 Page n of 2",
            "patient_name": None, "date_of_birth": None, "illegible": False, "rows": rows,
            "out_of_range_summary": None}


def scan_reads(shared=SHARED):
    """{page number: one literal read} of the two undated pages (the same for every read)."""
    one = [_row(name, earlier, "ROUTINE PANELS", lab_range) for name, _, earlier, _, lab_range in shared[:8]]
    two = [_row(name, earlier, "ROUTINE PANELS", lab_range) for name, _, earlier, _, lab_range in shared[8:]]
    two += [_row("Glucose", "88", "ROUTINE PANELS", "65-99"),
            _row("Cortisol, Total", "15.0", "HORMONES"), _row("DHEA-S", "201.7", "HORMONES", "57.3-279.2"),
            _row("Estradiol", "77", "HORMONES"), _row("FSH", "7.1", "HORMONES"),
            _row("SHBG", "71.0", "HORMONES", "17-124"),
            _row("TESTOSTERONE, BIOAVAILABLE", "2.4", "HORMONES", "0.5-8.5")]
    return {1: _page(one), 2: _page(two)}


# DEXA: date, total, fat, lean, printed %Fat (None = not printed), VAT lb
SCANS = [("06/12/2025", "166.2", "52.8", "106.9", "31.8", "0.92"),
         ("04/20/2026", "151.4", "35.1", "110.8", "23.2", "0.51"),   # 22 days before the latest bloodwork: current
         ("07/25/2026", "148.0", "32.2", "110.3", None, "0.47"),     # 74 days after: excluded
         ("08/30/2026", "146.9", "30.4", "111.0", None, "0.44")]     # 110 days after: excluded
DEXA_LABELS = ["summary", "vat"]


def _same(scans, age=None):
    return [dexa_fx.read(scans, patient_name=None, age=age),
            dexa_fx.read(copy.deepcopy(scans), patient_name=None, age=age)]


def dexa_pages():
    whole = [dexa_fx.scan(d, total, fat, lean, pct) for d, total, fat, lean, pct, _ in SCANS]
    vat = [dexa_fx.scan(d, vat=v) for d, _, _, _, _, v in SCANS]
    return {"summary": _same(whole, age="38.4"), "vat": _same(vat)}
