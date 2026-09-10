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
from dataclasses import asdict

from anthropic import Anthropic

from schema import PatientRecord, Marker, DexaReading, ProtocolItem, PainPoint
from markers_reference import MARKER_LIBRARY, DATA_TO_PATIENT_CATEGORY, NARRATIVE_CATEGORY_OVERRIDE
from protocol_reference import PROTOCOL_LIBRARY
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


def _pdf_content_block(path: str) -> dict:
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}


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
        max_tokens=8000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
    raw_text = "".join(text_blocks).strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()
    return json.loads(raw_text)


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

    dexa_history = [DexaReading(**d) for d in extracted.get("dexa_history", [])]

    protocol = []
    for raw in extracted.get("protocol", []):
        protocol.append(ProtocolItem(
            name=raw["name"], cadence=raw.get("cadence") or "as directed",
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
        max_tokens=8000,
        system=GENERATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Here is the fully scored patient record:\n\n{payload}\n\n"
                                                 "Generate the interpretive copy per the instructions."}],
    )
    text_blocks = [b.text for b in resp.content if hasattr(b, "text")]
    raw_text = "".join(text_blocks).strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()
    return json.loads(raw_text)


def run(labs_pdf, dexa_pdfs, note_text, patient_name, age, sex, out_path):
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    print("Step 1/3: extracting raw material...")
    extracted = extract(client, labs_pdf, dexa_pdfs, note_text)
    extracted.setdefault("name", patient_name)
    extracted.setdefault("age", age)
    extracted.setdefault("sex", sex)

    print("Step 2/3: scoring (deterministic, no AI)...")
    record, notice = score_and_build_record(extracted)

    print("Step 3/3: generating interpretive copy...")
    copy = generate_copy(client, record)

    print("Rendering PDF...")
    template.render(record, copy, out_path)

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
