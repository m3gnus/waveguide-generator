"""Regression coverage for accepted Luna gmsh and mesh-integrity findings."""

from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.mesh import gmsh_worker
from server.mesh.builder import _solver_mesher_config
from server.mesh.integrity import mesh_integrity_report


def test_gmsh_shutdown_rejects_new_work_until_old_executor_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"initialized": False}
    monkeypatch.setitem(
        sys.modules,
        "gmsh",
        SimpleNamespace(
            isInitialized=lambda: state["initialized"],
            initialize=lambda **_kwargs: state.__setitem__("initialized", True),
            finalize=lambda: state.__setitem__("initialized", False),
        ),
    )

    async def scenario() -> None:
        await gmsh_worker.shutdown_gmsh_worker()
        entered = threading.Event()
        release = threading.Event()

        def blocking() -> str:
            entered.set()
            release.wait(2)
            return "done"

        work = asyncio.create_task(gmsh_worker.run_on_gmsh_worker(blocking))
        assert await asyncio.to_thread(entered.wait, 1)
        shutdown = asyncio.create_task(gmsh_worker.shutdown_gmsh_worker())
        while not gmsh_worker._shutting_down:
            await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="shutting down"):
            await gmsh_worker.run_on_gmsh_worker(lambda: None)
        release.set()
        assert await work == "done"
        await shutdown
        assert await gmsh_worker.run_on_gmsh_worker(lambda: "new") == "new"
        await gmsh_worker.shutdown_gmsh_worker()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"formula": "OSSE", "mesh": {"quadrants": "p"}},
        {"formula": "OSSE", "enclosure": {"depth": "p"}},
    ],
)
def test_solver_control_expressions_must_be_scalar(payload: dict) -> None:
    design = DesignConfig.model_validate(payload)
    with pytest.raises(ValueError, match="scalar"):
        _solver_mesher_config(design)


def test_integrity_rejects_empty_and_duplicate_faces() -> None:
    empty = mesh_integrity_report(np.zeros((0, 3)), np.zeros((0, 3), dtype=int))
    assert empty["valid"] is False
    assert empty["is_watertight"] is False

    points = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    duplicate = mesh_integrity_report(points, np.asarray([[0, 1, 2], [2, 1, 0]]))
    assert duplicate["valid"] is False
    assert duplicate["duplicate_triangle_count"] == 1

    coincident_points = np.vstack([points, points])
    coincident = mesh_integrity_report(
        coincident_points, np.asarray([[0, 1, 2], [3, 4, 5]])
    )
    assert coincident["valid"] is False
    assert coincident["duplicate_triangle_count"] == 1
