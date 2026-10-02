import argparse
import asyncio
import dataclasses
import json
import signal
from pathlib import Path

from harness.runner import ScenarioRunner


def _load_kube_config() -> None:
    """Configure the kubernetes client's global default connection, in-cluster
    first with a local-kubeconfig fallback — the same pattern api/k8s.py's
    _kube() and api/maas_client.py already use for the API server process.

    Without this, every task that calls k8s_client.CustomObjectsApi() (all of
    subscription.py, access_policy.py, platform_health.py) crashes with
    urllib3.exceptions.LocationValueError: No host specified — the client has
    no idea what cluster to talk to. This was a real, previously-undiscovered
    gap: the API server process always loaded this for itself, but nothing
    equivalent ever existed for the harness Job process, so it went unnoticed
    until a CR-based task actually ran live for the first time (confirmed by
    ADR-009's own note that its corrected write path was "not yet verified
    against a real cluster" — this is why).
    """
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()


async def _run(scenario_path: str, run_id: str):
    # api/k8s.py's stop_run() asks the API server to delete this Job's pod with a
    # generous grace period, which delivers SIGTERM here first (then SIGKILL if we
    # overrun it). Without a handler, the default disposition just kills the
    # process outright and skips ScenarioRunner's cleanup (revoking provisioned
    # MaaS API keys, restoring MaaSSubscription CRs) entirely. Setting an
    # asyncio.Event here instead lets the runner notice between/within tasks and
    # fall through to its normal cleanup loop before exiting.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    return await ScenarioRunner(scenario_path, run_id, stop_event=stop_event).run()


def main() -> None:
    parser = argparse.ArgumentParser(description="MaaS:PAL scenario runner")
    parser.add_argument("--scenario", required=True, help="Path to scenario YAML")
    parser.add_argument("--run-id", required=True, dest="run_id", help="Unique run identifier")
    args = parser.parse_args()

    _load_kube_config()
    result = asyncio.run(_run(args.scenario, args.run_id))

    output_path = Path(f"/data/results/{args.run_id}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dataclasses.asdict(result), indent=2))

    print(f"[harness] result written to {output_path}", flush=True)
    print(f"[harness] status: {result.status}", flush=True)


if __name__ == "__main__":
    main()
