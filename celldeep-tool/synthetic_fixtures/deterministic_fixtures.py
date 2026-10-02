"""Synthetic, invented-value PDF builders for the deterministic parser tests. No real or real-derived
patient data: every name, value, date, and Order ID here is made up to reproduce a layout pattern."""

import fitz

PATIENT = "Pat Synthetic"
LAB_X = {"name": 40, "current": 220, "hist1": 300, "hist2": 380, "units": 460, "range": 510}
FONT_SIZE = 9


def write_lab_pdf(path, pages):
    """pages: list of lists of (x, y, text) placed exactly where a lab's text layer would put them."""
    document = fitz.open()
    for items in pages:
        page = document.new_page()
        for x, y, text in items:
            page.insert_text((x, y), text, fontsize=FONT_SIZE, fontname="helv")
    document.save(path)
    document.close()
    return path


def section_preamble(order_id, collected, y=40):
    return [(LAB_X["name"], y, f"Order ID: {order_id}"),
            (LAB_X["name"], y + 14, "Patient: Synthetic, Pat"),
            (LAB_X["name"], y + 28, f"Collected: {collected}")]


def header_line(y, historical_dates=(), current_label="Current", dates_below=True):
    """A Current/Historical header with zero, one, or several Historical columns."""
    items = [(LAB_X["name"], y, "Test Name")]
    if current_label:
        items.append((LAB_X["current"], y, current_label))
    for key, date in zip(("hist1", "hist2"), historical_dates):
        if dates_below:
            items.append((LAB_X[key], y, "Historical"))
            items.append((LAB_X[key], y + 11, date))
        else:
            items.append((LAB_X[key], y, f"Historical {date}"))
    items += [(LAB_X["units"], y, "Units"), (LAB_X["range"], y, "Range")]
    return items


def row(y, name, current=None, hist1=None, hist2=None, units="", lab_range=""):
    items = [(LAB_X["name"], y, name)]
    for key, value in (("current", current), ("hist1", hist1), ("hist2", hist2), ("units", units),
                       ("range", lab_range)):
        if value:
            items.append((LAB_X[key], y, value))
    return items
