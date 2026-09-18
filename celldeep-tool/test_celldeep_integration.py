"""Deterministic CellDeep alias, category, scoring, status, and render coverage."""

from pathlib import Path

import fitz

import pipeline
import scoring
from schema import Marker
import template
from markers_reference import DATA_TO_PATIENT_CATEGORY, lookup_marker


ALIAS_VALUES = {
    "Symmetric dimethyl-arginine": 100,
    "Estimated avg glucose": 100,
    "Serum C-Peptide": 1.0,
    "T4, Total": 7.0,
    "T3, Total": 120.0,
    "TGAb": 100.0,
    "Ubiquinol": 0.5,
    "Vitamin B9": 6.0,
    "Estimated glomerular filtration rate": 90.0,
    "Inorganic phosphorus": 3.5,
    "Magnesium, serum": 2.0,
    "Urate": 5.0,
    "Serum progesterone": 5.0,
    "Free T": 3.5,
    "Bioavailable T": 350.0,
    "Sex hormone-binding globulin": 60.0,
    "PSA, TOTAL": 1.0,
}

EXPECTED_CATEGORIES = {
    "SDMA": "Also Monitored",
    "Estimated Average Glucose": "Fuel",
    "C-Peptide": "Fuel",
    "Total T4": "Pace",
    "Total T3": "Pace",
    "Thyroglobulin Ab": "Pace",
    "CoQ10": "Reserves",
    "Folate": "Reserves",
    "eGFR": "Also Monitored",
    "Phosphorus": "Also Monitored",
    "Magnesium": "Also Monitored",
    "Uric Acid": "Also Monitored",
    "Progesterone": "Drive",
    "Free Testosterone": "Drive",
    "Bioavailable Testosterone": "Drive",
    "SHBG": "Drive",
    "PSA Total": "Also Monitored",
}


def _pdf_text(path):
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def _copy_for_render():
    return {
        "box_stories": {}, "box_forward": {}, "headlines": {},
        "marker_notes": {}, "marker_what": {}, "category_taglines": {},
        "group_narratives": {}, "optimization_summary_bullets": [],
    }


def test_all_new_markers_alias_score_category_and_render(tmp_path):
    source = tmp_path / "celldeep_aliases.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(36, 36, 560, 780),
        "\n".join(f"{name}: {value}" for name, value in ALIAS_VALUES.items()),
        fontsize=10,
        fontname="cour",
    )
    document.save(source)
    document.close()

    extracted_markers = []
    for line in _pdf_text(source).splitlines():
        raw_name, raw_value = line.split(":", 1)
        canonical, _ = lookup_marker(raw_name.strip())
        assert canonical is not None, raw_name
        extracted_markers.append({
            "name": canonical,
            "now": float(raw_value.strip()),
            "disp_now": raw_value.strip(),
        })

    record, notice = pipeline.score_and_build_record({
        "name": "Synthetic Alias Patient",
        "sex": "female",
        "markers": extracted_markers,
        "provider_note_raw": "",
    })

    assert not notice.unrecognized_markers
    assert {marker.name for marker in record.markers} == set(EXPECTED_CATEGORIES)
    for marker in record.markers:
        assert DATA_TO_PATIENT_CATEGORY.get(marker.category, marker.category) == EXPECTED_CATEGORIES[marker.name]
        assert marker.now_tier == "optimal"

    output = tmp_path / "celldeep_aliases.pdf.out.pdf"
    template.render(record, _copy_for_render(), str(output))
    assert output.exists() and output.stat().st_size > 0
    with fitz.open(output) as rendered:
        rendered_text = "\n".join(page.get_text() for page in rendered)
    for marker_name in EXPECTED_CATEGORIES:
        assert marker_name in rendered_text


def test_status_flags_are_explicit_and_safe():
    assert pipeline.parse_provider_statuses("Patient is postmenopausal and starting BHRT.") == (True, None)
    assert pipeline.parse_provider_statuses("Patient is on testosterone replacement therapy.") == (None, True)
    assert pipeline.parse_provider_statuses("Testosterone is listed as a future goal.") == (None, None)
    assert pipeline.parse_provider_statuses("Patient is postmenopausal but not on BHRT.") == (None, None)


def test_status_aware_estradiol_and_lh_fsh_scoring():
    unknown, unknown_notice = pipeline.score_and_build_record({
        "name": "Unknown Status", "sex": "female",
        "provider_note_raw": "Testosterone is a future goal.",
        "markers": [
            {"name": "Estradiol", "now": 200, "disp_now": "200"},
            {"name": "LH", "now": 0.1, "disp_now": "0.1"},
            {"name": "FSH", "now": 0.1, "disp_now": "0.1"},
        ],
    })
    assert unknown.postmenopausal_bhrt is None
    assert unknown.on_trt is None
    assert {marker.name: marker.now_tier for marker in unknown.markers} == {
        "Estradiol": "optimal", "LH": "unscored", "FSH": "unscored",
    }
    assert {marker.name: marker.unscored_reason for marker in unknown.markers} == {
        "Estradiol": None, "LH": "missing_threshold", "FSH": "missing_threshold",
    }
    assert any("missing threshold for LH" in note for note in unknown_notice.other_notes)
    assert any("missing threshold for FSH" in note for note in unknown_notice.other_notes)

    confirmed, _ = pipeline.score_and_build_record({
        "name": "Confirmed Status", "sex": "female",
        "provider_note_raw": "Patient is postmenopausal and on BHRT; testosterone replacement therapy is active.",
        "markers": [
            {"name": "Estradiol", "now": 200, "disp_now": "200"},
            {"name": "LH", "now": 0.1, "disp_now": "0.1"},
            {"name": "FSH", "now": 0.1, "disp_now": "0.1"},
        ],
    })
    assert confirmed.postmenopausal_bhrt is True
    assert confirmed.on_trt is True
    assert {marker.name: marker.now_tier for marker in confirmed.markers} == {
        "Estradiol": "flag", "LH": "unscored", "FSH": "unscored",
    }

    for name in ("LH", "FSH"):
        suppressed = Marker(name=name, category="Hormones", unit="mIU/mL", kind="range",
                            disp_range="lab-specific reference range", lo=1.0, hi=10.0,
                            now=0.1, disp_now="0.1", suppress_low_on_trt=True)
        scoring.attach_scores(suppressed, on_trt=True)
        assert suppressed.now_tier == "optimal"

        unknown_status = Marker(name=name, category="Hormones", unit="mIU/mL", kind="range",
                                disp_range="lab-specific reference range", lo=1.0, hi=10.0,
                                now=0.1, disp_now="0.1", suppress_low_on_trt=True)
        scoring.attach_scores(unknown_status, on_trt=None)
        assert unknown_status.now_tier == "flag"

    output = Path("/tmp/lh_fsh_pending_configuration.pdf")
    template.render(unknown, _copy_for_render(), str(output))
    with fitz.open(output) as rendered:
        rendered_text = "\n".join(page.get_text() for page in rendered)
    assert "LH" in rendered_text
    assert "FSH" in rendered_text
    assert "0.1" in rendered_text
    assert "Reference range pending" in rendered_text


def test_lab_printed_range_fallback_for_lh_fsh(tmp_path):
    source = tmp_path / "access_style_lh_fsh.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(36, 36, 560, 780),
        "TEST LAB REPORT - SYNTHETIC\n"
        "LH                         0.1 mIU/mL       Reference Range: 1.0 - 10.0\n"
        "FSH                        0.1 mIU/mL       Reference Range: 1.0 - 10.0\n",
        fontsize=10,
        fontname="cour",
    )
    document.save(source)
    document.close()

    with fitz.open(source) as source_document:
        source_text = "\n".join(page.get_text() for page in source_document)
    extracted_markers = []
    for line in source_text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in {"LH", "FSH"}:
            continue
        extracted_markers.append({
            "name": parts[0], "now": float(parts[1]), "disp_now": parts[1],
            "lab_range_now": {"lo": float(parts[-3]), "hi": float(parts[-1]),
                               "display": f"{parts[-3]} - {parts[-1]}"},
        })

    extracted = {
        "name": "Printed Range Patient", "sex": "male", "provider_note_raw": "",
        "markers": extracted_markers,
    }
    record, notice = pipeline.score_and_build_record(extracted)
    assert not notice.unrecognized_markers
    assert {m.name: m.now_tier for m in record.markers} == {"LH": "flag", "FSH": "flag"}
    assert all(m.range_source == "lab" for m in record.markers)

    trt_extracted = dict(extracted, provider_note_raw="Patient is on testosterone replacement therapy.")
    trt_record, _ = pipeline.score_and_build_record(trt_extracted)
    assert {m.name: m.now_tier for m in trt_record.markers} == {"LH": "optimal", "FSH": "optimal"}

    output = tmp_path / "access_style_lh_fsh.out.pdf"
    template.render(trt_record, _copy_for_render(), str(output))
    with fitz.open(output) as rendered:
        rendered_text = "\n".join(page.get_text() for page in rendered)
    assert "LH" in rendered_text and "FSH" in rendered_text
    assert "Reference range pending" not in rendered_text
    assert "1.0 - 10.0" in rendered_text

    no_range_record, _ = pipeline.score_and_build_record({
        "name": "Absent Range Patient", "sex": "male", "provider_note_raw": "",
        "markers": [{"name": "LH", "now": 0.1, "disp_now": "0.1"}],
    })
    assert no_range_record.markers[0].now_tier == "unscored"
    assert no_range_record.markers[0].unscored_reason == "missing_threshold"

    multi_draw, _ = pipeline.score_and_build_record({
        "name": "Per Draw Range Patient", "sex": "male", "provider_note_raw": "",
        "markers": [{
            "name": "LH", "then": 5.0, "disp_then": "5.0", "now": 0.1, "disp_now": "0.1",
            "lab_range_then": {"lo": 4.0, "hi": 6.0, "display": "4.0 - 6.0"},
            "lab_range_now": {"lo": 1.0, "hi": 10.0, "display": "1.0 - 10.0"},
        }],
    })
    assert multi_draw.markers[0].then_tier == "optimal"
    assert multi_draw.markers[0].now_tier == "flag"

    configured, _ = pipeline.score_and_build_record({
        "name": "Configured Threshold Patient", "sex": "female", "provider_note_raw": "",
        "markers": [{"name": "TSH", "now": 2.0, "disp_now": "2.0",
                      "lab_range_now": {"lo": 100.0, "hi": 200.0, "display": "100 - 200"}}],
    })
    assert configured.markers[0].range_source == "celldeep"
    assert configured.markers[0].now_tier == "optimal"
