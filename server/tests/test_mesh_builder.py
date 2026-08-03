"""Gmsh owner-thread, topology, cancellation, and real OCC mesh tests."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.mesh.builder import build_solver_mesh
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.mesh.integrity import mesh_integrity_report


def _tiny_design() -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 60,
            "a": 30,
            "a0": 10,
            "r0": 10,
            "k": 1,
            "n": 4,
            "q": 0.99,
            "s": 0.8,
            "mesh": {
                "angular_segments": 12,
                "length_segments": 4,
                "throat_resolution": 8,
                "mouth_resolution": 15,
                "quadrants": 1,
                "wall_thickness": 2,
                "max_triangles": 50000,
            },
            "source": {"shape": 2, "radius": -1, "curvature": 0},
            "simulation": {"f1": 500, "f2": 1000, "num_frequencies": 2},
        }
    )


def test_integrity_report_detects_known_open_fixture() -> None:
    report = mesh_integrity_report(
        np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        np.asarray([[0, 1, 2]], dtype=int),
    )
    assert report["valid"] is True
    assert report["is_watertight"] is False
    assert report["open_edge_count"] == 3
    assert report["nonmanifold_edge_count"] == 0


def test_gmsh_worker_serializes_every_call_on_one_persistent_thread() -> None:
    async def scenario() -> None:
        active = 0
        maximum = 0

        def observe():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            identity = threading.get_ident()
            active -= 1
            return identity

        identities = await asyncio.gather(*(run_on_gmsh_worker(observe) for _ in range(8)))
        assert len(set(identities)) == 1
        assert maximum == 1

    asyncio.run(scenario())


def test_worker_owned_gmsh_initializes_noninterruptible(monkeypatch) -> None:
    from server.mesh import gmsh_worker

    state = {"initialized": False, "arguments": [], "finalized": 0}
    fake = SimpleNamespace(
        isInitialized=lambda: state["initialized"],
        initialize=lambda **kwargs: (
            state["arguments"].append(kwargs),
            state.__setitem__("initialized", True),
        ),
        finalize=lambda: (
            state.__setitem__("initialized", False),
            state.__setitem__("finalized", state["finalized"] + 1),
        ),
    )
    monkeypatch.setitem(sys.modules, "gmsh", fake)
    assert gmsh_worker._run_in_gmsh_session(lambda: "ok") == "ok"
    assert state["arguments"] == [{"interruptible": False}]
    assert state["finalized"] == 1


def test_mesh_build_cancellation_at_prebuild_checkpoint() -> None:
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cancelled during mesh stage")

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="cancelled during mesh stage"):
            await build_solver_mesh(_tiny_design(), {"mesh_validation_mode": "warn"}, cancel)

    asyncio.run(scenario())


@pytest.mark.skipif(
    importlib.util.find_spec("hornlab_mesher") is None,
    reason="hornlab-waveguide-mesher is not installed",
)
def test_small_real_occ_build_has_source_tag_stats_integrity_and_msh() -> None:
    async def scenario() -> None:
        stages = []
        result = await build_solver_mesh(
            _tiny_design(),
            {"mesh_validation_mode": "warn"},
            lambda: None,
            lambda *values: stages.append(values),
        )
        assert result["msh_text"].startswith("$MeshFormat")
        assert result["stats"]["vertex_count"] > 20
        assert result["stats"]["triangle_count"] > 20
        assert result["stats"]["tag_counts"]["2"] > 0
        assert result["stats"]["domain_multiplier"] == 4.0
        assert result["integrity"]["valid"] is True
        assert [stage for stage, _, _ in stages] == ["mesh_prepare", "mesh_validate"]

    asyncio.run(scenario())
