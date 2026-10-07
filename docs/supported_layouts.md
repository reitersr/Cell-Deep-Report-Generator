# Supported lab layouts

Which lab PDFs the tool reads today, how it recognizes each one, what it leaves out on purpose, and how to add a new
layout. Every reader below is deterministic. It works from the PDF's own text and word positions, with no AI. The one
exception is the unknown-layout fallback at the end. A page none of them can read is never guessed: it is excluded
and listed in the staff notes, and it appears on the confirmation screen before generation.

## Layout matrix

| Layout | How it is detected | Reader | Synthetic fixture (CI) | Verified real report (locked with `--record-real`?) |
|---|---|---|---|---|
| **Access Medical Laboratories** (the clinic's limited-male panel) | `access_medical.detect`: on one page, the four-column header line `Test Name \| Results \| Reference Range \| Units` plus the header block labels `Acc#`, `Coll. Date:` and `Report Status:` | `access_medical.py`, registered in `lab_layouts.LAYOUTS` | `access_medical`, `access_medical_limited` | Access-layout limited male (report 92): verified, not yet locked |
| **Quest-style** (In Range / Out of Range) | No registered layout matches, so `lab_layouts.select` returns `None` and the default table reader runs. Each section starts at a `Collected:` / `Order ID` line. Each table needs a `Test Name` (or `Test` / `Tests` / `Analyte`) line that also prints `In Range` / `Out of Range` or `Current` / `Historical` (`pipeline._bloodwork_header`). | `pipeline._parse_bloodwork_tables` | `quest_digital`, `labcorp_digital` (Labcorp-style Flag column), `mixed` | Quest-layout patient (reports 89/90): verified, not yet locked |
| **Cleveland HeartLab with Quest chemistry** (multi-draw) | The same default reader. The risk-category tables print `Current` / `Historical` group labels above the `Test Name` line. The Quest chemistry pages in the same file print the panel header (`Current Result`, `Reference Range`, `Units`, `Lab`, `Historical Results`, `In Range` / `Out of Range`). | `pipeline._parse_bloodwork_tables` | `chl_digital`, `chl_extensive` | Cleveland HeartLab extensive male: verified and locked |
| **Scanned (image-only) pages**, any lab | The page has no text layer | `scan_bloodwork.py`: 2 vision reads, a 3rd when they disagree; a row is kept only when 2 reads agree exactly. Staff must enter the Collected date. | `scanned`, `scanned_noisy`, `mixed` | Quest-layout patient (scanned draw): verified, not yet locked |
| **Undated scanned draw + dated Cleveland HeartLab report** (female patient type) | Scanned pages that print no Collected date, uploaded with a digital report whose own Collected date staff entered | Dated only by an exact Historical-column match (below) | `female_chl_scanned_undated` | Female Cleveland HeartLab: dry run done, waiting for the owner's hand verification, then `--record-real` |

## The column-role model (default reader)

Every table's columns get a role from the printed header, by x-position only (`_bloodwork_header` and
`_refine_header`).

**Where the roles come from:**
- the `Test Name` line;
- every header line printed under it: risk-tier labels, `Units`, `Lab`, `Flag`, Current sub-labels such as `Optimal`
  / `Non-Optimal` or `In Range` / `Out of Range`, Historical dates, and empty `/ /` slots.

**The roles:**
- test name;
- current result, including its sub-columns;
- historical result: one column per printed date; a `/ /` slot holds nothing;
- reference range;
- risk thresholds (Optimal / Moderate / High);
- units, lab code, flag, comments.

**What a column can hold:**
- Only current and historical columns hold results.
- A current result takes its section's own `Collected:` date.
- A historical result takes the date printed over its column.
- A word is placed in a column only by its x-position under that column's header.

**How cells are read:**
- Historical cells can carry an attached flag (`60L`, `429.6H`), a censored value (`<0.3`), a count range (`0-5`) or
  words (`None seen`).
- Each draw's own full report (its Current column) wins over a later report's Historical copy. A different value
  gives a `HISTORICAL VALUE DIFFERS` staff note.
- A cell that cannot be read (for example, digits run together) is left out alone, with a notice. The rest of the
  page is kept.
- Repeated column-header lines inside a table (`Optimal Moderate High ... / / / /`) are ignored silently.

## Left out on purpose

| What | Why | Where staff see it |
|---|---|---|
| **Risk-threshold columns** (`<=123`, `470-539`, `>539`) | Thresholds are never results. For a lab-reported row they are shown as the lab's printed risk categories (its range). | Not listed (not results) |
| **Trend / progress summary pages** (a progress-summary title, or dates printed on the `Test Name` line itself) | They restate results already printed in each draw's own report, so those reports are read instead. | `TREND PAGES NOT READ` staff note |
| **Genetic results** (ApoE genotype, MTHFR) | Word or genotype results with no scoring rule and no clinic decision yet (`docs/open_decisions.md`). | `Unrecognized marker ... not included in this report` staff note |
| **Access OUT OF RANGE SUMMARY block** | It repeats rows printed in their own sections. It is ignored silently unless a summary line matches no table row (or shows a different value). | Staff note only on a mismatch |
| **A table whose header date sits under no Historical column** | The columns cannot be assigned roles. Only that table is left out. | `INCOMPLETE` notice naming the tests |
| **Unknown layouts** | No reader recognizes them. Each page is excluded instead of stopping the report, unless the AI fallback below reads and verifies it. | `INCOMPLETE` notice and confirmation screen |

## Undated scanned draws

Some files start with screenshots of an earlier report: image-only pages that print no name, no Collected date, no
fasting status and no collection time (only an order ID and "Page n of m"). A date is never guessed.

- **When the rule applies:** the scanned pages print no Collected date, and the staff-entered Collected date is the
  digital report's own date.
- **How the date is found:** the scanned rows are read as usual (2 of 3 reads), then compared with the dated report's
  Historical column (`pipeline.date_undated_scan_draw`). A date is assigned only when:
  - at least `clinic_config.UNDATED_SCAN_MIN_MATCHES` (10) plain numeric results match exactly;
  - all of those matches fall under one single historical date;
  - no result conflicts at that date.
  Staff notes: "UNDATED SCANNED DRAW: ... date <date> assigned from historical-column match (N markers)".
- **When the match is not met:** no report is built. The stop message names the pages and asks staff to enter the
  scanned pages' own date in the upload form's "Scanned Pages Collected Date" field.
- **The dated draw:**
  - Its fasting status is unknown, so the non-fasting rule applies to glucose and insulin.
  - Its collection time reads "time not recorded".
  - The scanned name line reads "no name printed - confirm".
- **Values printed twice:** a test on both the scanned page and the later report's Historical column shows one value.
  If the two differ, the scanned page's value is kept and a "HISTORICAL VALUE DIFFERS" note is added.
- **Scanned-only tests** (for example bioavailable testosterone, the testosterone panel's albumin) are read like any
  other row.

## DEXA pairing with the bloodwork

The current DEXA scan ("Where you are now") is the scan with body composition nearest the latest bloodwork Collected
date, within `clinic_config.DEXA_PAIRING_WINDOW_DAYS` (60) either side (`pipeline.pair_dexa_with_bloodwork`).

- **Earlier scans** are history.
- **Scans dated after the paired scan** (for example 60+ days after the bloodwork) are left out of the report and
  never blended in. A "DEXA PAIRING" line in the STAFF CHECK names them; there is no stop screen.
- **No scan within 60 days:** the latest scan is used, as before, and the existing staleness warning applies.

## Female reports

These rules apply when the form sex, or the sex printed on the lab report ("Gender: Female"), is female. Thresholds
are never invented.

- **No CellDeep female threshold** (cortisol, DHEA-S, SHBG, total / free / bioavailable testosterone, ...): the
  result is shown lab-reported with the lab's range and flag and is never scored. Staff notes say
  "FEMALE RANGE NOT CONFIRMED".
- **Cycle-phase hormones** (Estradiol, FSH, LH, Progesterone; `clinic_config.CYCLE_PHASE_HORMONES`): their printed
  ranges depend on a cycle phase that is not recorded, and a phase is never chosen. They are shown lab-reported and
  unscored, with no range and no flag, and the note "reference range depends on cycle phase (not recorded)". The
  lab's printed phase ranges go to the staff notes ("CYCLE PHASE NOT RECORDED"). With explicit postmenopausal BHRT
  in the provider note, the CellDeep BHRT targets apply as before.
- **Cortisol** (lab-reported for women): it is named "Cortisol, Total", and each result notes its own collection time
  and whether that falls inside the lab's printed AM window.
- **`clinic_config.FEMALE_RANGES_CONFIRMED`** (default False). While False:
  - the STAFF CHECK opens with "FEMALE RANGES NOT CLINIC-CONFIRMED - STAFF REVIEW ONLY, DO NOT RELEASE";
  - every page of the patient PDF carries "DRAFT - staff review required before release".
  The stop screen and its button are unchanged.

## Unknown-layout AI fallback: status

**Current status: built, merged into `main`, and enabled by default.** In production it is on unless the server sets
`CELLDEEP_ALLOW_VISION_FALLBACK=0` (or `false`, `no`, `off`). This repository cannot see the server's environment
settings, so whoever runs the deploy confirms there that the variable is unset (on) or set (off).

- **Built and merged.** Merged in [PR #21](https://github.com/reitersr/Cell-Deep-Report-Generator/pull/21), merge
  commit `f5dc02a`. It has been in every deploy since.
- **Enabled by default.** In code, `pipeline.vision_fallback_enabled()` returns true when the variable is unset.
- **What it does.** A text page the table reader left out whole (layout not recognized, or a header it could not read)
  is read by the vision model, with 2 of 3 reads required to agree. It is dated only by the `Collected` date printed
  on that page. Every value is then checked against the page's own text layer: it is kept only when the test name and
  the exact printed value and flag are on one line of that page. Anything else is excluded with a notice. The model
  only transcribes rows; scoring, ranges, units and dates stay deterministic. Staff notes mark each such value
  `READ BY AI, VERIFIED AGAINST THE PAGE` and give an `AI FALLBACK SUMMARY` count.
- **Kill switch.** The environment variable `CELLDEEP_ALLOW_VISION_FALLBACK` on the server. Set it to `0` (or
  `false`, `no`, `off`) in the hosting service's environment settings and restart to turn the fallback off. The code
  is `pipeline.VISION_FALLBACK_ENV` / `pipeline.vision_fallback_enabled()`. The test suite turns the fallback off
  (`celldeep-tool/conftest.py`) except in tests that script the model (`test_vision_fallback.py`).
- **Not a substitute for a registered layout.** A lab the clinic uses regularly should get its own deterministic
  reader (below).

## Standing rule: verified, then locked

A new patient type or lab layout counts as **supported** only when both of these are done:
1. A generated report has been hand-verified against its source PDFs.
2. That report has been locked with `scripts/regression_check.py --record-real`.

Until then it may be used for staff review only. "Patient type" means a combination the tool has not produced a
verified report for before, such as a new lab, a new DEXA format, a new number of draws in one file, or a new sex.

The locked expected values live in the git-ignored `celldeep-tool/real_fixtures/`, alongside the PDFs. They exist
only on the machine that recorded them. Keep that folder (for example the clinic's secure copy) so later checks can
run, and never commit it. A synthetic fixture with the same structure (step 5 below) is what CI checks on every pull
request.

### Checklist: verify and lock a new patient type or layout

1. **Source files.** Copy the lab PDF and DEXA PDF(s) into `celldeep-tool/real_fixtures/`. Run `git status` and
   confirm nothing there is tracked.
2. **Generate.** Use the same staff entries as the real run: name, age, sex, Collected date, and the exact Vitality
   Index answers.
3. **Verify by hand.** Check against the source PDFs, page by page:
   - every result in the report: value, flag, range, unit and draw date;
   - every DEXA scan: total, fat, lean, body fat % and its label (printed / estimated / computed), VAT;
   - nothing printed is missing without a staff note;
   - the staff QA file's STAFF CHECK and INCOMPLETE entries are the expected ones;
   - the per-system scores and the overall score.
   Record the report number you verified.
4. **Spec.** Create `celldeep-tool/real_fixtures/<name>.expected.json` with:
   - `labs` and `dexa` (file names in `real_fixtures/`), `patient`, `age`, `sex`, `collected_date`;
   - `vitality_index`: the answers used in the verified report, so changes to the upload form never count as
     regressions;
   - `dexa_reads` (optional): a stored transcription, so the check runs offline;
   - `"rows": []`.
5. **Lock.** Run `python scripts/regression_check.py --record-real real:<name>`, then
   `python scripts/regression_check.py real:<name>`, which must print `OK`.
6. **Synthetic stand-in.** Add or extend a synthetic fixture that reproduces the new structure with invented data, so
   CI checks it on every pull request (step 5 of "Adding a new layout").
7. **Before every later change.** Run `python scripts/regression_check.py` against all locked reports. Any difference
   must be explained as intended before it is re-recorded. Never loosen an expectation to make a diff go away.

## Adding a new layout

1. **Get a de-identified sample.** Keep the real PDF only in the git-ignored `celldeep-tool/real_fixtures/`. Never
   commit it.
2. **Decide: registered layout or the default reader.**
   - If the lab prints a `Test Name` line with `Current` / `Historical` or `In Range` / `Out of Range` columns, the
     default reader may already read it. Run it first.
   - If its header labels are new (for example `Result` / `Reference Interval`), add them to
     `pipeline._BLOODWORK_HEADER_LABELS` (and `_COLUMN_HEADER_WORDS` for repeated header lines) rather than writing a
     new parser.
   - Otherwise write a module like `access_medical.py`:
     - `NAME`.
     - `detect(pages)`: true only when the layout's own printed structure is present (a header line plus header-block
       labels, never a lab name alone).
     - `parse(pages, ...)`: returns `(marker_occurrences, unrecognized_rows, info)` read from exact text and word
       boxes.
     - Append the module to `lab_layouts.LAYOUTS`.
3. **Header detection.** Read the patient name, the Collected date and any Fasting / collection-time fields from the
   printed header only. Two-digit years and similar formats are converted in one helper with a test. A field that is
   not printed stays empty; it is never guessed.
4. **Aliases.** Map the lab's printed test names with exact aliases only: `markers_reference.py` for scored markers,
   `lab_reported.py` for shown-but-not-scored tests. An alias that routes a test to a scored marker is a clinical
   change (see the Pull request workflow in `CLAUDE.md`). Names without an alias stay on the staff
   "Unrecognized marker" list.
5. **Synthetic fixture.**
   - Add a builder under `celldeep-tool/synthetic_fixtures/` (see `access_medical_lab.py` or `chl_extensive.py`). It
     reproduces the layout's structure with invented names, values, dates and IDs: header columns, historical
     columns, flags, placeholders, summary blocks, threshold columns, fasting and assay-change cases.
   - Register it as a scenario in `synthetic_fixtures/scenarios.py` (`build` and `SCENARIOS`).
   - Add focused tests (see `test_access_medical.py`, `test_column_roles.py`, `test_layout_fixtures.py`).
6. **Expected values.**
   - **Synthetic:** `python scripts/regression_check.py --record` writes
     `synthetic_fixtures/expected/<scenario>.json`. Read every new row and confirm it is what the report should
     show before committing. CI then fails on any change to it.
   - **Real (local only):** follow "Checklist: verify and lock a new patient type or layout" above.
7. **Before and after.** Run `python scripts/regression_check.py --snapshot <dir>` before the change and again
   after, then `--diff`. Only the new layout's rows may differ.
8. **Update this file.** Add the layout to the matrix above.
