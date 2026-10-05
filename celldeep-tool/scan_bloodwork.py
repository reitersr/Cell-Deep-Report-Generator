"""Literal scan transcription and deterministic gates; no scoring or inferred results."""

import base64
import json
import re
from datetime import date as calendar_date


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
    "reference_range": _TEXT, "page": {"type": "integer"}, "section": _TEXT,
})
SCAN_SCHEMA = _object({
    "page": {"type": "integer"}, "specimen_id": _TEXT, "collected": _TEXT,
    "footer": _TEXT,
    "patient_name": _TEXT, "illegible": {"type": "boolean"},
    "rows": {"type": "array", "items": _ROW},
    "out_of_range_summary": _nullable({"type": "array", "items": _SUMMARY_ROW}),
})
SCAN_PROMPT = """Transcribe this lab page literally. Never infer, calculate, correct, or complete text.
Return only the supplied JSON schema. Copy names, result_text, reference_range, section headings,
patient_name, footer SPECIMEN id, full footer text (including PAGE n OF m), and Collected date
exactly as printed. Use the PDF page number
provided, not the report's own printed page number. Classify each result by its printed In Range
or Out of Range column. Keep H/L separate from result_text. Do not transcribe reference values,
footnotes, interpretations, or summary entries as result rows. Preserve URINALYSIS section identity.
Copy every entry of LIST OF RESULTS PRINTED IN THE OUT OF RANGE COLUMN verbatim into
out_of_range_summary; null means no such block, [] means a printed empty block.
Absent metadata is null. Illegible fields are null with illegible=true, never guessed.
Do not infer specimen ids, dates, patient names, section headings, or flags from other pages."""

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


def read_page(page, client, create_message, model):
    image = base64.b64encode(page.get_pixmap(dpi=200, alpha=False).tobytes("png")).decode("ascii")
    reads = []
    for reading in (1, 2):
        try:
            reads.append(_read_transcription(page, image, reading, client, create_message, model))
        except ScanGateError as error:
            error.reads = reads
            raise
    return reads


def _read_transcription(page, image, reading, client, create_message, model):
    response = create_message(
        client, f"bloodwork scan page {page.number + 1} read {reading}",
        model=model, max_tokens=16000, system=SCAN_PROMPT,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
            {"type": "text", "text": f"Transcribe PDF page {page.number + 1} independently."},
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
    _validate(data, SCAN_SCHEMA)
    if data["page"] != page.number + 1 or any(row["page"] != data["page"] for row in data["rows"]):
        raise ScanGateError(f"page gate: wrong page number on page {page.number + 1}")
    return data


def name_key(name):
    return tuple(sorted(re.findall(r"[a-z]+", name.casefold())))


def digital_patient_names(pages, group_lines, page_words):
    names = set()
    for page in pages:
        for line in group_lines(page_words(page), 3):
            text = " ".join(word[4] for word in line)
            explicit = re.search(r"\bPatient(?: Name)?:\s*([A-Za-z ,'-]+)", text, re.I)
            if explicit:
                names.add(explicit[1].strip())
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


def _numeric_flag(row, result_re):
    result = result_re.fullmatch(row["result_text"])
    if result is None:
        return True
    if result["flag"] and result["flag"] != row["flag"]:
        return False
    reference = row["reference_range"]
    if not reference:
        return True
    bounded = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*", reference)
    single = re.fullmatch(r"\s*(<=|>=|<|>|≤|≥)\s*(-?\d+(?:\.\d+)?)\s*", reference)
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
    return row["flag"] == expected if determinate else row["flag"] is None


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


def gate_staff_identified_reads(reads, collected, result_re, patient_name, failures=()):
    """Staff-entered patient name and Collected date identify every scanned page, so printed
    footer/header/name/date are not gated. A row is kept only when both reads agree on its
    value, flag and reference range and the printed value passes the identity-independent
    format and flag checks; the printed summary excludes only rows it contradicts. A printed
    patient name that differs from the staff entry is a staff-review notice, never a rejection."""
    notes = []
    accepted = []

    def note(message):
        notes.append(message)
        print(message)

    mismatched = sorted({page["page"] for pair in [*reads, *(partial for _, partial, _ in failures)]
                         for page in pair if page["patient_name"]
                         and name_key(page["patient_name"]) != name_key(patient_name)})
    for number in mismatched:
        note(f"STAFF REVIEW - PATIENT NAME MISMATCH: source=scan page {number} prints a patient name "
             "that differs from the staff-entered name; rows were not rejected for this - confirm the "
             "page belongs to this patient")

    summaries = ({}, {})
    for pair in reads:
        for reading, page in enumerate(pair):
            for entry in page["out_of_range_summary"] or []:
                if not entry["illegible"] and entry["name"] and entry["result_text"]:
                    summaries[reading].setdefault(entry["name"], set()).add(_row_key(entry)[1:])

    def row_reason(rows):
        first, second = rows
        if len(first) != 1 or len(second) != 1:
            return "agreement gate: missing or duplicate row in independent reads"
        first, second = first[0], second[0]
        if any(first[key] != second[key] for key in ("result_text", "flag", "reference_range")):
            return "agreement gate: independent reads disagree on value, flag or range"
        if first["illegible"] or second["illegible"] or not first["name"] or not first["result_text"]:
            return "legibility gate: null or illegible row"
        numeric = result_re.fullmatch(first["result_text"])
        if numeric is None and first["result_text"].casefold() not in _STATUSES and \
                re.fullmatch(r"[1-4]\+", first["result_text"]) is None:
            return "grammar gate: unsupported printed result"
        if numeric and numeric["flag"]:
            return "grammar gate: result_text includes a flag instead of a separate flag field"
        if not _numeric_flag(first, result_re):
            return "flag gate: result contradicts numeric reference range"
        if any(entries and _row_key(first)[1:] not in entries
               for entries in (summary.get(first["name"]) for summary in summaries)):
            return "summary gate: row disagrees with printed out-of-range summary"
        return None

    pages = []
    for pair in reads:
        number = pair[0]["page"]
        identities = {}
        for reading, page in enumerate(pair):
            for row in page["rows"]:
                identities.setdefault((row["name"], row["section"]), ([], []))[reading].append(row)
        kept = 0
        for rows in identities.values():
            reason = row_reason(rows)
            for row in rows[0] if reason is None else rows[0] or rows[1]:
                if reason:
                    note(f"source=scan page {number} excluded {row['name']!r}: {reason}")
                else:
                    accepted.append({**row, "date": collected, "specimen_id": None, "source": "scan"})
                    kept += 1
        if not identities:
            reason = "no result rows read; notice only"
            note(f"source=scan page {number}: {reason}")
        else:
            reason = None if kept else "row gates: no rows passed"
            if reason:
                note(f"source=scan page {number}: {reason}; page excluded")
        pages.append((number, [len(page["rows"]) for page in pair], kept, reason))
    for number, partial_reads, reason in failures:
        note(f"source=scan page {number}: {reason}; page excluded")
        counts = [len(page["rows"]) for page in partial_reads]
        pages.append((number, counts + [None] * (2 - len(counts)), 0, reason))
    for number, counts, kept, reason in sorted(pages, key=lambda item: item[0]):
        verdict = "kept" if kept else "notice" if reason and reason.endswith("notice only") else "excluded"
        print(f"source=scan page={number} rows_read={json.dumps(counts[0])}/{json.dumps(counts[1])} "
              f"identity=\"staff\" date={json.dumps(collected)} rows_kept={kept} verdict={verdict} "
              f"reason={json.dumps(reason or 'passed')}")
    return accepted, notes


def gate_reads(reads, digital_names, result_re, date_re, normalize_date, failures=()):
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
                    elif row["illegible"] or other["illegible"] or not row["name"] or not row["result_text"]:
                        reason = "legibility gate: null or illegible row"
                    elif row["column"] is None:
                        reason = "column gate: no printed result column"
                    elif not row["section"]:
                        reason = "section gate: no printed section identity"
                    elif result_re.fullmatch(row["result_text"]) is None and \
                            row["result_text"].casefold() not in _STATUSES and \
                            re.fullmatch(r"[1-4]\+", row["result_text"]) is None:
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
