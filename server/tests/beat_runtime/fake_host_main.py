"""Tests-only entry point for a detached host without the optional engine."""

from __future__ import annotations

import os
from pathlib import Path

from server.solver.beat_runtime import host, retire
from server.tests.beat_runtime.fake_host_worker import EngineWorker


def _test_retire_event_name(name: str) -> str:
    """Refuse production or namespaced events before any Windows API call."""
    prefix = "WaveguideGeneratorTestRetireIdleBeatHosts-"
    if (not isinstance(name, str) or not name.startswith(prefix) or not name[len(prefix):].strip()
            or "\\" in name or "\0" in name):
        raise ValueError("Expected a private test retire event name")
    return name


def _simulate_suspend(marker: Path) -> None:
    """Jump the idle clock a day forward once ``marker`` exists, as a resume would.

    Lets a test expire a host it cannot reach in-process (one that left its
    parent's job) without waiting out a real idle timeout.
    """

    clock = host.suspend_aware_monotonic

    def suspend_aware_monotonic() -> float:
        return clock() + (86_400.0 if marker.exists() else 0.0)

    host.suspend_aware_monotonic = suspend_aware_monotonic


if __name__ == "__main__":
    # Only this tests-only entry reads the override, just like the suspend marker.
    # Missing or unsafe names fail closed instead of opening the installer event.
    retire.RETIRE_IDLE_EVENT = _test_retire_event_name(os.environ.get("BEAT_FAKE_HOST_RETIRE_EVENT", ""))
    host.official_engine_factory = EngineWorker
    if os.environ.get("BEAT_FAKE_HOST_SUSPEND_FILE"):
        _simulate_suspend(Path(os.environ["BEAT_FAKE_HOST_SUSPEND_FILE"]))
    raise SystemExit(host.main())
