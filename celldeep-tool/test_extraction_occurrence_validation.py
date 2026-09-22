import json

import pytest

import pipeline


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, payload):
        self.content = [_TextBlock(json.dumps(payload))]


class _SequenceClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.call_count = 0

        class Messages:
            def __init__(inner, owner):
                inner.owner = owner

            def create(inner, **_kwargs):
                index = min(inner.owner.call_count, len(inner.owner.payloads) - 1)
                inner.owner.call_count += 1
                return _Response(inner.owner.payloads[index])

        self.messages = Messages(self)


DATES = ("01/07/2026", "01/27/2026", "04/24/2026")
EVAN_THREE_DRAW_PANEL = {
    "hs-CRP": ((20.0, ">20.0"), (0.3, "0.3"), (None, "<3.0")),
    "Testosterone, Total": ((506, "506"), (1846, "1846"), (1193, "1193")),
    "Free Testosterone": ((66.3, "66.3"), (310.1, "310.1"), (210.0, "210.0")),
    "Estradiol": ((18, "18"), (22, "22"), (42, "42")),
    "DHEA-S": ((250, "250"), (270, "270"), (300, "300")),
    "Cortisol, Total (AM)": ((4.8, "4.8"), (8.2, "8.2"), (14.7, "14.7")),
    "FSH": ((None, "<0.3"), (None, "<0.5"), (None, "<0.7")),
    "LH": ((None, "<0.1"), (None, "<0.15"), (None, "<0.2")),
    "SHBG": ((38, "38"), (45, "45"), (52, "52")),
}


def _occurrence(name, date_display, value, disp_value):
    return {
        "name": name,
        "date_display": date_display,
        "source_label": "Synthetic Evan three-draw panel",
        "status": "reported",
        "value": value,
        "disp_value": disp_value,
        "is_good": None,
        "lab_range_lo": 0,
        "lab_range_hi": 0,
        "lab_range_display": "",
    }


def _full_panel_occurrences():
    return [
        _occurrence(name, date, value, display)
        for name, values in EVAN_THREE_DRAW_PANEL.items()
        for date, (value, display) in zip(DATES, values)
    ]


def _source_text():
    rows = [
        "Historical Previous Current Draw Dates: 01/07/2026 01/27/2026 04/24/2026",
    ]
    for name, values in EVAN_THREE_DRAW_PANEL.items():
        rows.append(f"{name} " + " ".join(display for _value, display in values))
    return "\n".join(rows)


def _run_extract(monkeypatch, tmp_path, payloads):
    lab_pdf = tmp_path / "evan-three-draw.pdf"
    lab_pdf.write_bytes(b"synthetic PDF bytes")
    monkeypatch.setattr(pipeline, "_pdf_text", lambda _path: _source_text())
    client = _SequenceClient(payloads)
    extracted = pipeline.extract(
        client,
        labs_pdf=str(lab_pdf),
        dexa_pdfs=[],
        note_text=None,
        patient_name="Evan Walker occurrence validation",
        audit_root=str(tmp_path),
    )
    return client, extracted


def test_full_evan_three_draw_panel_keeps_every_april_occurrence_as_now(monkeypatch, tmp_path):
    client, extracted = _run_extract(
        monkeypatch,
        tmp_path,
        [{"marker_occurrences": _full_panel_occurrences()}],
    )

    reconciled = {
        marker["name"]: marker
        for marker in pipeline.reconcile_marker_occurrences(extracted["marker_occurrences"])
    }
    assert client.call_count == 1
    assert set(reconciled) == set(EVAN_THREE_DRAW_PANEL)
    for name, values in EVAN_THREE_DRAW_PANEL.items():
        assert reconciled[name]["now_date_display"] == "04/24/2026"
        assert reconciled[name]["disp_now"] == values[-1][1]


def test_missing_april_occurrence_retries_once_and_preserves_both_attempts(monkeypatch, tmp_path):
    complete = _full_panel_occurrences()
    first_attempt = [
        occurrence for occurrence in complete
        if not (occurrence["name"] == "Estradiol" and occurrence["date_display"] == "04/24/2026")
    ]
    client, extracted = _run_extract(
        monkeypatch,
        tmp_path,
        [{"marker_occurrences": first_attempt}, {"marker_occurrences": complete}],
    )

    assert client.call_count == 2
    assert len(extracted["marker_occurrences"]) == len(complete)
    audit_dirs = list((tmp_path / "celldeep_extraction_audits").iterdir())
    assert len(audit_dirs) == 1
    assert (audit_dirs[0] / "attempt-1-marker-occurrences.json").exists()
    assert (audit_dirs[0] / "attempt-2-marker-occurrences.json").exists()


def test_repeated_omission_fails_with_exact_marker_date_and_audit_path(monkeypatch, tmp_path):
    incomplete = [
        occurrence for occurrence in _full_panel_occurrences()
        if not (occurrence["name"] == "Free Testosterone"
                and occurrence["date_display"] == "04/24/2026")
    ]
    lab_pdf = tmp_path / "evan-three-draw.pdf"
    lab_pdf.write_bytes(b"synthetic PDF bytes")
    monkeypatch.setattr(pipeline, "_pdf_text", lambda _path: _source_text())
    client = _SequenceClient([
        {"marker_occurrences": incomplete},
        {"marker_occurrences": incomplete},
    ])

    with pytest.raises(pipeline.ExtractionOccurrenceValidationError) as error:
        pipeline.extract(
            client,
            labs_pdf=str(lab_pdf),
            dexa_pdfs=[],
            note_text=None,
            patient_name="Evan Walker occurrence validation",
            audit_root=str(tmp_path),
        )

    message = str(error.value)
    assert client.call_count == 2
    assert "Free Testosterone [04/24/2026]" in message
    assert "Audit files:" in message
    audit_dirs = list((tmp_path / "celldeep_extraction_audits").iterdir())
    assert len(audit_dirs) == 1
    assert (audit_dirs[0] / "attempt-1-marker-occurrences.json").exists()
    assert (audit_dirs[0] / "attempt-2-marker-occurrences.json").exists()


def test_multiple_date_header_does_not_invent_dates_for_single_result_row():
    source = (
        "Historical Previous Current Draw Dates: 01/07/2026 01/27/2026 04/24/2026\n"
        "DHEA-S 250 Reference range 100-500\n"
    )

    assert pipeline._source_marker_dates(source).get("DHEA-S") is None