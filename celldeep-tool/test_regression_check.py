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
