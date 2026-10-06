# CellDeep Report Generator

Turns a patient's lab PDF, DEXA PDF(s) and a structured provider note into the CellDeep
patient report (PDF) plus staff-only review notes. All code lives in `celldeep-tool/` (plus `scripts/smoke_test.py`).

## Non-negotiables

These override any other instruction, convenience or test shortcut.

1. **NEVER INFER.** No value, flag, range, date or marker is ever guessed. Unreadable or
   disagreeing data is excluded and listed in the staff notes, never filled in.
2. **No real patient data in the repository.** No real patient PDFs, images, names,
   specimen IDs, dates of birth or draw/scan dates in code, tests, logs, commits or PR text.
   Fixtures are synthetic or de-identified. `real_fixtures/` stays git-ignored and is for
   local runs only. Generated exports and archives (`*.zip`, `claude_review_export.md`) are
   not committed.
3. **Staff QA content never appears in the patient PDF.** Gate reasons, `source=scan`
   provenance, exclusions and review notices go only to the staff review notes.
4. **DEXA is read twice and gated.** `_extract_dexa_with_claude` reads every DEXA page twice via
   `scan_dexa` and keeps a measurement only when both reads agree. Any change to DEXA reading must
   keep that rule, the per-page patient-name check and the date-sorted history.
5. **Deploys stay manual and owned by the clinic.** Never push to `main` directly; every change
   goes through a pull request. A PR may be merged only as described in "Pull request workflow"
   below; anything else waits for the owner.

## Repository layout

| Path | Purpose |
|---|---|
| `celldeep-tool/app.py` | Flask upload form (`templates/index.html`) and background report jobs. |
| `celldeep-tool/pipeline.py` | Orchestrator: deterministic bloodwork table parsing, scan routing, reconciliation, scoring hand-off, review notes, CLI (`python pipeline.py --help`). |
| `celldeep-tool/lab_layouts.py` | Registry of deterministic digital lab layouts; `select()` picks one, else the Quest/CHL table parser. |
| `celldeep-tool/access_medical.py` | Access Medical Laboratories layout: header block, four-column table, summary cross-check. |
| `celldeep-tool/scan_bloodwork.py` | Image-only bloodwork pages: two independent vision reads per page and the deterministic gates that keep or exclude each row. |
| `celldeep-tool/markers_reference.py` | Marker library: canonical names, exact aliases, CellDeep scoring thresholds. |
| `celldeep-tool/lab_reported.py` | Lab-reported, not-scored tests (CBC, chemistry, urinalysis, ...): exact aliases, section-scoped; shown with printed range and the lab's H/L flag, never a CellDeep score. |
| `celldeep-tool/clinic_config.py` | Clinic-decision defaults (censored results, lab flags, range labels); one comment per setting. |
| `celldeep-tool/tmp_cleanup.py` | Deletes uploads, reports and audits from `/tmp` after six hours. |
| `celldeep-tool/scoring.py` | Pure scoring math, no AI; `is_censored` for results printed as a limit. |
| `celldeep-tool/generation_prompt.py` | Deterministic patient-facing copy (template fill, no AI). |
| `celldeep-tool/template.py` | HTML/CSS renderer (locked V23 design) and PDF output via Playwright. |
| `celldeep-tool/schema.py` | `PatientRecord` / `Marker` / `DexaReading` data contract. |
| `celldeep-tool/unknown_marker_policy.py` | Staff review notice (`ExtractionReviewNotice`) and its plain-text format. |
| `celldeep-tool/scan_dexa.py` | DEXA pages: two independent vision reads, agreement gates, date-sorted history, "(e)" estimated values. |
| `celldeep-tool/extraction_prompt.py` | The `marker_occurrences` shape (`EXTRACTION_OUTPUT_SCHEMA`); its old prompt is no longer called. |
| `celldeep-tool/protocol_reference.py`, `dexa_reference.py` | Protocol compound library; DEXA scoring helpers. |
| `celldeep-tool/templates/provider_notes_template.md` | The structured provider-note format staff must follow. |
| `celldeep-tool/synthetic_fixtures/` | Invented-data fixture builders. |
| `celldeep-tool/test_*.py` | Test suite (pytest). |
| `calibration_baselines/` | Synthetic calibration snapshots. |
| `celldeep-tool/ranges_audit.py` | Read-only audit of each marker's threshold source; `--write` regenerates `docs/ranges_audit.md` (a test keeps it in sync). |
| `celldeep-tool/synthetic_fixtures/scenarios.py` | End-to-end synthetic scenarios with a scripted vision model (smoke test, `test_scenarios.py`). |
| `scripts/smoke_test.py` | Generates reports from synthetic fixtures and prints PASS/FAIL (no API key, no network, under a minute). |
| `docs/` | Audits, open clinical decisions (`open_decisions.md`), `staff_guide.md`, `what_the_tool_guarantees.md`. |
| `.github/workflows/tests.yml` | CI: full suite offline on every pull request. |

## Running tests

```bash
cd celldeep-tool
pip install -r requirements.txt pytest
python -m playwright install chromium
ANTHROPIC_API_KEY=offline-mocked-key python -m pytest -q
```

Tests must pass with no network: every Anthropic call is mocked, and every mock checks the call against
the installed SDK (`synthetic_fixtures/sdk_contract.py`: the real `Messages.create` signature and the SDK's
parameter types), so an argument the SDK rejects fails in CI. `anthropic` is pinned exactly in
`requirements.txt`, which both CI and production install (`test_sdk_contract.py` checks the pin). CI runs the suite inside a
network namespace with no interfaces (`unshare --net`) and fails if the network is reachable.
To reproduce locally on Linux: `sudo unshare --net -- sudo -u "$USER" env "PATH=$PATH"
ANTHROPIC_API_KEY=offline-mocked-key python -m pytest -q`. Tests that need
`real_fixtures/` or `CELLDEEP_LIVE_VISION=1` skip when those are absent.

## Data flow

1. **Upload** (`app.generate`, staff login required via `CELLDEEP_STAFF_PASSWORD`; sessions signed with
   `CELLDEEP_SECRET_KEY`, else a key derived from the password, never a per-process random key;
   every protected route goes through `app.is_staff_session`): patient name,
   age, sex, lab PDF, DEXA PDF(s), provider note, Vitality Index, and the bloodwork Collected date
   (required when the lab PDF has image-only pages). Runs `pipeline.run` in a background job, one at a
   time per process; a failure shows "Generation failed. Job ID: <id>", and a job whose process died
   (missing folder or heartbeat older than `STALE_JOB_SECONDS`) shows "This report job was interrupted.
   Please try again". Scanned pages are rendered once per page via `scan_bloodwork.render_page_png_b64`
   (200 DPI, MuPDF cache emptied) so peak memory does not grow with page count (`test_memory.py`).
   After the documents are read and before anything is built, `pipeline.run(confirm=...)` pauses the job
   when anything was left out (`extracted["preflight"]`: a lab page/section, a scanned result row, a printed
   result with an unrecognized test name, a DEXA page, or a provider note not read); headings are never
   listed. The generating page lists them and staff continue or stop (`/generate/decision/<id>`,
   `GenerationAborted`).
2. **Bloodwork** (`pipeline.extract`):
   - Pages with a text layer: `lab_layouts.select` picks a registered layout (today `access_medical`: header
     block for patient, DOB, age, two-digit-year Coll. Date and Fasting; rows only under a section title in the
     Test Name | Results | Reference Range | Units pattern; the OUT OF RANGE SUMMARY is found by its heading wherever it
     is printed and ignored silently, except that a summary line matching no table row (or a different value) is a
     staff note; "Bili" is resolved by section or left out with a notice; word results
     and assays that differ from the CellDeep range basis are lab-reported; units are never filled in). Otherwise
     `_parse_bloodwork_tables` reads rows by printed column position
     under each page's own Current/Historical or In Range/Out of Range header. Names match
     the marker library by exact alias only; anything else becomes an unrecognized-marker
     review item.
   - Image-only pages: `_extract_scan_bloodwork` renders each page and reads it twice with the vision model
     (no sampling settings: anthropic 1.x accepts no temperature/top_p/top_k and has no seed); when the two
     reads disagree on any result row, a third read is made (`scan_bloodwork.SCAN_MAX_READS`). Transport
     retries only. `scan_bloodwork.gate_staff_identified_reads` keeps a row only when at least two reads print
     exactly the same value, flag and range (both, when only two reads exist) and it passes the value-format
     and flag-vs-range checks. A row no two reads agree on is listed as "INCOMPLETE - row excluded: <marker>
     (reads disagree: X / Y / Z)"; every other excluded result row is listed with its check. A row no read
     prints a value for (a section heading) is not a result and is never listed or confirmed. Staff-entered
     name and Collected date identify the pages.
   - `_merge_scan_occurrences` combines both sources.
   - A section or page that cannot be parsed is excluded whole and listed under "INCOMPLETE" at the
     top of the staff notes; the rest of the report is built. A text page with result rows under no
     recognized table header is excluded and the tests it holds are named; an unrecognized document is
     excluded page by page instead of stopping the report. `BloodworkHardStop` (conflicting
     duplicate results, one Order ID with two Collected dates) still stops the job.
3. **DEXA**: `_extract_dexa_with_claude` renders every DEXA page and `scan_dexa` reads it twice; a
   measurement is kept only when both reads agree, a scan date the reads disagree on drops the
   whole scan, and `scan_dexa.attribute_pages` decides which pages are the patient's: a page printing a
   different name is always dropped; a page printing the patient's name is validated; an unnamed page
   printing an age (header or scan rows, decimals allowed, both reads agreeing) is validated when the age
   is within `clinic_config.DEXA_AGE_TOLERANCE_YEARS` of the median age across the pages (a clear cluster)
   and of the staff-entered age; an unnamed page with no age is accepted only when every scan date it
   shows is on a validated page. Every other page is listed under "INCOMPLETE - pages excluded" with its
   dates and reason, and reaches no history row, current/first-visit value, summary, scan image or score.
   The printed body fat % is used whenever printed, including "(e)" values (labelled
   `clinic_config.DEXA_ESTIMATED_LABEL`; a value printed identically with "(e)" on only some reads/pages is kept
   as estimated); it is computed as fat / (fat + lean) only when never printed (labelled
   `clinic_config.DEXA_COMPUTED_LABEL`) and never when printed but contested ("withheld"). The same value and
   label appear in the history, "When you came in", "Where you are now" (the latest accepted scan with body
   composition, `scoring.has_body_composition`), the summary line, the headline and the score; with nothing to
   score, the "% optimized" figure is left out (never "—%") and the staff notes say why (`dexa_score_notes`).
   Staff notes warn when "Where you are now" is older than the latest bloodwork by more than
   `DEXA_STALE_DAYS`. Kept scans are merged by date and
   sorted oldest first. Outcomes open the staff notes (DEXA block). Names everywhere compare with
   `scan_bloodwork.names_match` (any order and case, or first name plus last initial).
4. **Provider note**: `parse_provider_note` reads only the documented template sections
   (including `## Treatment Status`); every other line is listed with its reason.
4b. **Vitality Index** (`pipeline.resolve_vitality`): a domain's answer comes from the upload form or a read
   provider note; "Not Assessed" (the form default) is no answer. A form/note disagreement leaves the domain
   out (staff note). With no answer the box shows `clinic_config.VITALITY_NOT_PROVIDED_LABEL`, never "No
   Concern", and the symptom score is left out of the overall score. The STAFF CHECK names each answer's source.
4a. **Identity**: age comes from the printed date of birth and collection date when printed
   (`_age_from_dob`; DOB never stored, disagreeing DOBs give no age); `name_header` lists the name
   each source printed at the top of the staff notes.
5. **Scoring** (`score_and_build_record`): reconciles occurrences into `Marker`s, scores with
   `markers_reference.py` thresholds, collects staff notices. Unrecognized rows that match
   `lab_reported.py`, and any other row the lab flagged H/L, become `record.lab_reported`
   (shown, never scored). `coverage_gaps` checks that every printed row reached the report or
   the staff notes and raises a `COVERAGE GAP` note otherwise.
6. **Copy and render**: `generation_prompt.build_copy` fills patient-facing text;
   `template.render` writes the patient PDF. Full-panel column headers name a date only when every
   value in the column is from it (`template.panel_column_headers`). The lab's own flag is shown under a
   current result only where CellDeep calls it Optimal (`clinic_config.LAB_FLAG_DISAGREES_WITH`; the clinic
   deliberately does not show it on Moderate or unscored results); a censored value shows it next to the value.
   The summary lists the systems whose current results come from a draw before the headline date
   (`generation_prompt.earlier_draw_systems`).
7. **Staff notes**: `_write_review_notes` / `format_review_notice` write the staff-only review
   file, downloadable separately from the report. It opens with the one-screen STAFF CHECK
   (`pipeline.staff_check_block`: entered name and date, DEXA dates accepted/excluded with ages and body fat
   labels, the current DEXA scan vs the latest draw, lab pages accepted/excluded, provider note status, counts,
   name mismatches), then INCOMPLETE; tests read past it with
   `unknown_marker_policy.without_staff_check`.

## Pull request workflow

Standing instruction from the owner for every PR Claude opens:

1. **Wait for the `tests` check** on the PR's latest commit. Never merge while any check is
   failing, pending or missing, and never with a merge conflict.
2. **Decide whether the PR is clinical.** It is clinical if it changes any of:
   - thresholds or scoring rules: `markers_reference.py` (thresholds, sex variants, aliases that
     route a printed test to a scored marker), `scoring.py`, `dexa_reference.py`, the
     threshold/scoring logic in `pipeline.score_and_build_record` or `reconcile_marker_occurrences`,
     or `docs/ranges_audit.md`;
   - patient-facing wording or templates: `generation_prompt.py`, `template.py` (HTML, CSS, copy),
     `lab_reported.py` (which tests patients see and how), `protocol_reference.py`,
     `markers_reference.py` descriptions/taglines;
   - any golden file under `synthetic_fixtures/golden/` where a scored value, tier, range,
     lab-reported result or date changed.
   If unsure, treat it as clinical.
3. **Not clinical and `tests` passed:** squash-merge it yourself, then report what merged
   (PR link, squash commit, one-line summary of each change).
4. **Clinical:** leave it open, do not merge, and tell the owner which files/changes make it
   clinical and what decision is needed.
5. **After any merge,** list the exact things the owner should check in the next generated
   report: which sections, markers, values, flags, ranges, dates or staff-note entries should
   look different (or must look unchanged), and where to find them (patient PDF vs staff QA
   file).
6. **Always:** never include PDFs, images or patient identifiers in commits, PR text or
   comments; never push to `main` directly; never merge with failing checks.

## Working rules

- Every production failure becomes a synthetic fixture and a failing test before it is fixed.
- Keep exclusions explicit: when data cannot be trusted, drop it and say why in staff notes.
- Log lines are value-free and name-free. File paths that reach logs never contain the patient's
  name; the staff notice is written to the QA file, never printed.
- Clinic decisions belong in `clinic_config.py`, not in code.
