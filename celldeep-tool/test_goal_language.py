"""Patient-facing goal language (synthetic data; the four locked real cases only where real_fixtures/ is present).

- The hero and the age column state a goal for a time frame, never a promise for an age: "Your goal: X% optimized in
  90 days", where X is the ceiling (the existing rollup with every not-yet-optimized scored marker that has a CellDeep
  threshold counted as optimized, at the lowest optimized score), or "Maintain your current score" at the ceiling.
  Female reports count only clinic-confirmed female thresholds.
- One regimen line from the static config/protocol_marker_map.json (items marked Start, Continue or Adjust); an item
  not in the table gives only the generic line; no such items, no line.
- One added sentence per category from the static config/marker_explanations.json; no entry, no sentence.
- Nothing else in the patient PDF changes: every score, status, value, date and DEXA line stays."""

import collections
import dataclasses
import json
import re
from pathlib import Path

import fitz
import pytest

import clinic_config
import generation_prompt as gp
import pipeline
import template
from schema import ProtocolItem
from synthetic_fixtures import scenarios

FORBIDDEN = ("fully optimized", "by your next birthday", "everything, optimized", "—", '"', "“", "”")
REAL = Path(__file__).resolve().parent / "real_fixtures"
PDF_TEXT = REAL / "pdf_text"


def _record_and_copy(name, tmp_path, monkeypatch):
    captured = {}
    original = pipeline.template.render
    monkeypatch.setattr(pipeline.template, "render", lambda record, copy, out, **kw: (
        captured.update(record=record, copy=copy), original(record, copy, out, **kw)))
    scenarios.run_scenario(name, tmp_path, monkeypatch)
    return captured["record"], captured["copy"]


def _overall_and_ceiling(record, copy):
    args = (copy.get("structure_score_now"), copy.get("structure_score_then"), copy.get("structure_improved", False))
    return template.build_rollups(record, *args)[2], template.goal_ceiling(record, *args)


def _pdf_text(path):
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document), len(document)


# --- the goal --------------------------------------------------------------------------------------------------

def test_the_goal_is_the_ceiling_and_maintain_at_or_above_it():
    assert gp.goal_texts(70, 83) == {"hero_question": "Your goal: 83% optimized in 90 days",
                                     "hero_target_line": "YOUR GOAL FOR THE NEXT 90 DAYS",
                                     "by_age_sub": "Goal: 83% optimized"}
    for ceiling in (70, 65, None):
        texts = gp.goal_texts(70, ceiling)
        assert texts["hero_question"] == texts["by_age_sub"] == "Maintain your current score"
        assert not re.search(r"\d", " ".join(texts.values()))  # no number


@pytest.mark.parametrize("name", ["chl_digital", "chl_extensive", "female_chl_scanned_undated", "scanned_mixed_reads"])
def test_the_goal_never_exceeds_the_ceiling_and_never_uses_promise_wording(name, tmp_path, monkeypatch):
    record, copy = _record_and_copy(name, tmp_path, monkeypatch)
    overall, ceiling = _overall_and_ceiling(record, copy)
    assert ceiling >= overall
    text, _ = _pdf_text(tmp_path / "report.pdf")
    if ceiling > overall:
        assert f"Your goal: {ceiling}% optimized in 90 days" in text and f"Goal: {ceiling}% optimized" in text
        assert all(int(x) <= ceiling for x in re.findall(r"goal: (\d+)%", text, re.IGNORECASE))
    else:
        assert text.count("Maintain your current score") == 2
    lowered = " ".join(text.split()).lower()
    assert "fully optimized" not in lowered and "by your next birthday" not in lowered
    assert "everything, optimized" not in lowered


def test_a_female_report_uses_only_clinic_confirmed_female_thresholds(tmp_path, monkeypatch):
    monkeypatch.setattr(clinic_config, "FEMALE_RANGES_CONFIRMED", False)
    record, copy = _record_and_copy("female_chl_scanned_undated", tmp_path, monkeypatch)
    overall, ceiling = _overall_and_ceiling(record, copy)
    assert ceiling == overall  # nothing is clinic-confirmed yet, so nothing moves
    text, _ = _pdf_text(tmp_path / "report.pdf")
    assert "Maintain your current score" in text and clinic_config.FEMALE_DRAFT_MARK in text


def test_the_ceiling_counts_only_not_yet_optimized_markers_with_a_celldeep_threshold(tmp_path, monkeypatch):
    record, copy = _record_and_copy("chl_digital", tmp_path, monkeypatch)
    overall, ceiling = _overall_and_ceiling(record, copy)
    countable = [m for m in record.markers if m.now_tier in ("moderate", "flag") and m.range_source != "lab"]
    assert countable and ceiling > overall
    # Scored on the lab's printed fallback range only: never counted.
    as_lab = [dataclasses.replace(m, range_source="lab") for m in record.markers]
    args = (copy.get("structure_score_now"), copy.get("structure_score_then"), copy.get("structure_improved", False))
    assert template.goal_ceiling(dataclasses.replace(record, markers=as_lab), *args) == overall


# --- the regimen line --------------------------------------------------------------------------------------------

def _with_protocol(record, *items):
    return dataclasses.replace(record, protocol=list(items))


def _testosterone(record, value):
    """The record with Testosterone, Total at value (the male CellDeep range, 600-900 ng/dL), rescored."""
    import scoring

    marker = next(m for m in record.markers if m.name == "Testosterone, Total")
    changed = dataclasses.replace(marker, now=value, disp_now=str(value))
    scoring.attach_scores(changed, sex="male")
    return dataclasses.replace(record, markers=[changed if m is marker else m for m in record.markers]), changed


def test_a_high_testosterone_never_reads_as_moving_toward_optimized(tmp_path, monkeypatch):
    record, _ = _record_and_copy("scanned_mixed_reads", tmp_path, monkeypatch)
    high, marker = _testosterone(record, 1214)  # above the 600-900 range; testosterone raises it
    assert marker.now_tier == "flag" and gp.optimized_side(marker) == "above"
    item = ProtocolItem(name="Testosterone", cadence="weekly", action="Continue")
    assert gp.regimen_line(_with_protocol(high, item)) == (
        "Your protocol includes Testosterone, which acts on Testosterone, Total.")
    low, marker = _testosterone(record, 410)  # below the range: the item moves it toward optimized
    assert gp.optimized_side(marker) == "below"
    assert gp.regimen_line(_with_protocol(low, item)) == (
        "Continuing Testosterone supports your Testosterone, Total and moves your Drive toward optimized.")
    assert gp.regimen_line(_with_protocol(low, dataclasses.replace(item, action="Adjust"))).startswith(
        "Adjusting Testosterone supports")


def test_the_regimen_line_otherwise(tmp_path, monkeypatch):
    record, _ = _record_and_copy("scanned_mixed_reads", tmp_path, monkeypatch)
    # Not in the table: only the generic line.
    unmapped = ProtocolItem(name="Semax", cadence="as directed", action="Start")
    assert gp.regimen_line(_with_protocol(record, unmapped)) == "Continuing your protocol supports this goal."
    # No item marked Start, Continue or Adjust (none, Stop, Considering), or no protocol at all: no line.
    for action in (None, "Stop", "Considering"):
        item = ProtocolItem(name="Testosterone", cadence="weekly", action=action)
        assert gp.regimen_line(_with_protocol(record, item)) == "", action
    assert gp.regimen_line(_with_protocol(record)) == ""


def test_the_provider_note_action_field_is_read_and_checked():
    note = pipeline.parse_provider_note("## Protocol\n\n- Testosterone | Cadence: weekly | Action: Continue\n"
                                        "- Vitamin D-3 | Action: start\n- BPC-157\n- Retatrutide | Action: Stop\n"
                                        "- Enclomiphene | Action: Considering\n- Semax | Action: Pause\n")
    assert [(p["name"], p["action"]) for p in note["protocol"]] == [
        ("Testosterone", "Continue"), ("Vitamin D-3", "Start"), ("BPC-157", None)]
    # Stop / Considering are not current protocol: never shown as such, listed for staff.
    assert note["inactive_protocol"] == ["Retatrutide (Action: Stop)", "Enclomiphene (Action: Considering)"]
    assert any("Semax | Action: Pause" in line for line in note["other_notes"])  # never guessed


# --- the category sentence ------------------------------------------------------------------------------------------

_ADDED = re.compile(r"\. Yours is (below|above) the optimized range, which holds this part of your (\w+) back\.")


def test_the_added_sentences_follow_their_marker_and_say_which_side(tmp_path, monkeypatch):
    record, copy = _record_and_copy("chl_digital", tmp_path, monkeypatch)
    story = copy["box_stories"]["Repair"]
    marker = next(m for m in record.markers if m.name == "hs-CRP")
    pair = (f"{gp.MARKER_EXPLANATIONS['hs-CRP']}. Yours is above the optimized range, which holds this part of your "
            "Repair back.")
    assert gp.optimized_side(marker) == "above"
    assert story == f"{gp.marker_sentence(marker)} {pair}"  # directly after the marker's own sentence
    assert len(pair.split()) <= 35


def test_no_added_sentence_without_an_entry_a_side_or_a_celldeep_threshold(tmp_path, monkeypatch):
    record, copy = _record_and_copy("chl_digital", tmp_path, monkeypatch)
    assert _ADDED.search(copy["box_stories"]["Repair"])
    monkeypatch.setattr(gp, "MARKER_EXPLANATIONS", {})
    assert not any(_ADDED.search(text) for text in gp.build_copy(record)["box_stories"].values())
    monkeypatch.undo()
    as_lab = [dataclasses.replace(m, range_source="lab") if m.name == "hs-CRP" else m for m in record.markers]
    assert not _ADDED.search(gp.build_copy(dataclasses.replace(record, markers=as_lab))["box_stories"]["Repair"])
    marker = next(m for m in record.markers if m.name == "hs-CRP")
    assert gp._category_explanation(dataclasses.replace(marker, now_tier="optimal"), "Repair") == ""  # no side


def test_the_static_tables_follow_the_voice_rules():
    import markers_reference
    import protocol_reference

    for name, clause in gp.MARKER_EXPLANATIONS.items():
        assert name in markers_reference.MARKER_LIBRARY
        lowered = clause.lower()
        assert not any(word in lowered for word in FORBIDDEN), name
        assert not re.search(r"\b(good|bad|flagged|caused?|because)\b", lowered), name
        assert not clause.endswith(".")  # a clause: the sentence is composed deterministically
        longest = f"{clause}. Yours is below the optimized range, which holds this part of your Reserves back."
        assert len(longest.split()) <= 35, name
    for item, effects in gp.PROTOCOL_MARKER_MAP.items():
        assert item in protocol_reference.PROTOCOL_LIBRARY and set(effects) <= {"raises", "lowers"}
        assert all(n in markers_reference.MARKER_LIBRARY for names in effects.values() for n in names)
    for text in (gp.MAINTAIN_TEXT, gp.GENERIC_REGIMEN_LINE, *gp.goal_texts(1, 2).values()):
        assert not any(word in text.lower() for word in FORBIDDEN)


# --- the four locked real cases (local only) ----------------------------------------------------------------------

_NEW_SENTENCE = re.compile(r" [A-Z][^.]*?\. Yours is (?:below|above) the optimized range, which holds this part of "
                           r"your \w+ back\.")
_OLD = ["What if you were fully optimized by your next birthday?", "TARGET: FULLY OPTIMIZED BY YOUR NEXT BIRTHDAY",
        "Everything, optimized"]
_NEW = re.compile(r"Your goal: \d+% optimized in 90 days|YOUR GOAL FOR THE NEXT 90 DAYS|YOUR GOAL|"
                  r"Goal: \d+% optimized|Maintain your current score")


@pytest.mark.parametrize("case", ["extensive_male", "female_chl", "limited_male", "chl_quest_male"])
def test_a_locked_cases_pdf_changes_only_in_the_goal_and_category_sentences(case, tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import regression_check as check

    spec_file, baseline = REAL / f"{case}.expected.json", PDF_TEXT / f"{case}.txt"
    if not spec_file.is_file() or not baseline.is_file():
        pytest.skip(check.missing_note(check.real_missing()) + "; patient PDF text baseline not present")
    spec = json.loads(spec_file.read_text())
    reads = {key: json.loads((REAL / spec[key]).read_text())["pages"] if spec.get(key) else None
             for key in ("dexa_reads", "lab_scan_reads")}
    scripted = check.ScriptedReads(reads["dexa_reads"], reads["lab_scan_reads"])
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kw: scripted)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda _n: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda _n: str(tmp_path / "diag"))
    pipeline.run(str(REAL / spec["labs"]), [str(REAL / d) for d in spec["dexa"]], None, spec["patient"], spec["age"],
                 spec.get("sex", "male"), str(tmp_path / "report.pdf"), vitality_index=check.spec_vitality(spec),
                 collected_date=spec["collected_date"], confirm=lambda screen: True,
                 scan_collected_date=spec.get("scan_collected_date"))
    text, _ = _pdf_text(tmp_path / "report.pdf")
    def content(raw):  # the text with the goal wording (old or new) and the added sentences set aside
        flat = " ".join(raw.split())
        for phrase in _OLD:
            flat = flat.replace(phrase, " ")
        return _NEW_SENTENCE.sub(" ", _NEW.sub(" ", flat))

    before, after = content(baseline.read_text()), content(text)
    # Every score, status, value, date and DEXA word is still there exactly as often as before (the added sentences
    # can move a row onto the next page, so the order may shift at a page break, never the content).
    assert collections.Counter(after.split()) == collections.Counter(before.split())
