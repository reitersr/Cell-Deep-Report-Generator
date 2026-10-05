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


def _assert_structured_schema(schema):
    supported = {"type", "enum", "anyOf", "properties", "required", "additionalProperties", "items"}
    assert set(schema) <= supported, f"Unsupported keywords: {set(schema) - supported}"
    types = schema.get("type", [])
    types = types if isinstance(types, list) else [types]
    validators = {
        "string": lambda value: isinstance(value, str),
        "null": lambda value: value is None,
        "boolean": lambda value: isinstance(value, bool),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "array": lambda value: isinstance(value, list),
        "object": lambda value: isinstance(value, dict),
    }
    assert all(kind in validators for kind in types)
    for value in schema.get("enum", []):
        assert types and all(validators[kind](value) for kind in types), (
            f"Enum value {value!r} does not match every declared type {types}")
    if "object" in types:
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert len(schema["required"]) == len(schema["properties"])
    for child in schema.get("properties", {}).values():
        _assert_structured_schema(child)
    for branch in schema.get("anyOf", []):
        _assert_structured_schema(branch)
    if "items" in schema:
        _assert_structured_schema(schema["items"])


def test_scan_schema_structured_output_limits():
    _assert_structured_schema(scan.SCAN_SCHEMA)


@pytest.mark.parametrize("invalid", [
    {"type": ["string", "null"], "enum": ["H", "L"]},
    {"type": ["string", "null"], "enum": ["H", "L", None]},
    {"type": "boolean", "enum": ["false"]},
    {"anyOf": [{"type": "string", "enum": ["in_range", None]}, {"type": "null"}]},
])
def test_schema_walker_rejects_enum_type_mismatches(invalid):
    with pytest.raises(AssertionError, match="Enum value"):
        _assert_structured_schema(invalid)


@pytest.mark.parametrize(("value", "valid"), [("H", True), ("L", True), (None, True), ("X", False), (False, False)])
def test_nullable_enum_response_validation(value, valid):
    if valid:
        scan._validate(value, scan.SCAN_SCHEMA["properties"]["rows"]["items"]["properties"]["flag"])
    else:
        with pytest.raises(scan.ScanGateError, match="schema gate:"):
            scan._validate(value, scan.SCAN_SCHEMA["properties"]["rows"]["items"]["properties"]["flag"])


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
            "page": 2 if index < 54 else 3, "illegible": False, "lab_code": None,
            "section": "URINALYSIS" if 54 <= index <= 70 else "ROUTINE PANELS",
        })
    summary = [{"name": row["name"], "result_text": row["result_text"], "flag": row["flag"],
                "illegible": False} for row in rows if row["column"] == "out_of_range"]
    return [{
        "page": number, "specimen_id": "SYN-SCAN-001",
        "footer": f"SPECIMEN: SYN-SCAN-001 PAGE {number - 1} OF 2",
        "collected": "04/14/2026 09:15" if number == 3 else None,
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
    assert all(row["date"] == "04/14/2026" and row["source"] == "scan" for row in accepted)


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


def test_rowless_page_without_identity_does_not_reject_batch():
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    continuation = {
        "page": 4, "specimen_id": None, "collected": None, "patient_name": None,
        "footer": None,
        "illegible": True, "rows": [], "out_of_range_summary": None,
    }
    data.append([copy.deepcopy(continuation), copy.deepcopy(continuation)])
    accepted, notes = gate(data)
    assert len(accepted) == len(GOLDEN)
    assert len(notes) == 1 and "rowless page without specimen identity" in notes[0]


@pytest.mark.parametrize("reading", [0, 1, None])
def test_rowful_page_without_verified_footer_is_excluded(reading):
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in data[0]:
        page["specimen_id"] = None
        page["footer"] = None
    if reading is not None:
        data[0][1 - reading]["rows"] = []
    accepted, notes = gate(data)
    assert len(accepted) == len(payloads()[1]["rows"])
    assert all(row["page"] == 3 for row in accepted)
    assert any("page 2" in note and "no verified adjacent" in note for note in notes)


def test_rowless_specimen_pages_supply_identity_and_split_summary():
    template = payloads()
    summary = template[1]["out_of_range_summary"]
    template[1]["out_of_range_summary"] = None
    for page in template:
        page["patient_name"] = None
        page["collected"] = None
    for number, block in [(4, summary[:5]), (5, summary[5:])]:
        template.append({
            "page": number, "specimen_id": "SYN-SCAN-001", "collected": "04/14/2026",
            "footer": None,
            "patient_name": "Synthetic, Pat", "illegible": False,
            "rows": [], "out_of_range_summary": block,
        })
    accepted, notes = gate([[copy.deepcopy(page), copy.deepcopy(page)] for page in template])
    assert len(accepted) == len(GOLDEN)
    assert all(row["date"] == "04/14/2026" for row in accepted)
    assert notes == []


def test_rowless_summary_without_specimen_is_excluded():
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in data[1]:
        page["rows"] = []
        page["specimen_id"] = None
    accepted, notes = gate(data)
    assert accepted == []  # Only this excluded page carried the specimen date.
    assert any("rowless page without specimen identity" in note for note in notes)


def test_rowless_page_preserves_metadata_and_patient_checks():
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in data[1]:
        page.update(rows=[], specimen_id=None, out_of_range_summary=None,
                    patient_name="Different, Person")
    with pytest.raises(scan.ScanGateError, match="patient gate: name mismatch"):
        gate(data)
    data[1][1]["patient_name"] = None
    with pytest.raises(scan.ScanGateError, match="patient gate: name mismatch"):
        gate(data)


@pytest.mark.parametrize("failure", ["conflicting_dates", "name"])
def test_batch_gates_raise(failure):
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    if failure == "conflicting_dates":
        for page in data[0]:
            page["collected"] = "04/23/2026"
    elif failure == "name":
        for page in data[0]:
            page["patient_name"] = "Different, Person"
    with pytest.raises(scan.ScanGateError, match="gate:"):
        gate(data)


def _footer_pages():
    template = payloads()
    middle = copy.deepcopy(template[0])
    middle.update(page=3, specimen_id=None, footer="PAGE 2 OF 3")
    middle["rows"] = [middle["rows"].pop(0)]
    template[0]["rows"] = template[0]["rows"][1:]
    template[0]["footer"] = "SPECIMEN: SYN-SCAN-001 PAGE 1 OF 3"
    template[1].update(page=4, footer="SPECIMEN: SYN-SCAN-001 PAGE 3 OF 3")
    pages = [template[0], middle, template[1]]
    for page in pages:
        for row in page["rows"]:
            row["page"] = page["page"]
    return pages


def test_missing_specimen_between_matching_footer_pages_is_inherited(capsys):
    accepted, notes = gate([[copy.deepcopy(page), copy.deepcopy(page)] for page in _footer_pages()])
    assert len(accepted) == len(GOLDEN)
    assert notes == []
    assert next(row for row in accepted if row["page"] == 3)["specimen_id"] == "SYN-SCAN-001"
    assert 'page=3 rows_read=1/1 specimen_id="SYN-SCAN-001"' in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["no_sequence", "wrong_sequence", "no_footer_id", "disagreement"])
def test_footer_inheritance_requires_verified_sequence(failure):
    pages = _footer_pages()
    if failure == "no_sequence":
        pages[1]["footer"] = None
    elif failure == "wrong_sequence":
        pages[1]["footer"] = "PAGE 3 OF 3"
    elif failure == "no_footer_id":
        pages[0]["footer"] = "PAGE 1 OF 3"
        pages[2]["footer"] = "PAGE 3 OF 3"
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in pages]
    if failure == "disagreement":
        pairs[1][1]["footer"] = "PAGE 1 OF 3"
    accepted, notes = gate(pairs)
    assert len(accepted) == len(GOLDEN) - 1
    assert all(row["page"] != 3 for row in accepted)
    assert any("page 3" in note and "gate:" in note for note in notes)


@pytest.mark.parametrize("reading", [0, 1, None])
def test_summary_mismatch_excludes_only_disagreeing_row(reading, capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for index in (0, 1) if reading is None else (reading,):
        pairs[1][index]["out_of_range_summary"][0]["result_text"] = "PRIVATE-RESULT"
    accepted, notes = gate(pairs)
    assert len(accepted) == len(GOLDEN) - 1
    assert all(row["name"] != "FERRITIN" for row in accepted)
    assert notes == [
        "source=scan page 2 excluded 'FERRITIN': summary gate: out-of-range row disagrees with printed summary"]
    lines = capsys.readouterr().out.splitlines()
    assert notes[0] in lines
    assert sum("verdict=kept" in line for line in lines) == 2
    assert not any("Synthetic" in line or "PRIVATE-RESULT" in line or "result_text=" in line
                   for line in lines)


def test_page_log_format_and_privacy(capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    gate(pairs)
    assert capsys.readouterr().out.splitlines() == [
        f'source=scan page={number} rows_read={count}/{count} specimen_id="SYN-SCAN-001" '
        'name_matched=yes date="04/14/2026" verdict=kept reason="passed"'
        for number, count in ((2, 54), (3, len(GOLDEN) - 54))
    ]


def test_fatal_conflict_logs_every_page_and_row(capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in pairs[0]:
        page["collected"] = "04/23/2026"
    with pytest.raises(scan.ScanGateError, match="conflicting Collected dates"):
        gate(pairs)
    lines = capsys.readouterr().out.splitlines()
    assert sum("verdict=excluded" in line for line in lines) == 2
    assert sum(" excluded " in line for line in lines) == len(GOLDEN)
    assert not any("Synthetic" in line for line in lines)


def test_metadata_failure_is_page_local():
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    pairs[0][1]["specimen_id"] = "SYN-DIFFERENT"
    accepted, notes = gate(pairs)
    assert len(accepted) == len(payloads()[1]["rows"])
    assert any("metadata agreement gate: specimen_id differs" in note for note in notes)


def test_missing_date_is_specimen_local():
    pages = payloads()
    pages[0].update(specimen_id="SYN-NO-DATE", footer=None)
    accepted, notes = gate([[copy.deepcopy(page), copy.deepcopy(page)] for page in pages])
    assert len(accepted) == len(pages[1]["rows"])
    assert any("missing Collected date for specimen" in note for note in notes)


def test_invalid_date_is_page_local():
    pages = _footer_pages()
    pages[1]["collected"] = "02/30/2026"
    accepted, notes = gate([[copy.deepcopy(page), copy.deepcopy(page)] for page in pages])
    assert len(accepted) == len(GOLDEN) - 1
    assert any("page 3: date gate: invalid date" in note for note in notes)


def test_partial_transcription_still_contributes_contradiction_evidence(capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    partial = copy.deepcopy(pairs[0][0])
    partial["collected"] = "04/23/2026"
    with pytest.raises(scan.ScanGateError, match="conflicting Collected dates"):
        scan.gate_reads(pairs[1:], {"Synthetic, Pat"}, pipeline._CELL_VALUE_RE,
                        pipeline._PRINTED_DATE_RE, pipeline._normalize_date_for_matching,
                        [(2, [partial], "schema gate: invalid JSON on page 2")])
    output = capsys.readouterr().out
    assert 'page=2 rows_read=54/null specimen_id="SYN-SCAN-001"' in output
    assert output.count("verdict=excluded") == 2


@pytest.fixture
def mixed_pdf(tmp_path):
    digital = fx.section_preamble("SYN-SCAN-DIGITAL", "02/03/2026") + fx.header_line(100, ("01/13/2026",))
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
    assert extracted["first_draw_date"] == "01/13/2026"
    assert extracted["latest_draw_date"] == "04/14/2026"
    record, notice = pipeline.score_and_build_record(extracted)
    expected = {"TSH": "1.19", "Testosterone, Total": "1193", "Free Testosterone": "210.0",
                "FSH": "<0.7", "LH": "<0.2", "PSA Total": "0.53", "Ferritin": "32",
                "Vitamin D": "73", "Vitamin B12": ">2000", "HbA1c": "4.9",
                "Glucose (fasting)": "82", "Cortisol, Total (AM)": "13.1",
                "Estradiol": "34", "Creatinine": "0.91"}
    values = {(item["name"], item["date_display"]): item for item in extracted["marker_occurrences"]}
    for name, value in expected.items():
        assert values[name, "04/14/2026"]["disp_value"] == value
    assert all("source" not in item and "scan" not in item["source_label"]
               for item in extracted["marker_occurrences"])
    assert "source=scan" in pipeline.format_review_notice(notice)
    assert "source=scan" not in json.dumps(pipeline.build_copy(record))
    audit = json.loads(next(tmp_path.rglob("source-rows.json")).read_text())
    assert sum(row["page"] in (2, 3) for row in audit) == len(GOLDEN)
    assert all("source" not in row for row in audit)
    unknown = extracted["unrecognized_markers"]
    assert any(row["raw_name"] == "GLUCOSE" and "URINALYSIS" in row["source_context"] for row in unknown)


def test_bad_transcription_does_not_prevent_next_page(mixed_pdf, tmp_path, capsys):
    page = payloads()[1]
    client = MockClient([None, page, page])
    extracted = pipeline.extract(str(mixed_pdf), [], None, client=client, audit_root=str(tmp_path))
    assert extracted["latest_draw_date"] == "04/14/2026"
    assert any("page 2: schema gate" in note for note in extracted["other_notes"])
    lines = capsys.readouterr().out.splitlines()
    assert any("page=2 rows_read=null/null" in line and "verdict=excluded" in line for line in lines)
    assert any("page=3" in line and "verdict=kept" in line for line in lines)


def test_summary_mismatch_preserves_other_scan_rows(mixed_pdf, tmp_path):
    data = payloads()
    data[1]["out_of_range_summary"] = []
    extracted = pipeline.extract(str(mixed_pdf), [], None, client=client_for(data), audit_root=str(tmp_path))
    assert any(item["date_display"] == "04/14/2026" for item in extracted["marker_occurrences"])
    assert any("summary gate" in note for note in extracted["other_notes"])
    assert not any("ALL SCAN ROWS REJECTED" in note for note in extracted["other_notes"])


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
    assert extracted["latest_draw_date"] == "02/03/2026"
    assert any("gate:" in note and "page excluded" in note for note in extracted["other_notes"])
    assert not any("ALL SCAN ROWS REJECTED" in note for note in extracted["other_notes"])


def test_patient_header_gate_is_mandatory():
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    accepted, notes = scan.gate_reads(pairs, set(), pipeline._CELL_VALUE_RE,
                                     pipeline._PRINTED_DATE_RE, pipeline._normalize_date_for_matching)
    assert accepted == []
    assert any("digital patient name missing" in note for note in notes)


def test_invalid_calendar_date_rejected():
    data = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in data[1]:
        page["collected"] = "02/30/2026"
    accepted, notes = gate(data)
    assert accepted == []
    assert any("date gate: invalid date" in note for note in notes)


def staff_gate(data, failures=()):
    return scan.gate_staff_identified_reads(data, "04/14/2026", pipeline._CELL_VALUE_RE,
                                            "Pat Synthetic", failures)


def test_staff_identity_skips_printed_metadata_gates(capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    pairs[0][1]["footer"] = "SPECIMEN: SYN-SCAN-001 PAGE 1 OF 7"
    pairs[1][0].update(illegible=True, specimen_id=None, collected=None, footer=None)
    pairs[1][1].update(collected="04/23/2026")
    accepted, notes = staff_gate(pairs)
    assert notes == []
    assert [(row["name"], row["result_text"], row["flag"], row["reference_range"]) for row in accepted] == GOLDEN
    assert all(row["date"] == "04/14/2026" and row["source"] == "scan" for row in accepted)
    lines = capsys.readouterr().out.splitlines()
    assert sum('identity="staff"' in line and "verdict=kept" in line for line in lines) == 2
    assert not any("Synthetic" in line for line in lines)


def test_staff_printed_name_mismatch_is_review_notice_only(capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    pairs[1][1]["patient_name"] = "Different, Person"
    partial = {**copy.deepcopy(pairs[0][0]), "page": 4, "patient_name": "Other, Name", "rows": []}
    accepted, notes = staff_gate(pairs, [(4, [partial], "schema gate: invalid JSON on page 4")])
    assert len(accepted) == len(GOLDEN)
    mismatches = [note for note in notes if note.startswith("STAFF REVIEW - PATIENT NAME MISMATCH")]
    assert [note.split("page ")[1].split()[0] for note in mismatches] == ["3", "4"]
    assert notes[:2] == mismatches
    output = capsys.readouterr().out
    assert "PATIENT NAME MISMATCH: source=scan page 3" in output
    for name in ("Synthetic", "Pat", "Different", "Person", "Other"):
        assert name not in output and not any(name in note for note in notes)


@pytest.mark.parametrize("failure", ["flag", "grammar", "flag_in_text"])
def test_staff_identity_keeps_value_format_and_flag_checks(failure):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in pairs[0]:
        row = page["rows"][0]
        if failure == "flag":
            row.update(result_text="49", flag=None)
        elif failure == "grammar":
            row["result_text"] = "probably normal"
        else:
            row["result_text"] = "59H"
    for page in pairs[1]:
        page.update(patient_name=None, footer=None, specimen_id=None, illegible=True)
    accepted, notes = staff_gate(pairs)
    assert len(accepted) == len(GOLDEN) - 1
    assert all(row["name"] != "IRON, TOTAL" for row in accepted)
    reason = {"flag": "flag gate:", "grammar": "grammar gate: unsupported",
              "flag_in_text": "grammar gate: result_text includes a flag"}[failure]
    assert len(notes) == 1 and "excluded 'IRON, TOTAL': " + reason in notes[0]


@pytest.mark.parametrize("field", ["result_text", "flag", "reference_range", "missing", "duplicate"])
def test_staff_identity_keeps_only_rows_both_reads_agree_on(field):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    row = pairs[0][1]["rows"][0]
    if field == "missing":
        pairs[0][1]["rows"].pop(0)
    elif field == "duplicate":
        pairs[0][1]["rows"].append(copy.deepcopy(row))
    else:
        row[field] = {"result_text": "58", "flag": "L", "reference_range": "50-170"}[field]
    accepted, notes = staff_gate(pairs)
    assert len(accepted) == len(GOLDEN) - 1
    assert all(row["name"] != "IRON, TOTAL" for row in accepted)
    assert len(notes) == 1 and "excluded 'IRON, TOTAL': agreement gate" in notes[0]


def test_staff_identity_does_not_apply_digital_layout_gates():
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in pairs[0]:
        page["rows"][0].update(column=None, section=None)
    accepted, notes = staff_gate(pairs)
    assert notes == []
    assert len(accepted) == len(GOLDEN)


@pytest.mark.parametrize("reading", [0, 1])
def test_staff_summary_excludes_only_contradicted_rows(reading):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    summary = pairs[1][reading]["out_of_range_summary"]
    summary[0]["result_text"] = "31"
    del summary[1:3]
    accepted, notes = staff_gate(pairs)
    assert len(accepted) == len(GOLDEN) - 1
    assert all(row["name"] != "FERRITIN" for row in accepted)
    assert notes == ["source=scan page 2 excluded 'FERRITIN': "
                     "summary gate: row disagrees with printed out-of-range summary"]


def test_staff_empty_summary_does_not_exclude_rows():
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    for page in pairs[1]:
        page["out_of_range_summary"] = []
    accepted, notes = staff_gate(pairs)
    assert len(accepted) == len(GOLDEN) and notes == []


def test_staff_rowless_page_is_notice_only(capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    rowless = {"page": 4, "specimen_id": None, "collected": None, "patient_name": None, "footer": None,
               "illegible": True, "rows": [], "out_of_range_summary": None}
    pairs.append([copy.deepcopy(rowless), copy.deepcopy(rowless)])
    accepted, notes = staff_gate(pairs)
    assert len(accepted) == len(GOLDEN)
    assert notes == ["source=scan page 4: no result rows read; notice only"]
    assert any("page=4" in line and "verdict=notice" in line for line in capsys.readouterr().out.splitlines())


def test_staff_page_with_no_agreeing_rows_is_reported():
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    pairs[0][1]["rows"] = []
    accepted, notes = staff_gate(pairs)
    assert len(accepted) == len(GOLDEN) - 54
    assert sum("row present" in note or "agreement gate" in note for note in notes) == 54
    assert notes[-1] == "source=scan page 2: row gates: no rows passed; page excluded"


def test_staff_failed_transcription_is_page_local(capsys):
    pairs = [[copy.deepcopy(page), copy.deepcopy(page)] for page in payloads()]
    accepted, notes = staff_gate(pairs[1:], [(2, [pairs[0][0]], "schema gate: invalid JSON on page 2")])
    assert len(accepted) == len(GOLDEN) - 54
    assert notes == ["source=scan page 2: schema gate: invalid JSON on page 2; page excluded"]
    assert any("page=2 rows_read=54/null" in line for line in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize(("text", "expected"), [
    ("04/14/2026", "04/14/2026"), ("4/14/2026", "04/14/2026"), ("2026-04-14", "04/14/2026"),
    ("02/30/2026", None), ("April 14", None), ("", None),
])
def test_staff_collected_date(text, expected):
    if expected:
        assert scan.staff_collected_date(text) == expected
    else:
        with pytest.raises(ValueError, match="Collected date"):
            scan.staff_collected_date(text)


def test_pipeline_uses_staff_identity_for_scanned_pages(mixed_pdf, tmp_path):
    data = payloads()
    for page in data:
        page.update(collected=None, patient_name=None, specimen_id=None, footer=None)
    reads = [copy.deepcopy(page) for page in data for _ in (1, 2)]
    reads[1]["footer"] = "PAGE 1 OF 7"
    reads[2]["illegible"] = True
    reads[3]["patient_name"] = "Different, Person"
    extracted = pipeline.extract(str(mixed_pdf), [], None, patient_name="Synthetic, Pat",
                                 collected_date="2026-04-14", client=MockClient(reads),
                                 audit_root=str(tmp_path))
    assert extracted["latest_draw_date"] == "04/14/2026"
    assert sum(" accepted " in note for note in extracted["other_notes"]) == len(GOLDEN)
    assert not any("excluded" in note or "REJECTED" in note for note in extracted["other_notes"])
    assert extracted["other_notes"][0].startswith("STAFF REVIEW - PATIENT NAME MISMATCH: source=scan page 3")
    assert any("identified by staff-entered" in note for note in extracted["other_notes"])
    values = {(item["name"], item["date_display"]): item for item in extracted["marker_occurrences"]}
    assert values["TSH", "04/14/2026"]["disp_value"] == "1.19"


def test_pipeline_without_staff_date_keeps_printed_identity_gates(mixed_pdf, tmp_path):
    reads = [copy.deepcopy(page) for page in payloads() for _ in (1, 2)]
    reads[1]["footer"] = "PAGE 1 OF 7"
    extracted = pipeline.extract(str(mixed_pdf), [], None, patient_name="Synthetic, Pat",
                                 client=MockClient(reads), audit_root=str(tmp_path))
    assert any("metadata agreement gate: footer differs" in note for note in extracted["other_notes"])


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
            "page": number, "specimen_id": "SYN-MOCKED-SCAN", "collected": "04/14/2026 09:15" if number == 21 else None,
            "footer": f"SPECIMEN: SYN-MOCKED-SCAN PAGE {number - 14} OF 7",
            "patient_name": next(iter(names)), "illegible": False,
            "rows": page_rows, "out_of_range_summary": summary if number == 20 else None,
        })
    extracted = pipeline.extract(str(SOURCE), [], None, client=client_for(data), audit_root=str(tmp_path))
    assert extracted["first_draw_date"] == "01/13/2026"
    assert extracted["latest_draw_date"] == "04/14/2026"
    latest = {row["name"]: row for row in extracted["marker_occurrences"] if row["date_display"] == "04/14/2026"}
    assert latest["TSH"]["disp_value"] == "1.19"
    assert latest["Testosterone, Total"]["disp_value"] == "1193"
    assert latest["Glucose (fasting)"]["disp_value"] == "82"
    assert latest["Vitamin D"]["disp_value"] == "73"
    assert sum("accepted " in note for note in extracted["other_notes"]) == len(GOLDEN)
    assert not any("excluded" in note or "REJECTED" in note for note in extracted["other_notes"])
    audit = json.loads(next(tmp_path.rglob("source-rows.json")).read_text())
    assert all("source" not in row for row in audit)


def _synthetic_live_pdf(tmp_path):
    document = fitz.open()
    digital = document.new_page()
    preamble = [
        (40, 40, "Order ID: SYN-LIVE"),
        (40, 54, "Patient Name: TEST, PATIENT"),
        (40, 68, "Collected: 02/03/2026"),
    ]
    for x, y, text in preamble + fx.header_line(100) + fx.row(130, "TSH", "1.35"):
        digital.insert_text((x, y), text, fontsize=9)
    image_doc = fitz.open()
    source = image_doc.new_page()
    for point, text in [
        ((40, 40), "Patient Name: TEST, PATIENT"), ((40, 60), "Collected: 04/14/2026"),
        ((40, 80), "SPECIMEN: SPECIMEN-A"), ((40, 100), "ROUTINE PANELS"),
        ((40, 120), "Test Name"), ((220, 120), "In Range"), ((320, 120), "Out of Range"),
        ((430, 120), "Reference Range"), ((40, 150), "TSH"), ((220, 150), "1.19"),
        ((430, 150), "0.40-4.50"),
        ((40, 180), "HEMOGLOBIN"), ((320, 180), "18.0 H"), ((430, 180), "13.2-17.1"),
        ((40, 210), "FERRITIN"), ((320, 210), "20 L"), ((430, 210), "38-380"),
        ((40, 240), "GLUCOSE"), ((220, 240), "85"), ((430, 240), "65-99"),
        ((40, 270), "CREATININE"), ((220, 270), "1.00"), ((430, 270), "0.60-1.29"),
        ((40, 300), "URINALYSIS"),
        ((40, 330), "GLUCOSE"), ((220, 330), "NEGATIVE"), ((430, 330), "NEGATIVE"),
        ((40, 400), "LIST OF RESULTS PRINTED IN THE OUT OF RANGE COLUMN"),
        ((40, 430), "HEMOGLOBIN 18.0 H"), ((40, 460), "FERRITIN 20 L"),
    ]:
        source.insert_text(point, text, fontsize=9)
    document.new_page().insert_image(source.rect, pixmap=source.get_pixmap(dpi=200))
    path = tmp_path / "synthetic-live.pdf"
    document.save(path)
    document.close()
    image_doc.close()
    return path


def test_synthetic_live_fixture_has_one_identity_and_image_only_scan(tmp_path):
    with fitz.open(_synthetic_live_pdf(tmp_path)) as document:
        assert scan.digital_patient_names([document[0]], pipeline._group_lines, pipeline._page_words) == {
            "TEST, PATIENT",
        }
        assert pipeline._page_words(document[1]) == []


@pytest.mark.skipif(os.environ.get("CELLDEEP_LIVE_VISION") != "1", reason="Live vision explicitly disabled")
def test_live_synthetic_scan(tmp_path):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("API key absent")
    path = _synthetic_live_pdf(tmp_path)
    extracted = pipeline.extract(str(path), [], None, audit_root=str(tmp_path))
    accepted = [note for note in extracted["other_notes"] if " accepted " in note]
    excluded = [note for note in extracted["other_notes"] if " excluded " in note or "REJECTED" in note]
    print(f"Synthetic live result: {len(accepted)} accepted, {len(excluded)} excluded/rejected")
    for note in accepted + excluded:
        print(note)
    assert len(accepted) == 6
    assert excluded == []
    assert any(row["name"] == "TSH" and row["disp_value"] == "1.19"
               and row["date_display"] == "04/14/2026" for row in extracted["marker_occurrences"])
