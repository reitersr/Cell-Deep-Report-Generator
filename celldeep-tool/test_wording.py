"""No count-based sentence produces a grammar mismatch for counts 0, 1 and 2."""

import itertools
import re

import pytest

from generation_prompt import build_copy, plural
from schema import Marker, PatientRecord

NOUNS = r"(?:more )?(?:scored )?(?:marker|result|scan|system|domain|item)"
# A count followed by a noun that disagrees with it, or by a verb that disagrees with it.
MISMATCHES = [
    re.compile(rf"\b1 {NOUNS}s\b"),
    re.compile(rf"\b(?:0|[2-9]|\d{{2,}}) {NOUNS}(?!s)\b"),
    re.compile(rf"\b1 {NOUNS}\b[^.,:;]*?\b(?:are|need|were|have)\b"),
    re.compile(rf"\b(?:0|[2-9]|\d{{2,}}) {NOUNS}s\b[^.,:;]*?\b(?:is|needs|was|has)\b"),
    re.compile(r"\bAll [01] "),
    re.compile(r"\b(?:0|[2-9]) is\b|\b1 are\b"),
    re.compile(r"\b1 result was\b[^<]*\bThese are\b|\b(?:0|[2-9]) results were\b[^<]*\bIt is\b"),
]


def _marker(name, category, tier, censored=False):
    if censored:
        return Marker(name=name, category=category, unit="", kind="range", disp_range="", now=None,
                      disp_now="<0.5", now_date_display="04/14/2026")
    return Marker(name=name, category=category, unit="", kind="range", disp_range="", now=1.0, disp_now="1.0",
                  now_date_display="04/14/2026", now_tier=tier, now_pct={"optimal": 96, "moderate": 70,
                                                                           "flag": 20}[tier])


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _assert_grammatical(copy):
    for text in _strings(copy):
        for pattern in MISMATCHES:
            assert not pattern.search(text), f"{pattern.pattern!r} matched: {text!r}"


@pytest.mark.parametrize(("optimal", "moderate", "flag", "censored"),
                         list(itertools.product((0, 1, 2), repeat=4)))
def test_summary_and_system_copy_agree_with_every_count(optimal, moderate, flag, censored):
    markers = ([_marker(f"Optimal {i}", "Thyroid", "optimal") for i in range(optimal)]
               + [_marker(f"Moderate {i}", "Thyroid", "moderate") for i in range(moderate)]
               + [_marker(f"Flagged {i}", "Thyroid", "flag") for i in range(flag)]
               + [_marker(f"Censored {i}", "Thyroid", None, censored=True) for i in range(censored)])
    _assert_grammatical(build_copy(PatientRecord(name="Synthetic", markers=markers)))


@pytest.mark.parametrize("extra", [0, 1, 2])
def test_more_markers_need_attention_sentence(extra):
    markers = [_marker(f"Flagged {i}", "Thyroid", "flag") for i in range(3 + extra)]
    story = build_copy(PatientRecord(name="Synthetic", markers=markers))["box_stories"]["Pace"]
    _assert_grammatical({"story": story})
    if extra == 1:
        assert story.endswith("1 more marker in this system needs attention.")
    if extra == 2:
        assert story.endswith("2 more markers in this system need attention.")


def test_where_you_are_now_keeps_every_count():
    markers = ([_marker("A", "Thyroid", "optimal")] * 2 + [_marker("B", "Thyroid", "moderate")]
               + [_marker("C", "Lipids", "flag")] * 0)
    bullets = build_copy(PatientRecord(name="Synthetic", markers=markers))["optimization_summary_bullets"]
    assert ("<b>Where you are now:</b> Of your 3 scored markers, 2 are optimal, 1 is moderate and 0 are flagged."
            in bullets)
    single = build_copy(PatientRecord(name="Synthetic", markers=[_marker("A", "Thyroid", "flag")]))
    assert "<b>Where you are now:</b> Your 1 scored marker is flagged." in single["optimization_summary_bullets"]


def test_system_story_for_one_zero_and_two_scored_markers():
    one = build_copy(PatientRecord(name="S", markers=[_marker("A", "Thyroid", "optimal")]))
    assert one["box_stories"]["Pace"] == "Your 1 scored marker in this system is optimal."
    two = build_copy(PatientRecord(name="S", markers=[_marker("A", "Thyroid", "optimal")] * 2))
    assert two["box_stories"]["Pace"] == "All 2 scored markers in this system are optimal."
    none = build_copy(PatientRecord(name="S", markers=[_marker("A", "Thyroid", None, censored=True)]))
    assert none["box_stories"]["Pace"] == "No scored markers in this system this round."


@pytest.mark.parametrize(("count", "expected"), [(0, "are"), (1, "is"), (2, "are")])
def test_plural_helper(count, expected):
    assert plural(count, "is", "are") == expected


@pytest.mark.parametrize("bad", [
    "1 more marker in this system need attention.",
    "All 1 scored marker in this system are optimal.",
    "2 more marker to recheck",
    "1 result was reported by the lab as a limit: FSH. These are shown as printed.",
    "Of your 2 scored markers, 0 is moderate",
])
def test_checker_catches_the_old_mismatches(bad):
    with pytest.raises(AssertionError):
        _assert_grammatical({"text": bad})
