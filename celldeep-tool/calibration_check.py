"""Required A-H regression gate for composite scoring integration."""

import json
import re
from dataclasses import asdict
from pathlib import Path

import fitz

from dexa_reference import dexa_percent_optimized
import pipeline
import scoring
import template


ROOT = Path(__file__).parent
BASELINE_DIR = ROOT.parent / "calibration_baselines" / "pre-composite"
REFERENCE_IDS = ("SYN-COMPLETE-1", "SYN-EXTENSIVE-2", "SYN-LIMITED-3")


def _differences(expected, actual, path=""):
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences = []
        for key in sorted(set(expected) | set(actual)):
            child_path = f"{path}.{key}" if path else key
            if key not in expected:
                differences.append(f"{child_path}: added {actual[key]!r}")
            elif key not in actual:
                differences.append(f"{child_path}: removed {expected[key]!r}")
            else:
                differences.extend(_differences(expected[key], actual[key], child_path))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        differences = []
        for index in range(max(len(expected), len(actual))):
            child_path = f"{path}[{index}]"
            if index >= len(expected):
                differences.append(f"{child_path}: added {actual[index]!r}")
            elif index >= len(actual):
                differences.append(f"{child_path}: removed {expected[index]!r}")
            else:
                differences.extend(_differences(expected[index], actual[index], child_path))
        return differences
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def _legacy_extracted(record_data):
    marker_fields = {
        "name", "then", "now", "disp_then", "disp_now", "then_date_display",
        "now_date_display", "is_good_then", "is_good_now", "full_history",
        "lab_range_then", "lab_range_now",
    }
    markers = []
    for marker in record_data["markers"]:
        raw = {key: marker.get(key) for key in marker_fields}
        for draw in ("then", "now"):
            lab_range = marker.get(f"lab_range_{draw}")
            if lab_range:
                raw.update({
                    f"lab_range_{draw}_lo": lab_range["lo"],
                    f"lab_range_{draw}_hi": lab_range["hi"],
                    f"lab_range_{draw}_display": lab_range["display"],
                })
        markers.append(raw)
    return {
        "name": record_data["name"],
        "age": record_data["age"],
        "sex": record_data["sex"],
        "first_draw_date": record_data["first_draw_date"] or "",
        "latest_draw_date": record_data["latest_draw_date"] or "",
        "markers": markers,
        "dexa_history": record_data["dexa_history"],
        "protocol": record_data["protocol"],
        "pain_points": record_data["pain_points"],
        "cns_domains": record_data["cns_domains"],
        "provider_note_raw": record_data["provider_note_raw"],
    }


def _scored_snapshot(record):
    record_data = asdict(record)
    rollups, order, overall_now, overall_then, has_dexa = template.build_rollups(
        record, None, None, False
    )
    return {
        "record": record_data,
        "category_rollups": {
            category: {key: value for key, value in rollup.items() if key != "weak"}
            for category, rollup in rollups.items() if category != "Structure"
        },
        "category_order": order,
        "percent_optimized": overall_now,
        "percent_optimized_then": overall_then,
        "has_dexa": has_dexa,
    }


def check_a():
    results = {}
    for patient_id in REFERENCE_IDS:
        baseline = json.loads((BASELINE_DIR / f"{patient_id}.json").read_text(encoding="utf-8"))
        record, _ = pipeline.score_and_build_record(_legacy_extracted(baseline["record"]))
        current = _scored_snapshot(record)
        current_record = current["record"]
        current_record.pop("vitality_index")
        for reading in current_record["dexa_history"]:
            reading.pop("visceral_fat_area_cm2")
        current_without_composite = {**current, "record": current_record}
        current_without_composite.pop("percent_optimized")
        baseline_without_composite = dict(baseline)
        baseline_without_composite.pop("percent_optimized")
        differences = _differences(baseline_without_composite, current_without_composite)
        assert not differences, f"{patient_id}: forbidden scored JSON changes:\n" + "\n".join(differences)
        results[patient_id] = {
            "markers": len(current_record["markers"]),
            "marker_tiers": {
                marker["name"]: {"then": marker["then_tier"], "now": marker["now_tier"]}
                for marker in current_record["markers"]
            },
            "category_scores": {
                category: {"then": values["then"], "now": values["now"]}
                for category, values in current["category_rollups"].items()
            },
            "dexa_raw": current_record["dexa_history"],
            "bloodwork_pct": baseline["percent_optimized"],
            "composite_pct": current["percent_optimized"],
        }
    return results


def check_b():
    note_text = (ROOT / "synthetic_fixtures" / "syn_composite_provider_note.md").read_text(encoding="utf-8")
    selections = {
        label: re.search(rf"^- {re.escape(label)}: (.+)$", note_text, re.MULTILINE).group(1)
        for label in scoring.VITALITY_LABELS
    }
    domain_scores = scoring.normalize_vitality_index(selections)
    with fitz.open(ROOT / "synthetic_fixtures" / "syn_composite_dexa.pdf") as document:
        dexa_text = "\n".join(page.get_text() for page in document)
    body_fat = float(re.search(r"Body Fat: ([0-9.]+)%", dexa_text).group(1))
    vat_area = float(re.search(r"Visceral Fat Area: ([0-9.]+) cm2", dexa_text).group(1))
    bloodwork = 80
    symptoms = scoring.symptom_percent_optimized(domain_scores)
    dexa = dexa_percent_optimized(body_fat, vat_area, "male")
    assert symptoms == (14 - 6) / 14 * 100
    expected = (60 * bloodwork + 30 * symptoms + 10 * dexa) / 100
    actual = scoring.overall_percent_optimized(bloodwork, symptoms, dexa)
    assert round(actual, 4) == round(expected, 4)
    return {"bloodwork": bloodwork, "symptom": symptoms, "dexa": dexa,
            "answered_domains": 7, "concern_score": 6,
            "hand_calculation": "(60*80 + 30*(8/14*100) + 10*96) / 100",
            "composite": actual}


def check_c():
    actual = scoring.overall_percent_optimized(80, 50, None)
    assert actual == (60 / 90) * 80 + (30 / 90) * 50
    return {"bloodwork": 80, "symptom": 50, "dexa": None, "composite": actual}


def check_d():
    actual = scoring.overall_percent_optimized(80, None, 96)
    assert actual == (60 / 70) * 80 + (10 / 70) * 96
    return {"bloodwork": 80, "symptom": None, "dexa": 96, "composite": actual}


def check_e():
    actual = scoring.overall_percent_optimized(80, None, None)
    assert actual == 80
    return {"bloodwork": 80, "symptom": None, "dexa": None, "composite": actual}


def check_f():
    values = {"energy": 0, "sleep": 1, "mental_clarity_focus": 2}
    actual = scoring.symptom_percent_optimized(values)
    assert actual == (6 - 3) / 6 * 100
    return {"answered": 3, "concern_score": 3, "denominator": 6, "symptom": actual}


def check_g():
    actual = dexa_percent_optimized("15.0%", None, "male")
    assert actual == 96
    return {"body_fat_pct": 15.0, "visceral_fat_area_cm2": None, "dexa": actual}


def check_h():
    values = {"energy": 0, "sleep": 1, "mental_clarity_focus": 2}
    without = scoring.symptom_percent_optimized(values)
    with_physical = scoring.symptom_percent_optimized({**values, "physical_performance": 2})
    assert with_physical == without
    return {"without_physical_performance": without, "with_physical_performance": with_physical}


def main() -> None:
    checks = {
        "A": check_a,
        "B": check_b,
        "C": check_c,
        "D": check_d,
        "E": check_e,
        "F": check_f,
        "G": check_g,
        "H": check_h,
    }
    for name, check in checks.items():
        print(f"{name} PASS {json.dumps(check(), sort_keys=True)}")


if __name__ == "__main__":
    main()