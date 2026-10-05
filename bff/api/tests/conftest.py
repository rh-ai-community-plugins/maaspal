import pytest


@pytest.fixture(autouse=True)
def _auth_off(monkeypatch) -> None:
    """Route tests exercise the routes themselves; the access gate has its own
    tests in test_auth.py, which turn it back on."""
    monkeypatch.setenv("MAASPAL_AUTH_MODE", "off")
