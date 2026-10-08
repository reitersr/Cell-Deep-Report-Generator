# Regression status: verified real patient types

Each row is a real patient type whose generated report was hand-verified against its source PDFs. A **locked** case
has its report data (every lab result, DEXA scan, per-system counts and scores, overall score) recorded with
`scripts/regression_check.py --record-real` and is compared exactly on every run. The PDFs, stored transcriptions and
expected values live only in the git-ignored `celldeep-tool/real_fixtures/`; nothing here names a patient.

| Case | Patient type | Layout | Locked |
|---|---|---|---|
| `extensive_male` | Male, extensive panel | Cleveland HeartLab digital report (multi-draw, Historical column) + Lunar DEXA | yes |
| `female_chl` | Female | Cleveland HeartLab digital report + undated scanned earlier draw + Lunar DEXA | yes |
| `limited_male` | Male, limited panel | Access Medical Laboratories digital report + Lunar DEXA | yes |
| `chl_quest_male` | Male, CHL panels plus a scanned Quest draw | Cleveland HeartLab digital report + scanned Quest pages + Lunar DEXA | yes |

## Stop screens on the locked cases

The pre-generation guards only add stops; the patient PDF text and the review screen's items are unchanged. Every QA
file gains one "PRE-GENERATION CHECK" line.

| Case | Stop screens |
|---|---|
| extensive_male | New: name screen (the de-identified lab prints a name variant), then the review screen as before |
| female_chl | Review screen as before |
| limited_male | None, as before (the Access Medical layout does not read the printed name, so the name guard cannot check it) |
| chl_quest_male | As before |

The stop screens of every case are recorded too: `python scripts/regression_check.py --screens` compares them
(`--record-screens` re-records; the real cases' screens stay in the git-ignored `real_fixtures/stop_screens.json`).
With `KNOWN_ITEMS_AUTOPROCEED` off (the default) they must not change (`test_known_items.py`).

## Running the checks

- **Locally, all real cases, one command** (from the repository root):
  `python scripts/regression_check.py --real`
- **Everything** (synthetic and real): `python scripts/regression_check.py`
- **CI** has no `real_fixtures/`. The run never passes a real case silently: the pytest suite (`-rs`) and the
  "Real fixtures status" step both print `real fixtures not present: N cases not checked (...)`.

The case list lives in `scripts/regression_check.py` (`REAL_CASES`); `test_regression_check.py` fails if this table
and that list disagree. Synthetic fixtures (`synthetic_fixtures/expected/`) are checked in CI on every pull request.
