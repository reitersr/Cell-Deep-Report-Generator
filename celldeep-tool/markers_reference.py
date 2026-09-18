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
    "SDMA": dict(category="Also Monitored", unit="ng/mL", kind="range",
        lo=73, hi=135, disp_range="73\u2013135",
        aliases=["sdma", "symmetric dimethylarginine", "symmetric dimethyl-arginine"]),
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
    "Estimated Average Glucose": dict(category="Metabolic", unit="mg/dL", kind="bounded",
        direction="lower", optimal=117, moderate=117, disp_range="optimal <117",
        aliases=["estimated average glucose", "estimated avg glucose", "eag", "average glucose", "estimated mean glucose"]),
    "TMAO": dict(category="Metabolic", unit="\u00b5M", kind="bounded",
        direction="lower", optimal=6.2, moderate=9.9, disp_range="optimal <6.2",
        aliases=["tmao", "trimethylamine n-oxide"]),
    "Insulin Resistance Score": dict(category="Metabolic", unit="", kind="bounded",
        direction="lower", optimal=33, moderate=66, disp_range="sensitive <33",
        aliases=["insulin resistance score", "ir score"]),
    "Fasting Insulin": dict(category="Metabolic", unit="\u00b5IU/mL", kind="range",
        lo=2, hi=10, disp_range="2\u201310",
        aliases=["fasting insulin", "insulin, fasting"]),
    "C-Peptide": dict(category="Metabolic", unit="ng/mL", kind="bounded",
        direction="lower", optimal=2.16, moderate=2.16, disp_range="optimal \u22642.16",
        aliases=["c-peptide", "c peptide", "connecting peptide", "c peptide, serum", "serum c-peptide"]),

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
    # Kerry's source specifies the patient's own lab reference range for LH/FSH, not a
    # CellDeep-wide numeric range. Until extraction carries that printed range through,
    # these remain recognized but intentionally unscored rather than using guessed values.
    "LH": dict(category="Hormones", unit="mIU/mL", kind="range",
        lo=None, hi=None, disp_range="lab-specific reference range", suppress_low_on_trt=True,
        aliases=["lh", "luteinizing hormone", "luteinising hormone"]),
    "FSH": dict(category="Hormones", unit="mIU/mL", kind="range",
        lo=None, hi=None, disp_range="lab-specific reference range", suppress_low_on_trt=True,
        aliases=["fsh", "follicle stimulating hormone", "follicle-stimulating hormone"]),

    # ---- Thyroid ----
    "TSH": dict(category="Thyroid", unit="\u00b5IU/mL", kind="range",
        lo=0.40, hi=5.50, disp_range="0.40\u20135.50",
        aliases=["tsh", "thyroid stimulating hormone", "thyrotropin"]),
    "Free T4": dict(category="Thyroid", unit="ng/dL", kind="range",
        lo=0.8, hi=1.8, disp_range="0.8\u20131.8",
        aliases=["free t4", "t4, free", "ft4", "free thyroxine"]),
    "Total T4": dict(category="Thyroid", unit="\u00b5g/dL", kind="range",
        lo=4.5, hi=11.7, disp_range="4.5\u201311.7",
        aliases=["total t4", "t4 total", "t4, total", "thyroxine, total", "total thyroxine"]),
    "Free T3": dict(category="Thyroid", unit="pg/mL", kind="range",
        lo=3.0, hi=4.5, disp_range="3.0\u20134.5",
        aliases=["free t3", "t3, free", "ft3", "free triiodothyronine"]),
    "Total T3": dict(category="Thyroid", unit="ng/dL", kind="range",
        lo=80, hi=200, disp_range="80\u2013200",
        aliases=["total t3", "t3 total", "t3, total", "triiodothyronine, total", "total triiodothyronine"]),
    "Thyroid Peroxidase Ab": dict(category="Thyroid", unit="IU/mL", kind="bounded",
        direction="lower", optimal=35, moderate=100, disp_range="optimal <35",
        aliases=["tpo ab", "thyroid peroxidase antibody", "tpo antibody"]),
    "Thyroglobulin Ab": dict(category="Thyroid", unit="IU/mL", kind="bounded",
        direction="lower", optimal=115, moderate=115, disp_range="optimal <115",
        aliases=["thyroglobulin ab", "thyroglobulin antibody", "thyroglobulin antibodies", "thyroglobulin antibody, serum", "tg ab", "tgab"]),

    # ---- Foundational ----
    "Vitamin D": dict(category="Foundational", unit="ng/mL", kind="bounded",
        direction="higher", optimal=90, moderate=60, disp_range="optimal 60\u201390",
        aliases=["vitamin d", "25-oh vitamin d", "vitamin d, 25-hydroxy"]),
    "Vitamin B12": dict(category="Foundational", unit="pg/mL", kind="range",
        lo=600, hi=1000, disp_range="600\u20131000",
        aliases=["vitamin b12", "b12", "cobalamin"]),
    "CoQ10": dict(category="Foundational", unit="\u00b5g/mL", kind="bounded",
        direction="higher", optimal=0.35, moderate=0.35, disp_range="optimal >0.35",
        aliases=["coq10", "coenzyme q10", "coenzyme q-10", "coenzyme q 10", "ubiquinol"]),
    "Folate": dict(category="Foundational", unit="ng/mL", kind="bounded",
        direction="higher", optimal=5.4, moderate=5.4, disp_range="optimal >5.4",
        aliases=["folate", "folic acid", "serum folate", "folate, serum", "vitamin b9"]),
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

    # ---- New renal/mineral/reproductive markers ----
    "eGFR": dict(category="Also Monitored", unit="mL/min/1.73m^2", kind="bounded",
        direction="higher", optimal=70, moderate=70, disp_range="optimal >70",
        aliases=["egfr", "estimated gfr", "gfr, estimated", "estimated glomerular filtration rate", "glomerular filtration rate", "egfr mdrd"]),
    "Phosphorus": dict(category="Also Monitored", unit="mg/dL", kind="range",
        lo=2.5, hi=4.5, disp_range="2.5\u20134.5",
        aliases=["phosphorus", "phosphate", "serum phosphorus", "inorganic phosphorus", "phosphorus, serum"]),
    "Magnesium": dict(category="Also Monitored", unit="mg/dL", kind="range",
        lo=1.5, hi=2.5, disp_range="1.5\u20132.5",
        aliases=["magnesium", "serum magnesium", "magnesium, serum", "mg"]),
    "Uric Acid": dict(category="Also Monitored", unit="mg/dL", kind="range",
        disp_range="sex-specific default (see sex_variants)", default_sex="female",
        sex_variants={
            "female": dict(lo=2.5, hi=7.3, disp_range="2.5\u20137.3"),
            "male": dict(lo=3.2, hi=8.6, disp_range="3.2\u20138.6"),
        },
        aliases=["uric acid", "serum uric acid", "uric acid, serum", "urate"]),
    "Progesterone": dict(category="Hormones", unit="ng/mL", kind="range",
        lo=2, hi=10, disp_range="2\u201310 (postmenopausal BHRT target)",
        aliases=["progesterone", "progesterone, serum", "serum progesterone"]),
    "Free Testosterone": dict(category="Hormones", unit="pg/mL", kind="range",
        disp_range="sex-specific default (see sex_variants)", default_sex="female",
        sex_variants={
            "female": dict(lo=2.5, hi=5.0, disp_range="2.5\u20135.0"),
            "male": dict(lo=100, hi=180, disp_range="100\u2013180"),
        },
        aliases=["free testosterone", "testosterone, free", "free testosterone, serum", "free t"]),
    "Bioavailable Testosterone": dict(category="Hormones", unit="ng/dL", kind="range",
        lo=250, hi=500, disp_range="250\u2013500",
        aliases=["bioavailable testosterone", "testosterone, bioavailable", "bioavailable t", "bioavailable testosterone, serum"]),
    "SHBG": dict(category="Hormones", unit="nmol/L", kind="range",
        disp_range="sex-specific default (see sex_variants)", default_sex="female",
        sex_variants={
            "female": dict(lo=24.6, hi=122, disp_range="24.6\u2013122"),
            "male": dict(lo=20, hi=50, disp_range="20\u201350"),
        },
        aliases=["shbg", "sex hormone binding globulin", "sex hormone-binding globulin", "sex hormone binding globulin, serum"]),
    "PSA Total": dict(category="Also Monitored", unit="ng/mL", kind="bounded",
        direction="lower", optimal=4.0, moderate=4.0, disp_range="optimal \u22644.0",
        aliases=["psa", "psa total", "psa, total", "prostate specific antigen", "total psa", "prostate-specific antigen"]),

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
