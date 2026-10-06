"""
CellDeep Report Generator — Pipeline Orchestrator
=====================================================
This is the actual entry point. Run this with raw patient material and it
produces the finished PDF, following the locked V23 design end to end.

Bloodwork tables are read deterministically from the PDF text layer by
row/column position, provider notes must follow the structured template, and
patient-facing copy is template fill (generation_prompt.py). DEXA PDFs still
go through the original Claude extraction call.

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
import tempfile
import uuid
import time
from datetime import date, datetime, timezone
from pathlib import Path

from anthropic import APIConnectionError, APITimeoutError, Anthropic, RateLimitError
from json_repair import repair_json
import fitz

from schema import PatientRecord, Marker, DexaReading, ProtocolItem, PainPoint, LabReportedResult
from schema import normalize_date_for_matching as _normalize_date_for_matching
from markers_reference import MARKER_LIBRARY, resolve_marker_config, has_missing_thresholds
from protocol_reference import PROTOCOL_LIBRARY, lookup_protocol_item
from unknown_marker_policy import STAFF_CHECK_END, UnrecognizedMarker, ExtractionReviewNotice, format_review_notice
from generation_prompt import build_copy
import clinic_config
import lab_reported
import scoring
from dexa_reference import dexa_percent_optimized
import template

if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

MODEL = "claude-sonnet-4-6"
ANTHROPIC_CALL_TIMEOUT_SECONDS = 240.0
ANTHROPIC_MAX_RETRIES = 1


class ExtractionOccurrenceValidationError(RuntimeError):
    """The source contains a dated marker result that extraction omitted."""


class ExtractionValueMismatchError(ExtractionOccurrenceValidationError):
    """A marker_occurrence's disp_value doesn't match any result token on its own source evidence line."""


class BloodworkParseError(RuntimeError):
    """A recognized bloodwork row or section could not be read under the column rule."""


class BloodworkHardStop(BloodworkParseError):
    """A parse failure that must stop the whole report: conflicting duplicate results or a
    contradictory identity (e.g. one Order ID printing two Collected dates). Every other
    BloodworkParseError excludes only the page or section it came from."""


class AnthropicAPIError(RuntimeError):
    """An Anthropic API request could not complete."""


def _log_anthropic_call_end(operation: str, outcome: str, start_time: datetime,
                            start_monotonic: float, configured_timeout, error: Exception | None = None) -> None:
    end_time = datetime.now(timezone.utc)
    duration_seconds = time.monotonic() - start_monotonic
    error_type = type(error).__name__ if error else None
    print(
        "ANTHROPIC-API-CALL-END "
        f"operation={operation!r} outcome={outcome} start_time={start_time.isoformat()} "
        f"end_time={end_time.isoformat()} duration_seconds={duration_seconds:.3f} "
        f"configured_timeout={configured_timeout!r} error_type={error_type!r}"
    )


def _create_anthropic_message(client: Anthropic, operation: str, **kwargs):
    start_time = datetime.now(timezone.utc)
    start_monotonic = time.monotonic()
    configured_timeout = getattr(client, "timeout", None)
    print(
        "ANTHROPIC-API-CALL-START "
        f"operation={operation!r} start_time={start_time.isoformat()} "
        f"configured_timeout={configured_timeout!r}"
    )
    try:
        response = client.messages.create(**kwargs)
    except APITimeoutError as error:
        _log_anthropic_call_end(
            operation, "sdk_timeout", start_time, start_monotonic, configured_timeout, error
        )
        raise AnthropicAPIError(
            f"Anthropic API timed out during {operation}. Please retry the report."
        ) from error
    except RateLimitError as error:
        _log_anthropic_call_end(
            operation, "exception", start_time, start_monotonic, configured_timeout, error
        )
        raise AnthropicAPIError(
            f"Anthropic API rate limit reached during {operation}. Please retry shortly."
        ) from error
    except APIConnectionError as error:
        _log_anthropic_call_end(
            operation, "exception", start_time, start_monotonic, configured_timeout, error
        )
        raise AnthropicAPIError(
            f"Could not connect to the Anthropic API during {operation}. Please retry the report."
        ) from error
    except Exception as error:
        _log_anthropic_call_end(
            operation, "exception", start_time, start_monotonic, configured_timeout, error
        )
        raise
    _log_anthropic_call_end(
        operation, "completed", start_time, start_monotonic, configured_timeout
    )
    return response


class ProviderNoteFormatError(ValueError):
    """A provider note does not follow the structured provider-notes template."""


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


def provider_statuses(extracted: dict, notice=None) -> tuple[bool | None, bool | None]:
    """The '## Treatment Status' section when the note has one; otherwise only explicit statements in
    the accepted note text. Returns (postmenopausal_bhrt, on_trt)."""
    stated = parse_provider_statuses(extracted.get("provider_note_raw"))
    section = extracted.get("treatment_status")
    if section is None:
        return stated
    chosen = (section.get("postmenopausal_bhrt"), section.get("on_trt"))
    for label, text_value, section_value in zip(("postmenopausal BHRT", "TRT"), stated, chosen):
        if notice is not None and text_value is True and section_value is not True:
            notice.other_notes.append(f"TREATMENT STATUS: the note text mentions {label}, but the Treatment Status "
                                      "section does not say Yes; the section is used - confirm with the provider")
    return chosen


def _valid_lab_range(raw: dict, draw: str) -> dict | None:
    lo = raw.get(f"lab_range_{draw}_lo")
    hi = raw.get(f"lab_range_{draw}_hi")
    display = raw.get(f"lab_range_{draw}_display")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or not isinstance(display, str):
        return None
    if not display or lo > hi:
        return None
    return {"lo": lo, "hi": hi, "display": display}


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
            **({"lab_flag": occ["lab_flag"]} if occ.get("lab_flag") else {}),
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
            "lab_flag_then": (then_occ.get("lab_flag") or None) if then_occ else None,
            "lab_flag_now": (now_occ.get("lab_flag") or None) if now_occ else None,
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
            del pixmap
            fitz.TOOLS.store_shrink(100)
            crop_source = "full rendered page 1"
            file_size = len(image_bytes)  # no PNG copy is written to /tmp
            report.write(f"file={pdf_path} page={page_number} "
                         f"source={crop_source} size_kb={file_size / 1024:.1f}\n")
            print(f"DEXA scan page: file={pdf_path} page={page_number} "
                  f"source={crop_source} size_kb={file_size / 1024:.1f}")
            encoded = base64.b64encode(image_bytes).decode("ascii")
            del image_bytes
            images.append((encoded, pdf_path, page_number, None, file_size))
    return images


def _review_notes_path(patient_name: str) -> str:
    """A unique path that does not contain the patient's name (paths end up in logs)."""
    return f"/tmp/celldeep_review_notes_{uuid.uuid4().hex}.txt"


def _diagnostic_path_prefix(patient_name: str | None) -> str:
    """A concurrent-request-safe, name-free filename stem: several reports can generate at once in one
    process, and file paths end up in logs, so they never contain the patient's name."""
    return f"/tmp/celldeep_{uuid.uuid4().hex}"


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


def _pdf_text(path: str | None) -> str:
    if not path:
        return ""
    with fitz.open(path) as document:
        return "\n".join(_page_text(page) for page in document)


def _page_text(page) -> str:
    text = page.get_text()
    fitz.TOOLS.store_shrink(100)  # see _page_words: do not keep scanned pages' decoded images cached
    return text


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


_RESULT_TOKEN_RE = re.compile(
    r"(?:[<>]=?\s*)?\d+(?:\.\d+)?|\b(?:negative|positive|detected|not detected|"
    r"not performed|cancelled|canceled)\b",
    re.IGNORECASE,
)
_DATE_HEADER_RE = re.compile(
    r"\b(?:collected|collection|specimen|draw|current|historical|previous|result date|report date)\b",
    re.IGNORECASE,
)
_BIRTH_DATE_RE = re.compile(r"\b(?:date of birth|birth date|dob)\b", re.IGNORECASE)
_MAX_RESULT_EVIDENCE_LINE_LENGTH = 240
_FOOTNOTE_EXCLUSION_PHRASES = (
    "effective",
    "this test was performed",
    "please refer to",
    "validated pursuant to",
    "this assay",
    "for additional information",
    "compared to historical results",
    "developed and its analytical performance",
)


def _has_numeric_result_with_unit(result_text: str, unit: str) -> bool:
    if not unit:
        return False
    result_pattern = re.compile(
        r"(?:[<>]=?\s*)?\d+(?:\.\d+)?\s*" + re.escape(unit) + r"(?!\w)",
        re.IGNORECASE,
    )
    return bool(result_pattern.search(result_text))


def _is_footnote_or_methodology_text(text: str) -> bool:
    return any(phrase in text.lower() for phrase in _FOOTNOTE_EXCLUSION_PHRASES)


def _is_result_evidence_line(line: str) -> bool:
    return (
        len(line) <= _MAX_RESULT_EVIDENCE_LINE_LENGTH
        and not _is_footnote_or_methodology_text(line)
    )


def _detect_section_boundaries(lines: list[str]) -> list[tuple[int, str]]:
    """Find strong, unambiguous report-section starts: a line naming exactly one printed date
    alongside a collection/specimen/draw/received keyword. A header line listing several dates for
    several value columns of one single report (e.g. "Historical Previous Current Draw Dates: ...")
    is deliberately excluded here — that's one report's own column headers, not a new section — so
    only genuinely separate combined-report boundaries get this treatment. Sorted by line index."""
    boundaries = []
    for index, line in enumerate(lines):
        if _BIRTH_DATE_RE.search(line) or _is_footnote_or_methodology_text(line):
            continue
        if len(_PRINTED_DATE_RE.findall(line)) != 1:
            continue
        match = _COLLECTION_DATE_RE.search(line)
        if match:
            boundaries.append((index, match.group(1)))
    return boundaries


def _source_marker_evidence(lab_text: str) -> dict[str, dict[object, dict]]:
    """Return conservative source evidence for marker/date pairs, keyed by canonical marker then
    normalized draw date, each with the literal printed date_display and the literal result token(s)
    found on that evidence line.

    A marker must appear beside a numeric result in its configured unit. When a report contains
    genuine separate section boundaries (see _detect_section_boundaries), every result is attributed
    to the nearest preceding section's date regardless of line distance — real combined multi-report
    PDFs print one specimen/collection date near a section's start and never repeat it beside every
    row below it. Only when the document has no detectable section boundary at all does this fall
    back to the previous line-window/header-proximity heuristic, so existing single-report formats
    (e.g. one row printing several dated columns) behave exactly as before.
    """
    lines = lab_text.splitlines()
    dated_header_lines = []
    for index, line in enumerate(lines):
        dates = [match.group(0) for match in _PRINTED_DATE_RE.finditer(line)]
        if not dates:
            continue
        context = " ".join(lines[max(0, index - 2):min(len(lines), index + 3)])
        if (_DATE_HEADER_RE.search(context) and not _BIRTH_DATE_RE.search(context)
                and not _is_footnote_or_methodology_text(context)):
            dated_header_lines.append((index, dates))
    section_boundaries = _detect_section_boundaries(lines)

    evidence: dict[str, dict[object, dict]] = {}
    for canonical, config in MARKER_LIBRARY.items():
        aliases = sorted({canonical, *config.get("aliases", [])}, key=len, reverse=True)
        alias_pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(alias) for alias in aliases) + r")(?!\w)",
            re.IGNORECASE,
        )
        for index, line in enumerate(lines):
            match = alias_pattern.search(line)
            if not match:
                continue
            if not _is_result_evidence_line(line):
                continue
            result_text = line[:match.start()] + line[match.end():]
            result_text = re.split(r"\breference\s+range\b", result_text, maxsplit=1,
                                   flags=re.IGNORECASE)[0]
            if not _has_numeric_result_with_unit(result_text, config.get("unit", "")):
                continue
            result_tokens = _RESULT_TOKEN_RE.findall(result_text)
            if not result_tokens:
                continue

            source_dates = [date.group(0) for date in _PRINTED_DATE_RE.finditer(line)]
            if not source_dates and section_boundaries:
                preceding_sections = [item for item in section_boundaries if item[0] <= index]
                if preceding_sections:
                    source_dates = [preceding_sections[-1][1]]
            if not source_dates:
                preceding = [item for item in dated_header_lines if 0 <= index - item[0] <= 40]
                if preceding:
                    nearest_index = preceding[-1][0]
                    source_dates = [date for header_index, dates in preceding
                                    if nearest_index - header_index <= 2 for date in dates]
                    if len(result_tokens) < len(source_dates):
                        source_dates = []
            for date_display in source_dates:
                normalized = _normalize_date_for_matching(date_display)
                if isinstance(normalized, tuple):
                    bucket = evidence.setdefault(canonical, {})
                    if normalized not in bucket:
                        bucket[normalized] = {"date_display": date_display, "tokens": list(result_tokens)}
    return evidence


def _source_marker_dates(lab_text: str) -> dict[str, dict[object, str]]:
    """Backward-compatible view of _source_marker_evidence(): normalized date -> literal date_display."""
    return {
        canonical: {normalized: info["date_display"] for normalized, info in dates.items()}
        for canonical, dates in _source_marker_evidence(lab_text).items()
    }


def _normalize_result_token(token: str):
    stripped = token.strip()
    match = re.match(r"^([<>]=?)?\s*(\d+(?:\.\d+)?)$", stripped)
    if match:
        prefix, number = match.groups()
        return (prefix or "", float(number))
    return stripped.lower()


def _disp_value_matches_tokens(disp_value: str, tokens: list[str]) -> bool:
    """True when disp_value corresponds to one of the literal result tokens found on the source
    evidence line — as a normalized number (with matching inequality prefix, if any) when both sides
    parse as numbers, otherwise as exact stripped/lowercased text (e.g. status words)."""
    if not tokens:
        return True  # nothing captured on the evidence line to contradict this value
    target = _normalize_result_token(disp_value)
    return any(target == _normalize_result_token(token) for token in tokens)


def _mismatched_source_marker_values(extracted: dict, lab_text: str) -> list[tuple[str, str, str, list[str]]]:
    """Marker occurrences whose reported disp_value doesn't match any token on its own source
    evidence line — the same class of problem as a missing occurrence (something claimed that the
    source doesn't actually support), just caught on value instead of presence."""
    evidence = _source_marker_evidence(lab_text)
    mismatches = []
    for occurrence in extracted.get("marker_occurrences", []):
        if occurrence.get("status") != "reported":
            continue
        disp_value = occurrence.get("disp_value", "")
        if not disp_value:
            continue
        match = markers_reference_lookup(occurrence.get("name", ""))
        if match is None:
            continue
        normalized = _normalize_date_for_matching(occurrence.get("date_display", ""))
        if not isinstance(normalized, tuple):
            continue
        info = evidence.get(match[0], {}).get(normalized)
        if info is None:
            continue  # no source evidence for this marker/date pair at all - the occurrence guard handles that
        if not _disp_value_matches_tokens(disp_value, info["tokens"]):
            mismatches.append((match[0], occurrence.get("date_display", ""), disp_value, info["tokens"]))
    return sorted(mismatches, key=lambda item: (item[0], _normalize_date_for_matching(item[1])))


def _format_value_mismatches(mismatches: list[tuple[str, str, str, list[str]]]) -> str:
    details = "; ".join(
        f"{marker} [{date_display}]: extracted {disp_value!r} not found among source tokens {tokens}"
        for marker, date_display, disp_value, tokens in mismatches
    )
    return ("Extraction value fidelity validation failed: marker_occurrences contains a disp_value "
            f"the source PDF does not support: {details}")


def _missing_source_marker_dates(extracted: dict, lab_text: str) -> list[tuple[str, str]]:
    extracted_dates: dict[str, set] = {}
    for occurrence in extracted.get("marker_occurrences", []):
        match = markers_reference_lookup(occurrence.get("name", ""))
        normalized = _normalize_date_for_matching(occurrence.get("date_display", ""))
        if match and isinstance(normalized, tuple):
            extracted_dates.setdefault(match[0], set()).add(normalized)

    missing = []
    for canonical, source_dates in _source_marker_dates(lab_text).items():
        for normalized, date_display in source_dates.items():
            if normalized not in extracted_dates.get(canonical, set()):
                missing.append((canonical, date_display))
    return sorted(missing, key=lambda item: (item[0], _normalize_date_for_matching(item[1])))


def _format_missing_occurrences(missing: list[tuple[str, str]]) -> str:
    grouped: dict[str, list[str]] = {}
    for marker, date_display in missing:
        grouped.setdefault(marker, []).append(date_display)
    details = "; ".join(f"{marker} [{', '.join(dates)}]" for marker, dates in grouped.items())
    return ("Extraction occurrence validation failed: source PDF contains marker/date pairs "
            f"missing from marker_occurrences: {details}")


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
                                   lab_text: str = "", dexa_text: str = "",
                                   dexa_scanned: bool = False) -> ExtractionReviewNotice:
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
        # A scanned DEXA page has no text layer, so a verbatim search proves nothing there; its
        # evidence is the two-read agreement gate, reported in the DEXA block of the staff notes.
        if numeric and dexa_text.strip() and not (dexa_scanned and date_match < 0) \
                and numeric not in context and value not in context:
            warning = (f"WARNING: DEXA BODY FAT % FOR {date} = {value} NOT FOUND VERBATIM IN SOURCE PDF - "
                       "POSSIBLE HALLUCINATION, NEEDS HUMAN REVIEW")
            notice.other_notes.append(warning)
            log_lines.append(warning)

    if log_lines:
        with open("/tmp/extraction_completeness_log.txt", "a", encoding="utf-8") as log:
            log.write("\n".join(log_lines) + "\n")
    return notice


# ---------------------------------------------------------------------------
# Word-position helpers for the bloodwork text layer
# ---------------------------------------------------------------------------

def _page_words(page) -> list[tuple]:
    """(x0, y0, x1, y1, text) for every word on a page; accepts a fitz.Page or a prepared word list."""
    if hasattr(page, "get_text"):
        raw = page.get_text("words")
        # Reading a scanned page's text layer decodes its full-resolution image into MuPDF's cache;
        # empty the cache so those images never accumulate (it is a cache only: output is unchanged).
        fitz.TOOLS.store_shrink(100)
    else:
        raw = page
    return [(float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
            for w in raw if str(w[4]).strip()]


def _group_lines(words: list[tuple], tolerance: float) -> list[list[tuple]]:
    lines: list[tuple[float, list]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        center = (word[1] + word[3]) / 2
        if lines and abs(lines[-1][0] - center) <= tolerance:
            lines[-1][1].append(word)
        else:
            lines.append((center, [word]))
    return [sorted(line_words, key=lambda w: w[0]) for _, line_words in lines]


def _line_text(words) -> str:
    return " ".join(w[4] for w in words)


def _header_token(text: str) -> str:
    return re.sub(r"[^\w%\-]", "", text.lower())


def _match_header_columns(words: list[tuple], label_specs) -> list[dict]:
    """Greedy longest-phrase match of a header line's words against known column labels. Words that
    match no label (units, printed dates) attach to the column label they follow."""
    tokens = [_header_token(w[4]) for w in words]
    specs = sorted(label_specs, key=lambda spec: -len(spec[1]))
    columns = []
    index = 0
    while index < len(words):
        for kind, phrase in specs:
            if tuple(tokens[index:index + len(phrase)]) == phrase:
                span = words[index:index + len(phrase)]
                columns.append({"kind": kind, "x0": span[0][0], "x1": span[-1][2], "extra": []})
                index += len(phrase)
                break
        else:
            if columns:
                columns[-1]["extra"].append(words[index])
                columns[-1]["x1"] = max(columns[-1]["x1"], words[index][2])
            index += 1
    return columns


def _column_bounds(columns: list[dict], left=float("-inf"), right=float("inf")) -> list[tuple[float, float]]:
    bounds = []
    for index, column in enumerate(columns):
        lo = left if index == 0 else (columns[index - 1]["x1"] + column["x0"]) / 2
        hi = right if index == len(columns) - 1 else (column["x1"] + columns[index + 1]["x0"]) / 2
        bounds.append((lo, hi))
    return bounds


def _column_at(x: float, bounds) -> int | None:
    for index, (lo, hi) in enumerate(bounds):
        if lo <= x < hi:
            return index
    return None


# ---------------------------------------------------------------------------
# Deterministic bloodwork parsing
# ---------------------------------------------------------------------------

_LINE_TOLERANCE_PT = 3.0
_CELL_VALUE_RE = re.compile(r"(?P<ineq>[<>≤≥]=?)?(?P<num>\d+(?:\.\d+)?|\.\d+)(?P<flag>[HL])?")
_CELL_PIECE_RE = re.compile(r"[<>≤≥]=?(?:\d+(?:\.\d+)?|\.\d+)[HL]?|(?:\d+(?:\.\d+)?|\.\d+)[HL]?")
_NOT_PERFORMED_CELLS = {"tnp", "test not performed", "not performed"}
_QUALITATIVE_CELLS = {"negative", "positive", "detected", "not detected", "none detected", "trace",
                      "normal", "abnormal", "reactive", "non-reactive", "nonreactive",
                      # printed urine color/clarity results, read literally
                      "yellow", "straw", "amber", "colorless", "clear", "hazy", "cloudy", "turbid"}
_EXPECTED_QUALITATIVE = {"negative", "not detected", "none detected", "normal", "non-reactive", "nonreactive"}
_MULTIWORD_CELLS = (("test", "not", "performed"), ("not", "performed"), ("not", "detected"), ("none", "detected"))
_STANDALONE_FLAGS = {"H", "L", "HH", "LL"}
# Words a separate "Flag" column prints, mapped to the H/L flag they state. Anything else is ignored.
_FLAG_COLUMN_WORDS = {"H": "H", "L": "L", "HH": "HH", "LL": "LL", "HIGH": "H", "LOW": "L"}
_COMPARATOR_TOKEN_RE = re.compile(r"(?:<=|>=|[<>≤≥])\s*(?:\d+(?:\.\d+)?|\.\d+)")
_BOUNDED_RANGE_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?")
# How far (pt) a result's center may sit outside its dated column's printed header label.
_DATE_COLUMN_PAD_PT = 6.0
_ORDER_ID_RE = re.compile(r"\border\s*(?:id|#|number)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
_COLLECTED_RE = re.compile(r"\bcollected\s*:?\s*(" + _PRINTED_DATE_RE.pattern + r")", re.IGNORECASE)
# Quest's own multi-draw restatements of values printed elsewhere - never a source of truth.
_TREND_TABLE_TITLES = ("progress summary", "trend summary", "cumulative summary")
# A document section title ("... Report"); a different exact title ends any table above it.
_SECTION_TITLE_RE = re.compile(r"[A-Za-z][A-Za-z&/-]*(?:\s+[A-Za-z][A-Za-z&/-]*){0,7}\s+Report", re.IGNORECASE)
# Words further apart than this on one baseline are separate printed phrases (e.g. title vs. page number).
_PHRASE_GAP_PT = 12.0
_BLOODWORK_NAME_LABELS = {"test", "tests", "analyte"}
_BLOODWORK_HEADER_LABELS = [
    ("name", ("test", "name")), ("name", ("test",)), ("name", ("tests",)), ("name", ("analyte",)),
    ("current", ("current",)), ("current", ("current", "result")), ("current", ("result",)),
    ("current", ("in", "range")), ("current", ("out", "of", "range")),
    ("current", ("optimal",)), ("current", ("moderate",)), ("current", ("high",)),
    ("historical", ("historical",)), ("historical", ("historical", "result")),
    ("range", ("reference", "range")), ("range", ("reference", "interval")), ("range", ("range",)),
    ("ignore", ("units",)), ("ignore", ("unit",)), ("ignore", ("lab",)), ("flag", ("flag",)),
]


class _AmbiguousCell(ValueError):
    pass


def _split_cell_word(text: str) -> list[str] | None:
    """Split one extracted word into result tokens. Adjacent columns that ran together are only split
    where the text itself delimits them (an appended H/L flag or a leading inequality); digits running
    straight into digits are ambiguous and raise rather than guess a boundary. None = not a result word."""
    pieces = _CELL_PIECE_RE.findall(text)
    if not pieces or "".join(pieces) != text:
        return None
    for left, right in zip(pieces, pieces[1:]):
        if not (left[-1] in "HL" or right[0] in "<>≤≥"):
            raise _AmbiguousCell(text)
    return pieces


def _is_cell_token(text: str) -> bool:
    low = text.lower()
    if low in _NOT_PERFORMED_CELLS or low in _QUALITATIVE_CELLS:
        return True
    try:
        return _split_cell_word(text) is not None
    except _AmbiguousCell:
        return True


def _is_threshold_token(text: str) -> bool:
    return bool(_COMPARATOR_TOKEN_RE.fullmatch(text) or _BOUNDED_RANGE_TOKEN_RE.fullmatch(text))


def _within_date_column(x: float, column: dict) -> bool:
    return column["x0"] - _DATE_COLUMN_PAD_PT <= x <= column["x1"] + _DATE_COLUMN_PAD_PT


def _within_cell_column(word: tuple, column: dict) -> bool:
    center = (word[0] + word[2]) / 2
    return _within_date_column(center, column) or (
        " " in word[4] and _within_date_column(word[0], column))


def _merge_cell_phrases(words: list[tuple]) -> list[tuple]:
    """Join multi-word cell phrases ("Test Not Performed") into one word; append each word's anchor x."""
    merged = []
    index = 0
    while index < len(words):
        for phrase in _MULTIWORD_CELLS:
            span = words[index:index + len(phrase)]
            if tuple(w[4].lower().strip(",;") for w in span) == phrase:
                anchor = (span[0][0] + span[0][2]) / 2
                merged.append((span[0][0], span[0][1], span[-1][2], span[-1][3],
                               " ".join(w[4] for w in span), anchor))
                index += len(phrase)
                break
        else:
            word = words[index]
            merged.append((*word, (word[0] + word[2]) / 2))
            index += 1
    return merged


def _bloodwork_header(words: list[tuple], group_lines: list[list[tuple]] = ()) -> dict | None:
    if not words or _header_token(words[0][4]) not in _BLOODWORK_NAME_LABELS:
        return None
    tokens = [_header_token(w[4]) for w in words]
    group_tokens = [_header_token(w[4]) for line in group_lines for w in line]
    has_dated_labels = any(token in ("current", "historical") for token in tokens + group_tokens)
    has_range_labels = any(
        tokens[index:index + len(phrase)] == list(phrase)
        for phrase in (("in", "range"), ("out", "of", "range"))
        for index in range(len(tokens))
    )
    if not (has_dated_labels or has_range_labels):
        return None
    columns = _match_header_columns(words, _BLOODWORK_HEADER_LABELS)
    if not columns or columns[0]["kind"] != "name" or not any(
            column["kind"] in ("current", "historical") for column in columns):
        return None
    # A Current/Historical label printed on a line above decides which section a result column belongs to.
    groups = [(_header_token(w[4]), w[0], w[2]) for line in group_lines for w in line
              if _header_token(w[4]) in ("current", "historical")]
    for column in columns:
        if column["kind"] not in ("current", "historical"):
            continue
        owners = {kind for kind, x0, x1 in groups if x0 <= column["x1"] and column["x0"] <= x1}
        if len(owners) == 1:
            column["kind"] = owners.pop()
    logical, value_columns = [], []
    current_index = None
    for column in columns:
        if column["kind"] == "current":
            if current_index is None:
                current_index = len(value_columns)
                value_columns.append({"kind": "current", "date": None})
            logical.append(current_index)
        elif column["kind"] == "historical":
            dates = [match.group(0) for match in _PRINTED_DATE_RE.finditer(_line_text(column["extra"]))]
            if len(dates) > 1:
                raise BloodworkParseError(f"Historical column header prints more than one date: {dates}")
            logical.append(len(value_columns))
            value_columns.append({"kind": "historical", "date": dates[0] if dates else None})
        else:
            logical.append(None)
    return {"columns": columns, "bounds": _column_bounds(columns), "logical": logical,
            "value_columns": value_columns}


def _attach_header_dates(words: list[tuple], header: dict) -> bool:
    """A Historical column's date may be printed on the line directly under its label."""
    attached = False
    for word in words:
        if not _PRINTED_DATE_RE.fullmatch(word[4]):
            continue
        column = _column_at((word[0] + word[2]) / 2, header["bounds"])
        logical = header["logical"][column] if column is not None else None
        if logical is not None and header["value_columns"][logical]["kind"] == "historical" \
                and header["value_columns"][logical]["date"] is None:
            header["value_columns"][logical]["date"] = word[4]
            attached = True
    return attached


def _cell_result(token: str) -> tuple[str, float | None, str]:
    low = token.lower()
    if low in _NOT_PERFORMED_CELLS:
        return "not_performed", None, ""
    if low in _QUALITATIVE_CELLS:
        return "final", None, token
    match = _CELL_VALUE_RE.fullmatch(token)
    if match is None:
        return token, None, token
    if match.group("ineq"):
        return "final", None, match.group("ineq") + match.group("num")
    return "final", float(match.group("num")), match.group("num")


def _printed_lab_range(range_words: list[str]) -> tuple[float, float, str]:
    display = " ".join(range_words).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)(?:\s+[A-Za-zµμ%][\w/%.^µμ]*)?", display)
    if not match:
        return 0, 0, ""
    return float(match.group(1)), float(match.group(2)), display


def _section_date(section: dict) -> str | None:
    return section["dates"][0] if section["dates"] else None


def _section_label(section: dict) -> str:
    date = _section_date(section) or "undated"
    label = f"Order {section['order_id']} (collected {date})" if section["order_id"] else f"Collected {date}"
    return f"{label}; {section['heading']}" if section.get("heading") else label


def _clean_row_name(text: str, lab_codes: set[str]) -> str:
    text = re.sub(r"\(\d+\)", "", text)
    text = re.sub(r"\(([^()]*)\)", lambda m: "" if m[1].strip().casefold() in
                  {code.casefold() for code in lab_codes} else m[0], text)
    return " ".join(text.split())


def _match_row_name(name: str, heading: str | None):
    query = name.casefold()
    namespaces = {canonical.split(" \u2014 ")[0] for canonical in MARKER_LIBRARY if " \u2014 " in canonical}
    namespace = next((item for item in namespaces if item.casefold() == (heading or "").casefold()), None)
    candidates = {}
    for canonical, config in MARKER_LIBRARY.items():
        if namespace and canonical.split(" \u2014 ", 1)[0] != namespace:
            continue
        aliases = [canonical, *config.get("aliases", [])]
        for alias in aliases:
            if query == " ".join(alias.split()).casefold():
                candidates[canonical] = config
    if not candidates:
        return None
    if len(candidates) != 1:
        raise BloodworkParseError(f"Ambiguous marker name {name!r} in section {heading!r}")
    return next(iter(candidates.items()))


def _parse_bloodwork_row(words: list[tuple], header: dict, section: dict,
                         occurrences: list[dict], unrecognized: list[dict],
                         row_name: str | None = None, allow_text: bool = False) -> bool:
    """Apply the single extraction rule to one table line. Returns True for a recognized marker row."""
    words = _merge_cell_phrases(words)
    value_centers = [(c["x0"] + c["x1"]) / 2 for c, li in zip(header["columns"], header["logical"])
                     if li is not None]
    first_value_center = min(value_centers)
    name_words = []
    for word in words if row_name is None else ():
        if _is_cell_token(word[4]) or _is_threshold_token(word[4]) or word[5] >= first_value_center:
            break
        name_words.append(word)
    raw_name = row_name if row_name is not None else _line_text(name_words).strip()
    if not raw_name:
        return False
    match = _match_row_name(raw_name, section.get("heading"))

    # Only a token printed inside a dated column's own header span can be a result; anything else
    # (reference legends, units, lab codes, stray thresholds) is never eligible, whatever its shape.
    cells: dict[int, str] = {}
    flags: dict[int, str] = {}  # the lab's own printed H/L flag per value column; never computed
    stray: dict[int, list[str]] = {}
    range_words: list[str] = []
    problems: list[str] = []
    value_columns = header["value_columns"]
    for word in words[len(name_words):]:
        column = _column_at(word[5], header["bounds"])
        kind = header["columns"][column]["kind"]
        if kind == "range":
            range_words.append(word[4])
            continue
        if kind == "flag":
            # A separate printed Flag column (Labcorp-style) belongs to the single current result.
            printed = _FLAG_COLUMN_WORDS.get(word[4].upper())
            current = [i for i, c in enumerate(header["value_columns"]) if c["kind"] == "current"]
            if printed and len(current) == 1:
                flags[current[0]] = printed
            continue
        if kind in ("current", "historical") and word[4] in _STANDALONE_FLAGS \
                and header["logical"][column] is not None:
            flags[header["logical"][column]] = word[4]
        if kind not in ("current", "historical") or word[4] in _STANDALONE_FLAGS:
            continue
        try:
            pieces = _split_cell_word(word[4])
        except _AmbiguousCell:
            raise BloodworkParseError(
                f"{raw_name} ({_section_label(section)}): {word[4]!r} runs result digits together with no "
                "flag or inequality between them, so its column boundary cannot be read")
        result_shaped = pieces is not None or _is_threshold_token(word[4]) or (
            word[4].lower() in _NOT_PERFORMED_CELLS or word[4].lower() in _QUALITATIVE_CELLS)
        if pieces is None:
            pieces = [word[4]]
        anchor = word[0] + (word[2] - word[0]) * len(pieces[0]) / len(word[4]) / 2 if len(pieces) > 1 else word[5]
        anchor_column = _column_at(anchor, header["bounds"])
        start = header["logical"][anchor_column] if anchor_column is not None else None
        eligible = _within_date_column(anchor, header["columns"][anchor_column]) if len(pieces) > 1 \
            else _within_cell_column(word, header["columns"][anchor_column])
        if start is None or not eligible:
            if result_shaped and header["logical"][column] is not None:
                stray.setdefault(header["logical"][column], []).append(word[4])
            continue
        if (not result_shaped and not allow_text) or start + len(pieces) > len(value_columns):
            problems.append(word[4])
            continue
        for offset, piece in enumerate(pieces):
            if _BOUNDED_RANGE_TOKEN_RE.fullmatch(piece) or start + offset in cells:
                problems.append(piece)
            else:
                cells[start + offset] = piece
                printed_flag = _CELL_VALUE_RE.fullmatch(piece)
                if printed_flag and printed_flag.group("flag"):
                    flags[start + offset] = printed_flag.group("flag")

    if problems:
        raise BloodworkParseError(
            f"{raw_name} ({_section_label(section)}): {problems} sits in a dated result column but is not a "
            "readable result")
    unresolved = sorted(i for i in stray if i not in cells)
    if unresolved:
        raise BloodworkParseError(
            f"{raw_name} ({_section_label(section)}): no single result in the dated "
            f"{[value_columns[i]['kind'] for i in unresolved]} column(s); tokens printed outside those columns "
            f"{[t for i in unresolved for t in stray[i]]} are never read as a result")

    if match is None:
        parsed_cells = []
        for index, column in enumerate(value_columns):
            date = _section_date(section) if column["kind"] == "current" else column["date"]
            token = cells.get(index)
            if token is not None and not date:
                raise BloodworkParseError(f"{raw_name}: {column['kind']} result has no governing date")
            if date:
                status, value, display = _cell_result(token) if token is not None else ("not_performed", None, "")
                parsed_cells.append({"kind": column["kind"], "date_display": date, "status": status,
                                     "value": value, "disp_value": display, "present": token is not None,
                                     "lab_flag": flags.get(index) if token is not None else None})
        unrecognized.append({
            "raw_name": raw_name, "raw_value": " | ".join(cells[i] for i in sorted(cells)),
            "raw_unit": "", "raw_range": " ".join(range_words), "source_context": _section_label(section),
            "cells": parsed_cells, "section_heading": section.get("heading"),
        })
        return True

    canonical, config = match
    lab_lo, lab_hi, lab_display = _printed_lab_range(range_words)
    for index, column in enumerate(value_columns):
        date = _section_date(section) if column["kind"] == "current" else column["date"]
        token = cells.get(index)
        if token is None:
            status, value, disp_value = ("not_performed", None, "") if date else (None, None, "")
            if status is None:
                continue
        else:
            if not date:
                raise BloodworkParseError(
                    f"{raw_name} ({_section_label(section)}): result {token!r} is in a {column['kind']} "
                    "column with no governing date")
            status, value, disp_value = _cell_result(token)
        is_good = None
        if config.get("kind") == "categorical" and value is None and disp_value:
            is_good = disp_value.lower() in _EXPECTED_QUALITATIVE
        occurrences.append({
            "name": canonical,
            "date_display": date,
            "source_label": _section_label(section),
            "status": status,
            "value": value,
            "disp_value": disp_value,
            "is_good": is_good,
            "lab_range_lo": lab_lo,
            "lab_range_hi": lab_hi,
            "lab_range_display": lab_display,
            "lab_flag": flags.get(index, "") if token is not None else "",
        })
    return True


def _table_row_groups(page, lines: list[list[tuple]], header: dict):
    """Use printed name-cell rules to delimit rows; unruled tables use separated text lines."""
    first_value = min(c["x0"] for c in header["columns"] if c["kind"] in ("current", "historical"))
    left = header["columns"][0]["x0"]
    rules = []
    if hasattr(page, "get_drawings"):
        for drawing in page.get_drawings():
            for item in drawing["items"]:
                if item[0] != "l":
                    continue
                start, end = sorted(item[1:3], key=lambda point: point.x)
                if abs(start.y - end.y) < 0.5 and abs(start.x - left) < 8 \
                        and header["columns"][0]["x1"] < end.x:
                    rules.append((start.y, end.x))
    name_rules = [x for _, x in rules if x < first_value]
    if name_rules:
        right = min(name_rules)
        boundaries = sorted({round(y, 2) for y, x in rules if x >= right - 1})
        groups = {}
        for line in lines:
            for word in line:
                center = (word[1] + word[3]) / 2
                band = next((i for i in range(len(boundaries) - 1)
                             if boundaries[i] <= center < boundaries[i + 1]), None)
                if band is not None:
                    groups.setdefault(band, []).append(word)
        return list(groups.values()), right, True
    return lines, first_value, False


def _is_table_prose(words, name_right, header):
    crossing = any(w[0] < name_right < w[2] for w in words)
    continuous = any(0 <= right[0] - left[2] < _PHRASE_GAP_PT
                     for line in _group_lines(words, _LINE_TOLERANCE_PT)
                     for left, right in zip(line, line[1:])
                     if left[2] <= name_right < right[2])
    label_crossing = any(
        w[0] < column["x1"] < w[2] and not _is_cell_token(w[4])
        for w in words for column in header["columns"]
        if column["kind"] in ("current", "historical")
    )
    explanatory = label_crossing and any(w[4] == "=" for w in words)
    return crossing or continuous or explanatory


def _printed_cells(words):
    cells = []
    for line in _group_lines(words, _LINE_TOLERANCE_PT):
        current = []
        for word in line:
            gap = word[0] - current[-1][2] if current else 0
            normal_gap = min(word[3] - word[1], current[-1][3] - current[-1][1]) * 0.6 if current else 0
            if current and gap > normal_gap:
                cells.append(current)
                current = []
            current.append(word)
        if current:
            cells.append(current)
    return cells


def _parse_table_region(page, lines, header, section, occurrences, unrecognized, row_audit=None,
                        printed_lab_codes=()):
    groups, name_right, ruled = _table_row_groups(page, lines, header)
    for words in groups:
        name_words = [w for w in words if w[2] <= name_right
                      and not _is_cell_token(w[4]) and not _is_threshold_token(w[4])]
        other_words = [w for w in words if w not in name_words]
        name_lines = _group_lines(name_words, 4.0)
        text = " ".join(_line_text(line) for line in name_lines).strip()
        band_text = " ".join(_line_text(line) for line in _group_lines(words, 4.0)).strip()
        if name_words and band_text.isupper() and _is_single_phrase(words) and all(
                not _is_cell_token(w[4]) and not _is_threshold_token(w[4]) for w in words):
            section["heading"] = band_text
            continue
        if _is_table_prose(words, name_right, header):
            continue
        if text and not other_words:
            section["heading"] = text
            continue
        cells = []
        lab_codes = set(printed_lab_codes)
        assigned = {index: [] for index in range(len(header["columns"]))}
        for span in _printed_cells(other_words):
            center = (span[0][0] + span[-1][2]) / 2
            index = _column_at(center, header["bounds"])
            column = header["columns"][index]
            if column["kind"] in ("current", "historical"):
                tokens = [w[4] for w in span]
                popped = []
                while len(tokens) > 1 and tokens[-1] in _STANDALONE_FLAGS:
                    popped.append(tokens.pop())
                token = " ".join(tokens)
                numeric = _CELL_VALUE_RE.fullmatch(token)
                if popped in (["H"], ["L"]) and numeric and not numeric.group("flag"):
                    token += popped[0]  # keep the lab's printed flag with its own result
                assigned[index].append((span[0][0], min(w[1] for w in span),
                                        span[-1][2], max(w[3] for w in span), token))
            else:
                assigned[index].extend(span)
        for index, column in enumerate(header["columns"]):
            column_words = assigned[index]
            if column["kind"] == "ignore":
                lab_codes.update(w[4] for w in column_words)
            if column["kind"] in ("current", "historical"):
                column_words = sorted(column_words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
                dated_words = [w for w in column_words
                               if _within_cell_column(w, column)
                               and w[4] not in _STANDALONE_FLAGS]
                phrase = _line_text(dated_words).lower()
                text_cell = ruled and dated_words and all(
                    not _is_cell_token(w[4]) and not _is_threshold_token(w[4]) for w in dated_words)
                if phrase in _NOT_PERFORMED_CELLS or text_cell:
                    first = dated_words[0]
                    column_words = [w for w in column_words if w not in dated_words]
                    token = "TNP" if phrase in _NOT_PERFORMED_CELLS and len(dated_words) > 1 \
                        else _line_text(dated_words)
                    column_words.insert(0, (first[0], first[1], first[2], first[3], token))
            cells.extend(column_words)
        printed = any((ruled or _is_cell_token(w[4]) or _is_threshold_token(w[4])) for w in cells
                     if (i := _column_at((w[0] + w[2]) / 2, header["bounds"])) is not None
                     and header["logical"][i] is not None
                     and _within_cell_column(w, header["columns"][i]))
        if not printed:
            continue
        if not text:
            raise BloodworkParseError(
                f"{_section_label(section)}: result-shaped token has no row name: {_line_text(words)!r}")
        name = _clean_row_name(text, lab_codes)
        occurrence_start = len(occurrences)
        unknown_start = len(unrecognized)
        before = len(occurrences) + len(unrecognized)
        # Name words are supplied separately; the row reader sees only non-name cells.
        _parse_bloodwork_row(cells, header, section, occurrences, unrecognized, row_name=name, allow_text=ruled)
        if len(occurrences) + len(unrecognized) == before:
            raise BloodworkParseError(f"{_section_label(section)}: unaccounted result row {name!r}")
        if row_audit is not None:
            row_audit.append({"page": page.number + 1 if hasattr(page, "number") else section.get("_page"),
                              "name": name, "section": section.get("heading"),
                              "y": min(w[1] for w in words),
                              "occurrences": [dict(item) for item in occurrences[occurrence_start:]],
                              "unrecognized": unrecognized[unknown_start:]})


def _is_single_phrase(words: list[tuple]) -> bool:
    """A printed heading is one run of words; a result row's words are spread across columns."""
    for line in _group_lines(words, 4.0):
        if any(right[0] - left[2] > _PHRASE_GAP_PT for left, right in zip(line, line[1:])):
            return False
    return True


def _section_title(words: list[tuple]) -> str | None:
    """The section title printed on this line, if one of its separately spaced phrases is a title."""
    phrases, current = [], []
    for word in words:
        if current and word[0] - current[-1][2] > _PHRASE_GAP_PT:
            phrases.append(current)
            current = []
        current.append(word)
    phrases.append(current)
    for phrase in phrases:
        text = _line_text(phrase).strip()
        if _SECTION_TITLE_RE.fullmatch(text):
            return text.lower()
    return None


def _dedupe_bloodwork_rows(occurrences, audit):
    pages = [row["page"] for row in audit for _ in row["occurrences"]]
    unique = {}
    sources = {}
    for occurrence, page in zip(occurrences, pages, strict=True):
        key = (occurrence["name"], _normalize_date_for_matching(occurrence["date_display"]))
        if key in unique:
            previous = unique[key]
            same_value = previous["value"] == occurrence["value"] and (
                previous["value"] is not None or previous["disp_value"] == occurrence["disp_value"])
            if previous["status"] != occurrence["status"] or not same_value:
                raise BloodworkHardStop(
                    f"{occurrence['name']} on {occurrence['date_display']}: conflicting results on "
                    f"pages {sources[key][0]} and {page}: "
                    f"{previous['disp_value'] or previous['status']!r} versus "
                    f"{occurrence['disp_value'] or occurrence['status']!r}")
        else:
            unique[key] = dict(occurrence)
            sources[key] = []
        if page not in sources[key]:
            sources[key].append(page)
    for key, occurrence in unique.items():
        if len(sources[key]) > 1:
            occurrence["source_label"] += "; pages " + ", ".join(map(str, sources[key]))
    return list(unique.values())


def _headerless_marker_rows(page_words) -> list[str]:
    """Names of known tests (scored or lab-reported) that start a line followed by a result, on a page
    with no recognized table header. Used only to name what was not read; nothing here is kept."""
    import lab_reported
    names = []
    for words in _group_lines(page_words, _LINE_TOLERANCE_PT):
        tokens = [word[4] for word in words]
        for cut in range(len(tokens) - 1, 0, -1):
            name = " ".join(tokens[:cut])
            if not re.fullmatch(r"[<>]?\d[\d.,]*", tokens[cut]):
                continue
            match = markers_reference_lookup(name) or lab_reported.lookup(name, "")
            if match and match[0] not in names:
                names.append(match[0])
            break
    return names[:8]


def _parse_bloodwork_tables(pdf_pages, row_audit=None, review_notes=None,
                            exclusions=None) -> tuple[list[dict], list[dict]]:
    """One rule for every Quest/Cleveland HeartLab section: each result printed in a test-name row is
    paired with the date governing its column - a Current-style column takes its section's own
    Collected: date, a Historical column takes the date printed in its own header. Sections are split
    at Collected:/Order ID lines; pages repeating an Order ID rejoin that section, but must print
    their own column header. Unreadable pages are skipped with visible manual-review warnings. Returns
    (marker_occurrences, unrecognized_rows)."""
    sections: list[dict] = []
    by_order: dict[str, dict] = {}
    occurrences: list[dict] = []
    unrecognized: list[dict] = []
    audit = row_audit if row_audit is not None else []
    unreadable = []
    printed_lab_codes = {
        match[1] for page in pdf_pages
        for match in re.finditer(r"\(\d+\)\s*\(([A-Z][A-Z0-9]{1,5})\)", _line_text(_page_words(page)))
    }
    state = {"section": None, "header": None, "excluded": False, "await_dates": False, "pending_dates": [],
             "recent_lines": [], "title": None, "rows": []}

    def new_section(order_id):
        section = {"order_id": order_id, "dates": [], "has_table": False}
        sections.append(section)
        if order_id:
            by_order[order_id] = section
        return section

    def switch(section):
        if section is not state["section"]:
            state.update(section=section, header=None, excluded=False, await_dates=False)

    def add_dates(section, dates):
        for date in dates:
            known = [_normalize_date_for_matching(d) for d in section["dates"]]
            key = _normalize_date_for_matching(date)
            if known and key not in known:
                raise BloodworkHardStop(
                    f"Order ID {section['order_id']} prints conflicting Collected: dates "
                    f"{section['dates'][0]!r} and {date!r}")
            if key not in known:
                section["dates"].append(date)

    excluded = exclusions if exclusions is not None else []

    def rollback(marks):
        del occurrences[marks[0]:], unrecognized[marks[1]:], audit[marks[2]:]

    def flush(page):
        if state["header"] is not None and state["rows"]:
            state["section"]["_page"] = page_number
            marks = (len(occurrences), len(unrecognized), len(audit))
            try:
                _parse_table_region(page, state["rows"], state["header"], state["section"],
                                    occurrences, unrecognized, audit, printed_lab_codes)
            except BloodworkHardStop:
                raise
            except BloodworkParseError as error:
                # Exclude only this table region; never keep part of it or guess.
                rollback(marks)
                excluded.append({"page": page_number, "section": _section_label(state["section"]),
                                 "reason": str(error)})
        state["rows"] = []

    def parse_page(page, page_words):
        for words in _group_lines(page_words, _LINE_TOLERANCE_PT):
            text = _line_text(words)
            order = _ORDER_ID_RE.search(text)
            collected = _COLLECTED_RE.search(text)
            title = _section_title(words)
            if title is not None:
                if title != state["title"]:
                    flush(page)
                    state.update(title=title, header=None, excluded=False, await_dates=False, recent_lines=[])
                if not (order or collected):
                    continue
            if order:
                flush(page)
                order_id = order.group(1)
                current = state["section"]
                existing = by_order.get(order_id)
                if current is not None and current["order_id"] is None and not current["has_table"]:
                    if existing is not None and existing is not current:
                        sections.remove(current)
                        add_dates(existing, current["dates"])
                        target = existing
                    else:
                        current["order_id"] = order_id
                        by_order[order_id] = current
                        target = current
                else:
                    target = existing or new_section(order_id)
                add_dates(target, state["pending_dates"])
                switch(target)
            if collected:
                flush(page)
                date = collected.group(1)
                current = state["section"]
                known = [_normalize_date_for_matching(d) for d in current["dates"]] if current else []
                if current is None:
                    target = new_section(None)
                elif not known or _normalize_date_for_matching(date) in known or order:
                    target = current
                else:
                    target = new_section(None)
                add_dates(target, [date])
                state["pending_dates"].append(date)
                switch(target)
            if order or collected:
                continue

            if state["section"] is None:
                switch(new_section(None))
            section = state["section"]
            if any(title in text.lower() for title in _TREND_TABLE_TITLES):
                flush(page)
                state.update(excluded=True, header=None, page_had_table=True)  # trend tables are skipped on purpose
                continue
            if state["excluded"]:
                continue
            header = _bloodwork_header(words, state["recent_lines"][-3:])
            if header:
                state["page_had_table"] = True
                flush(page)
                state.update(header=header, await_dates=True, pending_dates=[], recent_lines=[])
                section["has_table"] = True
                continue
            state["recent_lines"].append(words)
            if state["header"] is None:
                continue
            # Historical dates may sit a few header lines below the labels; read them until the first row.
            if state["await_dates"] and _attach_header_dates(words, state["header"]):
                continue
            state["rows"].append(words)
        flush(page)

    for page_number, page in enumerate(pdf_pages, 1):
        state.update(header=None, excluded=False, await_dates=False, pending_dates=[], recent_lines=[],
                     page_had_table=False)
        page_words = _page_words(page)
        if not page_words:
            unreadable.append(page_number)
            continue
        page_marks = (len(occurrences), len(unrecognized), len(audit))
        try:
            parse_page(page, page_words)
        except BloodworkHardStop:
            raise
        except BloodworkParseError as error:
            # Exclude only this page: everything it added is removed, nothing is guessed.
            rollback(page_marks)
            state.update(header=None, rows=[], excluded=False, await_dates=False, recent_lines=[])
            excluded.append({"page": page_number, "section": "whole page", "reason": str(error)})
            continue
        if not state["page_had_table"] and (names := _headerless_marker_rows(page_words)):
            excluded.append({"page": page_number, "section": "whole page", "reason": (
                "result rows printed under no recognized table header (expected 'Current'/'Historical' or "
                "'In Range'/'Out of Range'), so they were not read: " + ", ".join(names))})
    if unreadable:
        warning = (f"Lab PDF pages {', '.join(map(str, unreadable))}: no readable text, "
                   "OCR not supported, manual review required")
        print(f"WARNING: {warning}")
        if review_notes is not None:
            review_notes.append(warning)
    occurrences = _dedupe_bloodwork_rows(occurrences, audit)
    # An unrecognized layout is never rendered as an empty bloodwork section without notice. Direct callers
    # (exclusions=None) get the error; the report pipeline excludes the pages, lists them under
    # INCOMPLETE and still builds the rest of the report.
    problem = None
    if not any(section["dates"] for section in sections):
        problem = "Lab PDF has no recognizable 'Collected:' section - layout not recognized"
    elif not occurrences:
        problem = "Lab PDF produced zero recognized marker rows - layout not recognized"
    if problem:
        if exclusions is None:
            raise BloodworkParseError(problem + "; route for manual review")
        listed = {item["page"] for item in excluded}
        excluded.extend({"page": number, "section": "whole document", "reason": problem}
                        for number in range(1, len(pdf_pages) + 1) if number not in listed)
        return [], unrecognized
    return occurrences, unrecognized


# ---------------------------------------------------------------------------
# DEXA extraction (unchanged Claude path from before the deterministic bloodwork change)
# ---------------------------------------------------------------------------

def _extract_dexa_with_claude(client: Anthropic | None, dexa_pdfs: list[str], patient_name: str | None,
                              dob_sink: list | None = None, name_sink: list | None = None,
                              excluded_sink: list | None = None, confirmed_sink: list | None = None,
                              staff_age: int | None = None, info: dict | None = None):
    """Read every DEXA page twice and keep only measurements both reads agree on (scan_dexa.gate).
    Returns (dexa_history, staff_notes, summary_lines). info, when given, receives the pages that could
    not be read and the age printed on each accepted page, for the staff check and confirmation step."""
    import scan_bloodwork
    import scan_dexa

    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            note = "DEXA NOT READ: no API key - enter DEXA results manually"
            return [], [note], [f"DEXA - {note}"]
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                           timeout=ANTHROPIC_CALL_TIMEOUT_SECONDS, max_retries=ANTHROPIC_MAX_RETRIES)
    pages, failures = [], []
    for file_number, path in enumerate(dexa_pdfs, start=1):
        with fitz.open(path) as document:
            for page in document:
                key = (file_number, page.number + 1)
                try:
                    reads = scan_dexa.read_page(page, f"file {key[0]} page {key[1]}", client,
                                                _create_anthropic_message, MODEL)
                except scan_bloodwork.ScanGateError as error:
                    failures.append((key, error.reads, str(error)))
                else:
                    pages.append((key, reads))
    if name_sink is not None:
        for key, reads in sorted(pages, key=lambda item: item[0]):
            printed = {" ".join(read["patient_name"].split()) for read in reads if read["patient_name"]}
            if len(printed) == 1 and all(read["patient_name"] for read in reads):
                name_sink.append(("DEXA", f"file {key[0]} page {key[1]}", printed.pop()))
    excluded = [] if excluded_sink is None else excluded_sink
    result = scan_dexa.gate(pages, patient_name, failures, dob_sink, excluded, staff_age)
    accepted = sorted({key for key, _ in pages} - {key for key, _ in excluded})
    if confirmed_sink is not None:  # pages read twice and attributed to the staff-entered patient
        confirmed_sink.extend(accepted)
    if info is not None:
        ages = {key: scan_dexa.page_age(reads) for key, reads in pages}
        info.update(failed=[(key, reason) for key, _, reason in sorted(failures, key=lambda item: item[0])],
                    accepted=[(key, ages[key][1] if ages.get(key) else None) for key in accepted],
                    page_count=len(pages) + len(failures))
    return result

def has_scanned_pages(pdf: bytes | str | os.PathLike) -> bool:
    """True when a lab PDF (a path, or its bytes) has image-only pages, which only staff-entered identity
    can date."""
    try:
        with (fitz.open(stream=pdf, filetype="pdf") if isinstance(pdf, bytes) else
              fitz.open(pdf, filetype="pdf")) as document:
            return any(not _page_words(page) for page in document)
    except RuntimeError:
        return False  # Unreadable PDFs fail visibly in the report job.


def _has_image_only_pages(paths: list[str]) -> bool:
    for path in paths:
        with fitz.open(path) as document:
            if any(not _page_words(page) for page in document):
                return True
    return False


def _scan_summary(numbers: list[int], notes: list[str], collected_date: str | None) -> list[str]:
    """The scanned-page outcome digest printed at the top of the staff notes."""
    kept, excluded, pages, batch, mismatches = {}, [], [], [], []
    for note in notes:
        if note.startswith("STAFF REVIEW"):
            mismatches.append(note)
        elif match := re.match(r"source=scan page (\d+) accepted ", note):
            kept[int(match[1])] = kept.get(int(match[1]), 0) + 1
        elif match := re.match(r"source=scan page (\d+) excluded (.+?): (.+)$", note):
            excluded.append(f"page {match[1]}: {match[2]} - {match[3]}")
        elif match := re.match(r"source=scan page (\d+): (.+)$", note):
            pages.append(f"page {match[1]}: {match[2]}")
        elif note.startswith("source=scan pages") and "identified by staff-entered" not in note:
            batch.append(note.split(": ", 1)[1] if ": " in note else note)
    identity = (f"staff-entered patient name and Collected {collected_date}" if collected_date
                else "printed page identity (no staff Collected date)")
    lines = [f"SCANNED BLOODWORK - pages {', '.join(map(str, numbers))} - identified by {identity}",
             f"  Kept: {sum(kept.values())} results"
             + (f" ({', '.join(f'page {page}: {count}' for page, count in sorted(kept.items()))})" if kept else ""),
             f"  Excluded: {len(excluded)} results"]
    lines += [f"    - {item}" for item in excluded]
    if pages:
        lines.append("  Page outcomes:")
        lines += [f"    - {item}" for item in pages]
    lines += [f"  {item}" for item in batch + mismatches]
    return lines


_DOB_RE = re.compile(r"\b(?:DOB|D\.O\.B\.?|Date of Birth|Birth Date)\s*[:#]?\s*(" + _PRINTED_DATE_RE.pattern + ")",
                     re.IGNORECASE)


def _printed_dob(text):
    import scan_dexa
    return scan_dexa.scan_date(text) if text else None


def _age_from_dob(dob_sources: list, collected: str | None) -> dict:
    """Age on the collection date from the printed date(s) of birth. The DOB itself is not returned.
    Sources that disagree, or a DOB after the collection date, give no age and a staff notice."""
    if not dob_sources:
        return {"age_from_dob": None, "dob_notes": []}
    labels = ", ".join(dict.fromkeys(label for label, _ in dob_sources))
    if len({dob for _, dob in dob_sources}) > 1:
        return {"age_from_dob": None, "dob_conflict": True, "dob_notes": [
            f"DOB CONFLICT: the printed dates of birth differ between sources ({labels}); no age is shown in the "
            "report - confirm the patient's age"]}
    day = _normalize_date_for_matching(collected or "")
    if not isinstance(day, tuple):
        return {"age_from_dob": None, "dob_notes": [
            "AGE NOT COMPUTED: a date of birth is printed but there is no bloodwork collection date"]}
    dob = dob_sources[0][1]
    age = day[0] - dob[0] - ((day[1], day[2]) < (dob[1], dob[2]))
    if not 0 <= age <= 120:
        return {"age_from_dob": None, "dob_conflict": True, "dob_notes": [
            f"DOB CONFLICT: the printed date of birth ({labels}) does not fit the collection date; no age is shown"]}
    return {"age_from_dob": age, "dob_notes": []}


def scan_dexa_incomplete_notice(exclusions: list) -> list[str]:
    import scan_dexa

    return scan_dexa.incomplete_notice(exclusions)


def scan_collected_date(text: str) -> str:
    import scan_bloodwork as scan

    return scan.staff_collected_date(text)


def _extract_scan_bloodwork(pages, digital_pages, client, row_audit, patient_name=None, collected_date=None,
                            dob_sink=None, name_sink=None, row_exclusions=None):
    import scan_bloodwork as scan

    numbers = [page.number + 1 for page in pages]
    notes = []
    failures = []
    # Staff-entered patient and Collected date identify every scanned page in place of printed metadata.
    staff = bool(patient_name and collected_date)
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            failures = [(number, [], "client gate: no API key") for number in numbers]
            if staff:
                scan.gate_staff_identified_reads([], collected_date, _CELL_VALUE_RE, patient_name, failures)
            else:
                scan.gate_reads([], set(), _CELL_VALUE_RE, _PRINTED_DATE_RE, _normalize_date_for_matching,
                                failures)
            return [], [], [f"source=scan pages {numbers}: client gate failed: no API key; manual review required"]
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                           timeout=ANTHROPIC_CALL_TIMEOUT_SECONDS, max_retries=ANTHROPIC_MAX_RETRIES)
    reads = []
    # The staff-identified gate decides on a 2-of-3 majority, so a third read is made where two disagree.
    max_reads = scan.SCAN_MAX_READS if staff else 2
    for page in pages:
        try:
            reads.append(scan.read_page(page, client, _create_anthropic_message, MODEL, max_reads))
        except scan.ScanGateError as error:
            failures.append((page.number + 1, error.reads, str(error)))
    if name_sink is not None:
        for pair in reads:
            printed = {" ".join(page["patient_name"].split()) for page in pair if page["patient_name"]}
            if len(printed) == 1 and all(page["patient_name"] for page in pair):
                name_sink.append(("scanned lab page", pair[0]["page"], printed.pop()))
    if dob_sink is not None:
        for pair in reads:
            dobs = {_printed_dob(page.get("date_of_birth")) for page in pair}
            if len(dobs) == 1 and None not in dobs:
                dob_sink.append((f"scanned lab page {pair[0]['page']}", dobs.pop()))
    lab_codes = {
        match[1] for page in digital_pages
        for match in re.finditer(r"\(\d+\)\s*\(([A-Z][A-Z0-9]{1,5})\)", _line_text(_page_words(page)))
    }
    if staff:
        accepted, notes = scan.gate_staff_identified_reads(
            reads, collected_date, _CELL_VALUE_RE, patient_name, failures, lab_codes, row_exclusions)
        # Name-mismatch notices stay first so staff see them before the identity summary.
        notes.insert(sum(note.startswith("STAFF REVIEW") for note in notes),
                     f"source=scan pages {numbers}: identified by staff-entered patient name and "
                     f"Collected {collected_date}; printed footer/header/name/date not gated")
    else:
        digital_names = scan.digital_patient_names(digital_pages, _group_lines, _page_words)
        try:
            accepted, notes = scan.gate_reads(
                reads, digital_names, _CELL_VALUE_RE, _PRINTED_DATE_RE, _normalize_date_for_matching, failures,
                row_exclusions)
        except scan.ScanGateError as error:
            return [], [], error.notes + [
                f"source=scan pages {numbers}: {error}; ALL SCAN ROWS REJECTED; manual review required"]
    occurrences, unknown = [], []
    header = _bloodwork_header([
        (40, 90, 70, 100, "Test"), (220, 90, 260, 100, "Current"),
        (320, 90, 350, 100, "Reference"), (352, 90, 380, 100, "Range"),
    ])
    for row in accepted:
        section = {"order_id": None, "dates": [row["date"]], "heading": row["section"]}
        name = _clean_row_name(row["name"], lab_codes)
        value = row["result_text"]
        words = [(220, 120, 240, 130, value)]
        if row["reference_range"]:
            words.append((320, 120, 380, 130, row["reference_range"]))
        start, unknown_start = len(occurrences), len(unknown)
        _parse_bloodwork_row(words, header, section, occurrences, unknown, row_name=name, allow_text=True)
        for occurrence in occurrences[start:]:
            occurrence["source_label"] += f"; pages {row['page']}"
            occurrence["lab_flag"] = row["flag"] or "" if occurrence["disp_value"] else ""
        for item in unknown[unknown_start:]:
            for cell in item["cells"]:
                cell["lab_flag"] = row["flag"] if cell["present"] else None
        row_audit.append({
            "page": row["page"], "name": name, "section": row["section"],
            "occurrences": [dict(item) for item in occurrences[start:]], "unrecognized": unknown[unknown_start:],
        })
        notes.append(f"source=scan page {row['page']} accepted {row['name']!r}: "
                     f"{row['result_text']!r}, flag={row['flag']!r}, Collected={row['date']}")
    return occurrences, unknown, notes


def _merge_scan_occurrences(digital, scanned, row_audit):
    provenance = {}
    for row in row_audit:
        for occurrence in row["occurrences"]:
            key = (occurrence["name"], _normalize_date_for_matching(occurrence["date_display"]))
            provenance.setdefault(key, []).append(row["page"])
    combined = { (row["name"], _normalize_date_for_matching(row["date_display"])): row for row in digital}
    for row in scanned:
        key = (row["name"], _normalize_date_for_matching(row["date_display"]))
        previous = combined.get(key)
        if previous is not None:
            if (previous["status"], previous["value"], previous["disp_value"]) != (
                    row["status"], row["value"], row["disp_value"]):
                raise BloodworkHardStop(
                    f"{row['name']} on {row['date_display']}: conflicting results on pages {provenance[key]}")
            previous["source_label"] = previous["source_label"].split("; pages ", 1)[0] + \
                "; pages " + ", ".join(map(str, sorted(set(provenance[key]))))
        else:
            combined[key] = row
    return list(combined.values())


# ---------------------------------------------------------------------------
# Structured provider notes (see templates/provider_notes_template.md)
# ---------------------------------------------------------------------------

_NOTE_SECTIONS = ("consultation note", "patient concerns", "protocol", "marker targets", "vitality index",
                  "treatment status")
NOTE_FIX = ("To fix: put each part of the note under its own heading line, using these headings exactly: "
            "'## Consultation Note', '## Treatment Status', '## Patient Concerns', '## Protocol', "
            "'## Marker Targets', '## Vitality Index' (download the blank template from the upload page). "
            "A pasted medication list belongs under '## Protocol' as '- <name> | Cadence: <cadence>' lines.")
_STATUS_LINES = {"on trt": "on_trt", "postmenopausal and on bhrt": "postmenopausal_bhrt"}
_STATUS_VALUES = {"yes": True, "no": False, "not stated": None}
_PATIENT_SYSTEMS = ("Drive", "Pace", "Fuel", "Flow", "Repair", "Reserves", "Structure")
_VITALITY_VALUES = ("No Concern", "Some Concern", "Significant Concern", "Not Assessed")


def _parse_structured_note(note_text: str):
    """Read the documented '## ' template line by line. A line that matches its section's format is
    read; every other line is returned in `rejected` with its line number and reason, and is never
    interpreted any other way. Returns (parsed, rejected, sections_seen, accepted_text)."""
    parsed = {"protocol": [], "pain_points": [], "marker_overrides": [], "vitality_index": {},
              "treatment_status": None}
    # Blank out comments but keep their newlines so reported line numbers match the note as typed.
    text = re.sub(r"<!--.*?-->", lambda match: "\n" * match.group(0).count("\n"), note_text, flags=re.DOTALL)
    section = None
    seen = []
    rejected = []
    accepted_lines = []
    systems = {system.lower(): system for system in _PATIENT_SYSTEMS}

    def reject(number, line, reason):
        rejected.append({"line": number, "text": line, "reason": reason})

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            name = line[3:].strip().lower()
            if name not in _NOTE_SECTIONS:
                reject(number, line, "unknown section; expected one of: "
                       + ", ".join(f"'## {title.title()}'" for title in _NOTE_SECTIONS))
                section = "unknown"
            elif name in seen:
                reject(number, line, "section appears twice; only the first one is read")
                section = "duplicate"
            else:
                seen.append(name)
                section = name
                accepted_lines.append(line)
            continue
        if section is None:
            if line.startswith("# "):
                accepted_lines.append(line)
            else:
                reject(number, line, "text outside a '## ' section")
            continue
        if section in ("unknown", "duplicate"):
            reject(number, line, f"under {'an unknown' if section == 'unknown' else 'a repeated'} section "
                   "heading, so it is not read")
            continue
        if section == "consultation note":
            accepted_lines.append(line)
            continue
        if not line.startswith("- "):
            reject(number, line, f"expected a '- ' item under '## {section.title()}'")
            continue
        item = line[2:].strip()
        if section == "patient concerns":
            match = re.fullmatch(r"(?P<text>[^|\"]+?)\s*\|\s*Systems?:\s*(?P<systems>[^|]+)", item)
            names = [s.strip().lower() for s in match.group("systems").split(",")] if match else []
            if not match or not names or any(s not in systems for s in names):
                reject(number, line, "concern must read '- <concern> | Systems: <system>, ...' using "
                       + ", ".join(_PATIENT_SYSTEMS))
                continue
            parsed["pain_points"].append({"text": match.group("text"), "categories": [systems[s] for s in names]})
        elif section == "protocol":
            match = re.fullmatch(r"(?P<name>[^|]+?)(?:\s*\|\s*Cadence:\s*(?P<cadence>[^|]+?))?", item)
            if not match:
                reject(number, line, "protocol item must read '- <compound> | Cadence: <cadence>'")
                continue
            known = lookup_protocol_item(match.group("name"))
            config = known[1] if known else {}
            parsed["protocol"].append({
                "name": known[0] if known else match.group("name"),
                "cadence": match.group("cadence"),
                "target_categories": list(config.get("typical_categories", [])),
                "lab_visible": config.get("lab_visible", True),
            })
        elif section == "marker targets":
            match = re.fullmatch(r"(?P<marker>[^:]+):\s*(?P<lo>\d+(?:\.\d+)?)\s*[-–]\s*(?P<hi>\d+(?:\.\d+)?)", item)
            marker = markers_reference_lookup(match.group("marker")) if match else None
            if not marker:
                reject(number, line, "target must read '- <recognized marker>: <lo>-<hi>'"
                       + (f"; {match.group('marker').strip()!r} is not a recognized marker" if match else ""))
                continue
            parsed["marker_overrides"].append({"marker": marker[0], "lo": float(match.group("lo")),
                                               "hi": float(match.group("hi"))})
        elif section == "treatment status":
            match = re.fullmatch(r"(?P<label>[^:]+):\s*(?P<value>.+)", item)
            key = _STATUS_LINES.get(match.group("label").strip().lower()) if match else None
            value = match.group("value").strip().lower() if match else None
            if key is None or value not in _STATUS_VALUES:
                reject(number, line, "status must read '- On TRT: Yes / No / Not stated' or "
                       "'- Postmenopausal and on BHRT: Yes / No / Not stated'")
                continue
            parsed["treatment_status"] = {**(parsed["treatment_status"] or {}), key: _STATUS_VALUES[value]}
        elif section == "vitality index":
            match = re.fullmatch(r"(?P<label>[^:]+):\s*(?P<value>.+)", item)
            label = match.group("label").strip() if match else ""
            if label == "Physical Performance":
                accepted_lines.append(line)
                continue
            if label not in scoring.VITALITY_LABELS or match.group("value").strip() not in _VITALITY_VALUES:
                reject(number, line, "Vitality Index entry must read '- <domain>: <"
                       + " / ".join(_VITALITY_VALUES) + ">' for one of " + ", ".join(scoring.VITALITY_LABELS))
                continue
            parsed["vitality_index"][label] = match.group("value").strip()
        accepted_lines.append(line)
    if "vitality index" in seen:
        for label in scoring.VITALITY_LABELS:
            parsed["vitality_index"].setdefault(label, "Not Assessed")
    return parsed, rejected, seen, "\n".join(accepted_lines)


def check_provider_note(note_text: str | None) -> dict:
    """What the parser will read from a note, for the upload page's format check."""
    if not note_text or not note_text.strip():
        return {"read": False, "sections": [], "rejected": []}
    _, rejected, seen, _ = _parse_structured_note(note_text)
    return {"read": bool(seen), "sections": [title.title() for title in seen], "rejected": rejected,
            "fix": "" if seen else NOTE_FIX}


def parse_provider_note(note_text: str | None) -> dict:
    """Read only lines that follow the structured template; list every other line for staff with its
    reason. Free text is never mined for protocol, concerns, targets or status."""
    result = {"accepted": False, "protocol": [], "pain_points": [], "marker_overrides": [],
              "vitality_index": {}, "other_notes": [], "accepted_text": None, "treatment_status": None}
    if not note_text or not note_text.strip():
        return result
    parsed, rejected, seen, accepted_text = _parse_structured_note(note_text)
    listing = [f"    line {item['line']}: {item['text']!r} - {item['reason']}" for item in rejected]
    if not seen:
        result["other_notes"].append(
            "PROVIDER NOTE REJECTED - no structured '## ' sections found - NOTE WAS NOT READ (the rest of the "
            "report was built without it). " + NOTE_FIX)
        result["other_notes"].extend(listing)
        return result
    result.update(parsed, accepted=True, accepted_text=accepted_text)
    if rejected:
        result["other_notes"].append(
            f"PROVIDER NOTE: {len(rejected)} LINE(S) NOT READ - the rest of the note was read; fix these lines "
            "in the note or enter them manually:")
        result["other_notes"].extend(listing)
    return result


def extract(labs_pdf: str | None, dexa_pdfs: list[str], note_text: str | None,
            patient_name: str | None = None, audit_root: str | None = None,
            client: Anthropic | None = None, collected_date: str | None = None, age: int | None = None) -> dict:
    """Parse readable labs and notes deterministically; gate scan transcription; extract DEXA via Claude."""
    collected_date = scan_collected_date(collected_date) if collected_date else None
    occurrences, unrecognized, lab_review_notes = [], [], []
    row_audit = []
    scan_numbers = []
    parse_exclusions = []  # pages/sections that could not be parsed deterministically
    scan_row_exclusions = []  # scanned result rows left out (reads disagree or a check failed): INCOMPLETE
    lab_text_pages, scan_notes = [], []
    dob_sources = []  # (source label, (y, m, d)); used only to compute age, never stored
    printed_names = []  # (source, page or None, name as printed) for the staff-notes header
    if labs_pdf:
        with fitz.open(labs_pdf) as document:
            pages = list(document)
            digital_pages = [page for page in pages if _page_words(page)]
            lab_text_pages = [page.number + 1 for page in digital_pages]
            scans = [page for page in pages if not _page_words(page)]
            scan_numbers = [page.number + 1 for page in scans]
            if digital_pages:
                import scan_bloodwork
                printed_names.extend(("lab PDF text pages", None, " ".join(name.split())) for name in sorted(
                    scan_bloodwork.digital_patient_names(digital_pages, _group_lines, _page_words)))
            for page in digital_pages:
                for match in _DOB_RE.finditer(_page_text(page)):
                    if (dob := _printed_dob(match.group(1))) is not None:
                        dob_sources.append((f"lab page {page.number + 1}", dob))
            if digital_pages:
                occurrences, unrecognized = _parse_bloodwork_tables(
                    digital_pages, row_audit=row_audit, review_notes=lab_review_notes,
                    exclusions=parse_exclusions)
            if scans:
                scanned, scan_unknown, scan_notes = _extract_scan_bloodwork(
                    scans, digital_pages, client, row_audit, patient_name, collected_date, dob_sources,
                    printed_names, scan_row_exclusions)
                occurrences = _merge_scan_occurrences(occurrences, scanned, row_audit)
                unrecognized.extend(scan_unknown)
                lab_review_notes.extend(scan_notes)
    scan_summary = _scan_summary(scan_numbers, scan_notes, collected_date) if scan_numbers else []
    lab_items, unrecognized, lab_notes = lab_reported.build(unrecognized)
    lab_review_notes.extend(lab_notes)
    dexa_exclusions = []  # DEXA pages not attributed to this patient: listed under INCOMPLETE
    dexa_confirmed = []  # (file, page) keys confirmed as this patient's
    dexa_info = {}  # pages that could not be read, ages of accepted pages
    dexa_history, dexa_notes, dexa_summary = (_extract_dexa_with_claude(client, dexa_pdfs, patient_name, dob_sources,
                                                                        printed_names, dexa_exclusions, dexa_confirmed,
                                                                        age, dexa_info)
                                              if dexa_pdfs else ([], [], []))
    note = parse_provider_note(note_text)
    for match in re.finditer(r"^\s*Patient(?: Name)?\s*:\s*(.+?)\s*$", note["accepted_text"] or "", re.I | re.M):
        printed_names.append(("provider note", None, " ".join(match.group(1).split())))

    dated = [occ for occ in occurrences
             if isinstance(_normalize_date_for_matching(occ["date_display"]), tuple)
             and (occ["value"] is not None or occ["disp_value"])]
    first = min(dated, key=lambda occ: _normalize_date_for_matching(occ["date_display"]), default=None)
    latest = max(dated, key=lambda occ: _normalize_date_for_matching(occ["date_display"]), default=None)
    single_draw = first is None or (_normalize_date_for_matching(first["date_display"])
                                    == _normalize_date_for_matching(latest["date_display"]))
    extracted = {
        "name": patient_name or "",
        "first_draw_date": "" if single_draw else first["date_display"],
        "latest_draw_date": latest["date_display"] if latest else "",
        "marker_occurrences": occurrences,
        "dexa_history": dexa_history,
        "protocol": note["protocol"],
        "pain_points": note["pain_points"],
        "marker_overrides": note["marker_overrides"],
        "vitality_index": note["vitality_index"],
        "provider_note_raw": note["accepted_text"] if note["accepted"] else None,
        "treatment_status": note["treatment_status"],
        "unrecognized_markers": unrecognized,
        "lab_reported": lab_items,
        "source_rows": row_audit,
        "scan_summary": scan_summary,
        "parse_exclusions": parse_exclusions,
        "scan_row_exclusions": scan_row_exclusions,
        "dexa_summary": dexa_summary,
        "dexa_incomplete": scan_dexa_incomplete_notice(dexa_exclusions),
        "dexa_confirmed_pages": dexa_confirmed,
        "dexa_info": dexa_info,
        "dexa_exclusions": dexa_exclusions,
        "collected_date": collected_date,
        "lab_pages": {"text": lab_text_pages, "scanned": scan_numbers},
        "preflight": preflight_items(parse_exclusions, scan_notes if scan_numbers else [], scan_row_exclusions,
                                     dexa_exclusions, dexa_info, unrecognized) + note_preflight(note),
        "provider_note_status": note_status(note_text, note),
        "printed_names": printed_names,
        **_age_from_dob(dob_sources, latest["date_display"] if latest else None),
        "other_notes": [*lab_review_notes, *note["other_notes"]],
    }

    audit_base = Path(audit_root or tempfile.gettempdir()) / "celldeep_extraction_audits"
    audit_base.mkdir(parents=True, exist_ok=True)
    audit_dir = Path(tempfile.mkdtemp(prefix="extraction_", dir=audit_base))  # no name: paths reach logs
    occurrences_path = audit_dir / "marker-occurrences.json"
    occurrences_path.write_text(json.dumps(occurrences, indent=2, ensure_ascii=False), encoding="utf-8")
    (audit_dir / "source-rows.json").write_text(
        json.dumps(row_audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Extraction audit saved: {occurrences_path}")
    return extracted


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
    postmenopausal_bhrt, on_trt = provider_statuses(extracted, notice)
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
                       disp_range=f"{clinic_config.LAB_RANGE_LABEL} {active_lab_range['display']}")
            missing_threshold = False
        elif missing_threshold:
            cfg = dict(cfg, disp_range=clinic_config.NO_RANGE_LABEL)  # never a library placeholder
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
                lab_flag_then=raw.get("lab_flag_then"), lab_flag_now=raw.get("lab_flag_now"),
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
                lab_flag_then=raw.get("lab_flag_then"), lab_flag_now=raw.get("lab_flag_now"),
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
        cns_domains=extracted.get("cns_domains"),
        vitality_index=scoring.normalize_vitality_index(extracted.get("vitality_index")),
        provider_note_raw=extracted.get("provider_note_raw"),
        lab_reported=[LabReportedResult(name=item["name"], group=item["group"], results=item["results"])
                      for item in extracted.get("lab_reported", [])],
    )
    censored = [f"{m.name} {disp!r} on {date}" + (f" (lab flag {flag})" if flag else "")
                for m in markers
                for disp, value, date, flag in ((m.disp_then, m.then, m.then_date_display, m.lab_flag_then),
                                                (m.disp_now, m.now, m.now_date_display, m.lab_flag_now))
                if scoring.is_censored(disp, value)]
    differing = [f"{m.name} {m.disp_now!r} (lab flag {m.lab_flag_now}, CellDeep {m.now_tier})"
                 for m in markers if template.lab_flag_differs(m)]
    if differing:
        notice.other_notes.append("LAB FLAG DIFFERS FROM CELLDEEP STATUS: " + "; ".join(differing))
    if censored:
        notice.other_notes.append("CENSORED RESULTS (shown exactly as printed, excluded from every score): "
                                  + "; ".join(censored))
    notice.other_notes.extend(extracted.get("other_notes", []))
    notice.scan_summary = list(extracted.get("scan_summary", []))
    notice.dexa_summary = list(extracted.get("dexa_summary", []))
    notice.other_notes.extend(dexa_staleness_notes(record.dexa_history, extracted.get("latest_draw_date")))
    notice.other_notes.extend(dexa_score_notes(record))
    exclusions = extracted.get("parse_exclusions", [])
    if exclusions:
        pages = sorted({item["page"] for item in exclusions})
        notice.incomplete = [
            f"INCOMPLETE - pages/sections excluded: lab PDF page(s) {', '.join(map(str, pages))} could not be "
            "parsed deterministically; their results are NOT in this report - review them by hand",
            *[f"  - page {item['page']} ({item['section']}): {item['reason']}" for item in exclusions]]
    notice.incomplete.extend(f"INCOMPLETE - row excluded: {item['name']} ({scan_row_reason(item)}) - "
                             f"scanned lab page {item['page']}; not in this report"
                             for item in extracted.get("scan_row_exclusions", []))
    notice.incomplete.extend(extracted.get("dexa_incomplete", []))
    notice.name_header = name_header(extracted.get("name"), extracted.get("printed_names", []))
    unrecognized_names = {" ".join(item["raw_name"].split()).casefold()
                          for item in extracted.get("unrecognized_markers", [])}
    notice.other_notes[:0] = coverage_gaps(extracted.get("source_rows", []), record, unrecognized_names)
    notice.staff_check = staff_check_block(extracted, record, notice)
    return record, notice


class GenerationAborted(RuntimeError):
    """Staff stopped the report at the confirmation step; nothing was written."""


_SCAN_PAGE_EXCLUDED_RE = re.compile(r"source=scan page (\d+): (.+); page excluded$")


def scan_row_reason(item: dict) -> str:
    """Why a scanned result row was excluded, with what each read printed: 'reads disagree: 3.1 / 2.8 / 4.0'
    when no two reads agree, otherwise the failed check and the reads."""
    if item.get("reason", "agreement gate").startswith("agreement gate"):
        return f"reads disagree: {item['reads']}"
    return f"{item['reason'].split(': ', 1)[-1]}; reads: {item['reads']}"


def note_preflight(note: dict) -> list[str]:
    """The confirmation-step line for a provider note that was given but not read at all, or read in part."""
    if not note["other_notes"]:
        return []
    if not note["accepted"]:
        return ["Provider note: NOT READ - it has no '## ' section headings, so its protocol, concerns, targets and "
                "Vitality Index are not in the report (the staff notes list the required headings)"]
    lines = sum(line.startswith("    line ") for line in note["other_notes"])
    return [f"Provider note: {lines} line(s) outside the template were not read (listed in the staff notes)"]


def note_status(note_text: str | None, note: dict) -> str:
    if not note_text or not note_text.strip():
        return "none given"
    if not note["accepted"]:
        return "NOT READ - no '## ' section headings (see the notes below for the required headings)"
    unread = sum(line.startswith("    line ") for line in note["other_notes"])
    return f"read; {unread} line(s) outside the template not read" if unread else "read"


def preflight_items(parse_exclusions, scan_notes, scan_row_exclusions, dexa_exclusions, dexa_info,
                    unrecognized=()) -> list[str]:
    """Every lab page/section, scanned result row, unrecognized printed result and DEXA page left out of the
    report, one line each, for the confirmation step before the report is built (staff can stop there). Only
    result rows count: a heading or label row with no value is never listed. Empty when nothing was left out."""
    items = [f"Lab PDF page {item['page']} ({item['section']}): {item['reason']}" for item in parse_exclusions]
    for note in scan_notes:
        if match := _SCAN_PAGE_EXCLUDED_RE.match(note):
            items.append(f"Lab PDF page {match[1]} (scanned): {match[2]}")
    items += [f"Lab PDF page {item['page']} (scanned): result {item['name']} excluded - {scan_row_reason(item)}"
              for item in scan_row_exclusions]
    items += [f"DEXA file {key[0]} page {key[1]}: {reason}" for key, reason in dexa_exclusions]
    items += [f"DEXA file {key[0]} page {key[1]}: could not be read ({reason})"
              for key, reason in dexa_info.get("failed", [])]
    if unrecognized:
        names = ", ".join(dict.fromkeys(" ".join(item["raw_name"].split()) for item in unrecognized))
        items.append(f"Lab PDF: {len(unrecognized)} printed result(s) with a test name the tool does not recognize, "
                     f"left out of the report and listed in the staff notes: {names}")
    return items


def staff_check_block(extracted: dict, record: PatientRecord, notice: ExtractionReviewNotice) -> list[str]:
    """The one-screen STAFF CHECK that opens the staff notes. Only what the report and staff notes already
    hold: entered name and date, scan dates and printed ages, page numbers, counts and mismatch notices."""
    info = extracted.get("dexa_info", {})
    excluded_dexa = extracted.get("dexa_exclusions", [])
    lab = extracted.get("lab_pages", {"text": [], "scanned": []})
    lab_excluded = sorted({item["page"] for item in extracted.get("parse_exclusions", [])})
    scan_excluded = sorted({int(m[1]) for note in extracted.get("other_notes", [])
                            if (m := _SCAN_PAGE_EXCLUDED_RE.match(note))})
    ages = sorted({age for _, age in info.get("accepted", []) if age}, key=float)
    lines = ["STAFF CHECK - confirm before sending this report",
             f"  Patient name (entered): {extracted.get('name') or '(none entered)'}",
             f"  Bloodwork Collected date (entered): {extracted.get('collected_date') or '(none entered)'}"]
    if record.dexa_history:
        dates = ", ".join(reading.date_display + (" (body fat % computed)" if "body_fat_pct" in reading.computed
                                                  else " (body fat % estimated)" if "body_fat_pct" in reading.estimated
                                                  else "") for reading in record.dexa_history)
        lines.append(f"  DEXA scan dates accepted: {dates}" + (f"; ages printed on accepted pages: {', '.join(ages)}"
                                                               if ages else ""))
    elif info or excluded_dexa:
        lines.append("  DEXA scan dates accepted: none")
    for key, reason in excluded_dexa:
        lines.append(f"  DEXA EXCLUDED file {key[0]} page {key[1]}: "
                     + ("prints a different patient name" if reason.startswith("prints patient name") else reason))
    for key, reason in info.get("failed", []):
        lines.append(f"  DEXA EXCLUDED file {key[0]} page {key[1]}: could not be read ({reason})")
    if record.dexa_history:
        lines.append(f"  {dexa_bloodwork_gap(record.dexa_history, extracted.get('latest_draw_date'))}")
    if lab["text"] or lab["scanned"]:
        kept_text = [page for page in lab["text"] if page not in lab_excluded]
        kept_scan = [page for page in lab["scanned"] if page not in scan_excluded]
        lines.append(f"  Lab pages accepted: {', '.join(map(str, sorted(kept_text + kept_scan))) or 'none'}"
                     + (f" (scanned: {', '.join(map(str, kept_scan))})" if kept_scan else ""))
        for item in extracted.get("parse_exclusions", []):
            lines.append(f"  LAB EXCLUDED page {item['page']} ({item['section']}): {item['reason']}")
        for note in extracted.get("other_notes", []):
            if match := _SCAN_PAGE_EXCLUDED_RE.match(note):
                lines.append(f"  LAB EXCLUDED page {match[1]} (scanned): {match[2]}")
    if extracted.get("provider_note_status"):
        lines.append(f"  Provider note: {extracted['provider_note_status']}")
    row_exclusions = extracted.get("scan_row_exclusions", [])
    disagree = sum(scan_row_reason(item).startswith("reads disagree") for item in row_exclusions)
    lines.append(f"  Scored markers: {len(record.markers)}; rows excluded (reads disagree): {disagree}; "
                 f"rows excluded by other checks: {len(row_exclusions) - disagree}; "
                 f"unrecognized rows (staff list only): {len(extracted.get('unrecognized_markers', []))}")
    mismatches = [line.strip() for line in notice.name_header if "NAME MISMATCH" in line]
    mismatches += [note for note in [*notice.other_notes, *notice.dexa_summary] if "NAME MISMATCH" in note]
    lines += [f"  {line.strip()}" for line in mismatches] or ["  Name mismatches: none"]
    return [*lines, STAFF_CHECK_END]


def dexa_bloodwork_gap(dexa_history: list, latest_draw: str | None) -> str:
    """One STAFF CHECK line: the scan shown as "Where you are now" and how far it is from the latest draw."""
    scans = scoring.body_composition_scans(dexa_history)
    if not scans:
        return "DEXA vs bloodwork: no accepted scan with body composition"
    current = scans[-1].date_display
    scan_day, draw_day = _normalize_date_for_matching(current), _normalize_date_for_matching(latest_draw or "")
    if not (isinstance(scan_day, tuple) and isinstance(draw_day, tuple)):
        return f"DEXA vs bloodwork: current DEXA scan {current}; no bloodwork draw date to compare"
    gap = (date(*draw_day) - date(*scan_day)).days
    when = f"{gap} days before" if gap >= 0 else f"{-gap} days after"
    flag = " - OLDER THAN THE LIMIT, see the warning below" if gap > clinic_config.DEXA_STALE_DAYS else ""
    return (f"DEXA vs bloodwork: current DEXA scan {current} is {when} the latest bloodwork draw ({latest_draw}); "
            f"limit {clinic_config.DEXA_STALE_DAYS} days{flag}")


def dexa_staleness_notes(dexa_history: list, latest_draw: str | None) -> list[str]:
    """Warn staff when the scan shown as "Where you are now" (the latest accepted body-composition scan) is more
    than clinic_config.DEXA_STALE_DAYS older than the latest bloodwork draw. Nothing in the report changes."""
    complete = scoring.body_composition_scans(dexa_history)
    scan_day = _normalize_date_for_matching(complete[-1].date_display) if complete else None
    draw_day = _normalize_date_for_matching(latest_draw or "")
    if not (isinstance(scan_day, tuple) and isinstance(draw_day, tuple)):
        return []
    gap = (date(*draw_day) - date(*scan_day)).days
    if gap <= clinic_config.DEXA_STALE_DAYS:
        return []
    return [f"DEXA SCAN OLDER THAN BLOODWORK: \"Where you are now\" shows the DEXA scan of {complete[-1].date_display}, "
            f"{gap} days before the latest bloodwork draw ({latest_draw}); confirm no newer DEXA scan was left out"]


def dexa_score_notes(record: PatientRecord) -> list[str]:
    """The patient report leaves out the DEXA "% optimized" figure when it has nothing to score (it is never
    shown as "—% optimized"); this staff note says so and why."""
    if not record.dexa_history:
        return []
    scans = scoring.body_composition_scans(record.dexa_history)
    latest = scans[-1] if scans else record.dexa_history[-1]
    if dexa_percent_optimized(latest.body_fat_pct, latest.visceral_fat_area_cm2, record.sex) is not None:
        return []
    missing = [] if record.sex in ("male", "female") else ["no sex entered for the body fat ranges"]
    if latest.body_fat_pct is None:
        missing.append("no body fat % (printed, or computable from printed fat and lean mass) for that scan")
    if latest.visceral_fat_area_cm2 is None:
        missing.append("no VAT area printed for that scan")
    return [f"DEXA SCORE NOT SHOWN: the patient report shows no DEXA \"% optimized\" for the scan of "
            f"{latest.date_display} ({'; '.join(missing)}); the Structure score is left out of the overall score"]


def name_header(staff_name: str | None, printed: list) -> list[str]:
    """'This report is for <staff-entered name>' and the name each source printed, flagging mismatches.
    Only names are listed; no date of birth or ID is read into it."""
    import scan_bloodwork

    lines = [f"This report is for {staff_name or '(no name entered)'} (staff-entered)"]
    sources = ("lab PDF text pages", "scanned lab page", "DEXA", "provider note")
    for source in sources:
        entries = [(where, name) for kind, where, name in printed if kind == source]
        if not entries:
            lines.append(f"  {source}: no name printed")
            continue
        by_name = {}
        for where, name in entries:
            by_name.setdefault(name, []).append(where)
        for name, places in by_name.items():
            where = [str(place) for place in places if place is not None]
            location = f" ({', '.join(where)})" if where else ""
            verdict = ("matches" if scan_bloodwork.names_match(staff_name, name)
                       else "NAME MISMATCH - confirm this source belongs to the patient")
            lines.append(f"  {source}{location}: {name} - {verdict}")
    return lines


def coverage_gaps(source_rows: list[dict], record: PatientRecord, unrecognized_names: set[str]) -> list[str]:
    """Every printed result row must reach the patient report or the staff notes; never vanish."""
    scored = {marker.name for marker in record.markers}
    shown = {item.name for item in record.lab_reported}
    gaps = []
    for row in source_rows:
        names = [occurrence["name"] for occurrence in row["occurrences"] if occurrence["name"] not in scored]
        for unknown in row["unrecognized"]:
            match = lab_reported.lookup(unknown["raw_name"], lab_reported._heading(unknown))
            printed = " ".join(unknown["raw_name"].split())
            if printed.casefold() not in unrecognized_names and (match is None or match[0] not in shown):
                names.append(printed)
        gaps.extend(f"COVERAGE GAP - {name!r} printed on page {row['page']} is in neither the patient "
                    "report nor these notes; manual review required" for name in names)
    return gaps


def markers_reference_lookup(raw_name: str):
    from markers_reference import lookup_marker
    return lookup_marker(raw_name)


def resolve_age(extracted: dict, staff_age: int | None) -> int | None:
    """Printed DOB + collection date wins; conflicting DOBs mean no age at all; otherwise the staff entry."""
    notes = extracted.setdefault("other_notes", [])
    notes[:0] = extracted.get("dob_notes", [])
    if extracted.get("dob_conflict"):
        return None
    computed = extracted.get("age_from_dob")
    if computed is None:
        return staff_age
    if staff_age is not None and staff_age != computed:
        notes.insert(0, f"AGE CHECK: staff-entered age {staff_age} differs from the age computed from the printed "
                        f"date of birth and collection date ({computed}); the report uses {computed}")
    return computed


def run(labs_pdf, dexa_pdfs, note_text, patient_name, age, sex, out_path, vitality_index=None,
        collected_date=None, confirm=None):
    """Build the patient report and staff notes. confirm(items) is called after the documents are read and
    before anything is built, only when pages or rows were left out; returning False stops the run
    (GenerationAborted) so staff can fix the inputs instead of sending a report with gaps."""
    raw_lab_text = _pdf_text(labs_pdf)
    raw_dexa_text = "\n".join(_pdf_text(path) for path in dexa_pdfs)
    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=ANTHROPIC_CALL_TIMEOUT_SECONDS,
        max_retries=ANTHROPIC_MAX_RETRIES,
    ) if dexa_pdfs else None

    print("Step 1/3: parsing source documents...")
    extracted = extract(labs_pdf, dexa_pdfs, note_text, patient_name=patient_name, client=client,
                        collected_date=collected_date, age=age)
    if confirm is not None and extracted["preflight"] and not confirm(list(extracted["preflight"])):
        raise GenerationAborted("stopped by staff at the confirmation step")
    extracted["name"] = patient_name
    extracted["age"] = resolve_age(extracted, age)
    extracted["sex"] = sex
    if vitality_index:
        extracted["vitality_index"] = vitality_index
    # Protocol comes only from the note's structured Protocol section, never from scanning its prose.
    completeness_notice = verify_extraction_completeness(
        extracted, provider_note_text="", lab_text=raw_lab_text, dexa_text=raw_dexa_text,
        dexa_scanned=_has_image_only_pages(dexa_pdfs),
    )

    print("Step 2/3: scoring (deterministic, no AI)...")
    record, notice = score_and_build_record(extracted)
    notice.unrecognized_markers.extend(UnrecognizedMarker(**raw) for raw in extracted["unrecognized_markers"])
    notice.other_notes.extend(completeness_notice.other_notes)

    print("Step 3/3: filling report templates (deterministic, no AI)...")
    copy = _sanitize_em_dashes(build_copy(record))
    with open(f"{_diagnostic_path_prefix(patient_name)}_report_copy.json", "w", encoding="utf-8") as f:
        json.dump(copy, f, indent=2, ensure_ascii=False)

    # The scan image is page 1 of the first DEXA PDF: embed it only when that page passed the name gate.
    page_one_confirmed = (1, 1) in extracted.get("dexa_confirmed_pages", [])
    dexa_images = _extract_dexa_scan_images(dexa_pdfs) if page_one_confirmed else []
    first_image = dexa_images[0] if dexa_images else None
    if first_image and record.dexa_history:
        record.dexa_history[0].scan_image_b64 = first_image[0]
    dexa_img_b64 = first_image[0] if first_image else None

    print("Rendering PDF...")
    template.render(record, copy, out_path, dexa_img_b64=dexa_img_b64)

    review_path = _write_review_notes(patient_name, notice)
    review = format_review_notice(notice)
    if review:
        # Staff notes contain names and values; logs get only a count.
        print(f"\nStaff review items: {len(review.splitlines()) - 1} line(s) written to the QA file")
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
    ap.add_argument("--collected-date", help="Collected date identifying scanned bloodwork pages (MM/DD/YYYY)")
    ap.add_argument("--out", default="report.pdf")
    args = ap.parse_args()

    note_text = None
    if args.note:
        with open(args.note) as f:
            note_text = f.read()

    run(args.labs, args.dexa, note_text, args.patient_name, args.age, args.sex, args.out,
        collected_date=args.collected_date)
