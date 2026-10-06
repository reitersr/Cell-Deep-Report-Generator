import app
import io
import pipeline
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


def test_version_reports_render_commit(staff_client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "d326491fullsha")

    response = staff_client.get("/version")

    assert response.status_code == 200
    assert response.get_json() == {"commit": "d326491fullsha"}
    assert response.headers["Cache-Control"] == "no-store"


def _wait_for_status(client, job_id, expected_status):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(f"/generate/status/{job_id}")
        status = response.get_json()
        if status["status"] == expected_status:
            return response, status
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {expected_status}")


def test_generate_runs_as_disk_backed_background_job(staff_client, tmp_path, monkeypatch):
    captured = {}
    worker_started = threading.Event()
    release_worker = threading.Event()
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")

    def fake_run(**kwargs):
        captured.update(kwargs)
        captured["labs_bytes"] = Path(kwargs["labs_pdf"]).read_bytes()
        worker_started.set()
        assert release_worker.wait(timeout=2)
        Path(kwargs["out_path"]).write_bytes(b"synthetic report PDF")
        review_path = tmp_path / "review.txt"
        review_path.write_text("synthetic review", encoding="utf-8")
        return str(review_path)

    with patch.object(app.pipeline, "run", side_effect=fake_run):
        response = staff_client.post(
            "/generate",
            data={
                "patient_name": "Test Patient",
                "note_text": "Original note.",
                "collected_date": "2026-04-14",
                "labs_pdf": (io.BytesIO(b"synthetic input PDF"), "synthetic-labs.pdf"),
            },
        )

    assert response.status_code == 200
    job_id = re.search(r'data-job-id="([a-f0-9]+)"', response.get_data(as_text=True)).group(1)
    assert worker_started.wait(timeout=2)
    processing_response = staff_client.get(f"/generate/status/{job_id}")
    assert processing_response.get_json() == {"status": "processing"}
    release_worker.set()

    status_response, status = _wait_for_status(staff_client, job_id, "done")
    assert status_response.status_code == 200
    assert status["report_url"] == f"/download/{job_id}/report"
    assert status["review_notes_url"] == f"/download/{job_id}/review-notes"
    assert staff_client.get(status["report_url"]).data == b"synthetic report PDF"
    assert captured["note_text"] == "Original note."
    assert captured["collected_date"] == "04/14/2026"
    assert captured["vitality_index"] == {
        label: "Not Assessed" for _, label in app.VITALITY_FIELDS
    }
    assert captured["labs_bytes"] == b"synthetic input PDF"
    # The upload is deleted once the job ends; only the downloads remain.
    assert not Path(captured["labs_pdf"]).exists()
    assert {path.name for path in (tmp_path / "jobs" / job_id).iterdir()} == {
        "report.pdf", "review_notes.txt", "status.json", "heartbeat"}


def test_unrecognized_lab_layout_fails_the_upload_job_visibly(staff_client, tmp_path, monkeypatch, capsys):
    import fitz

    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    document = fitz.open()
    document.new_page().insert_text((40, 60), "Collected: 04/14/2026  hs-CRP 0.3 mg/L  TSH 1.9", fontsize=10)
    pdf_bytes = document.tobytes()
    document.close()

    response = staff_client.post("/generate", data={
        "patient_name": "Pat Synthetic",
        "labs_pdf": (io.BytesIO(pdf_bytes), "unknown-layout.pdf"),
    })

    job_id = re.search(r'data-job-id="([a-f0-9]+)"', response.get_data(as_text=True)).group(1)
    _, status = _wait_for_status(staff_client, job_id, "error")
    assert status["error"] == f"Generation failed. Job ID: {job_id}"
    assert "report_url" not in status
    log = capsys.readouterr().out
    assert f"generation failed job_id={job_id}" in log and "zero recognized marker rows" in log
    assert not (tmp_path / "jobs" / job_id / "report.pdf").exists()


def test_generate_status_shows_a_plain_failure_with_the_job_id(staff_client, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")

    def fake_run(**_kwargs):
        raise pipeline.AnthropicAPIError("Anthropic API timed out during extraction. Please retry the report.")

    with patch.object(app.pipeline, "run", side_effect=fake_run):
        response = staff_client.post("/generate", data={"patient_name": "Test Patient"})

    job_id = re.search(r'data-job-id="([a-f0-9]+)"', response.get_data(as_text=True)).group(1)
    _, status = _wait_for_status(staff_client, job_id, "error")
    assert status == {"status": "error", "error": f"Generation failed. Job ID: {job_id}", "job_id": job_id}
    log = capsys.readouterr().out
    assert f"generation failed job_id={job_id}" in log and "Anthropic API timed out" in log

def test_invalid_collected_date_is_rejected_before_job_starts(staff_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    with patch.object(app.pipeline, "run") as run:
        response = staff_client.post(
            "/generate", data={"patient_name": "Test Patient", "collected_date": "2026-02-30"})
    assert response.status_code == 302
    run.assert_not_called()
    assert not (tmp_path / "jobs").exists()


def _lab_pdf_bytes(scanned):
    import fitz

    document = fitz.open()
    document.new_page().insert_text((40, 60), "Collected: 02/03/2026  TSH 1.9", fontsize=10)
    if scanned:
        source = fitz.open()
        source.new_page().insert_text((40, 60), "TSH 1.19", fontsize=10)
        document.new_page().insert_image(document[0].rect, pixmap=source[0].get_pixmap(dpi=72))
        source.close()
    data = document.tobytes()
    document.close()
    return data


def test_scanned_lab_pdf_requires_collected_date_before_job_starts(staff_client, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    with patch.object(app.pipeline, "run") as run:
        client = staff_client
        response = client.post("/generate", data={
            "patient_name": "Test Patient",
            "labs_pdf": (io.BytesIO(_lab_pdf_bytes(scanned=True)), "scanned-labs.pdf"),
        })
        assert response.status_code == 302
        assert "contains scanned pages" in client.get("/").get_data(as_text=True)
    run.assert_not_called()
    assert not list((tmp_path / "jobs").glob("*"))  # the saved upload was removed with its job folder


@pytest.mark.parametrize("scanned", [True, False])
def test_lab_pdf_with_date_or_without_scans_starts_job(staff_client, tmp_path, monkeypatch, scanned):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    pdf = _lab_pdf_bytes(scanned)
    data = {"patient_name": "Test Patient", "labs_pdf": (io.BytesIO(pdf), "labs.pdf")}
    if scanned:
        data["collected_date"] = "2026-04-14"
    received = []

    def fake_run(**kwargs):
        received.append(Path(kwargs["labs_pdf"]).read_bytes())
        (tmp_path / "review.txt").write_text("synthetic review", encoding="utf-8")
        return str(tmp_path / "review.txt")

    with patch.object(app.pipeline, "run", side_effect=fake_run):
        response = staff_client.post("/generate", data=data)
    assert response.status_code == 200
    job_id = re.search(r'data-job-id="([a-f0-9]+)"', response.get_data(as_text=True)).group(1)
    _wait_for_status(staff_client, job_id, "done")
    assert received == [pdf]
