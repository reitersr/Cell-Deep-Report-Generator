"""The staff session must reach every protected request: the upload POST, status polling and
downloads, including after the server process restarts. Synthetic data only; no network."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import app
from conftest import STAFF_TEST_PASSWORD

HTTPS = "https://celldeep.example"  # Render terminates TLS; the browser talks HTTPS


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv(app.STAFF_PASSWORD_ENV, STAFF_TEST_PASSWORD)
    monkeypatch.setattr(app, "JOBS_DIR", tmp_path / "jobs")
    return app.app.test_client()


def _fake_run(tmp_path):
    def run(**kwargs):
        Path(kwargs["out_path"]).write_bytes(b"synthetic report PDF")
        review = tmp_path / "review.txt"
        review.write_text("synthetic review", encoding="utf-8")
        return str(review)
    return run


def _status(client, job_id, wanted):
    for _ in range(100):
        response = client.get(f"/generate/status/{job_id}", base_url=HTTPS)
        if response.status_code != 200 or response.get_json()["status"] == wanted:
            return response
        time.sleep(0.02)
    return response


def test_login_then_upload_status_and_downloads_share_one_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    login = client.post("/login", data={"password": STAFF_TEST_PASSWORD, "next": "/"}, base_url=HTTPS)
    assert login.status_code == 302
    cookie = login.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert client.get("/", base_url=HTTPS).status_code == 200
    with patch.object(app.pipeline, "run", side_effect=_fake_run(tmp_path)):
        generate = client.post("/generate", data={"patient_name": "Synthetic, Pat"}, base_url=HTTPS)
        assert generate.status_code == 200
        job_id = re.search(r'data-job-id="([a-f0-9]+)"', generate.get_data(as_text=True)).group(1)
        status = _status(client, job_id, "done")
    assert status.status_code == 200 and status.get_json()["status"] == "done"
    body = status.get_json()
    assert client.get(body["report_url"], base_url=HTTPS).data == b"synthetic report PDF"
    assert client.get(body["review_notes_url"], base_url=HTTPS).status_code == 200


def test_without_login_upload_status_and_downloads_are_refused(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    job_id = "a" * 32
    (tmp_path / "jobs" / job_id).mkdir(parents=True)
    (tmp_path / "jobs" / job_id / "report.pdf").write_bytes(b"synthetic report PDF")
    generate = client.post("/generate", data={"patient_name": "Synthetic, Pat"}, base_url=HTTPS)
    assert generate.status_code == 302 and "/login" in generate.headers["Location"]
    assert client.get(f"/generate/status/{job_id}", base_url=HTTPS).status_code == 401
    for path in (f"/download/{job_id}/report", f"/download/{job_id}/review-notes"):
        response = client.get(path, base_url=HTTPS)
        assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_every_protected_route_uses_the_one_session_check():
    public = {"login", "static"}
    protected = {rule.endpoint for rule in app.app.url_map.iter_rules()} - public
    assert {"index", "generate", "generate_status", "download_report", "download_review_notes",
            "check_note", "provider_note_template", "version", "logout"} <= protected
    with app.app.test_request_context("/"):
        assert app.is_staff_session() is False


_CHILD = """
import json, sys
sys.path.insert(0, {root!r})
import app
client = app.app.test_client()
base = "https://celldeep.example"
if {cookie!r} is None:
    response = client.post("/login", data={{"password": {password!r}, "next": "/"}}, base_url=base)
    print(json.dumps(response.headers["Set-Cookie"].split(";", 1)[0]))
else:
    name, value = {cookie!r}.split("=", 1)
    client.set_cookie(name, value, domain="celldeep.example", secure=True)
    print(json.dumps(client.get("/generate/status/" + "a" * 32, base_url=base).status_code))
"""


def _in_new_process(env, cookie=None):
    child_env = {key: value for key, value in os.environ.items()
                 if key not in ("CELLDEEP_SECRET_KEY", "FLASK_SECRET_KEY", app.STAFF_PASSWORD_ENV)}
    child_env.update(env)
    code = _CHILD.format(root=str(Path(app.__file__).parent), cookie=cookie, password=STAFF_TEST_PASSWORD)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=child_env,
                            cwd=Path(app.__file__).parent, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("env", [
    {app.STAFF_PASSWORD_ENV: STAFF_TEST_PASSWORD},  # no secret configured: derived from the password
    {app.STAFF_PASSWORD_ENV: STAFF_TEST_PASSWORD, "CELLDEEP_SECRET_KEY": "synthetic-secret-key-value"},
])
def test_session_survives_a_server_restart(env):
    cookie = _in_new_process(env)
    # A fresh process (worker restart, redeploy, second instance) must accept the same cookie.
    assert _in_new_process(env, cookie) == 404  # authenticated: unknown job id, not 401


def test_changing_the_staff_password_ends_derived_sessions():
    cookie = _in_new_process({app.STAFF_PASSWORD_ENV: STAFF_TEST_PASSWORD})
    assert _in_new_process({app.STAFF_PASSWORD_ENV: STAFF_TEST_PASSWORD + "-rotated"}, cookie) == 401


def test_secret_key_precedence(monkeypatch):
    monkeypatch.delenv("CELLDEEP_SECRET_KEY", raising=False)
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv(app.STAFF_PASSWORD_ENV, STAFF_TEST_PASSWORD)
    derived = app.secret_key_from_environment()
    assert derived == app.secret_key_from_environment() and STAFF_TEST_PASSWORD not in derived
    monkeypatch.setenv("FLASK_SECRET_KEY", "synthetic-flask-key")
    assert app.secret_key_from_environment() == "synthetic-flask-key"
    monkeypatch.setenv("CELLDEEP_SECRET_KEY", "synthetic-celldeep-key")
    assert app.secret_key_from_environment() == "synthetic-celldeep-key"


def test_https_is_recognized_behind_the_proxy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/login", data={"password": STAFF_TEST_PASSWORD, "next": "/"},
                           headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 302
    environ = {}
    def capture(env, start_response):
        environ.update(env)
        start_response("200 OK", [])
        return [b""]
    proxied = app.ProxyFix(capture, x_proto=1, x_host=1)
    list(proxied({"wsgi.url_scheme": "http", "HTTP_X_FORWARDED_PROTO": "https", "REQUEST_METHOD": "GET",
                  "PATH_INFO": "/", "SERVER_NAME": "x", "SERVER_PORT": "80", "HTTP_HOST": "x"}, lambda *a: None))
    assert environ["wsgi.url_scheme"] == "https"
    assert isinstance(app.app.wsgi_app, app.ProxyFix)
