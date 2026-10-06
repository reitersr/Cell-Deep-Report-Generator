"""Full-panel column headers name a date only when every result in that column is from that date.
Regression: a marker whose only result was older than the newest draw sat in the earlier column under
the document's first-draw date, a date none of its values came from. Synthetic data only."""

import re

import pipeline
import template
from synthetic_fixtures import deterministic_fixtures as fx

HEADER = re.compile(r'bio-table-header"><div>Marker</div><div>Reference Range</div><div>(.*?)</div><div>(.*?)</div>')


def _record(tmp_path, pages):
    labs = fx.write_lab_pdf(tmp_path / "labs.pdf", pages)
    extracted = pipeline.extract(str(labs), [], None, patient_name=fx.PATIENT, audit_root=str(tmp_path))
    record, _ = pipeline.score_and_build_record({**extracted, "sex": "male"})
    return record


def _headers(record):
    return HEADER.search(template.bio_group("Metabolic", record, {}, True)).groups()


def _marker(record, name):
    return next(m for m in record.markers if m.name == name)


def test_single_older_result_is_not_labelled_with_the_first_draw_date(tmp_path):
    first = fx.section_preamble("SYN-FEB", "02/09/2026") + fx.header_line(100) + fx.row(130, "TSH", "2.2")
    middle = (fx.section_preamble("SYN-MAY", "05/11/2026") + fx.header_line(100)
             + fx.row(130, "Insulin", "4.2", units="uIU/mL") + fx.row(144, "TSH", "1.9"))
    last = fx.section_preamble("SYN-JUN", "06/15/2026") + fx.header_line(100) + fx.row(130, "Ferritin", "88")
    record = _record(tmp_path, [first, middle, last])
    insulin = _marker(record, "Fasting Insulin")
    assert (insulin.disp_then, insulin.then_date_display) == ("4.2", "05/11/2026")  # in the earlier column
    assert record.first_draw_date == "02/09/2026"
    earlier, latest = _headers(record)
    assert earlier == "Earlier" and "February 9" not in earlier  # the column mixes results from two draws
    assert latest == "Latest"  # TSH's latest is one draw, Ferritin's another
    row = template.bio_row_tr(insulin, {})
    assert "May 11, 2026" in row and "February 9, 2026" not in row  # each value keeps its own date


def test_censored_earlier_value_stays_under_its_own_date(tmp_path):
    page = (fx.section_preamble("SYN-INS", "05/11/2026") + fx.header_line(100, ("02/09/2026",))
            + fx.row(130, "Insulin", "4.2", "<2", units="uIU/mL") + fx.row(144, "TSH", "1.9", "2.2"))
    record = _record(tmp_path, [page])
    insulin = _marker(record, "Fasting Insulin")
    assert (insulin.disp_then, insulin.then_date_display) == ("<2", "02/09/2026")
    assert (insulin.disp_now, insulin.now_date_display) == ("4.2", "05/11/2026")
    assert _headers(record) == ("February 9, 2026", "May 11, 2026")  # one date per column: unchanged


def test_single_draw_has_no_earlier_header(tmp_path):
    page = fx.section_preamble("SYN-ONE", "05/11/2026") + fx.header_line(100) + fx.row(130, "Insulin", "4.2")
    assert _headers(_record(tmp_path, [page])) == ("", "May 11, 2026")
