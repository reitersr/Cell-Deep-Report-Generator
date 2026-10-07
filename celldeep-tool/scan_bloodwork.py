"""Literal scan transcription and deterministic gates; no scoring or inferred results."""

import base64
import json
import re
from datetime import date as calendar_date

import fitz

SCAN_RENDER_DPI = 200  # changing this changes what the vision model sees; keep it fixed


# Run-to-run consistency comes from the read-agreement rule, not from sampling settings: the SDK in use
# (anthropic 1.x) accepts no temperature/top_p/top_k and offers no seed, and temperature 0 never guaranteed
# identical output anyway. Every scanned page is read twice; when the two reads disagree on any result row,
# a third independent read is made (SCAN_MAX_READS) and a row is kept only when at least two reads print exactly
# the same value, flag and reference range (SCAN_AGREEMENT_READS). A row no two reads agree on is excluded and
# listed for staff with every read's value, so the report never depends on which read "won". A row no read
# prints a value for (a section heading such as "CBC (INCLUDES DIFF/PLT)") is not a result and is never listed
# as an excluded result. Retries are transport-level only (the client's max_retries, see pipeline); every
# remaining tie-break in the gates (e.g. strip_lab_code) is ordered explicitly.
SCAN_MAX_READS = 3
SCAN_AGREEMENT_READS = 2
# A row that some reads print and others simply leave out ("not read / 1214 H / not read") is a missing read, not a
# disagreement: the page is read again, each further read asked to look for those rows, until two reads print the row
# identically or SCAN_TARGETED_MAX_READS reads have been made. A single read is never enough.
SCAN_TARGETED_MAX_READS = 5


def _read_values(rows):
    """What one read printed for a row, for the staff notes: value and flag, 'not read' or 'read twice'."""
    if not rows:
        return "not read"
    texts = [" ".join(filter(None, [row["result_text"] or "illegible", row["flag"]])) for row in rows]
    return texts[0] if len(texts) == 1 else "read twice: " + ", ".join(texts)


def render_page_png_b64(page, dpi=SCAN_RENDER_DPI):
    """One page as a base64 PNG, holding only one page's render at a time. The pixmap and PNG bytes are
    released before returning, and MuPDF's decoded-image cache (up to 256MB by default) is emptied so
    a scanned page's full-resolution image does not stay in memory after it is rendered."""
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    png = pixmap.tobytes("png")
    del pixmap
    fitz.TOOLS.store_shrink(100)
    encoded = base64.b64encode(png).decode("ascii")
    del png
    return encoded


class ScanGateError(ValueError):
    pass


def _object(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


_TEXT = _nullable({"type": "string"})
_FLAG = _nullable({"type": "string", "enum": ["H", "L"]})
_SUMMARY_ROW = _object({
    "name": _TEXT, "result_text": _TEXT, "flag": _FLAG, "illegible": {"type": "boolean"},
})
_ROW = _object({
    **_SUMMARY_ROW["properties"],
    "column": _nullable({"type": "string", "enum": ["in_range", "out_of_range"]}),
    "reference_range": _TEXT, "lab_code": _TEXT, "page": {"type": "integer"}, "section": _TEXT,
})
SCAN_SCHEMA = _object({
    "page": {"type": "integer"}, "specimen_id": _TEXT, "collected": _TEXT,
    "footer": _TEXT,
    "patient_name": _TEXT, "date_of_birth": _TEXT, "illegible": {"type": "boolean"},
    "rows": {"type": "array", "items": _ROW},
    "out_of_range_summary": _nullable({"type": "array", "items": _SUMMARY_ROW}),
    "urine_note_printed": {"type": "boolean"},
})
SCAN_PROMPT = """Transcribe this lab page literally. Never infer, calculate, correct, or complete text.
Return only the supplied JSON schema. Copy names, result_text, reference_range, section headings,
patient_name, footer SPECIMEN id, full footer text (including PAGE n OF m), and Collected date
exactly as printed. Use the PDF page number
provided, not the report's own printed page number. Classify each result by its printed In Range
or Out of Range column. Keep H/L separate from result_text. Copy the printed Lab column code
(performing-site code) into lab_code; never append it to the name or reference_range; null if no
Lab column. Do not transcribe reference values,
footnotes, interpretations, or summary entries as result rows. Preserve URINALYSIS section identity.
Copy every entry of LIST OF RESULTS PRINTED IN THE OUT OF RANGE COLUMN verbatim into
out_of_range_summary; null means no such block, [] means a printed empty block.
Copy the patient's date of birth exactly as printed into date_of_birth, or null.
Absent metadata is null. Illegible fields are null with illegible=true, never guessed.
Do not infer specimen ids, dates, patient names, section headings, or flags from other pages.
Set urine_note_printed to true only when this page prints a note saying the urine was analyzed (such as
"This urine was analyzed for the presence of ..."); otherwise false."""

_COUNT_RANGE_RE = re.compile(r"\d{1,3}\s*-\s*\d{1,3}")  # "0-5", "6-10": a microscopy count range (per HPF/LPF)


def is_text_result(text, reference_range=None) -> bool:
    """A printed result that is words or a count, not a number: a status ("Negative", "Trace", "None Seen"), a
    dipstick grade ("1+") or a whole-number count range ("0-5", "6-10", as microscopy prints it; never the row's own
    reference range copied into the result)."""
    folded = " ".join((text or "").split())
    if folded.casefold() in _STATUSES or re.fullmatch(r"[1-4]\+", folded):
        return True
    return bool(_COUNT_RANGE_RE.fullmatch(folded)) and _canonical_range(folded) != _canonical_range(reference_range)


_STATUSES = {
    "negative", "positive", "none seen", "yellow", "clear", "no culture indicated",
    "see note: not reported", "not applicable", "test not performed", "not performed", "tnp",
    "detected", "not detected", "trace", "normal", "abnormal", "reactive", "non-reactive",
}


def _validate(value, schema, location="page"):
    actual = ("null" if value is None else "boolean" if isinstance(value, bool) else
              "integer" if isinstance(value, int) else "string" if isinstance(value, str) else
              "array" if isinstance(value, list) else "object" if isinstance(value, dict) else "invalid")
    if "anyOf" in schema:
        branches = [branch for branch in schema["anyOf"] if branch.get("type") == actual]
        if len(branches) != 1:
            raise ScanGateError(f"schema gate: invalid {location}")
        return _validate(value, branches[0], location)
    types = schema["type"]
    types = types if isinstance(types, list) else [types]
    if actual not in types or ("enum" in schema and value not in schema["enum"]):
        raise ScanGateError(f"schema gate: invalid {location}")
    if actual == "object":
        if set(value) != set(schema["properties"]):
            raise ScanGateError(f"schema gate: missing or extra fields in {location}")
        for key, child in schema["properties"].items():
            _validate(value[key], child, f"{location}.{key}")
    if actual == "array":
        for index, child in enumerate(value):
            _validate(child, schema["items"], f"{location}[{index}]")


def read_page(page, client, create_message, model, max_reads=2):
    """Independent reads of one page, all sent the same single render, which is released on return. Two reads
    always; a third (when max_reads allows it) only when the first two disagree on a result row. A read after the
    second that fails its schema or page gate is dropped: the earlier reads still stand, and the rows they disagree
    on are excluded. With the third read allowed, a row some reads print and others leave out is read again
    (targeted reads, up to SCAN_TARGETED_MAX_READS in all) until two reads print it identically."""
    image = render_page_png_b64(page)
    reads = []
    try:
        for reading in (1, 2):
            try:
                reads.append(_read_transcription(page, image, reading, client, create_message, model))
            except ScanGateError as error:
                error.reads = reads
                raise
        if max_reads >= 3 and reads_disagree(reads):
            try:
                reads.append(_read_transcription(page, image, 3, client, create_message, model))
            except ScanGateError as error:
                print(f"source=scan page={page.number + 1} third read failed: {json.dumps(str(error))}; "
                      "rows the first two reads disagree on stay excluded")
        while max_reads >= 3 and len(reads) >= 3 and len(reads) < SCAN_TARGETED_MAX_READS \
                and (missing := rows_missing_from_reads(reads)):
            try:
                reads.append(_read_transcription(page, image, len(reads) + 1, client, create_message, model,
                                                 focus=missing))
            except ScanGateError as error:
                print(f"source=scan page={page.number + 1} targeted read failed: {json.dumps(str(error))}; "
                      "rows still missing from the reads stay excluded")
                break
    finally:
        del image
    return reads


def rows_missing_from_reads(reads):
    """The result rows (printed names, in reading order) that at least one read prints with a value and at least
    one read leaves out entirely, and that two reads do not yet print identically. Rows every read prints, however
    differently, are a disagreement for the gate, not a missing read."""
    codes = _lab_codes(reads)
    missing = []
    for (name, _section), rows in _row_identities(reads, codes).items():
        if all(is_heading_row(row) for read_rows in rows for row in read_rows):
            continue
        valued = [read_rows for read_rows in rows if any(not is_heading_row(row) for row in read_rows)]
        if valued and any(not read_rows for read_rows in rows) and _agreed(rows, SCAN_AGREEMENT_READS) is None \
                and name and name not in missing:
            missing.append(name)
    return missing


def _read_transcription(page, image, reading, client, create_message, model, focus=()):
    text = f"Transcribe PDF page {page.number + 1} independently."
    if focus:
        # Names only, never values: the read must still find and transcribe them on the page itself.
        text += (" Earlier reads of this page disagree on whether these rows are printed; look for them carefully "
                 "and transcribe every row of the page as usual: " + "; ".join(focus) + ".")
    response = create_message(
        client, f"bloodwork scan page {page.number + 1} read {reading}",
        model=model, max_tokens=16000, system=SCAN_PROMPT,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
            {"type": "text", "text": text},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": SCAN_SCHEMA}},
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ScanGateError(f"schema gate: truncated response on page {page.number + 1}")
    raw = "".join(block.text for block in response.content if hasattr(block, "text"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ScanGateError(f"schema gate: invalid JSON on page {page.number + 1}") from error
    # A stored or older transcription without the urine-note field: absent means not printed, so it can never
    # make a page a urinalysis page.
    if isinstance(data, dict):
        data.setdefault("urine_note_printed", False)
    _validate(data, SCAN_SCHEMA)
    if data["page"] != page.number + 1 or any(row["page"] != data["page"] for row in data["rows"]):
        raise ScanGateError(f"page gate: wrong page number on page {page.number + 1}")
    return data


def name_key(name):
    return tuple(sorted(re.findall(r"[a-z]+", name.casefold())))


def _words(text):
    return re.findall(r"[a-z]+", text.casefold())


def names_match(staff_name, printed_name):
    """True when a printed name is the staff-entered patient: the same words in any order and case, or
    the staff entry is the first name plus the last-name initial ("Pat S" for "SYNTHETIC, PAT" or
    "Pat Synthetic"), in either order. Nothing else matches; no fuzzy or partial matching."""
    if not staff_name or not printed_name or not name_key(printed_name):
        return False
    if name_key(staff_name) == name_key(printed_name):
        return True
    staff = _words(staff_name)
    if "," in printed_name:
        last, rest = printed_name.split(",", 1)
        last_words, first_words = _words(last), _words(rest)
    else:
        words = _words(printed_name)
        last_words, first_words = words[-1:], words[:-1]
    if len(staff) != 2 or not last_words or not first_words:
        return False
    first, last_initial = first_words[0], last_words[0][0]
    return any(full == first and initial == last_initial
               for full, initial in (staff, staff[::-1]) if len(initial) == 1)


def digital_patient_names(pages, group_lines, page_words):
    names = set()
    for page in pages:
        for line in group_lines(page_words(page), 3):
            text = " ".join(word[4] for word in line)
            explicit = re.search(r"\bPatient(?: Name)?:\s*([A-Za-z ,'-]+)", text, re.I)
            if explicit:
                # The name ends where the next printed label on the same line begins ("DOB:", "Sex", ...).
                name = re.split(r"\s+(?:DOB|D\.?O\.?B|Date of Birth|Birth|Sex|Gender|Age|ID|MRN|Phone|Acct)\b",
                                explicit[1], maxsplit=1, flags=re.I)[0]
                names.add(name.strip(" ,"))
            elif line[0][1] < 50 and re.fullmatch(r"[A-Z][A-Z '-]+,\s*[A-Z][A-Z '-]+", text):
                names.add(text)
    return names


def staff_collected_date(text):
    """Return a staff-entered Collected date (MM/DD/YYYY or YYYY-MM-DD) as MM/DD/YYYY."""
    us = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", text or "")
    iso = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*", text or "")
    if not (us or iso):
        raise ValueError(f"Collected date {text!r} must be MM/DD/YYYY")
    month, day, year = map(int, us.groups() if us else (iso[2], iso[3], iso[1]))
    try:
        calendar_date(year, month, day)
    except ValueError as error:
        raise ValueError(f"Collected date {text!r} is not a calendar date") from error
    return f"{month:02d}/{day:02d}/{year:04d}"


def _row_key(row):
    return row["name"], row["result_text"], row["flag"]


_UNIT = r"(?:\s+[a-zµμ%][a-z0-9µμ%/.^]*)?"


def _canonical_range(text):
    """One spelling for one printed range: '> OR = 5.4', '>OR=5.4' and '≥ 5.4' all read '>=5.4'.
    Only notation and spacing change; numbers, units and wording are compared as printed."""
    if text is None:
        return None
    text = " ".join(text.split()).casefold().replace("≤", "<=").replace("≥", ">=")
    text = re.sub(r"([<>])\s*or\s*=", r"\1=", text)
    text = re.sub(r"\s*(<=|>=|<|>)\s*", r"\1", text)
    return re.sub(r"(\d)\s*[-–]\s*(?=\d)", r"\1-", text)


def strip_lab_code(name, codes):
    """Remove a printed Lab column code that the transcription attached to the end of a test
    name, e.g. 'TESTOSTERONE, TOTAL, MS AMD' or '... (AMD)'. A code after a comma is part of
    the name ('ESTROGENS, TOTAL, IA') and is kept."""
    for code in sorted(codes, key=lambda code: (-len(code), code)):
        code = re.escape(code)
        stripped = re.sub(rf"\s*\(\s*{code}\s*\)$", "", name)
        stripped = re.sub(rf"(?<=[^,\s])\s+{code}$", "", stripped)
        if stripped.strip() and stripped != name:
            return stripped.strip()
    return name


def _numeric_flag(row, result_re):
    result = result_re.fullmatch(row["result_text"])
    if result is None:
        return True
    if result["flag"] and result["flag"] != row["flag"]:
        return False
    reference = _canonical_range(row["reference_range"])
    if not reference:
        return True
    bounded = re.fullmatch(r"(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)" + _UNIT, reference)
    single = re.fullmatch(r"(<=|>=|<|>)(-?\d+(?:\.\d+)?)" + _UNIT, reference)
    if bounded:
        low, high = map(float, bounded.groups())
        if low > high:
            return False
        low_inclusive = high_inclusive = True
    elif single:
        op, value = single.groups()
        low, high = (float(value), float("inf")) if op in (">", ">=", "≥") else (float("-inf"), float(value))
        low_inclusive = high_inclusive = op in ("<=", ">=", "≤", "≥")
    else:
        return True
    value = float(result["num"])
    comparator = result["ineq"] or ""
    if comparator.startswith(("<", "≤")):
        expected = "L" if value < low or (
            value == low and (comparator == "<" or not low_inclusive)) else None
        determinate = expected is not None
    elif comparator.startswith((">", "≥")):
        expected = "H" if value > high or (
            value == high and (comparator == ">" or not high_inclusive)) else None
        determinate = expected is not None
    else:
        expected = "L" if value < low or (value == low and not low_inclusive) else (
            "H" if value > high or (value == high and not high_inclusive) else None)
        determinate = True
    if determinate and row["flag"] != expected and not comparator and _in_flagged_band(row, single, value, expected):
        return True
    return row["flag"] == expected if determinate else row["flag"] is None


def _in_flagged_band(row, single, value, expected):
    """Risk-category pages (Optimal / Moderate / High columns) print bands, not one normal range; the transcribed
    reference_range may be any one band. A result is consistent with the band its flag or pill points to: an H
    whose value sits inside a printed high band (">3.0", ">=200"), an L inside a printed low band ("<29.2"), or a
    result in the printed Non-Optimal (out-of-range) column with no letter flag whose value lies outside the
    printed optimal band. Every other mismatch (a flag pointing the other way, an in-range result outside its
    range) is still a contradiction."""
    flag = row["flag"]
    if flag in ("H", "L") and single:
        op, bound = single.group(1), float(single.group(2))
        inside = {">": value > bound, ">=": value >= bound, "<": value < bound, "<=": value <= bound}[op]
        return inside and ((flag == "H" and op.startswith(">")) or (flag == "L" and op.startswith("<")))
    return flag is None and expected is not None and row.get("column") == "out_of_range"


def _printed_date(text, date_re, normalize_date):
    match = date_re.match(text)
    if not match:
        raise ScanGateError("date gate: unreadable Collected date")
    normalized = normalize_date(match[0])
    if not isinstance(normalized, tuple):
        raise ScanGateError("date gate: invalid date")
    try:
        calendar_date(*normalized)
    except ValueError as error:
        raise ScanGateError("date gate: invalid date") from error
    return normalized, match[0]


def _footer_sequence(footer):
    match = re.search(r"\bPAGE\s+(\d+)\s+OF\s+(\d+)\b", footer or "", re.I)
    if match and 1 <= int(match[1]) <= int(match[2]):
        return int(match[1]), int(match[2])
    return None


def is_heading_row(row):
    """A transcribed row that is not a result: a name with no value, flag or range, not marked illegible
    (a section heading such as "CBC (INCLUDES DIFF/PLT)" or a panel label)."""
    return (row["result_text"] is None and row["flag"] is None and row["reference_range"] is None
            and not row["illegible"])


def _signature(row):
    return row["result_text"], row["flag"], _canonical_range(row["reference_range"])


def _lab_codes(reads, lab_codes=()):
    return {*lab_codes, *(row["lab_code"] for page in reads for row in page["rows"]
                          if row["lab_code"] and re.fullmatch(r"[A-Z0-9]{2,5}", row["lab_code"]))}


def _row_identities(reads, codes):
    """{(name, section): [rows of read 1], [rows of read 2], ...} for one page's reads."""
    identities = {}
    for reading, page in enumerate(reads):
        for row in page["rows"]:
            name = strip_lab_code(" ".join(row["name"].split()), codes) if row["name"] else row["name"]
            identities.setdefault((name, row["section"]), [[] for _ in reads])[reading].append(row)
    return identities


def _agreed(rows_per_read, needed):
    """The reads (indexes) that print one identical row for this test, when at least `needed` of them do."""
    votes = {}
    for reading, rows in enumerate(rows_per_read):
        if len(rows) == 1:
            votes.setdefault(_signature(rows[0]), []).append(reading)
    best = max(votes.values(), key=len, default=[])
    if sum(len(readings) == len(best) for readings in votes.values()) > 1:
        return None  # two different rows printed equally often: no agreement
    return best if len(best) >= needed else None


def urine_note_pages(reads):
    """Page numbers whose reads agree (two at least) that the page prints a urine-analysis note."""
    return {page_reads[0]["page"] for page_reads in reads
            if sum(read.get("urine_note_printed") is True for read in page_reads) >= SCAN_AGREEMENT_READS}


def _is_urine_test(name):
    import lab_reported
    return bool(name) and lab_reported.lookup(name, "URINALYSIS") is not None


def carry_sections(rows, urine_note_pages=()):
    """Accepted scanned rows (page order, reading order) with the section a page break hides restored: rows printed
    before the first heading of a page belong to the section the previous page ended in (the previous page number
    only, never across a gap). A page that prints the urine-analysis note starts in "URINALYSIS" when nothing else
    is carried. A carried urinalysis section covers only urine tests (lab_reported's urinalysis names) and ends at
    the first row that is not one, so a blood test printed after the urine block is never read as urine; a printed
    heading always ends a carried section. Rows given a section carry "section_carried": where it came from."""
    import lab_reported

    by_page = {}
    for row in rows:
        by_page.setdefault(row["page"], []).append(row)
    ended_in, out = {}, []
    for number in sorted(by_page):
        carrying, source = ended_in.get(number - 1), f"continued from page {number - 1}"
        if number in urine_note_pages and not lab_reported.is_urinalysis(carrying):
            carrying, source = "URINALYSIS", "the page prints the urine-analysis note"
        last = None
        for row in by_page[number]:
            if row.get("section"):
                carrying = None
            elif carrying and lab_reported.is_urinalysis(carrying) and not _is_urine_test(row["name"]):
                carrying = None
            elif carrying:
                row = {**row, "section": carrying, "section_carried": source}
            out.append(row)
            last = row.get("section") or last
        ended_in[number] = last
    return out


def reads_disagree(reads):
    """True when the reads of one page do not print every result row identically (headings ignored)."""
    codes = _lab_codes(reads)
    for rows in _row_identities(reads, codes).values():
        if all(is_heading_row(row) for read_rows in rows for row in read_rows):
            continue
        if _agreed(rows, len(reads)) is None:
            return True
    return False


def gate_staff_identified_reads(reads, collected, result_re, patient_name, failures=(), lab_codes=(),
                                row_exclusions=None):
    """Staff-entered patient name and Collected date identify every scanned page, so printed
    footer/header/name/date are not gated. reads: one list of 2 or 3 reads per page. A row is kept only
    when at least two reads print the same value, flag and reference range (both reads when only two were
    made) and the agreed value passes the identity-independent format and flag checks; the printed summary
    of the agreeing reads excludes only rows it contradicts. A row no read prints a value for is a heading,
    not a result: it is noted and never listed as an excluded result. Every excluded result row is appended
    to row_exclusions ({page, name, reason, reads}) for the staff notes and the confirmation step. A printed
    patient name that differs from the staff entry is a staff-review notice, never a rejection."""
    notes = []
    accepted = []

    def note(message):
        notes.append(message)
        print(message)

    mismatched = sorted({page["page"] for pages in [*reads, *(partial for _, partial, _ in failures)]
                         for page in pages if page["patient_name"]
                         and not names_match(patient_name, page["patient_name"])})
    for number in mismatched:
        note(f"STAFF REVIEW - PATIENT NAME MISMATCH: source=scan page {number} prints a patient name "
             "that differs from the staff-entered name; rows were not rejected for this - confirm the "
             "page belongs to this patient")

    codes = _lab_codes([page for pages in reads for page in pages], lab_codes)

    def name_of(text):
        return strip_lab_code(" ".join(text.split()), codes)

    def summary_key(text, flag):
        # The printed out-of-range list may show "210.0 H" as one string with no separate flag.
        text = " ".join(text.split())
        split = re.fullmatch(r"(.*?\S)\s*([HL])", text)
        if flag is None and split and result_re.fullmatch(split[1]) and not result_re.fullmatch(split[1])["flag"]:
            text, flag = split[1], split[2]
        return text.casefold(), flag

    # Printed out-of-range summaries, per read position (the n-th read of every page).
    summaries = [{} for _ in range(max((len(pages) for pages in reads), default=0))]
    for pages in reads:
        for reading, page in enumerate(pages):
            for entry in page["out_of_range_summary"] or []:
                if not entry["illegible"] and entry["name"] and entry["result_text"]:
                    summaries[reading].setdefault(name_of(entry["name"]).casefold(), set()).add(
                        summary_key(entry["result_text"], entry["flag"]))

    def row_reason(rows, needed):
        agreeing = _agreed(rows, needed)
        if agreeing is None:
            if any(len(read_rows) != 1 for read_rows in rows):
                return "agreement gate: missing or duplicate row in independent reads", None
            return "agreement gate: independent reads disagree on value, flag or range", None
        chosen = [rows[reading][0] for reading in agreeing]
        first = chosen[0]
        if any(row["illegible"] for row in chosen) or not first["name"] or not first["result_text"]:
            return "legibility gate: null or illegible row", first
        numeric = result_re.fullmatch(first["result_text"])
        if numeric is None and not is_text_result(first["result_text"], first["reference_range"]):
            return "grammar gate: unsupported printed result", first
        if numeric and numeric["flag"]:
            return "grammar gate: result_text includes a flag instead of a separate flag field", first
        if not _numeric_flag(first, result_re):
            return "flag gate: result contradicts numeric reference range", first
        key = summary_key(first["result_text"], first["flag"])
        if any(entries and key not in entries
               for entries in (summaries[reading].get(name_of(first["name"]).casefold()) for reading in agreeing)):
            return "summary gate: row disagrees with printed out-of-range summary", first
        return None, first

    pages = []
    for page_reads in reads:
        number = page_reads[0]["page"]
        needed = len(page_reads) if len(page_reads) < 3 else SCAN_AGREEMENT_READS  # two reads: both must agree
        identities = _row_identities(page_reads, codes)
        kept = results = 0
        for (name, _section), rows in identities.items():
            if all(is_heading_row(row) for read_rows in rows for row in read_rows):
                note(f"source=scan page {number} {name!r}: no read prints a result for it (a heading or label, "
                     "not a result); not a result row")
                continue
            results += 1
            reason, row = row_reason(rows, needed)
            if reason is None:
                accepted.append({**row, "name": name_of(row["name"]), "date": collected,
                                 "specimen_id": None, "source": "scan"})
                kept += 1
                continue
            printed = row or next(read_row for read_rows in rows for read_row in read_rows)
            note(f"source=scan page {number} excluded {printed['name']!r}: {reason}")
            if row_exclusions is not None:
                row_exclusions.append({"page": number, "name": name or "(unnamed row)", "reason": reason,
                                       "reads": " / ".join(_read_values(read_rows) for read_rows in rows)})
        if not results:
            reason = "no result rows read; notice only"
            note(f"source=scan page {number}: {reason}")
        else:
            reason = None if kept else "row gates: no rows passed"
            if reason:
                note(f"source=scan page {number}: {reason}; page excluded")
        counts = [len(page["rows"]) for page in page_reads]
        pages.append((number, counts, kept, reason))
    for number, partial_reads, reason in failures:
        note(f"source=scan page {number}: {reason}; page excluded")
        counts = [len(page["rows"]) for page in partial_reads]
        pages.append((number, counts + [None] * (2 - len(counts)), 0, reason))
    for number, counts, kept, reason in sorted(pages, key=lambda item: item[0]):
        verdict = "kept" if kept else "notice" if reason and reason.endswith("notice only") else "excluded"
        print(f"source=scan page={number} rows_read={'/'.join(json.dumps(count) for count in counts)} "
              f"identity=\"staff\" date={json.dumps(collected)} rows_kept={kept} verdict={verdict} "
              f"reason={json.dumps(reason or 'passed')}")
    return accepted, notes


def gate_reads(reads, digital_names, result_re, date_re, normalize_date, failures=(), row_exclusions=None):
    """Isolate page/row failures; only contradictory identity evidence is batch-fatal."""
    expected_names = {name_key(name) for name in digital_names}
    states = []
    dates = {}
    specimens = {}
    notes = []
    fatal = None
    names = set()
    for pair in reads:
        first, second = pair
        state = {"pair": pair, "page": first["page"], "specimen": first["specimen_id"],
                 "date": None, "reason": None, "matched": False}
        states.append(state)
        for page in pair:
            if page["patient_name"]:
                key = name_key(page["patient_name"])
                names.add(key)
                if expected_names and key not in expected_names:
                    fatal = "patient gate: name mismatch"
            if page["collected"] and page["specimen_id"]:
                try:
                    normalized, printed = _printed_date(page["collected"], date_re, normalize_date)
                except ScanGateError:
                    continue
                dates.setdefault(page["specimen_id"], {})[normalized] = printed
        state["matched"] = bool(first["patient_name"] and
                                name_key(first["patient_name"]) in expected_names)
        for field in ("specimen_id", "collected", "patient_name", "footer"):
            if first[field] != second[field]:
                state["reason"] = f"metadata agreement gate: {field} differs"
                break
        if first["collected"]:
            try:
                _, state["date"] = _printed_date(first["collected"], date_re, normalize_date)
            except ScanGateError as error:
                state["reason"] = state["reason"] or str(error)
        if first["illegible"] or second["illegible"]:
            state["reason"] = state["reason"] or "legibility gate: illegible page identity"
    for _, partial_reads, _ in failures:
        for page in partial_reads:
            if page["patient_name"]:
                key = name_key(page["patient_name"])
                names.add(key)
                if expected_names and key not in expected_names:
                    fatal = "patient gate: name mismatch"
            if page["collected"] and page["specimen_id"]:
                try:
                    normalized, printed = _printed_date(page["collected"], date_re, normalize_date)
                except ScanGateError:
                    continue
                dates.setdefault(page["specimen_id"], {})[normalized] = printed
    if len(names) > 1 or len(expected_names) > 1:
        fatal = fatal or "patient gate: conflicting patient names"
    if any(len(values) > 1 for values in dates.values()):
        fatal = fatal or "date gate: conflicting Collected dates for same specimen"

    for state in states:
        first, second = state["pair"]
        has_rows = bool(first["rows"] or second["rows"])
        if not state["specimen"] and not has_rows:
            state["reason"] = "specimen gate: rowless page without specimen identity"
        if not state["specimen"] and has_rows and not state["reason"]:
            sequence = _footer_sequence(first["footer"])
            neighbours = []
            for other in states:
                neighbour = other["pair"][0]
                neighbour_sequence = _footer_sequence(neighbour["footer"])
                if sequence and neighbour_sequence and not other["reason"] and \
                        abs(other["page"] - state["page"]) == 1 and \
                        neighbour_sequence == (sequence[0] + other["page"] - state["page"], sequence[1]) and \
                        neighbour["specimen_id"] and re.search(
                            rf"(?<![\w-]){re.escape(neighbour['specimen_id'])}(?![\w-])",
                            neighbour["footer"]):
                    neighbours.append(neighbour["specimen_id"])
            if neighbours and len(set(neighbours)) == 1:
                state["specimen"] = neighbours[0]
        if not state["specimen"]:
            reason = ("specimen gate: missing identity; no verified adjacent PAGE n OF m footer"
                      if has_rows else "specimen gate: rowless page without specimen identity")
            state["reason"] = state["reason"] or reason
        if not expected_names:
            state["reason"] = state["reason"] or "patient gate: digital patient name missing"
        if not state["reason"]:
            specimens.setdefault(state["specimen"], []).append(state)
            if state["date"]:
                normalized = normalize_date(state["date"])
                dates.setdefault(state["specimen"], {})[normalized] = state["date"]
    if any(len(values) > 1 for values in dates.values()):
        fatal = fatal or "date gate: conflicting Collected dates for same specimen"

    def exclude_row(row, page, reason):
        message = f"source=scan page {page} excluded {row['name']!r}: {reason}"
        notes.append(message)
        print(message)

    accepted = []
    for specimen, group in specimens.items():
        group_names = {name_key(page["patient_name"]) for state in group
                       for page in state["pair"] if page["patient_name"]}
        group_dates = {normalize_date(state["date"]): state["date"] for state in group if state["date"]}
        reason = fatal
        if not group_names:
            reason = reason or "patient gate: specimen patient name missing"
        if not group_dates:
            reason = reason or "date gate: missing Collected date for specimen"
        if reason:
            for state in group:
                state["reason"] = reason
            continue
        date = next(iter(group_dates.values()))
        summaries = []
        for reading in (0, 1):
            summaries.append({_row_key(row) for state in group
                              for row in (state["pair"][reading]["out_of_range_summary"] or [])
                              if not row["illegible"] and row["name"] and row["result_text"]})
        for state in group:
            first, second = state["pair"]
            state["date"] = date
            by_name = {}
            for row in second["rows"]:
                by_name.setdefault((row["name"], row["section"]), []).append(row)
            seen = set()
            for row in first["rows"]:
                identity = (row["name"], row["section"])
                candidates = by_name.get(identity, [])
                reason = None
                if len(candidates) != 1 or identity in seen:
                    reason = "agreement gate: missing or duplicate row in independent reads"
                else:
                    other = candidates[0]
                    if any(row[key] != other[key] for key in
                           ("name", "result_text", "flag", "column", "reference_range", "section")):
                        reason = "agreement gate: independent reads disagree"
                if reason and row_exclusions is not None:
                    row_exclusions.append({"page": first["page"], "name": row["name"] or "(unnamed row)",
                                           "reads": f"{_read_values([row])} / {_read_values(candidates)}"})
                if not reason:
                    other = candidates[0]
                    if row["illegible"] or other["illegible"] or not row["name"] or not row["result_text"]:
                        reason = "legibility gate: null or illegible row"
                    elif row["column"] is None:
                        reason = "column gate: no printed result column"
                    elif not row["section"]:
                        reason = "section gate: no printed section identity"
                    elif result_re.fullmatch(row["result_text"]) is None and \
                            not is_text_result(row["result_text"], row["reference_range"]):
                        reason = "grammar gate: unsupported printed result"
                    elif (numeric := result_re.fullmatch(row["result_text"])) and numeric["flag"]:
                        reason = "grammar gate: result_text includes a flag instead of a separate flag field"
                    elif not _numeric_flag(row, result_re):
                        reason = "flag gate: result contradicts numeric reference range"
                    elif row["column"] == "out_of_range" and any(
                            _row_key(row) not in summary for summary in summaries):
                        reason = "summary gate: out-of-range row disagrees with printed summary"
                    elif row["column"] == "in_range" and any(
                            _row_key(row) in summary for summary in summaries):
                        reason = "summary gate: in-range row appears in out-of-range summary"
                seen.add(identity)
                if reason:
                    exclude_row(row, first["page"], reason)
                else:
                    accepted.append({**row, "date": date, "specimen_id": specimen, "source": "scan"})
            for row in second["rows"]:
                if (row["name"], row["section"]) not in seen:
                    exclude_row(row, second["page"], "agreement gate: row present only in second read")
                    if row_exclusions is not None:
                        row_exclusions.append({"page": second["page"], "name": row["name"] or "(unnamed row)",
                                               "reads": f"not read / {_read_values([row])}"})
            if (first["rows"] or second["rows"]) and not any(
                    row["page"] == state["page"] for row in accepted):
                state["reason"] = "row gates: no rows passed"
    for number, partial_reads, reason in failures:
        first = partial_reads[0] if partial_reads else None
        printed = None
        if first and first["collected"]:
            try:
                _, printed = _printed_date(first["collected"], date_re, normalize_date)
            except ScanGateError:
                pass  # The page's transcription failure is already reported.
        states.append({"page": number, "pair": partial_reads,
                       "specimen": first["specimen_id"] if first else None,
                       "date": printed, "matched": bool(first and first["patient_name"] and
                       name_key(first["patient_name"]) in expected_names), "reason": reason})
    for state in sorted(states, key=lambda item: item["page"]):
        reason = fatal or state["reason"]
        if reason:
            notes.append(f"source=scan page {state['page']}: {reason}; page excluded")
            if state["reason"] != "row gates: no rows passed":
                seen = set()
                for reading, page in enumerate(state["pair"]):
                    for row in page["rows"]:
                        identity = (row["name"], row["section"])
                        if reading == 0 or identity not in seen:
                            exclude_row(row, state["page"], reason)
                    seen.update((row["name"], row["section"]) for row in page["rows"])
        counts = [len(page["rows"]) for page in state["pair"]]
        counts += [None] * (2 - len(counts))
        print(f"source=scan page={state['page']} rows_read={json.dumps(counts[0])}/{json.dumps(counts[1])} "
              f"specimen_id={json.dumps(state['specimen'])} name_matched={'yes' if state['matched'] else 'no'} "
              f"date={json.dumps(state['date'])} verdict={'excluded' if reason else 'kept'} "
              f"reason={json.dumps(reason or 'passed')}")
    if fatal:
        error = ScanGateError(fatal)
        error.notes = notes
        raise error
    return accepted, notes
