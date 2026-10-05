"""Tests-only entry point for a detached host without the optional engine."""

from __future__ import annotations

from server.solver.beat_runtime import host
from server.tests.beat_runtime.fake_host_worker import EngineWorker

if __name__ == "__main__":
    host.official_engine_factory = EngineWorker
    raise SystemExit(host.main())
