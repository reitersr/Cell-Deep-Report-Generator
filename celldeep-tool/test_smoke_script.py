"""scripts/smoke_test.py must keep passing on the synthetic fixtures (staff and owners run it after a deploy)."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_test.py"


def test_smoke_test_passes(capsys):
    spec = importlib.util.spec_from_file_location("smoke_test", SCRIPT)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    assert smoke.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].startswith("PASS - 0 check(s) failed")
    assert not any(line.startswith("FAIL") for line in lines)
