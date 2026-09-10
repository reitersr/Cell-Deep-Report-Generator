"""
CellDeep Report Generator — Known Protocol / Compound Reference
===================================================================
Same discipline as markers_reference.py: a deterministic lookup, not an
open-ended guess. When a provider's note names a compound, this table gives
the pipeline its TYPICAL target system(s) and whether it's expected to show
up in bloodwork at all.

IMPORTANT — this is a starting point, not a substitute for the note itself.
A compound's actual target category for a given patient should be narrowed
by the generation step to whichever of its typical categories actually has
a moderate/flagged marker for THAT patient (exactly how Omega-3 HP-D was
tied to Flow for Star specifically, because Lp-PLA2 and Omega-3 Index were
her actual flagged/moderate markers there — not because Omega-3 always
means Flow for every patient). If a compound's typical categories don't
overlap with anything moderate/flagged for this patient, generation should
state the compound's purpose plainly without forcing a category link.

Per calibration: protocol accuracy is the provider's responsibility. This
table exists so the tool doesn't reinvent a compound's general purpose from
scratch each time — it does not second-guess what the provider prescribed
or why.

List sourced directly from Kerry's stated prescribing set (not exhaustive —
extend this table the same way a new marker gets added: once, deliberately).
"""

PROTOCOL_LIBRARY = {
    "BPC-157": dict(
        typical_categories=["Repair"],
        lab_visible=False,
        typical_cadence="daily",
        purpose_hint="tissue repair and recovery support",
    ),
    "Testosterone": dict(
        typical_categories=["Drive"],
        lab_visible=True,
        typical_cadence="weekly",
        purpose_hint="hormone optimization",
    ),
    "Progesterone": dict(
        typical_categories=["Drive"],
        lab_visible=True,
        typical_cadence="daily",
        purpose_hint="hormone balance",
    ),
    "CJC-1295/Ipamorelin": dict(
        typical_categories=["Repair", "Reserves"],
        lab_visible=False,
        typical_cadence="daily",
        purpose_hint="growth hormone support, recovery and sleep quality",
        aliases=["cjc-1295", "ipamorelin", "cjc-1295 / ipamorelin", "cjc 1295/ipamorelin"],
    ),
    "Enclomiphene": dict(
        typical_categories=["Drive"],
        lab_visible=True,
        typical_cadence="daily",
        purpose_hint="natural testosterone support",
    ),
    "Klow peptide blend": dict(
        typical_categories=["Repair"],
        lab_visible=False,
        typical_cadence="daily",
        purpose_hint="tissue repair, skin health, recovery",
        aliases=["klow", "ghk-cu", "ghk-cu/klow", "glow peptide", "glow", "ghk-cu/glow"],
    ),
    "MOTS-c": dict(
        typical_categories=["Fuel"],
        lab_visible=True,
        typical_cadence="as directed",
        purpose_hint="metabolic and mitochondrial support",
    ),
    "T3/T4": dict(
        typical_categories=["Pace"],
        lab_visible=True,
        typical_cadence="daily",
        purpose_hint="thyroid support",
    ),
    "Semax": dict(
        typical_categories=[],   # deliberately empty — cognitive support, not expected to move labs
        lab_visible=False,
        typical_cadence="as directed",
        purpose_hint="mental clarity and processing speed",
    ),
    "Tesamorelin": dict(
        typical_categories=["Structure", "Fuel"],
        lab_visible=False,   # primarily visible via DEXA, not bloodwork
        typical_cadence="daily",
        purpose_hint="visceral fat reduction, body composition",
    ),
    "Omega-3 HP-D": dict(
        typical_categories=["Flow", "Reserves"],
        lab_visible=True,
        typical_cadence="daily",
        purpose_hint="lipid and inflammation support",
    ),
    "Retatrutide": dict(
        typical_categories=["Fuel"],
        lab_visible=True,
        typical_cadence="weekly",
        purpose_hint="metabolic health, appetite regulation, body composition",
    ),
    "Vitamin D-3": dict(
        typical_categories=["Reserves"],
        lab_visible=True,
        typical_cadence="daily",
        purpose_hint="foundational vitamin support",
    ),
}


def lookup_protocol_item(raw_name: str):
    """Case-insensitive match against canonical names + aliases. Returns (canonical_name, config) or None.
    A miss means generation describes the compound using only what the provider's note itself says about
    it — never inventing a category link for a compound not in this table."""
    q = raw_name.strip().lower()
    for canonical, cfg in PROTOCOL_LIBRARY.items():
        if q == canonical.lower():
            return canonical, cfg
        if q in [a.lower() for a in cfg.get("aliases", [])]:
            return canonical, cfg
    return None
