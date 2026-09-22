import app


def test_version_reports_render_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "d326491fullsha")

    response = app.app.test_client().get("/version")

    assert response.status_code == 200
    assert response.get_json() == {"commit": "d326491fullsha"}
    assert response.headers["Cache-Control"] == "no-store"