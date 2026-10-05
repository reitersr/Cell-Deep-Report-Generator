"""Local-only regression against the git-ignored source PDF; no patient data embedded here."""

from pathlib import Path

import fitz
import pytest

import pipeline


SOURCE = Path(__file__).parent / "real_fixtures" / "bloodwork_fixture_01.pdf"


def _printed_cell_bands(page):
    """Independent source count: all horizontal rules, then separated words in dated columns."""
    words = pipeline._page_words(page)
    lines = pipeline._group_lines(words, pipeline._LINE_TOLERANCE_PT)
    headers = []
    for index, line in enumerate(lines):
        header = pipeline._bloodwork_header(line, lines[max(0, index - 3):index])
        if header:
            headers.append((max(w[3] for w in line), header))
    bands = []
    for index, (top, header) in enumerate(headers):
        bottom = headers[index + 1][0] if index + 1 < len(headers) else page.rect.height
        name_column = header["columns"][0]
        values = [c for c in header["columns"] if c["kind"] in ("current", "historical")]
        rules = []
        for drawing in page.get_drawings():
            for item in drawing["items"]:
                if item[0] == "l":
                    start, end = sorted(item[1:3], key=lambda point: point.x)
                    if abs(start.y - end.y) < 0.5 and abs(start.x - name_column["x0"]) < 8 \
                            and end.x > name_column["x1"]:
                        rules.append((round(start.y, 2), end.x))
        name_right = min(x for _, x in rules if x < min(c["x0"] for c in values))
        edges = sorted({y for y, _ in rules})
        for lo, hi in zip(edges, edges[1:]):
            if lo < top or hi > bottom:
                continue
            band = [w for w in words if lo <= (w[1] + w[3]) / 2 < hi]
            if not any(w[2] <= name_right for w in band):
                continue
            if any(w[0] < name_right < w[2] for w in band):
                continue
            if any(0 <= right[0] - left[2] < pipeline._PHRASE_GAP_PT
                   for line in pipeline._group_lines(band, pipeline._LINE_TOLERANCE_PT)
                   for left, right in zip(line, line[1:])
                   if left[2] <= name_right < right[2]):
                continue
            if any(c["x0"] - pipeline._DATE_COLUMN_PAD_PT <= (w[0] + w[2]) / 2
                   <= c["x1"] + pipeline._DATE_COLUMN_PAD_PT
                   for w in band if w[2] > name_right for c in values):
                bands.append((lo, hi))
    return bands


@pytest.mark.skipif(not SOURCE.is_file(), reason="Local real bloodwork fixture is absent")
def test_real_bloodwork_page_local_headers_and_unreadable_scans(tmp_path, capsys):
    with fitz.open(SOURCE) as document:
        pages = list(document)
        results = pipeline._parse_bloodwork_tables(pages[:8])
        assert results[0]
        assert pipeline._parse_bloodwork_tables(pages[:14]) == results

        for page in pages[8:14]:
            lines = pipeline._group_lines(pipeline._page_words(page), pipeline._LINE_TOLERANCE_PT)
            assert all(pipeline._bloodwork_header(words, lines[max(0, i - 3):i]) is None
                       for i, words in enumerate(lines))
            assert pipeline._parse_bloodwork_tables(pages[:8] + [page]) == results

        for number in range(15, 22):
            assert pipeline._page_words(pages[number - 1]) == []
        warnings = []
        assert pipeline._parse_bloodwork_tables(pages, review_notes=warnings) == results
        assert warnings == [
            "Lab PDF pages 15, 16, 17, 18, 19, 20, 21: no readable text, "
            "OCR not supported, manual review required"]
    assert warnings[0] in capsys.readouterr().out
    extracted = pipeline.extract(str(SOURCE), [], None, audit_root=str(tmp_path))
    assert extracted["marker_occurrences"] == results[0]
    assert extracted["unrecognized_markers"] == results[1]
    _, notice = pipeline.score_and_build_record(extracted)
    assert warnings[0] in pipeline.format_review_notice(notice)


@pytest.mark.skipif(not SOURCE.is_file(), reason="Local real bloodwork fixture is absent")
def test_real_bloodwork_golden_values_and_sections():
    audit = []
    with fitz.open(SOURCE) as document:
        results, unknown = pipeline._parse_bloodwork_tables(list(document)[:8], row_audit=audit)
        source_bands = {page.number + 1: _printed_cell_bands(page) for page in list(document)[:8]}
    found = {(item["name"], item["date_display"]): item for item in results}
    current, historical = "01/27/2026", "01/07/2026"
    for name, now, then in [
        ("hs-CRP", "0.3", ">20.0"),
        ("Estradiol", "62", "12"),
        ("CoQ10", "1.82", "0.85"),
        ("Free Testosterone", "310.1", "66.3"),
        ("LDL-P (particle count)", "1228", "695"),
        ("Cortisol, Total (AM)", "14.7", "4.8"),
        ("Ferritin", "122", "216"),
        ("Uric Acid", "6.4", "8.2"),
        ("TSH", "1.35", "1.80"),
        ("Phosphorus", "3.7", "2.3"),
        ("Testosterone, Total", "1846", "506"),
        ("Vitamin D", "50.9", "58.8"),
        ("Troponin T, HS", "7", "6"),
        ("ADMA", "97", "86"),
        ("SDMA", "88", "70"),
        ("TMAO", "29.0", "3.0"),
        ("C-Peptide", "0.25", "0.50"),
        ("Total T4", "7.6", "7.6"),
        ("Total T3", "85", "111"),
    ]:
        assert found[(name, current)]["disp_value"] == now
        assert found[(name, historical)]["disp_value"] == then
    assert found[("Myeloperoxidase", current)]["status"] == "not_performed"
    assert found[("Myeloperoxidase", historical)]["value"] == 543
    assert all("URINALYSIS" not in item["source_label"]
               for item in results if item["name"] in ("Glucose (fasting)", "Uric Acid"))
    assert [(item["disp_value"], item["date_display"]) for item in results if item["name"] == "Uric Acid"] == [
        ("6.4", current), ("8.2", historical)]
    raw = {item["raw_name"]: item for item in unknown}
    for name, now, then in [
        ("LDL Size", "21.4", "21.1"),
        ("HDL Size", "10.1", "9.5"),
        ("ApoB/ApoA1 Ratio", "0.48", "0.47"),
        ("BUN/Creatinine Ratio", "Not Applicable", "Not Applicable"),
        ("OmegaCheck\u00ae (Whole Blood: EPA+DPA+DHA)", "8.0", "5.3"),
    ]:
        assert [(cell["date_display"], cell["disp_value"]) for cell in raw[name]["cells"]] == [
            (current, now), (historical, then)]
    assert [(item["date_display"], item["disp_value"]) for item in results
            if item["name"] == "LDL Cholesterol"] == [(current, "89"), (historical, "53")]
    assert [(item["date_display"], item["disp_value"]) for item in results
            if item["name"] == "HDL Cholesterol"] == [(current, "68"), (historical, "50")]
    assert [(item["date_display"], item["disp_value"]) for item in results
            if item["name"] == "Apolipoprotein B"] == [(current, "78"), (historical, "63")]
    assert all(cell["value"] is None and cell["status"] == "Not Applicable"
               for cell in raw["BUN/Creatinine Ratio"]["cells"])
    assert all(item["name"] != "Omega-3 Index" for item in results)
    urine = next(item for item in unknown
                 if item["raw_name"] == "Glucose" and "URINALYSIS" in item["source_context"])
    assert urine["cells"][0]["status"] == "not_performed"
    assert "Uric Acid Crystals" in raw
    sars = raw["SARS COV2,TOT,SPKE,SEMIQN"]
    assert sars["cells"][0]["date_display"] == current
    assert sars["cells"][0]["disp_value"] == ">2500.0"
    assert not sars["cells"][1]["present"]
    assert all("vaccinees" not in item["raw_name"] for item in unknown)
    assert len(audit) == sum(len(row["occurrences"]) // 2 + len(row["unrecognized"]) for row in audit)
    assert len(results) == len({(row["name"], row["date_display"]) for row in results})
    for date in (current, historical):
        assert found[("Glucose (fasting)", date)]["source_label"].endswith("pages 2, 3")
    assert len({(row["page"], row["y"]) for row in audit}) == len(audit)
    for page, bands in source_bands.items():
        rows = [row for row in audit if row["page"] == page]
        assert len(rows) == len(bands)
        assert all(sum(lo <= row["y"] < hi for row in rows) == 1 for lo, hi in bands)


@pytest.mark.skipif(not SOURCE.is_file(), reason="Local real bloodwork fixture is absent")
def test_real_bloodwork_conflicting_duplicate_names_both_pages(monkeypatch):
    original = pipeline._parse_table_region

    def altered(page, lines, header, section, occurrences, unrecognized, *args):
        start = len(occurrences)
        original(page, lines, header, section, occurrences, unrecognized, *args)
        if page.number == 2:
            for occurrence in occurrences[start:]:
                if occurrence["name"] == "Glucose (fasting)" and occurrence["disp_value"] == "74":
                    occurrence.update(value=75.0, disp_value="75")

    monkeypatch.setattr(pipeline, "_parse_table_region", altered)
    with fitz.open(SOURCE) as document:
        with pytest.raises(pipeline.BloodworkParseError, match=r"Glucose .*pages 2 and 3"):
            pipeline._parse_bloodwork_tables(list(document)[:8])
