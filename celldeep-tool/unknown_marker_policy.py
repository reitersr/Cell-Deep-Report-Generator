"""
CellDeep Report Generator — Unknown Marker Policy
====================================================
A real tension exists between two rules locked during calibration:
  1. "Never infer" — the pipeline must never invent a scoring threshold.
  2. "No gray, ever" — every colored pill must be a real green/yellow/red judgment.

An unrecognized marker (a name not in markers_reference.MARKER_LIBRARY — a
different lab's panel item, a new test CellDeep starts running, a name
formatted in a way our alias list doesn't catch) cannot honestly satisfy
both rules at once: we can't color it correctly without a threshold, and we
can't invent a threshold without violating never-infer.

RESOLUTION, locked here as policy rather than decided ad hoc per patient:
An unrecognized marker is NOT silently dropped, and it is NOT silently
scored with a guessed threshold. It is surfaced as a REVIEW ITEM — the
pipeline still completes and produces the patient's report using every
marker it DID recognize, but flags the unrecognized one(s) in a short
operator-facing notice (not shown to the patient) so a human can add it to
markers_reference.py once, after which every future patient with that same
marker is handled automatically. This treats "new marker" as a one-time
library update, not a recurring judgment call.

This mirrors exactly how the human-built version of this pipeline worked
tonight: when a genuinely new marker (Fibrinogen) needed to be added for
the reference patient, it was added to the reference library once, deliberately, with a real
clinical cutoff — not invented per-report.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UnrecognizedMarker:
    raw_name: str              # exactly as it appeared in the source document
    raw_value: str              # the value as extracted, unparsed
    raw_unit: Optional[str] = None
    raw_range: Optional[str] = None   # reference range as printed on the source lab report, if present
    source_context: Optional[str] = None  # a short excerpt for the operator to quickly verify
    cells: list[dict] = field(default_factory=list)
    section_heading: Optional[str] = None   # printed section heading the row sat under, if any


@dataclass
class ExtractionReviewNotice:
    """Attached to a PatientRecord's generation run when extraction hit something it couldn't
    safely resolve on its own. This is operator-facing only — never rendered into the patient PDF."""
    unrecognized_markers: list = field(default_factory=list)   # list[UnrecognizedMarker]
    other_notes: list = field(default_factory=list)            # free-text flags, e.g. ambiguous protocol cadence
    scan_summary: list = field(default_factory=list)           # scanned-page outcomes, printed first
    dexa_summary: list = field(default_factory=list)           # DEXA two-read outcomes, printed first
    name_header: list = field(default_factory=list)            # "This report is for ..." and printed names
    incomplete: list = field(default_factory=list)             # pages/sections excluded by parse failures
    staff_check: list = field(default_factory=list)            # one-screen STAFF CHECK, printed first


STAFF_CHECK_END = "(end of STAFF CHECK)"


def without_staff_check(text: str) -> str:
    """The staff notes after the STAFF CHECK block (tests and tools that read the detailed notices)."""
    head, separator, rest = text.partition(STAFF_CHECK_END + "\n\n")
    return rest if separator else text


# Text that only staff QA output produces. template.render refuses to build a patient PDF whose
# HTML contains any of these, and tests scan generated PDFs for them.
STAFF_NOTE_MARKERS = (
    "source=scan", "gate:", "STAFF REVIEW", "COVERAGE GAP", "NEEDS HUMAN REVIEW", "manual review required",
    "LAB-REPORTED CONFLICT", "PATIENT NAME MISMATCH", "SCANNED BLOODWORK", "ERROR: missing threshold",
    "Unrecognized marker", "POSSIBLE HALLUCINATION", "STAFF CHECK", "DEXA EXCLUDED", "LAB EXCLUDED", "DEXA PATIENT NAME MISMATCH", "DEXA name not printed", "DEXA AGE MISMATCH", "DEXA AGE NOT PRINTED", "DEXA SCAN OLDER THAN BLOODWORK", "DEXA NOT READ", "CENSORED RESULTS", "LAB FLAG DIFFERS", "DOB CONFLICT", "INCOMPLETE - ", "This report is for", "NAME MISMATCH", "TREATMENT STATUS:", "AGE CHECK", "AGE NOT COMPUTED", "PROVIDER NOTE REJECTED", "PROVIDER NOTE:", "LINE(S) NOT READ",
)


def format_review_notice(notice: ExtractionReviewNotice) -> str:
    """Plain-text summary an operator sees after generation, if anything needs their attention.
    Returns an empty string if nothing needs review — the common case."""
    if not (notice.unrecognized_markers or notice.other_notes or notice.scan_summary or notice.dexa_summary
            or notice.name_header or notice.incomplete):
        return ""
    lines = [*notice.staff_check, ""] if notice.staff_check else []
    lines += [*notice.incomplete, ""] if notice.incomplete else []
    if notice.name_header:
        lines += [*notice.name_header, ""]
    lines.append("This report generated successfully. A few items need a quick human check:")
    for block in (notice.dexa_summary, notice.scan_summary):
        if block:
            lines += ["", *block]
    if notice.dexa_summary or notice.scan_summary:
        lines += ["", "OTHER REVIEW ITEMS"]
    for m in notice.unrecognized_markers:
        line = f"  - Unrecognized marker \"{m.raw_name}\" ({m.raw_value}{' ' + m.raw_unit if m.raw_unit else ''})"
        if m.raw_range:
            line += f", reference range on source: {m.raw_range}"
        line += " — not included in this report. Add to markers_reference.py to include it in future reports."
        lines.append(line)
    for note in notice.other_notes:
        lines.append(f"  - {note}")
    return "\n".join(lines)
