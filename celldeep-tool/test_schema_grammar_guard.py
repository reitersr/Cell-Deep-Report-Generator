"""Guard test for the recurring 'compiled grammar too large' / strict-tools API failure.

This has hit production three times in one week because nullable/union-typed JSON schema
fields sent to the API's strict structured-output feature were never audited locally - only a
live API call ever caught it. This test counts those fields across every schema the pipeline
sends to the API and fails loudly, well before the real API-side limit, if one creeps back up.
"""

import pytest

from schema_grammar_guard import SAFE_THRESHOLD, count_nullable_fields, discover_output_schemas


def test_at_least_one_output_schema_is_discovered():
    # sanity check: if this ever finds nothing, the guard is silently checking nothing
    assert discover_output_schemas()


@pytest.mark.parametrize("name", sorted(discover_output_schemas()))
def test_schema_stays_under_the_safe_nullable_field_threshold(name):
    schema = discover_output_schemas()[name]
    count = count_nullable_fields(schema)
    assert count <= SAFE_THRESHOLD, (
        f"{name} has {count} nullable/union-typed fields (safe threshold {SAFE_THRESHOLD}). "
        "This exact pattern has caused a 'compiled grammar too large' / strict-tools API failure "
        "in production before - reduce nullable fields using the required-field-with-sentinel "
        "pattern (see extraction_prompt.py's disp_now/-1 sentinel precedent) before this ships."
    )
