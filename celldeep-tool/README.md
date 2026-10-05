# CellDeep Patient Report Generator

Built from the locked V23 reference design, following the calibration
framework established over the full design/build conversation. Bloodwork
tables are read deterministically from the PDF text layer by row/column
position, provider notes must follow `templates/provider_notes_template.md`,
and patient-facing copy is template fill from the scored record. DEXA PDFs
are still extracted by the original Claude call (`ANTHROPIC_API_KEY` required
when a DEXA PDF is uploaded).

Each bloodwork page must print its own Current/Historical column header
(with governing dates), or In Range / Out of Range columns. Table state
never carries across pages; readable pages without a header contribute no
rows. Pages without readable text are rendered at 200 dpi and transcribed
twice in independent Anthropic vision requests using the same model, client
configuration, and API error handling as DEXA. The existing DEXA call is
unchanged. An API key is required for scan transcription; without one,
readable results continue with an explicit staff review notice.

Scan names, results, flags, columns, reference ranges, and section headings
must agree between reads. Unsupported or illegible results and inconsistent
numeric flags are excluded with a reason. A row without a printed section
identity is also excluded to preserve section-scoped matching. Across each specimen, both reads
must match the printed out-of-range summary; Collected dates must exist and
agree, and any printed patient name must match the digital header. A failed
summary, identity, or date gate rejects the entire scan batch with a staff
review notice. Never substitute a date or infer text. A row absent from one
read cannot enter the report.
Rowful pages require a readable specimen identity; their date and printed
patient identity may come from other pages of that specimen. Rowless pages
without a specimen identity contribute no rows and produce a staff note
instead of rejecting the batch. Summary blocks still require a specimen
identity for attribution, including when split across rowless pages.

Accepted scan rows use the same exact aliases and unknown-marker review
policy as digital rows. Their `source=scan` provenance, accepted raw values,
and exclusions are recorded only in staff review notes, not in patient
copy or patient marker objects. Tests mock the vision calls; the optional
`CELLDEEP_LIVE_VISION=1` test uses a synthetic image-only PDF with invented
identity, never the local patient fixture.
The scan schema uses `anyOf` branches for nullable fields, including flags
and columns. Offline tests check enum/type compatibility and the strict
object shape. The opt-in live test double-reads six invented results,
including H/L flags, a qualitative result, and an out-of-range summary.

Printed horizontal rules, including rules spanning multiple columns,
delimit results bands. Wrapped names and cells are assembled within a band;
name-only bands set the section heading instead of becoming row names.
Adjacent words with a normal inter-word gap are grouped into a cell before
column assignment; the whole cell is assigned using its center.
Matching against canonical names and existing aliases is exact after
removing numeric footnotes, printed lab-code suffixes, and extra whitespace
(case-insensitive). Other wording and punctuation are retained, so unmatched
tests remain explicit review items. Urinalysis sections cannot match serum
markers. Every separated dated cell is accounted for by a recognized row,
an unrecognized row with dated cells, or a parsing error; text statuses such
as "Not Applicable" retain their wording and never become numbers.
Exact printed-name aliases are maintained in `markers_reference.py`; they
do not create new markers or change scoring thresholds. Repeated canonical
marker/date results are collapsed only when their values agree, with all
source pages retained in the source label and both rows in the extraction
audit. Conflicting duplicates raise a `BloodworkParseError` naming the pages.

## What's in this folder

| File | Purpose |
|---|---|
| `schema.py` | The data contract between extraction and generation. Every field is `Optional` on purpose — a missing field means "not provided," never a guess. |
| `markers_reference.py` | Known biomarker library — scoring config per marker, corrected against Star's real CHL data during calibration. |
| `protocol_reference.py` | Known compound library (BPC-157, Retatrutide, Klow, etc.) with typical target systems. |
| `unknown_marker_policy.py` | What happens when extraction finds a marker not in the library — flagged for human review, never guessed. |
| `scoring.py` | Pure math. No AI. The exact corrected tier/percentage logic from data.py. |
| `extraction_prompt.py` | `EXTRACTION_OUTPUT_SCHEMA`, the marker_occurrences shape the bloodwork parser emits and reconciliation/scoring consume; the prompt and schema are also still used for the Claude DEXA extraction call. |
| `generation_prompt.py` | Deterministic template fill for every patient-facing sentence, plus `select_priority_marker()`. |
| `template.py` | The renderer. CSS is copied verbatim from V23 — the design itself is not up for variation. **This has been tested directly and confirmed working** (see below). |
| `pipeline.py` | The orchestrator that ties it all together — this is the file you actually run. |

## What's been verified vs. what hasn't

**Verified, directly, in this environment:**
- Every file imports cleanly with no syntax errors
- The entire deterministic path — schema → scoring → template rendering —
  was run end-to-end with hand-entered test data (based on Star's real
  numbers) and produced a correct PDF matching V23's visual design,
  including the real DEXA scan image and the VAT column

**Not yet verified:**
- Additional real Quest/CHL layouts beyond the local regression fixture

## Local real-file regression

`test_real_bloodwork.py` uses `real_fixtures/bloodwork_fixture_01.pdf` when present
and skips when absent. Keep this directory git-ignored; never commit
patient source files. Run `python -m pytest -q test_real_bloodwork.py`.
The regression checks results on pages 1–8, no extra rows from readable
non-table pages 9–14, and visible warnings on image-only pages 15–21 without
changing readable-page results. It also checks golden values, section
isolation, result-band accounting, duplicate provenance, and conflicts.

## First real test to run (do this first, before any new patient)

Run the full pipeline against **Star Hawkins' actual real source
material** — the same lab PDF, DEXA PDFs, and provider note used
throughout calibration — and confirm the output matches V23. This is the
one case where the correct answer is already known, so it's the right
first proof before testing on any patient whose correct output isn't
already established.

```bash
pip install -r requirements.txt
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
