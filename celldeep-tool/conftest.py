"""Shared test fixtures. Synthetic data only."""

import pytest

STAFF_TEST_PASSWORD = "synthetic-test-password"


@pytest.fixture
def staff_client(monkeypatch):
    """A Flask test client already signed in with the shared staff password."""
    import app

    monkeypatch.setenv(app.STAFF_PASSWORD_ENV, STAFF_TEST_PASSWORD)
    monkeypatch.setitem(app.app.config, "SESSION_COOKIE_SECURE", False)  # the test client speaks plain HTTP
    client = app.app.test_client()
    with client.session_transaction() as session:
        session["staff_authenticated"] = True
    return client


@pytest.fixture(autouse=True)
def no_vision_fallback_unless_asked(monkeypatch):
    """Tests run offline: the unknown-layout AI fallback is off unless a test turns it on with a scripted model."""
    monkeypatch.setenv("CELLDEEP_ALLOW_VISION_FALLBACK", "0")
