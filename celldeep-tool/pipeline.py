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
        r"|\bon\s+testosterone\s+injections?\b",
        text,
    )
    negative_trt = re.search(r"\b(?:not|never|no longer)\s+(?:on\s+)?(?:trt|testosterone(?: replacement therapy| injections?| therapy))\b", text)
    on_trt = True if trt and not negative_trt else None
    return bhrt_status, on_trt


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
    for marker in extracted.get("markers", []):
        match = markers_reference_lookup(marker.get("name", ""))
        if match:
            extracted_markers.add(match[0])
    lab_lower = lab_text.lower()
    for canonical, config in MARKER_LIBRARY.items():
        if canonical.lower() not in lab_lower and not any(alias.lower() in lab_lower for alias in config.get("aliases", [])):
            continue
        if canonical not in extracted_markers:
            warning = (f"WARNING: MARKER '{canonical}' FOUND IN SOURCE BUT MISSING FROM EXTRACTION - "
                       "NEEDS HUMAN REVIEW")
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
    return _parse_json_response(raw_text, patient_name=patient_name)


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
    patient_sex = extracted.get("sex")
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

    for raw in extracted.get("markers", []):
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
        if override is None and has_missing_thresholds(cfg):
            # Data error in the reference library (or a bad sex resolution) - exclude just this
            # one marker rather than letting a None optimal/moderate crash the whole report.
            error_line = (f"ERROR: missing threshold for {canonical} / {patient_sex or 'unknown'} "
                          "- marker excluded from scoring")
            scoring_log_lines.append(error_line)
            notice.other_notes.append(error_line)
            continue
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
                is_good_then=raw.get("is_good_then"), is_good_now=raw.get("is_good_now"),
                full_history=raw.get("full_history", []),
            )
            scoring_log_lines.append(f"OVERRIDE APPLIED: {canonical} range set to {lo}-{hi} per provider note")
        else:
            m = Marker(
                name=canonical, category=cfg["category"], unit=cfg["unit"], kind=cfg["kind"],
                disp_range=cfg["disp_range"],
                optimal=cfg.get("optimal"), moderate=cfg.get("moderate"), direction=cfg.get("direction"),
                lo=cfg.get("lo"), hi=cfg.get("hi"),
                then=raw.get("then"), now=raw.get("now"),
                disp_then=raw.get("disp_then"), disp_now=raw.get("disp_now"),
                is_good_then=raw.get("is_good_then"), is_good_now=raw.get("is_good_now"),
                full_history=raw.get("full_history", []),
            )
        scoring.attach_scores(m, sex=patient_sex, on_trt=on_trt)   # computes now_tier/then_tier/pct in place — pure math, no AI
        if m.now_tier == "unscored":
            # Defense-in-depth: should already be caught by has_missing_thresholds() above, but
            # never let a None threshold that slips through crash the report - skip it instead.
            skip_line = f"MARKER SKIPPED - missing threshold data: {canonical}"
            scoring_log_lines.append(skip_line)
            notice.other_notes.append(skip_line)
            continue
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
        name=extracted["name"], age=extracted.get("age"), sex=extracted.get("sex"),
        postmenopausal_bhrt=postmenopausal_bhrt, on_trt=on_trt,
        first_draw_date=extracted.get("first_draw_date"), latest_draw_date=extracted.get("latest_draw_date"),
        markers=markers, dexa_history=dexa_history, protocol=protocol, pain_points=pain_points,
        cns_domains=extracted.get("cns_domains"), provider_note_raw=extracted.get("provider_note_raw"),
    )
    notice.other_notes.extend(extracted.get("other_notes", []))
    return record, notice


def markers_reference_lookup(raw_name: str):
    from markers_reference import lookup_marker
    return lookup_marker(raw_name)


def generate_copy(client: Anthropic, record: PatientRecord) -> dict:
    """Step 3: the interpretive writing pass. Takes the fully-scored PatientRecord (all numbers,
    all tiers already fixed by deterministic code) and generates the sentences that go around them,
    in V23's locked voice."""
    payload = json.dumps(asdict(record), default=str, indent=2)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=GENERATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Here is the fully scored patient record:\n\n{payload}\n\n"
                                                 "Generate the interpretive copy per the instructions."}],
    )
    text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
    raw_text = "".join(text_blocks)
    with open("/tmp/pre_sanitize_copy.json", "w", encoding="utf-8") as f:
        f.write(raw_text)
    return _parse_json_response(raw_text, patient_name=record.name)


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
    copy = _sanitize_em_dashes(generate_copy(client, record))
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
