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


_TEXT = {"type": ["string", "null"]}
_FLAG = {"type": ["string", "null"], "enum": ["H", "L", None]}
_SUMMARY_ROW = _object({
    "name": _TEXT, "result_text": _TEXT, "flag": _FLAG, "illegible": {"type": "boolean"},
})
_ROW = _object({
    **_SUMMARY_ROW["properties"],
    "column": {"type": ["string", "null"], "enum": ["in_range", "out_of_range", None]},
    "reference_range": _TEXT, "page": {"type": "integer"}, "section": _TEXT,
})
SCAN_SCHEMA = _object({
    "page": {"type": "integer"}, "specimen_id": _TEXT, "collected": _TEXT,
    "patient_name": _TEXT, "illegible": {"type": "boolean"},
    "rows": {"type": "array", "items": _ROW},
    "out_of_range_summary": {"type": ["array", "null"], "items": _SUMMARY_ROW},
})
SCAN_PROMPT = """Transcribe this lab page literally. Never infer, calculate, correct, or complete text.
Return only the supplied JSON schema. Copy names, result_text, reference_range, section headings,
patient_name, footer SPECIMEN id, and Collected date exactly as printed. Use the PDF page number
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
    types = schema["type"]
    types = types if isinstance(types, list) else [types]
    actual = ("null" if value is None else "boolean" if isinstance(value, bool) else
              "integer" if isinstance(value, int) else "string" if isinstance(value, str) else
              "array" if isinstance(value, list) else "object" if isinstance(value, dict) else "invalid")
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
        reads.append(data)
    return reads


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


def gate_reads(reads, digital_names, result_re, date_re, normalize_date):
    """Return consensus rows and notices. Fatal failures reject the complete scan batch."""
    expected_names = {name_key(name) for name in digital_names}
    if len(expected_names) != 1:
        raise ScanGateError("patient gate: digital patient name missing or conflicting")
    dates = {}
    specimens = {}
    for pair in reads:
        first, second = pair
        for field in ("specimen_id", "collected", "patient_name"):
            if first[field] != second[field]:
                raise ScanGateError(f"metadata agreement gate: {field} differs on page {first['page']}")
        if first["illegible"] or not first["specimen_id"]:
            raise ScanGateError(f"specimen gate: missing or illegible identity on page {first['page']}")
        specimen = first["specimen_id"]
        specimens.setdefault(specimen, []).append(pair)
        if first["patient_name"] and name_key(first["patient_name"]) not in expected_names:
            raise ScanGateError(f"patient gate: name mismatch on page {first['page']}")
        if first["collected"]:
            match = date_re.match(first["collected"])
            if not match:
                raise ScanGateError(f"date gate: unreadable Collected date on page {first['page']}")
            date = match[0]
            # The report grammar supports more formats; parse via the existing normalizer at merge.
            normalized = normalize_date(date)
            if not isinstance(normalized, tuple):
                raise ScanGateError(f"date gate: invalid date on page {first['page']}")
            try:
                calendar_date(*normalized)
            except ValueError as error:
                raise ScanGateError(f"date gate: invalid date on page {first['page']}") from error
            dates.setdefault(specimen, {})[normalized] = date
    accepted, notes = [], []
    for specimen, pairs in specimens.items():
        if len(dates.get(specimen, set())) != 1:
            raise ScanGateError(f"date gate: missing or conflicting Collected dates for pages "
                                f"{[pair[0]['page'] for pair in pairs]}")
        date = next(iter(dates[specimen].values()))
        for reading in (0, 1):
            rows = [row for pair in pairs for row in pair[reading]["rows"]
                    if row["column"] == "out_of_range"]
            blocks = [pair[reading]["out_of_range_summary"] for pair in pairs
                      if pair[reading]["out_of_range_summary"] is not None]
            summary = [row for block in blocks for row in block]
            if not blocks or any(row["illegible"] or not row["name"] or not row["result_text"]
                                 for row in rows + summary) or \
                    {_row_key(row) for row in rows} != {_row_key(row) for row in summary}:
                raise ScanGateError(f"summary gate: out-of-range summary mismatch for pages "
                                    f"{[pair[0]['page'] for pair in pairs]}")
        for first, second in pairs:
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
                seen.add(identity)
                if reason:
                    notes.append(f"source=scan page {first['page']} excluded {row['name']!r}: {reason}")
                else:
                    accepted.append({**row, "date": date, "specimen_id": specimen, "source": "scan"})
            for row in second["rows"]:
                if (row["name"], row["section"]) not in seen:
                    notes.append(f"source=scan page {second['page']} excluded {row['name']!r}: "
                                 "agreement gate: row present only in second read")
    return accepted, notes
