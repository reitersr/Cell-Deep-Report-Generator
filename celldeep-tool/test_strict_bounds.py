"""Strict "<" / ">" thresholds are exclusive and "<=" / ">=" ("≤" / "≥") inclusive, everywhere: a value equal to a
strict optimal cutoff is never optimal (Apolipoprotein B 90 against "optimal <90" is moderate), and where the lab
prints the next band as ">=" a value equal to that cutoff is in it (Triglycerides 200: lab High ">=200")."""

import re

import pytest

import markers_reference as mr
import scoring


def _bounded():
    for name, cfg in mr.MARKER_LIBRARY.items():
        for sex, variant in [(None, {})] + list((cfg.get("sex_variants") or {}).items()):
            resolved = {**cfg, **variant}
            if resolved.get("kind") == "bounded":
                yield name, sex, resolved


@pytest.mark.parametrize("name,sex,cfg", list(_bounded()), ids=lambda v: str(v)[:30])
def test_every_bounded_cutoff_scores_the_way_its_sign_reads(name, sex, cfg):
    sign = re.search(r"(<=|>=|≤|≥|<|>)", cfg["disp_range"])
    assert sign, f"{name}: no sign in {cfg['disp_range']!r}"
    strict = sign[1] in ("<", ">")
    assert cfg.get("inclusive", True) is not strict, f"{name}: {cfg['disp_range']!r} but inclusive={cfg.get('inclusive', True)}"


def test_a_value_equal_to_a_strict_cutoff_is_not_optimal():
    apob = mr.MARKER_LIBRARY["Apolipoprotein B"]
    assert scoring.score_bounded(90, "lower", apob["optimal"], apob["moderate"], apob["inclusive"])[1] == "moderate"
    assert scoring.score_bounded(89, "lower", apob["optimal"], apob["moderate"], apob["inclusive"])[1] == "optimal"
    hdl_p = mr.MARKER_LIBRARY["HDL-P"]  # "optimal >32.8"
    assert scoring.score_bounded(32.8, "higher", hdl_p["optimal"], hdl_p["moderate"], hdl_p["inclusive"])[1] == \
        "moderate"
    hdl = mr.MARKER_LIBRARY["HDL Cholesterol"]  # "optimal ≥50": inclusive
    assert scoring.score_bounded(50, "higher", hdl["optimal"], hdl["moderate"], hdl.get("inclusive", True))[1] == \
        "optimal"


def test_a_value_equal_to_the_labs_high_cutoff_is_flagged():
    for name, high in (("Triglycerides", 200), ("Non-HDL Cholesterol", 190)):
        cfg = mr.MARKER_LIBRARY[name]
        tier = scoring.score_bounded(high, "lower", cfg["optimal"], cfg["moderate"], cfg["inclusive"],
                                     cfg["moderate_inclusive"])[1]
        assert tier == "flag", name
        assert scoring.score_bounded(high - 1, "lower", cfg["optimal"], cfg["moderate"], cfg["inclusive"],
                                     cfg["moderate_inclusive"])[1] == "moderate"
