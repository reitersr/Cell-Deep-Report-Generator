"""
CellDeep Report Generator — Web App
=======================================
A minimal Flask wrapper around pipeline.py.
"""

import os
import json
from pathlib import Path
import threading
import traceback
import shutil
import uuid

from flask import Flask, abort, request, render_template, send_file, flash, redirect, url_for, jsonify

import pipeline

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "celldeep-dev-secret-change-in-production")
JOBS_DIR = Path("/tmp/celldeep_jobs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)

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
    return render_template("index.html")


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


def _run_report_job(job_directory: Path, job_data: dict) -> None:
    try:
        review_path = pipeline.run(
            labs_pdf=job_data["labs_path"],
            dexa_pdfs=job_data["dexa_paths"],
            note_text=job_data["note_text"],
            patient_name=job_data["patient_name"],
            age=job_data["age"],
            sex=job_data["sex"],
            out_path=str(job_directory / "report.pdf"),
            vitality_index=job_data["vitality_index"],
        )
        shutil.copyfile(review_path, job_directory / "review_notes.txt")
        _write_job_status(job_directory, "done")
    except Exception as error:
        _write_job_status(job_directory, "error", error=str(error))
        print(traceback.format_exc())


@app.route("/generate", methods=["POST"])
def generate():
    try:
        patient_name = request.form.get("patient_name", "").strip()
        age = request.form.get("age", "").strip()
        sex = request.form.get("sex", "").strip() or None
        note_text = request.form.get("note_text", "").strip() or None
        vitality_index = {
            label: request.form.get(f"vitality_{field}", "Not Assessed")
            for field, label in VITALITY_FIELDS
        }

        if not patient_name:
            flash("Patient name is required.")
            return redirect(url_for("index"))

        age = int(age) if age else None

        job_id = uuid.uuid4().hex
        job_directory = _job_directory(job_id)
        job_directory.mkdir(parents=True, exist_ok=False)

        labs_path = None
        labs_file = request.files.get("labs_pdf")
        if labs_file and labs_file.filename:
            labs_path = job_directory / "labs.pdf"
            labs_file.save(labs_path)

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
        }
        _write_job_status(job_directory, "processing")
        threading.Thread(
            target=_run_report_job,
            args=(job_directory, job_data),
            daemon=True,
            name=f"celldeep-report-{job_id}",
        ).start()
        return render_template("generating.html", job_id=job_id)

    except Exception as e:
        error_detail = traceback.format_exc()
        flash(f"Something went wrong generating this report: {str(e)}")
        print(error_detail)
        return redirect(url_for("index"))


@app.route("/generate/status/<job_id>", methods=["GET"])
def generate_status(job_id):
    job_directory = _job_directory(job_id)
    if job_directory is None or not job_directory.is_dir():
        abort(404)
    status = _read_job_status(job_directory)
    if status["status"] == "done":
        status["report_url"] = url_for("download_report", job_id=job_id)
        status["review_notes_url"] = url_for("download_review_notes", job_id=job_id)
    return jsonify(status)


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
