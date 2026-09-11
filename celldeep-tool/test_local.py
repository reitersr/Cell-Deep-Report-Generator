"""Repeatable local Star fixture harness for report rendering and pagination checks."""

import argparse
import os
import sys
from pathlib import Path

import fitz

import pipeline


def _blank_space_summary(pdf_path: Path) -> list[tuple[int, float]]:
    document = fitz.open(pdf_path)
    summary = []
    for page_number, page in enumerate(document, start=1):
        blocks = page.get_text("blocks")
        last_bottom = max((block[3] for block in blocks), default=0)
        blank_percent = max(0.0, (page.rect.height - last_bottom) / page.rect.height * 100)
        summary.append((page_number, blank_percent))
    document.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", default="fixtures/star", help="directory containing Star's local source files")
    parser.add_argument("--out", default="local-output", help="directory for generated PDF and HTML")
    args = parser.parse_args()

    fixture_dir = Path(args.fixtures)
    output_dir = Path(args.out)
    labs = fixture_dir / "labs.pdf"
    dexa = sorted(fixture_dir.glob("dexa*.pdf"))
    note = fixture_dir / "provider_note.txt"
    missing = [str(path) for path in (labs, note) if not path.exists()]
    if not dexa:
        missing.append(str(fixture_dir / "dexa*.pdf"))
    if missing:
        print("Local harness cannot run: missing fixture files:", file=sys.stderr)
        print("  " + "\n  ".join(missing), file=sys.stderr)
        print("Place Star Hawkins' real files in the fixture directory and rerun.", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    output_pdf = output_dir / "star_report.pdf"
    note_text = note.read_text(encoding="utf-8")
    pipeline.run(
        labs_pdf=str(labs),
        dexa_pdfs=[str(path) for path in dexa],
        note_text=note_text,
        patient_name="Star Hawkins",
        age=37,
        sex="female",
        out_path=str(output_pdf),
    )

    html_path = output_pdf.with_suffix(".html")
    if not html_path.exists() or not output_pdf.exists():
        print("Local harness failed: renderer did not produce both PDF and HTML.", file=sys.stderr)
        return 1

    page_summary = _blank_space_summary(output_pdf)
    print(f"PDF: {output_pdf}")
    print(f"HTML: {html_path}")
    print(f"Pages: {len(page_summary)}")
    for page_number, blank_percent in page_summary:
        print(f"Page {page_number}: {blank_percent:.1f}% blank below last rendered text block")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
