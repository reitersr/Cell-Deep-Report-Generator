"""A synthetic lab page in a made-up layout the table reader does not know ("Analyte | Value | Ref. Interval"), for
the AI fallback tests (test_vision_fallback.py). Every name, value, date and ID is invented."""

from synthetic_fixtures import deterministic_fixtures as fx

COLLECTED = "04/14/2026"
ROWS = [("TSH", "1.9", "0.40-4.50"), ("Ferritin", "22 L", "30-400"), ("hs-CRP", "0.6", "0.0-3.0")]


def page(collected=COLLECTED, rows=ROWS):
    items = [(40, 40, "Specimen: SYN-U-1"), (40, 54, "Patient: Synthetic, Pat"), (40, 68, f"Collected: {collected}"),
             (40, 100, "Analyte"), (220, 100, "Value"), (320, 100, "Ref. Interval")]
    for index, (name, value, interval) in enumerate(rows):
        y = 124 + 16 * index
        items += [(40, y, name), (220, y, value), (320, y, interval)]
    return items


def write(path, pages=None):
    return fx.write_lab_pdf(path, pages if pages is not None else [page()])


def read(rows, collected="01/01/2020", page_number=1):
    """One scripted vision read of the page: rows are (name, result_text, flag, reference_range). The model's own
    Collected date is deliberately wrong: the fallback must take the date from the page's printed header."""
    return {"page": page_number, "specimen_id": None, "collected": collected, "footer": None,
            "patient_name": "Synthetic, Pat", "date_of_birth": None, "illegible": False, "out_of_range_summary": None,
            "rows": [{"name": name, "result_text": value, "flag": flag, "reference_range": reference,
                      "lab_code": None, "column": "in_range", "page": page_number, "illegible": False,
                      "section": "ROUTINE PANELS"} for name, value, flag, reference in rows]}
