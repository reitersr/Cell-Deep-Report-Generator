"""
CellDeep Report Generator — API Schema Grammar Guard
========================================================
This exists because the same failure class has now hit production three times in one week:
Anthropic's strict structured-output grammar compiler rejects a JSON schema as "too large" once
it has too many nullable/union-typed fields (`{"type": [..., "null"]}`), and this has only ever
been caught by a live production API call failing, never by a local test.

count_nullable_fields() is a rough proxy for whatever the real compiled-grammar limit is, not an
exact mirror of it — SAFE_THRESHOLD is set well under the real limit on purpose, so this guard
fails loudly in CI/local testing long before a schema gets anywhere near the actual API cutoff.

discover_output_schemas() finds every top-level dict named `*_SCHEMA` in extraction_prompt.py and
generation_prompt.py, so a new schema added to either module in the future is checked automatically
without anyone needing to remember to update this file.
"""

import extraction_prompt
import generation_prompt

SAFE_THRESHOLD = 10


def count_nullable_fields(schema) -> int:
    """Recursively count every JSON-schema node typed as a union (e.g. ["number", "null"])."""
    count = 0
    if isinstance(schema, dict):
        type_field = schema.get("type")
        if isinstance(type_field, list) and len(type_field) > 1:
            count += 1
        for value in schema.values():
            count += count_nullable_fields(value)
    elif isinstance(schema, list):
        for item in schema:
            count += count_nullable_fields(item)
    return count


def discover_output_schemas() -> dict:
    schemas = {}
    for module in (extraction_prompt, generation_prompt):
        for name in dir(module):
            if name.endswith("_SCHEMA"):
                candidate = getattr(module, name)
                if isinstance(candidate, dict):
                    schemas[f"{module.__name__}.{name}"] = candidate
    return schemas
