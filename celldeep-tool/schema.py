"""
CellDeep Report Generator — Data Schema
=========================================
This is the CONTRACT between the extraction step (reads raw pasted PDFs/notes)
and the generation step (produces the locked V23 design).

Core rule, locked during calibration: NEVER INFER. Every field below is
Optional for a reason — if the source material doesn't contain it, it stays
None / empty, and the template renders that section gracefully absent. The
extraction step must never guess, estimate, or fill a gap. A missing field
is an honest "not provided," never a silent assumption.

This schema is deliberately plain dicts/dataclasses, not tied to any specific
web framework — it's the shape of the JSON that flows from extraction -> generation -> template.
"""

from dataclasses import dataclass, field
import re
from typing import Optional, Literal

_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def normalize_date_for_matching(date_str: str):
    """Best-effort normalization so the same real draw date printed differently across two
    reports (e.g. "04/14/2026" vs "April 14, 2026") is recognized as one draw for reconciliation.
    Falls back to the raw stripped/lowercased string when the format isn't recognized - this only
    affects whether two occurrences get grouped together, it never invents or alters a date used
    for display."""
    if not date_str:
        return ""
    s = date_str.strip().lower().rstrip(".")
    m = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)", s)
    if m:
        mm, dd, yy = m.groups()
        yy = int(yy)
        if yy < 100:
            yy += 2000
        return (yy, int(mm), int(dd))
    m = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", s)
    if m:
        yy, mm, dd = m.groups()
        return (int(yy), int(mm), int(dd))
    m = re.search(r"\b([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b", s)
    if m:
        month_name, dd, yy = m.groups()
        month = _MONTH_NAMES.get(month_name)
        if month:
            return (int(yy), month, int(dd))
    return s

Direction = Literal["lower", "higher"]  # for a bounded marker: which direction is optimal
MarkerKind = Literal["bounded", "range", "categorical"]  # matches data.py's three scoring modes


@dataclass
class Marker:
    """One biomarker, one lab category, with as much history as was actually provided."""
    name: str                       # e.g. "Lp-PLA2 Activity" — must match a name we recognize (see markers_reference.py)
    category: str                   # e.g. "Inflammation" — one of the fixed data categories
    unit: str                       # e.g. "mg/L" — empty string if unitless
    kind: MarkerKind                # which scoring formula applies
    disp_range: str                 # human-readable reference range as it should print, e.g. "optimal <1.0"

    # scoring inputs (only what's needed for this marker's kind — others stay None)
    optimal: Optional[float] = None       # for "bounded": the optimal cutoff
    moderate: Optional[float] = None      # for "bounded": the moderate cutoff
    direction: Optional[Direction] = None # for "bounded": which way is better
    inclusive: bool = True                 # whether equality at a bounded cutoff is optimal
    moderate_inclusive: bool = True        # whether equality at the moderate cutoff is moderate (False: flag)
    lo: Optional[float] = None            # for "range": low end of reference range
    hi: Optional[float] = None            # for "range": high end of reference range
    suppress_low_on_trt: bool = False     # LH/FSH only: low values are not flagged on explicit TRT
    unscored_reason: Optional[str] = None # e.g. missing_threshold; distinct from a missing latest value
    range_source: Optional[str] = None    # "celldeep" or "lab" when a numeric range was used
    lab_range_then: Optional[dict] = None # literal printed range for the earliest draw, if extracted
    lab_range_now: Optional[dict] = None  # literal printed range for the latest draw, if extracted
    then_lo: Optional[float] = None       # scoring endpoints for a source-lab then range
    then_hi: Optional[float] = None

    # actual values — "then" is the earliest available reading, "now" is the most recent.
    # If only one reading exists (first-ever test), then=None and only now is set.
    then: Optional[float] = None
    now: Optional[float] = None
    disp_then: Optional[str] = None   # pre-formatted display string, e.g. "3.1" or "1+" (categorical)
    disp_now: Optional[str] = None              # pre-formatted display string, e.g. "0.7" or "Negative"
    then_date_display: Optional[str] = None     # date of the reconciled then value, never a raw occurrence date
    now_date_display: Optional[str] = None      # date of the reconciled now value, never a raw occurrence date

    # for categorical markers (e.g. Urinalysis) — bounded/range fields stay None
    is_good_then: Optional[bool] = None
    is_good_now: Optional[bool] = None

    # OPTIONAL: full intermediate history if more than 2 readings exist (mirrors DEXA's multi-point pattern).
    # List of (date_display, value, disp_value) tuples, chronological. Empty list if only then/now exist.
    full_history: list = field(default_factory=list)

    # the lab's own printed H/L flag for the then/now result, never computed
    lab_flag_then: Optional[str] = None
    lab_flag_now: Optional[str] = None

    # Shown instead of name when the printed source changes the label (e.g. "Cortisol, Total" when the draw time is
    # not inside the lab's printed morning window), and a short printed fact shown next to the value
    # ("collected 07:45"). Neither changes scoring.
    display_name: Optional[str] = None
    value_note: Optional[str] = None
    # Date of a newer result for this test that is shown lab-reported instead of scored (drawn without confirmed
    # fasting, or on a different assay); the report never calls such a test "not retested".
    latest_lab_reported_date: Optional[str] = None

    # set by scoring.attach_scores() — declared here (not left dynamic) so dataclasses.asdict()
    # actually serializes them when the record is sent to the generation step.
    now_tier: Optional[str] = None
    now_pct: Optional[int] = None
    then_tier: Optional[str] = None
    then_pct: Optional[int] = None


@dataclass
class LabReportedResult:
    """A test CellDeep shows exactly as the lab reported it, with no CellDeep score.

    results: chronological list of {date_display, disp_value, lab_flag ("H"/"L"/None, the lab's own
    printed flag), lab_range (printed reference range text or None)}."""
    name: str
    group: str
    results: list = field(default_factory=list)
    note: Optional[str] = None  # one patient-facing line, e.g. why a non-fasting glucose is not scored


@dataclass
class DexaReading:
    """One DEXA scan. VAT/SAT are optional per-reading — not every visit re-measures them."""
    date_display: str          # e.g. "May 12, 2025"
    # A scan may be partial (e.g. a follow-up visit that only re-measured VAT) — any metric the
    # source didn't actually report stays None, never a fabricated 0, so the template can render
    # it honestly (a dash) instead of a real-looking zero measurement.
    total_mass_lb: Optional[float] = None
    fat_mass_lb: Optional[float] = None
    lean_mass_lb: Optional[float] = None
    body_fat_pct: Optional[str] = None   # pre-formatted, e.g. "34.4%"
    vat_fat_mass_lb: Optional[float] = None   # None if this visit didn't measure VAT
    visceral_fat_area_cm2: Optional[float] = None  # None if this visit didn't report VAT area
    scan_image_b64: Optional[str] = None      # rendered body-composition scan page, if present in the source PDF
    estimated: list = field(default_factory=list)  # fields the scanner printed with "(e)", e.g. ["vat_fat_mass_lb"]
    computed: list = field(default_factory=list)   # fields not printed, computed from printed values ("body_fat_pct")


@dataclass
class ProtocolItem:
    """One active compound/therapy. target_categories is the patient-facing system name(s) it addresses —
    may be empty if the item isn't expected to move any lab marker (e.g. a cognitive-support peptide)."""
    # Protocol item count and reasoning length affect page flow per the locked framework in template.py.
    name: str                          # e.g. "Retatrutide"
    cadence: str                       # e.g. "weekly", "daily", "as directed"
    target_categories: list = field(default_factory=list)   # e.g. ["Fuel"] — empty list is valid and meaningful
    lab_visible: bool = True           # False = "Also in your protocol, not reflected in bloodwork" section


@dataclass
class PainPoint:
    """A synthesized (never literally quoted) patient concern or goal, tagged to whichever
    category or categories the story genuinely touches — may be more than one, per calibration."""
    text: str                          # plain statement, no quotation marks, e.g. "Unwanted weight you couldn't drop."
    categories: list = field(default_factory=list)  # e.g. ["Structure", "Fuel"]


@dataclass
class PatientRecord:
    """Everything extraction produces for one patient, one volume. This is what generation consumes."""
    # identity
    name: str
    age: Optional[int] = None
    sex: Optional[Literal["male", "female"]] = None
    postmenopausal_bhrt: Optional[bool] = None  # true only when both conditions are explicit in the note
    on_trt: Optional[bool] = None               # true only when TRT is explicit in the note

    # bloodwork draw dates — may be a single date (first consult) or two (then/now)
    first_draw_date: Optional[str] = None   # None if this IS the first draw
    latest_draw_date: Optional[str] = None

    # the actual panel — whatever markers were present in the source material, nothing invented
    markers: list = field(default_factory=list)   # list[Marker]

    # DEXA — chronological list of every real scan found. Empty list if no DEXA was provided at all.
    dexa_history: list = field(default_factory=list)   # list[DexaReading]

    # protocol — whatever was actually named in the provider's note
    protocol: list = field(default_factory=list)   # list[ProtocolItem]

    # synthesized from the provider's note — may be empty if the note had nothing usable
    pain_points: list = field(default_factory=list)   # list[PainPoint]

    # CNS Vital Signs — entirely optional, premium-tier only. None means "not applicable," not "missing."
    cns_domains: Optional[list] = None   # list of (domain_name, patient_pct, tier) or None entirely

    # Seven provider-selected Vitality Index domains, normalized to 0/1/2/None.
    vitality_index: dict = field(default_factory=dict)

    # raw provider note text, kept for reference/traceability — not directly rendered
    provider_note_raw: Optional[str] = None

    # lab results shown as printed (value, lab range, lab H/L flag) but never scored
    lab_reported: list = field(default_factory=list)   # list[LabReportedResult]
    draft_label: Optional[str] = None   # printed on the patient PDF when the report needs staff review before release

    # document mode — determined by whether first_draw_date is None (true first consult)
    # or a real prior date exists (progression document). Generation branches on this.
    @property
    def is_first_consult(self) -> bool:
        return self.first_draw_date is None
