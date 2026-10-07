"""Lab-reported results that CellDeep shows but does not score.

These tests appear in the patient report exactly as the lab printed them: the result, the lab's
own reference range and the lab's own H/L flag. They never receive a CellDeep score and never
feed a system rollup, until the clinic supplies thresholds and the test moves into
markers_reference.MARKER_LIBRARY.

Matching is by exact printed name (case- and whitespace-insensitive), never fuzzy. Each entry
is one test: different tests are never merged (standard C-Reactive Protein is not hs-CRP,
absolute counts are not percentages). Urinalysis entries match only inside a urinalysis section
and serum entries never match there.

Any other printed result that the lab flagged H or L is also shown, under its printed name, so
no lab-flagged result is missing from the patient report.
"""


from schema import normalize_date_for_matching

URINALYSIS = "Urinalysis"
OTHER_FLAGGED = "Other Lab-Flagged Results"
OTHER_RESULTS = "Other Lab Results"  # an unaliased printed result under no section heading
GROUP_ORDER = ("Complete Blood Count", "Chemistry", "Iron Studies", "Lipids", "Fatty Acids (OmegaCheck)",
               "Cardiovascular", "Inflammation", "Hormones", "Tumor Markers", "Infectious Disease", URINALYSIS,
               OTHER_FLAGGED)


def _entries(group, rows):
    return {name: dict(group=group, aliases=aliases) for name, aliases in rows}


LAB_REPORTED_LIBRARY = {
    **_entries("Complete Blood Count", [
        ("White Blood Cell Count", ["white blood cell count", "wbc", "wbc count", "white blood cells",
                                    "white blood cell"]),
        ("Red Blood Cell Count", ["red blood cell count", "rbc", "rbc count", "red blood cells", "red blood cell"]),
        ("Hemoglobin", ["hemoglobin", "hgb"]),
        ("Hematocrit", ["hematocrit", "hct"]),
        ("MCV", ["mcv", "mean corpuscular volume"]),
        ("MCH", ["mch", "mean corpuscular hemoglobin"]),
        ("MCHC", ["mchc", "mean corpuscular hemoglobin concentration"]),
        ("RDW", ["rdw", "red cell distribution width"]),
        ("Platelet Count", ["platelet count", "platelets", "plt"]),
        ("MPV", ["mpv", "mean platelet volume"]),
        ("Absolute Neutrophils", ["absolute neutrophils", "neutrophils, absolute", "neutrophils (absolute)",
                                  "neutrophil #", "neutrophil absolute"]),
        ("Absolute Lymphocytes", ["absolute lymphocytes", "lymphocytes, absolute", "lymphs (absolute)",
                                  "lymphocyte #", "lymphocyte absolute"]),
        ("Absolute Monocytes", ["absolute monocytes", "monocytes, absolute", "monocytes (absolute)", "monocyte #", "monocyte absolute"]),
        ("Absolute Eosinophils", ["absolute eosinophils", "eosinophils, absolute", "eos (absolute)", "eosinophil #", "eosinophil absolute"]),
        ("Absolute Basophils", ["absolute basophils", "basophils, absolute", "baso (absolute)", "basophil #", "basophil absolute"]),
        ("Neutrophils %", ["neutrophils", "neutrophils %", "neutrophils, percent", "neutrophils (%)",
                           "neutrophil %"]),
        ("Lymphocytes %", ["lymphocytes", "lymphocytes %", "lymphocytes, percent", "lymphs", "lymphocyte %"]),
        ("Monocytes %", ["monocytes", "monocytes %", "monocytes, percent", "monocyte %"]),
        ("Eosinophils %", ["eosinophils", "eosinophils %", "eosinophils, percent", "eos", "eosinophil %"]),
        ("Basophils %", ["basophils", "basophils %", "basophils, percent", "basos", "basophil %"]),
    ]),
    **_entries("Chemistry", [
        ("Sodium", ["sodium"]),
        ("Potassium", ["potassium"]),
        ("Chloride", ["chloride"]),
        ("Carbon Dioxide", ["carbon dioxide", "co2", "carbon dioxide, total", "co2 (carbon dioxide, bicarbonate)"]),
        ("Bicarbonate", ["bicarbonate", "hco3"]),
        ("Anion Gap", ["anion gap"]),
        ("Urea Nitrogen (BUN)", ["urea nitrogen (bun)", "urea nitrogen", "bun", "blood urea nitrogen",
                                 "bun (blood urea nitrogen)"]),
        ("BUN/Creatinine Ratio", ["bun/creatinine ratio", "bun/creat ratio"]),
        ("Calcium", ["calcium", "calcium, total"]),
        ("Protein, Total", ["protein, total", "total protein"]),
        ("Albumin", ["albumin"]),
        ("Globulin", ["globulin", "globulin, total"]),
        ("Albumin/Globulin Ratio", ["albumin/globulin ratio", "a/g ratio"]),
        ("Bilirubin, Total", ["bilirubin, total", "total bilirubin", "bili", "bili total"]),
        ("Bilirubin, Direct", ["bilirubin, direct", "direct bilirubin"]),
        ("Alkaline Phosphatase", ["alkaline phosphatase", "alk phos", "alp (alkaline phosphatase)"]),
        ("AST", ["ast", "ast (sgot)", "sgot", "ast (aspartate amino transferase)"]),
        ("ALT", ["alt", "alt (sgpt)", "sgpt", "alt (alanine amino transferase)"]),
        ("GGT", ["ggt", "gamma-glutamyl transferase", "gamma glutamyl transferase", "gamma-glutamyltransferase"]),
    ]),
    **_entries("Iron Studies", [
        ("Iron, Total", ["iron, total", "iron", "total iron"]),
        ("Iron Binding Capacity", ["iron binding capacity", "tibc", "total iron binding capacity"]),
        ("% Saturation", ["% saturation", "iron saturation", "% iron saturation"]),
    ]),
    **_entries("Lipids", [
        ("Cholesterol/HDL Ratio", ["chol/hdlc ratio", "cholesterol/hdl ratio", "chol/hdl ratio",
                                   "total cholesterol/hdl ratio"]),
        ("VLDL Cholesterol", ["vldl cholesterol", "vldl-c"]),
        ("LDL Small", ["ldl small", "small ldl-p", "small ldl particle number"]),
        ("LDL Medium", ["ldl medium", "medium ldl-p", "medium ldl particle number"]),
        ("HDL Large", ["hdl large", "large hdl-p", "large hdl particle number"]),
        ("LDL Pattern", ["ldl pattern"]),
        ("LDL Peak Size", ["ldl peak size", "ldl particle size"]),
    ]),
    **_entries("Fatty Acids (OmegaCheck)", [
        ("OmegaCheck", ["omegacheck", "omegacheck(tm)", "omegacheck (tm)"]),
        ("EPA", ["epa", "eicosapentaenoic acid"]),
        ("DPA", ["dpa", "docosapentaenoic acid"]),
        ("DHA", ["dha", "docosahexaenoic acid"]),
        ("Arachidonic Acid", ["arachidonic acid"]),
        ("Linoleic Acid", ["linoleic acid"]),
        ("Omega-6 Total", ["omega-6 total", "omega-6 fatty acids, total"]),
        ("Arachidonic Acid/EPA Ratio", ["arachidonic acid/epa ratio", "aa/epa ratio"]),
        ("Omega-6/Omega-3 Ratio", ["omega-6/omega-3 ratio"]),
    ]),
    **_entries("Cardiovascular", [
        ("Homocysteine", ["homocysteine", "homocysteine, plasma"]),
    ]),
    **_entries("Inflammation", [
        ("C-Reactive Protein", ["c-reactive protein", "crp"]),
    ]),
    **_entries("Hormones", [
        ("Estrogens, Total", ["estrogens, total", "estrogens, total, ia", "total estrogens"]),
        ("Prolactin", ["prolactin"]),
    ]),
    **_entries("Tumor Markers", [
        ("Free PSA", ["free psa", "psa, free"]),
        ("% Free PSA", ["% free psa", "percent free psa", "psa, % free"]),
    ]),
    **_entries("Infectious Disease", [
        ("SARS-CoV-2", ["sars cov2", "sars-cov-2", "sars cov 2", "sars-cov2"]),
        ("SARS-CoV-2 RNA", ["sars-cov-2 rna", "sars cov 2 rna", "sars-cov-2 rna, qual rt-pcr"]),
        ("SARS-CoV-2 Antibody", ["sars-cov-2 antibody", "sars cov 2 antibody", "sars-cov-2 ab"]),
    ]),
    **{f"{URINALYSIS} — {name}": dict(group=URINALYSIS, aliases=aliases) for name, aliases in [
        ("Color", ["color"]), ("Appearance", ["appearance"]), ("Specific Gravity", ["specific gravity"]),
        ("pH", ["ph"]), ("Glucose", ["glucose"]), ("Bilirubin", ["bilirubin", "bili"]), ("Ketones", ["ketones"]),
        ("Protein", ["protein"]), ("Nitrite", ["nitrite"]), ("Leukocyte Esterase", ["leukocyte esterase", "leukocytes"]),
        ("Urobilinogen", ["urobilinogen"]), ("Blood", ["blood", "occult blood", "urine occult blood"]), ("WBC", ["wbc"]), ("RBC", ["rbc"]),
        ("Squamous Epithelial Cells", ["squamous epithelial cells"]), ("Bacteria", ["bacteria"]),
        ("Hyaline Cast", ["hyaline cast", "hyaline casts"]), ("Yeast", ["yeast"]),
        ("Reflexive Urine Culture", ["reflexive urine culture"]),
    ]},
}

_FLAGS = {"H", "L", "HH", "LL"}

# Rows a lab prints inside another test's panel under that panel's own lab code: (printed name, lab code) ->
# (shown name, group). They are separate results from the chemistry test of the same name, never a conflict.
PANEL_SCOPED = {
    ("albumin", "AMD"): ("Albumin (testosterone panel)", "Hormones"),
    ("glob", "AMD"): ("Globulin (testosterone panel)", "Hormones"),
    ("globulin", "AMD"): ("Globulin (testosterone panel)", "Hormones"),
}


def panel_scoped(raw_name, lab_codes):
    """(shown name, group) for a row printed under a panel's own lab code, else None. Exact name and code only."""
    for code in lab_codes or ():
        if (found := PANEL_SCOPED.get((_key(raw_name), code.strip().upper()))) is not None:
            return found
    return None


def _group_rank(group):
    """Library groups in GROUP_ORDER; the lab's own section headings after Urinalysis, before the flagged list."""
    if group in GROUP_ORDER:
        return GROUP_ORDER.index(group) * 2
    return GROUP_ORDER.index(OTHER_FLAGGED) * 2 - 1


def _key(text):
    return " ".join((text or "").split()).casefold()


def is_urinalysis(heading):
    return _key(heading).startswith("urinalysis")


def lookup(raw_name, heading):
    """Exact printed-name match, scoped by section. Returns (canonical, group) or None."""
    from markers_reference import name_key

    query = name_key(raw_name)
    urine = is_urinalysis(heading)
    matches = {canonical: config["group"] for canonical, config in LAB_REPORTED_LIBRARY.items()
               if (config["group"] == URINALYSIS) == urine
               and query in {name_key(alias) for alias in [canonical.split(" — ")[-1], *config["aliases"]]}}
    if len(matches) != 1:
        return None
    return next(iter(matches.items()))


def _heading(unknown):
    if unknown.get("section_heading") is not None:
        return unknown["section_heading"]
    context = unknown.get("source_context") or ""
    return context.split("; ", 1)[1] if "; " in context else None


def _results(unknown):
    results = []
    for cell in unknown.get("cells", []):
        if not cell.get("present") or cell.get("status") == "not_performed" or not cell.get("disp_value"):
            continue
        flag = cell.get("lab_flag")
        unit = (unknown.get("raw_unit") or "").strip()
        # The printed range and unit belong to the current result; a historical column prints neither.
        current = cell.get("kind", "current") == "current"
        results.append({"date_display": cell["date_display"], "disp_value": cell["disp_value"],
                        "lab_flag": flag if flag in _FLAGS else None,
                        "lab_range": ((unknown.get("raw_range") or "").strip() or None) if current else None,
                        **({"unit": unit} if unit and current else {}),  # the lab's printed unit only
                        **({"note": cell["note"]} if cell.get("note") else {}),
                        "_column": "current" if current else "historical"})
    return results


def build(unrecognized):
    """Split unrecognized rows into lab-reported items and the rows that stay staff-only.

    Returns (items, remaining_unrecognized, staff_notes). A library match with a printed result
    leaves the staff list; a lab-flagged row outside the library is shown AND stays in the staff
    list so the library can be extended. Conflicting results for one test on one date are never
    resolved: both are dropped from the report and listed for staff."""
    items, remaining, notes = {}, [], []
    for unknown in unrecognized:
        results = _results(unknown)
        # show_as: a recognized test the layout parser decided must be shown as lab-reported, not scored
        # (e.g. an assay that differs from the CellDeep range basis); it carries its own (name, group).
        match = (tuple(unknown["show_as"]) if unknown.get("show_as") else lookup(unknown["raw_name"], _heading(unknown))
                 ) if results else None
        if match is None:
            remaining.append(unknown)
            flagged = any(result["lab_flag"] for result in results)
            # A readable result row (name, value, and the lab's unit or range) with no CellDeep alias is shown as
            # printed under the lab's own section heading; it stays in the staff list so the library can grow.
            readable = bool(results) and bool((unknown.get("raw_unit") or "").strip()
                                              or (unknown.get("raw_range") or "").strip())
            if not (flagged or readable):
                continue
            unknown["shown_as_lab_reported"] = True
            urine = is_urinalysis(_heading(unknown))
            name = " ".join(unknown["raw_name"].split())
            group = URINALYSIS if urine and not flagged else OTHER_FLAGGED if flagged else \
                (" ".join((_heading(unknown) or "").split()) or OTHER_RESULTS)
            canonical = f"{URINALYSIS} — {name}" if urine else name
        else:
            canonical, group = match
        item = items.setdefault(_key(canonical), {"name": canonical, "group": group, "results": [],
                                                  "printed_names": []})
        if unknown.get("patient_note"):
            item["note"] = unknown["patient_note"]
        item["printed_names"].append(unknown["raw_name"])
        item["results"].extend(results)
    final = []
    for item in items.values():
        by_date = {}
        for result in item["results"]:
            by_date.setdefault(normalize_date_for_matching(result["date_display"]), []).append(result)
        kept = []
        for date, results in by_date.items():
            # The draw's own full report wins over a later report's historical column; a difference is a warning.
            full = [r for r in results if r["_column"] == "current"]
            if full:
                for other in (r for r in results if r["_column"] != "current"):
                    if (other["disp_value"], other["lab_flag"]) != (full[0]["disp_value"], full[0]["lab_flag"]):
                        notes.append(f"HISTORICAL VALUE DIFFERS: {item['name']!r} on {full[0]['date_display']}: the "
                                     f"full report prints {full[0]['disp_value']!r}; a later report's historical column "
                                     f"prints {other['disp_value']!r}. The full report's value is used.")
                results = full
            distinct = {(r["disp_value"], r["lab_flag"]) for r in results}
            if len(distinct) > 1:
                notes.append(f"LAB-REPORTED CONFLICT: {item['name']!r} on {results[0]['date_display']} was printed "
                             f"with different results {sorted(map(str, distinct))}; not shown - manual review required")
                continue
            ranges = {r["lab_range"] for r in results if r["lab_range"]}
            units = {r["unit"] for r in results if r.get("unit")}
            result = {key: value for key, value in results[0].items() if key not in ("_column", "unit")}
            kept.append({**result, "lab_range": next(iter(ranges)) if len(ranges) == 1 else None,
                         **({"unit": next(iter(units))} if len(units) == 1 else {})})
        if kept:
            kept.sort(key=lambda r: normalize_date_for_matching(r["date_display"]))
            final.append({**item, "results": kept})
    final.sort(key=lambda item: (_group_rank(item["group"]), item["group"],
                                 list(LAB_REPORTED_LIBRARY).index(item["name"])
                                 if item["name"] in LAB_REPORTED_LIBRARY else 0, item["name"]))
    return final, remaining, notes
