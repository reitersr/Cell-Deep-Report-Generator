"""Regression check: run every available fixture through the full pipeline up to the report data and compare the
result with its expected-values file, so a fix for one report cannot silently change another.

    python scripts/regression_check.py                 check every fixture; print each difference; exit 1 on any
    python scripts/regression_check.py --record        rewrite the synthetic expected files from the current output
    python scripts/regression_check.py --snapshot DIR  write every fixture's current rows to DIR (before/after diffs)
    python scripts/regression_check.py --diff A B      diff two snapshot directories
    python scripts/regression_check.py --record-real real:NAME   lock a hand-verified real report (local only)
    python scripts/regression_check.py --real          check only the locked real reports (local, one command)

Fixtures:
- every synthetic scenario (synthetic_fixtures/scenarios.py, scripted vision reads, no network); expected rows in
  synthetic_fixtures/expected/<scenario>.json, compared exactly. CI runs this through test_regression_check.py.
- every real_fixtures/<name>.expected.json (git-ignored, never committed: it holds a real report's values). It names
  its lab PDF and DEXA PDF(s) in real_fixtures/ and the staff entries ("labs", "dexa", "patient", "age", "sex",
  "collected_date", "vitality_index", optional "scan_collected_date", "dexa_reads", "lab_scan_reads"), and lists the report's rows: "mode": "exact" (written by --record-real)
  compares every row; without it the list is a subset that may leave fields open with "*". Real DEXA pages are read by the vision model (CELLDEEP_LIVE_VISION=1 and an API
  key) or replayed from a stored transcription ("dexa_reads"); without either, the DEXA rows and the scores that use
  them are reported as SKIPPED (never silently passed). --record-real fills "rows" from the current output once the
  report has been verified by hand.

Each row is one line: a scored-or-not marker result (M), a lab-reported result (L), a DEXA scan (D), a system's score
outputs (S: optimal / moderate / flag / not-scored counts, score, color; S | Structure: the DEXA score) or the overall
score (O: now and at the first visit).
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "celldeep-tool"
sys.path.insert(0, str(TOOL))
os.environ.setdefault("ANTHROPIC_API_KEY", "offline-regression-check")  # synthetic reads are scripted

import pipeline  # noqa: E402
from synthetic_fixtures import scenarios  # noqa: E402

EXPECTED = TOOL / "synthetic_fixtures" / "expected"
DEXA_READS_OVERRIDE = None  # --dexa-reads
REAL = TOOL / "real_fixtures"
TIERS = ("optimal", "moderate", "flag")
# The Vitality Index is a fixed input of every checked case, never the upload form's current defaults: synthetic
# cases answer "Not Assessed" for every domain, and a real fixture's spec stores the answers its verified report used
# ("vitality_index"; --record-real writes it). A change to the form therefore never shows up as a regression.
VITALITY_NOT_ASSESSED = {label: "Not Assessed" for label in pipeline.scoring.VITALITY_LABELS}
# The verified real patient types, by case name only: their PDFs, transcriptions and expected values stay in the
# git-ignored real_fixtures/ (<case>.expected.json once locked). docs/regression_status.md lists the same cases
# (test_regression_check keeps the two in sync). A locked case whose files are not present is never passed silently:
# the run prints "real fixtures not present: N cases not checked".
REAL_CASES = {
    "extensive_male": {"type": "Male, extensive panel", "locked": True,
                       "layout": "Cleveland HeartLab digital report (multi-draw, Historical column) + Lunar DEXA"},
    "female_chl": {"type": "Female", "locked": True,
                   "layout": "Cleveland HeartLab digital report + undated scanned earlier draw + Lunar DEXA"},
    "limited_male": {"type": "Male, limited panel", "locked": True,
                     "layout": "Access Medical Laboratories digital report + Lunar DEXA"},
    "chl_quest_male": {"type": "Male, CHL panels plus a scanned Quest draw", "locked": False,
                       "layout": "Cleveland HeartLab digital report + scanned Quest pages + Lunar DEXA"},
}


def _num(value):
    return "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def score_rows(record, copy) -> list[str]:
    """The score outputs as the report computes them (template.build_rollups, read only): per system, how many
    scored markers are optimal / moderate / flagged, the system score and its color; the DEXA (Structure) score;
    the overall score now and at the first visit."""
    roll, _, overall_now, overall_then, has_dexa = pipeline.template.build_rollups(
        record, copy.get("structure_score_now"), copy.get("structure_score_then"), copy.get("structure_improved", False))
    out = []
    for system, rollup in roll.items():
        if system == "Structure":
            continue
        tiers = [m.now_tier for m in pipeline.template.markers_for_category(record, system)]
        if not tiers:
            continue  # a system with no markers is not shown in the report
        counts = " | ".join(f"{tier} {tiers.count(tier)}" for tier in TIERS)
        out.append(f"S | {system} | {counts} | not scored {sum(t not in TIERS for t in tiers)} | score "
                   f"{rollup['now']} | {rollup['now_zone']}")
    if has_dexa:
        out.append(f"S | Structure | score {_num(roll['Structure']['now'])} | {roll['Structure']['now_zone']}")
    out.append(f"O | overall now {overall_now} | first visit {_num(overall_then)}")
    return out


def rows(record, copy=None) -> list[str]:
    """The report data that must not change by accident: every marker result with its flag and whether it is
    scored, every lab-reported result with its printed range, every DEXA scan's measurements and labels, and the
    score outputs (score_rows)."""
    out = score_rows(record, copy) if copy is not None else []
    for m in record.markers:
        name = m.display_name or m.name
        points = [(m.then_date_display, m.disp_then, m.lab_flag_then, m.then_tier),
                  *[(h["date_display"], h["disp_value"], h.get("lab_flag"), None) for h in m.full_history],
                  (m.now_date_display, m.disp_now, m.lab_flag_now, m.now_tier)]
        for date, value, flag, tier in points:
            if date and value not in (None, ""):
                scored = tier if tier in TIERS else "history" if tier is None and (date, value) != (
                    m.now_date_display, m.disp_now) and (date, value) != (m.then_date_display, m.disp_then) \
                    else "not scored"
                out.append(f"M | {name} | {date} | {value} | {flag or ''} | {scored}")
    for item in record.lab_reported:
        for result in item.results:
            out.append(f"L | {item.group} | {item.name} | {result['date_display']} | {result['disp_value']} | "
                       f"{result.get('lab_flag') or ''} | {result.get('lab_range') or ''}")
    for d in record.dexa_history:
        label = "computed" if "body_fat_pct" in d.computed else "estimated" if "body_fat_pct" in d.estimated else \
            "printed"
        out.append(f"D | {d.date_display} | total {_num(d.total_mass_lb)} | fat {_num(d.fat_mass_lb)} | lean "
                   f"{_num(d.lean_mass_lb)} | body fat {d.body_fat_pct or '—'} ({label if d.body_fat_pct else '-'})"
                   f" | VAT lb {_num(d.vat_fat_mass_lb)} | VAT cm2 {_num(d.visceral_fat_area_cm2)}")
    return sorted(out)


def _capture(run):
    """Run the pipeline with rendering replaced by a capture of the report data (no PDF is written)."""
    captured = {}
    original = pipeline.template.render
    pipeline.template.render = lambda record, copy, out_path, **kwargs: captured.update(record=record, copy=copy)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run()
    finally:
        pipeline.template.render = original
    return captured["record"], captured["copy"]


def synthetic_rows(name, folder) -> list[str]:
    os.environ[pipeline.VISION_FALLBACK_ENV] = "0"
    folder.mkdir(parents=True, exist_ok=True)
    labs, dexa, reads, options = scenarios.build(name, folder)
    vision = scenarios.ScriptedVision(reads)
    saved = (pipeline.Anthropic, pipeline._review_notes_path, pipeline._diagnostic_path_prefix)
    pipeline.Anthropic = lambda **kwargs: vision
    pipeline._review_notes_path = lambda _name: str(folder / "review.txt")
    pipeline._diagnostic_path_prefix = lambda _name: str(folder / "diag")
    try:
        patient = options.get("patient") or (scenarios.clinic_dexa.PATIENT if dexa else scenarios.layouts.PATIENT)
        return rows(*_capture(lambda: pipeline.run(
            labs, dexa, options.get("note"), patient, options.get("age"), options.get("sex", "male"),
            str(folder / "report.pdf"),
            vitality_index=dict(options.get("vitality_index") or VITALITY_NOT_ASSESSED),
            collected_date=options.get("collected_date"), confirm=lambda items: True)))
    finally:
        pipeline.Anthropic, pipeline._review_notes_path, pipeline._diagnostic_path_prefix = saved


class ScriptedReads:
    """Offline stand-in for the vision model on a real fixture: answers each page with a stored literal
    transcription (git-ignored, in real_fixtures/), the same for every read of the page. dexa_pages: {page: scans
    list, or {"age": printed age, "scans": [...]}} (spec "dexa_reads"); lab_pages: {page: one scanned-lab page
    transcription in the scan schema} (spec "lab_scan_reads"). Scanned lab requests ask to "Transcribe PDF page N"."""

    def __init__(self, dexa_pages, lab_pages=None):
        self.dexa_pages, self.lab_pages = dexa_pages or {}, lab_pages or {}
        self.messages, self.timeout = self, 240.0

    def create(self, **kwargs):
        import copy
        import re
        from types import SimpleNamespace
        from synthetic_fixtures.sdk_contract import check_create_kwargs
        check_create_kwargs(kwargs)
        text = kwargs["messages"][0]["content"][1]["text"]
        number = int(re.search(r"page (\d+)", text)[1])
        if text.startswith("Transcribe PDF page"):
            payload = copy.deepcopy(self.lab_pages.get(str(number), {
                "page": number, "specimen_id": None, "collected": None, "footer": None, "patient_name": None,
                "date_of_birth": None, "illegible": True, "rows": [], "out_of_range_summary": None}))
            payload["page"] = number
            for row in payload["rows"]:
                row["page"] = number
        else:
            entry = self.dexa_pages.get(str(number), [])
            entry = entry if isinstance(entry, dict) else {"age": None, "scans": entry}
            payload = {"page": number, "patient_name": None, "date_of_birth": None, "age": entry["age"],
                       "illegible": False, "scans": entry["scans"]}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))], stop_reason="end_turn")


def real_rows(spec, folder, reads_file=None) -> tuple[list[str], list[str]]:
    """(rows, skipped notes) for one git-ignored real fixture. Real DEXA and scanned lab pages are read by the vision
    model (CELLDEEP_LIVE_VISION=1 and an API key), or offline from the fixture's stored transcriptions
    ("dexa_reads", "lab_scan_reads")."""
    folder.mkdir(parents=True, exist_ok=True)
    skipped = []
    dexa = [str(REAL / name) for name in spec.get("dexa", [])]
    missing = [name for name in spec.get("dexa", []) if not (REAL / name).is_file()]
    live = os.environ.get("CELLDEEP_LIVE_VISION") == "1"
    reads_file = reads_file or spec.get("dexa_reads")
    lab_reads = spec.get("lab_scan_reads")
    dexa_pages = lab_pages = None
    if dexa and not missing and not live and reads_file and (REAL / reads_file).is_file():
        dexa_pages = json.loads((REAL / reads_file).read_text())["pages"]
        skipped.append(f"DEXA read from the stored transcription {reads_file} (not a live vision read)")
    elif missing or (dexa and not live):
        skipped.append("DEXA rows SKIPPED: " + (f"file(s) not in real_fixtures/: {', '.join(missing)}" if missing
                                                else "real DEXA pages need CELLDEEP_LIVE_VISION=1 and an API key"))
        dexa = []
    if lab_reads and not live and (REAL / lab_reads).is_file():
        lab_pages = json.loads((REAL / lab_reads).read_text())["pages"]
        skipped.append(f"scanned lab pages read from the stored transcription {lab_reads} (not a live vision read)")
    saved_client = pipeline.Anthropic
    if dexa_pages is not None or lab_pages is not None:
        scripted = ScriptedReads(dexa_pages, lab_pages)
        pipeline.Anthropic = lambda **kwargs: scripted
    saved = (pipeline._review_notes_path, pipeline._diagnostic_path_prefix)
    pipeline._review_notes_path = lambda _name: str(folder / "review.txt")
    pipeline._diagnostic_path_prefix = lambda _name: str(folder / "diag")
    try:
        record, copy = _capture(lambda: pipeline.run(
            str(REAL / spec["labs"]), dexa, None, spec.get("patient"), spec.get("age"), spec.get("sex", "male"),
            str(folder / "report.pdf"), vitality_index=dict(spec_vitality(spec)),
            collected_date=spec.get("collected_date"), confirm=lambda items: True,
            scan_collected_date=spec.get("scan_collected_date")))
    finally:
        pipeline._review_notes_path, pipeline._diagnostic_path_prefix = saved
        pipeline.Anthropic = saved_client
    return rows(record, copy), skipped


def spec_vitality(spec) -> dict:
    """The fixed Vitality Index answers of a real fixture: every domain, as stored in its spec ("Not Assessed" for a
    domain the verified report had no answer for). An unknown domain or answer is an error, never ignored."""
    given = spec.get("vitality_index") or {}
    unknown = sorted(set(given) - set(VITALITY_NOT_ASSESSED))
    bad = sorted(label for label, value in given.items()
                 if value != "Not Assessed" and pipeline.scoring.VITALITY_VALUE_SCORES.get(value) is None)
    if unknown or bad:
        raise ValueError(f"vitality_index in the spec has unknown domains {unknown} or answers for {bad}")
    return {**VITALITY_NOT_ASSESSED, **given}


def current(selected=None) -> dict[str, dict]:
    """{fixture: {"rows": [...], "expected": [...] or None, "mode": "exact"|"subset", "skipped": [...]}}"""
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in scenarios.SCENARIOS:
            if selected and name not in selected:
                continue
            expected_file = EXPECTED / f"{name}.json"
            results[name] = {"rows": synthetic_rows(name, Path(tmp) / name),
                             "expected": json.loads(expected_file.read_text())["rows"] if expected_file.is_file()
                             else None, "mode": "exact", "skipped": []}
        for spec_file in sorted(REAL.glob("*.expected.json")) if REAL.is_dir() else []:
            name = "real:" + spec_file.name.removesuffix(".expected.json")
            if selected and name not in selected:
                continue
            spec = json.loads(spec_file.read_text())
            if not (REAL / spec["labs"]).is_file():
                results[name] = {"rows": [], "expected": None, "mode": "subset",
                                 "skipped": [f"lab PDF {spec['labs']} not in real_fixtures/"]}
                continue
            got, skipped = real_rows(spec, Path(tmp) / spec_file.stem, DEXA_READS_OVERRIDE)
            dexa_checked = not any(note.startswith("DEXA rows SKIPPED") for note in skipped)
            # Without the DEXA read, its rows and the scores that use it (Structure, overall) cannot be checked;
            # they are reported as skipped above, never passed.
            expected = [row for row in spec["rows"] if dexa_checked or not _uses_dexa(row)]
            got = [row for row in got if dexa_checked or not _uses_dexa(row)]
            results[name] = {"rows": got, "expected": expected, "mode": spec.get("mode", "subset"),
                             "skipped": skipped, "dexa_checked": dexa_checked, "spec_file": spec_file}
    return results


def _uses_dexa(row) -> bool:
    return row.startswith(("D |", "O |", "S | Structure"))


def real_missing() -> list[str]:
    """The locked real cases whose expected-values file is not in real_fixtures/ (always all of them in CI)."""
    return [name for name, case in REAL_CASES.items()
            if case["locked"] and not (REAL / f"{name}.expected.json").is_file()]


def missing_note(missing) -> str:
    return f"real fixtures not present: {len(missing)} cases not checked ({', '.join(missing)})"


def _matches(expected, actual) -> bool:
    want, have = [part.strip() for part in expected.split("|")], [part.strip() for part in actual.split("|")]
    return len(want) == len(have) and all(
        w == "*" or w == h or (w.endswith(" *") and h.startswith(w[:-1])) for w, h in zip(want, have))


def compare(results) -> int:
    failures = 0
    for name, result in results.items():
        for note in result["skipped"]:
            print(f"{name}: {note}")
        if result["expected"] is None:
            print(f"{name}: no expected-values file (run --record for synthetic fixtures)")
            failures += 1
            continue
        got, expected = set(result["rows"]), set(result["expected"])
        if result["mode"] == "subset":
            # Expected rows of a real fixture may leave a field open with "*" (a tier or VAT area not specified).
            missing = sorted(row for row in expected if not any(_matches(row, actual) for actual in got))
            extra = []
        else:
            missing = sorted(expected - got)
            extra = sorted(got - expected)
        if not missing and not extra:
            print(f"{name}: OK ({len(expected)} rows)")
            continue
        failures += 1
        print(f"{name}: DIFFERS")
        for row in missing:
            print(f"  - {row}")
        for row in extra:
            print(f"  + {row}")
    return failures


def snapshot(results, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, result in results.items():
        (directory / f"{name.replace(':', '_')}.txt").write_text("\n".join(result["rows"]) + "\n", encoding="utf-8")


def diff(a, b) -> int:
    changed = 0
    for path in sorted({p.name for p in Path(a).glob("*.txt")} | {p.name for p in Path(b).glob("*.txt")}):
        before = set((Path(a) / path).read_text().splitlines()) if (Path(a) / path).exists() else set()
        after = set((Path(b) / path).read_text().splitlines()) if (Path(b) / path).exists() else set()
        if before != after:
            changed += 1
            print(f"{path.removesuffix('.txt')}:")
            for row in sorted(before - after):
                print(f"  - {row}")
            for row in sorted(after - before):
                print(f"  + {row}")
    if not changed:
        print("no differences")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--diff", nargs=2, type=Path)
    parser.add_argument("--record-real", action="store_true",
                        help="write every current row into the selected real_fixtures/*.expected.json files (after "
                             "the report has been verified by hand); needs the DEXA read (stored transcription or "
                             "CELLDEEP_LIVE_VISION=1)")
    parser.add_argument("--real", action="store_true", help="check only the real fixtures in real_fixtures/")
    parser.add_argument("--dexa-reads", help="use this stored transcription (in real_fixtures/) for real DEXA files")
    parser.add_argument("fixtures", nargs="*", help="limit to these fixture names")
    args = parser.parse_args()
    if args.diff:
        diff(*args.diff)
        return 0
    global DEXA_READS_OVERRIDE
    DEXA_READS_OVERRIDE = args.dexa_reads
    selected = set(args.fixtures) or None
    if args.real:
        selected = {"real:" + spec.name.removesuffix(".expected.json") for spec in REAL.glob("*.expected.json")} \
            if REAL.is_dir() else set()
    results = current(selected) if selected != set() else {}
    if args.snapshot:
        snapshot(results, args.snapshot)
        print(f"snapshot of {len(results)} fixture(s) written to {args.snapshot}")
        return 0
    if args.record:
        EXPECTED.mkdir(parents=True, exist_ok=True)
        for name, result in results.items():
            if not name.startswith("real:"):
                (EXPECTED / f"{name}.json").write_text(json.dumps({"rows": result["rows"]}, indent=1,
                                                                  ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"recorded {sum(not n.startswith('real:') for n in results)} synthetic expected file(s)")
        return 0
    if args.record_real:
        recorded = 0
        for name, result in results.items():
            if not name.startswith("real:") or "spec_file" not in result:
                continue
            if not result["dexa_checked"]:
                print(f"{name}: NOT recorded - its DEXA was not read ({'; '.join(result['skipped'])})")
                continue
            spec = json.loads(result["spec_file"].read_text())
            spec.update(rows=result["rows"], mode="exact", vitality_index=spec_vitality(spec))
            result["spec_file"].write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            recorded += 1
            print(f"{name}: recorded {len(result['rows'])} rows")
        return 0 if recorded else 1
    failures = compare(results)
    if missing := real_missing():
        # Loud, never a silent pass: in GitHub Actions also as a warning annotation on the run.
        print(("::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else "") + missing_note(missing))
    print("PASS" if not failures else f"FAIL - {failures} fixture(s) differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
