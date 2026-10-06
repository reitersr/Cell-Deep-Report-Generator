"""Synthetic DEXA PDFs and mocked double reads. Invented values only.

Each page is image-only and carries a label ("summary", "trend", ...). DexaReader recognizes the
rendered page image exactly as scan_dexa.read_page renders it, so pages can be shuffled and every
page still gets its own literal transcriptions. The page number in each read is taken from the
request, as a real transcription would report it.
"""

import base64
import copy
import json
import re
from types import SimpleNamespace

import fitz
from synthetic_fixtures.sdk_contract import check_create_kwargs

PATIENT = "Synthetic, Pat"


def scan(date, total=None, fat=None, lean=None, body_fat=None, vat=None, area=None, age=None):
    return {"date": date, "age": age, "total_mass": total, "fat_mass": fat, "lean_mass": lean,
            "body_fat_pct": body_fat, "vat_mass": vat, "vat_area": area}


def read(scans, patient_name=PATIENT, illegible=False, date_of_birth=None, age=None):
    return {"page": 0, "patient_name": patient_name, "date_of_birth": date_of_birth, "age": age,
            "illegible": illegible, "scans": copy.deepcopy(scans)}


BASELINE = scan("05/12/2025", "172.0", "58.0", "108.9", "33.7 %", "1.06", "84.0")
FOLLOW_UP = scan("02/03/2026", "166.0", "50.5", "110.0", "30.4 %", "0.88", "71.5")
VAT_ONLY = scan("03/09/2026", vat="0.77")


def write_pdf(path, labels):
    """An image-only DEXA PDF whose pages show the given labels, in that order."""
    document = fitz.open()
    for label in labels:
        source = fitz.open()
        page = source.new_page()
        page.insert_text((40, 60), f"Synthetic DEXA report page: {label}", fontsize=12)
        document.new_page().insert_image(page.rect, pixmap=page.get_pixmap(dpi=72))
        source.close()
    document.save(path)
    document.close()
    return path


def _image(page):
    return base64.b64encode(page.get_pixmap(dpi=200, alpha=False).tobytes("png")).decode("ascii")


class DexaReader:
    """Mocked Anthropic client. pages: {label: (read1, read2)} for the labels in `path`."""

    def __init__(self, path, labels, pages):
        with fitz.open(path) as document:
            self.by_image = {_image(page): label for page, label in zip(document, labels)}
        self.pages = pages
        self.calls = []
        self.messages = self
        self.timeout = 240.0

    def create(self, **kwargs):
        check_create_kwargs(kwargs)  # the installed SDK must accept this call
        self.calls.append(kwargs)
        content = kwargs["messages"][0]["content"]
        label = self.by_image[content[0]["source"]["data"]]
        number = int(re.search(r"page (\d+)", content[1]["text"])[1])
        image = content[0]["source"]["data"]
        index = sum(call["messages"][0]["content"][0]["source"]["data"] == image for call in self.calls[:-1]) % 2
        payload = {**copy.deepcopy(self.pages[label][index]), "page": number}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))], stop_reason="end_turn")
