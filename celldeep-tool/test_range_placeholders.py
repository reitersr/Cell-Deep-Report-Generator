"""No placeholder range text: the lab-printed range ("lab range ...") or "no range printed"."""

import fitz

import pipeline
import template
from synthetic_fixtures import deterministic_fixtures as fx

PLACEHOLDERS = ("Reference range pending", "lab-specific reference range", "target only", "pending")


def _report(tmp_path, rows, sex="male"):
    page = fx.section_preamble("SYN-RANGE", "04/14/2026") + fx.header_line(100)
    for index, row in enumerate(rows):
        page += fx.row(130 + index * 14, *row)
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", [page])
    extracted = pipeline.extract(str(labs), [], None, patient_name=fx.PATIENT, audit_root=str(tmp_path))
    record, _ = pipeline.score_and_build_record({**extracted, "sex": sex})
    out = tmp_path / "report.pdf"
    template.render(record, pipeline.build_copy(record), str(out))
    html = out.with_suffix(".html").read_text(encoding="utf-8")
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return record, html, text


def test_missing_threshold_shows_the_printed_lab_range(tmp_path):
    record, html, text = _report(tmp_path, [("LH", "4.1", None, None, "mIU/mL", "1.5-9.3"),
                                            ("TSH", "1.9", None, None, "uIU/mL")])
    lh = next(m for m in record.markers if m.name == "LH")
    assert lh.disp_range == "lab range 1.5-9.3" and lh.range_source == "lab"
    assert "lab range 1.5-9.3" in text
    for placeholder in PLACEHOLDERS:
        assert placeholder not in text


def test_missing_threshold_without_a_printed_range_says_so(tmp_path):
    record, html, text = _report(tmp_path, [("DHEA-S", "250"), ("Testosterone, Total", "35")], sex="female")
    for name in ("DHEA-S", "Testosterone, Total"):
        marker = next(m for m in record.markers if m.name == name)
        assert marker.disp_range == "no range printed" and marker.now_pct is None and marker.unscored_reason == "missing_threshold"
    assert "no range printed" in text and "Not scored, no range printed" in text
    for placeholder in PLACEHOLDERS:
        assert placeholder not in text


def test_lab_reported_result_without_a_range_says_no_range_printed(tmp_path):
    _, html, _ = _report(tmp_path, [("Sodium", "140"), ("TSH", "1.9")])
    section = html[html.index('class="lab-reported"'):]
    assert "no range printed" in section
