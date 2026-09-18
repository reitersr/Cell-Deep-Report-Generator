"""
CellDeep Report Generator — Known Marker Reference Library
=============================================================
This is a DETERMINISTIC lookup table, not AI-generated. Extraction's job is
only to recognize which known marker a lab value corresponds to (by name,
allowing for common aliases/formatting differences across lab vendors) and
pull the value out. The scoring configuration (what counts as optimal,
moderate, flagged) comes from HERE — the same clinical definitions we
calibrated and corrected against Star Hawkins' real CHL panel and Priscilla's
brand-approved tier logic. This keeps scoring consistent patient to patient,
lab to lab, rather than re-derived per document.

If extraction encounters a marker NOT in this table, that is a real, expected
case (different lab, different panel) — see unknown_marker_policy.py for how
the pipeline handles it. It is NEVER silently invented here.

Corrections applied during calibration (verified against Star's real CHL PDF,
not the original invented thresholds):
  - Lp-PLA2 Activity: binary optimal/high, no moderate zone (moderate == optimal)
  - Myeloperoxidase: moderate ceiling corrected to 539
  - Oxidized LDL: moderate ceiling corrected to 69
  - Non-HDL Cholesterol: moderate ceiling corrected to 190
  - Insulin Resistance Score: moderate ceiling corrected to 66
"""

# kind: "bounded" (optimal/moderate cutoffs + direction) | "range" (lo/hi reference band) | "categorical" (pass/fail)
# aliases: alternate names/spellings this marker might appear under in a different lab's PDF —
#          extraction matches against these case-insensitively before giving up.

MARKER_LIBRARY = {

    # ---- Inflammation ----
    "hs-CRP": dict(category="Inflammation", unit="mg/L", kind="bounded",
        direction="lower", optimal=1.0, moderate=3.0, disp_range="optimal <1.0",
        aliases=["hs-crp", "high sensitivity crp", "c-reactive protein, high sensitivity"]),
    "Myeloperoxidase": dict(category="Inflammation", unit="pmol/L", kind="bounded",
        direction="lower", optimal=470, moderate=539, disp_range="optimal <470",
        aliases=["mpo", "myeloperoxidase"]),
    "Lp-PLA2 Activity": dict(category="Inflammation", unit="nmol/min/mL", kind="bounded",
        direction="lower", optimal=123, moderate=123, disp_range="optimal \u2264123",
        aliases=["lp-pla2", "lp pla2 activity", "lipoprotein-associated phospholipase a2"]),
    "ADMA": dict(category="Inflammation", unit="ng/mL", kind="bounded",
        direction="lower", optimal=100, moderate=120, disp_range="optimal <100",
        aliases=["adma", "asymmetric dimethylarginine"]),
    "Oxidized LDL": dict(category="Inflammation", unit="U/L", kind="bounded",
        direction="lower", optimal=60, moderate=69, disp_range="optimal <60",
        aliases=["oxidized ldl", "ox-ldl", "oxldl"]),
    "Fibrinogen": dict(category="Inflammation", unit="mg/dL", kind="bounded",
        direction="lower", optimal=350, moderate=350, disp_range="optimal <350",
        aliases=["fibrinogen"]),

    # ---- Lipids ----
    "Total Cholesterol": dict(category="Lipids", unit="mg/dL", kind="bounded",
        direction="lower", optimal=200, moderate=240, disp_range="optimal <200",
        aliases=["total cholesterol", "cholesterol, total"]),
    "HDL Cholesterol": dict(category="Lipids", unit="mg/dL", kind="bounded",
        direction="higher", optimal=50, moderate=40, disp_range="optimal \u226550",
        aliases=["hdl", "hdl cholesterol", "hdl-c"]),
    "Triglycerides": dict(category="Lipids", unit="mg/dL", kind="bounded",
        direction="lower", optimal=150, moderate=200, disp_range="optimal <150",
        aliases=["triglycerides", "trig"]),
    "LDL Cholesterol": dict(category="Lipids", unit="mg/dL", kind="bounded",
        direction="lower", optimal=100, moderate=129, disp_range="optimal <100",
        aliases=["ldl", "ldl cholesterol", "ldl-c", "ldl (calc)"]),
    "Non-HDL Cholesterol": dict(category="Lipids", unit="mg/dL", kind="bounded",
        direction="lower", optimal=130, moderate=190, disp_range="optimal <130",
        aliases=["non-hdl cholesterol", "non hdl", "non-hdl-c"]),
    "LDL-P (particle count)": dict(category="Lipids", unit="nmol/L", kind="bounded",
        direction="lower", optimal=935, moderate=1816, disp_range="optimal <935",
        aliases=["ldl-p", "ldl particle number", "ldl particle count"]),
    "HDL-P": dict(category="Lipids", unit="\u00b5mol/L", kind="bounded",
        direction="higher", optimal=32.8, moderate=29.2, disp_range="optimal >32.8",
        aliases=["hdl-p", "hdl particle number"]),
    "Apolipoprotein B": dict(category="Lipids", unit="mg/dL", kind="bounded",
        direction="lower", optimal=90, moderate=129, disp_range="optimal <90",
        aliases=["apob", "apolipoprotein b", "apo b"]),
    "Lipoprotein(a)": dict(category="Lipids", unit="nmol/L", kind="bounded",
        direction="lower", optimal=75, moderate=125, disp_range="optimal <75",
        aliases=["lp(a)", "lipoprotein (a)", "lipoprotein a"]),

    # ---- Metabolic ----
    "Glucose (fasting)": dict(category="Metabolic", unit="mg/dL", kind="range",
        lo=70, hi=90, disp_range="70\u201390",
        aliases=["glucose", "glucose, fasting", "fasting glucose"]),
    "HbA1c": dict(category="Metabolic", unit="%", kind="bounded",
        direction="lower", optimal=5.7, moderate=6.4, disp_range="optimal <5.7%",
        aliases=["hba1c", "hemoglobin a1c", "a1c"]),
    "TMAO": dict(category="Metabolic", unit="\u00b5M", kind="bounded",
        direction="lower", optimal=6.2, moderate=9.9, disp_range="optimal <6.2",
        aliases=["tmao", "trimethylamine n-oxide"]),
    "Insulin Resistance Score": dict(category="Metabolic", unit="", kind="bounded",
        direction="lower", optimal=33, moderate=66, disp_range="sensitive <33",
        aliases=["insulin resistance score", "ir score"]),
    "Fasting Insulin": dict(category="Metabolic", unit="\u00b5IU/mL", kind="range",
        lo=2, hi=10, disp_range="2\u201310",
        aliases=["fasting insulin", "insulin, fasting"]),

    # ---- Hormones ----
    "Cortisol, Total (AM)": dict(category="Hormones", unit="\u00b5g/dL", kind="range",
        lo=4.8, hi=19.5, disp_range="4.8\u201319.5",
        aliases=["cortisol", "cortisol, am", "cortisol total"]),
    "DHEA-S": dict(category="Hormones", unit="\u00b5g/dL", kind="range",
        lo=60.9, hi=337.0, disp_range="60.9\u2013337.0",
        aliases=["dhea-s", "dhea sulfate"]),
    "Estradiol": dict(category="Hormones", unit="pg/mL", kind="range",
        lo=15, hi=350, disp_range="phase-dependent",
        aliases=["estradiol", "e2"]),
    # Sex-conditional default range. Male default (600-900 ng/dL) sourced from a functional-medicine
    # reference range via web research, NOT confirmed against CellDeep's own clinical protocol -
    # this is a placeholder pending clinical staff review, not a finalized threshold.
    # Female default (2-45) is the pre-existing, already-calibrated value - left unchanged.
    "Testosterone, Total": dict(category="Hormones", unit="ng/dL", kind="range",
        disp_range="sex-specific default (see sex_variants)",
        default_sex="female",  # fallback used only if sex is missing/unrecognized - preserves prior behavior
        sex_variants={
            "male": dict(lo=600, hi=900, disp_range="600\u2013900"),
            "female": dict(lo=2, hi=45, disp_range="2\u201345"),
        },
        aliases=["testosterone", "testosterone, total", "total testosterone"]),

    # ---- Thyroid ----
    "TSH": dict(category="Thyroid", unit="\u00b5IU/mL", kind="range",
        lo=0.40, hi=5.50, disp_range="0.40\u20135.50",
        aliases=["tsh", "thyroid stimulating hormone"]),
    "Free T4": dict(category="Thyroid", unit="ng/dL", kind="range",
        lo=0.8, hi=1.8, disp_range="0.8\u20131.8",
        aliases=["free t4", "ft4"]),
    "Free T3": dict(category="Thyroid", unit="pg/mL", kind="range",
        lo=2.0, hi=4.4, disp_range="2.0\u20134.4",
        aliases=["free t3", "ft3"]),
    "Thyroid Peroxidase Ab": dict(category="Thyroid", unit="IU/mL", kind="bounded",
        direction="lower", optimal=35, moderate=100, disp_range="optimal <35",
        aliases=["tpo ab", "thyroid peroxidase antibody", "tpo antibody"]),

    # ---- Foundational ----
    "Vitamin D": dict(category="Foundational", unit="ng/mL", kind="bounded",
        direction="higher", optimal=90, moderate=60, disp_range="optimal 60\u201390",
        aliases=["vitamin d", "25-oh vitamin d", "vitamin d, 25-hydroxy"]),
    "Vitamin B12": dict(category="Foundational", unit="pg/mL", kind="range",
        lo=600, hi=1000, disp_range="600\u20131000",
        aliases=["vitamin b12", "b12", "cobalamin"]),
    "Omega-3 Index": dict(category="Foundational", unit="%", kind="bounded",
        direction="higher", optimal=5.5, moderate=3.8, disp_range="optimal \u22655.5",
        aliases=["omega-3 index", "omega 3 index"]),
    "Ferritin": dict(category="Foundational", unit="ng/mL", kind="range",
        disp_range="sex-specific default (see sex_variants)", default_sex="female",
        sex_variants={
            "female": dict(lo=9, hi=150, disp_range="9\u2013150"),
            "male": dict(lo=18, hi=300, disp_range="18\u2013300"),
        },
        aliases=["ferritin"]),

    # ---- Also Monitored ----
    "Creatinine": dict(category="Also Monitored", unit="mg/dL", kind="range",
        lo=0.50, hi=0.97, disp_range="0.50\u20130.97",
        aliases=["creatinine"]),
    "Troponin T, HS": dict(category="Also Monitored", unit="ng/L", kind="categorical",
        disp_range="optimal <6",
        aliases=["troponin", "troponin t", "troponin, high sensitivity", "hs troponin"]),
    "Urinalysis \u2014 Occult Blood": dict(category="Also Monitored", unit="", kind="categorical",
        disp_range="expected negative",
        aliases=["urinalysis", "occult blood", "urine occult blood"]),
}

# Data category -> patient-facing category name (matches PATIENT_TO_DATA in build_final_v7.py exactly)
DATA_TO_PATIENT_CATEGORY = {
    "Hormones": "Drive",
    "Thyroid": "Pace",
    "Metabolic": "Fuel",
    "Lipids": "Flow",
    "Inflammation": "Repair",
    "Foundational": "Reserves",
    # "Also Monitored" has no patient-facing category — it's general screening, shown only in the full panel
}

# Narrative editorial override, locked during calibration: Lp-PLA2 speaks to cholesterol handling
# (per the original Volume 1 report's own reasoning), so it's grouped with Flow in patient-facing
# summaries even though its lab category is Inflammation. This is a fixed rule, not per-patient judgment.
NARRATIVE_CATEGORY_OVERRIDE = {
    "Lp-PLA2 Activity": "Flow",
}


def lookup_marker(raw_name: str):
    """Case-insensitive match against canonical names + aliases. Returns (canonical_name, config) or None.
    Extraction calls this for every marker it finds in a source document — a miss here means the marker
    goes through the unknown-marker policy, not a guess."""
    q = raw_name.strip().lower()
    for canonical, cfg in MARKER_LIBRARY.items():
        if q == canonical.lower():
            return canonical, cfg
        if q in [a.lower() for a in cfg.get("aliases", [])]:
            return canonical, cfg
    return None


# Sex values seen in practice from extraction/CLI/form input - normalizes case and common
# single-letter abbreviations before comparing against sex_variants keys ("male"/"female").
_SEX_ALIASES = {"male": "male", "m": "male", "female": "female", "f": "female"}


def resolve_marker_config(canonical: str, cfg: dict, sex: str | None) -> dict:
    """Resolve a marker's threshold config for a specific patient's sex.

    Only markers with a "sex_variants" entry (currently: Testosterone, Total) branch on sex —
    every other marker is returned unchanged. If sex is missing/unrecognized, falls back to the
    marker's "default_sex" variant rather than guessing which sex to apply - this preserves the
    pipeline's pre-existing single-range behavior instead of inferring a sex that wasn't provided.
    """
    variants = cfg.get("sex_variants")
    if not variants:
        return cfg
    key = _SEX_ALIASES.get(sex.strip().lower()) if isinstance(sex, str) else None
    if key not in variants:
        key = cfg.get("default_sex", next(iter(variants)))
    resolved = dict(cfg)
    resolved.update(variants[key])
    return resolved


def has_missing_thresholds(cfg: dict) -> bool:
    """True if a marker's resolved config lacks a required numeric threshold for its kind.
    Used as a last-line data-integrity check so a bad/incomplete library entry excludes just
    that one marker from scoring instead of crashing the whole report (see pipeline.py)."""
    kind = cfg.get("kind")
    if kind == "bounded":
        return cfg.get("optimal") is None or cfg.get("moderate") is None or cfg.get("direction") is None
    if kind == "range":
        return cfg.get("lo") is None or cfg.get("hi") is None
    return False



# ---- Sex-conditional audit (per request) ----
# Full library reviewed for other markers that, like sex-conditional markers, show signs of being
# single-range/female-oriented rather than genuinely sex-neutral:
#   - Estradiol (lo=15, hi=350, disp_range="phase-dependent"): this range and its explicit
#     "phase-dependent" label describe the female menstrual cycle: it is not a male reference
#     range. Male estradiol reference ranges are meaningfully different/narrower. Flagged for a
#     future sex_variants split, but NOT implemented here - no researched male default was
#     provided for this task, and inventing one would violate the same never-infer discipline
#     this fix is meant to enforce. Needs a clinically-sourced male value before implementing.
#   - DHEA-S (lo=60.9, hi=337.0): reference ranges for DHEA-S differ by sex (and meaningfully by
#     age). Current single range looks adult-female-oriented. Same as above: flagged, not
#     implemented, pending a clinically-sourced male default.
#   - Ferritin (lo=15, hi=150): commonly given as a lower range for menstruating women than for
#     men (men often reference up to ~300-400). Flagged, not implemented, pending a
#     clinically-sourced male default.
# None of these three were changed in this pass. They are documented here so clinical staff can
# decide whether/what sex-specific defaults to add, the same way Testosterone's male default
# still needs their sign-off before being treated as final.
