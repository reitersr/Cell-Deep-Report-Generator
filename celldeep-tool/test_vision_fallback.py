"""Unknown-layout fallback: a page the table reader cannot read is transcribed by the vision model (the scanned-page
agreement rule) and every value is verified against the page's own text layer. Synthetic page in a made-up layout
(synthetic_fixtures/unknown_layout.py), scripted model reads; no network."""

import fitz
import pytest

import pipeline
from synthetic_fixtures import unknown_layout as ul
from synthetic_fixtures.scenarios import ScriptedVision, run_scenario

TRUE_ROWS = [("TSH", "1.9", None, "0.40-4.50"), ("Ferritin", "22", "L", "30-400"), ("hs-CRP", "0.6", None, "0.0-3.0")]


@pytest.fixture
def fallback_on(monkeypatch):
    monkeypatch.setenv(pipeline.VISION_FALLBACK_ENV, "1")


def _extract(tmp_path, reads, pages=None):
    labs = ul.write(tmp_path / "labs.pdf", pages)
    vision = ScriptedVision({(str(labs), 1): reads})
    extracted = pipeline.extract(str(labs), [], None, patient_name="Synthetic, Pat", audit_root=str(tmp_path),
                                 client=vision, sex="male")
    return extracted, vision


def _values(extracted):
    found = {(o["name"], o["date_display"]): (o["disp_value"], o["lab_flag"], o["lab_range_display"])
             for o in extracted["marker_occurrences"]}
    found.update({(item["name"], r["date_display"]): (r["disp_value"], r.get("lab_flag") or "", r.get("lab_range") or "")
                  for item in extracted["lab_reported"] for r in item["results"]})
    return found


def test_values_printed_on_the_page_are_accepted_with_the_pages_own_date(tmp_path, fallback_on):
    extracted, vision = _extract(tmp_path, [ul.read(TRUE_ROWS)] * 2)
    assert len(vision.calls) == 2  # two reads agreed: no third read
    assert _values(extracted) == {("TSH", ul.COLLECTED): ("1.9", "", "0.40-4.50"),
                                  ("hs-CRP", ul.COLLECTED): ("0.6", "", "0.0-3.0"),
                                  ("Ferritin", ul.COLLECTED): ("22", "L", "30-400")}
    assert extracted["parse_exclusions"] == []  # the page is read now; its whole-page exclusion is replaced
    notes = extracted["other_notes"]
    assert notes[0].startswith("AI FALLBACK SUMMARY: 3 value(s) came from AI reads verified against the page")
    assert "READ BY AI, VERIFIED AGAINST THE PAGE: page 1 'TSH' '1.9' (Collected 04/14/2026, printed on the page)" \
        in notes
    assert all(o["source_label"].endswith("read by AI and verified against the page")
               for o in extracted["marker_occurrences"])


def test_a_value_the_model_makes_up_is_rejected(tmp_path, fallback_on):
    made_up = [("TSH", "2.7", None, "0.40-4.50"), ("Ferritin", "22", None, "30-400"), ("hs-CRP", "0.6", None, "0.0-3.0"),
               ("Vitamin B12", "512", None, "200-1100")]
    extracted, _ = _extract(tmp_path, [ul.read(made_up)] * 2)
    found = _values(extracted)
    assert ("TSH", ul.COLLECTED) not in found  # page prints 1.9
    assert ("Ferritin", ul.COLLECTED) not in found  # page prints "22 L": the model dropped the lab's flag
    assert ("Vitamin B12", ul.COLLECTED) not in found  # not on the page at all
    assert found[("hs-CRP", ul.COLLECTED)] == ("0.6", "", "0.0-3.0")
    reasons = [item["reason"] for item in extracted["parse_exclusions"]]
    assert "TSH: value '2.7' read by AI is not printed on that test's line of the page's text layer; not used" in reasons
    assert any(reason.startswith("Vitamin B12: value '512'") for reason in reasons)
    assert any(item.startswith("Lab PDF page 1 (AI fallback): TSH: value '2.7'") for item in extracted["preflight"])


def test_a_value_printed_on_another_line_or_a_range_not_printed_is_never_taken(tmp_path, fallback_on):
    rows = [("TSH", "0.6", None, "0.40-4.50"), ("hs-CRP", "0.6", None, "0.1-9.9")]  # 0.6 is hs-CRP's line, not TSH's
    extracted, _ = _extract(tmp_path, [ul.read(rows)] * 2)
    found = _values(extracted)
    assert ("TSH", ul.COLLECTED) not in found
    assert found[("hs-CRP", ul.COLLECTED)] == ("0.6", "", "")  # the model's range is not on the page: dropped


def test_disagreeing_reads_follow_the_two_of_three_rule(tmp_path, fallback_on):
    second = [("TSH", "1.9", None, "0.40-4.50"), ("Ferritin", "23", "L", "30-400"), ("hs-CRP", "0.6", None, "0.0-3.0")]
    third = [("TSH", "1.9", None, "0.40-4.50"), ("Ferritin", "24", "L", "30-400"), ("hs-CRP", "0.6", None, "0.0-3.0")]
    extracted, vision = _extract(tmp_path, [ul.read(TRUE_ROWS), ul.read(second), ul.read(third)])
    assert len(vision.calls) == 3
    assert ("Ferritin", ul.COLLECTED) not in _values(extracted)  # no two reads agree on it
    assert [item["name"] for item in extracted["scan_row_exclusions"]] == ["Ferritin"]
    assert extracted["scan_row_exclusions"][0]["reason"].startswith("AI fallback - agreement gate")


def test_a_page_with_no_single_printed_collected_date_is_not_read_by_ai(tmp_path, fallback_on):
    page = [item for item in ul.page() if not item[2].startswith("Collected")]
    extracted, vision = _extract(tmp_path, [ul.read(TRUE_ROWS)] * 2, pages=[page])
    assert vision.calls == [] and _values(extracted) == {}
    assert any("prints no Collected date, and a date is never taken from the model" in note
               for note in extracted["other_notes"])


def test_the_kill_switch_turns_the_fallback_off(tmp_path, monkeypatch):
    monkeypatch.setenv(pipeline.VISION_FALLBACK_ENV, "0")
    extracted, vision = _extract(tmp_path, [ul.read(TRUE_ROWS)] * 2)
    assert vision.calls == [] and _values(extracted) == {}
    assert extracted["parse_exclusions"][0]["page"] == 1  # the page stays excluded, as before
    assert pipeline.vision_fallback_enabled() is False
    monkeypatch.delenv(pipeline.VISION_FALLBACK_ENV)
    assert pipeline.vision_fallback_enabled() is True  # on by default


def test_a_page_with_no_text_layer_keeps_the_scanned_two_of_three_rule(tmp_path):
    noisy = run_scenario("scanned_noisy", tmp_path / "noisy")
    assert noisy["reads"] == 6
    assert "INCOMPLETE - row excluded: FERRITIN (reads disagree: 88 / 86 / 83)" in noisy["review"]


def test_the_report_marks_and_counts_ai_read_values(tmp_path, fallback_on, monkeypatch):
    labs = ul.write(tmp_path / "labs.pdf")
    vision = ScriptedVision({(str(labs), 1): [ul.read(TRUE_ROWS)] * 2})
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: vision)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    pipeline.run(str(labs), [], None, "Synthetic, Pat", 44, "male", str(tmp_path / "report.pdf"))
    review = (tmp_path / "review.txt").read_text(encoding="utf-8")
    assert "AI FALLBACK SUMMARY: 3 value(s) came from AI reads verified against the page" in review
    with fitz.open(tmp_path / "report.pdf") as document:
        text = " ".join(page.get_text() for page in document)
    assert "READ BY AI" not in text and "AI FALLBACK" not in text  # staff-only


def test_nothing_read_and_nothing_scored_are_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    labs = ul.write(tmp_path / "labs.pdf")  # fallback off: nothing is read
    with pytest.raises(pipeline.NoResultsRead, match="^No results were read from the uploaded files"):
        pipeline.run(str(labs), [], None, "Synthetic, Pat", 44, "male", str(tmp_path / "report.pdf"))
    from synthetic_fixtures import deterministic_fixtures as fx
    only_lab_reported = fx.write_lab_pdf(tmp_path / "cbc.pdf", [
        fx.section_preamble("SYN-E-1", ul.COLLECTED) + fx.header_line(100)
        + fx.row(130, "Sodium", "140", units="mmol/L", lab_range="135-146")])
    with pytest.raises(pipeline.NoResultsRead, match="that CellDeep can score"):
        pipeline.run(str(only_lab_reported), [], None, "Synthetic, Pat", 44, "male", str(tmp_path / "r2.pdf"))
    assert not (tmp_path / "report.pdf").exists() and not (tmp_path / "r2.pdf").exists()
