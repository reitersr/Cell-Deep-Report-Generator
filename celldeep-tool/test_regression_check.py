"""The regression check (scripts/regression_check.py) on every synthetic fixture: the full pipeline up to the report
data must match synthetic_fixtures/expected/*.json exactly. A change that alters any marker, flag, scored status,
lab-reported result or DEXA row fails here until its expected file is deliberately re-recorded (--record)."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "regression_check.py"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("regression_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["regression_check"] = module
    spec.loader.exec_module(module)
    return module


def test_every_synthetic_fixture_matches_its_expected_report_data(check, monkeypatch, capsys):
    monkeypatch.setattr(check, "REAL", Path("/nonexistent"))  # real fixtures are checked locally only
    results = check.current()
    assert set(results) == set(check.scenarios.SCENARIOS)
    failures = check.compare(results)
    assert failures == 0, capsys.readouterr().out


def test_a_changed_value_is_reported_as_a_difference(check, capsys):
    results = {"example": {"rows": ["M | TSH | 03/02/2026 | 2.0 |  | optimal"], "skipped": [], "mode": "exact",
                           "expected": ["M | TSH | 03/02/2026 | 1.9 |  | optimal"]}}
    assert check.compare(results) == 1
    out = capsys.readouterr().out
    assert "- M | TSH | 03/02/2026 | 1.9 |  | optimal" in out and "+ M | TSH | 03/02/2026 | 2.0 |  | optimal" in out


def test_real_fixture_rows_may_leave_a_field_open(check):
    assert check._matches("D | 01/01/2025 | total 1 | *", "D | 01/01/2025 | total 1 | VAT cm2 90")
    assert not check._matches("M | TSH | 03/02/2026 | 1.9 |  | *", "M | TSH | 03/02/2026 | 2.0 |  | optimal")


def test_score_outputs_are_part_of_the_checked_report_data(check):
    rows = check.json.loads((check.EXPECTED / "chl_extensive.json").read_text())["rows"]
    assert any(row.startswith("S | ") and " | score " in row for row in rows)
    assert sum(row.startswith("O | overall now ") for row in rows) == 1


def test_a_real_fixture_without_its_dexa_read_skips_only_the_rows_that_use_it(check):
    assert check._uses_dexa("D | 01/01/2025 | total 1 | fat 1 | lean 1 | body fat — (-) | VAT lb  | VAT cm2 ")
    assert check._uses_dexa("O | overall now 70 | first visit ") and check._uses_dexa("S | Structure | score 50 | moderate")
    assert not check._uses_dexa("S | Flow | optimal 1 | moderate 0 | flag 0 | not scored 0 | score 90 | optimal")
    assert not check._uses_dexa("M | TSH | 03/02/2026 | 1.9 |  | optimal")


def test_the_vitality_index_is_a_fixed_input_of_every_checked_case(check, monkeypatch):
    # Synthetic cases always answer "Not Assessed" for every domain, whatever the upload form's defaults become.
    assert set(check.VITALITY_NOT_ASSESSED) == set(check.pipeline.scoring.VITALITY_LABELS)
    assert set(check.VITALITY_NOT_ASSESSED.values()) == {"Not Assessed"}
    seen = []
    monkeypatch.setattr(check, "_capture", lambda run: (run(), (None, None))[1])
    monkeypatch.setattr(check.pipeline, "run", lambda *args, **kwargs: seen.append(kwargs["vitality_index"]))
    monkeypatch.setattr(check, "rows", lambda *args: [])
    check.synthetic_rows("quest_digital", Path(check.tempfile.mkdtemp()))
    assert seen == [check.VITALITY_NOT_ASSESSED]
    # A real fixture's spec stores the verified report's answers; unstored domains are "Not Assessed".
    fixed = check.spec_vitality({"vitality_index": {"Energy": "Some Concern"}})
    assert fixed["Energy"] == "Some Concern" and fixed["Sleep"] == "Not Assessed" and len(fixed) == len(seen[0])
    with pytest.raises(ValueError):
        check.spec_vitality({"vitality_index": {"Energy": "Fine"}})
    with pytest.raises(ValueError):
        check.spec_vitality({"vitality_index": {"Stamina": "No Concern"}})


def test_absent_real_fixtures_are_reported_as_not_checked_never_passed(check, monkeypatch, capsys):
    monkeypatch.setattr(check, "REAL", Path("/nonexistent"))  # as in CI: real_fixtures/ is git-ignored
    locked = [name for name, case in check.REAL_CASES.items() if case["locked"]]
    assert check.real_missing() == locked
    assert check.missing_note(locked).startswith(f"real fixtures not present: {len(locked)} cases not checked")
    monkeypatch.setattr(check.sys, "argv", ["regression_check.py", "--real"])
    assert check.main() == 0
    assert f"real fixtures not present: {len(locked)} cases not checked" in capsys.readouterr().out


def test_every_locked_real_report_matches_when_present(check, capsys):
    # Locally (real_fixtures/ present) every locked real report is checked; in CI this skips with the count.
    present = {f"real:{name}" for name, case in check.REAL_CASES.items()
               if case["locked"] and (check.REAL / f"{name}.expected.json").is_file()}
    if not present:
        pytest.skip(check.missing_note(check.real_missing()))
    failures = check.compare(check.current(present))
    assert failures == 0, capsys.readouterr().out


def test_regression_status_doc_lists_every_real_case(check):
    doc = (SCRIPT.parent.parent / "docs" / "regression_status.md").read_text(encoding="utf-8")
    rows = {cells[0].strip("` "): cells for line in doc.splitlines() if line.startswith("| `")
            for cells in [[cell.strip() for cell in line.strip("|").split("|")]]}
    real_rows = {name: cells for name, cells in rows.items() if name in check.REAL_CASES}
    assert set(real_rows) == set(check.REAL_CASES)
    for name, case in check.REAL_CASES.items():
        assert ("yes" if case["locked"] else "no") in [cell.casefold() for cell in real_rows[name]], name
