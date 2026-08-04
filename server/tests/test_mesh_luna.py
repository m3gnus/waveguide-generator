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
from server.mesh.integrity import mesh_integrity_report, mesh_semantic_orientation_report


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


def test_integrity_rejects_global_and_local_winding_reversals() -> None:
    points = np.asarray(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float
    )
    outward = np.asarray(
        [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=int
    )

    valid = mesh_integrity_report(points, outward)
    assert valid["valid"] is True
    assert valid["orientation_valid"] is True
    assert valid["inconsistent_edge_count"] == 0
    assert valid["signed_volume"] > 0.0

    globally_reversed = mesh_integrity_report(points, outward[:, [0, 2, 1]])
    assert globally_reversed["valid"] is False
    assert globally_reversed["is_watertight"] is True
    assert globally_reversed["orientation_valid"] is False
    assert globally_reversed["inconsistent_edge_count"] == 0
    assert globally_reversed["signed_volume"] < 0.0

    one_face_reversed = outward.copy()
    one_face_reversed[0] = one_face_reversed[0, [0, 2, 1]]
    locally_inconsistent = mesh_integrity_report(points, one_face_reversed)
    assert locally_inconsistent["valid"] is False
    assert locally_inconsistent["orientation_valid"] is False
    assert locally_inconsistent["inconsistent_edge_count"] == 3


def test_integrity_accepts_negative_volume_for_coupled_interior_domain() -> None:
    points = np.asarray(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float
    )
    inward = np.asarray(
        [[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]], dtype=int
    )

    report = mesh_integrity_report(points, inward, expected_volume_sign=-1)
    assert report["valid"] is True
    assert report["orientation_valid"] is True
    assert report["signed_volume"] < 0.0


def test_semantic_orientation_rejects_detached_source_cap_reversal() -> None:
    points = np.asarray(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [0, 1, 1],
        ],
        dtype=float,
    )
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=int)
    tags = np.asarray([2, 1], dtype=int)

    valid = mesh_semantic_orientation_report(
        points, faces, tags, mode="freestanding"
    )
    assert valid["valid"] is True
    assert valid["source_normal_projection"] > 0.0

    reversed_source = faces.copy()
    reversed_source[0] = reversed_source[0, [0, 2, 1]]
    invalid = mesh_semantic_orientation_report(
        points, reversed_source, tags, mode="freestanding"
    )
    assert invalid["valid"] is False
    assert invalid["source_normal_projection"] < 0.0
    assert any("primary source" in error for error in invalid["errors"])


def test_semantic_orientation_rejects_reversed_infinite_baffle_aperture() -> None:
    points = np.asarray(
        [
            [0, 0, -1],
            [1, 0, -1],
            [0, 1, -1],
            [0, 0, 0],
            [0, 1, 0],
            [1, 0, 0],
            [0, 0, -0.5],
            [1, 0, -0.5],
            [0, 1, -0.5],
        ],
        dtype=float,
    )
    faces = np.asarray([[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=int)
    tags = np.asarray([2, 12, 1], dtype=int)

    valid = mesh_semantic_orientation_report(
        points, faces, tags, mode="infinite-baffle"
    )
    assert valid["valid"] is True
    assert valid["source_normal_projection"] > 0.0
    assert valid["aperture_normal_projection"] < 0.0

    reversed_aperture = faces.copy()
    reversed_aperture[1] = reversed_aperture[1, [0, 2, 1]]
    invalid = mesh_semantic_orientation_report(
        points, reversed_aperture, tags, mode="infinite-baffle"
    )
    assert invalid["valid"] is False
    assert invalid["aperture_normal_projection"] > 0.0
    assert any("aperture" in error for error in invalid["errors"])


def test_semantic_orientation_rejects_detached_bare_wall_reversal() -> None:
    ring = np.asarray(
        [[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], dtype=float
    )
    mouth = 2.0 * ring
    mouth[:, 2] = 1.0
    points = np.vstack(([0, 0, 0], ring, ring, mouth))
    source = np.asarray(
        [[0, 1, 2], [0, 2, 3], [0, 3, 4], [0, 4, 1]], dtype=int
    )
    wall: list[list[int]] = []
    for index in range(4):
        throat = 5 + index
        next_throat = 5 + (index + 1) % 4
        outer = 9 + index
        next_outer = 9 + (index + 1) % 4
        wall.extend(
            ([throat, next_outer, next_throat], [throat, outer, next_outer])
        )
    faces = np.vstack((source, np.asarray(wall, dtype=int)))
    tags = np.concatenate((np.full(len(source), 2), np.full(len(wall), 1)))

    valid = mesh_semantic_orientation_report(points, faces, tags, mode="bare")
    assert valid["valid"] is True
    assert valid["open_shell_bore_alignment"] == pytest.approx(1.0)

    reversed_wall = faces.copy()
    reversed_wall[len(source) :] = reversed_wall[len(source) :, [0, 2, 1]]
    invalid = mesh_semantic_orientation_report(
        points, reversed_wall, tags, mode="bare"
    )
    assert invalid["valid"] is False
    assert invalid["open_shell_bore_alignment"] == pytest.approx(0.0)
    assert any("bare rigid-wall" in error for error in invalid["errors"])
