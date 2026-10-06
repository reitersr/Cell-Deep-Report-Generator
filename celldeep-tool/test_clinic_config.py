"""All clinic-decision defaults for censored results, lab flags and range display live in one file."""

import ast
import re
from pathlib import Path

import clinic_config

ROOT = Path(clinic_config.__file__).parent
SETTINGS = [name for name in vars(clinic_config) if name.isupper()]


def test_every_setting_has_its_own_comment():
    lines = (ROOT / "clinic_config.py").read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    assigned = [node for node in tree.body if isinstance(node, ast.Assign)]
    assert {node.targets[0].id for node in assigned} == set(SETTINGS)
    for node in assigned:
        assert lines[node.lineno - 2].startswith("# "), f"{node.targets[0].id} needs a comment directly above it"


def test_wording_defaults_are_not_duplicated_in_code():
    texts = [value for name in SETTINGS for value in
             ([getattr(clinic_config, name)] if isinstance(getattr(clinic_config, name), str) else [])
             if len(value) > 12 and "{" not in value]
    for path in ROOT.glob("*.py"):
        if path.name == "clinic_config.py" or path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8")
        for text in texts:
            assert text not in source, f"{text!r} is hard-coded in {path.name}; use clinic_config"


def test_settings_cover_items_two_three_and_five():
    assert {"LAB_FLAG_WORDS", "CENSORED_CHIP_LABEL", "UNSCORABLE_CHIP_LABEL", "CENSORED_SUMMARY_LABEL",
            "SHOW_LAB_FLAG_WHEN_IT_DIFFERS", "LAB_FLAG_DISAGREES_WITH", "LAB_FLAG_LINE", "LAB_RANGE_LABEL",
            "NO_RANGE_LABEL", "NO_RANGE_CHIP_LABEL"} <= set(SETTINGS)
    assert re.fullmatch(r".*\{flag\}.*\{lab_range\}.*", clinic_config.LAB_FLAG_LINE)


def test_disagreement_rule_follows_the_setting(monkeypatch):
    import template
    from schema import Marker
    marker = Marker(name="Ferritin", category="Foundational", unit="", kind="range", disp_range="",
                    now=20.0, disp_now="20", now_tier="moderate", lab_flag_now="L",
                    lab_range_now={"lo": 38, "hi": 380, "display": "38-380"})
    assert not template.lab_flag_differs(marker)
    monkeypatch.setattr(clinic_config, "LAB_FLAG_DISAGREES_WITH", ("optimal", "moderate"))
    assert template.lab_flag_differs(marker)
    assert "Lab flag: Low (lab range 38-380)" in template._lab_flag_line(marker)
