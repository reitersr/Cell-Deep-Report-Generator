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
    """Both reads of every scanned page agree, except the second read of one row, which prints a random
    other value each run (the way a sampled read can differ)."""

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
            for row in page["rows"]:
                if row["name"] == UNSTABLE:
                    row["result_text"] = self.rng.choice(["2.8", "3.4", "<3", "13.1", "8.1"])
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(page))], stop_reason="end_turn")


def _run(tmp_path, monkeypatch, attempt):
    folder = tmp_path / f"run-{attempt}"
    folder.mkdir()
    labs = fx.write_lab_pdf(folder / "labs.pdf", [
        fx.section_preamble("SYN-DET", "04/14/2026") + fx.header_line(100) + fx.row(130, "hs-CRP", "0.4", units="mg/L"), [], []])
    vision = DisagreeingVision(random.Random())  # unseeded: a different wrong value every run
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
        assert re.fullmatch(r"INCOMPLETE - row excluded: INSULIN \(reads disagree: 3\.1 / \S+\) - scanned lab page "
                            r"\d; not in this report", excluded[0])
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
