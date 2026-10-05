"""Reproducibility: one synthetic fixture per production layout, a golden file per fixture, and
idempotence (same inputs -> same report, in-process and across interpreter hash seeds).

Golden files live in synthetic_fixtures/golden/. When a change is intended, regenerate with
CELLDEEP_UPDATE_GOLDEN=1 python -m pytest test_reproducibility.py and review the JSON diff: every
value, flag, range, date, tier and staff note that moved is visible there."""

import json
import os
import subprocess
import sys
from pathlib import Path

import fitz
import pytest

import pipeline
import template
from synthetic_fixtures import layouts

GOLDEN = Path(__file__).parent / "synthetic_fixtures" / "golden"


def build(name, directory):
    builder, reader = layouts.FIXTURES[name]
    path = builder(Path(directory) / f"{name}.pdf")
    extracted = pipeline.extract(str(path), [], None, patient_name=layouts.PATIENT,
                                 collected_date=layouts.SCAN_DATE if reader else None,
                                 client=reader() if reader else None, audit_root=str(directory))
    record, notice = pipeline.score_and_build_record({**extracted, "sex": "male", "age": 44})
    return extracted, record, notice


def snapshot(extracted, record, notice):
    return {
        "first_draw_date": extracted["first_draw_date"],
        "latest_draw_date": extracted["latest_draw_date"],
        "scored": [{
            "name": m.name, "then": m.disp_then, "then_date": m.then_date_display, "now": m.disp_now,
            "now_date": m.now_date_display, "then_tier": m.then_tier, "now_tier": m.now_tier,
            "celldeep_range": m.disp_range, "range_source": m.range_source, "unscored": m.unscored_reason,
            "lab_range_now": (m.lab_range_now or {}).get("display"),
            "history": m.full_history,
        } for m in record.markers],
        "lab_reported": [{"name": item.name, "group": item.group, "results": item.results}
                         for item in record.lab_reported],
        "staff_unrecognized": [{"raw_name": item["raw_name"], "raw_range": item["raw_range"],
                                "cells": [{key: cell.get(key) for key in ("date_display", "disp_value", "lab_flag")}
                                          for cell in item["cells"]]}
                               for item in extracted["unrecognized_markers"]],
        "scan_summary": notice.scan_summary,
        "staff_notes": notice.other_notes,
    }


@pytest.mark.parametrize("name", sorted(layouts.FIXTURES))
def test_golden_values_flags_ranges_and_dates(name, tmp_path):
    actual = snapshot(*build(name, tmp_path))
    path = GOLDEN / f"{name}.json"
    if os.environ.get("CELLDEEP_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, f"{name} changed; review and regenerate golden files if intended"


def _report(name, directory):
    Path(directory).mkdir(parents=True, exist_ok=True)
    extracted, record, notice = build(name, directory)
    out = Path(directory) / "report.pdf"
    template.render(record, pipeline.build_copy(record), str(out))
    html = out.with_suffix(".html").read_text(encoding="utf-8")
    with fitz.open(out) as document:
        text = "\n".join(page.get_text() for page in document)
    return html, text, json.dumps(snapshot(extracted, record, notice), sort_keys=True)


@pytest.mark.parametrize("name", sorted(layouts.FIXTURES))
def test_same_inputs_produce_the_same_report_twice(name, tmp_path):
    first = _report(name, tmp_path / "first")
    second = _report(name, tmp_path / "second")
    assert first[0] == second[0], "report HTML differs between identical runs"
    assert first[1] == second[1], "report PDF text differs between identical runs"
    assert first[2] == second[2]


_CHILD = """
import json, sys, tempfile
sys.path.insert(0, {root!r})
import test_reproducibility as reproducibility
import pipeline, template
with tempfile.TemporaryDirectory() as directory:
    html, text, data = reproducibility._report({name!r}, directory)
print(json.dumps([html, text, data]))
"""


@pytest.mark.parametrize("name", ["mixed", "quest_digital"])
def test_report_does_not_depend_on_python_hash_seed(name):
    outputs = []
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "ANTHROPIC_API_KEY": "offline-mocked-key"}
        result = subprocess.run([sys.executable, "-c", _CHILD.format(root=str(Path(__file__).parent), name=name)],
                                capture_output=True, text=True, env=env, cwd=Path(__file__).parent, check=True)
        outputs.append(json.loads(result.stdout.strip().splitlines()[-1]))
    assert outputs[0] == outputs[1]
