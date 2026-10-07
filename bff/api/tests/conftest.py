import pytest


@pytest.fixture(autouse=True)
def _auth_off(monkeypatch) -> None:
    """Route tests exercise the routes themselves; the access gate has its own
    tests in test_auth.py, which turn it back on."""
    monkeypatch.setenv("MAASPAL_AUTH_MODE", "off")


@pytest.fixture(autouse=True)
def _custom_scenarios_dir(tmp_path, monkeypatch) -> None:
    """Imported scenarios go to a scratch dir, never a real /data."""
    monkeypatch.setenv("CUSTOM_SCENARIOS_DIR", str(tmp_path / "imported-scenarios"))
