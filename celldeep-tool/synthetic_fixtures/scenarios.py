"""End-to-end synthetic scenarios: a lab PDF (and DEXA PDF) built from invented data, a mocked vision model,
and one full pipeline.run. Used by the smoke test (scripts/smoke_test.py), the scenario tests and the
before/after comparison of a change. No network: every vision read is scripted here.

run_scenario(name, folder) returns the patient report text, the staff notes, the confirmation-step items and
the number of vision reads made.
"""

import base64
import copy
import json
import re
from types import SimpleNamespace

import fitz

import pipeline
from synthetic_fixtures import access_medical_lab, clinic_dexa
from synthetic_fixtures import deterministic_fixtures as fx
from synthetic_fixtures import dexa as dexa_fx
from synthetic_fixtures import layouts
from synthetic_fixtures.sdk_contract import check_create_kwargs


def _image(page):
    return base64.b64encode(page.get_pixmap(dpi=200, alpha=False).tobytes("png")).decode("ascii")


class ScriptedVision:
    """Mocked Anthropic client. reads_by_page: {(path, page number): [read 1, read 2, (read 3)]}. The n-th
    request for a page answers its n-th scripted read (cycling), with the page number the request names."""

    def __init__(self, reads_by_page):
        self.by_image, self.counts, self.calls = {}, {}, []
        for (path, number), reads in reads_by_page.items():
            with fitz.open(path) as document:
                self.by_image[_image(document[number - 1])] = reads
        self.messages, self.timeout = self, 240.0

    def create(self, **kwargs):
        check_create_kwargs(kwargs)  # the installed SDK must accept this call
        self.calls.append(kwargs)
        content = kwargs["messages"][0]["content"]
        image = content[0]["source"]["data"]
        reads = self.by_image[image]
        count = self.counts.get(image, 0)
        self.counts[image] = count + 1
        number = int(re.search(r"page (\d+)", content[1]["text"])[1])
        payload = copy.deepcopy(reads[count % len(reads)])
        payload["page"] = number
        for row in payload.get("rows", []):
            row["page"] = number
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))], stop_reason="end_turn")


def _row(name, result, flag=None, reference=None, column="in_range", section="ROUTINE PANELS"):
    return {"name": name, "result_text": result, "flag": flag, "reference_range": reference, "lab_code": None,
            "column": column, "page": 0, "illegible": False, "section": section}


def _scan_page(rows):
    return {"page": 0, "specimen_id": None, "collected": None, "footer": None, "patient_name": layouts.PATIENT,
            "date_of_birth": None, "illegible": False, "rows": rows, "out_of_range_summary": None}


def noisy_scan_reads():
    """Two scanned pages whose reads vary the way production reads did: a section heading with no value read
    in one read only, one row the second read gets wrong, and one row every read gets differently."""
    stable = [_row("TSH", "2.10", reference="0.40-4.50"), _row("HEMOGLOBIN A1c", "5.2", reference="<5.7")]
    heading = _row("CBC (INCLUDES DIFF/PLT)", None, reference=None, column=None)
    insulin = _row("INSULIN", "4.4", reference="<OR=18.4 uIU/mL")
    page_one = [_scan_page(stable[:1] + [heading, insulin]),
                _scan_page(stable[:1] + [dict(insulin, result_text="4.9")]),
                _scan_page(stable[:1] + [insulin])]
    ferritin = _row("FERRITIN", "88", reference="38-380 ng/mL")
    page_two = [_scan_page(stable[1:] + [ferritin]),
                _scan_page(stable[1:] + [dict(ferritin, result_text="86")]),
                _scan_page(stable[1:] + [dict(ferritin, result_text="83")])]
    return page_one, page_two


def _text(path):
    with fitz.open(path) as document:
        return " ".join(" ".join(page.get_text().split()) for page in document)


def _dexa_labs(folder):
    return fx.write_lab_pdf(folder / "labs.pdf", [
        fx.section_preamble("SYN-DEXA", clinic_dexa.LAB_DATE) + fx.header_line(100)
        + fx.row(130, "TSH", "1.9", units="uIU/mL", lab_range="0.40-4.50")
        + fx.row(144, "hs-CRP", "0.4", units="mg/L", lab_range="0.0-3.0")])


def build(name, folder):
    """(lab PDF, [DEXA PDFs], vision reads by page, run kwargs) for one scenario."""
    if name in layouts.FIXTURES:
        builder, reader = layouts.FIXTURES[name]
        labs = builder(folder / "labs.pdf")
        reads = {}
        if reader is not None:
            first = 2 if name == "mixed" else 1
            for page in layouts._scan_pages(first):
                reads[(str(labs), page["page"])] = [page, page]
        # Staff enter the draw's own Collected date: a date with no accepted result stops the report.
        options = {"collected_date": layouts.COLLECTED.get(name, layouts.SCAN_DATE), "age": 44}
        if name == "variant":  # a pasted medication list instead of the structured note
            options["note"] = "Testosterone cypionate 100 mg weekly\nBPC-157 250 mcg daily\n"
        return str(labs), [], reads, options
    if name == "scanned_noisy":
        labs = layouts.scanned(folder / "labs.pdf")
        one, two = noisy_scan_reads()
        return str(labs), [], {(str(labs), 1): one, (str(labs), 2): two}, {"collected_date": layouts.SCAN_DATE,
                                                                           "age": 44}
    if name.startswith("clinic_dexa"):
        dexa = dexa_fx.write_pdf(folder / "dexa.pdf", clinic_dexa.LABELS)
        script = clinic_dexa.pages()
        reads = {(str(dexa), number): script[label] for number, label in enumerate(clinic_dexa.LABELS, start=1)}
        age = clinic_dexa.AGE if name == "clinic_dexa" else None
        return str(_dexa_labs(folder)), [str(dexa)], reads, {"age": age}
    if name.startswith("access_medical"):
        # One draw (Access Medical Laboratories layout) and one DEXA scan: names redacted, the age printed on one
        # page only, no VAT/SAT page.
        limited = name == "access_medical_limited"  # the limited male panel of the clinic's first run
        labs = access_medical_lab.write(folder / "labs.pdf", access_medical_lab.limited_pages() if limited else None)
        dexa = dexa_fx.write_pdf(folder / "dexa.pdf", ["access-image", "access-segmental"])
        scan = dexa_fx.scan(ACCESS_SCAN_DATE, "172.0", "30.1", "136.4", "18.1 %")
        reads = {(str(dexa), 1): [dexa_fx.read([scan], patient_name=None, age="21.5")] * 2,
                 (str(dexa), 2): [dexa_fx.read([scan], patient_name=None)] * 2}
        options = {"age": access_medical_lab.AGE, "patient": access_medical_lab.STAFF_NAME,
                   "collected_date": access_medical_lab.COLLECTED_FULL}
        if name == "access_medical_other_date":
            options["collected_date"] = "08/14/2026"  # staff entered a different date than the lab header
        return str(labs), [str(dexa)], reads, options
    raise KeyError(name)


ACCESS_SCAN_DATE = "07/20/2026"
SCENARIOS = ["quest_digital", "chl_digital", "labcorp_digital", "scanned", "mixed", "variant", "scanned_noisy",
             "clinic_dexa", "clinic_dexa_no_age", "access_medical", "access_medical_limited"]


def run_scenario(name, folder, monkeypatch=None):
    """Run one scenario end to end in folder. monkeypatch (pytest's) is used when given; otherwise the
    pipeline hooks are patched and restored here."""
    folder.mkdir(parents=True, exist_ok=True)
    labs, dexa, reads, options = build(name, folder)
    vision = ScriptedVision(reads)
    patches = {"Anthropic": lambda **kwargs: vision,
               "_review_notes_path": lambda _name: str(folder / "review.txt"),
               "_diagnostic_path_prefix": lambda _name: str(folder / "diag")}
    saved = {key: getattr(pipeline, key) for key in patches}
    for key, value in patches.items():
        if monkeypatch is not None:
            monkeypatch.setattr(pipeline, key, value)
        else:
            setattr(pipeline, key, value)
    asked = []

    def confirm(items):
        asked.extend(items)
        return True

    try:
        out = folder / "report.pdf"
        patient = options.get("patient") or (clinic_dexa.PATIENT if dexa else layouts.PATIENT)
        pipeline.run(labs, dexa, options.get("note"), patient, options.get("age"), "male", str(out),
                     collected_date=options.get("collected_date"), confirm=confirm)
    finally:
        if monkeypatch is None:
            for key, value in saved.items():
                setattr(pipeline, key, value)
    html = out.with_suffix(".html")
    return {"text": _text(out), "review": (folder / "review.txt").read_text(encoding="utf-8"),
            "html": html.read_text(encoding="utf-8") if html.exists() else "",
            "confirmation": asked, "reads": len(vision.calls)}
