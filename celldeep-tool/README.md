# CellDeep Patient Report Generator

Built from the locked V23 reference design, following the calibration
framework established over the full design/build conversation. This is a
complete, real implementation — not pseudocode — but it has **not yet been
run against the live Anthropic API**, because the environment this was
built in has no outbound internet access. Everything that doesn't require
the API has been tested directly (see "What's been verified" below).

## What's in this folder

| File | Purpose |
|---|---|
| `schema.py` | The data contract between extraction and generation. Every field is `Optional` on purpose — a missing field means "not provided," never a guess. |
| `markers_reference.py` | Known biomarker library — scoring config per marker, corrected against Star's real CHL data during calibration. |
| `protocol_reference.py` | Known compound library (BPC-157, Retatrutide, Klow, etc.) with typical target systems. |
| `unknown_marker_policy.py` | What happens when extraction finds a marker not in the library — flagged for human review, never guessed. |
| `scoring.py` | Pure math. No AI. The exact corrected tier/percentage logic from data.py. |
| `extraction_prompt.py` | The instruction sent to Claude to turn raw pasted PDFs/notes into structured data. |
| `generation_prompt.py` | The instruction sent to Claude to write the interpretive copy in V23's locked voice. |
| `template.py` | The renderer. CSS is copied verbatim from V23 — the design itself is not up for variation. **This has been tested directly and confirmed working** (see below). |
| `pipeline.py` | The orchestrator that ties it all together — this is the file you actually run. |

## What's been verified vs. what hasn't

**Verified, directly, in this environment:**
- Every file imports cleanly with no syntax errors
- The entire deterministic path — schema → scoring → template rendering —
  was run end-to-end with hand-entered test data (based on Star's real
  numbers) and produced a correct PDF matching V23's visual design,
  including the real DEXA scan image and the VAT column

**Not yet verified — requires an environment with internet access:**
- The actual API calls in `extract()` and `generate_copy()` (in `pipeline.py`)
  have never been executed
- The full pipeline has never been run against a real lab PDF or provider
  note

## First real test to run (do this first, before any new patient)

Run the full pipeline against **Star Hawkins' actual real source
material** — the same lab PDF, DEXA PDFs, and provider note used
throughout calibration — and confirm the output matches V23. This is the
one case where the correct answer is already known, so it's the right
first proof before testing on any patient whose correct output isn't
already established.

```bash
pip install anthropic playwright
playwright install chromium

export ANTHROPIC_API_KEY="your-key-here"

python pipeline.py \
  --labs star_labs.pdf \
  --dexa star_dexa_1.pdf star_dexa_2.pdf \
  --note star_provider_note.txt \
  --patient-name "Star Hawkins" --age 37 --sex female \
  --out star_report.pdf
```

## A known open item, found during testing

The test render showed that a patient-facing category with **zero
markers** (nothing in that system was tested) currently falls back to a
default 50% score rather than being handled explicitly. Worth deciding
before running this against a genuinely sparse patient panel: should a
system with no markers at all be omitted from the grid entirely, or shown
some other way? This wasn't specified during calibration and should be
before it matters for a real patient.

## Also still needed, not built here

- The DEXA scan image extraction (cropping the real image out of a raw
  DEXA PDF) was done by hand during calibration using fixed pixel
  coordinates for one specific report template. Confirmed the DEXA
  provider is consistent, so this can be built as a deterministic crop —
  just not yet implemented as code.
- The actual employee-facing website/input page. This tool is the engine;
  the website is a separate build.
