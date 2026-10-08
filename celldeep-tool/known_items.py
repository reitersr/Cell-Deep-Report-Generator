"""The clinic's known-items allowlist (config/known_items.json): printed test names the stop screen does not need to
ask about, because the clinic already decided how each is handled ("lab-reported" or "excluded"). Matching is exact
after case and whitespace normalization only - no fuzzy, substring or OCR-lookalike matching - so a near-miss name
always reaches staff. Every known item is still listed in the QA file ("Auto-accepted (known)"); nothing is hidden.
Adding a name requires clinic approval (docs/open_decisions.md). Used only while
clinic_config.KNOWN_ITEMS_AUTOPROCEED is True (default False: never read)."""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "known_items.json"
LAB_REPORTED, EXCLUDED = "lab-reported", "excluded"


def normalize(text) -> str:
    """Case and whitespace only: "  chol/hdl-c " == "Chol/HDL-C"; nothing else is folded."""
    return " ".join(str(text or "").split()).casefold()


def _result_key(text) -> str:
    # A printed result compares after case and whitespace normalization and one trailing colon ("SEE NOTE:").
    return normalize(text).removesuffix(":").strip()


def load(path=CONFIG_PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"lab_reported": {normalize(name) for name in data.get("lab_reported_known", [])},
            "excluded": {normalize(name) for name in data.get("excluded_known", [])},
            "excluded_when_result": {normalize(name): {_result_key(result) for result in results}
                                     for name, results in data.get("excluded_known_when_result", {}).items()}}


def handling(name, handled_as, results=(), known=None):
    """How the clinic list handles this printed test, when it matches the way the tool already handled it:
    "lab-reported" for a name on lab_reported_known shown lab-reported; "excluded" for a name on excluded_known left
    out, or on excluded_known_when_result left out with every printed result one of its listed results. None for
    anything else (staff review it)."""
    known = known or load()
    key = normalize(name)
    if handled_as == LAB_REPORTED:
        return LAB_REPORTED if key in known["lab_reported"] else None
    if key in known["excluded"]:
        return EXCLUDED
    allowed = known["excluded_when_result"].get(key)
    printed = {_result_key(result) for result in results}
    return EXCLUDED if allowed and printed and printed <= allowed else None
