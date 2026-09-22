import app
from unittest.mock import patch


def test_version_reports_render_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "d326491fullsha")

    response = app.app.test_client().get("/version")

    assert response.status_code == 200
    assert response.get_json() == {"commit": "d326491fullsha"}
    assert response.headers["Cache-Control"] == "no-store"


def test_generate_appends_all_vitality_index_defaults(tmp_path):
    captured = {}
    review_path = tmp_path / "review.txt"
    review_path.write_text("", encoding="utf-8")

    def fake_run(**kwargs):
        captured.update(kwargs)
        tmp_path.joinpath("report.pdf").write_bytes(b"")
        return str(review_path)

    with patch.object(app.pipeline, "run", side_effect=fake_run):
        response = app.app.test_client().post("/generate", data={"patient_name": "Test Patient"})

    assert response.status_code == 200
    for _, label in app.VITALITY_FIELDS:
        assert f"{label}: Not Assessed" in captured["note_text"]