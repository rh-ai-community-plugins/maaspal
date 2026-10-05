"""The PVC files a "Send from user browser" step (send_requests `from_browser`)
is handed over with — shared by the harness (writes the order, reads the
records) and the BFF (api/routes/browser.py: hands the order to the open run
page, appends what the browser reports). Same image, like cleanup_state.py.

Per step, in the results directory:
  <run>-browser-<result_key>.order.json   what to send — targets include the
                                           run's own API keys, so it's deleted
                                           the moment the step ends
  <run>-browser-<result_key>.claim.json   which browser took it (created with
                                           O_EXCL: exactly one tab sends)
  <run>-browser-<result_key>.jsonl        one JSON record per finished request,
                                           then {"done": true, "reason": …}
"""

import json
import os
import re
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_MODE = 0o640


def _base(results_dir: Path, run_id: str, result_key: str) -> str:
    return str(results_dir / f"{_SAFE.sub('_', run_id)}-browser-{_SAFE.sub('_', result_key)}")


def order_path(results_dir: Path, run_id: str, result_key: str) -> Path:
    return Path(_base(results_dir, run_id, result_key) + ".order.json")


def claim_path(results_dir: Path, run_id: str, result_key: str) -> Path:
    return Path(_base(results_dir, run_id, result_key) + ".claim.json")


def records_path(results_dir: Path, run_id: str, result_key: str) -> Path:
    return Path(_base(results_dir, run_id, result_key) + ".jsonl")


def write_order(results_dir: Path, run_id: str, result_key: str, order: dict) -> None:
    """Atomically publish an order (fresh claim and records files)."""
    results_dir.mkdir(parents=True, exist_ok=True)
    for stale in (claim_path(results_dir, run_id, result_key), records_path(results_dir, run_id, result_key)):
        stale.unlink(missing_ok=True)
    path = order_path(results_dir, run_id, result_key)
    tmp = path.with_suffix(".tmp")
    # Not world-readable: it carries API keys. Group-readable, since the BFF
    # pod reading it may run as a different UID (OpenShift; both are GID 0).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(order, f)
    os.replace(tmp, path)


def remove_order(results_dir: Path, run_id: str, result_key: str) -> None:
    """The step is over: no browser may pick it up (or report into it) again.
    The records file stays — it holds no secrets."""
    order_path(results_dir, run_id, result_key).unlink(missing_ok=True)
    claim_path(results_dir, run_id, result_key).unlink(missing_ok=True)


def find_order(results_dir: Path, run_id: str) -> tuple[str, dict] | None:
    """The run's open order, if any: (result_key, order)."""
    prefix = f"{_SAFE.sub('_', run_id)}-browser-"
    for path in sorted(results_dir.glob(f"{prefix}*.order.json")):
        try:
            order = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # being replaced or removed right now
        return str(order.get("result_key") or path.name[len(prefix):-len(".order.json")]), order
    return None


def try_claim(results_dir: Path, run_id: str, result_key: str, claim: dict) -> bool:
    """Claim an order for one browser; False if another one already has it."""
    try:
        fd = os.open(claim_path(results_dir, run_id, result_key), os.O_WRONLY | os.O_CREAT | os.O_EXCL, _MODE)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(claim, f)
    return True


def read_claim(results_dir: Path, run_id: str, result_key: str) -> dict | None:
    try:
        return json.loads(claim_path(results_dir, run_id, result_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def append_records(results_dir: Path, run_id: str, result_key: str, records: list[dict]) -> None:
    """One write of whole lines, so a reader never sees half a batch's line."""
    if not records:
        return
    data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    with open(records_path(results_dir, run_id, result_key), "a", encoding="utf-8") as f:
        f.write(data)


def read_records(results_dir: Path, run_id: str, result_key: str, offset: int) -> tuple[list[dict], int]:
    """Complete records appended since byte `offset`, and the new offset."""
    try:
        with open(records_path(results_dir, run_id, result_key), "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except FileNotFoundError:
        return [], offset
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    records = []
    for line in chunk[: end + 1].splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records, offset + end + 1
