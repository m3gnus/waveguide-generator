"""The F8 bridge over the detached fake host transport; no Julia."""
from pathlib import Path

import numpy as np
import pytest

from server.solver import official_beat
from server.solver.beat_adapter.results import acceleration_scale
from server.solver.beat_runtime import manager, readiness
from server.tests.beat_runtime.fake_host_worker import events
from server.tests.test_fem_engine_route import compiled


@pytest.mark.parametrize("cancel", [False, True])
def test_coupled_bridge_over_detached_fake_host(launch, monkeypatch, cancel):
    key, directory, children = launch
    key["environment"]["TEST_COUPLED"] = "1"
    if cancel:
        key["environment"]["TEST_COUPLED_PAUSE_AFTER_FIRST"] = "1"
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setattr(manager, "resolve_key", lambda *a, **k: key)
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **k:
                        readiness.BackendReadiness(True, "ready", "fixture proof"))
    owned = manager.WorkerManager(mode="host", directory=directory)
    progress = []
    try:
        sweep = official_beat.solve_coupled_compiled(
            compiled(), worker_manager=owned, progress_callback=lambda *args: progress.append(args),
            on_frequency_result=(lambda *args: False) if cancel else None)
        assert len(progress) == (1 if cancel else 2)
        assert sweep.cancelled is cancel
        np.testing.assert_allclose(sweep.nodal_pressure[0, 1],
                                   2 * (2 + 3j) * acceleration_scale(1000.123456))
        submitted = [e for e in events(key) if e["type"] == "submitted"]
        assert len(submitted) == 1
        assert not Path(submitted[0]["request"]["cancel_path"]).parent.exists()
        assert len(children) == 1
    finally:
        owned.shutdown()
