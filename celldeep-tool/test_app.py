import app
import io
import pipeline
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch


def test_version_reports_render_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "d326491fullsha")

    response = app.app.test_client().get("/version")

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


def test_generate_runs_as_disk_backed_background_job(tmp_path, monkeypatch):
    captured = {}
    worker_started = threading.Event()
    release_worker = threading.Event()
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")

    def fake_run(**kwargs):
        captured.update(kwargs)
        worker_started.set()
        assert release_worker.wait(timeout=2)
        Path(kwargs["out_path"]).write_bytes(b"synthetic report PDF")
        review_path = tmp_path / "review.txt"
        review_path.write_text("synthetic review", encoding="utf-8")
        return str(review_path)

    with patch.object(app.pipeline, "run", side_effect=fake_run):
        response = app.app.test_client().post(
            "/generate",
            data={
                "patient_name": "Test Patient",
                "note_text": "Original note.",
                "labs_pdf": (io.BytesIO(b"synthetic input PDF"), "synthetic-labs.pdf"),
            },
        )

    assert response.status_code == 200
    job_id = re.search(r'data-job-id="([a-f0-9]+)"', response.get_data(as_text=True)).group(1)
    assert worker_started.wait(timeout=2)
    processing_response = app.app.test_client().get(f"/generate/status/{job_id}")
    assert processing_response.get_json() == {"status": "processing"}
    release_worker.set()

    status_response, status = _wait_for_status(app.app.test_client(), job_id, "done")
    assert status_response.status_code == 200
    assert status["report_url"] == f"/download/{job_id}/report"
    assert status["review_notes_url"] == f"/download/{job_id}/review-notes"
    assert app.app.test_client().get(status["report_url"]).data == b"synthetic report PDF"
    assert captured["note_text"] == "Original note."
    assert captured["vitality_index"] == {
        label: "Not Assessed" for _, label in app.VITALITY_FIELDS
    }
    assert Path(captured["labs_pdf"]).read_bytes() == b"synthetic input PDF"


def test_generate_status_surfaces_background_pipeline_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")

    def fake_run(**_kwargs):
        raise pipeline.AnthropicAPIError("Anthropic API timed out during extraction. Please retry the report.")

    with patch.object(app.pipeline, "run", side_effect=fake_run):
        response = app.app.test_client().post("/generate", data={"patient_name": "Test Patient"})

    job_id = re.search(r'data-job-id="([a-f0-9]+)"', response.get_data(as_text=True)).group(1)
    _, status = _wait_for_status(app.app.test_client(), job_id, "error")
    assert status == {
        "status": "error",
        "error": "Anthropic API timed out during extraction. Please retry the report.",
    }