"""The Bloodwork Collected Date must reach the server with a scanned lab PDF, in every way staff submit it.
Production showed "contains scanned pages" with the date filled in; this pins the whole path from the real
form in Chromium to the job. Synthetic PDFs; every request is answered locally (no network)."""

import io
import re
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

import app
from synthetic_fixtures import dexa as dexa_fx


def _lab_pdf(path):
    document = fitz.open()
    document.new_page().insert_text((40, 60), "Collected: 04/24/2026  TSH 1.9", fontsize=10)
    source = fitz.open()
    source.new_page().insert_text((40, 60), "TSH 1.19", fontsize=10)
    document.new_page().insert_image(document[0].rect, pixmap=source[0].get_pixmap(dpi=72))  # image-only page
    document.save(path)
    return path


@pytest.fixture
def files(tmp_path):
    return _lab_pdf(tmp_path / "Pat_S_BW.pdf"), dexa_fx.write_pdf(tmp_path / "Pat_S_Dexas.pdf",
                                                                   [f"page {n}" for n in range(6)])


def _post(client, fields):
    started = []
    with patch.object(app, "_run_report_job", side_effect=lambda directory, data, run: started.append(data)):
        response = client.post("/generate", **fields)
    return response, started


@pytest.mark.parametrize("date", ["2026-04-24", "04/24/2026"])
def test_scanned_lab_pdf_with_the_date_starts_the_job(staff_client, tmp_path, monkeypatch, files, date):
    """The production entry: name, age 46, scanned lab PDF, Collected date and a 6-page DEXA PDF."""
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    labs, dexa = files
    response, started = _post(staff_client, {"data": {
        "patient_name": "Pat S", "age": "46", "sex": "male", "collected_date": date,
        "labs_pdf": (io.BytesIO(labs.read_bytes()), labs.name), "dexa_pdfs": [(io.BytesIO(dexa.read_bytes()), dexa.name)]}})
    assert response.status_code == 200 and "scanned pages" not in response.get_data(as_text=True)
    assert started[0]["collected_date"] == "04/24/2026" and started[0]["age"] == 46
    assert len(started[0]["dexa_paths"]) == 1


def test_rejection_says_what_was_missing_keeps_the_entries_and_logs_no_values(staff_client, tmp_path, monkeypatch,
                                                                             files, capsys):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    labs, _ = files
    response, started = _post(staff_client, {"data": {
        "patient_name": "Pat S", "age": "46", "sex": "male", "note_text": "## Consultation Note\nSynthetic.",
        "vitality_energy": "Some Concern", "labs_pdf": (io.BytesIO(labs.read_bytes()), labs.name)}})
    page = response.get_data(as_text=True)
    assert response.status_code == 422 and not started
    assert app.SCANNED_WITHOUT_DATE in page
    assert 'name="patient_name" value="Pat S"' in page and 'name="age" value="46"' in page
    assert '<option value="male" selected>' in page and "## Consultation Note" in page
    assert "<option selected>Some Concern</option>" in page
    log = capsys.readouterr().out
    assert "upload rejected: scanned lab pages and no collected_date received" in log
    assert "file_fields=['labs_pdf']" in log and "'collected_date'" not in log
    assert "Pat" not in log and "Some Concern" not in log and "Consultation" not in log  # names only, no values
    assert not list((tmp_path / "jobs").glob("*"))


def test_pages_are_never_served_from_the_browser_cache(staff_client):
    """A stored copy of the form could show an old rejection next to newly entered fields."""
    assert staff_client.get("/").headers["Cache-Control"] == "no-store"


def test_date_field_comes_before_the_files_in_the_form(staff_client):
    """Browsers post fields in page order: the date is sent ahead of the large PDF files."""
    page = staff_client.get("/").get_data(as_text=True)
    assert page.index('name="collected_date"') < page.index('name="labs_pdf"') < page.index('name="dexa_pdfs"')


@pytest.mark.parametrize("order", [("date", "labs", "dexa"), ("labs", "dexa", "date"), ("labs", "date", "dexa")])
def test_real_form_in_chromium_posts_the_date_and_the_server_accepts_it(staff_client, tmp_path, monkeypatch, files,
                                                                        order):
    """Fill the real form in Chromium in different orders, capture exactly what the browser posts, and replay it
    into the app: choosing files never clears the date, and the server starts the job with it."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    labs, dexa = files
    form_html = staff_client.get("/").get_data(as_text=True)
    posted = {}

    def capture(route):
        posted.update(type=route.request.headers["content-type"], body=route.request.post_data_buffer)
        route.fulfill(status=200, body="captured")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.route("https://celldeep.example/", lambda route: route.fulfill(status=200, body=form_html,
                                                                             content_type="text/html"))
        page.route("https://celldeep.example/generate", capture)
        page.goto("https://celldeep.example/")
        page.fill('input[name="patient_name"]', "Pat S")
        page.fill('input[name="age"]', "46")
        steps = {"date": lambda: page.fill('input[name="collected_date"]', "2026-04-24"),
                 "labs": lambda: page.set_input_files('input[name="labs_pdf"]', str(labs)),
                 "dexa": lambda: page.set_input_files('input[name="dexa_pdfs"]', str(dexa))}
        for step in order:
            steps[step]()
        assert page.input_value('input[name="collected_date"]') == "2026-04-24"
        page.click('button[type="submit"]')
        page.wait_for_function("document.body.innerText.includes('captured')", timeout=5000)
        browser.close()
    names = [name.decode() for name in re.findall(rb'name="([^"]+)"', posted["body"])]
    assert names.index("collected_date") < names.index("labs_pdf")
    response, started = _post(staff_client, {"data": posted["body"], "content_type": posted["type"]})
    assert response.status_code == 200 and started[0]["collected_date"] == "04/24/2026"
