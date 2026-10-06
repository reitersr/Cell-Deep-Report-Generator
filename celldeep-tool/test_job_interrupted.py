"""A job whose process was killed (out of memory, restart, redeploy) must say so instead of spinning.
Synthetic data only; no network (the browser test answers every request itself)."""

import os
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import app

INTERRUPTED = {"status": "interrupted", "error": "This report job was interrupted. Please try again"}


def _job(tmp_path, monkeypatch, status="processing", age_seconds=0):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    job_id = "b" * 32
    directory = tmp_path / "jobs" / job_id
    directory.mkdir(parents=True)
    app._write_job_status(directory, status)
    app._touch_heartbeat(directory)
    old = time.time() - age_seconds
    for path in (directory, directory / "status.json", directory / "heartbeat"):
        os.utime(path, (old, old))
    return job_id


def test_message_text():
    assert app.JOB_INTERRUPTED_MESSAGE == "This report job was interrupted. Please try again"


def test_status_of_a_job_that_no_longer_exists_says_interrupted(staff_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    response = staff_client.get("/generate/status/" + "c" * 32)
    assert response.status_code == 404
    assert response.get_json() == INTERRUPTED


def test_malformed_job_id_also_says_interrupted(staff_client):
    response = staff_client.get("/generate/status/not-a-job")
    assert response.status_code == 404 and response.get_json() == INTERRUPTED


def test_processing_job_without_a_heartbeat_says_interrupted(staff_client, tmp_path, monkeypatch):
    job_id = _job(tmp_path, monkeypatch, age_seconds=app.STALE_JOB_SECONDS + 5)
    response = staff_client.get(f"/generate/status/{job_id}")
    assert response.status_code == 200 and response.get_json() == INTERRUPTED


def test_processing_job_with_a_fresh_heartbeat_is_still_processing(staff_client, tmp_path, monkeypatch):
    job_id = _job(tmp_path, monkeypatch, age_seconds=app.STALE_JOB_SECONDS - 30)
    assert staff_client.get(f"/generate/status/{job_id}").get_json() == {"status": "processing"}


@pytest.mark.parametrize("status", ["done", "error"])
def test_finished_jobs_are_never_reported_as_interrupted(staff_client, tmp_path, monkeypatch, status):
    job_id = _job(tmp_path, monkeypatch, status=status, age_seconds=app.STALE_JOB_SECONDS * 10)
    assert staff_client.get(f"/generate/status/{job_id}").get_json()["status"] == status


def test_a_long_running_job_keeps_its_heartbeat(staff_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(app, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(app, "STALE_JOB_SECONDS", 0.5)
    seen = []

    def slow_run(**kwargs):
        directory = Path(kwargs["out_path"]).parent
        for _ in range(15):  # 1.5s: three times longer than the stale limit
            seen.append(app._job_is_stale(directory))
            time.sleep(0.1)
        Path(kwargs["out_path"]).write_bytes(b"synthetic report PDF")
        (tmp_path / "review.txt").write_text("synthetic review", encoding="utf-8")
        return str(tmp_path / "review.txt")

    with patch.object(app.pipeline, "run", side_effect=slow_run):
        response = staff_client.post("/generate", data={"patient_name": "Synthetic, Pat"})
    job_id = re.search(r'data-job-id="([a-f0-9]+)"', response.get_data(as_text=True)).group(1)
    deadline = time.monotonic() + 5
    while staff_client.get(f"/generate/status/{job_id}").get_json()["status"] != "done":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert seen and not any(seen)


def _generating_page(staff_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    with patch.object(app, "_run_report_job"):  # no job runs: the browser test answers status itself
        return staff_client.post("/generate", data={"patient_name": "Synthetic, Pat"}).get_data(as_text=True)


@pytest.mark.parametrize("status_code, body", [
    (404, '{"status": "interrupted", "error": "This report job was interrupted. Please try again"}'),
    (404, "<html>Not Found</html>"),  # any 404, even without the JSON body
    (200, '{"status": "interrupted", "error": "This report job was interrupted. Please try again"}'),
])
def test_generating_page_shows_the_message_and_stops_polling(staff_client, tmp_path, monkeypatch,
                                                             status_code, body):
    from playwright.sync_api import sync_playwright

    html = _generating_page(staff_client, tmp_path, monkeypatch)
    polls = []

    def answer_status(route):
        polls.append(route.request.url)
        route.fulfill(status=status_code, body=body, content_type="application/json")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.route("**/generate/status/**", answer_status)
        page.route("https://celldeep.example/generating",
                   lambda route: route.fulfill(status=200, body=html, content_type="text/html"))
        page.goto("https://celldeep.example/generating")
        page.wait_for_function("document.getElementById('status').className === 'error'", timeout=5000)
        text = page.inner_text("#status")
        page.wait_for_timeout(2000)  # longer than the 1.5s poll interval
        browser.close()
    assert text == "This report job was interrupted. Please try again"
    assert len(polls) == 1
