import fitz

import pipeline
import scoring
import template


def _copy_for_render():
    return {
        "box_stories": {}, "box_forward": {}, "headlines": {},
        "marker_notes": {}, "marker_what": {}, "category_taglines": {},
        "group_narratives": {}, "optimization_summary_bullets": [],
    }


def _rendered_text(tmp_path, vitality_index=None, dexa_history=None):
    record, _ = pipeline.score_and_build_record({
        "name": "Synthetic Vitality Patient",
        "sex": "male",
        "latest_draw_date": "09/01/2026",
        "markers": [{"name": "hs-CRP", "now": 0.7, "disp_now": "0.7"}],
        "vitality_index": vitality_index or {},
        "dexa_history": dexa_history or [],
    })
    output = tmp_path / "vitality-display.pdf"
    template.render(record, _copy_for_render(), str(output))
    with fitz.open(output) as document:
        return record, "\n".join(page.get_text() for page in document)


def test_full_vitality_index_renders_all_domains_and_concern_levels(tmp_path):
    selections = {
        "Energy": "No Concern",
        "Sleep": "Some Concern",
        "Mental Clarity & Focus": "Significant Concern",
        "Mood & Emotional Balance": "No Concern",
        "Cravings": "Some Concern",
        "Sexual Desire": "No Concern",
        "Sexual Function": "Significant Concern",
    }
    record, text = _rendered_text(tmp_path, selections)

    assert "Symptom / Vitality Index" in text
    assert "57%" in text
    for label, status in selections.items():
        assert label in text
        assert status in text
    assert "Physical Performance" not in text
    assert scoring.symptom_percent_optimized(record.vitality_index) == (14 - 6) / 14 * 100


def test_missing_vitality_index_renders_not_assessed_without_score(tmp_path):
    record, text = _rendered_text(tmp_path)
    normalized_text = " ".join(text.split())

    assert scoring.symptom_percent_optimized(record.vitality_index) is None
    assert "Symptom / Vitality Index" in text
    assert "Not assessed this round" in normalized_text
    assert "Seven scored domains" in text


def test_vitality_display_does_not_change_composite_headline(tmp_path):
    selections = {label: "No Concern" for label in scoring.VITALITY_LABELS}
    record, text = _rendered_text(tmp_path, selections)
    _roll, _order, overall_now, _overall_then, _has_dexa = template.build_rollups(
        record, None, None, False
    )
    bloodwork_now = round(sum(
        template.build_rollups(record, None, None, False)[0][category]["now"]
        for category in template.DATA_TO_PATIENT_CATEGORY.values()
    ) / len(template.DATA_TO_PATIENT_CATEGORY))
    expected = round(scoring.overall_percent_optimized(
        bloodwork_now, scoring.symptom_percent_optimized(record.vitality_index), None
    ))

    assert overall_now == expected
    assert f"{expected}%" in text


def test_dexa_panel_discloses_interim_reference_ranges():
    record, _ = pipeline.score_and_build_record({
        "name": "Synthetic DEXA Patient",
        "sex": "male",
        "dexa_history": [{
            "date_display": "09/01/2026", "total_mass_lb": 180.0,
            "fat_mass_lb": 27.0, "lean_mass_lb": 146.0,
            "body_fat_pct": "15.0%", "visceral_fat_area_cm2": 80.0,
        }],
    })
    roll, _order, _overall_now, _overall_then, _has_dexa = template.build_rollups(
        record, None, None, False
    )

    html = template.dexa_panel(record, _copy_for_render(), roll, None)
    assert "Interim reference ranges use standard medical/athletic body-fat and visceral-fat ranges" in html
