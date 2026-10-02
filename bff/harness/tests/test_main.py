from unittest.mock import patch

from kubernetes.config import ConfigException


def test_load_kube_config_prefers_incluster() -> None:
    from harness.main import _load_kube_config

    with (
        patch("kubernetes.config.load_incluster_config") as incluster,
        patch("kubernetes.config.load_kube_config") as kubeconfig,
    ):
        _load_kube_config()

    incluster.assert_called_once()
    kubeconfig.assert_not_called()


def test_load_kube_config_falls_back_outside_cluster() -> None:
    """Without this fallback (and without _load_kube_config existing at all,
    which was the actual bug — see harness/main.py's docstring), every task
    using kubernetes.client.CustomObjectsApi() fails live with
    urllib3.exceptions.LocationValueError: No host specified, since the
    client has no cluster to talk to. Confirmed live: check_platform_health
    was the first CR-based task ever actually run as a real Job, and it hit
    exactly this."""
    from harness.main import _load_kube_config

    with (
        patch("kubernetes.config.load_incluster_config", side_effect=ConfigException()),
        patch("kubernetes.config.load_kube_config") as kubeconfig,
    ):
        _load_kube_config()

    kubeconfig.assert_called_once()


def test_main_loads_kube_config_before_running(monkeypatch) -> None:
    import pathlib

    import harness.main as main_module

    monkeypatch.setattr("sys.argv", ["main.py", "--scenario", "x.yaml", "--run-id", "run-1"])
    monkeypatch.setattr(pathlib.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(pathlib.Path, "write_text", lambda *a, **k: None)

    calls: list[str] = []
    monkeypatch.setattr(main_module, "_load_kube_config", lambda: calls.append("kube_config"))

    async def _fake_run(scenario_path: str, run_id: str):
        calls.append("run")

        class _Result:
            status = "PASS"

        return _Result()

    monkeypatch.setattr(main_module, "_run", _fake_run)
    monkeypatch.setattr(main_module.dataclasses, "asdict", lambda r: {"status": r.status})

    main_module.main()

    assert calls == ["kube_config", "run"]
