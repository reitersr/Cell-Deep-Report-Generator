"""Same inputs, same report, without relying on sampling settings: when the two reads of a scanned
row disagree, the row is excluded and listed - never kept in one run and dropped in another by chance.
Synthetic data; the vision model is a stub."""

import copy
import json
import random
import re
from types import SimpleNamespace

import fitz

import pipeline
import scan_bloodwork
from synthetic_fixtures import deterministic_fixtures as fx
from test_scan_bloodwork import payloads
from unknown_marker_policy import without_staff_check
from synthetic_fixtures.sdk_contract import check_create_kwargs

UNSTABLE = "INSULIN"  # the row the stub's second read gets wrong, differently every run


class DisagreeingVision:
    """All reads of every scanned page agree, except one row: the second and third reads each print a random
    other value every run (the way a sampled read can differ), never the same one, so no two reads agree."""

    def __init__(self, rng):
        self.rng, self.calls, self.messages, self.timeout = rng, [], self, 240.0
        self.pages = {page["page"]: page for page in payloads()}

    def create(self, **kwargs):
        check_create_kwargs(kwargs)  # the installed SDK must accept this call
        self.calls.append(kwargs)
        number = int(re.search(r"page (\d+)", kwargs["messages"][0]["content"][1]["text"])[1])
        reading = sum(f"page {number} independently" in call["messages"][0]["content"][1]["text"]
                      for call in self.calls)  # 1 for the first read of this page, 2 for the second
        page = copy.deepcopy(self.pages[number])
        page.setdefault("date_of_birth", None)
        if reading == 2:
            self.wrong = self.rng.sample(["2.8", "3.4", "<3", "13.1", "8.1"], 2)
        if reading in (2, 3):
            for row in page["rows"]:
                if row["name"] == UNSTABLE:
                    row["result_text"] = self.wrong[reading - 2]
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(page))], stop_reason="end_turn")


class OneOutlierVision(DisagreeingVision):
    """Only the second read of one row is wrong (a random value each run); the third read agrees with the
    first, so two of three reads agree and the row is kept, the same in every run."""

    def create(self, **kwargs):
        response = super().create(**kwargs)
        number = int(re.search(r"page (\d+)", kwargs["messages"][0]["content"][1]["text"])[1])
        if sum(f"page {number} independently" in call["messages"][0]["content"][1]["text"]
               for call in self.calls) == 3:
            page = copy.deepcopy(self.pages[number])
            page.setdefault("date_of_birth", None)
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(page))], stop_reason="end_turn")
        return response


def _run(tmp_path, monkeypatch, attempt, vision_class=None):
    folder = tmp_path / f"run-{attempt}"
    folder.mkdir()
    labs = fx.write_lab_pdf(folder / "labs.pdf", [
        fx.section_preamble("SYN-DET", "04/14/2026") + fx.header_line(100) + fx.row(130, "hs-CRP", "0.4", units="mg/L"), [], []])
    vision = (vision_class or DisagreeingVision)(random.Random())  # unseeded: different wrong values every run
    monkeypatch.setattr(pipeline, "Anthropic", lambda **kwargs: vision)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(folder / "review.txt"))
    monkeypatch.setattr(pipeline, "_diagnostic_path_prefix", lambda name: str(folder / "diag"))
    out = folder / "report.pdf"
    pipeline.run(str(labs), [], None, "Synthetic, Pat", 44, "male", str(out), collected_date="04/14/2026")
    with fitz.open(out) as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    return text, (folder / "review.txt").read_text(encoding="utf-8"), vision.calls


def test_five_runs_with_a_disagreeing_read_give_the_same_report(tmp_path, monkeypatch):
    runs = [_run(tmp_path, monkeypatch, attempt) for attempt in range(5)]
    texts = [text for text, _, _ in runs]
    assert all(text == texts[0] for text in texts)  # the patient report is identical every run
    assert "Fasting Insulin" not in texts[0]  # the disagreeing row is never kept, in any run
    for _, review, calls in runs:
        notes = without_staff_check(review).splitlines()
        excluded = [line for line in notes if line.startswith("INCOMPLETE - row excluded: INSULIN")]
        assert len(excluded) == 1
        assert re.fullmatch(r"INCOMPLETE - row excluded: INSULIN \(reads disagree: 3\.1 / \S+ / \S+\) - scanned lab "
                            r"page \d; not in this report", excluded[0])
        assert "rows excluded (reads disagree): 1" in review  # and counted in the STAFF CHECK
        # Determinism comes from the agreement rule, not sampling settings (the SDK rejects them anyway).
        assert not any({"temperature", "top_p", "top_k"} & set(call) for call in calls)


def test_disagreeing_values_never_reach_the_logs(tmp_path, monkeypatch, capsys):
    _run(tmp_path, monkeypatch, 0)
    log = capsys.readouterr().out
    assert "reads disagree: 3.1" not in log
    assert "agreement gate" in log  # the exclusion itself is logged, value-free


def test_lab_code_tie_break_does_not_depend_on_set_order():
    names = ["TESTOSTERONE, TOTAL, MS AB", "INSULIN (CD)"]
    for codes in ({"AB", "CD", "EF"}, {"EF", "CD", "AB"}):
        assert [scan_bloodwork.strip_lab_code(name, codes) for name in names] == [
            "TESTOSTERONE, TOTAL, MS", "INSULIN"]


def test_five_runs_with_one_wrong_read_keep_the_same_value(tmp_path, monkeypatch):
    runs = [_run(tmp_path, monkeypatch, attempt, OneOutlierVision) for attempt in range(5)]
    texts = [text for text, _, _ in runs]
    assert all(text == texts[0] for text in texts)
    assert "Fasting Insulin" in texts[0]  # two of three reads print 3.1: kept in every run
    for _, review, calls in runs:
        assert "INCOMPLETE - row excluded" not in review
        assert "rows excluded (reads disagree): 0" in review
        assert len(calls) == 5  # page 1 is digital; pages 2 and 3 read twice, the disagreeing page a third time


def test_a_heading_read_only_once_is_not_an_excluded_result():
    """A section heading with no value ("CBC (INCLUDES DIFF/PLT)") transcribed by one read only is not a
    result: it is not listed as excluded and never reaches the confirmation step."""
    page = copy.deepcopy(payloads()[0])
    page.setdefault("date_of_birth", None)
    heading = {**page["rows"][0], "name": "CBC (INCLUDES DIFF/PLT)", "result_text": None, "flag": None,
               "reference_range": None, "column": None}
    first = copy.deepcopy(page)
    first["rows"].insert(0, heading)
    exclusions = []
    accepted, notes = scan_bloodwork.gate_staff_identified_reads(
        [[first, copy.deepcopy(page)]], "04/14/2026", pipeline._CELL_VALUE_RE, "Synthetic, Pat",
        row_exclusions=exclusions)
    assert exclusions == []
    assert len(accepted) == len(page["rows"])
    assert any("'CBC (INCLUDES DIFF/PLT)': no read prints a result for it" in note for note in notes)
    assert not scan_bloodwork.reads_disagree([first, copy.deepcopy(page)])  # no third read for a heading
    # A row one read prints a value for is a result: the reads disagree on it.
    second = copy.deepcopy(page)
    second["rows"].insert(0, {**heading, "result_text": "4.2"})
    assert scan_bloodwork.reads_disagree([first, second])
