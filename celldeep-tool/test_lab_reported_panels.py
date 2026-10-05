"""CBC, electrolytes, liver and urinalysis panels: lab range and lab flag, never a CellDeep score; every
lab-flagged result reaches the patient report, including results from a middle draw."""

import fitz

import pipeline
import template
from synthetic_fixtures import deterministic_fixtures as fx
from synthetic_fixtures import layouts

PANEL = [
    ("TSH", "1.90", None, "0.40-4.50"), ("WHITE BLOOD CELL COUNT", "6.1", None, "3.8-10.8"), ("HEMOGLOBIN", None, "17.9 H", "13.2-17.1"),
    ("PLATELET COUNT", None, "130 L", "140-400"),
    ("SODIUM", "140", None, "135-146"), ("POTASSIUM", None, "5.6 H", "3.5-5.3"), ("CHLORIDE", "103", None, "98-110"),
    ("CARBON DIOXIDE", "26", None, "20-32"), ("ANION GAP", "11", None, "3-16"),
    ("AST", "24", None, "10-40"), ("ALT", None, "61 H", "9-46"), ("ALKALINE PHOSPHATASE", "70", None, "36-130"),
    ("GGT", None, "88 H", "3-70"), ("BILIRUBIN, TOTAL", "0.6", None, "0.2-1.2"),
    "URINALYSIS",
    ("PROTEIN", None, "POSITIVE", "NEGATIVE"), ("GLUCOSE", "NEGATIVE", None, "NEGATIVE"),
]


def _render(tmp_path, pages):
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", pages)
    extracted = pipeline.extract(str(labs), [], None, patient_name=fx.PATIENT, audit_root=str(tmp_path))
    record, notice = pipeline.score_and_build_record({**extracted, "sex": "male"})
    out = tmp_path / "report.pdf"
    template.render(record, pipeline.build_copy(record), str(out))
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return extracted, record, notice, text, out.with_suffix(".html").read_text(encoding="utf-8")


def test_panels_show_lab_range_and_lab_flag_without_a_celldeep_score(tmp_path):
    extracted, record, notice, text, html = _render(tmp_path, [layouts._quest_page("03/02/2026", PANEL, "SYN-PANEL")])
    shown = {item.name: item for item in record.lab_reported}
    for name in ("White Blood Cell Count", "Hemoglobin", "Platelet Count", "Sodium", "Potassium", "Chloride",
                 "Carbon Dioxide", "Anion Gap", "AST", "ALT", "Alkaline Phosphatase", "GGT", "Bilirubin, Total",
                 "Urinalysis — Protein", "Urinalysis — Glucose"):
        assert name in shown, name
        assert name in text
    flags = {name: shown[name].results[-1]["lab_flag"] for name in shown}
    assert flags["Hemoglobin"] == "H" and flags["Platelet Count"] == "L" and flags["GGT"] == "H"
    for expected in ("17.9 H", "13.2-17.1", "130 L", "140-400", "5.6 H", "88 H", "3-70", "POSITIVE"):
        assert expected in text, expected
    section = html[html.index('class="lab-reported"'):]
    assert not any(word in section for word in ("Optimal", "Moderate", "Flagged", "bio-tierchip"))
    assert {m.name for m in record.markers}.isdisjoint(shown)
    assert not any("COVERAGE GAP" in note for note in notice.other_notes)


def test_a_flagged_result_from_a_middle_draw_is_shown(tmp_path):
    page = (fx.section_preamble("SYN-3DRAW", "03/02/2026") + fx.header_line(100, ("11/20/2025", "08/01/2025"))
            + fx.row(130, "hs-CRP", "0.8", "4.0H", "0.9", units="mg/L", lab_range="0.0-3.0")
            + fx.row(144, "Hemoglobin", "15.0", "17.9H", "15.2", units="g/dL", lab_range="13.2-17.1"))
    _, record, _, text, _ = _render(tmp_path, [page])
    assert "Also on file: 4.0 (lab flag High) on November 20, 2025" in text
    assert "Also on file: 17.9 (lab flag High) on November 20, 2025" in text
    crp = next(m for m in record.markers if m.name == "hs-CRP")
    assert crp.full_history == [{"date_display": "11/20/2025", "value": 4.0, "disp_value": "4.0", "lab_flag": "H"}]
