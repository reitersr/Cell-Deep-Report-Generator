"""Provider note intake: read template lines, list every other line with its reason, never infer."""

from pathlib import Path

import app
import pipeline

NOTE = """# CellDeep Provider Notes

<!-- A two-line comment
that must not shift line numbers. -->

## Consultation Note
Follow-up visit; synthetic patient.

## Patient Concerns
- Afternoon energy dips | Systems: Repair, Pace
- Poor sleep | Systems: Sleepiness

## Protocol
- BPC-157 | Cadence: daily
- Tirzepatide weekly | Cadence: weekly | extra
Started NAD+ last week

## Marker Targets
- TSH: 1.0-2.0
- Made Up Marker: 1-2

## Vitality Index
- Energy: Some Concern
- Sleep: Pretty Bad

## Supplements
- Magnesium nightly; patient is on testosterone replacement therapy
"""


def _note():
    return pipeline.parse_provider_note(NOTE)


def test_valid_lines_are_read_and_others_listed_with_line_numbers():
    rejected = pipeline.check_provider_note(NOTE)["rejected"]
    assert [(item["line"], item["text"]) for item in rejected] == [
        (11, "- Poor sleep | Systems: Sleepiness"),
        (15, "- Tirzepatide weekly | Cadence: weekly | extra"),
        (16, "Started NAD+ last week"),
        (20, "- Made Up Marker: 1-2"),
        (24, "- Sleep: Pretty Bad"),
        (26, "## Supplements"),
        (27, "- Magnesium nightly; patient is on testosterone replacement therapy"),
    ]
    reasons = {item["line"]: item["reason"] for item in rejected}
    assert "Systems" in reasons[11] and "Repair" in reasons[11]
    assert reasons[16].startswith("expected a '- ' item")
    assert "'Made Up Marker' is not a recognized marker" in reasons[20]
    assert reasons[26].startswith("unknown section")
    assert reasons[27] == "under an unknown section heading, so it is not read"


def test_accepted_content_is_exactly_the_matching_lines():
    note = _note()
    assert note["accepted"] is True
    assert note["pain_points"] == [{"text": "Afternoon energy dips", "categories": ["Repair", "Pace"]}]
    assert [(item["name"], item["cadence"]) for item in note["protocol"]] == [("BPC-157", "daily")]
    assert note["marker_overrides"] == [{"marker": "TSH", "lo": 1.0, "hi": 2.0}]
    assert note["vitality_index"]["Energy"] == "Some Concern"
    assert note["vitality_index"]["Sleep"] == "Not Assessed"  # the unreadable line is not guessed


def test_rejected_lines_never_set_treatment_status():
    note = _note()
    assert "testosterone replacement therapy" not in note["accepted_text"]
    assert pipeline.parse_provider_statuses(note["accepted_text"]) == (None, None)
    extracted = pipeline.extract(None, [], NOTE, patient_name="Synthetic, Pat")
    record, _ = pipeline.score_and_build_record(extracted)
    assert record.on_trt is None


def test_free_text_never_becomes_protocol_or_concerns():
    note = pipeline.parse_provider_note("## Consultation Note\nStarted BPC-157 daily; worried about sleep.")
    assert note["accepted"] is True
    assert note["protocol"] == [] and note["pain_points"] == [] and note["other_notes"] == []


def test_staff_notes_list_each_rejected_line():
    notes = _note()["other_notes"]
    assert notes[0].startswith("PROVIDER NOTE: 7 LINE(S) NOT READ")
    assert "    line 16: 'Started NAD+ last week' - expected a '- ' item under '## Protocol'" in notes


def test_note_without_sections_is_not_read_and_every_line_is_listed():
    note = pipeline.parse_provider_note("Pt doing well.\nStarted BPC-157 daily.")
    assert note["accepted"] is False and note["protocol"] == []
    assert note["other_notes"][0].startswith("PROVIDER NOTE REJECTED")
    assert note["other_notes"][1:] == ["    line 1: 'Pt doing well.' - text outside a '## ' section",
                                       "    line 2: 'Started BPC-157 daily.' - text outside a '## ' section"]


def test_upload_page_offers_template_download_and_note_check(staff_client):
    client = staff_client
    page = client.get("/").get_data(as_text=True)
    assert 'href="/provider-note-template"' in page and 'id="check-note"' in page
    download = client.get("/provider-note-template")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]
    template = (Path(app.__file__).parent / "templates" / "provider_notes_template.md").read_text(encoding="utf-8")
    assert download.get_data(as_text=True) == template
    assert pipeline.check_provider_note(template) == {
        "read": True, "rejected": [], "fix": "",
        "sections": ["Consultation Note", "Treatment Status", "Patient Concerns", "Protocol", "Marker Targets",
                     "Vitality Index"]}
    result = client.post("/note/check", data={"note_text": NOTE}).get_json()
    assert result["read"] is True and len(result["rejected"]) == 7
    assert result["rejected"][0] == {"line": 11, "text": "- Poor sleep | Systems: Sleepiness",
                                     "reason": pipeline.check_provider_note(NOTE)["rejected"][0]["reason"]}


STATUS_NOTE = """## Consultation Note
Patient is on testosterone replacement therapy.

## Treatment Status
- On TRT: {trt}
- Postmenopausal and on BHRT: Not stated
- Thyroid medication: Yes

## Patient Concerns
- Afternoon energy dips | Systems: Repair

## Protocol
- BPC-157 | Cadence: daily

## Marker Targets
- TSH: 1.0-2.0
"""


def _record(note):
    extracted = pipeline.extract(None, [], note, patient_name="Synthetic, Pat")
    return pipeline.score_and_build_record({**extracted, "sex": "male"})


def test_all_four_sections_fill_from_the_template():
    record, notice = _record(STATUS_NOTE.format(trt="Yes"))
    assert record.on_trt is True and record.postmenopausal_bhrt is None
    assert [p.name for p in record.protocol] == ["BPC-157"]
    assert [p.text for p in record.pain_points] == ["Afternoon energy dips"]
    note = pipeline.parse_provider_note(STATUS_NOTE.format(trt="Yes"))
    assert note["marker_overrides"] == [{"marker": "TSH", "lo": 1.0, "hi": 2.0}]
    assert note["treatment_status"] == {"on_trt": True, "postmenopausal_bhrt": None}


def test_unknown_status_line_is_rejected_with_its_reason():
    rejected = pipeline.check_provider_note(STATUS_NOTE.format(trt="Yes"))["rejected"]
    assert rejected == [{"line": 7, "text": "- Thyroid medication: Yes",
                         "reason": "status must read '- On TRT: Yes / No / Not stated' or "
                                   "'- Postmenopausal and on BHRT: Yes / No / Not stated'"}]


def test_status_section_wins_over_note_text_and_the_conflict_is_noted():
    record, notice = _record(STATUS_NOTE.format(trt="No"))
    assert record.on_trt is not True
    assert any(note.startswith("TREATMENT STATUS: the note text mentions TRT") for note in notice.other_notes)


def test_without_a_status_section_only_explicit_statements_count():
    record, _ = _record("## Consultation Note\nPatient is on testosterone replacement therapy.\n")
    assert record.on_trt is True
    record, _ = _record("## Consultation Note\nDiscussed TRT as a possible future option.\n")
    assert record.on_trt is None
