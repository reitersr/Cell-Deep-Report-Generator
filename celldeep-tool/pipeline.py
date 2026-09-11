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
from markers_reference import MARKER_LIBRARY, DATA_TO_PATIENT_CATEGORY, NARRATIVE_CATEGORY_OVERRIDE
from protocol_reference import PROTOCOL_LIBRARY, lookup_protocol_item
from unknown_marker_policy import UnrecognizedMarker, ExtractionReviewNotice, format_review_notice
from extraction_prompt import EXTRACTION_SYSTEM_PROMPT, build_extraction_user_message
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


def _extract_dexa_scan_image(dexa_pdfs: list[str]) -> str | None:
    """Extract a confidently identified portrait-oriented embedded DEXA image."""
    candidates = []
    for pdf_path in dexa_pdfs:
        with fitz.open(pdf_path) as document:
            for page_number, page in enumerate(document, start=1):
                page_heading = page.get_text().lower()
                heading_match = "body composition" in page_heading or "color coding" in page_heading
                for image in page.get_images(full=True):
                    xref = image[0]
                    extracted = document.extract_image(xref)
                    width = extracted.get("width", 0)
                    height = extracted.get("height", 0)
                    aspect = height / width if width else 0
                    area = width * height
                    print(f"DEXA image candidate: page={page_number} dimensions={width}x{height} aspect={aspect:.3f}")
                    if width < 80 or height < 80 or aspect < 1.1 or aspect > 3.5:
                        continue
                    candidates.append((heading_match, area, pdf_path, xref))
    if not candidates:
        return None
    preferred = [candidate for candidate in candidates if candidate[0]] or candidates
    preferred.sort(key=lambda candidate: candidate[1], reverse=True)
    if len(preferred) > 1 and preferred[0][1] <= preferred[1][1] * 1.15:
        print("DEXA image selection: ambiguous portrait candidates, omitting scan image")
        return None
    _, _, selected_pdf, xref = preferred[0]
    with fitz.open(selected_pdf) as document:
        pixmap = fitz.Pixmap(document, xref)
        png_bytes = pixmap.tobytes("png")
    return base64.b64encode(png_bytes).decode("ascii")


def _parse_json_response(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = repair_json(text)
        return json.loads(repaired)


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


def extract(client: Anthropic, labs_pdf: str | None, dexa_pdfs: list[str], note_text: str | None) -> dict:
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
    )
    text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
    raw_text = "".join(text_blocks)
    with open("/tmp/last_extraction_raw.txt", "w") as f:
        f.write(raw_text)
    return _parse_json_response(raw_text)


def score_and_build_record(extracted: dict) -> tuple[PatientRecord, ExtractionReviewNotice]:
    """Step 2: deterministic. No AI. Takes extraction's structured output, matches markers against
    the reference library, computes tiers/percentages via scoring.py (the same math as data.py),
    and assembles the final PatientRecord. This is where 'never infer' is enforced in code, not
    just in a prompt — an unmatched marker CANNOT reach the record with a guessed threshold."""
    notice = ExtractionReviewNotice()
    markers = []

    for raw in extracted.get("markers", []):
        match = markers_reference_lookup(raw["name"])
        if match is None:
            notice.unrecognized_markers.append(UnrecognizedMarker(
                raw_name=raw["name"], raw_value=str(raw.get("now", "")),
                raw_unit=raw.get("unit"), raw_range=raw.get("disp_range"),
            ))
            continue
        canonical, cfg = match
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
        scoring.attach_scores(m)   # computes now_tier/then_tier/pct in place — pure math, no AI
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

    record = PatientRecord(
        name=extracted["name"], age=extracted.get("age"), sex=extracted.get("sex"),
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
    return _parse_json_response(raw_text)


def run(labs_pdf, dexa_pdfs, note_text, patient_name, age, sex, out_path):
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    raw_lab_text = _pdf_text(labs_pdf)
    raw_dexa_text = "\n".join(_pdf_text(path) for path in dexa_pdfs)

    print("Step 1/3: extracting raw material...")
    extracted = extract(client, labs_pdf, dexa_pdfs, note_text)
    extracted.setdefault("name", patient_name)
    extracted.setdefault("age", age)
    extracted.setdefault("sex", sex)
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

    dexa_img_b64 = _extract_dexa_scan_image(dexa_pdfs)
    if dexa_img_b64 and record.dexa_history:
        record.dexa_history[-1].scan_image_b64 = dexa_img_b64

    print("Rendering PDF...")
    template.render(record, copy, out_path, dexa_img_b64=dexa_img_b64, review_notice=notice)

    review = format_review_notice(notice)
    if review:
        print("\n" + review)
    print(f"\nDone. Report saved to {out_path}")


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
