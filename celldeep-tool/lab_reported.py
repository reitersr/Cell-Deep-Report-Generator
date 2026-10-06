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
GROUP_ORDER = ("Complete Blood Count", "Chemistry", "Iron Studies", "Lipids", "Fatty Acids (OmegaCheck)",
               "Cardiovascular", "Inflammation", "Hormones", "Infectious Disease", URINALYSIS, OTHER_FLAGGED)


def _entries(group, rows):
    return {name: dict(group=group, aliases=aliases) for name, aliases in rows}


LAB_REPORTED_LIBRARY = {
    **_entries("Complete Blood Count", [
        ("White Blood Cell Count", ["white blood cell count", "wbc", "wbc count", "white blood cells"]),
        ("Red Blood Cell Count", ["red blood cell count", "rbc", "rbc count", "red blood cells"]),
        ("Hemoglobin", ["hemoglobin", "hgb"]),
        ("Hematocrit", ["hematocrit", "hct"]),
        ("MCV", ["mcv", "mean corpuscular volume"]),
        ("MCH", ["mch", "mean corpuscular hemoglobin"]),
        ("MCHC", ["mchc", "mean corpuscular hemoglobin concentration"]),
        ("RDW", ["rdw", "red cell distribution width"]),
        ("Platelet Count", ["platelet count", "platelets", "plt"]),
        ("MPV", ["mpv", "mean platelet volume"]),
        ("Absolute Neutrophils", ["absolute neutrophils", "neutrophils, absolute", "neutrophils (absolute)",
                                  "neutrophil #"]),
        ("Absolute Lymphocytes", ["absolute lymphocytes", "lymphocytes, absolute", "lymphs (absolute)",
                                  "lymphocyte #"]),
        ("Absolute Monocytes", ["absolute monocytes", "monocytes, absolute", "monocytes (absolute)", "monocyte #"]),
        ("Absolute Eosinophils", ["absolute eosinophils", "eosinophils, absolute", "eos (absolute)", "eosinophil #"]),
        ("Absolute Basophils", ["absolute basophils", "basophils, absolute", "baso (absolute)", "basophil #"]),
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
    **_entries("Infectious Disease", [
        ("SARS-CoV-2", ["sars cov2", "sars-cov-2", "sars cov 2", "sars-cov2"]),
        ("SARS-CoV-2 RNA", ["sars-cov-2 rna", "sars cov 2 rna", "sars-cov-2 rna, qual rt-pcr"]),
        ("SARS-CoV-2 Antibody", ["sars-cov-2 antibody", "sars cov 2 antibody", "sars-cov-2 ab"]),
    ]),
    **{f"{URINALYSIS} — {name}": dict(group=URINALYSIS, aliases=aliases) for name, aliases in [
        ("Color", ["color"]), ("Appearance", ["appearance"]), ("Specific Gravity", ["specific gravity"]),
        ("pH", ["ph"]), ("Glucose", ["glucose"]), ("Bilirubin", ["bilirubin"]), ("Ketones", ["ketones"]),
        ("Protein", ["protein"]), ("Nitrite", ["nitrite"]), ("Leukocyte Esterase", ["leukocyte esterase", "leukocytes"]),
        ("Urobilinogen", ["urobilinogen"]), ("Blood", ["blood", "occult blood"]), ("WBC", ["wbc"]), ("RBC", ["rbc"]),
        ("Squamous Epithelial Cells", ["squamous epithelial cells"]), ("Bacteria", ["bacteria"]),
        ("Hyaline Cast", ["hyaline cast", "hyaline casts"]), ("Yeast", ["yeast"]),
        ("Reflexive Urine Culture", ["reflexive urine culture"]),
    ]},
}

_FLAGS = {"H", "L", "HH", "LL"}


def _key(text):
    return " ".join((text or "").split()).casefold()


def is_urinalysis(heading):
    return _key(heading).startswith("urinalysis")


def lookup(raw_name, heading):
    """Exact printed-name match, scoped by section. Returns (canonical, group) or None."""
    query = _key(raw_name)
    urine = is_urinalysis(heading)
    matches = {canonical: config["group"] for canonical, config in LAB_REPORTED_LIBRARY.items()
               if (config["group"] == URINALYSIS) == urine
               and query in {_key(alias) for alias in [canonical.split(" — ")[-1], *config["aliases"]]}}
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
        results.append({"date_display": cell["date_display"], "disp_value": cell["disp_value"],
                        "lab_flag": flag if flag in _FLAGS else None,
                        "lab_range": (unknown.get("raw_range") or "").strip() or None})
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
            if not any(result["lab_flag"] for result in results):
                continue
            urine = is_urinalysis(_heading(unknown))
            name = " ".join(unknown["raw_name"].split())
            canonical, group = (f"{URINALYSIS} — {name}" if urine else name), OTHER_FLAGGED
        else:
            canonical, group = match
        item = items.setdefault(_key(canonical), {"name": canonical, "group": group, "results": [],
                                                  "printed_names": []})
        item["printed_names"].append(unknown["raw_name"])
        item["results"].extend(results)
    final = []
    for item in items.values():
        by_date = {}
        for result in item["results"]:
            by_date.setdefault(normalize_date_for_matching(result["date_display"]), []).append(result)
        kept = []
        for date, results in by_date.items():
            distinct = {(r["disp_value"], r["lab_flag"]) for r in results}
            if len(distinct) > 1:
                notes.append(f"LAB-REPORTED CONFLICT: {item['name']!r} on {results[0]['date_display']} was printed "
                             f"with different results {sorted(map(str, distinct))}; not shown - manual review required")
                continue
            ranges = {r["lab_range"] for r in results if r["lab_range"]}
            kept.append({**results[0], "lab_range": next(iter(ranges)) if len(ranges) == 1 else None})
        if kept:
            kept.sort(key=lambda r: normalize_date_for_matching(r["date_display"]))
            final.append({**item, "results": kept})
    final.sort(key=lambda item: (GROUP_ORDER.index(item["group"]), list(LAB_REPORTED_LIBRARY).index(item["name"])
                                 if item["name"] in LAB_REPORTED_LIBRARY else 0, item["name"]))
    return final, remaining, notes
