"""A synthetic model of the clinic's 7-page DEXA PDF, built from invented values and dates only.

Names are redacted on every page (patient_name is null), as in the clinic's real file. Page types:
  1 image page: two scan dates, the patient's age in the header; the latest scan prints its total
    body fat as "19.0 (e)" (estimated)
  2 Segmental Analysis page: the body-composition history table, no age; prints the latest body fat
    as "19.0" (no "(e)" on this page) and no body fat at all for the two middle scans
  3 Abdomen Composition page: a date table with an Age column (VAT rows)
  4 VAT/SAT trend page: dates and ages
  5 a second Segmental Analysis page, no age
  6 a page from ANOTHER profile: ages 34.0 / 34.1, two later scans (it must reach nothing)
  7 a page with no scan rows
The two middle scans print no body fat %, so it is computed as fat / (fat + lean), the scan's own
definition: 36.1 / (36.1 + 126.8) = 22.2% and 33.0 / (33.0 + 128.5) = 20.4% (fat / total mass
would give the different 21.3% and 19.6%).
"""

import copy

from synthetic_fixtures import dexa as fx

PATIENT = fx.PATIENT
AGE = 45

# date, total, fat, lean, printed body fat (None = not printed)
P1 = ("09/08/2025", "171.0", "40.2", "124.6", "24.4 %")
P2 = ("11/17/2025", "169.4", "36.1", "126.8", None)    # computed 22.2 (fat/total would be 21.3)
P3 = ("01/26/2026", "168.0", "33.0", "128.5", None)    # computed 20.4 (fat/total would be 19.6)
P4 = ("03/30/2026", "166.2", "30.4", "129.6", "19.0")  # "(e)" on the image page only
VAT = {P1[0]: ("1.12", "118.0", "44.9"), P2[0]: ("1.01", "109.0", "45.1"),
       P3[0]: ("0.93", "101.0", "45.2"), P4[0]: ("0.86", "96.0", "45.3")}
# The other profile: later scans, so if it were accepted it would become "Where you are now".
F1 = ("05/18/2026", "148.2", "52.3", "91.4", "36.4 %")
F2 = ("07/06/2026", "147.5", "51.0", "92.0", "35.7 %")
FOREIGN_TEXT = ["36.4", "35.7", "148.2", "147.5", "52.3", "51.0", "91.4", "92.0", "May 18, 2026", "July 6, 2026",
                "05/18/2026", "07/06/2026"]
LAB_DATE = "06/15/2026"  # 77 days after the latest scan: the staff notes warn (DEXA_STALE_DAYS = 60)

LABELS = ["image", "segmental-1", "abdomen", "vat-trend", "segmental-2", "other-profile", "no-scans"]


def _composition(scan, body_fat=None):
    date, total, fat, lean, printed = scan
    return fx.scan(date, total, fat, lean, printed if body_fat is None else body_fat)


def _vat(scan):
    vat, area, age = VAT[scan[0]]
    return fx.scan(scan[0], vat=vat, area=area, age=age)


def _same(scans, **kwargs):
    return [fx.read(scans, **kwargs), fx.read(copy.deepcopy(scans), **kwargs)]


def pages():
    """{label: [read 1, read 2]}: both reads of every page agree."""
    segmental = [_composition(P1), _composition(P2), _composition(P3), _composition(P4)]
    return {
        "image": _same([_composition(P1), _composition(P4, "19.0 (e)")], patient_name=None, age="45.3"),
        "segmental-1": _same(segmental, patient_name=None),
        "abdomen": _same([_vat(scan) for scan in (P1, P2, P3, P4)], patient_name=None),
        "vat-trend": _same([_vat(scan) for scan in (P1, P2, P3, P4)], patient_name=None),
        "segmental-2": _same(segmental, patient_name=None),
        "other-profile": _same([dict(_composition(F1), age="34.0"), dict(_composition(F2), age="34.1")],
                               patient_name=None),
        "no-scans": _same([], patient_name=None),
    }
