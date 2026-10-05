"""Inspect only an explicitly isolated installed qualifier's host registry."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from . import paths, registry
from .cleanup import cleanup_host
from .client import connect_client
from .ipc import receive_frame, send_frame


def inspect_hosts(directory: Path, *, stop: bool = False) -> dict[str, Any]:
    """Authenticate existing hosts without starting Julia or signalling PIDs."""
    effective = paths.worker_dir()
    answer: dict[str, Any] = {
        "verified": [], "refused": [], "effective_worker_dir": str(effective),
        "contained": effective.resolve() == directory.resolve(),
    }
    if not answer["contained"] or not effective.exists():
        return answer
    registry.private_directory(effective)
    for path in sorted(effective.glob("*.json")):
        if path.name.endswith(".key.json"):
            continue
        try:
            record = registry.read_record(path.stem, effective)
            if record is None:
                continue
            with connect_client(record, effective, timeout=2.0) as connection:
                send_frame(connection, {"op": "adopt"})
                report = receive_frame(connection, deadline=time.monotonic() + 2.0)
                if (not isinstance(report, dict) or report.get("type") != "adopted"
                        or report.get("host_pid") != record.pid):
                    raise registry.RecordRefused("Host did not report its own PID")
            found = {"host_pid": record.pid, "engine_pid": report.get("engine_pid"),
                     "worker_instance": report.get("worker_instance")}
            answer["verified"].append(found)
            if stop:
                cleanup_host(record, record.key, effective, timeout=5.0)
                found["still_alive"] = (registry.pid_alive(record.pid)
                                        and registry.process_start_identity(record.pid) in {None, record.pid_start})
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            answer["refused"].append({"record": path.name, "reason": str(exc)})
    return answer
