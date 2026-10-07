"""Synthetic pages that reproduce the table layouts of a multi-draw Cleveland HeartLab-style report, by word
position only (test_column_roles.py). Every name, value, date and ID is invented.

    risk_table_page   "Current | Reference Range/Relative Risk Categories | Historical" group labels, the "Test Name"
                      line, a risk-tier line ("Optimal Moderate High Units") and a sub-label line ("Optimal
                      Non-Optimal" under Current, two dates or "/ /" slots under the one Historical label)
    panel_table_page  "Test Name Current Result Reference Range Units Lab Historical Results" on one line, then
                      "In Range Out of Range" with the Historical dates or slots
    trend_page        the lab's progress summary: dates printed on the "Test Name" line itself
"""

from synthetic_fixtures import deterministic_fixtures as fx

SECOND, FIRST = "11/04/2025", "07/08/2025"


def _preamble(order_id, collected, fasting="Fasting"):
    return [(34, 40, f"Order ID: {order_id}"), (34, 52, f"Collected: {collected}, 7:40 AM"),
            (34, 64, f"Fasting: {fasting}")]


def risk_header(y, slots):
    """slots: two Historical slot labels, each a date or "/ /"."""
    items = [(198, y, "Current"), (291, y, "Reference Range/Relative Risk Categories"), (512, y, "Historical"),
             (28, y + 11, "Test Name"), (170, y + 11, "Result & Relative Risk"), (488, y + 11, "Result & Relative Risk"),
             (280, y + 18, "Optimal"), (328, y + 18, "Moderate"), (388, y + 18, "High"), (438, y + 18, "Units"),
             (170, y + 26, "Optimal"), (214, y + 26, "Non-Optimal")]
    for x, slot in zip((485, 536), slots):
        items.append((x, y + 26, slot))
    return items


def risk_row(y, name, optimal=None, non_optimal=None, tiers=("", "", ""), units="", hist=(None, None)):
    items = [(37, y, name)]
    if optimal:
        items.append((180, y, optimal))
    if non_optimal:
        items.append((228, y, non_optimal))
    items += [(x, y, text) for x, text in zip((285, 329, 387), tiers) if text]
    if units:
        items.append((437, y, units))
    items += [(x, y, text) for x, text in zip((495, 546), hist) if text]
    return items


def risk_table_page(collected, slots, rows, order_id="SYN-R-1"):
    items = _preamble(order_id, collected) + risk_header(170, slots) + [(34, 210, "INFLAMMATION")]
    y = 230
    for row in rows:
        items += risk_row(y, *row) if isinstance(row, tuple) else row(y)
        y += 24
    return items


def panel_header(y, slots):
    items = [(34, y, "Test Name"), (185, y, "Current Result"), (288, y, "Reference Range"), (387, y, "Units"),
             (444, y, "Lab"), (496, y, "Historical Results"),
             (171, y + 9, "In Range"), (214, y + 9, "Out of Range")]
    for x, slot in zip((485, 536), slots):
        items.append((x, y + 9, slot))
    return items


def panel_row(y, name, current, lab_range="", units="", lab="SYN", hist=(None, None), out_of_range=False):
    items = [(37, y, name), (228 if out_of_range else 182, y, current)]
    if lab_range:
        items.append((303, y, lab_range))
    if units:
        items.append((387, y, units))
    items.append((444, y, lab))
    items += [(x, y, text) for x, text in zip((497, 548), hist) if text]
    return items


def panel_table_page(collected, slots, rows, order_id="SYN-P-1", heading="Comprehensive Metabolic Panel"):
    items = _preamble(order_id, collected) + panel_header(170, slots) + [(38, 196, heading)]
    y = 214
    for row in rows:
        items += panel_row(y, *row[:4], **row[4] if len(row) > 4 else {})
        y += 16
    return items


def trend_page(collected, title=True, order_id="SYN-R-1"):
    items = _preamble(order_id, collected)
    if title:
        items.append((180, 120, "Cardiometabolic Patient Progress Summary"))
    items += [(34, 160, "Test Name"), (226, 160, "Current"), (278, 160, SECOND), (334, 160, FIRST),
              (408, 160, "/ /"), (557, 160, "Units"),
              (37, 180, "hs-CRP"), (233, 180, "9.9"), (289, 180, "8.8"), (346, 180, "7.7"), (556, 180, "mg/L")]
    return items


def write(path, pages):
    return fx.write_lab_pdf(path, pages)
