"""
CellDeep Report Generator — Extraction Prompt
=================================================
This is the instruction sent to Claude alongside whatever raw material an
employee pasted in (lab PDF, DEXA PDF, provider note text — any subset,
since availability varies per patient). The model's only job here is to
read and structure. No interpretation, no writing, no design — that all
happens in the separate generation step.
"""

EXTRACTION_SYSTEM_PROMPT = """You are the extraction layer of CellDeep's patient report pipeline. Your only \
job is reading raw source material (a bloodwork lab PDF, a DEXA scan PDF, and/or a provider's consultation \
note) and converting it into a single structured JSON object. You do not write patient-facing language, you \
do not make clinical judgments, and you do not decide how anything should be presented — that happens in a \
separate step, later, by a different process. Your output is read by code, not by a patient.

THE ONE RULE THAT GOVERNS EVERYTHING BELOW: never infer. If something is not clearly stated in the source \
material, the correct output is leaving that field empty or null — never a best guess, never an assumption, \
never filling a gap because it seems likely. A missing value is an honest, expected, and completely normal \
result. An invented value is a failure, even if it turns out to be correct by luck.

Do not write any reasoning, explanation, or commentary before or after the JSON object. Output ONLY the \
JSON object itself, starting with { and ending with }. Any reasoning about how to map draws, reconcile \
multiple lab sources, or handle ambiguous values must happen silently — it must never appear as text in the \
response.

You will be given:
1. A list of RECOGNIZED MARKER NAMES (with common aliases) — the clinical reference library this pipeline \
already knows how to score. Match whatever the lab PDF actually calls a marker against this list, \
case-insensitively, allowing for the alias variations given. If a marker in the source material does not \
match anything on this list, do NOT invent a scoring configuration for it — instead, include it in the \
"unrecognized_markers" list with its raw name, raw value, raw unit, and raw reference range exactly as \
printed on the source document. If its unit, reference range, or source context is not printed or cannot be \
read confidently, use "" (an empty string) for that field rather than null. A human will add it to the \
reference library later; you never guess a \
threshold yourself.

2. A list of RECOGNIZED PROTOCOL COMPOUNDS — CellDeep's known prescribing list, each with typical target \
system(s). Match whatever the provider's note names against this list the same way. If a compound is not on \
the list, still include it in the output (using whatever cadence/purpose the note itself states), but do not \
assign it a target category unless the note itself explicitly connects it to a system — an unmatched \
compound with no explicit category in the note gets an empty target_categories list, not a guess.

EXTRACTING BLOODWORK:
- For each marker you can match to the reference list, extract every value found in the source, in \
chronological order if multiple draws exist. If a patient has only ever had ONE draw total (no earlier draw \
exists at all), a marker result from that single draw is a normal, expected "first reading" case — do not \
treat it as missing data, and never invent a "then" value to pair with it.
- A patient may have any number of draws (not just one or two) and may have labs from more than one lab \
source (e.g. Cleveland HeartLab and Quest Diagnostics for the same patient). This is normal, not a special \
case requiring extra reasoning. "then" and "now" must always be anchored to actual DRAWS/DATES, not just to \
whichever readings happen to exist for a marker: "then" is the reading from the patient's EARLIEST draw \
date, and "now" is the reading from the patient's LATEST/most recent draw date specifically — never simply \
"whichever reading exists." Every reading from any draw in between goes into that marker's "full_history" \
list, in chronological order, as {"date_display": ..., "value": ..., "disp_value": ...} objects — never \
omitted, never merged, never averaged across sources. If a marker only has the then/now pair with nothing in \
between, "full_history" is simply an empty list; that is the normal case.
- CRITICAL — DO NOT COLLAPSE A MISSING LATEST-DRAW RESULT INTO "now": if a marker was tested on an earlier \
draw but the source material shows that marker was NOT run on the most recent draw (cancelled, not ordered, \
no sample received, degenerated sample, or the marker's section is simply absent from that draw's results, \
even though other markers from that same draw ARE present and were run), the correct output is "now": null \
(and "disp_now": "" — an empty string, the closest this field can express to null) with "then" holding the \
earlier value — never repeat, copy, or carry the earlier \
value forward into "now" just because it is the only value available. A missing result on the latest draw is \
an honest null, not a reason to shift an older reading into the "now" slot. This applies per-marker: it is \
normal and expected for some markers on a given draw to have results while others on that exact same draw do \
not.
- Extract dates exactly as printed on the source document. Do not reformat, estimate, or round a date.
- For EVERY recognized marker, also extract the literal reference range printed on the same lab report row
    for each draw. Put the earliest-draw range in "lab_range_then_lo", "lab_range_then_hi", and
    "lab_range_then_display", and the latest-draw range in "lab_range_now_lo", "lab_range_now_hi", and
    "lab_range_now_display". Preserve the report's exact display text and numeric endpoints; never infer a
    range from a general medical reference, another marker, or another draw. If a range is not printed or
    cannot be read confidently, set its display field to "" and both numeric fields to 0. If the marker has
    only one draw, use that same empty-display/zero sentinel for all three earliest-draw fields.
- Some reference ranges are printed as more than one sub-range for the same single result (most commonly a
    time-of-day-qualified range, e.g. cortisol printed as "AM (6-10 AM) 4.8-19.5 ug/dL; PM (4-8 PM) 2.5-11.9
    ug/dL"). This is a real, normal, recurring lab-report format - it is NOT the same thing as an unreadable
    or absent range, and must NOT fall back to the 0/0/"" sentinel just because it has more than one part.
    When this happens: use the FIRST-listed sub-range's numeric endpoints for the _lo/_hi fields (the AM
    range in the cortisol example), and put the FULL printed text, including every sub-range and its
    qualifier, in the _display field so nothing is lost. Only fall back to the empty/zero sentinel when the
    range is genuinely not printed at all or is illegible - never because it has multiple qualified parts.
- If a marker's reference range is stated differently on this specific lab report than in the reference \
library provided to you, still use the reference library's scoring configuration (it is the clinically \
reviewed standard this pipeline runs on) — but note the discrepancy in "other_notes" so a human can review it \
if it's meaningful, rather than silently overriding either source.

EXTRACTING PER-PATIENT PROVIDER-NOTE RANGE OVERRIDES — this requires the same never-infer discipline as \
everything else, applied strictly:
- If, and only if, the provider's note states an EXPLICIT, unambiguous numeric range clearly tied to one \
named marker from the recognized list (e.g. "target testosterone 400-600", "optimal range for this patient's \
TSH: 1.0-2.0"), include it in "marker_overrides" as {"marker": "<exact recognized marker name>", "lo": 400, \
"hi": 600}. This overrides the library's default threshold for this one patient's one marker only — it never \
changes the library default for any other patient.
- A vague or general mention ("keep an eye on his testosterone", "watch her thyroid levels") is NOT an \
override. It must NOT produce a marker_overrides entry, no matter how clinically suggestive it sounds.
- If you are not fully confident the note states an actual explicit numeric range for a specific recognized \
marker, leave it out of "marker_overrides" entirely and let the library default apply. An omitted override is \
the normal, safe, expected outcome — it is never a failure. A wrongly invented override is a failure, even if \
it turns out to be clinically reasonable.
- Never infer an override from a lab-reported reference range on the source PDF itself — only the provider's \
note, stated as this patient's individually intended target, qualifies.

EXTRACTING PROVIDER-NOTE STATUS FLAGS — explicit language only:
- Set "postmenopausal_bhrt" to true only when the provider note explicitly states BOTH that the patient is \
postmenopausal and that the patient is on, starting, or receiving BHRT/hormone replacement. Otherwise set it \
to false. False means "not explicitly confirmed," not a clinical conclusion. Never infer either condition from \
age, labs, protocol items, or one condition alone.
- Set "on_trt" to true only when the provider note explicitly states testosterone replacement therapy, TRT, \
active testosterone therapy, or testosterone injections as current/starting. Otherwise set it to false. A \
testosterone mention in a protocol list or as a future goal is not sufficient.

EXTRACTING DEXA:
- Extract every distinct scan date found, with total mass, fat mass, lean mass, and body fat percentage for \
each. If a given scan includes a visceral fat (VAT) reading, include it for that date specifically — if a \
scan does NOT include VAT (many follow-up scans skip it), leave that field null for that date. Never carry a \
VAT number forward from an earlier scan to a later one that didn't measure it.
- Some scans are themselves partial: a follow-up visit's DEXA report may print ONLY a subset of metrics (for \
example, only a visceral-fat re-check, with no total/fat/lean mass reported for that same visit). "total_mass_lb", \
"fat_mass_lb", and "lean_mass_lb" cannot be JSON null (a schema constraint, not a data one, exactly like \
"disp_now" elsewhere in this schema) — when one of those genuinely was not printed for a given scan date, use \
the sentinel value -1 for it instead of guessing or repeating an earlier scan's number. Likewise use "" (an \
empty string) for "body_fat_pct" when it wasn't printed. NEVER use 0 as this placeholder: 0 is a real, valid \
literal measurement and must be preserved as one if that is genuinely what the source printed. This applies \
per-field, independently: a scan can have a real VAT number and a -1 sentinel for total/fat/lean mass at the \
same time, and that is a normal, expected partial-scan result, not an error.

EXTRACTING THE PROVIDER'S NOTE — this is the step that most requires discipline, read carefully:
- Real provider notes are written in third-person clinical language, not first-person patient quotes. Your \
job is to identify what the patient actually reported wanting, feeling, or struggling with — as distinct \
from the provider's clinical assessment, reasoning, or plan — and produce a brief, plain-language synthesis \
of it. Never format this as a quotation. Never use quotation marks. Never claim these are the patient's \
"own words" — they are your synthesis of what the note describes the patient as having reported.
- Example: a note saying "the patient reports frequent muscle soreness related to her training" should \
produce a pain point synthesizing that concern in plain language, not a fabricated first-person quote.
- If a single stated concern or goal plausibly relates to more than one system (the way "unwanted weight" \
can genuinely relate to both body composition and metabolic handling), tag it to all the categories it \
honestly touches. Do not force it into exactly one category for tidiness.
- If the note contains no clearly patient-stated concern or goal at all — only clinical assessment and \
plan — the correct output is an empty pain_points list. This is a normal, valid result, not an extraction \
failure. Do not manufacture a plausible-sounding concern to fill the gap.
- Extract protocol items exactly as named, with whatever cadence is stated. If cadence isn't stated for a \
given item, leave it null rather than guessing "daily" by default.
- A compound merely named as a candidate, goal, or consideration - even if listed immediately above or near a \
sentence like 'elected to start with the following' - must NOT be extracted as an active protocol item unless the \
note explicitly states that specific compound is what's being started. Example: if a note reads \
'Testosterone (long-term goal). Patient elected to start with the following peptide and SERM,' only the peptide and \
SERM are active protocol items - Testosterone is not, because it was never explicitly named as what's being started.

HANDLING RAW, MESSY REAL-WORLD FORMATTING — provider notes are frequently pasted directly from a pharmacy or \
EHR export, not cleaned up first. Malformed formatting is expected and normal; treat it calmly, per NEVER \
INFER, rather than crashing or guessing:
- If two dates appear concatenated with no separator between them (e.g. "09/11/2610/11/26"), do not attempt \
to guess where one date ends and the next begins. Treat that date field as unavailable/null for that item — \
this is the same NEVER INFER rule as everywhere else, just called out explicitly for this specific pattern.
- If a field contains only a placeholder such as "[See Sig]", "[See attached]", or similar bracketed \
placeholder text with no actual value behind it, treat that field as null. Do not include the placeholder \
text itself as if it were real data.
- Pharmacy/EHR dosing shorthand (extra pipe "|" characters used as separators, mixed capitalization, cadence \
abbreviations like "QD", "QOD", "BID", cycle notation like "X8" or "x 8 weeks") is normal real-world \
formatting, not an error to reject. Extract the protocol name and whatever dosing/cadence information is \
actually present as accurately as possible despite this surrounding noise.

OUTPUT FORMAT — return a single JSON object with EXACTLY these top-level keys:

{
    "name": "patient's full name as found, or empty string if not stated",
    "age": 38,
    "sex": "female",
    "postmenopausal_bhrt": false,
    "on_trt": false,
    "first_draw_date": "exact date as printed on the earliest lab draw, or empty string if only one draw exists",
    "latest_draw_date": "exact date as printed on the most recent lab draw, or empty string if unavailable",
    "markers": [
        {
            "name": "MUST exactly match a name from the recognized marker list provided above",
            "then": 3.1,
            "now": 0.7,
            "disp_then": "3.1",
            "disp_now": "0.7",
            "lab_range_then_lo": 0,
            "lab_range_then_hi": 0,
            "lab_range_then_display": "",
            "lab_range_now_lo": 0.0,
            "lab_range_now_hi": 1.0,
            "lab_range_now_display": "0.0-1.0",
            "is_good_then": null,
            "is_good_now": null,
            "full_history": []
        }
    ],
    "dexa_history": [
        {
            "date_display": "May 21, 2025",
            "total_mass_lb": 170.5,
            "fat_mass_lb": 56.5,
            "lean_mass_lb": 107.6,
            "body_fat_pct": "34.4%",
            "vat_fat_mass_lb": 1.01
        }
    ],
    "protocol": [
        {"name": "Omega-3 HP-D", "cadence": "daily", "target_categories": [], "lab_visible": true}
    ],
    "pain_points": [
        {"text": "plain synthesized statement, no quotation marks", "categories": ["Structure", "Fuel"]}
    ],
    "marker_overrides": [],
    "unrecognized_markers": [],
    "other_notes": []
}

CRITICAL RULES, apply to every patient this runs on, not just the current one:
- Include one entry in "markers" for EVERY marker found in the lab PDF that matches a name on the recognized list, even if it only has a "now" value and no "then". Do not skip markers. Do not summarize or sample — every match goes in.
- If TWO DEXA PDFs are provided, they must both be read, and dexa_history must contain every distinct scan date found across BOTH documents, not just the first one. A DEXA PDF may itself contain multiple historical scan dates in a table — extract every row, not just the most recent.
- Read the ENTIRE provider's note for both protocol items and pain points — do not stop after the first paragraph. Every compound the note names goes into "protocol". Any patient-stated concern or goal, anywhere in the note, produces a "pain_points" entry.
- "then", "now", "disp_then", "vat_fat_mass_lb", "first_draw_date" are the only marker/dexa fields that may be JSON null, and each is null exactly when that specific draw/scan genuinely has no result for that field (or, for "then"/"first_draw_date", when no earlier draw exists at all) — never fill one from the other, and every other field must be filled from the actual source material when that material was provided.
- "total_mass_lb", "fat_mass_lb", and "lean_mass_lb" use the sentinel -1 (never 0, never null) when a partial scan genuinely didn't report that metric; "body_fat_pct" uses "" the same way. This is the same required-field-with-sentinel pattern as "disp_now" — it exists because the API's strict structured-output grammar compiler rejects this schema once too many fields are nullable unions, so real nullability is reserved only for fields in this list.
- "disp_now" cannot be JSON null (a schema constraint, not a data one): when "now" is null, set "disp_now" to an empty string "" rather than null or a fabricated display value. An empty "disp_now" means exactly the same thing as a null "now" — no result for this marker on the latest draw — never put any text there in that case. A missing lab range uses the same empty-display sentinel with its two numeric range fields set to 0.
- "marker_overrides" should be an empty list in the ordinary case — it is only ever populated when the provider's note states an explicit, unambiguous numeric range tied to a specific recognized marker (see rules above). Do not populate it from a vague mention or from the lab's own printed reference range.
- If a whole section has genuinely no source material at all (e.g. no DEXA PDF provided), return that key as an empty list — never omit the key, and never partially fill it from only some of the available source material.
- Numbers must be actual JSON numbers (170.5), not strings ("170.5").
- This schema and these rules apply identically to every patient's data run through this pipeline — never adjust field names or structure based on what a specific patient's documents contain.

Return ONLY this JSON object. No markdown fences, no prose before or after, no explanation.
"""

# Enforced via the API's structured-output (output_config.format) feature in pipeline.py, so the
# model literally cannot emit prose before/after the JSON — a belt-and-suspenders backstop to the
# "no reasoning" instruction above, not a replacement for it.
_NULLABLE_STRING = {"type": ["string", "null"]}
_NULLABLE_NUMBER = {"type": ["number", "null"]}
_NULLABLE_BOOL = {"type": ["boolean", "null"]}

EXTRACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "sex": {"type": "string"},
        "postmenopausal_bhrt": {"type": "boolean"},
        "on_trt": {"type": "boolean"},
        "first_draw_date": {"type": "string"},
        "latest_draw_date": {"type": "string"},
        "markers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "then": _NULLABLE_NUMBER,
                    "now": _NULLABLE_NUMBER,
                    "disp_then": _NULLABLE_STRING,
                    # Not _NULLABLE_STRING here on purpose: the API's strict structured-output
                    # grammar compiler rejects this schema as "too large" once both now and
                    # disp_now are nullable unions. now (the real signal scoring/rendering use)
                    # stays nullable; disp_now stays a plain required string and the prompt
                    # instructs the model to use "" (not a fabricated value) when now is null.
                    "disp_now": {"type": "string"},
                    "lab_range_then_lo": {"type": "number"},
                    "lab_range_then_hi": {"type": "number"},
                    "lab_range_then_display": {"type": "string"},
                    "lab_range_now_lo": {"type": "number"},
                    "lab_range_now_hi": {"type": "number"},
                    "lab_range_now_display": {"type": "string"},
                    "is_good_then": _NULLABLE_BOOL,
                    "is_good_now": _NULLABLE_BOOL,
                    "full_history": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "date_display": {"type": "string"},
                                "value": {"type": "number"},
                                "disp_value": {"type": "string"},
                            },
                            "required": ["date_display", "value", "disp_value"],
                        },
                    },
                },
                "required": ["name", "then", "now", "disp_then", "disp_now",
                             "lab_range_then_lo", "lab_range_then_hi", "lab_range_then_display",
                             "lab_range_now_lo", "lab_range_now_hi", "lab_range_now_display",
                             "is_good_then", "is_good_now", "full_history"],
            },
        },
        "dexa_history": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "date_display": {"type": "string"},
                    # Not _NULLABLE_NUMBER/_NULLABLE_STRING here on purpose - see the CRITICAL RULES
                    # note above. These four are required, sentinel-valued fields (-1 / "") so this
                    # schema doesn't cross the strict structured-output grammar's nullable-field limit
                    # (see markers.then/now above for the same constraint already in place).
                    "total_mass_lb": {"type": "number"},
                    "fat_mass_lb": {"type": "number"},
                    "lean_mass_lb": {"type": "number"},
                    "body_fat_pct": {"type": "string"},
                    "vat_fat_mass_lb": _NULLABLE_NUMBER,
                },
                "required": ["date_display", "total_mass_lb", "fat_mass_lb", "lean_mass_lb",
                             "body_fat_pct", "vat_fat_mass_lb"],
            },
        },
        "protocol": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "cadence": _NULLABLE_STRING,
                    "target_categories": {"type": "array", "items": {"type": "string"}},
                    "lab_visible": {"type": "boolean"},
                },
                "required": ["name", "cadence", "target_categories", "lab_visible"],
            },
        },
        "pain_points": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "categories"],
            },
        },
        "marker_overrides": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "marker": {"type": "string"},
                    "lo": {"type": "number"},
                    "hi": {"type": "number"},
                },
                "required": ["marker", "lo", "hi"],
            },
        },
        "unrecognized_markers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "raw_name": {"type": "string"},
                    "raw_value": {"type": "string"},
                    "raw_unit": {"type": "string"},
                    "raw_range": {"type": "string"},
                    "source_context": {"type": "string"},
                },
                "required": ["raw_name", "raw_value", "raw_unit", "raw_range", "source_context"],
            },
        },
        "other_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "age", "sex", "postmenopausal_bhrt", "on_trt", "first_draw_date", "latest_draw_date", "markers",
                 "dexa_history", "protocol", "pain_points", "marker_overrides",
                 "unrecognized_markers", "other_notes"],
}


def build_extraction_user_message(marker_library_summary: str, protocol_library_summary: str,
                                    provider_note_text: str | None) -> str:
    """Assembles the text portion of the extraction request. Lab/DEXA PDFs are attached separately
    as document content blocks in the actual API call (see pipeline.py) — the model reads them directly,
    they are not pre-parsed to text here."""
    parts = [
        "RECOGNIZED MARKER NAMES AND ALIASES:",
        marker_library_summary,
        "",
        "RECOGNIZED PROTOCOL COMPOUNDS:",
        protocol_library_summary,
        "",
    ]
    if provider_note_text:
        parts.append("PROVIDER'S CONSULTATION NOTE:")
        parts.append(provider_note_text)
        parts.append("")
    else:
        parts.append("No provider note was provided this round. Protocol and pain_points should be empty "
                      "unless a note is attached separately.")
    parts.append("Any lab PDF and/or DEXA PDF for this patient are attached to this message directly. "
                  "Extract from them per the rules above and return the structured JSON object.")
    return "\n".join(parts)
