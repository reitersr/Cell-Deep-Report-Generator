"""
CellDeep Report Generator — Web App
=======================================
A minimal Flask wrapper around pipeline.py.
"""

import os
import tempfile
import traceback

from flask import Flask, request, render_template, send_file, flash, redirect, url_for

import pipeline

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "celldeep-dev-secret-change-in-production")


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    try:
        patient_name = request.form.get("patient_name", "").strip()
        age = request.form.get("age", "").strip()
        sex = request.form.get("sex", "").strip() or None
        note_text = request.form.get("note_text", "").strip() or None

        if not patient_name:
            flash("Patient name is required.")
            return redirect(url_for("index"))

        age = int(age) if age else None

        with tempfile.TemporaryDirectory() as tmpdir:
            labs_path = None
            labs_file = request.files.get("labs_pdf")
            if labs_file and labs_file.filename:
                labs_path = os.path.join(tmpdir, "labs.pdf")
                labs_file.save(labs_path)

            dexa_paths = []
            for i, f in enumerate(request.files.getlist("dexa_pdfs")):
                if f and f.filename:
                    p = os.path.join(tmpdir, f"dexa_{i}.pdf")
                    f.save(p)
                    dexa_paths.append(p)

            out_path = os.path.join(tmpdir, "report.pdf")

            pipeline.run(
                labs_pdf=labs_path,
                dexa_pdfs=dexa_paths,
                note_text=note_text,
                patient_name=patient_name,
                age=age,
                sex=sex,
                out_path=out_path,
            )

            download_name = f"{patient_name.replace(' ', '_')}_report.pdf"
            return send_file(out_path, as_attachment=True, download_name=download_name,
                              mimetype="application/pdf")

    except Exception as e:
        error_detail = traceback.format_exc()
        flash(f"Something went wrong generating this report: {str(e)}")
        print(error_detail)
        return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
