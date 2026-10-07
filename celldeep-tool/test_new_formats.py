"""A lab layout the parser has not seen must never stop the report or be guessed at: what cannot be read is
excluded and listed under INCOMPLETE, unknown test names stay staff-only, and the rest of the report is
built. Synthetic, invented layouts only."""

import fitz

import pipeline
from synthetic_fixtures import deterministic_fixtures as fx
from unknown_marker_policy import without_staff_check

X = fx.LAB_X

# A recognized section (the control).
KNOWN = (fx.section_preamble("SYN-KNOWN", "04/14/2026") + fx.header_line(100)
         + fx.row(130, "TSH", "1.9", units="uIU/mL", lab_range="0.40-4.50")
         + fx.row(144, "hs-CRP", "0.4", units="mg/L", lab_range="0.0-3.0"))
# Variant 1: the same kind of table under header wording this parser does not know.
NEW_HEADER = (fx.section_preamble("SYN-VARIANT", "04/14/2026")
              + [(X["name"], 100, "Analyte"), (X["current"], 100, "Result"), (X["hist1"], 100, "Prior Result"),
                 (X["units"], 100, "Units"), (X["range"], 100, "Reference")]
              + [(X["name"], 130, "Ferritin"), (X["current"], 130, "88"), (X["units"], 130, "ng/mL")]
              + [(X["name"], 144, "Vitamin B12"), (X["current"], 144, "512"), (X["units"], 144, "pg/mL")])
# Variant 2: a test printed under a name the library does not know (a renamed marker).
RENAMED = (fx.section_preamble("SYN-RENAMED", "04/14/2026") + fx.header_line(100)
           + fx.row(130, "Thyroid Stimulating Hormone Ultra", "2.1", units="uIU/mL", lab_range="0.40-4.50"))
# Variant 3: a page that prints its table header but no result rows.
NO_ROWS = fx.section_preamble("SYN-EMPTY", "04/14/2026") + fx.header_line(100)


def _report(tmp_path, monkeypatch, pages, name):
    folder = tmp_path / name
    folder.mkdir()
    labs = fx.write_lab_pdf(folder / "labs.pdf", pages)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda _name: str(folder / "review.txt"))
    out = folder / "report.pdf"
    pipeline.run(str(labs), [], None, fx.PATIENT, 44, "male", str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return text, without_staff_check((folder / "review.txt").read_text(encoding="utf-8"))


def test_variant_layout_still_reports_with_the_right_notices(tmp_path, monkeypatch):
    text, review = _report(tmp_path, monkeypatch, [KNOWN, NEW_HEADER, RENAMED, NO_ROWS], "variant")
    assert "TSH" in text and "hs-CRP" in text  # the recognized section is reported
    lines = review.splitlines()
    assert lines[0].startswith("INCOMPLETE - pages/sections excluded: lab PDF page(s) 2 ")
    assert ("  - page 2 (whole page): result rows printed under no recognized table header (expected "
            "'Current'/'Historical' or 'In Range'/'Out of Range'), so they were not read: Ferritin, Vitamin B12"
            ) in lines
    # Nothing from the unread page is guessed into the report.
    assert "Ferritin" not in text and "512" not in text
    # The renamed test is listed for staff and never scored; with a printed value, unit and range it is shown as
    # lab-reported, exactly as printed (CHL round 1, item 13).
    assert "Thyroid Stimulating Hormone Ultra" in review
    assert "Thyroid Stimulating Hormone Ultra 0.40-4.50 — 2.1 uIU/mL" in text
    assert "lab-reported results, not scored" in text.lower()
    assert "COVERAGE GAP" not in review


def test_page_order_does_not_change_the_report(tmp_path, monkeypatch):
    first, _ = _report(tmp_path, monkeypatch, [KNOWN, NEW_HEADER, RENAMED, NO_ROWS], "order-a")
    second, _ = _report(tmp_path, monkeypatch, [NO_ROWS, RENAMED, NEW_HEADER, KNOWN], "order-b")
    assert first == second


def test_a_lab_pdf_with_nothing_readable_still_builds_the_rest(tmp_path, monkeypatch):
    text, review = _report(tmp_path, monkeypatch, [NEW_HEADER], "nothing-readable")
    assert review.startswith("INCOMPLETE - pages/sections excluded: lab PDF page(s) 1 ")
    assert "Ferritin" not in text


def test_pasted_medication_list_is_rejected_with_how_to_fix_it():
    note = pipeline.parse_provider_note("Testosterone cypionate 100 mg weekly\nBPC-157 250 mcg daily\n")
    assert note["accepted"] is False and note["protocol"] == []
    message = note["other_notes"][0]
    assert message.startswith("PROVIDER NOTE REJECTED - no structured '## ' sections found")
    assert "the rest of the report was built without it" in message
    for heading in ("'## Consultation Note'", "'## Treatment Status'", "'## Patient Concerns'", "'## Protocol'",
                    "'## Marker Targets'", "'## Vitality Index'"):
        assert heading in message
    assert pipeline.check_provider_note("Testosterone cypionate 100 mg weekly")["fix"] == pipeline.NOTE_FIX
