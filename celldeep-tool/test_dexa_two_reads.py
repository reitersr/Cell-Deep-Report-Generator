"""DEXA two-read agreement gates and deterministic history. Synthetic data and mocked reads only."""

import copy

import fitz
import pytest

import pipeline
import scan_dexa
import template
from synthetic_fixtures import dexa as fx
from unknown_marker_policy import format_review_notice


def _run(tmp_path, labels, pages, name="dexa.pdf", patient=fx.PATIENT):
    path = fx.write_pdf(tmp_path / name, labels)
    reader = fx.DexaReader(path, labels, pages)
    history, notes, summary = pipeline._extract_dexa_with_claude(reader, [str(path)], patient)
    return history, notes, summary, reader


def _same(*scans, **kwargs):
    return (fx.read(list(scans), **kwargs), fx.read(copy.deepcopy(list(scans)), **kwargs))


def test_reads_that_agree_keep_every_measurement_oldest_first(tmp_path):
    history, notes, summary, reader = _run(tmp_path, ["trend"], {"trend": _same(fx.FOLLOW_UP, fx.BASELINE)})
    assert [h["date_display"] for h in history] == ["05/12/2025", "02/03/2026"]
    assert history[0] == {"date_display": "05/12/2025", "estimated": [], "total_mass_lb": 172.0,
                          "fat_mass_lb": 58.0, "lean_mass_lb": 108.9, "body_fat_pct": "33.7%",
                          "vat_fat_mass_lb": 1.06, "visceral_fat_area_cm2": 84.0}
    assert notes == []
    assert summary[0] == "DEXA - 1 page(s) read twice; 2 scan date(s) kept"
    assert len(reader.calls) == 2
    for call in reader.calls:
        assert call["model"] == pipeline.MODEL
        assert call["output_config"]["format"]["schema"] == scan_dexa.DEXA_SCHEMA


def test_one_field_that_differs_is_excluded_and_listed(tmp_path):
    second = copy.deepcopy(fx.BASELINE)
    second["fat_mass"] = "59.0"
    history, notes, _, _ = _run(tmp_path, ["summary"], {"summary": (fx.read([fx.BASELINE]), fx.read([second]))})
    assert history[0]["fat_mass_lb"] is None
    assert history[0]["total_mass_lb"] == 172.0 and history[0]["body_fat_pct"] == "33.7%"
    assert notes == ["DEXA file 1 page 1: scan 05/12/2025 fat mass (lb) excluded - independent reads disagree"]


def test_field_printed_in_only_one_read_is_excluded(tmp_path):
    second = copy.deepcopy(fx.BASELINE)
    second["vat_area"] = None
    history, notes, _, _ = _run(tmp_path, ["summary"], {"summary": (fx.read([fx.BASELINE]), fx.read([second]))})
    assert history[0]["visceral_fat_area_cm2"] is None
    assert notes == ["DEXA file 1 page 1: scan 05/12/2025 VAT area (cm2) excluded - printed in only one read"]


def test_scan_date_the_reads_disagree_on_excludes_the_whole_scan(tmp_path):
    misread = copy.deepcopy(fx.BASELINE)
    misread["date"] = "05/13/2025"
    pages = {"trend": (fx.read([fx.BASELINE, fx.FOLLOW_UP]), fx.read([misread, fx.FOLLOW_UP]))}
    history, notes, _, _ = _run(tmp_path, ["trend"], pages)
    assert [h["date_display"] for h in history] == ["02/03/2026"]
    assert sum("whole scan excluded" in note for note in notes) == 2
    assert all(note.startswith("DEXA file 1 page 1: scan 05/1") for note in notes)


def test_page_printing_a_different_patient_is_excluded_with_a_top_notice(tmp_path):
    pages = {"ours": _same(fx.BASELINE), "other": _same(fx.FOLLOW_UP, patient_name="Other, Person")}
    history, notes, summary, _ = _run(tmp_path, ["ours", "other"], pages)
    assert [h["date_display"] for h in history] == ["05/12/2025"]
    assert summary[1].startswith("  STAFF REVIEW - DEXA PATIENT NAME MISMATCH: DEXA file 1 page 2")
    extracted = {"name": fx.PATIENT, "dexa_history": history, "dexa_summary": summary}
    _, notice = pipeline.score_and_build_record(extracted)
    text = format_review_notice(notice)
    assert text.splitlines()[2] == summary[0]
    assert "Other" not in text


def test_vat_only_date_is_kept_when_both_reads_have_it(tmp_path):
    history, notes, _, _ = _run(tmp_path, ["trend"], {"trend": _same(fx.BASELINE, fx.VAT_ONLY)})
    vat_only = history[-1]
    assert vat_only["date_display"] == "03/09/2026" and vat_only["vat_fat_mass_lb"] == 0.77
    assert vat_only["total_mass_lb"] is None and vat_only["body_fat_pct"] is None
    assert notes == []
    history, notes, _, _ = _run(tmp_path, ["trend"], {"trend": (fx.read([fx.BASELINE, fx.VAT_ONLY]),
                                                                 fx.read([fx.BASELINE]))}, name="one.pdf")
    assert [h["date_display"] for h in history] == ["05/12/2025"]
    assert any("03/09/2026 excluded - date gate" in note for note in notes)


def test_estimated_values_are_kept_flagged_and_labelled(tmp_path):
    estimated = copy.deepcopy(fx.FOLLOW_UP)
    estimated["vat_mass"] = "0.88 (e)"
    history, _, _, _ = _run(tmp_path, ["summary"], {"summary": _same(fx.BASELINE, estimated)})
    assert history[1]["vat_fat_mass_lb"] == 0.88 and history[1]["estimated"] == ["vat_fat_mass_lb"]
    mixed = copy.deepcopy(fx.FOLLOW_UP)
    history2, notes, _, _ = _run(tmp_path, ["summary"], {"summary": (fx.read([estimated]), fx.read([mixed]))},
                                 name="mixed.pdf")
    assert history2[0]["vat_fat_mass_lb"] is None and "independent reads disagree" in notes[0]
    record, _ = pipeline.score_and_build_record({"name": "Synthetic", "sex": "male", "dexa_history": history})
    html = template.dexa_panel(record, {"headlines": {"Structure": "Tracked."}}, {"Structure": {"now": 80}}, None)
    assert '0.88 lb VAT <span class="dexa-est">estimated</span>' in html


def _two_page(tmp_path, labels, name):
    pages = {"summary": _same(fx.FOLLOW_UP), "trend": _same(fx.BASELINE, fx.FOLLOW_UP, fx.VAT_ONLY)}
    history, _, _, _ = _run(tmp_path, labels, pages, name=name)
    record, _ = pipeline.score_and_build_record({"name": "Synthetic", "sex": "male", "dexa_history": history})
    return history, template.dexa_panel(record, {"headlines": {"Structure": "Tracked."}}, {"Structure": {"now": 80}},
                                        None)


def test_shuffled_page_order_gives_identical_output(tmp_path):
    forward = _two_page(tmp_path, ["summary", "trend"], "forward.pdf")
    backward = _two_page(tmp_path, ["trend", "summary"], "backward.pdf")
    assert forward == backward
    assert [h["date_display"] for h in forward[0]] == ["05/12/2025", "02/03/2026", "03/09/2026"]


def test_same_input_twice_gives_identical_output(tmp_path):
    assert _two_page(tmp_path, ["summary", "trend"], "a.pdf") == _two_page(tmp_path, ["summary", "trend"], "b.pdf")


def test_pages_that_print_different_values_for_one_date_drop_that_field(tmp_path):
    other = copy.deepcopy(fx.FOLLOW_UP)
    other["lean_mass"] = "111.0"
    history, notes, _, _ = _run(tmp_path, ["summary", "trend"],
                                {"summary": _same(fx.FOLLOW_UP), "trend": _same(other)})
    assert history[0]["lean_mass_lb"] is None and history[0]["fat_mass_lb"] == 50.5
    assert notes == ["DEXA scan 02/03/2026 lean mass (lb) excluded - pages DEXA file 1 page 1, DEXA file 1 page 2 "
                     "print different values"]


def test_one_log_line_per_page_without_names_or_values(tmp_path, capsys):
    pages = {"ours": _same(fx.BASELINE), "other": _same(fx.FOLLOW_UP, patient_name="Other, Person"),
             "cover": _same()}
    _run(tmp_path, ["ours", "other", "cover"], pages)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("source=dexa")]
    assert [line.split()[1:3] for line in lines] == [["file=1", "page=1"], ["file=1", "page=2"],
                                                      ["file=1", "page=3"]]
    for line in lines:
        for secret in ("Synthetic", "Other", "172", "58.0", "33.7", "05/12/2025", "2025"):
            assert secret not in line
    assert "verdict=notice" in lines[2]


def test_invalid_transcription_excludes_only_that_page(tmp_path):
    bad = fx.read([fx.BASELINE])
    bad["scans"][0]["unexpected"] = "x"
    history, _, summary, _ = _run(tmp_path, ["bad", "good"], {"bad": (bad, bad), "good": _same(fx.FOLLOW_UP)})
    assert [h["date_display"] for h in history] == ["02/03/2026"]
    assert any("DEXA file 1 page 1: schema gate" in line for line in summary)


def test_scanned_dexa_has_no_verbatim_hallucination_warning():
    extracted = {"dexa_history": [{"date_display": "05/12/2025", "body_fat_pct": "33.7%"}]}
    for dexa_text in ("", "Synthetic cover page"):
        notice = pipeline.verify_extraction_completeness(extracted, dexa_text=dexa_text, dexa_scanned=True)
        assert notice.other_notes == []
    notice = pipeline.verify_extraction_completeness(extracted, dexa_text="Scan 05/12/2025 Body Fat 31.0%")
    assert any("POSSIBLE HALLUCINATION" in note for note in notice.other_notes)


def test_end_to_end_report_uses_gated_dexa_and_keeps_staff_text_out(tmp_path, monkeypatch):
    from synthetic_fixtures import deterministic_fixtures as labs_fx
    labels = ["summary", "trend"]
    path = fx.write_pdf(tmp_path / "dexa.pdf", labels)
    pages = {"summary": _same(fx.FOLLOW_UP), "trend": _same(fx.BASELINE, fx.FOLLOW_UP)}
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: fx.DexaReader(path, labels, pages))
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(tmp_path / "diag"))
    labs = labs_fx.write_lab_pdf(tmp_path / "labs.pdf", [labs_fx.section_preamble("SYN-D", "02/03/2026")
                                                         + labs_fx.header_line(100) + labs_fx.row(130, "TSH", "1.0")])
    out = tmp_path / "r.pdf"
    pipeline.run(str(labs), [str(path)], None, fx.PATIENT, 44, "male", str(out))
    review = (tmp_path / "review.txt").read_text(encoding="utf-8")
    assert review.splitlines()[2] == "DEXA - 2 page(s) read twice; 2 scan date(s) kept"
    assert "HALLUCINATION" not in review
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    assert "33.7%" in text and "30.4%" in text and "source=dexa" not in text


@pytest.mark.parametrize(("text", "expected"), [
    ("34.4 %", (34.4, "34.4", False)), ("1.23 (e)", (1.23, "1.23", True)), ("(e) 84.0 cm2", (84.0, "84.0", True)),
    ("172 lb", (172.0, "172", False)), (None, None),
])
def test_measurement_normalization(text, expected):
    assert scan_dexa.measure(text) == expected


@pytest.mark.parametrize("text", ["34,4", "about 30", "1.2.3", "30 kg"])
def test_unreadable_measurement_is_rejected(text):
    with pytest.raises(ValueError):
        scan_dexa.measure(text)
