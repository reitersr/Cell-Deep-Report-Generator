# CellDeep Patient Report Generator

Turns a lab PDF, DEXA PDF(s) and a structured provider note into the CellDeep patient report
(PDF) plus a staff-only QA file. Built from the locked V23 reference design. Bloodwork tables
are read deterministically from the PDF text layer by row/column position; scanned pages are
read twice by a vision model and kept only where both reads agree; patient-facing copy is
template fill from the scored record. DEXA PDFs are still extracted by the original Claude call
(`ANTHROPIC_API_KEY` required when a DEXA PDF or a scanned lab page is uploaded).

The non-negotiables (never infer; no real patient data in the repository; no staff QA text in
the patient PDF; `_extract_dexa_with_claude` unchanged; manual, PR-only deploys) and the data
flow are in [`../CLAUDE.md`](../CLAUDE.md). Open clinical decisions are in
[`../docs/open_decisions.md`](../docs/open_decisions.md); the threshold source of every marker
is in [`../docs/ranges_audit.md`](../docs/ranges_audit.md).

## Running the tests

```bash
pip install -r requirements.txt pytest
python -m playwright install chromium
ANTHROPIC_API_KEY=offline-mocked-key python -m pytest -q
```

Every Anthropic call is mocked; the suite needs no network. CI
(`.github/workflows/tests.yml`) runs it on every pull request inside a network namespace with no
interfaces and fails if the network is reachable. Tests needing `real_fixtures/` (git-ignored,
local only) or `CELLDEEP_LIVE_VISION=1` skip otherwise.

## Upload

Staff enter the patient name, age, sex, the lab PDF, DEXA PDF(s), the provider note, the Vitality
Index and the bloodwork **Collected date**. The Collected date is required when the lab PDF has
image-only pages; the upload is rejected on screen before any job starts without it.

## Bloodwork

Each readable bloodwork page must print its own Current/Historical column header (with governing
dates), or In Range / Out of Range columns. Table state never carries across pages; readable
pages without a header contribute no rows.

### Scanned (image-only) pages

Pages without readable text are rendered at 200 dpi and transcribed twice in independent
Anthropic vision requests using the same model, client configuration and API error handling as
DEXA. Without an API key, readable results continue with an explicit staff notice.

The staff-entered patient name and Collected date identify every scanned page, so printed
footer, header, specimen, page-number, name and date checks do not apply. A scanned row is kept
only when:

- both reads contain it once, with the same name (after removing a printed Lab-column code
  such as `AMD`, which the scan schema transcribes separately as `lab_code`) and section;
- both reads agree on the value, the H/L flag and the reference range (the same printed range
  spelled differently, e.g. `> OR = 5.4` vs `>=5.4`, counts as agreement; different numbers,
  units or wording do not);
- the value is a readable result (number, inequality, `1+`-`4+` or a known status word), the
  flag is not glued into the value, and the flag is consistent with a numeric reference range
  (including `<OR=` notation and ranges with units);
- the printed out-of-range list does not contradict it. The list only ever excludes a row it
  contradicts (a value printed with its flag, e.g. `210.0 H`, is split before comparing).

A printed patient name that differs from the staff entry is a prominent staff notice, never a
rejection. Pages with no rows produce a notice only. Without a staff Collected date (for example
from the command line), the stricter printed-identity gates in `scan_bloodwork.gate_reads` apply.

Each scanned page emits one value-free, name-free log line, and each excluded row one more:

```text
source=scan page=<PDF page> rows_read=<read 1>/<read 2> identity="staff" date=<Collected> rows_kept=<n> verdict=<kept|excluded|notice> reason=<JSON string>
source=scan page <PDF page> excluded <marker repr>: <reason>
```

Tests mock the vision calls; the optional `CELLDEEP_LIVE_VISION=1` test uses a synthetic
image-only PDF with invented identity.

### Every printed result is accounted for

Each printed result row ends in exactly one of these places, and `pipeline.coverage_gaps` puts a
`COVERAGE GAP` alarm at the top of the staff notes if a row reaches none of them:

- **Scored marker** (`markers_reference.py`): CellDeep tier against CellDeep thresholds, or against
  the printed lab range where no CellDeep threshold exists (see `docs/ranges_audit.md`).
- **Lab-reported, not scored** (`lab_reported.py`): CBC with differential, chemistry, iron
  studies, lipid ratios and sub-fractions, OmegaCheck components, Homocysteine, standard CRP,
  SARS-CoV-2 and urinalysis rows. Shown in the patient report with the printed value, the lab's
  reference range and the lab's own H/L flag, never a CellDeep score. hs-CRP and standard CRP stay
  separate tests; urinalysis rows never map to serum markers.
- **Any other result the lab flagged H or L** is also shown in the lab-reported section under its
  printed name, and stays in the staff list so the library can be extended.
- **Staff review only**: unflagged results in neither library, listed with their printed value
  and range.

## Staff QA file

Written next to the report and downloadable separately; never rendered into the patient PDF.
It opens with a **SCANNED BLOODWORK** block (pages, how they were identified, results kept per
page, every excluded row with page and reason, page outcomes, name-mismatch notices), followed
by coverage gaps, unrecognized markers and other review items. `template.render` refuses to
write a patient PDF whose HTML contains any staff-note marker
(`unknown_marker_policy.STAFF_NOTE_MARKERS`).

A DEXA PDF without a text layer is reported as "DEXA SOURCE IS SCANNED" (values must be compared
with the image) instead of a verbatim-text mismatch.

## Provider notes

Notes follow `templates/provider_notes_template.md` (downloadable from the upload page). Every
line that follows its `## ` section's format is read; every other line is listed with its line
number, text and reason in the staff QA file and by the upload page's **Check note format**
button. A note with no `## ` sections is not read at all. Free text is never mined for protocol,
concerns, targets or treatment status; TRT/BHRT status comes only from accepted note content.

## Digital table parsing

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
| `scan_bloodwork.py` | Scanned-page double reads and the gates that keep or exclude each row. |
| `lab_reported.py` | Lab-reported, not-scored tests; exact section-scoped aliases. |
| `ranges_audit.py` | Read-only threshold-source audit; `--write` regenerates `docs/ranges_audit.md`. |
| `synthetic_fixtures/` | Invented-data fixture builders (`layouts.py`) and golden files. |
| `app.py` | Flask upload form, background jobs, note check and template download. |
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

(Tracked with a proposal as item 11 in `docs/open_decisions.md`.)

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
