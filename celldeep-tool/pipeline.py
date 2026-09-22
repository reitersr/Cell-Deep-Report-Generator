"""
CellDeep Report Generator — Pipeline Orchestrator
=====================================================
This is the actual entry point. Run this with raw patient material and it
produces the finished PDF, following the locked V23 design end to end.

NOTE ON TESTING: this was written and assembled without live API access
(the build environment used to develop this has no outbound internet). It
has NOT been run end-to-end yet. The first real test should be: run this
against Star Hawkins' actual real source material (the same lab PDF, DEXA
PDFs, and provider note used throughout calibration) and confirm the output
matches V23 — that's the one case where we already know exactly what
correct output looks like, so it's the right first proof before testing on
any new patient.

Usage:
    python pipeline.py --labs path/to/labs.pdf --dexa path/to/dexa1.pdf path/to/dexa2.pdf \\
                        --note path/to/provider_note.txt --patient-name "Jane Doe" --age 42 --sex female \\
                        --out output.pdf
"""

import os
import json
import base64
import argparse
import re
from dataclasses import asdict

from anthropic import Anthropic
from json_repair import repair_json
import fitz

from schema import PatientRecord, Marker, DexaReading, ProtocolItem, PainPoint
from markers_reference import (MARKER_LIBRARY, DATA_TO_PATIENT_CATEGORY, NARRATIVE_CATEGORY_OVERRIDE,
                                resolve_marker_config, has_missing_thresholds)
from protocol_reference import PROTOCOL_LIBRARY, lookup_protocol_item
from unknown_marker_policy import UnrecognizedMarker, ExtractionReviewNotice, format_review_notice
from extraction_prompt import EXTRACTION_SYSTEM_PROMPT, EXTRACTION_OUTPUT_SCHEMA, build_extraction_user_message
from generation_prompt import GENERATION_SYSTEM_PROMPT
import scoring
import template

if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

MODEL = "claude-sonnet-4-6"


def parse_provider_statuses(note_text: str | None) -> tuple[bool | None, bool | None]:
    """Extract only explicitly stated BHRT and TRT statuses from provider notes."""
    text = (note_text or "").lower()
    if not text:
        return None, None

    postmenopausal = re.search(r"\bpost[- ]?menopausal\b", text)
    bhrt = re.search(r"\b(?:bhrt|bioidentical hormone replacement|hormone replacement therapy|hormone replacement)\b", text)
    negative_postmenopausal = re.search(r"\b(?:not|never|pre)[- ]?post[- ]?menopausal\b", text)
    negative_bhrt = re.search(r"\b(?:not|never)\s+(?:on\s+)?(?:bhrt|hormone replacement(?: therapy)?)\b", text)
    bhrt_status = True if postmenopausal and bhrt and not negative_postmenopausal and not negative_bhrt else None

    trt = re.search(
        r"\b(?:on|start(?:ing|ed)?|begin(?:ning)?|active(?:ly on)?)\s+(?:testosterone\s+)?(?:trt|replacement therapy)\b"
        r"|\btestosterone\s+(?:replacement therapy|injections?|therapy)\s+(?:is\s+)?(?:active|current|started|ongoing)\b"
        r"|\bon\s+testosterone\s+injections?\b"
        r"|\bactive\s+testosterone\s+(?:replacement\s+)?therapy\b",
        text,
    )
    negative_trt = re.search(r"\b(?:not|never|no longer)\s+(?:on\s+)?(?:trt|testosterone(?: replacement therapy| injections?| therapy))\b", text)
    on_trt = True if trt and not negative_trt else None
    return bhrt_status, on_trt


def _valid_lab_range(raw: dict, draw: str) -> dict | None:
    lo = raw.get(f"lab_range_{draw}_lo")
    hi = raw.get(f"lab_range_{draw}_hi")
    display = raw.get(f"lab_range_{draw}_display")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or not isinstance(display, str):
        return None
    if not display or lo > hi:
        return None
    return {"lo": lo, "hi": hi, "display": display}


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _normalize_date_for_matching(date_str: str):
    """Best-effort normalization so the same real draw date printed differently across two
    reports (e.g. "04/24/2026" vs "April 24, 2026") is recognized as one draw for reconciliation.
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


def reconcile_marker_occurrences(occurrences: list[dict]) -> list[dict]:
    """Reconcile marker values against the document-wide most recent report date."""
    report_dates = [
        _normalize_date_for_matching(occ.get("date_display", ""))
        for occ in occurrences
    ]
    report_dates = [date for date in report_dates if isinstance(date, tuple)]
    report_current_key = max(report_dates) if report_dates else None
    by_marker: dict = {}
    order = []
    for occ in occurrences:
        match = markers_reference_lookup(occ.get("name", ""))
        if match is None:
            continue  # unrecognized occurrences are surfaced via unrecognized_markers, not here
        canonical = match[0]
        if canonical not in by_marker:
            by_marker[canonical] = []
            order.append(canonical)
        by_marker[canonical].append(occ)

    reconciled = []

    for canonical in order:
        # A genuine "not_performed" occurrence always carries a null value and empty
        # disp_value by contract (see extraction_prompt.py); an occurrence with a real
        # value/disp_value is an actual result regardless of what its status field says,
        # so eligibility is decided by the presence of that data, not the status label.
        dated_results = [occ for occ in by_marker[canonical]
                         if _normalize_date_for_matching(occ.get("date_display", ""))
                         and (occ.get("value") is not None or occ.get("disp_value"))]
        dated_results.sort(
            key=lambda occ: _normalize_date_for_matching(occ.get("date_display", "")),
        )
        unique_results = []
        seen = set()
        for occ in dated_results:
            signature = (
                _normalize_date_for_matching(occ.get("date_display", "")),
                occ.get("value"),
                occ.get("disp_value") if occ.get("value") is None else None,
            )
            if signature not in seen:
                seen.add(signature)
                unique_results.append(occ)

        if len(unique_results) == 1 and report_current_key is not None:
            only_key = _normalize_date_for_matching(unique_results[0].get("date_display", ""))
            if only_key != report_current_key:
                now_occ = None
                then_occ = unique_results[0]
            else:
                now_occ = unique_results[0]
                then_occ = None
        else:
            then_occ = unique_results[0] if len(unique_results) > 1 else None
            now_occ = unique_results[-1] if unique_results else None
        history = [{
            "date_display": occ.get("date_display", ""),
            "value": occ.get("value") if occ.get("value") is not None else 0,
            "disp_value": occ.get("disp_value", ""),
        } for occ in unique_results[1:-1]]

        reconciled.append({
            "name": canonical,
            "then": then_occ.get("value") if then_occ else None,
            "disp_then": then_occ.get("disp_value") if then_occ else None,
            "then_date_display": then_occ.get("date_display", "") if then_occ else "",
            "now": now_occ.get("value") if now_occ else None,
            "disp_now": now_occ.get("disp_value") if now_occ else "",
            "now_date_display": now_occ.get("date_display", "") if now_occ else "",
            "is_good_then": then_occ.get("is_good") if then_occ else None,
            "is_good_now": now_occ.get("is_good") if now_occ else None,
            "lab_range_then_lo": then_occ.get("lab_range_lo", 0) if then_occ else 0,
            "lab_range_then_hi": then_occ.get("lab_range_hi", 0) if then_occ else 0,
            "lab_range_then_display": then_occ.get("lab_range_display", "") if then_occ else "",
            "lab_range_now_lo": now_occ.get("lab_range_lo", 0) if now_occ else 0,
            "lab_range_now_hi": now_occ.get("lab_range_hi", 0) if now_occ else 0,
            "lab_range_now_display": now_occ.get("lab_range_display", "") if now_occ else "",
            "full_history": history,
        })
    return reconciled


def sanitize_text(s: str) -> str:
    s = s.replace(" — ", "; ")
    s = s.replace("—", ", ")
    return s


def _sanitize_em_dashes(value):
    """Return a recursively sanitized copy of generated JSON-compatible data."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {key: _sanitize_em_dashes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_em_dashes(item) for item in value]
    return value


def _marker_list_placeholders(record: PatientRecord) -> dict[str, str]:
    """Build marker-list tokens from deterministic scores, never generated prose."""
    return {
        "{optimal_markers}": ", ".join(marker.name for marker in record.markers if marker.now_tier == "optimal"),
        "{moderate_markers}": ", ".join(marker.name for marker in record.markers if marker.now_tier == "moderate"),
        "{flagged_markers}": ", ".join(marker.name for marker in record.markers if marker.now_tier == "flag"),
    }


def _substitute_marker_list_placeholders(value, placeholders):
    if isinstance(value, str):
        for token, marker_list in placeholders.items():
            value = value.replace(token, marker_list)
        return value
    if isinstance(value, dict):
        return {key: _substitute_marker_list_placeholders(item, placeholders)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_marker_list_placeholders(item, placeholders) for item in value]
    return value


def _extract_dexa_scan_images(dexa_pdfs: list[str]) -> list[tuple[str, str, int, str, int]]:
    """Render page 1 of the first DEXA PDF as the scan image."""
    images = []
    with open("/tmp/dexa_page_images.txt", "w", encoding="utf-8") as report:
        if not dexa_pdfs:
            return images

        pdf_path = dexa_pdfs[0]
        with fitz.open(pdf_path) as document:
            if not document:
                report.write(f"file={pdf_path} selection=none reason=empty-document\n")
                return images

            page_number = 1
            page = document[0]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image_bytes = pixmap.tobytes("png")
            crop_source = "full rendered page 1"
            report.write(f"file={pdf_path} page={page_number} source={crop_source}\n")
            png_path = "/tmp/dexa_scan_page_1.png"
            with open(png_path, "wb") as image_file:
                image_file.write(image_bytes)
            file_size = os.path.getsize(png_path)
            report.write(f"file={pdf_path} page={page_number} png={png_path} "
                         f"source={crop_source} size_kb={file_size / 1024:.1f}\n")
            print(f"DEXA scan page: file={pdf_path} page={page_number} "
                  f"source={crop_source} size_kb={file_size / 1024:.1f}")
            encoded = base64.b64encode(image_bytes).decode("ascii")
            images.append((encoded, pdf_path, page_number, png_path, file_size))
    return images


def _review_notes_path(patient_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", patient_name.strip()).strip("._") or "patient"
    return f"/tmp/{safe_name}_review_notes.txt"


def _diagnostic_path_prefix(patient_name: str | None) -> str:
    """A concurrent-request-safe filename stem: the gthread worker config runs multiple requests
    in the same process at once, and the previous fixed '/tmp/generate_copy_...' filenames could
    be overwritten mid-request by a second, unrelated report generating at the same time."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", (patient_name or "").strip()).strip("._") or "patient"
    return f"/tmp/{safe_name}"


def _write_review_notes(patient_name: str, notice: ExtractionReviewNotice) -> str:
    path = _review_notes_path(patient_name)
    text = format_review_notice(notice)
    with open(path, "w", encoding="utf-8") as review_file:
        review_file.write(text or "No internal QA items were generated.\n")
    return path


def _parse_json_response(text, patient_name=None):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    if not text:
        return {}
    if not text.startswith("{"):
        # Model prefaced the JSON with reasoning/prose despite instructions not to - salvage the
        # actual object rather than feeding the whole prose blob to the parser/repair library.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            repaired = repair_json(text)
            return json.loads(repaired)
        except ValueError:
            # json.JSONDecodeError is itself a ValueError, so this also covers a
            # failed re-parse of the "repaired" text, not just repair_json() itself.
            log_line = (f"ERROR: JSON parse failure for patient={patient_name or 'unknown'} "
                        f"raw_length={len(text)} first_200={text[:200]!r}")
            with open("/tmp/extraction_completeness_log.txt", "a", encoding="utf-8") as log:
                log.write(log_line + "\n")
            raise ValueError("Extraction produced unparseable output - please retry") from None


def _pdf_content_block(path: str) -> dict:
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}


def _pdf_text(path: str | None) -> str:
    if not path:
        return ""
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


_PRINTED_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4})(?!\d)", re.IGNORECASE
)
_COLLECTION_DATE_RE = re.compile(
    r"(?:collected|collection|specimen|draw|service|received)[^\n:]{0,35}[:#]?\s*"
    r"(" + _PRINTED_DATE_RE.pattern + r")", re.IGNORECASE
)


def _source_label_tokens(source_label: str) -> set[str]:
    ignored = {"report", "panel", "section", "current", "historical", "lab"}
    return {token for token in re.findall(r"[a-z0-9]+", source_label.lower())
            if len(token) >= 3 and token not in ignored}


def _report_collection_dates(lab_text: str, source_labels: set[str]) -> dict[str, set[str]]:
    """Find literal report-level collection dates, keyed by extracted source label.

    This is deliberately conservative: a date is attached only when a collection/specimen
    keyword is printed near a source-label match. It never chooses among unrelated dates.
    """
    lines = lab_text.splitlines()
    label_dates: dict[str, set[str]] = {}
    for label in source_labels:
        tokens = _source_label_tokens(label)
        if not tokens:
            continue
        matching_lines = [index for index, line in enumerate(lines)
                          if len(tokens & set(re.findall(r"[a-z0-9]+", line.lower()))) >= max(1, len(tokens) // 2)]
        candidates = set()
        for index in matching_lines:
            window = "\n".join(lines[max(0, index - 12):min(len(lines), index + 25)])
            candidates.update(match.group(1) for match in _COLLECTION_DATE_RE.finditer(window))
        if candidates:
            label_dates[label] = candidates
    return label_dates


def _attach_report_collection_dates(extracted: dict, lab_text: str) -> None:
    """Attach a report's printed collection date to undated occurrences from that report."""
    occurrences = extracted.get("marker_occurrences", [])
    labels = {occurrence.get("source_label", "") for occurrence in occurrences
              if not occurrence.get("date_display") and occurrence.get("source_label")}
    label_dates = _report_collection_dates(lab_text, labels)
    for occurrence in occurrences:
        if occurrence.get("date_display"):
            continue
        dates = label_dates.get(occurrence.get("source_label", ""), set())
        if len(dates) == 1:
            occurrence["date_display"] = next(iter(dates))


def _nearby_cadence(note_text: str, start: int, end: int) -> str | None:
    window_start = max(0, start - 40)
    window = note_text[window_start:min(len(note_text), end + 40)].lower()
    matches = list(re.finditer(r"\b(daily|weekly|as directed)\b", window))
    if not matches:
        return None
    mention_center = ((start - window_start) + (end - window_start)) / 2
    nearest = min(matches, key=lambda match: abs(match.start() - mention_center))
    return nearest.group(1)


def verify_extraction_completeness(extracted: dict, provider_note_text: str = "",
                                   lab_text: str = "", dexa_text: str = "") -> ExtractionReviewNotice:
    """Verify model omissions against source text without inventing lab values."""
    notice = ExtractionReviewNotice()
    log_lines = []
    note_lower = provider_note_text.lower()
    extracted_protocols = set()
    for item in extracted.get("protocol", []):
        name = item if isinstance(item, str) else item.get("name") or item.get("item") or ""
        match = lookup_protocol_item(name)
        extracted_protocols.add(match[0] if match else name.lower())

    for canonical, config in PROTOCOL_LIBRARY.items():
        for name in [canonical, *config.get("aliases", [])]:
            start = note_lower.find(name.lower())
            if start == -1:
                continue
            if canonical in extracted_protocols:
                break
            cadence = _nearby_cadence(provider_note_text, start, start + len(name))
            extracted.setdefault("protocol", []).append({
                "name": canonical,
                "cadence": cadence,
                "target_categories": config.get("typical_categories", []),
                "lab_visible": config.get("lab_visible", True),
            })
            log_lines.append(
                f"ADDED MISSING PROTOCOL ITEM: {canonical} (cadence: {cadence or 'none found'})"
            )
            extracted_protocols.add(canonical)
            break

    extracted_markers = set()
    reconciled_markers = reconcile_marker_occurrences(extracted.get("marker_occurrences", []))
    combined_markers = reconciled_markers + extracted.get("markers", [])
    for marker in combined_markers:
        match = markers_reference_lookup(marker.get("name", ""))
        if match:
            extracted_markers.add(match[0])
    lab_lower = lab_text.lower()
    for canonical, config in MARKER_LIBRARY.items():
        names = [canonical, *config.get("aliases", [])]
        source_positions = set()
        for name in names:
            source_positions.update(match.start() for match in re.finditer(
                rf"(?<!\w){re.escape(name.lower())}(?!\w)", lab_lower
            ))
        source_mentions = len(source_positions)
        if not source_mentions:
            continue
        if canonical not in extracted_markers:
            warning = (f"WARNING: MARKER '{canonical}' FOUND IN SOURCE BUT MISSING FROM EXTRACTION - "
                       "NEEDS HUMAN REVIEW")
            notice.other_notes.append(warning)
            log_lines.append(warning)
            continue
        extracted_marker = next(
            marker for marker in combined_markers
            if (match := markers_reference_lookup(marker.get("name", ""))) and match[0] == canonical
        )
        if source_mentions >= 2 and extracted_marker.get("now") is None:
            warning = (f"WARNING: MARKER '{canonical}' APPEARS {source_mentions} TIMES IN SOURCE BUT "
                       "HAS NO LATEST-DRAW VALUE IN EXTRACTION - NEEDS HUMAN REVIEW")
            notice.other_notes.append(warning)
            log_lines.append(warning)

    dexa_entries = extracted.get("dexa_history", [])
    if dexa_entries:
        first = dexa_entries[0]
        value = str(first.get("body_fat_pct") or "")
        date = str(first.get("date_display") or "")
        numeric = value.rstrip("%").strip()
        date_match = dexa_text.lower().find(date.lower()) if date else -1
        context = dexa_text[max(0, date_match - 500):date_match + 500] if date_match >= 0 else dexa_text
        if numeric and numeric not in context and value not in context:
            warning = (f"WARNING: DEXA BODY FAT % FOR {date} = {value} NOT FOUND VERBATIM IN SOURCE PDF - "
                       "POSSIBLE HALLUCINATION, NEEDS HUMAN REVIEW")
            notice.other_notes.append(warning)
            log_lines.append(warning)

    if log_lines:
        with open("/tmp/extraction_completeness_log.txt", "a", encoding="utf-8") as log:
            log.write("\n".join(log_lines) + "\n")
    return notice


def _marker_library_summary() -> str:
    lines = []
    for name, cfg in MARKER_LIBRARY.items():
        aliases = ", ".join(cfg.get("aliases", []))
        lines.append(f"- {name} (aliases: {aliases})")
    return "\n".join(lines)


def _protocol_library_summary() -> str:
    lines = []
    for name, cfg in PROTOCOL_LIBRARY.items():
        aliases = ", ".join(cfg.get("aliases", []))
        cats = ", ".join(cfg["typical_categories"]) or "none (not expected to move labs)"
        lines.append(f"- {name} (aliases: {aliases}) — typical target(s): {cats}")
    return "\n".join(lines)


def extract(client: Anthropic, labs_pdf: str | None, dexa_pdfs: list[str], note_text: str | None,
            patient_name: str | None = None) -> dict:
    """Step 1: raw material in, structured (but not yet scored or written) JSON out."""
    content = []
    if labs_pdf:
        content.append(_pdf_content_block(labs_pdf))
    for p in dexa_pdfs:
        content.append(_pdf_content_block(p))
    if labs_pdf:
        content.append({
            "type": "text",
            "text": (
                "LITERAL TEXT EXTRACTED FROM THE LAB PDF. Use this as a transcription aid, especially "
                "for report-level specimen dates and dates printed once in Historical/Current column headers. "
                "Do not infer or reconcile values from it; copy only what the source prints.\n\n"
                + _pdf_text(labs_pdf)
            ),
        })
    content.append({
        "type": "text",
        "text": build_extraction_user_message(_marker_library_summary(), _protocol_library_summary(), note_text),
    })

    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_OUTPUT_SCHEMA}},
    )
    text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
    raw_text = "".join(text_blocks)
    with open("/tmp/last_extraction_raw.txt", "w") as f:
        f.write(raw_text)
    parsed = _parse_json_response(raw_text, patient_name=patient_name)
    if labs_pdf:
        _attach_report_collection_dates(parsed, _pdf_text(labs_pdf))
    # exactly what the model returned for every marker occurrence, before reconciliation ever
    # runs - the ground truth needed to confirm (not assume) how a marker like Cortisol was
    # actually extracted, instead of reconstructing it from a guess
    with open("/tmp/last_extraction_markers_raw.json", "w", encoding="utf-8") as f:
        json.dump(parsed.get("marker_occurrences", []), f, indent=2, ensure_ascii=False)
    return parsed


_LAB_RANGE_TRIOS = [
    ("lab_range_then_lo", "lab_range_then_hi", "lab_range_then_display"),
    ("lab_range_now_lo", "lab_range_now_hi", "lab_range_now_display"),
]
_LAB_RANGE_FIELDS = {field for trio in _LAB_RANGE_TRIOS for field in trio}


def _dedupe_extracted_markers(raw_markers: list[dict]) -> tuple[list[dict], list[str]]:
    """Collapse multiple extracted mentions of the same canonical marker into exactly one entry.

    A single source lab PDF can name the same marker two different ways in two different
    sections (e.g. "Vitamin D" on one panel and "Vitamin D, 25-Hydroxy" on another), and each
    resolves to the same canonical marker via markers_reference.lookup_marker's alias matching.
    Without this step, both extracted mentions would reach score_and_build_record as separate
    Marker objects and render as duplicate rows - this is a generalized fix (grouped by
    resolved canonical name, not a per-marker patch) so it applies to every alias in the library,
    not just the ones seen in any one report.

    If the mentions agree (or one simply fills in a field the other left null), they're merged
    into a single entry. If they genuinely conflict on "then" or "now", this never silently picks
    one as correct - both values are logged as a review item and the first mention is kept so the
    report still renders, exactly like every other "flag it, don't guess" safeguard in this file.
    """
    groups: dict = {}
    order = []
    for raw in raw_markers:
        match = markers_reference_lookup(raw.get("name", ""))
        key = match[0] if match else id(raw)  # unrecognized markers are never merged with each other
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(raw)

    deduped = []
    conflict_notes = []
    for key in order:
        entries = groups[key]
        if len(entries) == 1 or not isinstance(key, str):
            deduped.extend(entries)
            continue
        conflicts = []
        for field in ("then", "now"):
            values = {entry.get(field) for entry in entries if entry.get(field) is not None}
            if len(values) > 1:
                conflicts.append(f"{field}={sorted(values)}")
        if conflicts:
            detail = "; ".join(conflicts)
            conflict_notes.append(
                f"WARNING: MARKER '{key}' EXTRACTED {len(entries)} TIMES WITH CONFLICTING VALUES "
                f"({detail}) - NOT AUTO-RESOLVED, NEEDS HUMAN REVIEW"
            )
            deduped.append(entries[0])
            continue
        merged = dict(entries[0])
        for other in entries[1:]:
            for field_name, value in other.items():
                if field_name in _LAB_RANGE_FIELDS:
                    continue  # handled as a whole lo/hi/display trio below, not field-by-field
                if merged.get(field_name) in (None, "") and value not in (None, ""):
                    merged[field_name] = value
            # lab_range_{then,now}_lo/hi/display is a sentinel unit, not independently-nullable
            # fields: "no printed range" is encoded as lo=0, hi=0, display="" (see
            # extraction_prompt.py), so a per-field None/"" gap-fill would never adopt a real
            # range from another duplicate mention whenever the first mention's lo/hi happen to
            # already be the 0 sentinel - exactly what silently dropped Cortisol's printed range
            # after it was extracted twice (once per alias) with the range on only one mention.
            for lo_field, hi_field, display_field in _LAB_RANGE_TRIOS:
                if not merged.get(display_field) and other.get(display_field):
                    merged[lo_field] = other.get(lo_field)
                    merged[hi_field] = other.get(hi_field)
                    merged[display_field] = other.get(display_field)
        deduped.append(merged)
    return deduped, conflict_notes


def score_and_build_record(extracted: dict) -> tuple[PatientRecord, ExtractionReviewNotice]:
    """Step 2: deterministic. No AI. Takes extraction's structured output, matches markers against
    the reference library, computes tiers/percentages via scoring.py (the same math as data.py),
    and assembles the final PatientRecord. This is where 'never infer' is enforced in code, not
    just in a prompt — an unmatched marker CANNOT reach the record with a guessed threshold.

    Sex-conditional defaults (e.g. Testosterone, Total) are resolved here via
    markers_reference.resolve_marker_config, using the patient's own sex - never guessed.

    Per-patient provider-note overrides (extraction-only, see extraction_prompt.py) take
    precedence over the sex-based default for that one marker, and are logged to the existing
    extraction_completeness_log.txt so the override is auditable, not silent.

    Safeguard: if a marker's resolved config is missing a required threshold for its kind (a
    data error in the library, or an unresolved sex lookup), that ONE marker is excluded from
    scoring and logged - it never crashes the whole report."""
    notice = ExtractionReviewNotice()
    markers = []
    patient_sex = extracted.get("sex") or None
    postmenopausal_bhrt, on_trt = parse_provider_statuses(extracted.get("provider_note_raw"))
    scoring_log_lines = []

    overrides_by_marker = {}
    for raw_override in extracted.get("marker_overrides", []):
        match = markers_reference_lookup(raw_override.get("marker", ""))
        if match is None:
            continue
        canonical, _ = match
        lo, hi = raw_override.get("lo"), raw_override.get("hi")
        if lo is None or hi is None:
            continue
        overrides_by_marker[canonical] = (lo, hi)

    # dump immediately before dedup/merge runs, so a regression can be diagnosed against what
    # the model actually returned instead of what the dedup logic assumed it returned
    with open("/tmp/pre_dedup_markers_raw.json", "w", encoding="utf-8") as f:
        json.dump({"markers": extracted.get("markers", []),
                    "marker_occurrences": extracted.get("marker_occurrences", [])},
                   f, indent=2, ensure_ascii=False)
    # Primary path: raw per-occurrence mentions (see extraction_prompt.py), reconciled here in
    # code rather than trusted to a single model pass - this is what correctly resolves a marker
    # whose only real value sits in a secondary report while the primary report is silent or says
    # "not performed" (see reconcile_marker_occurrences docstring). Legacy "markers" (already
    # pre-collapsed then/now entries, used by callers that never went through the occurrence-level
    # extraction) still runs through the older alias-collapsing dedupe below so nothing that
    # depended on that shape breaks.
    reconciled_markers = reconcile_marker_occurrences(extracted.get("marker_occurrences", []))
    legacy_deduped, legacy_notes = _dedupe_extracted_markers(extracted.get("markers", []))
    deduped_markers = reconciled_markers + legacy_deduped
    dedupe_notes = legacy_notes
    notice.other_notes.extend(dedupe_notes)
    scoring_log_lines.extend(dedupe_notes)
    with open("/tmp/post_dedup_markers.json", "w", encoding="utf-8") as f:
        json.dump(deduped_markers, f, indent=2, ensure_ascii=False)

    for raw in deduped_markers:
        match = markers_reference_lookup(raw["name"])
        if match is None:
            notice.unrecognized_markers.append(UnrecognizedMarker(
                raw_name=raw["name"], raw_value=str(raw.get("now", "")),
                raw_unit=raw.get("unit"), raw_range=raw.get("disp_range"),
            ))
            continue
        canonical, cfg = match
        cfg = resolve_marker_config(canonical, cfg, patient_sex, postmenopausal_bhrt=postmenopausal_bhrt)
        override = overrides_by_marker.get(canonical)
        lab_range_then = _valid_lab_range(raw, "then")
        lab_range = _valid_lab_range(raw, "now")
        active_lab_range = lab_range or lab_range_then
        missing_threshold = override is None and has_missing_thresholds(cfg)
        lab_range_fallback = missing_threshold and active_lab_range is not None
        if missing_threshold:
            # Keep the real lab result visible; scoring remains explicitly unscored until configured.
            error_line = (f"ERROR: missing threshold for {canonical} / {patient_sex or 'unknown'} "
                          + ("- using printed lab range" if lab_range_fallback else "- marker retained as unscored"))
            scoring_log_lines.append(error_line)
            notice.other_notes.append(error_line)
        if lab_range_fallback:
            cfg = dict(cfg, kind="range", lo=active_lab_range["lo"], hi=active_lab_range["hi"],
                       disp_range=active_lab_range["display"])
            missing_threshold = False
        if override is not None:
            lo, hi = override
            # Provider-note override always scores as a "range" band around the stated optimal
            # window, regardless of the marker's normal scoring kind - it replaces the default
            # threshold for this one patient/marker only, never the library default itself.
            m = Marker(
                name=canonical, category=cfg["category"], unit=cfg["unit"], kind="range",
                disp_range=f"{lo}\u2013{hi} (provider override)",
                lo=lo, hi=hi,
                then=raw.get("then"), now=raw.get("now"),
                disp_then=raw.get("disp_then"), disp_now=raw.get("disp_now"),
                then_date_display=raw.get("then_date_display"),
                now_date_display=raw.get("now_date_display"),
                is_good_then=raw.get("is_good_then"), is_good_now=raw.get("is_good_now"),
                full_history=raw.get("full_history", []),
            )
            scoring_log_lines.append(f"OVERRIDE APPLIED: {canonical} range set to {lo}-{hi} per provider note")
        else:
            m = Marker(
                name=canonical, category=cfg["category"], unit=cfg["unit"], kind=cfg["kind"],
                disp_range=cfg["disp_range"],
                optimal=cfg.get("optimal"), moderate=cfg.get("moderate"), direction=cfg.get("direction"),
                inclusive=cfg.get("inclusive", True),
                lo=cfg.get("lo"), hi=cfg.get("hi"),
                suppress_low_on_trt=cfg.get("suppress_low_on_trt", False),
                unscored_reason="missing_threshold" if missing_threshold else None,
                range_source="lab" if lab_range_fallback else ("celldeep" if cfg.get("kind") == "range" else None),
                lab_range_now=lab_range,
                lab_range_then=lab_range_then,
                then_lo=lab_range_then["lo"] if lab_range_then else None,
                then_hi=lab_range_then["hi"] if lab_range_then else None,
                then=raw.get("then"), now=raw.get("now"),
                disp_then=raw.get("disp_then"), disp_now=raw.get("disp_now"),
                then_date_display=raw.get("then_date_display"),
                now_date_display=raw.get("now_date_display"),
                is_good_then=raw.get("is_good_then"), is_good_now=raw.get("is_good_now"),
                full_history=raw.get("full_history", []),
            )
        scoring.attach_scores(m, sex=patient_sex, on_trt=on_trt)   # computes now_tier/then_tier/pct in place — pure math, no AI
        markers.append(m)

    dexa_history = [DexaReading(**scoring.normalize_dexa_body_fat(d))
                    for d in extracted.get("dexa_history", [])]

    protocol = []
    for raw in extracted.get("protocol", []):
        if isinstance(raw, str):
            protocol.append(ProtocolItem(name=raw, cadence="as directed",
                                          target_categories=[], lab_visible=True))
        else:
            protocol.append(ProtocolItem(
                name=raw.get("name") or raw.get("item") or str(raw),
                cadence=raw.get("cadence"),
                target_categories=raw.get("target_categories", []),
                lab_visible=raw.get("lab_visible", True),
            ))

    pain_points = [PainPoint(text=p["text"], categories=p.get("categories", []))
                   for p in extracted.get("pain_points", [])]

    if scoring_log_lines:
        with open("/tmp/extraction_completeness_log.txt", "a", encoding="utf-8") as log:
            log.write("\n".join(scoring_log_lines) + "\n")

    record = PatientRecord(
        name=extracted.get("name") or None,
        age=extracted.get("age") or None,
        sex=patient_sex,
        postmenopausal_bhrt=postmenopausal_bhrt, on_trt=on_trt,
        first_draw_date=extracted.get("first_draw_date") or None,
        latest_draw_date=extracted.get("latest_draw_date") or None,
        markers=markers, dexa_history=dexa_history, protocol=protocol, pain_points=pain_points,
        cns_domains=extracted.get("cns_domains"), provider_note_raw=extracted.get("provider_note_raw"),
    )
    notice.other_notes.extend(extracted.get("other_notes", []))
    return record, notice


def markers_reference_lookup(raw_name: str):
    from markers_reference import lookup_marker
    return lookup_marker(raw_name)


# An LLM occasionally degrades mid-response and drops spaces between words for a stretch of text
# (never a data-value corruption, always prose) - a real word essentially never runs this long
# with no space, so this is a reliable enough signal to catch it without false-positiving on
# legitimate long marker/compound names.
_RUN_ON_WORD_RE = re.compile(r"[A-Za-z]{24,}")

# A single glued-together word is only the easy case: real corrupted text (see the actual Evan
# Walker report) is often broken back up into <24-char chunks by ordinary punctuation the text
# still contains ("hormone-binding", "globulin, a protein"), so a run-on passage can dodge the
# word-length check entirely while still having no spaces between its actual words. Real English
# prose runs roughly one space per 5-6 letters; anything this sparse over a long-enough sample is
# corrupted regardless of where the letter-runs happen to be broken by punctuation.
_MIN_SAMPLE_LETTERS = 40
_MAX_LETTERS_PER_SPACE = 12


def _is_run_on_text(text: str) -> bool:
    if _RUN_ON_WORD_RE.search(text):
        return True
    letters = sum(1 for c in text if c.isalpha())
    if letters < _MIN_SAMPLE_LETTERS:
        return False
    spaces = text.count(" ")
    return spaces == 0 or (letters / spaces) > _MAX_LETTERS_PER_SPACE


def _find_corrupted_text_paths(value, path: str = "") -> list[str]:
    """Recursively find generated string fields that look like they lost their spacing."""
    found = []
    if isinstance(value, str):
        if _is_run_on_text(value):
            found.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_find_corrupted_text_paths(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_corrupted_text_paths(item, f"{path}[{index}]"))
    return found


def _clear_path(data, path: str) -> None:
    """Blank out one corrupted field by its dotted/bracketed path rather than rendering garbled
    run-on text - used only as a last resort after retries have already failed."""
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    node = data
    for part in parts[:-1]:
        node = node[int(part[1:-1])] if part.startswith("[") else node[part]
    last = parts[-1]
    if last.startswith("["):
        node[int(last[1:-1])] = ""
    else:
        node[last] = ""


def _narrative_marker_payload(marker: Marker) -> dict:
    """Expose only values that survived deterministic reconciliation to the copywriter."""
    history = [entry for entry in marker.full_history
               if entry.get("date_display") and
               (entry.get("value") is not None or entry.get("disp_value"))]
    return {
        "name": marker.name,
        "category": marker.category,
        "unit": marker.unit,
        "kind": marker.kind,
        "disp_range": marker.disp_range,
        "then_value": marker.then,
        "then_display": marker.disp_then,
        "then_date_display": marker.then_date_display,
        "now_value": marker.now,
        "now_display": marker.disp_now,
        "now_date_display": marker.now_date_display,
        "history": history,
        "then_tier": marker.then_tier,
        "then_pct": marker.then_pct,
        "now_tier": marker.now_tier,
        "now_pct": marker.now_pct,
        "is_good_then": marker.is_good_then,
        "is_good_now": marker.is_good_now,
        "unscored_reason": marker.unscored_reason,
    }


def generate_copy(client: Anthropic, record: PatientRecord) -> tuple[dict, list[str]]:
    """Step 3: the interpretive writing pass. Takes the fully-scored PatientRecord (all numbers,
    all tiers already fixed by deterministic code) and generates the sentences that go around them,
    in V23's locked voice."""
    category_membership = {
        patient_category: [marker.name for marker in record.markers
                           if DATA_TO_PATIENT_CATEGORY.get(marker.category) == patient_category
                           or NARRATIVE_CATEGORY_OVERRIDE.get(marker.name) == patient_category]
        for patient_category in DATA_TO_PATIENT_CATEGORY.values()
    }
    # Structure isn't derived from any marker category (it comes from DEXA, not bloodwork), so
    # without an explicit entry the model has no membership signal for it at all - unlike every
    # other category, which at least gets an explicit empty list telling it "no markers here."
    category_membership["Structure"] = (["DEXA body composition scan"] if record.dexa_history else [])
    record_payload = asdict(record)
    record_payload["markers"] = [_narrative_marker_payload(marker) for marker in record.markers]
    payload = json.dumps({"record": record_payload, "patient_facing_category_membership": category_membership},
                         default=str, indent=2)
    warnings = []
    parsed = {}
    diag_prefix = _diagnostic_path_prefix(record.name)
    for attempt in range(2):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=GENERATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Here is the fully scored patient record and its category membership:\n\n{payload}\n\n"
                                                     "Generate the interpretive copy per the instructions."}],
        )
        text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
        raw_text = "".join(text_blocks)
        with open("/tmp/pre_sanitize_copy.json", "w", encoding="utf-8") as f:
            f.write(raw_text)
        # per-attempt, per-patient, never overwritten by a concurrent request for a different
        # patient - the exact raw API response text, untouched by parsing or detection, so a
        # real-data corruption case can be inspected after the fact instead of reconstructed
        with open(f"{diag_prefix}_generate_copy_attempt{attempt}_raw.txt", "w", encoding="utf-8") as f:
            f.write(raw_text)
        parsed = _parse_json_response(raw_text, patient_name=record.name)
        corrupted_paths = _find_corrupted_text_paths(parsed)
        # printed (not just written to /tmp, which is ephemeral per-dyno and not user-visible on
        # Render) so this shows up in the live service's actual log stream for every real request,
        # and stop_reason is included since a truncated response (hit max_tokens mid-generation)
        # is a distinct, rule-out-able cause of malformed text that isn't a spacing bug at all
        detection_line = (f"COPY-CORRUPTION-DETECTION patient={record.name!r} attempt={attempt} "
                           f"stop_reason={getattr(resp, 'stop_reason', None)!r} "
                           f"response_chars={len(raw_text)} corrupted_paths={corrupted_paths!r}")
        print(detection_line)
        with open(f"{diag_prefix}_generate_copy_detection_log.txt", "a", encoding="utf-8") as log:
            log.write(detection_line + "\n")
        if not corrupted_paths:
            # deliberately NOT added to `warnings`/other_notes: that drives the "a few items need
            # review" banner in the downloadable notes, and a clean detection pass is not an item
            # needing review - the stdout/print line above is the trace for this case, visible in
            # Render's log stream, without turning every ordinary clean report into a false flag
            return parsed, warnings
        if attempt == 0:
            continue  # one resample is usually enough to clear a stochastic formatting glitch
        for path in corrupted_paths:
            _clear_path(parsed, path)
        warnings.append(
            "WARNING: GENERATED COPY HAD RUN-ON/UNSPACED TEXT THAT SURVIVED A RETRY - FIELDS "
            f"BLANKED RATHER THAN RENDERED GARBLED: {', '.join(corrupted_paths)} - NEEDS HUMAN REVIEW"
        )
    return parsed, warnings


def run(labs_pdf, dexa_pdfs, note_text, patient_name, age, sex, out_path):
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    raw_lab_text = _pdf_text(labs_pdf)
    raw_dexa_text = "\n".join(_pdf_text(path) for path in dexa_pdfs)

    print("Step 1/3: extracting raw material...")
    extracted = extract(client, labs_pdf, dexa_pdfs, note_text, patient_name=patient_name)
    extracted.setdefault("name", patient_name)
    extracted.setdefault("age", age)
    extracted.setdefault("sex", sex)
    extracted["provider_note_raw"] = note_text
    completeness_notice = verify_extraction_completeness(
        extracted, provider_note_text=note_text or extracted.get("provider_note_raw", ""),
        lab_text=raw_lab_text, dexa_text=raw_dexa_text,
    )

    print("Step 2/3: scoring (deterministic, no AI)...")
    record, notice = score_and_build_record(extracted)
    notice.other_notes.extend(completeness_notice.other_notes)

    print("Step 3/3: generating interpretive copy...")
    raw_copy, generation_warnings = generate_copy(client, record)
    notice.other_notes.extend(generation_warnings)
    copy = _sanitize_em_dashes(raw_copy)
    copy = _substitute_marker_list_placeholders(copy, _marker_list_placeholders(record))
    with open("/tmp/post_sanitize_copy.json", "w", encoding="utf-8") as f:
        json.dump(copy, f, indent=2, ensure_ascii=False)

    dexa_images = _extract_dexa_scan_images(dexa_pdfs)
    first_image = dexa_images[0] if dexa_images else None
    if first_image and record.dexa_history:
        record.dexa_history[0].scan_image_b64 = first_image[0]
    dexa_img_b64 = first_image[0] if first_image else None

    print("Rendering PDF...")
    template.render(record, copy, out_path, dexa_img_b64=dexa_img_b64)

    review_path = _write_review_notes(patient_name, notice)
    review = format_review_notice(notice)
    if review:
        print("\n" + review)
    print(f"\nDone. Report saved to {out_path}")
    print(f"Internal QA notes saved to {review_path}")
    return review_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--labs", help="path to bloodwork lab PDF")
    ap.add_argument("--dexa", nargs="*", default=[], help="path(s) to DEXA scan PDF(s)")
    ap.add_argument("--note", help="path to a text file containing the provider's note")
    ap.add_argument("--patient-name", required=True)
    ap.add_argument("--age", type=int)
    ap.add_argument("--sex", choices=["male", "female"])
    ap.add_argument("--out", default="report.pdf")
    args = ap.parse_args()

    note_text = None
    if args.note:
        with open(args.note) as f:
            note_text = f.read()

    run(args.labs, args.dexa, note_text, args.patient_name, args.age, args.sex, args.out)
