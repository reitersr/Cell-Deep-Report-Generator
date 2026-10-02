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


DEXA_COLUMNS_BMD = {"label": 60, "bmd": 230, "t": 320, "z": 420}
DEXA_COLUMNS_COMP = {"label": 60, "pct": 170, "total": 240, "fat": 330, "lean": 410, "bmc": 490}
DEXA_COLUMNS_VAT = {"label": 60, "mass": 200, "volume": 290, "area": 390}


def _dexa_text_page(document, lines, annotations=()):
    page = document.new_page()
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=10, fontname="helv")
    for kind, payload in annotations:
        if kind == "circle":
            page.draw_circle(payload[0], payload[1], color=(0.1, 0.1, 0.6), width=1.2)
        elif kind == "note":
            x, y, text = payload
            page.insert_text((x, y), text, fontsize=12, fontname="tiro", color=(0.1, 0.1, 0.6))
    return page


def write_image_only_pdf(path, text_pages):
    """Render text pages, then keep only their pixels - no extractable text layer, like the scanned
    Lunar Prodigy reports."""
    source = fitz.open()
    for lines, annotations in text_pages:
        _dexa_text_page(source, lines, annotations)
    output = fitz.open()
    for page in source:
        pixmap = page.get_pixmap(dpi=300)
        image_page = output.new_page(width=page.rect.width, height=page.rect.height)
        image_page.insert_image(image_page.rect, pixmap=pixmap)
    output.save(path)
    output.close()
    source.close()
    return path


def bmd_page(printed_name=PATIENT, forearm_rows=None):
    c = DEXA_COLUMNS_BMD
    lines = [(60, 50, f"Patient: {printed_name}     Birth Date: 01/01/1980"),
             (60, 90, "AP Spine L1-L4"),
             (c["label"], 115, "Region"), (c["bmd"], 115, "BMD"), (c["t"], 115, "YA T-Score"),
             (c["z"], 115, "AM Z-Score")]
    y = 140
    for label, bmd, t, z in (("L1", "1.101", "-0.4", "0.1"), ("L2", "1.152", "-0.2", "0.3"),
                             ("L3", "1.198", "0.1", "0.6"), ("L4", "1.204", "0.2", "0.7"),
                             ("L1-L4", "1.166", "-0.1", "0.4")):
        lines += [(c["label"], y, label), (c["bmd"], y, bmd), (c["t"], y, t), (c["z"], y, z)]
        y += 22
    lines += [(60, y + 20, "Left Forearm"),
              (c["label"], y + 45, "Region"), (c["bmd"], y + 45, "BMD"), (c["t"], y + 45, "YA T-Score"),
              (c["z"], y + 45, "AM Z-Score")]
    y += 70
    for label, bmd, t, z in forearm_rows or (("Radius UD", "0.456", "-1.1", "-0.8"),
                                             ("Radius 33%", "0.789", "", ""),
                                             ("Both Total", "0.612", "-0.9", "-0.6")):
        lines += [(c["label"], y, label), (c["bmd"], y, bmd)]
        if t:
            lines.append((c["t"], y, t))
        if z:
            lines.append((c["z"], y, z))
        y += 22
    return lines, ()


def composition_page(printed_name=PATIENT, history=None, vat=None, annotations=True):
    c, v = DEXA_COLUMNS_COMP, DEXA_COLUMNS_VAT
    lines = [(60, 50, f"Patient: {printed_name}     Birth Date: 01/01/1980"),
             (60, 90, "Segmental Analysis"),
             (c["label"], 115, "Region"), (c["pct"], 115, "%Fat"), (c["total"], 115, "Total Mass"),
             (c["fat"], 115, "Fat Mass"), (c["lean"], 115, "Lean Mass"), (c["bmc"], 115, "BMC")]
    y = 140
    for values in (("Arms Total", "30.1", "22.5", "6.8", "14.9", "0.9"),
                   ("Legs Total", "32.4", "61.0", "19.8", "38.6", "2.6"),
                   ("Trunk", "36.0", "78.0", "28.1", "48.2", "1.7"),
                   ("Total", "34.4", "170.5", "56.5", "107.6", "6.4")):
        for key, text in zip(("label", "pct", "total", "fat", "lean", "bmc"), values):
            lines.append((c[key], y, text))
        y += 22
    y += 20
    lines += [(60, y, "Body Composition History"),
              (c["label"], y + 25, "Date"), (c["pct"], y + 25, "%Fat"), (c["total"], y + 25, "Total Mass"),
              (c["fat"], y + 25, "Fat Mass"), (c["lean"], y + 25, "Lean Mass")]
    y += 50
    history_rows = history or (("05/21/2025", "34.4", "170.5", "56.5", "107.6"),
                               ("01/27/2026", "30.0", "165.0", "49.5", "109.0"))
    note_y = y
    for values in history_rows:
        for key, text in zip(("label", "pct", "total", "fat", "lean"), values):
            lines.append((c[key], y, text))
        y += 22
    y += 20
    lines += [(60, y, "Estimated Visceral Adipose Tissue"),
              (v["label"], y + 25, "Date"), (v["mass"], y + 25, "Mass (lb)"),
              (v["volume"], y + 25, "Volume (in3)"), (v["area"], y + 25, "Area (cm2)")]
    y += 50
    for values in vat or (("05/21/2025", "1.01", "33.5", "82.4"),
                          ("01/27/2026", "0.85", "28.2", "70.1"),
                          ("03/10/2026", "0.80", "26.6", "66.0")):
        for key, text in zip(("label", "mass", "volume", "area"), values):
            lines.append((v[key], y, text))
        y += 22
    marks = []
    if annotations:
        # Handwritten-style provider marks outside the printed cells: a margin note and a circled value.
        marks = [("note", (520, note_y, "recheck 42")),
                 ("circle", ((c["pct"] + 10, note_y - 3), 14))]
    return lines, marks
