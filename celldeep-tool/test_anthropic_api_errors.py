import pytest
import httpx2

from anthropic import APIConnectionError, APITimeoutError, RateLimitError

import pipeline
from schema import PatientRecord


class _FailingMessages:
    def __init__(self, error):
        self.error = error

    def create(self, **_kwargs):
        raise self.error


class _FailingClient:
    def __init__(self, error):
        self.messages = _FailingMessages(error)
        self.timeout = 240.0


def _rate_limit_error():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (APITimeoutError(request=None), "Anthropic API timed out"),
        (_rate_limit_error(), "Anthropic API rate limit reached"),
        (APIConnectionError(request=None), "Could not connect to the Anthropic API"),
    ],
)
@pytest.mark.parametrize(
    ("operation", "call"),
    [
        ("extraction", lambda client: pipeline.extract(client, labs_pdf=None, dexa_pdfs=[], note_text=None)),
        ("copy generation", lambda client: pipeline.generate_copy(client, PatientRecord(name="API Error Patient"))),
    ],
)
def test_anthropic_api_errors_raise_clear_application_error(error, expected_message, operation, call):
    with pytest.raises(pipeline.AnthropicAPIError, match=expected_message) as caught:
        call(_FailingClient(error))

    assert operation in str(caught.value)
    assert "Please retry" in str(caught.value)


def test_anthropic_timeout_log_includes_configured_timeout(capsys):
    with pytest.raises(pipeline.AnthropicAPIError):
        pipeline.extract(_FailingClient(APITimeoutError(request=None)), None, [], None)

    output = capsys.readouterr().out
    assert "ANTHROPIC-API-CALL-START" in output
    assert "ANTHROPIC-API-CALL-END" in output
    assert "outcome=sdk_timeout" in output
    assert "configured_timeout=240.0" in output
    assert "start_time=" in output
    assert "end_time=" in output
    assert "duration_seconds=" in output