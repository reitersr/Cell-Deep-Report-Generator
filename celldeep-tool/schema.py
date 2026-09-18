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
from typing import Optional, Literal

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

    # for categorical markers (e.g. Urinalysis) — bounded/range fields stay None
    is_good_then: Optional[bool] = None
    is_good_now: Optional[bool] = None

    # OPTIONAL: full intermediate history if more than 2 readings exist (mirrors DEXA's multi-point pattern).
    # List of (date_display, value, disp_value) tuples, chronological. Empty list if only then/now exist.
    full_history: list = field(default_factory=list)

    # set by scoring.attach_scores() — declared here (not left dynamic) so dataclasses.asdict()
    # actually serializes them when the record is sent to the generation step.
    now_tier: Optional[str] = None
    now_pct: Optional[int] = None
    then_tier: Optional[str] = None
    then_pct: Optional[int] = None


@dataclass
class DexaReading:
    """One DEXA scan. VAT/SAT are optional per-reading — not every visit re-measures them."""
    date_display: str          # e.g. "May 21, 2025"
    total_mass_lb: float
    fat_mass_lb: float
    lean_mass_lb: float
    body_fat_pct: str          # pre-formatted, e.g. "34.4%"
    vat_fat_mass_lb: Optional[float] = None   # None if this visit didn't measure VAT
    scan_image_b64: Optional[str] = None      # rendered body-composition scan page, if present in the source PDF


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

    # raw provider note text, kept for reference/traceability — not directly rendered
    provider_note_raw: Optional[str] = None

    # document mode — determined by whether first_draw_date is None (true first consult)
    # or a real prior date exists (progression document). Generation branches on this.
    @property
    def is_first_consult(self) -> bool:
        return self.first_draw_date is None
