"""A synthetic Cleveland HeartLab-style lab PDF holding three draws: two full reports (one page each) and an
earliest draw that appears only in their Historical columns. Every name, value, date, ID and range is invented to
reproduce a layout pattern from the clinic's first multi-draw run:

- repeated column-header lines inside the table ("Optimal Moderate High Units Optimal Non-Optimal / / / /",
  "In Range Out of Range / / / /");
- Historical cells printed with an attached flag ("429.6H", "60L"), censored ("<0.3"), a count range ("0-5") or
  words ("None seen");
- one unreadable Historical cell (digits run together) on the newest draw's page;
- "Fasting: Y" on one draw and "Fasting: Unknown" on the newest;
- a placeholder collection time (00:01) on the newest draw and a printed morning window for cortisol;
- Free Testosterone on the CellDeep basis assay (46-224) in one draw and the dialysis assay (35-155) in the newest;
- testosterone-panel ALBUMIN / GLOB under lab code AMD beside chemistry ALBUMIN under Z4M;
- tests with no CellDeep alias (LDL Size, Apolipoprotein A1) printed with the lab's unit and range.
"""

from synthetic_fixtures import deterministic_fixtures as fx

FIRST, SECOND, LATEST = "07/08/2025", "11/04/2025", "03/10/2026"
X = {**fx.LAB_X, "lab": 560}


def _preamble(order_id, collected, time, fasting, extra=()):
    lines = [f"Order ID: {order_id}", "Patient: Synthetic, Pat", f"Collected: {collected} {time}",
             *(["Fasting: " + fasting] if fasting is not None else []), *extra]
    return [(X["name"], 30 + 12 * index, text) for index, text in enumerate(lines)]


def _header(y, dates):
    return fx.header_line(y, dates) + [(X["lab"], y, "Lab")]


def _row(y, name, current=None, hist1=None, hist2=None, units="", lab_range="", lab=None):
    return fx.row(y, name, current, hist1, hist2, units=units, lab_range=lab_range) + (
        [(X["lab"], y, lab)] if lab else [])


def _column_header_row(y, text):
    """A repeated column-header line printed across the table: labels and empty date slots."""
    words = text.split()
    return [(X["current"] + 26 * index, y, word) for index, word in enumerate(words)]


def latest_page(free_t_range="35-155"):
    y = iter(range(150, 800, 14))
    rows = [
        *_column_header_row(next(y), "Optimal Moderate High Units Optimal Non-Optimal / / / /"),
        *_row(next(y), "Lp-PLA2 Activity", "118", "429.6H", "60L", units="nmol/min/mL"),
        *_row(next(y), "TMAO", "3.1", "<0.3", "4.0", units="uM"),
        *_row(next(y), "hs-CRP", "0.9", "2.3", "1.1", units="mg/L", lab_range="0.0-3.0"),
        *_row(next(y), "Apolipoprotein B", "71", "12.34.5", "90", units="mg/dL"),  # unreadable Historical cell
        *_row(next(y), "Glucose", "95", "88", units="mg/dL", lab_range="65-99"),
        *_row(next(y), "Insulin", "6.1", "5.0", units="uIU/mL", lab_range="2.0-19.6"),
        *_row(next(y), "Cortisol, AM", "14.0", "13.2", units="ug/dL"),
        *_row(next(y), "Free Testosterone", "120", "95", units="pg/mL", lab_range=free_t_range),
        *_row(next(y), "LDL Size", "21.2", units="nm", lab_range="20.5-23.0"),
        *_row(next(y), "Apolipoprotein A1", "150", units="mg/dL", lab_range="94-176"),
        *_column_header_row(next(y), "In Range Out of Range / / / /"),
        (X["name"], next(y), "CHEMISTRY"),
        *_row(next(y), "ALBUMIN", "4.6", units="g/dL", lab_range="3.6-5.1", lab="Z4M"),
        (X["name"], next(y), "TESTOSTERONE, FREE AND TOTAL"),
        *_row(next(y), "SEX HORMONE BINDING GLOB", "45", units="nmol/L", lab_range="10-50", lab="AMD"),
        *_row(next(y), "ALBUMIN", "4.4", units="g/dL", lab_range="3.6-5.1", lab="AMD"),
        *_row(next(y), "GLOB", "2.5", units="g/dL", lab_range="1.9-3.7", lab="AMD"),
        (X["name"], next(y), "URINALYSIS"),
        *_row(next(y), "Occult Blood", "Negative", "Negative", lab_range="Negative"),
        *_row(next(y), "WBC", "None seen", "0-5", units="/HPF", lab_range="0-5"),
        *_row(next(y), "RBC", "0-2", "None seen", units="/HPF", lab_range="0-2"),
    ]
    return (_preamble("SYN-M-300", LATEST, "00:01", "Unknown", ["Morning am 6-10: 6.02 - 18.4 ug/dl"])
            + _header(120, (SECOND, FIRST)) + rows)


def second_page(free_t_range="46-224"):
    y = iter(range(150, 800, 14))
    rows = [
        *_column_header_row(next(y), "In Range Out of Range / / / /"),
        *_row(next(y), "Lp-PLA2 Activity", "429.6H", "60L", units="nmol/min/mL"),
        *_row(next(y), "TMAO", "<0.3", "4.0", units="uM"),
        *_row(next(y), "hs-CRP", "2.2", "1.1", units="mg/L", lab_range="0.0-3.0"),  # the later page prints 2.3
        *_row(next(y), "Glucose", "88", units="mg/dL", lab_range="65-99"),
        *_row(next(y), "Insulin", "5.0", units="uIU/mL", lab_range="2.0-19.6"),
        *_row(next(y), "Cortisol, AM", "13.2", units="ug/dL"),
        *_row(next(y), "Free Testosterone", "95", units="pg/mL", lab_range=free_t_range),
    ]
    return _preamble("SYN-M-200", SECOND, "07:30", "Y") + _header(120, (FIRST,)) + rows


def write(path, pages=None):
    return fx.write_lab_pdf(path, pages if pages is not None else [second_page(), latest_page()])
