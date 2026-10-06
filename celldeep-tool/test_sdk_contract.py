"""Every argument the pipeline sends to the Anthropic SDK must be accepted by the SDK version production installs.

Regression: PR #11 passed temperature=0 to messages.create(); anthropic 1.x removed temperature/top_p/top_k, so
production failed with "Messages.create() got an unexpected keyword argument 'temperature'" while every test
passed, because the mocked clients accepted any **kwargs. All mocked clients now call
synthetic_fixtures.sdk_contract.check_create_kwargs(); this file covers each call site directly, the client
constructors, the model id and the version pin."""

import re
import typing
from pathlib import Path

import anthropic
import fitz
import pytest
from anthropic.types import ModelParam

import pipeline
import scan_bloodwork
import scan_dexa
from synthetic_fixtures import deterministic_fixtures as labs_fx
from synthetic_fixtures import dexa as dexa_fx
from synthetic_fixtures.sdk_contract import SDKContractError, check_client_kwargs, check_create_kwargs

ROOT = Path(pipeline.__file__).parent


class _Stop(Exception):
    pass


class _Recorder:
    """Records the kwargs of each messages.create call, then stops the read."""

    def __init__(self):
        self.calls, self.messages, self.timeout = [], self, 240.0

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise _Stop


def _call_site_kwargs():
    page = fitz.open().new_page()
    sites = {}
    for name, read in (("scan_bloodwork.read_page", lambda client: scan_bloodwork.read_page(
                            page, client, pipeline._create_anthropic_message, pipeline.MODEL)),
                       ("scan_dexa.read_page", lambda client: scan_dexa.read_page(
                            page, "file 1 page 1", client, pipeline._create_anthropic_message, pipeline.MODEL))):
        recorder = _Recorder()
        with pytest.raises(_Stop):
            read(recorder)
        sites[name] = recorder.calls[0]
    return sites


@pytest.mark.parametrize("site", ["scan_bloodwork.read_page", "scan_dexa.read_page"])
def test_every_messages_create_call_site_matches_the_installed_sdk(site):
    kwargs = _call_site_kwargs()[site]
    check_create_kwargs(kwargs)  # signature of Messages.create plus the SDK's own parameter types
    assert not {"temperature", "top_p", "top_k", "output_format"} & set(kwargs)
    assert set(kwargs) == {"model", "max_tokens", "system", "messages", "output_config"}


def test_the_production_failure_is_caught_by_the_contract():
    kwargs = _call_site_kwargs()["scan_bloodwork.read_page"]
    for removed in ("temperature", "top_p", "top_k"):
        with pytest.raises(SDKContractError, match=f"unexpected keyword argument '{removed}'"):
            check_create_kwargs({**kwargs, removed: 0.0})
    with pytest.raises(SDKContractError, match="output_format"):
        check_create_kwargs({**kwargs, "output_format": kwargs["output_config"]["format"]})
    with pytest.raises(SDKContractError, match="unknown keys"):
        check_create_kwargs({**kwargs, "output_config": {"formats": kwargs["output_config"]["format"]}})
    with pytest.raises(SDKContractError, match="matches none"):
        check_create_kwargs({**kwargs, "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": "x"}}]}]})  # media_type is required


def test_the_model_id_is_known_to_the_installed_sdk():
    literal = next(arg for arg in typing.get_args(ModelParam) if typing.get_origin(arg) is typing.Literal)
    assert pipeline.MODEL in typing.get_args(literal)


def _patch_client(monkeypatch, made):
    def make(**kwargs):
        check_client_kwargs(kwargs)
        made.append(kwargs)
        return _Recorder()
    monkeypatch.setattr(pipeline, "Anthropic", make)


def test_every_client_constructor_matches_the_installed_sdk(tmp_path, monkeypatch):
    made = []
    _patch_client(monkeypatch, made)
    monkeypatch.setattr(pipeline, "_review_notes_path", lambda name: str(tmp_path / "review.txt"))
    dexa = dexa_fx.write_pdf(tmp_path / "dexa.pdf", ["summary"])
    labs = labs_fx.write_lab_pdf(tmp_path / "labs.pdf", [
        labs_fx.section_preamble("SYN-SDK", "04/14/2026") + labs_fx.header_line(100) + labs_fx.row(130, "TSH", "1.0"),
        []])
    # pipeline.run builds the client when DEXA PDFs are given ...
    with pytest.raises(_Stop):
        pipeline.run(str(labs), [str(dexa)], None, "Synthetic, Pat", 44, "male", str(tmp_path / "r.pdf"),
                     collected_date="04/14/2026")
    # ... the scanned-bloodwork and DEXA readers build their own when called without one.
    with pytest.raises(_Stop):
        pipeline.extract(str(labs), [], None, patient_name="Synthetic, Pat", collected_date="04/14/2026",
                         audit_root=str(tmp_path))
    with pytest.raises(_Stop):
        pipeline._extract_dexa_with_claude(None, [str(dexa)], "Synthetic, Pat")
    assert len(made) == 3
    assert all(set(kwargs) == {"api_key", "timeout", "max_retries"} for kwargs in made)


def test_tests_run_the_sdk_version_production_installs():
    pinned = re.search(r"^anthropic==(\S+)$", (ROOT / "requirements.txt").read_text(), re.M)
    assert pinned, "requirements.txt must pin anthropic to an exact version (production and CI install it)"
    assert anthropic.__version__ == pinned[1]
