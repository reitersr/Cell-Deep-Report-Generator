"""Access Medical Laboratories digital report (the clinic's "CHL-style" lab), read deterministically from the
PDF text layer by exact text and word boxes. No vision model, no guessing.

Layout:
- Every page opens with a header block: lab address, "Client: ...", "Patient: LAST, FIRST",
  "DOB. MM/DD/YYYY Age:NN Sex: M", "Phys: ...", "Page:N", "Acc# ...", "Coll. Date: MM/DD/YY" (two-digit year:
  08/07/26 is 08/07/2026), "Recv. Date", "Print Date", "Coll. Time", "Final Report", "Report Status: FINAL",
  "Fasting: N|Y".
- A column header line "Test Name | Results | Reference Range | Units"; a word's column is decided by where its
  centre falls between the printed column labels.
- Section titles in capitals (COMPLETE BLOOD COUNT, GENERAL CHEMISTRY, ...), "(Continued)" when a section runs
  onto the next page, and "(Continued on Next Page)" at the bottom of a page.
- A result row: a test name, then in the Results column one value with the lab's flag right after it ("109 H"),
  a reference range ("83 - 102", "0 - 4", "> 60", "< 150", a word such as "Negative", or nothing) and a unit
  (or nothing).
- An "OUT OF RANGE SUMMARY" block repeats rows printed later; it is read only to cross-check them.

Rules (CLAUDE.md "never infer"): only lines under a section title that fit the four-column row pattern are
results. Explanatory text (indented note lines, "label:" lines, GFR stage and probability tables, method notes,
time-of-day ranges) never is. A line that looks like a result row for a known test but does not fit is excluded
and listed for staff. A word result (Negative, Yellow, ...) is shown as lab-reported, never scored. A test whose
assay differs from the CellDeep range basis is shown as lab-reported with the lab's own range and flag, never
scored against the CellDeep range. Units are never filled in.
"""

import re

import clinic_config

NAME = "Access Medical Laboratories"
LAB_KEY = "access_medical"

_HEADER_LABELS = ("Test", "Name", "Results", "Reference", "Range", "Units")
_LINE_TOLERANCE_PT = 3.0
_INDENT_PT = 8.0  # explanatory lines are printed indented from the test-name column
_QUALITATIVE = {"negative", "positive", "trace", "normal", "abnormal", "yellow", "straw", "amber", "colorless",
                "clear", "hazy", "cloudy", "turbid", "n/a", "none seen", "not detected", "detected", "rare",
                "few", "moderate", "many", "small", "large", "1+", "2+", "3+", "4+"}
_NUMBER = r"\d+(?:\.\d+)?|\.\d+"
_RESULT_RE = re.compile(rf"(?P<value>(?:[<>]=?\s?)?(?:{_NUMBER}))(?:\s?(?P<flag>HH|LL|H|L))?")
_RANGE_RE = re.compile(rf"(?:{_NUMBER})\s*-\s*(?:{_NUMBER})|[<>]=?\s*(?:{_NUMBER})")
_UNIT_RE = re.compile(r"[^\s]{1,24}")
_TITLE_RE = re.compile(r"[A-Z][A-Z0-9 /&,.'-]*[A-Z)]")
_EXPLANATORY_NAME_RE = re.compile(r"^(?:stage|grade|class|risk|probability|morning|afternoon|evening|note|"
                                  r"comment|method|performed|reference)\b", re.IGNORECASE)
_CONTINUED_RE = re.compile(r"\s*\(continued(?: on next page)?\)\s*$", re.IGNORECASE)
# The summary block's heading: any line naming "out of range" with no digits ("OUT OF RANGE SUMMARY", "Out of Range
# Results", ...), wherever it is printed.
_SUMMARY_HEADING_RE = re.compile(r"^\D*\bout\s+of\s+range\b\D*$", re.IGNORECASE)
_MORNING_WINDOW_RE = re.compile(r"\bmorning\b\D*?(\d{1,2})\s*-\s*(\d{1,2})\s*:", re.IGNORECASE)
_FLAGS = ("H", "L", "HH", "LL")
# Printed names that mean a different test depending on the section: "Bili" is urine bilirubin under urinalysis
# and total bilirubin under chemistry/liver. Under any other section it is left out with a notice, never guessed.
_SECTION_SCOPED_NAMES = {"bili": ("URINALYSIS", "CHEMISTRY", "LIVER", "HEPATIC")}
_UNITLESS_RE = re.compile(r"\bratio\b|^ph$|^specific gravity$", re.IGNORECASE)

_HEADER_FIELDS = {
    "patient": re.compile(r"Patient:\s*(?P<v>.+?)(?=\s+(?:DOB\b|Phys:|Acc#|Coll\.|Page:|Recv\.|Print)|$)"),
    "dob": re.compile(r"DOB\.?\s*:?\s*(?P<v>\d{1,2}/\d{1,2}/\d{4})"),
    "age": re.compile(r"Age:\s*(?P<v>\d{1,3})\b"),
    "sex": re.compile(r"Sex:\s*(?P<v>[MF])\b"),
    "accession": re.compile(r"Acc#\s*(?P<v>[A-Za-z0-9-]+)"),
    "collected": re.compile(r"Coll\.\s*Date:\s*(?P<v>\d{1,2}/\d{1,2}/\d{2,4})"),
    "printed": re.compile(r"Print\s*Date:\s*(?P<v>\d{1,2}/\d{1,2}/\d{2,4})"),
    "fasting": re.compile(r"Fasting:[ \t]*(?P<v>Yes|No|Unknown|Y|N)?(?![A-Za-z])", re.IGNORECASE),
    "collection_time": re.compile(r"Coll\.\s*Time:\s*(?P<v>\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)"),
}
_GROUP_BY_CATEGORY = {"Hormones": "Hormones", "Thyroid": "Hormones", "Lipids": "Lipids",
                      "Inflammation": "Inflammation"}


def _pipeline():
    import pipeline  # late import: pipeline imports this module through lab_layouts

    return pipeline


def _words(page):
    return _pipeline()._page_words(page)


def _lines(page):
    return _pipeline()._group_lines(_words(page), _LINE_TOLERANCE_PT)


def _text(words):
    return " ".join(word[4] for word in words)


def _column_header(lines):
    """(index, {"results": x, "range": x, "units": x, "name": x}) of the printed column header line, or None."""
    for index, words in enumerate(lines):
        tokens = [word[4] for word in words]
        if tokens == list(_HEADER_LABELS):
            by = {word[4]: word for word in words}
            return index, {"name": by["Test"][0], "results": by["Results"][0], "range": by["Reference"][0],
                           "units": by["Units"][0]}
    return None


def detect(pages):
    """The layout's own structure: the four-column header line and the accession/collection header block."""
    for page in pages:
        lines = _lines(page)
        text = "\n".join(_text(words) for words in lines)
        if _column_header(lines) and all(label in text for label in ("Acc#", "Coll. Date:", "Report Status:")):
            return True
    return False


def full_date(text):
    """'08/07/26' -> '08/07/2026' (this lab prints two-digit years of the 2000s); a four-digit year as printed."""
    month, day, year = text.split("/")
    year = int(year) + 2000 if len(year) == 2 else int(year)
    return f"{int(month):02d}/{int(day):02d}/{year:04d}"


def read_header(lines):
    """The header fields this page prints (only those printed)."""
    text = "\n".join(_text(words) for words in lines)
    header = {}
    for field, pattern in _HEADER_FIELDS.items():
        match = pattern.search(text)
        if match and field == "fasting":
            # "Unknown" or a blank "Fasting:" is kept as printed and treated like "N" (never fasting).
            printed = (match["v"] or "").upper()
            header[field] = {"YES": "Y", "NO": "N"}.get(printed, printed or "BLANK")
        elif match:
            header[field] = " ".join(match["v"].split()).strip(" ,")
    for field in ("collected", "printed"):
        if field in header:
            header[field] = full_date(header[field])
    return header


def _split_columns(words, columns):
    """name / results / range / units word lists, by each word's centre between the printed column labels."""
    cells = {"name": [], "results": [], "range": [], "units": []}
    for word in words:
        centre = (word[0] + word[2]) / 2
        if centre < columns["results"] - 4:
            cells["name"].append(word)
        elif centre < columns["range"] - 4:
            cells["results"].append(word)
        elif centre < columns["units"] - 4:
            cells["range"].append(word)
        else:
            cells["units"].append(word)
    return {key: _text(value) for key, value in cells.items()}, cells


def _qualitative(text):
    return " ".join(text.split()).casefold() in _QUALITATIVE


def _row(cells):
    """(value, flag, range, unit) when the line fits the result-row pattern, else (None, reason)."""
    name, results, printed_range, unit = (cells[key].strip() for key in ("name", "results", "range", "units"))
    if not name or name.endswith(":") or _EXPLANATORY_NAME_RE.match(name):
        return None, "not a result row"
    match = _RESULT_RE.fullmatch(results)
    if match is None:
        flag = None
        parts = results.rsplit(" ", 1)
        if len(parts) == 2 and parts[1] in ("H", "L", "HH", "LL") and _qualitative(parts[0]):
            results, flag = parts
        if not _qualitative(results):
            return None, f"result {results!r} is not one printed value with an optional H/L flag"
        value = " ".join(results.split())
    else:
        value, flag = match["value"].replace(" ", ""), match["flag"]
    if printed_range and not (_RANGE_RE.fullmatch(printed_range) or _qualitative(printed_range)):
        return None, f"reference range {printed_range!r} is not a printed range"
    if unit and not _UNIT_RE.fullmatch(unit):
        return None, f"units {unit!r} are not one printed unit"
    return (value, flag, " ".join(printed_range.split()), unit), None


def _unit_key(unit):
    return (unit or "").casefold().replace("µ", "u").replace("μ", "u").replace("^", "").replace(" ", "")


def _range_reference(printed_range):
    """The upper (or single) number of a printed range, for the assay comparison; None when not numeric."""
    numbers = re.findall(_NUMBER, printed_range or "")
    return float(numbers[-1]) if numbers else None


def assay_differs(canonical, config, printed_range):
    """Why this printed result must not be scored against the CellDeep range, or None. A test the clinic lists
    as a different assay at this lab, or a printed range more than LAB_RANGE_BASIS_RATIO times above or below
    the CellDeep range basis."""
    if canonical in clinic_config.LAB_ASSAY_DIFFERS.get(LAB_KEY, ()):
        return "a different assay at this lab from the one the CellDeep range is based on"
    basis = config.get("hi") if config.get("hi") is not None else config.get("optimal")
    lab = _range_reference(printed_range)
    ratio = clinic_config.LAB_RANGE_BASIS_RATIO
    if basis and lab and (lab / basis > ratio or basis / lab > ratio):
        return (f"the lab's range ({printed_range}) differs from the CellDeep range basis ({config.get('disp_range')})"
                f" by more than {ratio:g}x")
    return None


def parse(pages, row_audit=None, review_notes=None, exclusions=None, sex=None):
    """Read every page. Returns (marker_occurrences, unrecognized_rows, info); info holds the printed header
    (patient, dob, age, sex, collected, fasting, accession), the staff notes this layout raises, and the rows
    printed without units."""
    pipeline = _pipeline()
    from markers_reference import resolve_marker_config

    occurrences, unrecognized = [], []
    audit = row_audit if row_audit is not None else []
    excluded = exclusions if exclusions is not None else []
    notes = review_notes if review_notes is not None else []
    headers, summary_lines, table, no_units, staff = [], [], {}, [], []
    unsectioned, morning_windows = [], set()
    section = None

    def exclude(page, where, reason):
        excluded.append({"page": page, "section": where, "reason": reason})

    for number, page in enumerate(pages, 1):
        lines = _lines(page)
        if not lines:
            continue
        header = read_header(lines)
        found = _column_header(lines)
        if found is None or "collected" not in header:
            missing = "the 'Test Name / Results / Reference Range / Units' header" if found is None else \
                "a readable 'Coll. Date:'"
            exclude(number, "whole page", f"{NAME} page without {missing}; nothing on it was read")
            continue
        if "printed" in header and pipeline._normalize_date_for_matching(header["collected"]) > \
                pipeline._normalize_date_for_matching(header["printed"]):
            exclude(number, "whole page", f"Coll. Date {header['collected']} is after Print Date "
                                          f"{header['printed']}; the collection date cannot be read reliably")
            continue
        header["page"] = number
        headers.append(header)
        date = header["collected"]
        start, columns = found
        mode = "table"
        for index, words in enumerate(lines):
            text = _text(words)
            # The OUT OF RANGE SUMMARY is recognized by its heading wherever it is printed (before or after the
            # column header, across columns) and its lines are kept only to cross-check the result tables.
            if _SUMMARY_HEADING_RE.match(text):
                mode, section = "summary", None
                continue
            if index == start:
                if mode == "summary":
                    mode = "table"
                continue
            if mode == "summary" and index < start:
                summary_lines.append((text, number))
                continue
            if index < start:
                continue  # the header block
            if _CONTINUED_RE.fullmatch(text) or re.fullmatch(r"\(continued on next page\)", text, re.I):
                continue
            cells, by_column = _split_columns(words, columns)
            only_name = not (cells["results"] or cells["range"] or cells["units"])
            title = _CONTINUED_RE.sub("", cells["name"]).strip()
            if only_name and _TITLE_RE.fullmatch(title) and title == title.upper() and len(title) >= 4:
                mode, section = "table", title
                continue
            if mode == "summary":
                summary_lines.append((text, number))
                continue
            indented = by_column["name"] and by_column["name"][0][0] > columns["name"] + _INDENT_PT
            row, reason = (None, "indented explanatory line") if indented else _row(cells)
            name = " ".join(cells["name"].split())
            if window := _MORNING_WINDOW_RE.search(text):
                morning_windows.add((int(window[1]), int(window[2])))
            if row is None:
                # A known test name on a line that does not fit the row pattern is excluded with a notice, unless
                # that test was already read in this section: then the line is its interpretation text (e.g. the
                # "% Free PSA" probability table under the "% Free PSA" result).
                kind = "urine" if (section or "").upper().startswith("URINALYSIS") else "serum"
                already_read = (kind, name.casefold()) in table and table[(kind, name.casefold())][3] == section
                if cells["results"] and not indented and not already_read and _known(name, section):
                    exclude(number, section or "no section", f"row {name!r} not read: {reason}")
                continue
            value, flag, printed_range, unit = row
            key = name.casefold()
            if section is None:
                # Decided at the end: a repeat of a table row (a summary whose heading was not recognized) is
                # ignored silently; any other row under no section title is excluded with a notice.
                unsectioned.append((number, name, value, flag))
                continue
            scope = _SECTION_SCOPED_NAMES.get(key)
            if scope and not any(word in section.upper() for word in scope):
                exclude(number, section, f"row {name!r} means a different test depending on its section "
                                         f"({', '.join(scope)}); under {section!r} it cannot be told apart, so it "
                                         "was not read")
                continue
            # Urine and serum tests share names (Glucose, Protein, Bilirubin): they are different results.
            key = ("urine" if section.upper().startswith("URINALYSIS") else "serum", key)
            if key in table:
                if table[key][:2] != (value, flag):
                    raise pipeline.BloodworkHardStop(f"{name} on {date}: conflicting results on pages "
                                                     f"{table[key][2]} and {number}")
                continue  # the same row printed twice is read once
            table[key] = (value, flag, number, section)
            label = f"{NAME} Acc# {header.get('accession', '?')} (collected {date}); {section}"
            heading = "Urinalysis" if section.upper().startswith("URINALYSIS") else section
            start_occ, start_unk = len(occurrences), len(unrecognized)
            status, number_value, display = pipeline._cell_result(value)
            # A number the lab printed without units (word results, ratios, pH and specific gravity have none).
            if not unit and (number_value is not None or display.startswith(("<", ">"))) and \
                    not _UNITLESS_RE.search(name):
                no_units.append(name)
            show_as = None
            match = None
            if number_value is not None or display.startswith(("<", ">")):
                try:
                    match = pipeline._match_row_name(name, heading)
                except pipeline.BloodworkParseError as error:
                    exclude(number, section, str(error))
                    continue
            patient_note = None
            fasting = header.get("fasting")
            if match is not None and match[0] in clinic_config.FASTING_ONLY_MARKERS and fasting not in (None, "Y"):
                # Drawn non-fasting (or fasting not confirmed): never labelled or scored as fasting.
                show_as = clinic_config.FASTING_ONLY_MARKERS[match[0]]
                patient_note = clinic_config.NON_FASTING_GLUCOSE_NOTE if fasting == "N" else \
                    clinic_config.FASTING_NOT_CONFIRMED_NOTE
                printed_status = {"N": "'Fasting: N'", "UNKNOWN": "'Fasting: Unknown'",
                                  "BLANK": "a blank 'Fasting:'"}.get(fasting, f"'Fasting: {fasting}'")
                label = "GLUCOSE" if match[0] == "Glucose (fasting)" else "INSULIN"
                staff.append(f"NON-FASTING {label}: the lab header prints {printed_status}; {name} {display!r} on {date} "
                             f"(page {number}) is shown as {show_as[0]!r} with the lab's range "
                             f"({printed_range or 'none printed'}) and flag, not scored against the fasting range")
                match = None
            if match is not None:
                canonical, config = match
                resolved = resolve_marker_config(canonical, config, sex)
                differs = assay_differs(canonical, resolved, printed_range)
                if differs is None and unit and resolved.get("unit") and _unit_key(unit) != _unit_key(resolved["unit"]):
                    differs = f"printed in {unit}, not the {resolved['unit']} the CellDeep range uses"
                if differs:
                    show_as = (canonical, _GROUP_BY_CATEGORY.get(resolved.get("category"), "Chemistry"))
                    staff.append(f"ASSAY DIFFERS FROM CELLDEEP RANGE BASIS: {canonical} {display!r} on {date} "
                                 f"(page {number}) is {differs}; shown as lab-reported with the lab's range "
                                 f"({printed_range or 'none printed'}) and flag, not scored - clinic decision, "
                                 "docs/open_decisions.md")
                    match = None
            if match is not None:
                lab_lo, lab_hi, lab_display = pipeline._printed_lab_range(printed_range.split())
                occurrences.append({
                    "name": match[0], "date_display": date, "source_label": label, "status": status,
                    "value": number_value, "disp_value": display, "is_good": None,
                    "lab_range_lo": lab_lo, "lab_range_hi": lab_hi, "lab_range_display": lab_display or printed_range,
                    "lab_flag": flag or "", "printed_unit": unit,
                })
            else:
                unknown = {
                    "raw_name": name, "raw_value": value, "raw_unit": unit, "raw_range": printed_range,
                    "source_context": label, "section_heading": section,
                    "cells": [{"kind": "current", "date_display": date, "status": status, "value": number_value,
                               "disp_value": display, "present": True, "lab_flag": flag}],
                }
                if show_as:
                    unknown["show_as"] = show_as
                if patient_note:
                    unknown["patient_note"] = patient_note
                unrecognized.append(unknown)
            audit.append({"page": number, "name": name, "section": section, "y": min(w[1] for w in words),
                          "occurrences": [dict(item) for item in occurrences[start_occ:]],
                          "unrecognized": unrecognized[start_unk:]})

    staff.extend(summary_checks(summary_lines, table))
    for number, name, value, flag in unsectioned:
        if not any(key == name.casefold() and printed[:2] == (value, flag) for (_, key), printed in table.items()):
            exclude(number, "no section", f"row {name!r} is printed under no section title; not read")
    info = {"layout": NAME, "headers": headers, "no_units": no_units, "notes": staff}
    if headers:
        if len(morning_windows) == 1:
            info["morning_window"] = morning_windows.pop()
        for field in ("patient", "dob", "age", "sex", "collected", "fasting", "accession", "collection_time"):
            values = {header[field] for header in headers if field in header}
            if len(values) == 1:
                info[field] = values.pop()
            elif values:
                staff.append(f"LAB HEADER DISAGREES: pages print different {field} values; not used")
        dates = {header["collected"] for header in headers}
        accessions = {}
        for header in headers:
            accessions.setdefault(header.get("accession"), set()).add(header["collected"])
        if any(len(found) > 1 for found in accessions.values()):
            raise pipeline.BloodworkHardStop(f"{NAME}: one Acc# prints two Coll. Dates {sorted(dates)}")
    if no_units:
        staff.append("UNITS NOT PRINTED: the lab printed no units for " + ", ".join(dict.fromkeys(no_units))
                     + "; the report shows these results without units (none are filled in)")
    notes.extend(staff)
    return occurrences, unrecognized, info


def summary_checks(summary_lines, table):
    """Each OUT OF RANGE SUMMARY line must repeat a row printed in a result table (same name, value and flag).
    Lines that do (the normal case) are ignored silently; a line naming no table row, or printing a different
    value or flag, is a staff notice. Lines with no digit (the block's own column labels, notes) are skipped."""
    rows = {}  # printed name (casefolded) -> [(value, flag, page)]
    for (_, key), (value, flag, page, _section) in table.items():
        rows.setdefault(key, []).append((value, flag, page))
    notes = []
    for text, page in summary_lines:
        line = " ".join(text.split())
        if not re.search(r"\d", line):
            continue
        names = [name for name in rows if line.casefold().startswith(name + " ")]
        if not names:
            notes.append(f"OUT OF RANGE SUMMARY: {line!r} (page {page}) matches no row in the result tables; "
                         "not used - check the PDF")
            continue
        name = max(names, key=len)
        tokens = line[len(name):].split()
        if len(tokens) > 1 and tokens[0] in ("<", ">", "<=", ">="):
            tokens = [tokens[0] + tokens[1], *tokens[2:]]
        match = _RESULT_RE.fullmatch(" ".join(tokens[:2])) if len(tokens) > 1 and tokens[1] in _FLAGS else None
        match = match or (_RESULT_RE.fullmatch(tokens[0]) if tokens else None)
        printed = (match["value"].replace(" ", ""), match["flag"]) if match else None
        if printed not in [(value, flag) for value, flag, _ in rows[name]]:
            table_value = "; ".join(f"{value} {flag or ''}".strip() + f" (page {row_page})"
                                    for value, flag, row_page in rows[name])
            notes.append(f"OUT OF RANGE SUMMARY DISAGREES: {line!r} (page {page}) does not match the result table "
                         f"({table_value}); the table value is used - check the PDF")
    return notes


def _known(name, section):
    """True when the printed name is a test the tool knows (scored or lab-reported)."""
    pipeline = _pipeline()
    import lab_reported

    heading = "Urinalysis" if (section or "").upper().startswith("URINALYSIS") else section
    try:
        if pipeline._match_row_name(name, heading) is not None:
            return True
    except pipeline.BloodworkParseError:
        return True
    return lab_reported.lookup(name, section) is not None
