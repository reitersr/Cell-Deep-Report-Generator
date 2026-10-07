"""A synthetic Quest-style scanned draw whose urinalysis rows sit under Quest's full panel heading
("URINALYSIS, COMPLETE W/REFLEX TO CULTURE") rather than a bare "URINALYSIS" (scenario "quest_scanned_urinalysis").
The same printed test names appear in the blood chemistry and in the urinalysis ("GLUCOSE"), so a urine row read as
blood glucose would collide with the blood result. Every name, value and date is invented.

Lab PDF: two image-only pages read by the (scripted) vision model; staff enter the patient name and Collected date.
  1  COMPREHENSIVE METABOLIC PANEL: blood GLUCOSE and other chemistry, plus TSH
  2  URINALYSIS, COMPLETE W/REFLEX TO CULTURE: COLOR ... GLUCOSE NEGATIVE ... OCCULT BLOOD NEGATIVE ... KETONES 1+
"""

import fitz

from synthetic_fixtures import layouts

PATIENT = layouts.PATIENT
COLLECTED = "03/18/2026"
CMP = "COMPREHENSIVE METABOLIC PANEL"
URINALYSIS = "URINALYSIS, COMPLETE W/REFLEX TO CULTURE"
BLOOD_GLUCOSE = "88"


def _row(name, result, reference, flag=None, column="in_range", section=None):
    return {"name": name, "result_text": result, "flag": flag, "reference_range": reference, "lab_code": None,
            "column": column, "page": 0, "illegible": False, "section": section}


def _page(rows, number):
    return {"page": 0, "specimen_id": "SYN-Q-700", "collected": None,
            "footer": f"SPECIMEN: SYN-Q-700 PAGE {number} OF 2", "patient_name": PATIENT, "date_of_birth": None,
            "illegible": False, "rows": rows, "out_of_range_summary": None}


def scan_reads():
    """{page number: one literal read} (the same for every read of the page)."""
    one = [_row("GLUCOSE", BLOOD_GLUCOSE, "65-99 mg/dL", section=CMP),
           _row("SODIUM", "140", "135-146 mmol/L", section=CMP),
           _row("CREATININE", "0.88", "0.60-1.29 mg/dL", section=CMP),
           _row("TSH", "1.72", "0.40-4.50 mIU/L")]
    two = [_row("COLOR", "YELLOW", "YELLOW", section=URINALYSIS),
           _row("SPECIFIC GRAVITY", "1.015", "1.001-1.035", section=URINALYSIS),
           _row("PH", "6.0", "5.0-8.0", section=URINALYSIS),
           _row("GLUCOSE", "NEGATIVE", "NEGATIVE", section=URINALYSIS),
           _row("OCCULT BLOOD", "NEGATIVE", "NEGATIVE", section=URINALYSIS),
           _row("PROTEIN", "NEGATIVE", "NEGATIVE", section=URINALYSIS),
           _row("KETONES", "1+", "NEGATIVE", column="out_of_range", section=URINALYSIS)]
    return {1: _page(one, 1), 2: _page(two, 2)}


def write_labs(path):
    """Two image-only pages (no text layer), each with its own invented caption so their renders differ."""
    document = fitz.open()
    for number in (1, 2):
        layouts._image_page(document, f"Synthetic Quest-style scanned page {number} of 2")
    document.save(path)
    document.close()
    return path
