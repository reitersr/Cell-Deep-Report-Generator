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
Star, it was added to the reference library once, deliberately, with a real
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


@dataclass
class ExtractionReviewNotice:
    """Attached to a PatientRecord's generation run when extraction hit something it couldn't
    safely resolve on its own. This is operator-facing only — never rendered into the patient PDF."""
    unrecognized_markers: list = field(default_factory=list)   # list[UnrecognizedMarker]
    other_notes: list = field(default_factory=list)            # free-text flags, e.g. ambiguous protocol cadence


def format_review_notice(notice: ExtractionReviewNotice) -> str:
    """Plain-text summary an operator sees after generation, if anything needs their attention.
    Returns an empty string if nothing needs review — the common case."""
    if not notice.unrecognized_markers and not notice.other_notes:
        return ""
    lines = ["This report generated successfully. A few items need a quick human check:"]
    for m in notice.unrecognized_markers:
        line = f"  - Unrecognized marker \"{m.raw_name}\" ({m.raw_value}{' ' + m.raw_unit if m.raw_unit else ''})"
        if m.raw_range:
            line += f", reference range on source: {m.raw_range}"
        line += " — not included in this report. Add to markers_reference.py to include it in future reports."
        lines.append(line)
    for note in notice.other_notes:
        lines.append(f"  - {note}")
    return "\n".join(lines)
