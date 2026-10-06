"""Smoke test: generate reports from synthetic fixtures end to end and print PASS/FAIL.

    cd celldeep-tool && python ../scripts/smoke_test.py      (or: python scripts/smoke_test.py from the repo root)

Uses invented data only and a scripted vision model, so it needs no API key and no network; it does need the
packages in celldeep-tool/requirements.txt and Playwright's Chromium (python -m playwright install chromium).
It runs in well under a minute. Exit code 0 means every check passed.

Scenarios: a digital Cleveland HeartLab-style report; a scanned report whose reads disagree (a heading read
once, one row misread by one read, one row read three different ways); and the clinic's 7-page DEXA layout
(names redacted, one foreign profile page, an "(e)" body fat, two scans with no printed body fat).
"""

import contextlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "celldeep-tool"
sys.path.insert(0, str(TOOL))
os.environ.setdefault("ANTHROPIC_API_KEY", "offline-smoke-test")  # never used: every read is scripted

from synthetic_fixtures import clinic_dexa  # noqa: E402
from synthetic_fixtures.scenarios import run_scenario  # noqa: E402


def checks(name, result):
    text, review, asked = result["text"], result["review"], result["confirmation"]
    if name == "chl_digital":
        yield "scored markers in the report", all(m in text for m in ("hs-CRP", "LDL Cholesterol", "HbA1c"))
        yield "no confirmation needed", asked == []
    if name == "scanned_noisy":
        yield "2-of-3 read kept (Fasting Insulin 4.4)", "Fasting Insulin" in text and "4.4" in text
        yield "3-way disagreement excluded and listed", "FERRITIN (reads disagree: 88 / 86 / 83)" in review
        yield "heading never asked about", not any("CBC" in item for item in asked) and len(asked) == 1
    if name == "clinic_dexa":
        yield "printed (e) body fat shown as estimated", "19.0% estimated" in text
        yield "computed body fat = fat / (fat + lean)", "22.2% fat computed" in text and "20.4% fat computed" in text
        yield "summary line present", "Body fat 24.4% on 09/08/2025 to 19.0% (estimated) on 03/30/2026." in text
        yield "no '—% optimized'", "—% optimized" not in text.casefold()
        yield "foreign profile nowhere in the report", not any(value in text for value in clinic_dexa.FOREIGN_TEXT)
        yield "foreign page on the confirmation step", len(asked) == 1 and "page 6" in asked[0]
        yield "staff warning for an old DEXA scan", "DEXA SCAN OLDER THAN BLOODWORK" in review
    yield "STAFF CHECK opens the staff notes", review.startswith("STAFF CHECK - confirm before sending this report")
    yield "no staff text in the patient report", not any(marker in text for marker in
                                                         ("INCOMPLETE", "STAFF CHECK", "source=scan", "excluded"))


def main():
    start, failed = time.time(), 0
    with tempfile.TemporaryDirectory() as folder:
        for name in ("chl_digital", "scanned_noisy", "clinic_dexa"):
            try:
                with contextlib.redirect_stdout(io.StringIO()):  # pipeline progress lines
                    result = run_scenario(name, Path(folder) / name)
                outcomes = list(checks(name, result))
            except Exception as error:  # a crash is a failure, reported by type only
                outcomes = [(f"report generated ({type(error).__name__})", False)]
            for label, passed in outcomes:
                failed += not passed
                print(f"{'PASS' if passed else 'FAIL'}  {name}: {label}")
    print(f"{'PASS' if not failed else 'FAIL'} - {failed} check(s) failed, {time.time() - start:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
