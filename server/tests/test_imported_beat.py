"""BEAT's imported-geometry adapter, against a recording stand-in for the package.

Everything here runs without Julia. Admission and execution compile the same
source identities, axes, channels and observation frame. Numerical comparison
against Metal and an analytic reference belongs to qualification.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import numpy as np
import pytest

from server.engines import registry
from server.jobs.models import SolveRequest
from server.mesh.builder import _dense_solver_memory_requirements
from server.mesh.imported import polar_grid_from_symmetry
from server.solver import beat, beat_imported
from server.solver.context import SolverContext
from server.solver.imported import mesh_text_sha256


MANIFEST_SHA = "sha256:" + "1" * 64
ARTIFACT_SHA = "sha256:" + "2" * 64

#: A four-triangle surface: one rigid face and one face per source tag. BEAT's
#: stand-in never assembles it, so it need not be closed; every coordinate is
#: positive so the same mesh can stand for a half or a quarter.
MESH = """$MeshFormat
2.2 0 8
$EndMeshFormat
$PhysicalNames
4
2 1 "wg-import-v1|rigid"
2 101 "wg-import-v1|tag=101|source_id=source-a|instance_id=i|role=HF"
2 102 "wg-import-v1|tag=102|source_id=source-b|instance_id=i|role=MF"
2 103 "wg-import-v1|tag=103|source_id=source-c|instance_id=null|role=LF"
$EndPhysicalNames
$Nodes
4
1 0.01 0 0
2 0 0.02 0
3 0 0 0.03
4 0.01 0.02 0.03
$EndNodes
$Elements
4
1 2 2 1 1 1 2 3
2 2 2 101 2 1 2 4
3 2 2 102 3 2 3 4
4 2 2 103 4 1 3 4
$EndElements
"""


def _mesh(
    nodes: list[tuple[float, float, float]], triangles: list[tuple[int, tuple[int, int, int]]]
) -> str:
    """A Gmsh 2.2 surface of ``(tag, (node, node, node))`` triangles.

    The winding is the outward normal: (b - a) x (c - a).
    """

    rows = [
        "$MeshFormat",
        "2.2 0 8",
        "$EndMeshFormat",
        "$PhysicalNames",
        "4",
        '2 1 "wg-import-v1|rigid"',
        '2 101 "wg-import-v1|tag=101|source_id=source-a|instance_id=i|role=HF"',
        '2 102 "wg-import-v1|tag=102|source_id=source-b|instance_id=i|role=MF"',
        '2 103 "wg-import-v1|tag=103|source_id=source-c|instance_id=null|role=LF"',
        "$EndPhysicalNames",
        "$Nodes",
        str(len(nodes)),
        *(f"{index} {x} {y} {z}" for index, (x, y, z) in enumerate(nodes, start=1)),
        "$EndNodes",
        "$Elements",
        str(len(triangles)),
        *(
            f"{index} 2 2 {tag} {tag} {a} {b} {c}"
            for index, (tag, (a, b, c)) in enumerate(triangles, start=1)
        ),
        "$EndElements",
    ]
    return "\n".join(rows) + "\n"


#: Sources whose faces are square to BEAT's z: tag 101 faces -z, 102 and 103
#: face +z. Flat, so each tag's axis is exactly +-z of the solver frame.
MESH_Z = _mesh(
    [(0, 0, 0), (0.01, 0, 0), (0, 0.01, 0), (0, 0, 0.02), (0.01, 0, 0.02), (0, 0.01, 0.02)],
    [(1, (1, 2, 4)), (101, (4, 6, 5)), (102, (1, 2, 3)), (103, (4, 5, 6))],
)

#: The same, along x: tag 103 faces -x, so in SIDEWAYS_FRAME (axis +x) it is -z.
MESH_X = _mesh(
    [
        (0.01, 0, 0),
        (0, 0.02, 0),
        (0, 0, 0.03),
        (0.01, 0.02, 0.03),
        (0.01, 0, 0),
        (0.01, 0.02, 0),
        (0.01, 0, 0.03),
    ],
    [(1, (1, 2, 3)), (101, (1, 2, 4)), (102, (2, 3, 4)), (103, (5, 7, 6))],
)

IDENTITY_FRAME = {
    "axis": [0.0, 0.0, 1.0],
    "u": [1.0, 0.0, 0.0],
    "v": [0.0, 1.0, 0.0],
    "origin_m": [0.0, 0.0, 0.0],
    "mouth_center_m": [0.0, 0.0, 0.0],
    "source_center_m": [0.0, 0.0, 0.0],
}

#: A CAD-authored placement: the horn points along +x. u x v = axis.
SIDEWAYS_FRAME = {
    "axis": [1.0, 0.0, 0.0],
    "u": [0.0, 1.0, 0.0],
    "v": [0.0, 0.0, 1.0],
    "origin_m": [0.05, 0.02, 0.03],
    "mouth_center_m": [0.05, 0.02, 0.03],
    "source_center_m": [0.05, 0.02, 0.03],
}


def _at(origin: list[float], frame: dict[str, Any] | None = None) -> dict[str, Any]:
    """A frame whose throat -- the observation origin -- sits at ``origin``."""

    return {
        **(frame or IDENTITY_FRAME),
        "origin_m": list(origin),
        "mouth_center_m": list(origin),
        "source_center_m": list(origin),
    }


def _symmetry(planes: list[str]) -> dict[str, Any]:
    return {
        "cut_planes": list(planes),
        "planes": {name: {"accepted": name in planes} for name in ("x0", "y0", "z0")},
    }


def _record(
    *,
    planes: list[str] | None = None,
    frame: dict[str, Any] | None = None,
    msh_text: str = MESH,
) -> dict[str, Any]:
    symmetry = _symmetry(planes or [])
    return {
        "manifest_sha256": MANIFEST_SHA,
        "artifact_sha256": ARTIFACT_SHA,
        "mesh_content_sha256": mesh_text_sha256(msh_text),
        "_execution_msh_text": msh_text,
        "sources": [
            {"id": "source-a", "required": True, "role": "HF", "label": "HF throat"},
            {"id": "source-b", "required": True, "role": "MF"},
            {"id": "source-c", "required": False, "role": "LF"},
        ],
        "source_tags": {"source-a": 101, "source-b": 102, "source-c": 103},
        "tag_namespace": "wg-import-v1",
        "tag_map": {
            "1": {"source_id": None, "instance_id": None, "role": "rigid"},
            "101": {"source_id": "source-a", "instance_id": "i", "role": "HF"},
            "102": {"source_id": "source-b", "instance_id": "i", "role": "MF"},
            "103": {"source_id": "source-c", "instance_id": None, "role": "LF"},
        },
        "anchor": {
            "instance_id": "i",
            "design_id": None,
            "throat_frame": dict(frame or IDENTITY_FRAME),
        },
        "symmetry": symmetry,
        "polar_grid_derivation": polar_grid_from_symmetry(symmetry),
        "mesh": {"stats": {"triangle_count": 4, "vertex_count": 4}, "metadata": {}},
        "evidence": {"fem_air_volumes": []},
        "findings": [],
    }


def _request(**geometry_changes: Any) -> SolveRequest:
    geometry: dict[str, Any] = {
        "type": "imported",
        "ingest_id": "wgi_" + "0" * 26,
        "manifest_sha256": MANIFEST_SHA,
        "artifact_sha256": ARTIFACT_SHA,
        "drive_channels": [
            {"id": "left", "source_ids": ["source-a", "source-b"]},
            {"id": "right", "source_ids": ["source-c"]},
        ],
        "mesh": {
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {"source-a": 3.0, "source-b": 3.0, "source-c": 4.0},
        },
    }
    geometry.update(geometry_changes)
    return SolveRequest.model_validate(
        {
            "geometry": geometry,
            "options": {
                "engine": "beat-cpu",
                "frequencies_hz": [100.0, 500.0, 1000.0],
                "polar_config": {"angle_range": [-180.0, 180.0, 37]},
            },
        }
    )


def _run(request: SolveRequest, record: dict[str, Any], streamed: list[Any] | None = None):
    def result_cb(index: int, payload: dict[str, Any]) -> None:
        if streamed is not None:
            streamed.append((index, payload))

    return asyncio.run(
        beat.BeatEngine("cpu").run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_: None,
            result_cb=result_cb,
            imported_record=record,
        )
    )


def test_displaced_parser_and_sign_groups_are_removed() -> None:
    for name in ("_Gmsh22Mesh", "BeatImportedFrame", "beat_imported_frame", "beat_axial_signs",
                 "_drive_groups", "_signed_sum", "_check_kept_side"):
        assert not hasattr(beat_imported, name)


def _axial_request(*source_ids: str) -> SolveRequest:
    rest = [name for name in ("source-a", "source-b", "source-c") if name not in source_ids]
    channels: list[dict[str, Any]] = [
        {"id": "axial", "source_ids": list(source_ids), "motion": "axial"}
    ]
    if rest:
        channels.append({"id": "rest", "source_ids": rest})
    return _request(drive_channels=channels)


def test_a_tilted_source_within_half_a_degree_of_z_snaps_and_beyond_it_is_refused(monkeypatch) -> None:
    def cap(tilt_degrees: float) -> str:
        tilt = math.radians(tilt_degrees)
        # A triangle whose normal is tilted about y by ``tilt``.
        base = np.asarray([[0.0, 0.0, 0.0], [0.01, 0.0, 0.0], [0.0, 0.01, 0.0]])
        rotation = np.asarray(
            [
                [math.cos(tilt), 0.0, math.sin(tilt)],
                [0.0, 1.0, 0.0],
                [-math.sin(tilt), 0.0, math.cos(tilt)],
            ]
        )
        nodes = [tuple(float(v) for v in row) for row in base @ rotation.T]
        nodes += [(0.0, 0.0, 0.05), (0.01, 0.0, 0.05), (0.0, 0.01, 0.05)]
        return _mesh(nodes, [(1, (4, 5, 6)), (101, (1, 2, 3)), (102, (4, 5, 6)), (103, (4, 5, 6))])

    request = _axial_request("source-a")
    channels = request.geometry.drive_channels
    near = cap(0.3)
    assert (
        beat.BeatEngine("cpu").imported_preflight(
            _record(msh_text=near), near, drive_channels=channels
        )
        is None
    )
    far = cap(0.8)
    refusal = beat.BeatEngine("cpu").imported_preflight(
        _record(msh_text=far), far, drive_channels=channels
    )
    assert refusal is not None and "tag 101" in refusal
    from server.solver import official_beat
    monkeypatch.setattr(official_beat, "production_statuses", lambda **_: {"cpu": {"available": True}})
    with pytest.raises(beat.BeatUnavailable) as caught:
        beat_imported.solve_imported_beat_from_msh_text(far, request, _record(msh_text=far), backend="cpu")
    assert str(caught.value) == refusal


def test_a_closed_source_cannot_be_driven_axially() -> None:
    """A source whose faces cancel has no outward axis."""

    tetra = _mesh(
        [(0, 0, 0), (0.01, 0, 0), (0, 0.01, 0), (0, 0, 0.01)],
        [(1, (1, 2, 3)), (101, (1, 3, 2)), (101, (1, 2, 4)), (101, (2, 3, 4)), (101, (3, 1, 4))],
    )
    refusal = beat.BeatEngine("cpu").imported_preflight(
        _record(msh_text=tetra),
        tetra,
        drive_channels=_axial_request("source-a").geometry.drive_channels,
    )
    assert refusal is not None and "tag 101" in refusal


def _tilted_about_y(degrees: float) -> dict[str, Any]:
    angle = math.radians(degrees)
    return {
        **IDENTITY_FRAME,
        "axis": [math.sin(angle), 0.0, math.cos(angle)],
        "u": [math.cos(angle), 0.0, -math.sin(angle)],
        "v": [0.0, 1.0, 0.0],
    }


NEGATIVE_SIDE_MESH = MESH.replace("1 0.01 0 0", "1 -0.01 0 0")


@pytest.mark.parametrize(
    ("planes", "frame", "msh_text", "expected"),
    [
        (["y0"], IDENTITY_FRAME, MESH, "y-only half"),
        (["x0"], _tilted_about_y(30.0), MESH, "would move that plane"),
        (["x0"], _at([0.002, 0.0, 0.0]), MESH, "off that plane"),
        ([], {**IDENTITY_FRAME, "v": [0.0, -1.0, 0.0]}, MESH, "right-handed orthonormal"),
        (["x0"], IDENTITY_FRAME, NEGATIVE_SIDE_MESH, "negative side"),
    ],
    ids=["y-only-half", "tilted-mirror", "origin-off-plane", "left-handed", "negative-side"],
)
def test_returns_beat_cannot_solve_are_refused_with_the_reason_named(
    planes: list[str], frame: dict[str, Any], msh_text: str, expected: str, monkeypatch
) -> None:
    record = _record(planes=planes, frame=frame, msh_text=msh_text)

    refusal = beat.BeatEngine("cpu").imported_preflight(record, msh_text)

    assert refusal is not None and expected in refusal
    from server.solver import official_beat
    monkeypatch.setattr(official_beat, "production_statuses", lambda **_: {"cpu": {"available": True}})
    with pytest.raises(beat.BeatUnavailable) as caught:
        beat_imported.solve_imported_beat_from_msh_text(msh_text, _request(), record, backend="cpu")
    assert str(caught.value) == refusal


def test_an_unused_node_on_the_negative_side_has_the_compiler_verdict(monkeypatch) -> None:
    """Admission must catch the compiler refusal, even for orphan nodes."""

    orphan = MESH.replace("$Nodes\n4\n", "$Nodes\n5\n").replace(
        "$EndNodes", "5 -0.05 0 0\n$EndNodes"
    )

    from server.solver import official_beat
    record = _record(planes=["x0"], msh_text=orphan)
    refusal = beat.BeatEngine("cpu").imported_preflight(record, orphan)
    assert refusal and "negative side" in refusal
    monkeypatch.setattr(official_beat, "production_statuses", lambda **_: {"cpu": {"available": True}})
    with pytest.raises(beat.BeatUnavailable) as caught:
        beat_imported.solve_imported_beat_from_msh_text(orphan, _request(), record, backend="cpu")
    assert str(caught.value) == refusal


def test_one_tag_triangles_retain_their_physical_identity() -> None:
    one_tag = MESH.replace("1 2 2 1 1 1 2 3", "1 2 1 1 1 2 3").replace(
        "2 2 2 101 2 1 2 4", "2 2 1 101 1 2 4")
    compiled = beat_imported.compile_imported_beat(_record(msh_text=one_tag), one_tag)
    np.testing.assert_array_equal(compiled.mesh.tags, [1, 101, 102, 103])


def test_a_full_domain_accepts_any_right_handed_anchor_frame() -> None:
    for frame in (IDENTITY_FRAME, SIDEWAYS_FRAME, _tilted_about_y(30.0)):
        assert beat.BeatEngine("cpu").imported_preflight(_record(frame=frame), MESH) is None


def test_the_dense_memory_admission_carries_a_beat_term() -> None:
    triangles = np.asarray([[0, 1, 2], [1, 2, 3]])

    requirements = _dense_solver_memory_requirements(triangles, 1234)

    assert requirements["beat_bytes"] == 16 * 4**2
    assert requirements["beat_bytes_per_vertex_squared"] == 16
    assert requirements["estimated_bytes"] == max(
        requirements["metal_bytes"], requirements["bempp_bytes"], requirements["beat_bytes"]
    )


def test_the_imported_ground_plane_backstop_names_no_single_engine() -> None:
    request = _request()
    request.options.ground_plane.enabled = True

    with pytest.raises(ValueError, match="on any engine") as caught:
        SolverContext.from_imported_request(request, quadrants=1234, source_motion="normal")
    assert "Metal" not in str(caught.value)


def test_the_registry_declares_imported_geometry_for_every_beat_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.solver import bempp, metal, official_beat

    available = {"available": True, "reason": "ok", "version": "t"}
    monkeypatch.setattr(metal, "metal_status", lambda: dict(available))
    monkeypatch.setattr(bempp, "bempp_status", lambda: dict(available))
    monkeypatch.setattr(
        official_beat,
        "production_statuses",
        lambda: {name: dict(available, backend=name) for name in beat.BEAT_BACKENDS},
    )

    engines = {info.name: info for info in registry.detect_engines(environ={})}

    imported = sorted(name for name, info in engines.items() if "imported" in info.geometry_sources)
    assert imported == ["beat-cpu", "metal"]
    assert engines["beat-cpu"].symmetry_domains == ("full", "half-yz", "quarter")
    assert engines["metal"].imported_features == ("passive-cardioid",)
    assert engines["beat-cpu"].imported_features == ("exterior-transducers", "fem-regions")




@pytest.mark.parametrize("failure", ["missing-normal-source", "duplicate-channel", "ground", "cardioid"])
def test_full_request_admission_and_execution_share_compiler_refusals(monkeypatch, failure):
    from server.solver import official_beat
    request, record = _request(), _record()
    if failure == "missing-normal-source":
        record["source_tags"]["source-c"] = 999
    elif failure == "duplicate-channel":
        request.geometry.drive_channels[1].id = request.geometry.drive_channels[0].id
    elif failure == "ground":
        request.options.ground_plane.enabled = True
    else:
        request.geometry.passive_cardioid_rear_volume_l = 10.
    monkeypatch.setattr(official_beat, "production_statuses", lambda **_: {"cpu": {"available": True}})
    refusal = beat.BeatEngine("cpu").imported_preflight(record, MESH, request=request)
    assert refusal
    with pytest.raises(beat.BeatUnavailable) as caught:
        beat_imported.solve_imported_beat_from_msh_text(MESH, request, record, backend="cpu")
    assert str(caught.value) == refusal
