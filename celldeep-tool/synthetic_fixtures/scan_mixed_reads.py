"""A synthetic scanned page whose reads miss rows the way live reads did: the long testosterone panel on a page of
dense prose comes back in one read and is simply absent from the others ("not read / 1214 H / not read"). A missing
read is not a disagreeing value: the page is read again (targeted at the missing rows, at most
scan_bloodwork.SCAN_TARGETED_MAX_READS reads in all) and a row is kept once two reads print it identically. Every name,
value and date is invented.

reads(pattern): one read per entry; True = the read prints the two testosterone rows, False = it leaves them out.
"""

import fitz

from synthetic_fixtures import layouts

PATIENT = layouts.PATIENT
COLLECTED = "02/11/2026"
TOTAL_T = ("TESTOSTERONE, TOTAL, MS", "1214", "H", "250-1100 ng/dL")
FREE_T = ("TESTOSTERONE, FREE", "204.0", "H", "35.0-155.0 pg/mL")
PANEL = "TESTOSTERONE, FREE (DIALYSIS) AND TOTAL,MS"


def _row(name, result, flag, reference, column, section=None):
    return {"name": name, "result_text": result, "flag": flag, "reference_range": reference, "lab_code": None,
            "column": column, "page": 0, "illegible": False, "section": section}


def _read(with_testosterone):
    rows = [_row("PSA, TOTAL", "0.61", None, "< OR = 4.00 ng/mL", "in_range")]
    if with_testosterone:
        rows += [_row(*TOTAL_T, "out_of_range", PANEL), _row(*FREE_T, "out_of_range", PANEL)]
    return {"page": 0, "specimen_id": "SYN-M-300", "collected": None, "footer": "SPECIMEN: SYN-M-300 PAGE 1 OF 1",
            "patient_name": PATIENT, "date_of_birth": None, "illegible": False, "rows": rows,
            "out_of_range_summary": None, "urine_note_printed": False}


def reads(pattern):
    return [_read(flag) for flag in pattern]


RESOLVED = (False, True, False, True)          # agreement reached on the fourth read
NEVER = (False, True, False, False, False)     # one read only, ever: excluded after five reads


def write_labs(path):
    document = fitz.open()
    layouts._image_page(document, "Synthetic scanned page with a missed testosterone panel")
    document.save(path)
    document.close()
    return path
