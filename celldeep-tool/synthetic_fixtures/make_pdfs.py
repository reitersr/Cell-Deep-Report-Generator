"""SYNTHETIC, FAKE test data only - no real patient information.
Builds throwaway lab-report PDFs used solely to exercise the null-now
extraction/scoring/render path end to end. Not tied to any real person.
"""
import fitz

OUT = "/workspaces/Cell-Deep-Report-Generator/celldeep-tool/synthetic_fixtures"


def make_pdf(path: str, text: str):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(36, 36, 560, 780), text, fontsize=9, fontname="cour")
    doc.save(path)
    doc.close()


# ---- SYN-COMPLETE-1 (Star-analog): both draws complete, every marker has real then+now ----
make_pdf(f"{OUT}/syn_complete_1.pdf", """
SYNTHETIC TEST LAB REPORT - FAKE DATA, NOT A REAL PATIENT
Patient: Synthetic Test Patient One (SYN-COMPLETE-1)
DOB/Age: 37, Sex: Female

DRAW 1 - COLLECTED: 02/10/2026
  hs-CRP: 3.1 mg/L      (ref optimal <1.0)
  LDL Cholesterol: 138 mg/dL   (ref optimal <100)
  HDL Cholesterol: 45 mg/dL    (ref optimal >=50)
  TSH: 5.2 uIU/mL        (ref 0.40-4.50)
  Vitamin D, 25-Hydroxy: 18 ng/mL   (ref optimal >=30)

DRAW 2 - COLLECTED: 07/15/2026
  hs-CRP: 0.7 mg/L
  LDL Cholesterol: 109 mg/dL
  HDL Cholesterol: 52 mg/dL
  TSH: 2.1 uIU/mL
  Vitamin D, 25-Hydroxy: 32 ng/mL

All tests above were run and resulted on both collection dates.
""")

# ---- SYN-EXTENSIVE-2 (extensive-data male-analog): both draws complete, larger panel ----
make_pdf(f"{OUT}/syn_extensive_2.pdf", """
SYNTHETIC TEST LAB REPORT - FAKE DATA, NOT A REAL PATIENT
Patient: Synthetic Test Patient Two (SYN-EXTENSIVE-2)
DOB/Age: 50, Sex: Male

DRAW 1 - COLLECTED: 01/05/2026
  hs-CRP: 2.4 mg/L
  LDL Cholesterol: 145 mg/dL
  HDL Cholesterol: 38 mg/dL
  Triglycerides: 210 mg/dL
  Testosterone, Total: 380 ng/dL   (ref 500-900 male)
  TSH: 3.8 uIU/mL
  Free T4: 1.1 ng/dL
  Ferritin: 210 ng/mL   (ref 15-150)
  Vitamin D, 25-Hydroxy: 22 ng/mL
  HbA1c: 5.9 %

DRAW 2 - COLLECTED: 06/20/2026
  hs-CRP: 1.6 mg/L
  LDL Cholesterol: 118 mg/dL
  HDL Cholesterol: 44 mg/dL
  Triglycerides: 160 mg/dL
  Testosterone, Total: 560 ng/dL
  TSH: 2.9 uIU/mL
  Free T4: 1.3 ng/dL
  Ferritin: 140 ng/mL
  Vitamin D, 25-Hydroxy: 41 ng/mL
  HbA1c: 5.6 %

All tests above were run and resulted on both collection dates.
""")

# ---- SYN-LIMITED-3 (limited-data male-analog): single draw only, first-ever reading ----
make_pdf(f"{OUT}/syn_limited_3.pdf", """
SYNTHETIC TEST LAB REPORT - FAKE DATA, NOT A REAL PATIENT
Patient: Synthetic Test Patient Three (SYN-LIMITED-3)
DOB/Age: 29, Sex: Male

DRAW 1 - COLLECTED: 05/01/2026 (this patient's only lab draw on file, first-ever reading)
  hs-CRP: 1.2 mg/L
  LDL Cholesterol: 96 mg/dL
  TSH: 2.0 uIU/mL
  Vitamin D, 25-Hydroxy: 35 ng/mL

No prior draw exists for this patient.
""")

# ---- SYN-NULLNOW-4 (Giovanni-analog): draw 2 has chemistry/thyroid/endocrine cancelled ----
make_pdf(f"{OUT}/syn_nullnow_4.pdf", """
SYNTHETIC TEST LAB REPORT - FAKE DATA, NOT A REAL PATIENT
Patient: Synthetic Test Patient Four (SYN-NULLNOW-4)
DOB/Age: 45, Sex: Male

DRAW 1 - COLLECTED: 02/25/2026
  Ferritin: 180 ng/mL       (ref 15-150)
  TSH: 6.1 uIU/mL           (ref 0.40-4.50)
  Free T4: 0.6 ng/dL        (ref 0.8-1.8)
  Free T3: 1.7 pg/mL        (ref 2.0-4.4)
  Testosterone, Total: 310 ng/dL   (ref 500-900 male)
  DHEA-S: 45.0 ug/dL        (ref 60.9-337.0)
  Vitamin D, 25-Hydroxy: 19 ng/mL
  Vitamin B12: 410 pg/mL

DRAW 2 - COLLECTED: 07/31/2026, Accession# SYN-007426208

  CHEMISTRY PANEL: SPECIMEN REJECTED - DEGENERATED SAMPLE. NOT RESULTED.
  FERRITIN: TEST CANCELLED - NO SAMPLE RECEIVED. NOT RESULTED.
  THYROID PANEL (TSH, Free T4, Free T3): TEST CANCELLED - NOT ORDERED THIS DRAW. NOT RESULTED.
  ENDOCRINE PANEL (Testosterone Total, DHEA-S): SPECIMEN REJECTED - DEGENERATED SAMPLE. NOT RESULTED.

  Vitamin D, 25-Hydroxy: 34 ng/mL
  Vitamin B12: 560 pg/mL

Only Vitamin D and Vitamin B12 were actually run and resulted on the 07/31/2026 draw. Every other
section above listed for that draw was cancelled, rejected, or not ordered - no result exists for
those markers on this second draw. Do not carry forward the 02/25/2026 values for those markers.
""")

print("synthetic fixtures written")
