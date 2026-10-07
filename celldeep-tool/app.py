"""
CellDeep Report Generator — Web App
=======================================
A minimal Flask wrapper around pipeline.py.
"""

import hashlib
import hmac
import os
import json
import sys
import time
from datetime import timedelta
from pathlib import Path
import threading
import traceback
import shutil
import uuid

from flask import (Flask, abort, request, render_template, send_file, flash, redirect, url_for, jsonify,
                   session)
from werkzeug.middleware.proxy_fix import ProxyFix

import pipeline
import tmp_cleanup

STAFF_PASSWORD_ENV = "CELLDEEP_STAFF_PASSWORD"
SECRET_KEY_ENV = "CELLDEEP_SECRET_KEY"


def secret_key_from_environment() -> str | bytes:
    """The session-signing key, identical in every process so a login survives worker restarts,
    redeploys and multiple instances. CELLDEEP_SECRET_KEY (or the older FLASK_SECRET_KEY) wins; otherwise
    it is derived from the staff password, so rotating the password also ends every session. Without
    either, login is disabled anyway and a random key is used."""
    explicit = os.environ.get(SECRET_KEY_ENV) or os.environ.get("FLASK_SECRET_KEY")
    if explicit:
        return explicit
    password = os.environ.get(STAFF_PASSWORD_ENV)
    if password:
        return hmac.new(password.encode("utf-8"), b"celldeep-session-signing-key-v1", hashlib.sha256).hexdigest()
    return os.urandom(32)


app = Flask(__name__)
app.secret_key = secret_key_from_environment()
# Render terminates TLS and forwards plain HTTP with X-Forwarded-Proto/Host; trust exactly one proxy hop.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True,
                  SESSION_COOKIE_PATH="/", PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
JOBS_DIR = Path("/tmp/celldeep_jobs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)
_CLEANUP_INTERVAL_SECONDS = 15 * 60
_last_cleanup = 0.0


def _run_cleanup() -> None:
    global _last_cleanup
    _last_cleanup = time.time()
    try:
        removed = tmp_cleanup.cleanup(JOBS_DIR)
        if removed:
            print(f"tmp cleanup removed {removed} expired item(s)")
    except OSError as error:
        print(f"tmp cleanup failed: {type(error).__name__}")


def _cleanup_loop() -> None:
    while True:
        _run_cleanup()
        time.sleep(_CLEANUP_INTERVAL_SECONDS)


if "pytest" not in sys.modules:  # tests call tmp_cleanup directly
    threading.Thread(target=_cleanup_loop, daemon=True, name="celldeep-tmp-cleanup").start()


PUBLIC_ENDPOINTS = ("login", "static")


def is_staff_session() -> bool:
    """The one check every protected route uses (upload, status polling, downloads, note check...)."""
    return session.get("staff_authenticated") is True


@app.before_request
def require_staff_login():
    """Every page needs the shared staff password (CELLDEEP_STAFF_PASSWORD); nothing is public."""
    if time.time() - _last_cleanup > _CLEANUP_INTERVAL_SECONDS and "pytest" not in sys.modules:
        _run_cleanup()
    if request.endpoint in PUBLIC_ENDPOINTS or is_staff_session():
        return None
    if request.path.startswith(("/generate/status", "/note/check")):
        return jsonify({"status": "error", "error": "Staff login required"}), 401
    return redirect(url_for("login", next=request.path))


def _safe_next(target: str | None) -> str:
    return target if target and target.startswith("/") and not target.startswith("//") else url_for("index")


@app.route("/login", methods=["GET", "POST"])
def login():
    expected = os.environ.get(STAFF_PASSWORD_ENV)
    if not expected:
        return render_template("login.html", error="Staff login is not configured on this server.",
                               configured=False), 503
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            session.clear()
            session.permanent = True
            session["staff_authenticated"] = True
            return redirect(_safe_next(request.form.get("next")))
        return render_template("login.html", error="Incorrect password.", configured=True,
                               next=request.form.get("next", "")), 401
    return render_template("login.html", configured=True, next=request.args.get("next", ""))


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))

VITALITY_FIELDS = (
    ("energy", "Energy"),
    ("sleep", "Sleep"),
    ("mental_clarity_focus", "Mental Clarity & Focus"),
    ("mood_emotional_balance", "Mood & Emotional Balance"),
    ("cravings", "Cravings"),
    ("sexual_desire", "Sexual Desire"),
    ("sexual_function", "Sexual Function"),
)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", form={})


@app.after_request
def never_cache_pages(response):
    """Pages hold patient entries and per-upload messages: the browser must never show a stored copy
    (Back/Forward or cache), which could pair an old message with newly entered fields."""
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response


SCANNED_WITHOUT_DATE = ("This bloodwork PDF contains scanned pages, and no Bloodwork Collected Date was received "
                        "with this upload. Enter the Bloodwork Collected Date and attach the PDF files again.")


def _form_rejected(message: str, clear_date: bool = False):
    """Show the upload form again with the message and everything staff entered (files cannot be kept)."""
    form = request.form.to_dict()
    if clear_date:
        form["collected_date"] = ""
    flash(message)
    return render_template("index.html", form=form), 422


NOTE_TEMPLATE = Path(__file__).resolve().parent / "templates" / "provider_notes_template.md"


@app.route("/provider-note-template", methods=["GET"])
def provider_note_template():
    return send_file(NOTE_TEMPLATE, as_attachment=True, mimetype="text/markdown",
                     download_name="celldeep_provider_note_template.md")


@app.route("/note/check", methods=["POST"])
def check_note():
    """Which lines of a pasted provider note will be read, and why any others will not."""
    return jsonify(pipeline.check_provider_note(request.form.get("note_text", "")))


@app.route("/version", methods=["GET"])
def version():
    response = jsonify({"commit": os.environ.get("RENDER_GIT_COMMIT", "unknown")})
    response.headers["Cache-Control"] = "no-store"
    return response


def _job_directory(job_id: str) -> Path | None:
    try:
        parsed_job_id = uuid.UUID(hex=job_id)
    except ValueError:
        return None
    if parsed_job_id.hex != job_id:
        return None
    return JOBS_DIR / job_id


# A running job touches its heartbeat file every HEARTBEAT_SECONDS. A job still "processing" whose
# heartbeat is older than STALE_JOB_SECONDS was killed with its process (out of memory, restart, redeploy).
HEARTBEAT_SECONDS = 10
STALE_JOB_SECONDS = 120
JOB_INTERRUPTED_MESSAGE = "This report job was interrupted. Please try again"
# One report at a time per process: each job renders scanned pages and starts Chromium, and two at once
# do not fit a 512MB instance. Later jobs wait their turn (status stays "processing").
_REPORT_SLOT = threading.Lock()
# A job whose documents left pages or rows out pauses for staff before the report is built. A decision
# not made within this time stops the job (nothing is written).
CONFIRM_TIMEOUT_SECONDS = 30 * 60
STOPPED_MESSAGE = "Report not generated: stopped at the confirmation step. Nothing was written."
_DECISIONS: dict[str, dict] = {}  # job id -> {"event": threading.Event, "go": bool}, while a job waits
# Job folder contents kept after a job ends; uploads and the intermediate HTML are deleted.
_JOB_OUTPUTS = {"report.pdf", "review_notes.txt", "status.json", "status.tmp", "heartbeat"}


def _write_job_status(job_directory: Path, status: str, **details) -> None:
    payload = {"status": status, **details}
    temporary_path = job_directory / "status.tmp"
    temporary_path.write_text(json.dumps(payload), encoding="utf-8")
    temporary_path.replace(job_directory / "status.json")


def _read_job_status(job_directory: Path) -> dict:
    status_path = job_directory / "status.json"
    if not status_path.exists():
        return {"status": "processing"}
    return json.loads(status_path.read_text(encoding="utf-8"))


def _touch_heartbeat(job_directory: Path) -> None:
    (job_directory / "heartbeat").touch()


def _heartbeat_loop(job_directory: Path, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            _touch_heartbeat(job_directory)
        except OSError:
            return


def _job_is_stale(job_directory: Path) -> bool:
    beats = [path.stat().st_mtime for path in (job_directory / "heartbeat", job_directory / "status.json")
             if path.exists()]
    return time.time() - max(beats, default=job_directory.stat().st_mtime) > STALE_JOB_SECONDS


def _delete_job_inputs(job_directory: Path) -> None:
    """Uploaded PDFs and the intermediate report HTML are not needed once the job has ended."""
    if not job_directory.is_dir():
        return
    for path in job_directory.iterdir():
        if path.name in _JOB_OUTPUTS:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _run_report_job(job_directory: Path, job_data: dict, run=None) -> None:
    stop_heartbeat = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(job_directory, stop_heartbeat), daemon=True,
                     name=f"celldeep-heartbeat-{job_directory.name}").start()
    try:
        with _REPORT_SLOT:
            _run_report(job_directory, job_data, run or pipeline.run)
    finally:
        stop_heartbeat.set()


def _confirm_with_staff(job_directory: Path, items: list[str]) -> bool:
    """Pause the job and show staff what was left out; True only when staff choose to continue. The
    one-report slot is released while waiting so other uploads are not held up."""
    decision = {"event": threading.Event(), "go": False}
    _DECISIONS[job_directory.name] = decision
    _write_job_status(job_directory, "confirm", items=items)
    _REPORT_SLOT.release()
    try:
        decided = decision["event"].wait(CONFIRM_TIMEOUT_SECONDS)
    finally:
        _REPORT_SLOT.acquire()
        _DECISIONS.pop(job_directory.name, None)
    if decided and decision["go"]:
        _write_job_status(job_directory, "processing")
        return True
    return False


def _run_report(job_directory: Path, job_data: dict, run) -> None:
    try:
        review_path = run(
            labs_pdf=job_data["labs_path"],
            dexa_pdfs=job_data["dexa_paths"],
            note_text=job_data["note_text"],
            patient_name=job_data["patient_name"],
            age=job_data["age"],
            sex=job_data["sex"],
            out_path=str(job_directory / "report.pdf"),
            vitality_index=job_data["vitality_index"],
            collected_date=job_data.get("collected_date"),
            confirm=lambda items: _confirm_with_staff(job_directory, items),
        )
        shutil.move(review_path, job_directory / "review_notes.txt")
        _write_job_status(job_directory, "done")
    except pipeline.GenerationAborted:
        _write_job_status(job_directory, "stopped", error=STOPPED_MESSAGE)
        print(f"generation stopped at confirmation job_id={job_directory.name}")
    except pipeline.LatestDrawNotAccepted as blocked:
        # The message names draw dates: it goes to the staff page only, never to the log.
        _write_job_status(job_directory, "stopped", error=str(blocked))
        print(f"generation blocked: latest draw has no accepted result job_id={job_directory.name}")
    except Exception:
        job_id = job_directory.name
        # Log first, then publish the status: whoever sees "error" can already find the traceback.
        print(f"generation failed job_id={job_id}")
        print(traceback.format_exc())
        _write_job_status(job_directory, "error", error=f"Generation failed. Job ID: {job_id}", job_id=job_id)
    finally:
        _delete_job_inputs(job_directory)


@app.route("/generate", methods=["POST"])
def generate():
    try:
        patient_name = request.form.get("patient_name", "").strip()
        age = request.form.get("age", "").strip()
        sex = request.form.get("sex", "").strip() or None
        note_text = request.form.get("note_text", "").strip() or None
        collected_date = request.form.get("collected_date", "").strip() or None
        vitality_index = {
            label: request.form.get(f"vitality_{field}", "Not Assessed")
            for field, label in VITALITY_FIELDS
        }

        if not patient_name:
            return _form_rejected("Patient name is required.")

        age = int(age) if age else None
        if collected_date:
            try:
                collected_date = pipeline.scan_collected_date(collected_date)
            except ValueError as error:
                return _form_rejected(str(error), clear_date=True)

        job_id = uuid.uuid4().hex
        job_directory = _job_directory(job_id)
        job_directory.mkdir(parents=True, exist_ok=False)

        # Uploads stream straight to the job folder; the PDF is never also held in memory as bytes.
        labs_file = request.files.get("labs_pdf")
        labs_path = None
        if labs_file and labs_file.filename:
            labs_path = job_directory / "labs.pdf"
            labs_file.save(labs_path)
            if not collected_date and pipeline.has_scanned_pages(labs_path):
                shutil.rmtree(job_directory, ignore_errors=True)
                # Field and file-input names only (no values, no file names): shows what the server received.
                files = sorted({key for key, upload in request.files.items(multi=True) if upload.filename})
                print(f"upload rejected: scanned lab pages and no collected_date received; "
                      f"form_fields={sorted(request.form)} file_fields={files} content_length={request.content_length}")
                return _form_rejected(SCANNED_WITHOUT_DATE)

        dexa_paths = []
        for index, dexa_file in enumerate(request.files.getlist("dexa_pdfs")):
            if dexa_file and dexa_file.filename:
                dexa_path = job_directory / f"dexa_{index}.pdf"
                dexa_file.save(dexa_path)
                dexa_paths.append(str(dexa_path))

        job_data = {
            "labs_path": str(labs_path) if labs_path else None,
            "dexa_paths": dexa_paths,
            "note_text": note_text,
            "patient_name": patient_name,
            "age": age,
            "sex": sex,
            "vitality_index": vitality_index,
            "collected_date": collected_date,
        }
        _write_job_status(job_directory, "processing")
        _touch_heartbeat(job_directory)
        threading.Thread(
            target=_run_report_job,
            args=(job_directory, job_data, pipeline.run),  # bound now: the job may wait for its turn
            daemon=True,
            name=f"celldeep-report-{job_id}",
        ).start()
        return render_template("generating.html", job_id=job_id, interrupted_message=JOB_INTERRUPTED_MESSAGE)

    except Exception:
        job_id = uuid.uuid4().hex
        flash(f"Generation failed. Job ID: {job_id}")
        print(f"generation failed job_id={job_id}")
        print(traceback.format_exc())
        return redirect(url_for("index"))


@app.route("/generate/status/<job_id>", methods=["GET"])
def generate_status(job_id):
    job_directory = _job_directory(job_id)
    interrupted = {"status": "interrupted", "error": JOB_INTERRUPTED_MESSAGE}
    if job_directory is None or not job_directory.is_dir():
        # The job's files are gone: the instance was replaced or restarted while it ran.
        return jsonify(interrupted), 404
    status = _read_job_status(job_directory)
    if status["status"] in ("processing", "confirm") and _job_is_stale(job_directory):
        return jsonify(interrupted)
    if status["status"] == "done":
        status["report_url"] = url_for("download_report", job_id=job_id)
        status["review_notes_url"] = url_for("download_review_notes", job_id=job_id)
    return jsonify(status)


@app.route("/generate/decision/<job_id>", methods=["POST"])
def generate_decision(job_id):
    """Staff answer the confirmation step: continue to build the report, or stop."""
    decision = _DECISIONS.get(job_id) if _job_directory(job_id) else None
    if decision is None:
        return jsonify({"status": "error", "error": "This job is not waiting for a decision."}), 409
    decision["go"] = request.form.get("action") == "continue"
    decision["event"].set()
    return jsonify({"status": "processing" if decision["go"] else "stopping"})


@app.route("/download/<job_id>/report", methods=["GET"])
def download_report(job_id):
    job_directory = _job_directory(job_id)
    path = job_directory / "report.pdf" if job_directory else None
    if path is None or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name="patient_report.pdf", mimetype="application/pdf")


@app.route("/download/<job_id>/review-notes", methods=["GET"])
def download_review_notes(job_id):
    job_directory = _job_directory(job_id)
    path = job_directory / "review_notes.txt" if job_directory else None
    if path is None or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name="internal_qa_review_notes.txt", mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
