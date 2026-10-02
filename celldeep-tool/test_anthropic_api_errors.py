import fitz
import pytest
import httpx2

from anthropic import APIConnectionError, APITimeoutError, RateLimitError

import pipeline


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


@pytest.fixture
def dexa_pdf(tmp_path):
    path = tmp_path / "dexa.pdf"
    document = fitz.open()
    document.new_page().insert_text((40, 60), "Synthetic DEXA report", fontsize=10)
    document.save(path)
    document.close()
    return str(path)


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (APITimeoutError(request=None), "Anthropic API timed out"),
        (_rate_limit_error(), "Anthropic API rate limit reached"),
        (APIConnectionError(request=None), "Could not connect to the Anthropic API"),
    ],
)
def test_anthropic_api_errors_during_dexa_extraction_raise_clear_application_error(error, expected_message, dexa_pdf):
    with pytest.raises(pipeline.AnthropicAPIError, match=expected_message) as caught:
        pipeline.extract(None, [dexa_pdf], None, client=_FailingClient(error))

    assert "extraction" in str(caught.value)
    assert "Please retry" in str(caught.value)


def test_anthropic_timeout_log_includes_configured_timeout(capsys, dexa_pdf):
    with pytest.raises(pipeline.AnthropicAPIError):
        pipeline.extract(None, [dexa_pdf], None, client=_FailingClient(APITimeoutError(request=None)))

    output = capsys.readouterr().out
    assert "ANTHROPIC-API-CALL-START" in output
    assert "ANTHROPIC-API-CALL-END" in output
    assert "outcome=sdk_timeout" in output
    assert "configured_timeout=240.0" in output


def test_run_configures_anthropic_timeout_and_retry_limit(monkeypatch, dexa_pdf):
    configured = {}

    class CapturingAnthropic:
        def __init__(self, **kwargs):
            configured.update(kwargs)

    class StopAfterClientConstruction(RuntimeError):
        pass

    def stop_after_client_construction(*_args, **_kwargs):
        raise StopAfterClientConstruction

    monkeypatch.setattr(pipeline, "Anthropic", CapturingAnthropic)
    monkeypatch.setattr(pipeline, "extract", stop_after_client_construction)

    with pytest.raises(StopAfterClientConstruction):
        pipeline.run(None, [dexa_pdf], None, "Test Patient", None, None, "unused.pdf")

    assert configured["timeout"] == 240.0
    assert configured["max_retries"] == 1
