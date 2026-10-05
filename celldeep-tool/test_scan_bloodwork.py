"""Synthetic identity and mocked literal scan values; live tests use invented patient data only."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from anthropic import APITimeoutError

import pipeline
import scan_bloodwork as scan
from synthetic_fixtures import deterministic_fixtures as fx


GOLDEN = [
    ("IRON, TOTAL", "59", None, "50-180"), ("IRON BINDING CAPACITY", "295", None, "250-425"),
    ("% SATURATION", "20", None, "20-48"), ("FERRITIN", "32", "L", "38-380"),
    ("CHOLESTEROL, TOTAL", "163", None, "<200"), ("HDL CHOLESTEROL", "57", None, ">=40"),
    ("TRIGLYCERIDES", "90", None, "<150"), ("LDL-CHOLESTEROL", "88", None, None),
    ("CHOL/HDLC RATIO", "2.9", None, "<5.0"), ("NON HDL CHOLESTEROL", "106", None, "<130"),
    ("GLUCOSE", "82", None, "65-99"), ("UREA NITROGEN (BUN)", "18", None, "7-25"),
    ("CREATININE", "0.91", None, "0.60-1.29"), ("EGFR", "105", None, ">=60"),
    ("BUN/CREATININE RATIO", "SEE NOTE: Not Reported", None, "6-22"),
    ("SODIUM", "142", None, None), ("POTASSIUM", "4.5", None, None),
    ("CHLORIDE", "104", None, None), ("CARBON DIOXIDE", "27", None, None),
    ("CALCIUM", "9.3", None, None), ("PROTEIN, TOTAL", "6.7", None, None),
    ("ALBUMIN", "4.4", None, None), ("GLOBULIN", "2.3", None, None),
    ("ALBUMIN/GLOBULIN RATIO", "1.9", None, None), ("BILIRUBIN, TOTAL", "0.5", None, None),
    ("ALKALINE PHOSPHATASE", "62", None, None), ("AST", "25", None, None), ("ALT", "17", None, None),
    ("HEMOGLOBIN A1c", "4.9", None, "<5.7"), ("MAGNESIUM", "2.1", None, None),
    ("TSH", "1.19", None, "0.40-4.50"), ("T4, FREE", "1.5", None, None),
    ("T3, FREE", "3.5", None, None), ("ESTROGENS, TOTAL, IA", "208", None, "<=404"),
    ("WHITE BLOOD CELL COUNT", "4.4", None, None), ("RED BLOOD CELL COUNT", "5.46", None, None),
    ("HEMOGLOBIN", "17.6", "H", "13.2-17.1"), ("HEMATOCRIT", "52.3", "H", "39.4-51.1"),
    ("MCV", "95.8", None, None), ("MCH", "32.2", None, None), ("MCHC", "33.7", None, None),
    ("RDW", "13.1", None, None), ("PLATELET COUNT", "282", None, None), ("MPV", "9.5", None, None),
    ("ABSOLUTE NEUTROPHILS", "2957", None, None), ("ABSOLUTE LYMPHOCYTES", "827", "L", "850-3900"),
    ("ABSOLUTE MONOCYTES", "348", None, None), ("ABSOLUTE EOSINOPHILS", "229", None, None),
    ("ABSOLUTE BASOPHILS", "40", None, None), ("NEUTROPHILS", "67.2", None, None),
    ("LYMPHOCYTES", "18.8", None, None), ("MONOCYTES", "7.9", None, None),
    ("EOSINOPHILS", "5.2", None, None), ("BASOPHILS", "0.9", None, None),
    ("COLOR", "YELLOW", None, None), ("APPEARANCE", "CLEAR", None, None),
    ("SPECIFIC GRAVITY", "1.020", None, None), ("PH", "5.5", None, None),
    ("GLUCOSE", "NEGATIVE", None, None), ("BILIRUBIN", "NEGATIVE", None, None),
    ("KETONES", "2+", None, "NEGATIVE"), ("OCCULT BLOOD", "NEGATIVE", None, None),
    ("PROTEIN", "NEGATIVE", None, None), ("NITRITE", "NEGATIVE", None, None),
    ("LEUKOCYTE ESTERASE", "NEGATIVE", None, None), ("WBC", "NONE SEEN", None, None),
    ("RBC", "NONE SEEN", None, None), ("SQUAMOUS EPITHELIAL CELLS", "NONE SEEN", None, None),
    ("BACTERIA", "NONE SEEN", None, None), ("HYALINE CAST", "NONE SEEN", None, None),
    ("REFLEXIVE URINE CULTURE", "NO CULTURE INDICATED", None, None),
    ("VITAMIN B12", ">2000", "H", "200-1100"), ("FOLATE, SERUM", "6.9", None, None),
    ("C-REACTIVE PROTEIN", "<3.0", None, "<8.0"), ("CORTISOL, TOTAL", "13.1", None, None),
    ("DHEA SULFATE", "73", None, "61-442"), ("FSH", "<0.7", "L", "1.4-12.8"),
    ("INSULIN", "3.1", None, "<=18.4"), ("LH", "<0.2", "L", "1.5-9.3"),
    ("PROGESTERONE", "<0.5", None, None), ("PROLACTIN", "16.8", None, "2.0-18.0"),
    ("ESTRADIOL", "34", None, "<=39"), ("PSA, TOTAL", "0.53", None, "<=4.00"),
    ("TESTOSTERONE, TOTAL, MS", "1193", "H", "250-1100"),
    ("TESTOSTERONE, FREE", "210.0", "H", "35.0-155.0"),
    ("VITAMIN D,25-OH,TOTAL,IA", "73", None, "30-100"),
]


def payloads():
    rows = []
    for index, (name, value, flag, reference) in enumerate(GOLDEN):
        rows.append({
            "name": name, "result_text": value, "flag": flag, "reference_range": reference,
            "column": "out_of_range" if flag or name == "KETONES" else "in_range",
            "page": 2 if index < 54 else 3, "illegible": False,
            "section": "URINALYSIS" if 54 <= index <= 70 else "ROUTINE PANELS",
        })
    summary = [{"name": row["name"], "result_text": row["result_text"], "flag": row["flag"],
                "illegible": False} for row in rows if row["column"] == "out_of_range"]
    return [{
        "page": number, "specimen_id": "SYN-SCAN-001",
        "collected": "04/24/2026 09:15" if number == 3 else None,
        "patient_name": "Synthetic, Pat", "illegible": False,
        "rows": [row for row in rows if row["page"] == number],
        "out_of_range_summary": summary if number == 3 else None,
    } for number in (2, 3)]


class MockClient:
    def __init__(self, data):
        self.responses = iter(data)
        self.calls = []
        self.messages = self
        self.timeout = 240.0

    def create(self, **kwargs):
        self.calls.append(kwargs)
        data = next(self.responses)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(data))], stop_reason="end_turn")


def client_for(data):
    return MockClient([copy.deepcopy(page) for page in data for _ in (1, 2)])


def gate(data):
    return scan.gate_reads(data, {"Synthetic, Pat"}, pipeline._CELL_VALUE_RE,
                           pipeline._PRINTED_DATE_RE, pipeline._normalize_date_for_matching)


def test_golden_gates():
    accepted, notes = gate([[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()])
    assert notes == []
    assert len(accepted) == len(GOLDEN)
    assert [(row["name"], row["result_text"], row["flag"], row["reference_range"]) for row in accepted] == GOLDEN
    assert all(row["date"] == "04/24/2026" and row["source"] == "scan" for row in accepted)


@pytest.mark.parametrize("failure", ["flag", "disagreement", "grammar", "illegible", "section"])
def test_row_gates_exclude_without_guessing(failure):
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    if failure == "flag":
        for page in data[0]:
            page["rows"][0].update(result_text="49", flag=None)
    elif failure == "disagreement":
        data[0][1]["rows"][0]["result_text"] = "58"
    elif failure == "grammar":
        for page in data[0]:
            page["rows"][0]["result_text"] = "probably normal"
    elif failure == "illegible":
        for page in data[0]:
            page["rows"][0].update(result_text=None, illegible=True)
    else:
        for page in data[0]:
            page["rows"][0]["section"] = None
    accepted, notes = gate(data)
    assert len(accepted) == len(GOLDEN) - 1
    assert len(notes) == 1 and "excluded 'IRON, TOTAL'" in notes[0]
    assert all(row["name"] != "IRON, TOTAL" for row in accepted)


@pytest.mark.parametrize(("result", "reference", "flag", "valid"), [
    ("<1.4", "1.4-12.8", "L", True), ("<=1.4", "1.4-12.8", "L", False),
    (">200", "<=200", "H", True), (">=200", "<=200", "H", False),
    (">=200", "<200", "H", True), ("200", "<200", "H", True),
    ("200", "<=200", None, True), ("200H", "<=200", None, False),
])
def test_numeric_flag_boundaries(result, reference, flag, valid):
    assert scan._numeric_flag(
        {"result_text": result, "reference_range": reference, "flag": flag},
        pipeline._CELL_VALUE_RE,
    ) is valid


def test_same_name_in_different_sections_is_not_ambiguous():
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for reading in (0, 1):
        urine = next(row for row in data[1][reading]["rows"] if row["name"] == "GLUCOSE")
        data[1][reading]["rows"].remove(urine)
        data[0][reading]["rows"].append({**urine, "page": 2})
    accepted, notes = gate(data)
    assert notes == []
    assert len(accepted) == len(GOLDEN)
    assert {(row["section"], row["result_text"]) for row in accepted if row["name"] == "GLUCOSE"} == {
        ("ROUTINE PANELS", "82"), ("URINALYSIS", "NEGATIVE"),
    }


@pytest.mark.parametrize("failure", ["summary", "date", "conflicting_dates", "name", "metadata"])
def test_batch_gates_raise(failure):
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    if failure == "summary":
        for page in data[1]:
            page["out_of_range_summary"][0]["result_text"] = "33"
    elif failure == "date":
        for pair in data:
            for page in pair:
                page["collected"] = None
    elif failure == "conflicting_dates":
        for page in data[0]:
            page["collected"] = "04/23/2026"
    elif failure == "name":
        for page in data[0]:
            page["patient_name"] = "Different, Person"
    else:
        data[0][1]["specimen_id"] = "SYN-DIFFERENT"
    with pytest.raises(scan.ScanGateError, match="gate:"):
        gate(data)


@pytest.fixture
def mixed_pdf(tmp_path):
    digital = fx.section_preamble("SYN-SCAN-DIGITAL", "01/27/2026") + fx.header_line(100, ("01/07/2026",))
    for index, name in enumerate(("TSH", "Testosterone, Total", "Free Testosterone", "FSH", "LH",
                                 "PSA Total", "Ferritin", "Vitamin D", "Vitamin B12", "HbA1c", "Glucose",
                                 "Cortisol, Total", "Estradiol", "Creatinine")):
        digital += fx.row(130 + index * 14, name, "1.0", "2.0")
    return fx.write_lab_pdf(tmp_path / "mixed.pdf", [digital, [], []])


def test_mocked_merge_and_staff_only_provenance(mixed_pdf, tmp_path):
    client = client_for(payloads())
    extracted = pipeline.extract(str(mixed_pdf), [], None, client=client, audit_root=str(tmp_path))
    assert len(client.calls) == 4
    for call in client.calls:
        assert call["model"] == pipeline.MODEL
        assert call["output_config"]["format"]["schema"] == scan.SCAN_SCHEMA
    assert extracted["first_draw_date"] == "01/07/2026"
    assert extracted["latest_draw_date"] == "04/24/2026"
    record, notice = pipeline.score_and_build_record(extracted)
    expected = {"TSH": "1.19", "Testosterone, Total": "1193", "Free Testosterone": "210.0",
                "FSH": "<0.7", "LH": "<0.2", "PSA Total": "0.53", "Ferritin": "32",
                "Vitamin D": "73", "Vitamin B12": ">2000", "HbA1c": "4.9",
                "Glucose (fasting)": "82", "Cortisol, Total (AM)": "13.1",
                "Estradiol": "34", "Creatinine": "0.91"}
    values = {(item["name"], item["date_display"]): item for item in extracted["marker_occurrences"]}
    for name, value in expected.items():
        assert values[name, "04/24/2026"]["disp_value"] == value
    assert all("source" not in item and "scan" not in item["source_label"]
               for item in extracted["marker_occurrences"])
    assert "source=scan" in pipeline.format_review_notice(notice)
    assert "source=scan" not in json.dumps(pipeline.build_copy(record))
    audit = json.loads(next(tmp_path.rglob("source-rows.json")).read_text())
    assert sum(row["page"] in (2, 3) for row in audit) == len(GOLDEN)
    assert all("source" not in row for row in audit)
    unknown = extracted["unrecognized_markers"]
    assert any(row["raw_name"] == "GLUCOSE" and "URINALYSIS" in row["source_context"] for row in unknown)


def test_fatal_gate_uses_no_scan_rows(mixed_pdf, tmp_path):
    data = payloads()
    data[1]["out_of_range_summary"] = []
    extracted = pipeline.extract(str(mixed_pdf), [], None, client=client_for(data), audit_root=str(tmp_path))
    assert all(item["date_display"] != "04/24/2026" for item in extracted["marker_occurrences"])
    assert any("summary gate" in note and "ALL SCAN ROWS REJECTED" in note for note in extracted["other_notes"])


def test_scan_notes_are_staff_only_in_rendered_pdf(mixed_pdf, tmp_path, monkeypatch):
    data = payloads()
    data[0]["rows"][0]["result_text"] = "49"
    extracted = pipeline.extract(str(mixed_pdf), [], None, client=client_for(data), audit_root=str(tmp_path))
    record, notice = pipeline.score_and_build_record(extracted)
    notes_path = tmp_path / "staff-review.txt"
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(notes_path))
    pipeline._write_review_notes("Synthetic Patient", notice)
    assert "source=scan" in notes_path.read_text()
    assert "flag gate" in notes_path.read_text()
    out_path = tmp_path / "patient.pdf"
    pipeline.template.render(record, pipeline.build_copy(record), str(out_path))
    with fitz.open(out_path) as document:
        patient_text = "\n".join(page.get_text() for page in document)
    assert "source=scan" not in patient_text
    assert "flag gate" not in patient_text
    assert "excluded" not in patient_text


def test_api_error_handling_is_shared(mixed_pdf):
    client = MockClient([])
    client.create = lambda **kwargs: (_ for _ in ()).throw(APITimeoutError(request=None))
    with pytest.raises(pipeline.AnthropicAPIError, match="bloodwork scan page 2"):
        pipeline.extract(str(mixed_pdf), [], None, client=client)


@pytest.mark.parametrize("failure", ["json", "page", "schema", "truncated"])
def test_invalid_transcription_is_visibly_rejected(mixed_pdf, tmp_path, failure):
    client = MockClient([])
    data = payloads()[0]
    if failure == "page":
        data["page"] = 999
    if failure == "schema":
        del data["specimen_id"]
    client.create = lambda **kwargs: SimpleNamespace(
        content=[SimpleNamespace(text="not JSON" if failure == "json" else json.dumps(data))],
        stop_reason="max_tokens" if failure == "truncated" else "end_turn",
    )
    extracted = pipeline.extract(str(mixed_pdf), [], None, client=client, audit_root=str(tmp_path))
    assert extracted["latest_draw_date"] == "01/27/2026"
    assert any("gate:" in note and "ALL SCAN ROWS REJECTED" in note for note in extracted["other_notes"])


def test_patient_header_gate_is_mandatory():
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    with pytest.raises(scan.ScanGateError, match="digital patient name missing"):
        scan.gate_reads(pairs, set(), pipeline._CELL_VALUE_RE,
                        pipeline._PRINTED_DATE_RE, pipeline._normalize_date_for_matching)


def test_invalid_calendar_date_rejected():
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in data[1]:
        page["collected"] = "02/30/2026"
    with pytest.raises(scan.ScanGateError, match="date gate: invalid date"):
        gate(data)


SOURCE = Path(__file__).parent / "real_fixtures" / "bloodwork_fixture_01.pdf"


@pytest.mark.skipif(not SOURCE.is_file(), reason="Local real bloodwork fixture is absent")
def test_real_digital_rows_merge_with_mocked_golden(tmp_path):
    with fitz.open(SOURCE) as document:
        pages = list(document)
        names = scan.digital_patient_names(pages[:14], pipeline._group_lines, pipeline._page_words)
    assert len(names) == 1
    template = payloads()
    rows = [row for page in template for row in page["rows"]]
    summary = template[1]["out_of_range_summary"]
    data = []
    for number in range(15, 22):
        page_rows = []
        for index, row in enumerate(rows):
            if min(21, 15 + index // 13) == number:
                page_rows.append({**row, "page": number})
        data.append({
            "page": number, "specimen_id": "SYN-MOCKED-SCAN", "collected": "04/24/2026 09:15" if number == 21 else None,
            "patient_name": next(iter(names)), "illegible": False,
            "rows": page_rows, "out_of_range_summary": summary if number == 20 else None,
        })
    extracted = pipeline.extract(str(SOURCE), [], None, client=client_for(data), audit_root=str(tmp_path))
    assert extracted["first_draw_date"] == "01/07/2026"
    assert extracted["latest_draw_date"] == "04/24/2026"
    latest = {row["name"]: row for row in extracted["marker_occurrences"] if row["date_display"] == "04/24/2026"}
    assert latest["TSH"]["disp_value"] == "1.19"
    assert latest["Testosterone, Total"]["disp_value"] == "1193"
    assert latest["Glucose (fasting)"]["disp_value"] == "82"
    assert latest["Vitamin D"]["disp_value"] == "73"
    assert sum("accepted " in note for note in extracted["other_notes"]) == len(GOLDEN)
    assert not any("excluded" in note or "REJECTED" in note for note in extracted["other_notes"])
    audit = json.loads(next(tmp_path.rglob("source-rows.json")).read_text())
    assert all("source" not in row for row in audit)


@pytest.mark.skipif(os.environ.get("CELLDEEP_LIVE_VISION") != "1", reason="Live vision explicitly disabled")
def test_live_synthetic_scan(tmp_path):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("API key absent")
    document = fitz.open()
    digital = document.new_page()
    for x, y, text in fx.section_preamble("SYN-LIVE", "01/27/2026") + fx.header_line(100) + fx.row(130, "TSH", "1.35"):
        digital.insert_text((x, y), text, fontsize=9)
    image_doc = fitz.open()
    source = image_doc.new_page()
    for point, text in [
        ((40, 40), "Patient Name: Synthetic, Pat"), ((40, 60), "Collected: 04/24/2026"),
        ((40, 80), "SPECIMEN: SYN-LIVE-SCAN"), ((40, 100), "ROUTINE PANELS"),
        ((40, 120), "Test Name"), ((220, 120), "In Range"), ((320, 120), "Out of Range"),
        ((430, 120), "Reference Range"), ((40, 150), "TSH"), ((220, 150), "1.19"),
        ((430, 150), "0.40-4.50"), ((40, 200), "LIST OF RESULTS PRINTED IN THE OUT OF RANGE COLUMN"),
        ((40, 220), "None"),
    ]:
        source.insert_text(point, text, fontsize=9)
    document.new_page().insert_image(source.rect, pixmap=source.get_pixmap(dpi=200))
    path = tmp_path / "synthetic-live.pdf"
    document.save(path)
    document.close()
    image_doc.close()
    extracted = pipeline.extract(str(path), [], None, audit_root=str(tmp_path))
    assert any(row["name"] == "TSH" and row["disp_value"] == "1.19"
               and row["date_display"] == "04/24/2026" for row in extracted["marker_occurrences"])
