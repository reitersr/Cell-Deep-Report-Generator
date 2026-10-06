"""The generating page's confirmation step, in a real browser: what was left out is listed before the report is
built, and staff can stop or continue. Every request is answered by the test (no network); synthetic data."""

import json
from unittest.mock import patch

import pytest

import app

ITEMS = ["DEXA file 1 page 6: no patient name printed and it prints age 34.1; scan dates 06/15/2026",
         "Lab PDF page 3 (whole page): result rows printed under no recognized table header"]


def _page(staff_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    with patch.object(app, "_run_report_job"):
        return staff_client.post("/generate", data={"patient_name": "Synthetic, Pat"}).get_data(as_text=True)


@pytest.mark.parametrize("action", ["stop", "continue"])
def test_confirmation_lists_the_gaps_and_sends_the_choice(staff_client, tmp_path, monkeypatch, action):
    from playwright.sync_api import sync_playwright

    html = _page(staff_client, tmp_path, monkeypatch)
    decisions, state = [], {"status": "confirm"}

    def status(route):
        body = {"status": "confirm", "items": ITEMS} if state["status"] == "confirm" else (
            {"status": "stopped", "error": app.STOPPED_MESSAGE} if decisions == ["stop"] else
            {"status": "done", "report_url": "/r", "review_notes_url": "/n"})
        route.fulfill(status=200, body=json.dumps(body), content_type="application/json")

    def decide(route):
        decisions.append("continue" if b"continue" in route.request.post_data_buffer else "stop")
        state["status"] = "decided"
        route.fulfill(status=200, body='{"status": "processing"}', content_type="application/json")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.route("https://celldeep.example/generating",
                   lambda route: route.fulfill(status=200, body=html, content_type="text/html"))
        page.route("**/generate/status/**", status)
        page.route("**/generate/decision/**", decide)
        page.goto("https://celldeep.example/generating")
        page.wait_for_selector("#status.confirm", timeout=5000)
        listed = page.inner_text("#status")
        button = "Stop, I will fix the files" if action == "stop" else "Generate the report without them"
        page.click(f"text={button}")
        final = "error" if action == "stop" else "result"
        page.wait_for_selector(f"#status.{final}", timeout=5000)
        shown = page.inner_text("#status")
        browser.close()
    assert all(item in listed for item in ITEMS)
    assert decisions == [action]
    assert (app.STOPPED_MESSAGE in shown) if action == "stop" else ("Download patient report" in shown)
