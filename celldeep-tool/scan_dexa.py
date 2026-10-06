"""DEXA pages: two independent literal vision reads per page and deterministic agreement gates.

Every DEXA PDF page is rendered and read twice with the same API wrapper and model as the scanned
bloodwork route. A measurement is kept only when both reads agree after normalization; a field
only one read printed, or that the reads disagree on, is excluded and listed for staff. A scan
date the two reads disagree on excludes that whole scan. A page that prints a different patient
name than the staff entry is excluded. Kept scans are grouped by date across pages, merged, and
sorted oldest first, so the history never depends on page order or model output order.
Values the scanner marks "(e)" are kept and flagged as estimated. Nothing is computed, converted
or carried between dates here.
"""

import json
import re
import statistics
from datetime import date as calendar_date

import clinic_config
from scan_bloodwork import ScanGateError, _nullable, _object, _validate, name_key, names_match, render_page_png_b64
from schema import normalize_date_for_matching

_TEXT = _nullable({"type": "string"})
FIELDS = {  # schema field -> (DexaReading field, staff label)
    "total_mass": ("total_mass_lb", "total mass (lb)"),
    "fat_mass": ("fat_mass_lb", "fat mass (lb)"),
    "lean_mass": ("lean_mass_lb", "lean mass (lb)"),
    "body_fat_pct": ("body_fat_pct", "body fat %"),
    "vat_mass": ("vat_fat_mass_lb", "VAT mass (lb)"),
    "vat_area": ("visceral_fat_area_cm2", "VAT area (cm2)"),
}
_SCAN = _object({"date": _TEXT, "age": _TEXT, **{name: _TEXT for name in FIELDS}})
DEXA_SCHEMA = _object({
    "page": {"type": "integer"}, "patient_name": _TEXT, "date_of_birth": _TEXT, "age": _TEXT,
    "illegible": {"type": "boolean"},
    "scans": {"type": "array", "items": _SCAN},
})
DEXA_PROMPT = """Transcribe this DEXA body composition report page literally. Never infer, calculate,
convert, correct, or complete anything. Return only the supplied JSON schema.
For every scan date printed on this page (each row of a results or trend table, or the single scan
date of a summary page) return one entry in scans with the date exactly as printed and these
measurements exactly as printed for that date, including any "(e)" estimate marker:
total_mass, fat_mass and lean_mass in pounds (lb); body_fat_pct (percent body fat, total body);
vat_mass (visceral adipose tissue mass in lb); vat_area (visceral adipose tissue area in cm2).
A measurement that is not printed for that date, or is printed only in other units, is null.
Never copy a value from one date to another. Copy the patient name, date of birth and the patient's
age exactly as printed on the page, or null when not printed. When an age is printed for a scan row,
copy it into that scan's age exactly as printed, or null.
Use the PDF page number provided. Illegible fields are null with illegible=true, never guessed.
A page with no scan measurements returns scans: []."""

_ESTIMATE_RE = re.compile(r"\(\s*e\s*\)", re.IGNORECASE)
_MEASURE_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?:%|lbs?|cm2|cm²|cm\^2)?", re.IGNORECASE)


def read_page(page, label, client, create_message, model):
    """Two independent literal reads of one DEXA page."""
    image = render_page_png_b64(page)
    reads = []
    try:
        for reading in (1, 2):
            try:
                reads.append(_read(page, image, label, reading, client, create_message, model))
            except ScanGateError as error:
                error.reads = reads
                raise
    finally:
        del image
    return reads


def _read(page, image, label, reading, client, create_message, model):
    response = create_message(
        client, f"DEXA extraction {label} read {reading}",
        model=model, max_tokens=8000, system=DEXA_PROMPT,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
            {"type": "text", "text": f"Transcribe DEXA PDF page {page.number + 1} independently."},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": DEXA_SCHEMA}},
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ScanGateError(f"schema gate: truncated response on {label}")
    raw = "".join(block.text for block in response.content if hasattr(block, "text"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ScanGateError(f"schema gate: invalid JSON on {label}") from error
    _validate(data, DEXA_SCHEMA)
    if data["page"] != page.number + 1:
        raise ScanGateError(f"page gate: wrong page number on {label}")
    return data


def measure(text):
    """'34.4 %' -> (34.4, '34.4', False); '1.23 (e)' -> (1.23, '1.23', True); None -> None.
    Raises ValueError for anything that is not one printed number with an optional unit."""
    if text is None:
        return None
    estimated = bool(_ESTIMATE_RE.search(text))
    match = _MEASURE_RE.fullmatch(_ESTIMATE_RE.sub("", text).strip())
    if not match:
        raise ValueError(text)
    return float(match["num"]), match["num"], estimated


def scan_date(text):
    """Printed scan date -> (y, m, d), or None when it is not a readable calendar date."""
    normalized = normalize_date_for_matching(text or "")
    if not isinstance(normalized, tuple):
        return None
    try:
        calendar_date(*normalized)
    except ValueError:
        return None
    return normalized


def _display(day):
    return f"{day[1]:02d}/{day[2]:02d}/{day[0]:04d}"


_AGE_RE = re.compile(r"\s*(?:age\s*:?\s*)?(\d{1,3}(?:\.\d+)?)\s*(?:y|yr|yrs|years?|years old)?\.?\s*",
                     re.IGNORECASE)


def _printed(reads, field):
    return [" ".join((read.get(field) or "").split()) for read in reads]


def _read_ages(read):
    """Every age one read prints on the page (header and scan rows) as (value, text), or None when a
    printed age is not a readable number."""
    ages = []
    for text in [read.get("age"), *(scan.get("age") for scan in read["scans"])]:
        text = " ".join((text or "").split())
        if not text:
            continue
        match = _AGE_RE.fullmatch(text)
        if match is None:
            return None
        ages.append((float(match[1]), match[1]))
    return sorted(ages)


def page_age(reads):
    """The patient's latest age printed on the page as (value, printed text), or None when no age is
    printed, a printed age is unreadable, or the two reads do not print the same ages."""
    per_read = [_read_ages(read) for read in reads]
    if any(ages is None for ages in per_read) or any(ages != per_read[0] for ages in per_read) or not per_read[0]:
        return None
    return per_read[0][-1]


def page_identity(reads, patient_name):
    """'named' (both reads print the staff-entered patient), 'unnamed' (no name confirmed by both reads,
    and none that differs) or 'other' (a read prints a different name)."""
    names = [name for name in _printed(reads, "patient_name") if name_key(name)]
    if any(not names_match(patient_name, name) for name in names):
        return "other"
    return "named" if len(names) == len(reads) else "unnamed"


def _pages_text(keys):
    return ", ".join(f"file {key[0]} page {key[1]}" for key in keys)


def incomplete_notice(exclusions):
    """INCOMPLETE lines for the top of the staff notes: DEXA pages not attributed to this patient."""
    if not exclusions:
        return []
    labels = ", ".join(f"file {key[0]} page {key[1]}" for key, _ in exclusions)
    return [f"INCOMPLETE - pages excluded: DEXA {labels} not confirmed as this patient's; their scans are NOT "
            "in this report (history, current values or summary) - confirm which patient they belong to",
            *[f"  - DEXA file {key[0]} page {key[1]}: {reason}" for key, reason in exclusions]]


def attribute_pages(pages, patient_name, staff_age=None):
    """Which DEXA pages belong to the staff-entered patient. Returns (accepted keys, unnamed accepted keys,
    [(key, reason)] excluded).
    - A page printing a different name is excluded.
    - A page that prints the patient's name is accepted.
    - A page with no confirmed name is accepted only when it prints an age within
      clinic_config.DEXA_AGE_TOLERANCE_YEARS of the median age printed across the pages, and of the
      staff-entered age when one was entered. The median needs a clear cluster: more than half of the
      pages that print an age must lie within the tolerance of it, otherwise no unnamed page is accepted.
      An unnamed page that prints no readable age is never accepted."""
    if not patient_name:
        return [key for key, _ in pages], [], []
    tolerance = clinic_config.DEXA_AGE_TOLERANCE_YEARS
    excluded, named, unnamed = [], [], []
    for key, reads in sorted(pages, key=lambda item: item[0]):
        identity = page_identity(reads, patient_name)
        if identity == "other":
            names = [name for name in _printed(reads, "patient_name") if name_key(name)]
            printed = repr(names[0]) if len(set(names)) == 1 else " / ".join(map(repr, names))
            excluded.append((key, f"prints patient name {printed}, not the staff-entered patient"))
        elif identity == "named":
            named.append((key, page_age(reads)))
        elif (age := page_age(reads)) is None:
            excluded.append((key, "no patient name and no readable age printed (or the two reads differ); "
                                  "cannot confirm it is this patient's"))
        else:
            unnamed.append((key, age))
    ages = [age[0] for _, age in named + unnamed if age is not None]
    kept = []
    if unnamed:
        center = statistics.median(ages)
        clustered = sum(abs(age - center) <= tolerance for age in ages) * 2 > len(ages)
        for key, (value, text) in unnamed:
            if not clustered:
                listed = ", ".join(f"{age:g}" for age in sorted(ages))
                excluded.append((key, f"no patient name printed and it prints age {text}; the DEXA pages' ages "
                                      f"({listed}) form no clear cluster, so no unnamed page is attributed"))
            elif abs(value - center) > tolerance:
                excluded.append((key, f"no patient name printed and it prints age {text}; the DEXA pages center "
                                      f"on age {center:g}"))
            elif staff_age is not None and abs(value - staff_age) > tolerance:
                excluded.append((key, f"no patient name printed and it prints age {text}; the staff-entered age "
                                      f"is {staff_age}"))
            else:
                kept.append(key)
    accepted = sorted([key for key, _ in named] + kept)
    return accepted, kept, sorted(excluded)


def gate(pages, patient_name, failures=(), dob_sink=None, excluded_sink=None, staff_age=None):
    """pages: [((file, page), [read1, read2])]; failures: [((file, page), partial_reads, reason)].
    Returns (history, staff_notes, summary_lines). history is sorted oldest first and is a list of
    DexaReading-shaped dicts with an extra 'estimated' list of DexaReading field names.
    Pages are attributed by attribute_pages; an excluded page is dropped whole and (key, reason) is
    appended to excluded_sink (reasons may hold the printed name, so they go to the staff notes only,
    never to logs)."""
    notes, mismatches, page_lines, kept_by_page = [], [], [], {}
    candidates = {}  # date -> field -> {(value, text, estimated): [page labels]}

    def label_of(key):
        return f"DEXA file {key[0]} page {key[1]}"

    def log(key, counts, kept, excluded, verdict, reason):
        print(f"source=dexa file={key[0]} page={key[1]} scans_read={json.dumps(counts[0])}/"
              f"{json.dumps(counts[1])} scans_kept={kept} fields_excluded={excluded} verdict={verdict} "
              f"reason={json.dumps(reason)}")

    accepted, unnamed, excluded = attribute_pages(pages, patient_name, staff_age)
    if excluded_sink is not None:
        excluded_sink.extend(excluded)
    excluded_why = dict(excluded)
    if unnamed:
        mismatches.append(f"STAFF REVIEW - DEXA name not printed: DEXA {_pages_text(unnamed)} print no patient "
                          "name confirmed by both reads; accepted for the staff-entered patient - confirm they "
                          "are this patient's")
    for key, reads in sorted(pages, key=lambda item: item[0]):
        label = label_of(key)
        counts = [len(read["scans"]) for read in reads]
        if key not in accepted:
            if excluded_why[key].startswith("prints patient name"):
                heading, problem = "PATIENT NAME MISMATCH", "prints a patient name that differs from the staff-entered name"
            elif excluded_why[key].startswith("no patient name and no readable age"):
                heading, problem = "AGE NOT PRINTED", "prints no patient name and no readable age"
            else:
                heading, problem = "AGE MISMATCH", "prints no patient name and an age that does not fit this patient"
            mismatches.append(f"STAFF REVIEW - DEXA {heading}: {label} {problem}; page excluded - "
                              "confirm which patient it belongs to")
            log(key, counts, 0, 0, "excluded", f"patient gate: {problem}")  # never the printed name or age
            continue
        if dob_sink is not None:
            dobs = {scan_date(read.get("date_of_birth")) for read in reads}
            if len(dobs) == 1 and None not in dobs:
                dob_sink.append((f"{label}", dobs.pop()))
        by_date, unreadable = [], 0
        for read in reads:
            dates = {}
            for scan in read["scans"]:
                day = scan_date(scan["date"])
                if day is None:
                    unreadable += 1
                    continue
                dates.setdefault(day, []).append(scan)
            by_date.append(dates)
        page_excluded = 0
        kept_dates = []
        for day in sorted(set(by_date[0]) | set(by_date[1])):
            first, second = by_date[0].get(day, []), by_date[1].get(day, [])
            if len(first) != 1 or len(second) != 1:
                reason = ("date gate: scan date printed in only one read or read differently; whole scan excluded"
                          if not first or not second else "date gate: scan date appears twice in one read; "
                                                          "whole scan excluded")
                notes.append(f"{label}: scan {_display(day)} excluded - {reason}")
                page_excluded += 1
                continue
            kept_fields = 0
            for field, (_, field_label) in FIELDS.items():
                try:
                    a, b = measure(first[0][field]), measure(second[0][field])
                except ValueError:
                    notes.append(f"{label}: scan {_display(day)} {field_label} excluded - unreadable printed value")
                    page_excluded += 1
                    continue
                if a is None and b is None:
                    continue
                if a is None or b is None:
                    reason = "printed in only one read"
                elif a[0] != b[0] or a[2] != b[2]:
                    reason = "independent reads disagree"
                else:
                    text = min(a[1], b[1], key=lambda t: (len(t), t))
                    candidates.setdefault(day, {}).setdefault(field, {}).setdefault(
                        (a[0], text, a[2]), []).append(label)
                    kept_fields += 1
                    continue
                notes.append(f"{label}: scan {_display(day)} {field_label} excluded - {reason}")
                page_excluded += 1
            if kept_fields:
                kept_dates.append(day)
        if unreadable:
            notes.append(f"{label}: {unreadable} scan row(s) without a readable scan date excluded")
            page_excluded += unreadable
        kept_by_page[key] = kept_dates
        if not counts[0] and not counts[1]:
            page_lines.append(f"{label}: no scan measurements on this page; notice only")
            log(key, counts, 0, 0, "notice", "no scan measurements")
        else:
            verdict = "kept" if kept_dates else "excluded"
            log(key, counts, len(kept_dates), page_excluded, verdict,
                "passed" if kept_dates else "no measurement passed both reads")
    for key, partial, reason in sorted(failures, key=lambda item: item[0]):
        page_lines.append(f"{label_of(key)}: {reason}; page excluded")
        counts = [len(read["scans"]) for read in partial] + [None] * (2 - len(partial))
        log(key, counts, 0, 0, "excluded", reason)

    history = []
    for day in sorted(candidates):
        reading = {"date_display": _display(day), "estimated": []}
        for field, (target, field_label) in FIELDS.items():
            options = candidates[day].get(field, {})
            values = {value for value, _, _ in options}
            if len(values) > 1 or len({estimated for _, _, estimated in options}) > 1:
                pages_listed = sorted({page for labels in options.values() for page in labels})
                notes.append(f"DEXA scan {_display(day)} {field_label} excluded - pages {', '.join(pages_listed)} "
                             "print different values")
                reading[target] = None
                continue
            if not options:
                reading[target] = None
                continue
            value, text, estimated = min(options, key=lambda option: (len(option[1]), option[1]))
            reading[target] = f"{text}%" if field == "body_fat_pct" else value
            if estimated:
                reading["estimated"].append(target)
        if any(reading[target] is not None for target, _ in FIELDS.values()):
            history.append(reading)
    summary = []
    if pages or failures:
        summary = [f"DEXA - {len(pages) + len(failures)} page(s) read twice; {len(history)} scan date(s) kept",
                   *[f"  {line}" for line in mismatches]]
        for reading in history:
            count = sum(reading[target] is not None for target, _ in FIELDS.values())
            estimated = f", estimated: {', '.join(reading['estimated'])}" if reading["estimated"] else ""
            summary.append(f"  Kept scan {reading['date_display']}: {count} measurement(s) agreed by both reads"
                           f"{estimated}")
        summary.append(f"  Excluded: {len(notes)} item(s)")
        summary += [f"    - {note}" for note in notes]
        if page_lines:
            summary.append("  Page outcomes:")
            summary += [f"    - {line}" for line in page_lines]
    return history, notes, summary
