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
identity is also excluded to preserve section-scoped matching. Gates are page-
or specimen-local: malformed transcriptions, unreadable metadata, and missing
specimen dates/identity exclude only the affected pages. Contradictory patient
names, a name mismatching the digital header, or conflicting Collected dates
for the same specimen reject the entire scan batch. A missing digital patient
header excludes scans with an explicit notice. Never substitute a date or
infer text. A row absent from one read cannot enter the report.
The out-of-range cross-check uses only kept pages of each specimen and excludes
only disagreeing rows, not the batch; summary entries for excluded pages do not
invalidate other rows.
Rowful pages require a readable specimen identity; a missing id may be inherited
only from an adjacent PDF page with the literal same specimen id in its footer
and consecutive `PAGE n OF m` footers agreeing in both reads. Conflicting
neighbour ids do not permit inheritance. Dates and printed patient identity
may come from other kept pages of that specimen. Rowless pages
without a specimen identity contribute no rows and produce a staff note
instead of rejecting the batch. Summary blocks still require a specimen
identity for attribution, including when split across rowless pages.

Each scanned page emits one value-free diagnostic line (including rejected
batches); counts are `null` for reads that could not be validated:

```text
source=scan page=<PDF page> rows_read=<read 1 count>/<read 2 count> specimen_id=<JSON string or null> name_matched=<yes|no> date=<JSON string or null> verdict=<kept|excluded> reason=<JSON string>
source=scan page <PDF page> excluded <marker repr>: <reason>
```

The second format is emitted for every excluded row. Neither format includes
patient names or result values; `name_matched` reports the page's own printed
name match, while specimen/date can reflect verified inheritance.

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

## Reproducibility and the failure-to-test workflow

`synthetic_fixtures/layouts.py` builds one invented-data lab PDF per layout seen in
production: Quest-style digital (In Range / Out of Range, printed H/L flags, Lab column,
urinalysis band), Cleveland HeartLab-style digital (Current + dated Historical columns),
fully scanned (image-only pages with mocked double reads) and mixed digital + scanned.
PDFs are generated at test time and never committed.

`test_reproducibility.py` checks, for every fixture:

- **Golden file** (`synthetic_fixtures/golden/<fixture>.json`): every scored value, tier,
  CellDeep and lab range, draw date, lab-reported result and flag, staff-only row and staff
  note. Any change fails the test.
- **Idempotence**: the same inputs render the same report HTML and PDF text twice, and in
  two interpreters with different `PYTHONHASHSEED` values.

When a change to the output is intended, regenerate and review the JSON diff before
committing:

```bash
CELLDEEP_UPDATE_GOLDEN=1 ANTHROPIC_API_KEY=offline-mocked-key python -m pytest -q test_reproducibility.py
git diff synthetic_fixtures/golden/
```

### Every production failure becomes a fixture and a test before it is fixed

1. **Capture without patient data.** From the staff QA file and the value-free
   `source=scan ...` log lines, write down the layout feature that failed (column
   arrangement, print convention, flag/range notation, section band), never the patient's
   name, IDs, dates or real values.
2. **Rebuild it synthetically.** Add or extend a builder in `synthetic_fixtures/layouts.py`
   (or a mocked scan read) with invented values that reproduce the same layout feature.
3. **Write the failing test first.** Add a test that fails on the current code for the same
   reason production failed, and run it to see it fail.
4. **Fix generally,** in the parser, gate or library, not for one patient or one value.
5. **Prove it.** The new test passes, the full suite passes offline
   (`ANTHROPIC_API_KEY=offline-mocked-key`), golden diffs are reviewed and regenerated only
   if intended, and the PR names the failure and the test that now guards it.

`test_scan_april_regressions.py` (four scanned results dropped) and the all-caps
urinalysis band test in `test_lab_reported.py` follow this workflow.

## What's in this folder

| File | Purpose |
|---|---|
| `schema.py` | The data contract between extraction and generation. Every field is `Optional` on purpose — a missing field means "not provided," never a guess. |
| `markers_reference.py` | Known biomarker library — scoring config per marker, corrected against the calibration reference patient's CHL panel. |
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
  was run end-to-end with hand-entered test data (based on the calibration
  reference patient) and produced a correct PDF matching V23's visual design,
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

## Running against the calibration reference patient (local only)

Run the full pipeline against the calibration reference patient's source
material (lab PDF, DEXA PDFs, provider note), kept only in the git-ignored
`real_fixtures/` directory, and confirm the output matches V23. This is the
one case where the correct answer is already known. Never commit these files
or put the patient's name in commands that are saved to the repository.

```bash
pip install -r requirements.txt
playwright install chromium

export ANTHROPIC_API_KEY="your-key-here"

python pipeline.py \
  --labs real_fixtures/reference/labs.pdf \
  --dexa real_fixtures/reference/dexa_1.pdf real_fixtures/reference/dexa_2.pdf \
  --note real_fixtures/reference/provider_note.txt \
  --patient-name "<patient name>" --age <age> --sex <male|female> \
  --out real_fixtures/reference/report.pdf
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
