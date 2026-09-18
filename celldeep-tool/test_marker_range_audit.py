"""Locks the supplied CellDeep range list into the deterministic marker library."""

from markers_reference import DATA_TO_PATIENT_CATEGORY, MARKER_LIBRARY, lookup_marker, resolve_marker_config


def test_shared_ranges_categories_and_aliases():
    expected = {
        "Myeloperoxidase": ("Inflammation", "bounded", "lower", 470, None, False),
        "Lp-PLA2 Activity": ("Inflammation", "bounded", "lower", 123, None, True),
        "hs-CRP": ("Inflammation", "bounded", "lower", 1.0, None, False),
        "ADMA": ("Inflammation", "bounded", "lower", 100, None, False),
        "SDMA": ("Also Monitored", "range", None, 73, 135, None),
        "Oxidized LDL": ("Inflammation", "bounded", "lower", 60, None, False),
        "Fibrinogen": ("Inflammation", "bounded", "lower", 350, None, False),
        "Troponin T, HS": ("Also Monitored", "bounded", "lower", 6, None, False),
        "Glucose (fasting)": ("Metabolic", "range", None, 70, 90, None),
        "HbA1c": ("Metabolic", "bounded", "lower", 5.7, None, False),
        "Estimated Average Glucose": ("Metabolic", "bounded", "lower", 117, None, False),
        "TMAO": ("Metabolic", "bounded", "lower", 6.2, None, False),
        "Insulin Resistance Score": ("Metabolic", "bounded", "lower", 33, None, False),
        "Fasting Insulin": ("Metabolic", "range", None, 2, 10, None),
        "C-Peptide": ("Metabolic", "bounded", "lower", 2.16, None, True),
        "Vitamin B12": ("Foundational", "range", None, 600, 1000, None),
        "Vitamin D": ("Foundational", "range", None, 60, 90, None),
        "CoQ10": ("Foundational", "bounded", "higher", 0.35, None, False),
        "Folate": ("Foundational", "bounded", "higher", 5.4, None, False),
        "TSH": ("Thyroid", "range", None, 0.40, 5.50, None),
        "Free T4": ("Thyroid", "range", None, 0.8, 1.8, None),
        "Total T4": ("Thyroid", "range", None, 4.5, 11.7, None),
        "Free T3": ("Thyroid", "range", None, 3.0, 4.5, None),
        "Total T3": ("Thyroid", "range", None, 80, 200, None),
        "Thyroid Peroxidase Ab": ("Thyroid", "bounded", "lower", 35, None, False),
        "Thyroglobulin Ab": ("Thyroid", "bounded", "lower", 115, None, False),
    }
    for name, (category, kind, direction, first, second, inclusive) in expected.items():
        config = MARKER_LIBRARY[name]
        assert config["category"] == category
        assert config["kind"] == kind
        assert config.get("direction") == direction
        if kind == "range":
            assert (config["lo"], config["hi"]) == (first, second)
        else:
            assert config["optimal"] == first
            assert config.get("inclusive", True) is inclusive
        assert lookup_marker(name) is not None


def test_sex_specific_ranges_and_lab_only_reproductive_markers():
    expected = {
        "Ferritin": ((9, 150), (18, 300)),
        "Uric Acid": ((2.5, 7.3), (3.2, 8.6)),
        "Estradiol": ((None, None), (20, 45)),
        "Testosterone, Total": ((None, None), (600, 900)),
        "Free Testosterone": ((None, None), (100, 180)),
        "Bioavailable Testosterone": ((None, None), (250, 500)),
        "SHBG": ((None, None), (20, 50)),
    }
    for name, (female, male) in expected.items():
        config = MARKER_LIBRARY[name]
        assert (resolve_marker_config(name, config, "female").get("lo"),
                resolve_marker_config(name, config, "female").get("hi")) == female
        assert (resolve_marker_config(name, config, "male").get("lo"),
                resolve_marker_config(name, config, "male").get("hi")) == male

    for name, target in {"Estradiol": (80, 120), "Progesterone": (2, 10),
                         "Free Testosterone": (2.5, 5.0), "SHBG": (24.6, 122)}.items():
        config = MARKER_LIBRARY[name]
        resolved = resolve_marker_config(name, config, "female", postmenopausal_bhrt=True)
        assert (resolved["lo"], resolved["hi"]) == target
        assert lookup_marker(name) is not None

    for name in ("Progesterone", "LH", "FSH", "DHEA-S", "Cortisol, Total (AM)"):
        config = resolve_marker_config(name, MARKER_LIBRARY[name], "male")
        assert (config.get("lo"), config.get("hi")) == (None, None)


def test_shared_sex_neutral_ranges_are_reachable():
    for name in ("Glucose (fasting)", "eGFR", "Phosphorus", "Magnesium"):
        config = MARKER_LIBRARY[name]
        assert lookup_marker(name) is not None
        assert DATA_TO_PATIENT_CATEGORY.get(config["category"], config["category"])