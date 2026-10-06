"""A synthetic Access Medical Laboratories digital report (4 pages, browser-printed text layer), built from
invented names, dates and values only. It reproduces the layout's quirks: the per-page header block with a
two-digit-year Coll. Date and "Fasting: N", the "OUT OF RANGE SUMMARY" block repeating three later rows, flags
printed right after the value, ranges written "83 - 102" / "> 60", rows with no range or no units, sections
continued across pages, and explanatory text that must never become a result (the GFR stage table, the % Free
PSA probability table, a method note, the cortisol time-of-day lines).

EXPECTED lists every result row exactly once: (section, name, value, flag, range, unit)."""

import fitz

PATIENT_PRINTED = "SAMPLE, ALEX"
STAFF_NAME = "Alex S"
DOB = "01/15/2005"
AGE = 21
COLLECTED = "08/07/26"
COLLECTED_FULL = "08/07/2026"
X = {"name": 40, "results": 230, "range": 330, "units": 450, "indent": 60, "right": 360}
FONT = 8


def header(page_number, collected=COLLECTED, fasting="N", patient=PATIENT_PRINTED, age=AGE, accession="SYN-0001"):
    left = ["Access Medical Laboratories 5151 Synthetic Way, Testville, FL 00000",
            "Client: CELLDEEP SYNTHETIC CLINIC", f"Patient: {patient}", f"DOB. {DOB} Age:{age} Sex: M",
            "Phys: SYNTHETIC, DOCTOR", f"Page:{page_number}", "Final Report"]
    right = [None, f"Acc# {accession}", f"Coll. Date: {collected}", "Recv. Date: 08/08/26",
             "Print Date: 08/10/26", "Coll. Time: 07:45", "Report Status: FINAL", f"Fasting: {fasting}"]
    items = []
    for index, text in enumerate(left):
        items.append((X["name"], 30 + 12 * index, text))
    for index, text in enumerate(right):
        if text:
            items.append((X["right"], 30 + 12 * index, text))
    return items


def columns(y=135):
    return [(X["name"], y, "Test Name"), (X["results"], y, "Results"), (X["range"], y, "Reference Range"),
            (X["units"], y, "Units")]


def row(y, name, result, printed_range="", unit=""):
    items = [(X["name"], y, name), (X["results"], y, result)]
    if printed_range:
        items.append((X["range"], y, printed_range))
    if unit:
        items.append((X["units"], y, unit))
    return items


def text(y, words, x=None):
    return [(X["name"] if x is None else x, y, words)]


class Page:
    """Lines laid out top to bottom from just under the column header."""

    def __init__(self, number, **header_kwargs):
        self.items = header(number, **header_kwargs) + columns()
        self.y = 155

    def add(self, *parts):
        for part in parts:
            self.items += part(self.y) if callable(part) else part
        return self

    def title(self, words):
        self.items += text(self.y, words)
        self.y += 14
        return self

    def row(self, *args):
        self.items += row(self.y, *args)
        self.y += 12
        return self

    def note(self, words, indented=True):
        self.items += text(self.y, words, X["indent"] if indented else None)
        self.y += 12
        return self

    def cells(self, **placed):
        self.items += [(X[column], self.y, words) for column, words in placed.items()]
        self.y += 12
        return self


SUMMARY = [("MCV", "101 H", "80 - 100", "fL"), ("BUN/Creat Ratio", "7 L", "10 - 24", ""),
           ("Estradiol", "52 H", "8 - 35", "pg/mL")]


def pages(summary=SUMMARY, fasting="N", collected=COLLECTED, patient=PATIENT_PRINTED, age=AGE):
    kwargs = {"fasting": fasting, "collected": collected, "patient": patient, "age": age}
    one = Page(1, **kwargs).title("OUT OF RANGE SUMMARY")
    for entry in summary:
        one.row(*entry)
    one.title("COMPLETE BLOOD COUNT")
    for entry in [("WBC", "6.1", "3.8 - 10.8", "x10E3/uL"), ("RBC", "4.95", "4.20 - 5.80", "x10E6/uL"),
                  ("Hemoglobin", "15.2", "13.2 - 17.1", "g/dL"), ("Hematocrit", "45.8", "38.5 - 50.0", "%"),
                  ("MCV", "101 H", "80 - 100", "fL"), ("Platelets", "250", "140 - 400", "x10E3/uL")]:
        one.row(*entry)
    one.title("AUTOMATED DIFFERENTIAL")
    for entry in [("Neutrophil %", "58", "40 - 75", "%"), ("Lymphocyte %", "30", "20 - 45", "%"),
                  ("Monocyte %", "8", "2 - 12", "%")]:
        one.row(*entry)
    one.note("(Continued on Next Page)", indented=False)

    two = Page(2, **kwargs).title("AUTOMATED DIFFERENTIAL (Continued)")
    for entry in [("Eosinophil %", "3", "0 - 6", "%"), ("Basophil %", "1", "0 - 2", "%"),
                  ("Neutrophil #", "3.5", "1.5 - 7.8", "x10E3/uL"), ("Lymphocyte #", "1.8", "0.9 - 3.3", "x10E3/uL"),
                  ("Monocyte #", "0.5", "0.2 - 0.9", "x10E3/uL"), ("Eosinophil #", "0.2", "0 - 0.5", "x10E3/uL"),
                  ("Basophil #", "0.1", "0 - 0.2", "x10E3/uL")]:
        two.row(*entry)
    two.title("URINALYSIS GROSS EXAMINATION")
    for entry in [("Color", "Yellow", "Yellow"), ("Appearance", "Clear", "Clear"),
                  ("Specific Gravity", "1.020", "1.005 - 1.030"), ("pH", "6.0", "5.0 - 8.0"),
                  ("Protein", "Negative", "Negative"), ("Glucose", "Negative", "Negative")]:
        two.row(*entry)
    two.note("(Continued on Next Page)", indented=False)

    three = Page(3, **kwargs).title("URINALYSIS GROSS EXAMINATION (Continued)")
    for entry in [("Ketones", "Negative", "Negative"), ("Bilirubin", "Negative", "Negative"),
                  ("Occult blood", "Negative", "Negative"), ("Leukocytes", "Negative", "Negative"),
                  ("Nitrite", "Negative", "Negative"), ("Urobilinogen", "Normal", "Normal"),
                  ("Bacteria", "None seen", "None seen")]:
        three.row(*entry)
    three.title("GENERAL CHEMISTRY")
    three.row("Glucose", "95", "65 - 99", "mg/dL").row("BUN", "14", "7 - 25", "mg/dL")
    three.row("Creatinine", "1.05", "0.60 - 1.24", "mg/dL").row("GFR estimated", "98", "> 60", "mL/min/1.73m2")
    # The GFR stage table: text, not results (one line even fits the row columns).
    three.cells(name="Stage 1", results="> 90", units="ml/min").cells(name="Stage 3", results="30 to 59",
                                                                       units="ml/min")
    three.row("BUN/Creat Ratio", "7 L", "10 - 24").row("Sodium", "140", "135 - 146", "mmol/L")
    three.row("CO2", "25", "20 - 32", "mmol/L").row("Total Protein", "7.0", "6.1 - 8.1", "g/dL")
    three.row("Bili total", "0.6", "0.2 - 1.2", "mg/dL")
    three.title("IRON/ANEMIA EVALUATION").row("Ferritin", "120", "38 - 380", "ng/mL")
    three.title("TUMOR MARKERS").row("PSA, Total", "0.6", "0 - 4", "ng/mL").row("PSA, Free", "0.2", "", "ng/mL")
    # The % Free PSA probability table: text, not results.
    three.cells(name="% Free PSA 0 - 10%", results="49.2%", range="57.5%")
    three.note("10 - 15% 28.0%   15 - 20% 20.3%   20 - 25% 18.3%")

    four = Page(4, **kwargs).title("ENDOCRINE EVALUATION")
    for entry in [("Testosterone, Total", "612", "264 - 916"), ("Sex Hormone Bind Globulin", "38", "16.5 - 55.9"),
                  ("Testosterone, Free", "11.2", "5.7 - 17.9"), ("Bioavailable Testosterone", "290", "126 - 412"),
                  ("DHEA-Sulfate", "350", "138 - 475"), ("Estradiol", "52 H", "8 - 35", "pg/mL"),
                  ("Cortisol", "14.1")]:
        four.row(*entry)
    four.note("Morning am 6-10: 6.02 - 18.4 ug/dl").note("Afternoon pm 4-8: 2.68 - 10.5 ug/dl")
    four.note("Testosterone performed by Roche diagnostics ECLIA method on a cobas e801 analyzer", indented=False)
    return [one.items, two.items, three.items, four.items]


def write(path, page_items=None):
    document = fitz.open()
    for items in page_items or pages():
        page = document.new_page()
        for x, y, words in items:
            page.insert_text((x, y), words, fontsize=FONT, fontname="helv")
    document.save(path)
    document.close()
    return path


# Every result row in the table (never the summary), once: (section, printed name, value, flag, range, unit).
EXPECTED = [
    ("COMPLETE BLOOD COUNT", "WBC", "6.1", None, "3.8 - 10.8", "x10E3/uL"),
    ("COMPLETE BLOOD COUNT", "RBC", "4.95", None, "4.20 - 5.80", "x10E6/uL"),
    ("COMPLETE BLOOD COUNT", "Hemoglobin", "15.2", None, "13.2 - 17.1", "g/dL"),
    ("COMPLETE BLOOD COUNT", "Hematocrit", "45.8", None, "38.5 - 50.0", "%"),
    ("COMPLETE BLOOD COUNT", "MCV", "101", "H", "80 - 100", "fL"),
    ("COMPLETE BLOOD COUNT", "Platelets", "250", None, "140 - 400", "x10E3/uL"),
    ("AUTOMATED DIFFERENTIAL", "Neutrophil %", "58", None, "40 - 75", "%"),
    ("AUTOMATED DIFFERENTIAL", "Lymphocyte %", "30", None, "20 - 45", "%"),
    ("AUTOMATED DIFFERENTIAL", "Monocyte %", "8", None, "2 - 12", "%"),
    ("AUTOMATED DIFFERENTIAL", "Eosinophil %", "3", None, "0 - 6", "%"),
    ("AUTOMATED DIFFERENTIAL", "Basophil %", "1", None, "0 - 2", "%"),
    ("AUTOMATED DIFFERENTIAL", "Neutrophil #", "3.5", None, "1.5 - 7.8", "x10E3/uL"),
    ("AUTOMATED DIFFERENTIAL", "Lymphocyte #", "1.8", None, "0.9 - 3.3", "x10E3/uL"),
    ("AUTOMATED DIFFERENTIAL", "Monocyte #", "0.5", None, "0.2 - 0.9", "x10E3/uL"),
    ("AUTOMATED DIFFERENTIAL", "Eosinophil #", "0.2", None, "0 - 0.5", "x10E3/uL"),
    ("AUTOMATED DIFFERENTIAL", "Basophil #", "0.1", None, "0 - 0.2", "x10E3/uL"),
    ("URINALYSIS GROSS EXAMINATION", "Color", "Yellow", None, "Yellow", ""),
    ("URINALYSIS GROSS EXAMINATION", "Appearance", "Clear", None, "Clear", ""),
    ("URINALYSIS GROSS EXAMINATION", "Specific Gravity", "1.020", None, "1.005 - 1.030", ""),
    ("URINALYSIS GROSS EXAMINATION", "pH", "6.0", None, "5.0 - 8.0", ""),
    ("URINALYSIS GROSS EXAMINATION", "Protein", "Negative", None, "Negative", ""),
    ("URINALYSIS GROSS EXAMINATION", "Glucose", "Negative", None, "Negative", ""),
    ("URINALYSIS GROSS EXAMINATION", "Ketones", "Negative", None, "Negative", ""),
    ("URINALYSIS GROSS EXAMINATION", "Bilirubin", "Negative", None, "Negative", ""),
    ("URINALYSIS GROSS EXAMINATION", "Occult blood", "Negative", None, "Negative", ""),
    ("URINALYSIS GROSS EXAMINATION", "Leukocytes", "Negative", None, "Negative", ""),
    ("URINALYSIS GROSS EXAMINATION", "Nitrite", "Negative", None, "Negative", ""),
    ("URINALYSIS GROSS EXAMINATION", "Urobilinogen", "Normal", None, "Normal", ""),
    ("URINALYSIS GROSS EXAMINATION", "Bacteria", "None seen", None, "None seen", ""),
    ("GENERAL CHEMISTRY", "Glucose", "95", None, "65 - 99", "mg/dL"),
    ("GENERAL CHEMISTRY", "BUN", "14", None, "7 - 25", "mg/dL"),
    ("GENERAL CHEMISTRY", "Creatinine", "1.05", None, "0.60 - 1.24", "mg/dL"),
    ("GENERAL CHEMISTRY", "GFR estimated", "98", None, "> 60", "mL/min/1.73m2"),
    ("GENERAL CHEMISTRY", "BUN/Creat Ratio", "7", "L", "10 - 24", ""),
    ("GENERAL CHEMISTRY", "Sodium", "140", None, "135 - 146", "mmol/L"),
    ("GENERAL CHEMISTRY", "CO2", "25", None, "20 - 32", "mmol/L"),
    ("GENERAL CHEMISTRY", "Total Protein", "7.0", None, "6.1 - 8.1", "g/dL"),
    ("GENERAL CHEMISTRY", "Bili total", "0.6", None, "0.2 - 1.2", "mg/dL"),
    ("IRON/ANEMIA EVALUATION", "Ferritin", "120", None, "38 - 380", "ng/mL"),
    ("TUMOR MARKERS", "PSA, Total", "0.6", None, "0 - 4", "ng/mL"),
    ("TUMOR MARKERS", "PSA, Free", "0.2", None, "", "ng/mL"),
    ("ENDOCRINE EVALUATION", "Testosterone, Total", "612", None, "264 - 916", ""),
    ("ENDOCRINE EVALUATION", "Sex Hormone Bind Globulin", "38", None, "16.5 - 55.9", ""),
    ("ENDOCRINE EVALUATION", "Testosterone, Free", "11.2", None, "5.7 - 17.9", ""),
    ("ENDOCRINE EVALUATION", "Bioavailable Testosterone", "290", None, "126 - 412", ""),
    ("ENDOCRINE EVALUATION", "DHEA-Sulfate", "350", None, "138 - 475", ""),
    ("ENDOCRINE EVALUATION", "Estradiol", "52", "H", "8 - 35", "pg/mL"),
    ("ENDOCRINE EVALUATION", "Cortisol", "14.1", None, "", ""),
]
