"""Staff login on every page, six-hour /tmp cleanup, and a plain failure message."""

import os
import re
import time
from pathlib import Path

import pytest

import app
import tmp_cleanup
from conftest import STAFF_TEST_PASSWORD


@pytest.fixture
def anonymous(monkeypatch):
    monkeypatch.setenv(app.STAFF_PASSWORD_ENV, STAFF_TEST_PASSWORD)
    monkeypatch.setitem(app.app.config, "SESSION_COOKIE_SECURE", False)
    return app.app.test_client()


@pytest.mark.parametrize("path", ["/", "/version", "/provider-note-template", "/download/" + "a" * 32 + "/report"])
def test_every_page_requires_staff_login(anonymous, path):
    response = anonymous.get(path)
    assert response.status_code == 302 and "/login" in response.headers["Location"]


@pytest.mark.parametrize("path", ["/generate/status/" + "a" * 32, "/note/check"])
def test_api_endpoints_answer_401_without_login(anonymous, path):
    response = anonymous.post(path) if path == "/note/check" else anonymous.get(path)
    assert response.status_code == 401


def test_generate_is_refused_without_login(anonymous, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    response = anonymous.post("/generate", data={"patient_name": "Synthetic, Pat"})
    assert response.status_code == 302 and not (tmp_path / "jobs").exists()


def test_correct_password_signs_in_and_logout_signs_out(anonymous, capsys):
    response = anonymous.post("/login", data={"password": STAFF_TEST_PASSWORD, "next": "/"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/")
    assert anonymous.get("/").status_code == 200
    anonymous.get("/logout")
    assert anonymous.get("/").status_code == 302
    assert STAFF_TEST_PASSWORD not in capsys.readouterr().out


def test_wrong_password_is_refused_without_echoing_it(anonymous):
    response = anonymous.post("/login", data={"password": "wrong-synthetic-guess"})
    assert response.status_code == 401
    page = response.get_data(as_text=True)
    assert "Incorrect password." in page and "wrong-synthetic-guess" not in page
    assert anonymous.get("/").status_code == 302


def test_login_never_redirects_off_site(anonymous):
    response = anonymous.post("/login", data={"password": STAFF_TEST_PASSWORD, "next": "//evil.example/"})
    assert response.headers["Location"].endswith("/") and "evil" not in response.headers["Location"]


def test_unconfigured_password_locks_the_site(monkeypatch):
    monkeypatch.delenv(app.STAFF_PASSWORD_ENV, raising=False)
    client = app.app.test_client()
    assert client.get("/").status_code == 302
    response = client.get("/login")
    assert response.status_code == 503 and "not configured" in response.get_data(as_text=True)
    assert client.post("/login", data={"password": ""}).status_code == 503


def test_password_is_not_in_the_code():
    for path in Path(app.__file__).parent.glob("*.py"):
        if path.name not in ("conftest.py", "test_operations.py"):
            assert STAFF_TEST_PASSWORD not in path.read_text(encoding="utf-8")


def _touch(path, age_hours, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.suffix:
        path.mkdir(exist_ok=True)
        (path / "status.json").write_text("{}")
        target = [path / "status.json", path]
    else:
        path.write_text("x")
        target = [path]
    for item in target:
        os.utime(item, (now - age_hours * 3600, now - age_hours * 3600))


def test_cleanup_removes_only_expired_app_files(tmp_path):
    now = time.time()
    jobs = tmp_path / "celldeep_jobs"
    _touch(jobs / "old_job", 7, now)
    _touch(jobs / "new_job", 1, now)
    _touch(tmp_path / "celldeep_extraction_audits" / "extraction_old", 8, now)
    _touch(tmp_path / "celldeep_review_notes_old.txt", 6.5, now)
    _touch(tmp_path / "celldeep_review_notes_new.txt", 5, now)
    _touch(tmp_path / "pre_dedup_markers_raw.json", 9, now)
    _touch(tmp_path / "someone_elses_file.txt", 48, now)
    removed = tmp_cleanup.cleanup(jobs, tmp_root=tmp_path, now=now)
    assert removed == 4
    assert not (jobs / "old_job").exists() and (jobs / "new_job").exists()
    assert not (tmp_path / "celldeep_extraction_audits" / "extraction_old").exists()
    assert not (tmp_path / "celldeep_review_notes_old.txt").exists()
    assert (tmp_path / "celldeep_review_notes_new.txt").exists()
    assert not (tmp_path / "pre_dedup_markers_raw.json").exists()
    assert (tmp_path / "someone_elses_file.txt").exists()
    assert jobs.is_dir() and (tmp_path / "celldeep_extraction_audits").is_dir()


def test_cleanup_keeps_a_job_that_is_still_writing(tmp_path):
    now = time.time()
    jobs = tmp_path / "celldeep_jobs"
    _touch(jobs / "running", 10, now)
    (jobs / "running" / "status.json").write_text("{}")  # fresh write inside an old folder
    assert tmp_cleanup.cleanup(jobs, tmp_root=tmp_path, now=time.time()) == 0
    assert (jobs / "running").exists()


def test_failure_page_shows_a_plain_message_with_the_job_id():
    template = (Path(app.__file__).parent / "templates" / "generating.html").read_text(encoding="utf-8")
    assert "job.error" in template
    assert re.search(r"Generation failed\. Job ID: \{job_id\}", Path(app.__file__).read_text(encoding="utf-8"))
