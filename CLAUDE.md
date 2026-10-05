# CellDeep Report Generator

Turns a patient's lab PDF, DEXA PDF(s) and a structured provider note into the CellDeep
patient report (PDF) plus staff-only review notes. All code lives in `celldeep-tool/`.

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
4. **Do not change `_extract_dexa_with_claude`** (in `pipeline.py`) or the prompt and schema it
   sends (`extraction_prompt.py`). DEXA changes are proposals only (`docs/open_decisions.md`).
5. **Deploys stay manual and owned by the clinic.** Nothing pushes to `main` without a pull
   request the owner merges. Never merge your own PR.

## Repository layout

| Path | Purpose |
|---|---|
| `celldeep-tool/app.py` | Flask upload form (`templates/index.html`) and background report jobs. |
| `celldeep-tool/pipeline.py` | Orchestrator: deterministic bloodwork table parsing, scan routing, reconciliation, scoring hand-off, review notes, CLI (`python pipeline.py --help`). |
| `celldeep-tool/scan_bloodwork.py` | Image-only bloodwork pages: two independent vision reads per page and the deterministic gates that keep or exclude each row. |
| `celldeep-tool/markers_reference.py` | Marker library: canonical names, exact aliases, CellDeep scoring thresholds. |
| `celldeep-tool/lab_reported.py` | Lab-reported, not-scored tests (CBC, chemistry, urinalysis, ...): exact aliases, section-scoped; shown with printed range and the lab's H/L flag, never a CellDeep score. |
| `celldeep-tool/scoring.py` | Pure scoring math, no AI. |
| `celldeep-tool/generation_prompt.py` | Deterministic patient-facing copy (template fill, no AI). |
| `celldeep-tool/template.py` | HTML/CSS renderer (locked V23 design) and PDF output via Playwright. |
| `celldeep-tool/schema.py` | `PatientRecord` / `Marker` / `DexaReading` data contract. |
| `celldeep-tool/unknown_marker_policy.py` | Staff review notice (`ExtractionReviewNotice`) and its plain-text format. |
| `celldeep-tool/extraction_prompt.py` | DEXA Claude prompt/schema and the `marker_occurrences` shape. Do not change. |
| `celldeep-tool/protocol_reference.py`, `dexa_reference.py` | Protocol compound library; DEXA scoring helpers. |
| `celldeep-tool/templates/provider_notes_template.md` | The structured provider-note format staff must follow. |
| `celldeep-tool/synthetic_fixtures/` | Invented-data fixture builders. |
| `celldeep-tool/test_*.py` | Test suite (pytest). |
| `calibration_baselines/` | Synthetic calibration snapshots. |
| `docs/` | Audits and open clinical decisions. |
| `.github/workflows/tests.yml` | CI: full suite offline on every pull request. |

## Running tests

```bash
cd celldeep-tool
pip install -r requirements.txt pytest
python -m playwright install chromium
ANTHROPIC_API_KEY=offline-mocked-key python -m pytest -q
```

Tests must pass with no network: every Anthropic call is mocked. CI runs the suite inside a
network namespace with no interfaces (`unshare --net`) and fails if the network is reachable.
To reproduce locally on Linux: `sudo unshare --net -- sudo -u "$USER" env "PATH=$PATH"
ANTHROPIC_API_KEY=offline-mocked-key python -m pytest -q`. Tests that need
`real_fixtures/` or `CELLDEEP_LIVE_VISION=1` skip when those are absent.

## Data flow

1. **Upload** (`app.generate`): patient name, age, sex, lab PDF, DEXA PDF(s), provider note,
   Vitality Index, and the bloodwork Collected date (required when the lab PDF has image-only
   pages). Runs `pipeline.run` in a background job.
2. **Bloodwork** (`pipeline.extract`):
   - Pages with a text layer: `_parse_bloodwork_tables` reads rows by printed column position
     under each page's own Current/Historical or In Range/Out of Range header. Names match
     the marker library by exact alias only; anything else becomes an unrecognized-marker
     review item.
   - Image-only pages: `_extract_scan_bloodwork` renders each page, reads it twice with the
     vision model, and `scan_bloodwork.gate_staff_identified_reads` keeps a row only when both
     reads agree and it passes the value-format and flag-vs-range checks. Staff-entered name
     and Collected date identify the pages.
   - `_merge_scan_occurrences` combines both sources; conflicting results for the same marker
     and date raise `BloodworkParseError`.
3. **DEXA**: `_extract_dexa_with_claude` (unchanged Claude call).
4. **Provider note**: `parse_provider_note` reads only the documented template sections.
5. **Scoring** (`score_and_build_record`): reconciles occurrences into `Marker`s, scores with
   `markers_reference.py` thresholds, collects staff notices. Unrecognized rows that match
   `lab_reported.py`, and any other row the lab flagged H/L, become `record.lab_reported`
   (shown, never scored). `coverage_gaps` checks that every printed row reached the report or
   the staff notes and raises a `COVERAGE GAP` note otherwise.
6. **Copy and render**: `generation_prompt.build_copy` fills patient-facing text;
   `template.render` writes the patient PDF.
7. **Staff notes**: `_write_review_notes` / `format_review_notice` write the staff-only review
   file, downloadable separately from the report.

## Working rules

- Every production failure becomes a synthetic fixture and a failing test before it is fixed.
- Keep exclusions explicit: when data cannot be trusted, drop it and say why in staff notes.
- Log lines are value-free and name-free.
