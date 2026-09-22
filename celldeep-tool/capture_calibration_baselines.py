"""Capture immutable scored JSON baselines before composite scoring changes."""

import json
import os
from dataclasses import asdict
from pathlib import Path

from anthropic import Anthropic

import pipeline
import template


ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT.parent / "calibration_baselines" / "pre-composite"
REFERENCE_PATIENTS = (
    ("SYN-COMPLETE-1", "syn_complete_1.pdf", "Synthetic Test Patient One", 37, "female"),
    ("SYN-EXTENSIVE-2", "syn_extensive_2.pdf", "Synthetic Test Patient Two", 50, "male"),
    ("SYN-LIMITED-3", "syn_limited_3.pdf", "Synthetic Test Patient Three", 29, "male"),
)


def scored_payload(record):
    record_data = asdict(record)
    for marker_data, marker in zip(record_data["markers"], record.markers):
        marker_data.update({
            "then_pct": marker.then_pct,
            "then_tier": marker.then_tier,
            "now_pct": marker.now_pct,
            "now_tier": marker.now_tier,
        })
    rollups, order, overall_now, overall_then, has_dexa = template.build_rollups(
        record, None, None, False
    )
    serializable_rollups = {
        category: {key: value for key, value in rollup.items() if key != "weak"}
        for category, rollup in rollups.items()
    }
    return {
        "record": record_data,
        "category_rollups": serializable_rollups,
        "category_order": order,
        "percent_optimized": overall_now,
        "percent_optimized_then": overall_then,
        "has_dexa": has_dexa,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    for patient_id, filename, patient_name, age, sex in REFERENCE_PATIENTS:
        extracted = pipeline.extract(
            client,
            str(ROOT / "synthetic_fixtures" / filename),
            [],
            None,
            patient_name=patient_name,
        )
        extracted.update({"name": patient_name, "age": age, "sex": sex})
        record, _ = pipeline.score_and_build_record(extracted)
        output_path = OUTPUT_DIR / f"{patient_id}.json"
        if output_path.exists():
            raise FileExistsError(f"Refusing to overwrite immutable baseline: {output_path}")
        output_path.write_text(
            json.dumps(scored_payload(record), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{patient_id}: {output_path}")


if __name__ == "__main__":
    main()