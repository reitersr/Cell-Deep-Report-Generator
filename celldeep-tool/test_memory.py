"""Peak memory of the scanned-page paths must not grow with the page count (a 512MB instance ran out of
memory), and lowering memory must not change what the vision reads are sent. Synthetic noise images
only; every vision call is mocked."""

import base64
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

import scan_bloodwork
import scan_dexa

HERE = Path(__file__).parent

_BUILD = """
import os, sys, fitz
document = fitz.open()
for _ in range(int(sys.argv[2])):
    # A 300 DPI letter-size page of noise: the worst case for decoded-image size.
    pixmap = fitz.Pixmap(fitz.csRGB, 2550, 3300, os.urandom(2550 * 3300 * 3), False)
    page = document.new_page()
    page.insert_image(page.rect, stream=pixmap.tobytes("jpeg"))
document.save(sys.argv[1])
"""

_PROBE = """
import json, resource, sys, tempfile
from types import SimpleNamespace
import pipeline

class Client:
    timeout = 1
    def __init__(self):
        self.messages = self
    def create(self, **kwargs):
        return SimpleNamespace(content=[], stop_reason="max_tokens")  # every page renders, then is excluded

path, route = sys.argv[1], sys.argv[2]
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
if route == "labs":
    pipeline.extract(path, [], None, patient_name="Synthetic, Pat", collected_date="04/14/2026",
                     client=Client(), audit_root=tempfile.mkdtemp())
else:
    pipeline._extract_dexa_with_claude(Client(), [path], "Synthetic, Pat")
print(json.dumps((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) // 1024))
"""


@pytest.fixture(scope="module")
def scanned_pdfs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("synthetic_scans")
    paths = {pages: folder / f"scan_{pages}.pdf" for pages in (1, 5)}
    for pages, path in paths.items():
        subprocess.run([sys.executable, "-c", _BUILD, str(path), str(pages)], check=True, cwd=HERE)
    return paths


def _peak_growth_mb(path, route):
    result = subprocess.run([sys.executable, "-c", _PROBE, str(path), route], check=True, cwd=HERE,
                            capture_output=True, text=True,
                            env={"PATH": "", "ANTHROPIC_API_KEY": "offline-mocked-key"})
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("route", ["labs", "dexa"])
def test_peak_memory_does_not_grow_with_scanned_pages(scanned_pdfs, route):
    one, five = _peak_growth_mb(scanned_pdfs[1], route), _peak_growth_mb(scanned_pdfs[5], route)
    # Before the fix every page's decoded image stayed cached (~30MB more per page, ~+120MB for five).
    assert five - one < 40, (one, five)
    assert five < 160, five


class _Recorder:
    timeout = 1

    def __init__(self, payload):
        self.messages, self.payload, self.images = self, payload, []

    def create(self, **kwargs):
        self.images.append(kwargs["messages"][0]["content"][0]["source"]["data"])
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.payload))], stop_reason="end_turn")


def _create(client, _operation, **kwargs):
    return client.messages.create(**kwargs)


_EMPTY_LAB_PAGE = {"page": 1, "specimen_id": None, "collected": None, "footer": None, "patient_name": None,
                   "date_of_birth": None, "illegible": False, "rows": [], "out_of_range_summary": None}
_EMPTY_DEXA_PAGE = {"page": 1, "patient_name": None, "date_of_birth": None, "age": None, "illegible": False,
                   "scans": []}


@pytest.mark.parametrize("route", ["labs", "dexa"])
def test_both_reads_get_the_same_single_render_at_the_unchanged_dpi(route, monkeypatch):
    assert scan_bloodwork.SCAN_RENDER_DPI == 200
    source = fitz.open()
    source.new_page().insert_text((40, 60), "Synthetic scanned page  TSH 1.9", fontsize=10)
    document = fitz.open()
    document.new_page().insert_image(fitz.paper_rect("letter"), pixmap=source[0].get_pixmap(dpi=150))
    page = document[0]
    expected = base64.b64encode(page.get_pixmap(dpi=200, alpha=False).tobytes("png")).decode("ascii")

    renders = []
    original = fitz.Page.get_pixmap
    monkeypatch.setattr(fitz.Page, "get_pixmap", lambda self, *a, **k: renders.append(k) or original(self, *a, **k))
    if route == "labs":
        recorder = _Recorder(_EMPTY_LAB_PAGE)
        reads = scan_bloodwork.read_page(page, recorder, _create, "mock-model")
    else:
        recorder = _Recorder(_EMPTY_DEXA_PAGE)
        reads = scan_dexa.read_page(page, "file 1 page 1", recorder, _create, "mock-model")
    assert len(reads) == 2
    assert renders == [{"dpi": 200, "alpha": False}]  # rendered once, shared by both reads
    assert recorder.images == [expected, expected]  # byte-identical to the render before this change
