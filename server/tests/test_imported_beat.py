"""BEAT's imported-geometry adapter, against a recording stand-in for the package.

Everything here runs without Julia. The stand-in records the mesh text, the
frame and the drive each solve is handed, so the adapter's three translations
-- the per-channel tag merge, the rigid rotation into BEAT's +z frame, and the
reduced-domain mirrors -- are checked on exactly what BEAT would receive. The
numerical comparison against Metal and an analytic reference is the
qualification suite's job.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from server.contracts.conventions import PHASE_TIME_CONVENTION
from server.engines import registry
from server.jobs.models import SolveRequest
from server.mesh.builder import _dense_solver_memory_requirements
from server.mesh.imported import polar_grid_from_symmetry
from server.solver import beat, beat_imported
from server.solver.combine import deserialize_channel_bases
from server.solver.context import SolverContext
from server.solver.imported import mesh_text_sha256
from server.solver.result_mapping import REFERENCE_RHO_C


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


class _RecordingBeat:
    """Stands in for ``hornlab_beat_bem``; records what each solve receives."""

    def __init__(self) -> None:
        self.solves: list[dict[str, Any]] = []

    @staticmethod
    def ObservationConfig(**kwargs: Any) -> SimpleNamespace:  # noqa: N802
        return SimpleNamespace(**kwargs)

    @staticmethod
    def ObservationFrame(**kwargs: Any) -> SimpleNamespace:  # noqa: N802
        return SimpleNamespace(**kwargs)

    @staticmethod
    def SolveConfig(**kwargs: Any) -> SimpleNamespace:  # noqa: N802
        return SimpleNamespace(**kwargs)

    @staticmethod
    def reject_unsupported_native_symmetry(config: Any) -> None:
        if config.native_symmetry_plane in {"xz", "xy"}:
            raise NotImplementedError("no y-only mirror")

    def solve_frequencies(
        self, path: str, frequencies: list[float], config: Any, *, status_callback: Any = None
    ) -> SimpleNamespace:
        del status_callback
        scale = float(len(self.solves) + 1)
        self.solves.append({"text": Path(path).read_text(encoding="utf-8"), "config": config})
        count = len(frequencies)
        result = SimpleNamespace(
            frequencies_hz=np.asarray(frequencies, dtype=float),
            observation_angles_deg=np.asarray([-180.0, 0.0, 180.0]),
            observation_planes=["horizontal"],
            pressure_complex=np.ones((count, 1, 3), dtype=np.complex128) * 20.0e-6 * scale,
            directivity_db=np.zeros((count, 1, 3)),
            impedance=np.ones(count, dtype=np.complex128) * (1j * REFERENCE_RHO_C),
            solver_log=[],
            timings={},
            native_diagnostics=[],
        )
        for index, frequency in enumerate(frequencies):
            config.progress_callback(index, count, frequency)
            if config.on_frequency_result is not None:
                config.on_frequency_result(
                    index,
                    frequency,
                    {
                        "observation_angles_deg": result.observation_angles_deg,
                        "observation_planes": result.observation_planes,
                        "observation_pressure_complex": result.pressure_complex[index],
                        "observation_spl_db": np.zeros((1, 3)),
                        "impedance": result.impedance[index],
                    },
                )
        return result


@pytest.fixture
def recording_beat(monkeypatch: pytest.MonkeyPatch) -> _RecordingBeat:
    package = _RecordingBeat()
    monkeypatch.setattr(beat_imported, "_load_api", lambda: package)
    monkeypatch.setattr(
        beat_imported,
        "beat_backend_statuses",
        lambda: {
            "cpu": {
                "available": True,
                "reason": "ok",
                "version": "0.1.0",
                "backend": "cpu",
                "surface_traces": False,
            }
        },
    )
    return package


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


def _elements(text: str) -> dict[int, int]:
    """Element id -> physical tag, from a solve's mesh text."""

    lines = text.splitlines()
    start = lines.index("$Elements")
    rows = lines[start + 2 : start + 2 + int(lines[start + 1])]
    return {int(row.split()[0]): int(row.split()[3]) for row in rows}


def _nodes(text: str) -> np.ndarray:
    lines = text.splitlines()
    start = lines.index("$Nodes")
    rows = lines[start + 2 : start + 2 + int(lines[start + 1])]
    return np.asarray([[float(value) for value in row.split()[1:4]] for row in rows])


def test_each_channel_is_solved_once_on_one_merged_tag_in_beats_frame(
    recording_beat: _RecordingBeat,
) -> None:
    streamed: list[Any] = []
    record = _record(frame=SIDEWAYS_FRAME, msh_text=MESH_X)
    request = _request(
        drive_channels=[
            {"id": "left", "source_ids": ["source-a", "source-b"]},
            {"id": "right", "source_ids": ["source-c"], "motion": "axial"},
        ]
    )

    outcome = _run(request, record, streamed)

    # One BEAT solve per drive channel. The channel's member tags become the
    # one driven tag; every other tag, including the other channel's, is rigid.
    assert len(recording_beat.solves) == 2
    left, right = recording_beat.solves
    assert _elements(left["text"]) == {1: 1, 2: 2, 3: 2, 4: 1}
    assert _elements(right["text"]) == {1: 1, 2: 1, 3: 1, 4: 2}
    assert left["config"].velocity_sources == {2: 1.0}
    assert right["config"].velocity_sources == {2: 1.0}
    # Each channel keeps its own motion.
    assert left["config"].source_motion == "normal"
    assert right["config"].source_motion == "axial"

    # The mesh is rotated rigidly so the anchor frame (u, v, axis) lands on
    # BEAT's (+x, +y, +z); BEAT's frame is then a pure translation.
    rotation = np.asarray([SIDEWAYS_FRAME["u"], SIDEWAYS_FRAME["v"], SIDEWAYS_FRAME["axis"]])
    original = _nodes(MESH_X)
    np.testing.assert_allclose(_nodes(left["text"]), original @ rotation.T, atol=1e-15)
    frame = left["config"].frame_override
    np.testing.assert_array_equal(frame.axis, [0.0, 0.0, 1.0])
    np.testing.assert_array_equal(frame.u, [1.0, 0.0, 0.0])
    np.testing.assert_array_equal(frame.v, [0.0, 1.0, 0.0])
    np.testing.assert_allclose(frame.origin, rotation @ np.asarray(SIDEWAYS_FRAME["origin_m"]))
    assert left["config"].native_symmetry_plane is None

    response = outcome.results
    assert all(
        channel["metadata"]["phase_time_convention"] == PHASE_TIME_CONVENTION
        for channel in response["channels"].values()
    )
    assert response["result_kind"] == "multi_channel"
    assert response["channel_order"] == ["left", "right"]
    # The run reports the record's own frame, not BEAT's rotated one.
    assert response["metadata"]["observation_frame_basis"]["axis"] == [1.0, 0.0, 0.0]
    assert response["channels"]["left"]["metadata"]["observation_frame_basis"]["u"] == [
        0.0,
        1.0,
        0.0,
    ]
    assert response["metadata"]["solver_engine"]["engine"] == "beat-cpu"
    assert response["metadata"]["solver_engine"]["formulation"] == "burton_miller"
    assert response["metadata"]["beat_solver_frame"]["rotation_rows"] == rotation.tolist()
    assert response["channels"]["left"]["metadata"]["beat"]["merged_source_tags"] == [
        101,
        102,
    ]
    # Metal's multi-source impedance rule: a merged channel has no single
    # impedance, a single-source channel keeps its own.
    assert "impedance" not in response["channels"]["left"]
    assert response["channels"]["left"]["metadata"]["impedance_omitted"].startswith(
        "multi-source channel"
    )
    assert "impedance" in response["channels"]["right"]

    # Each channel keeps its own complex basis for recombination. The stand-in
    # returns 1x and 2x a unit field; tag 103's resolved axis is -x, which is
    # -z of BEAT's frame, so its axial channel is the -z group, negated.
    bases = deserialize_channel_bases(outcome.channel_bases)
    assert bases["channel_ids"] == ["left", "right"]
    left_basis = bases["results_by_id"]["left"].pressure_complex
    right_basis = bases["results_by_id"]["right"].pressure_complex
    np.testing.assert_allclose(right_basis, -2.0 * left_basis)
    assert response["channels"]["right"]["metadata"]["beat"]["axially_reversed_source_tags"] == [
        103
    ]

    # Streamed frames are numbered across channels, so the runtime's
    # one-revision-per-frame rule keeps every one of them.
    assert [index for index, _ in streamed] == list(range(6))
    assert [list(payload["channels"]) for _, payload in streamed] == [["left"]] * 3 + [
        ["right"]
    ] * 3
    # Channels arrive one after another: the live count is the current
    # channel's, out of the sweep, and the envelope's frequency axis comes
    # from the first channel only, so each frequency appears once.
    provisional = [payload["metadata"]["provisional"] for _, payload in streamed]
    assert [item["completed_frequency_count"] for item in provisional] == [1, 2, 3, 1, 2, 3]
    assert {item["expected_frequency_count"] for item in provisional} == {3}
    assert [item["channel"]["index"] for item in provisional] == [1, 1, 1, 2, 2, 2]
    assert ["frequencies" in payload for _, payload in streamed] == [True] * 3 + [False] * 3
    assert all(
        payload["channels"][channel]["metadata"]["observation_frame_basis"]["axis"]
        == [1.0, 0.0, 0.0]
        for _, payload in streamed
        for channel in payload["channels"]
    )
    # The job stores the mesh as ingested, not the rotated copy.
    assert outcome.msh_text == MESH_X
    # The channel records its motion, the contract and each tag's axis.
    right_metadata = response["channels"]["right"]["metadata"]
    assert right_metadata["source_motion"] == "axial"
    assert right_metadata["axial_contract"] == "per-source-axis-v2"
    assert right_metadata["source_axes"][0]["tag"] == 103
    assert right_metadata["source_axes"][0]["axis"] == [-1.0, 0.0, 0.0]
    assert right_metadata["source_axes"][0]["snapped_to"] == "-x"
    assert "source_axes" not in response["channels"]["left"]["metadata"]
    stored = json.loads(str(np.load(io.BytesIO(outcome.channel_bases))["metadata::right"].item()))
    assert stored["axial_contract"] == "per-source-axis-v2"
    assert stored["source_axes"][0]["axis"] == [-1.0, 0.0, 0.0]


def test_an_axial_source_along_minus_z_is_the_negated_plus_z_group(
    recording_beat: _RecordingBeat,
) -> None:
    """BEAT drives ``n . z`` on its one tag and carries no sign.

    A source whose resolved axis is -z is therefore its own group, subtracted,
    by linearity. In MESH_Z tag 101 faces -z (its outward axis is -z), 102 +z.
    """

    request = _request(
        drive_channels=[
            {"id": "left", "source_ids": ["source-a", "source-b"], "motion": "axial"},
            {"id": "right", "source_ids": ["source-c"]},
        ]
    )

    outcome = _run(request, _record(msh_text=MESH_Z))

    # Two BEAT solves for the axial channel -- the +z tags, then the -z one --
    # and one for the normal channel.
    assert len(recording_beat.solves) == 3
    forward, backward, normal = recording_beat.solves
    assert _elements(forward["text"]) == {1: 1, 2: 1, 3: 2, 4: 1}
    assert _elements(backward["text"]) == {1: 1, 2: 2, 3: 1, 4: 1}
    assert _elements(normal["text"]) == {1: 1, 2: 1, 3: 1, 4: 2}
    assert normal["config"].source_motion == "normal"
    # The stand-in returns 1x, 2x and 3x a unit field for the three solves:
    # the channel is forward minus backward.
    bases = deserialize_channel_bases(outcome.channel_bases)
    left = bases["results_by_id"]["left"].pressure_complex
    right = bases["results_by_id"]["right"].pressure_complex
    np.testing.assert_allclose(left, -1.0 * right / 3.0)
    metadata = outcome.results["channels"]["left"]["metadata"]
    assert metadata["beat"]["axially_reversed_source_tags"] == [101]
    assert [item["axis"] for item in metadata["source_axes"]] == [[0.0, 0.0, -1.0], [0.0, 0.0, 1.0]]


def test_drive_groups_follow_the_signs_and_never_a_rounding_residue() -> None:
    tags = frozenset({101, 102})
    signs = {101: -1.0, 102: 1.0}
    assert beat_imported._drive_groups(tags, "axial", signs) == [
        (1.0, frozenset({102})),
        (-1.0, frozenset({101})),
    ]
    assert beat_imported._drive_groups(tags, "normal", signs) == [(1.0, tags)]
    assert beat_imported._drive_groups(tags, "axial", {101: 1.0, 102: 1.0}) == [(1.0, tags)]


def _axial_request(*source_ids: str) -> SolveRequest:
    rest = [name for name in ("source-a", "source-b", "source-c") if name not in source_ids]
    channels: list[dict[str, Any]] = [
        {"id": "axial", "source_ids": list(source_ids), "motion": "axial"}
    ]
    if rest:
        channels.append({"id": "rest", "source_ids": rest})
    return _request(drive_channels=channels)


def test_an_axial_source_along_another_axis_is_refused_by_name(
    recording_beat: _RecordingBeat,
) -> None:
    """MESH's tag 103 is a tilted face: its axis is not +-z, so BEAT refuses."""

    record = _record()
    request = _axial_request("source-c")

    refusal = beat.BeatEngine("cpu").imported_preflight(
        record, MESH, drive_channels=request.geometry.drive_channels
    )
    assert refusal is not None
    assert "tag 103" in refusal and "z axis only" in refusal
    with pytest.raises(beat.BeatUnavailable, match="tag 103"):
        _run(request, record)
    assert recording_beat.solves == []
    # Without axial motion the same record is fine, and so is asking without channels.
    assert beat.BeatEngine("cpu").imported_preflight(record, MESH) is None


def test_a_tilted_source_within_half_a_degree_of_z_snaps_and_beyond_it_is_refused() -> None:
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


@pytest.mark.parametrize(
    ("planes", "native"),
    [(["x0"], "yz"), (["x0", "y0"], "yz+xz")],
    ids=["x0-half", "quarter"],
)
def test_an_x0_half_and_a_quarter_execute_natively_on_the_mirror_planes(
    recording_beat: _RecordingBeat, planes: list[str], native: str
) -> None:
    # A throat a nanometre off the plane is CAD noise; BEAT's translation
    # must still not shift the mirror, so the origin lands exactly on it.
    frame = _at([1.0e-9, 1.0e-9, 0.004])

    _run(_request(), _record(planes=planes, frame=frame))

    config = recording_beat.solves[0]["config"]
    assert config.native_symmetry_plane == native
    origin = config.frame_override.origin
    assert origin[0] == 0.0
    assert origin[1] == (0.0 if native == "yz+xz" else 1.0e-9)
    assert origin[2] == pytest.approx(0.004)


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
    planes: list[str], frame: dict[str, Any], msh_text: str, expected: str
) -> None:
    record = _record(planes=planes, frame=frame, msh_text=msh_text)

    refusal = beat.BeatEngine("cpu").imported_preflight(record, msh_text)

    assert refusal is not None and expected in refusal


def test_an_unused_node_on_the_negative_side_does_not_refuse_a_half() -> None:
    """Only the nodes a triangle uses are the surface BEAT mirrors."""

    orphan = MESH.replace("$Nodes\n4\n", "$Nodes\n5\n").replace(
        "$EndNodes", "5 -0.05 0 0\n$EndNodes"
    )

    assert (
        beat.BeatEngine("cpu").imported_preflight(_record(planes=["x0"], msh_text=orphan), orphan)
        is None
    )


def test_a_one_tag_triangle_row_is_written_with_both_tags() -> None:
    """BEAT reads a triangle's tag only from a row carrying two tags."""

    one_tag = MESH.replace("1 2 2 1 1 1 2 3", "1 2 1 1 1 2 3").replace(
        "2 2 2 101 2 1 2 4", "2 2 1 101 1 2 4"
    )

    rows = _elements(beat_imported._Gmsh22Mesh.parse(one_tag).text(frozenset({101})))
    text = beat_imported._Gmsh22Mesh.parse(one_tag).text(frozenset({101}))

    assert rows == {1: 1, 2: 2, 3: 1, 4: 1}
    assert "\n1 2 2 1 1 1 2 3\n" in text
    assert "\n2 2 2 2 2 1 2 4\n" in text


def test_a_full_domain_accepts_any_right_handed_anchor_frame() -> None:
    for frame in (IDENTITY_FRAME, SIDEWAYS_FRAME, _tilted_about_y(30.0)):
        assert beat.BeatEngine("cpu").imported_preflight(_record(frame=frame), MESH) is None


def test_a_linked_return_with_the_axis_along_minus_z_is_rotated_not_refused(
    recording_beat: _RecordingBeat,
) -> None:
    """The 180-degree case the capability assessment left open.

    Real linked returns carry the anchor axis along +z, but the adapter does not
    rely on it: a -z axis is a 180-degree rotation about x like any other.
    """

    frame = {**IDENTITY_FRAME, "axis": [0.0, 0.0, -1.0], "v": [0.0, -1.0, 0.0]}

    _run(_request(), _record(frame=frame))

    nodes = _nodes(recording_beat.solves[0]["text"])
    np.testing.assert_allclose(nodes, _nodes(MESH) * np.asarray([1.0, -1.0, -1.0]))


@pytest.mark.parametrize("backend", ["metal", "cuda", "rocm", "cpu"])
def test_imported_adapter_passes_selected_backend_to_beat(
    backend: str, recording_beat: _RecordingBeat, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        beat_imported,
        "beat_backend_statuses",
        lambda: {backend: {"available": True, "reason": "ok", "backend": backend}},
    )
    engine = beat.BeatEngine(backend)

    assert engine.imported_preflight(_record(), MESH) is None
    request = _request()
    # Fast is refused on the unqualified accelerators only; BEAT does not see
    # accuracy, so Metal takes the identical solve in Fast.
    if backend in ("cuda", "rocm"):
        with pytest.raises(beat.BeatUnavailable, match="only in Accurate"):
            asyncio.run(
                engine.run(
                    request,
                    cancel_cb=lambda: None,
                    stage_cb=lambda *_: None,
                    imported_record=_record(),
                )
            )
        assert recording_beat.solves == []
        request.options.accuracy = "accurate"
    result = asyncio.run(
        engine.run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_: None,
            imported_record=_record(),
        )
    )

    assert recording_beat.solves
    assert {solve["config"].beat_backend for solve in recording_beat.solves} == {backend}
    assert {solve["config"].regular_quadrature_mode for solve in recording_beat.solves} == {"fixed"}
    # The same thread count as the parametric solve and the warmup: the package
    # keys workers by it, so a mismatch boots a second Julia worker.
    from server.solver.beat_threads import beat_julia_threads

    assert {solve["config"].julia_threads for solve in recording_beat.solves} == {
        beat_julia_threads(backend)
    }
    assert result.results["metadata"]["solver_engine"]["device"] == backend


def test_the_passive_cardioid_campaign_is_refused_on_beat(
    recording_beat: _RecordingBeat,
) -> None:
    request = _request(
        drive_channels=[{"id": "left", "source_ids": ["source-a", "source-b", "source-c"]}],
        passive_cardioid_rear_volume_l=6.0,
        passive_cardioid_port_length_mm=25.0,
        model_port_area_m2=0.05,
        bem_port_area_m2=0.009471859930646809,
        port_area_source="user",
        passive_cardioid_foam_resistance_pa_s_m3=10_000.0,
    )

    with pytest.raises(beat.BeatUnavailable, match="implemented on Metal only"):
        _run(request, _record())
    assert recording_beat.solves == []


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
    from server.solver import bempp, metal

    available = {"available": True, "reason": "ok", "version": "t"}
    monkeypatch.setattr(metal, "metal_status", lambda: dict(available))
    monkeypatch.setattr(bempp, "bempp_status", lambda: dict(available))
    monkeypatch.setattr(
        beat,
        "beat_backend_statuses",
        lambda: {name: dict(available, backend=name) for name in beat.BEAT_BACKENDS},
    )

    engines = {info.name: info for info in registry.detect_engines(environ={})}

    imported = sorted(name for name, info in engines.items() if "imported" in info.geometry_sources)
    assert imported == ["beat-cpu", "metal"]
    assert engines["beat-cpu"].symmetry_domains == ("full", "half-yz", "quarter")
    assert engines["metal"].imported_features == ("passive-cardioid",)
    assert engines["beat-cpu"].imported_features == ()


@pytest.mark.parametrize("backend", beat.BEAT_BACKENDS)
def test_parametric_beat_uses_fixed_quadrature_and_shared_phase_tag(
    backend, recording_beat, monkeypatch
) -> None:
    from server.solver import beat
    from server.solver.context import SolverContext

    monkeypatch.setattr(beat, "_load_api", lambda: recording_beat)
    monkeypatch.setattr(
        beat,
        "beat_backend_statuses",
        lambda: {backend: {"available": True, "reason": "ok", "backend": backend}},
    )
    context = SolverContext(design=None, frequency_range=(100.0, 1000.0), num_frequencies=3)
    msh = Path(beat.__file__).with_name("warmup_mesh.msh").read_text()
    response = beat.solve_beat_from_msh_text(msh, context, backend=backend)
    assert recording_beat.solves
    assert {solve["config"].beat_backend for solve in recording_beat.solves} == {backend}
    assert {solve["config"].regular_quadrature_mode for solve in recording_beat.solves} == {"fixed"}
    assert response["metadata"]["phase_time_convention"] == PHASE_TIME_CONVENTION


def test_adaptive_off_keeps_the_original_single_batch_and_stream(recording_beat, monkeypatch):
    from server.solver.frequency_sweep import live_execution_frequencies

    request = _request()
    request.options.frequencies_hz = np.geomspace(100, 1000, 24).tolist()
    request.options.adaptive_frequency_sampling = False
    seen = []
    native_solve = recording_beat.solve_frequencies

    def record(path, frequencies, config, **kwargs):
        seen.append(list(frequencies))
        return native_solve(path, frequencies, config, **kwargs)

    monkeypatch.setattr(recording_beat, "solve_frequencies", record)
    monkeypatch.setattr(
        beat_imported,
        "solve_native_adaptively",
        lambda *args, **kwargs: pytest.fail("off must not invoke the planner"),
    )
    streamed = []
    outcome = _run(request, _record(), streamed)
    context = SolverContext.from_imported_request(request, quadrants=1234, source_motion="normal")
    assert seen == [live_execution_frequencies(context).tolist()] * 2
    assert len(streamed) == 48
    assert "frequency_status" not in outcome.results["channels"]["left"]


def test_adaptive_imported_batches_publish_replaceable_channel_snapshots(
    recording_beat, monkeypatch
):
    from server.jobs.runtime import merge_provisional_results

    request = _request()
    request.options.frequencies_hz = np.geomspace(100, 1000, 48).tolist()
    request.options.adaptive_frequency_sampling = True
    native_solve = recording_beat.solve_frequencies

    def smooth(path, frequencies, config, **kwargs):
        result = native_solve(path, frequencies, config, **kwargs)
        result.pressure_complex = (
            np.exp(2j * np.pi * np.asarray(frequencies) * 2 / 343)[:, None, None]
            * np.ones((len(frequencies), 1, 3))
            * 20e-6
        )
        result.impedance[:] = 1j * REFERENCE_RHO_C
        return result

    monkeypatch.setattr(recording_beat, "solve_frequencies", smooth)
    streamed = []
    outcome = _run(request, _record(), streamed)
    assert len(recording_beat.solves) >= 6
    merged = None
    for index, delta in streamed:
        assert index == streamed.index((index, delta))
        merged = merge_provisional_results(merged, delta)
    for channel in ["left", "right"]:
        payload = outcome.results["channels"][channel]
        assert payload["frequencies"] == request.options.frequencies_hz
        assert payload["frequency_status"][0] == payload["frequency_status"][-1] == "solved"
        assert "interpolated" in payload["frequency_status"]
        assert merged["channels"][channel]["frequencies"] == request.options.frequencies_hz
    assert merged["frequencies"] == request.options.frequencies_hz


def test_adaptive_signed_cancellation_is_fitted_at_the_summed_pressure_scale(
    recording_beat, monkeypatch
):
    # Review reproduction: S in WG's unit-acceleration convention, with a
    # small, smooth damped response added to the negative axial group.
    from server.tests.test_adaptive_sweep import reference

    f, values = reference()
    values = values / (-2j * np.pi * f[:, None])
    delay = np.exp(2j * np.pi * f * 2 / 343)
    residual = (abs(values[:, :-1]).max() * 1e-4 * 80 / (f - (750 - 80j)) * delay)[
        :, None
    ] * np.ones((1, 37))
    # Independently converged group fits reproduce the cancellation failure.
    from server.solver.adaptive_sweep import SweepPlanner

    independent = []
    for perturbation in [0, residual]:
        group_truth = values.copy()
        group_truth[:, :-1] += perturbation
        planner = SweepPlanner(f, delays_s=np.r_[np.full(37, 2 / 343), 0])
        while len(planner.pending):
            planner.add(planner.pending, group_truth[planner.pending])
        assert len(planner.observed) < len(f)
        assert planner.estimate_db < 0.1
        independent.append(planner.prediction[:, 0])
    group_sum = independent[0] - independent[1]
    assert np.max(abs(20 * np.log10(abs(group_sum) / abs(residual[:, 0])))) > 1
    request = _request(
        drive_channels=[
            {
                "id": "sum",
                "source_ids": ["source-a", "source-b"],
                "motion": "axial",
            }
        ],
        mesh={
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {"source-a": 3.0, "source-b": 3.0},
        },
    )
    request.options.frequencies_hz = f.tolist()
    request.options.adaptive_frequency_sampling = True
    native_solve = recording_beat.solve_frequencies
    batches = {"positive": [], "negative": []}

    def cancelling(path, frequencies, config, **kwargs):
        result = native_solve(path, frequencies, config, **kwargs)
        ids = np.searchsorted(f, frequencies)
        negative = _elements(Path(path).read_text())[2] == beat_imported.VELOCITY_TAG
        batches["negative" if negative else "positive"].append(ids.tolist())
        pressure = values[ids, :-1] + (residual[ids] if negative else 0)
        result.pressure_complex = pressure[:, None, :]
        result.observation_angles_deg = np.arange(37) * 5
        result.impedance = values[ids, -1]
        return result

    monkeypatch.setattr(recording_beat, "solve_frequencies", cancelling)
    streamed = []
    outcome = _run(request, _record(msh_text=MESH_Z), streamed)
    response = outcome.results["channels"]["sum"]
    assert batches["positive"] == batches["negative"]
    truth_db = 20 * np.log10(abs(residual[:, 0]) / 20e-6)
    np.testing.assert_allclose(response["spl_on_axis"]["spl"], truth_db, atol=0.1)
    sampling = response["metadata"]["adaptive_sampling"]
    assert sampling["solved_count"] == sum(map(len, batches["positive"]))
    assert sampling["estimate_db"] < sampling["tolerance_db"]
    assert streamed[-1][1]["channels"]["sum"]["metadata"]["adaptive_sampling"] == sampling


@pytest.mark.parametrize("coverage, signed_groups", [(False, False), (True, False), (True, True)])
def test_adaptive_imported_live_frame_progress_counts_and_channel_intersection(
    recording_beat, monkeypatch, coverage, signed_groups
):
    from server.jobs.runtime import merge_provisional_results
    from server.solver.adaptive_sweep import native_acquisition_frequencies

    request = _request()
    if signed_groups:
        request.geometry.drive_channels[0].motion = "axial"
    request.options.frequencies_hz = (
        np.geomspace(100, 20000, 24).tolist() if coverage else np.geomspace(100, 1000, 48).tolist()
    )
    request.options.adaptive_frequency_sampling = True
    native_solve = recording_beat.solve_frequencies
    progress = []

    def smooth(path, frequencies, config, **kwargs):
        if kwargs.get("status_callback"):
            kwargs["status_callback"]("Starting batch")
        result = native_solve(path, frequencies, config, **kwargs)
        f = np.asarray(frequencies)
        left = _elements(Path(path).read_text())[2] == beat_imported.VELOCITY_TAG
        pole = (250 - 90j) if left else (750 - 90j)
        pressure = np.exp(2j * np.pi * f * 2 / 343) / (f - pole)
        if not left:
            # Single-pole fits agree to roundoff on both channels. Give the
            # right channel real additional structure so acquisition differs
            # independently of sub-1e-12 dB score noise and host tie ordering.
            pressure *= 1 + 0.5 * np.exp(-((f - 650) / 70) ** 2)
        result.pressure_complex = pressure[
            :, None, None
        ] * np.ones((1, 1, 3))
        result.impedance[:] = 1j * REFERENCE_RHO_C
        return result

    monkeypatch.setattr(recording_beat, "solve_frequencies", smooth)
    streamed = []
    outcome = asyncio.run(
        beat.BeatEngine("cpu").run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *args: progress.append(args),
            result_cb=lambda index, payload: streamed.append((index, payload)),
            imported_record=_record(msh_text=MESH_Z)
            if signed_groups
            else _record(frame=SIDEWAYS_FRAME, msh_text=MESH_X),
        )
    )
    fractions = [fraction for stage, fraction, *_ in progress if stage == "frequency_solve"]
    assert len(fractions) > 16
    assert fractions == sorted(fractions)
    assert max(fractions) <= 1
    context = SolverContext.from_imported_request(request, quadrants=1234, source_motion="normal")
    target = len(native_acquisition_frequencies(context)) * (3 if signed_groups else 2)
    solves = [entry for entry in progress if entry[2].startswith("Solving frequency")]
    assert [entry[1] for entry in solves] == pytest.approx(
        [i / target for i in range(1, len(solves) + 1)]
    )
    per_channel = {"left": 0, "right": 0}
    for _, _, message in solves:
        channel = "left" if "(left)" in message else "right"
        per_channel[channel] += 1
        channel_target = len(native_acquisition_frequencies(context)) * (
            2 if signed_groups and channel == "left" else 1
        )
        assert per_channel[channel] <= channel_target
        assert message.startswith(
            f"Solving frequency {per_channel[channel]}/{channel_target} of drive channel "
        )
        assert message.endswith("(native acquisition)")
    if coverage:
        assert target == 47 * (3 if signed_groups else 2)
        assert len(solves) > 24 * (3 if signed_groups else 2)
    merged = None
    selections = {}
    for _, delta in streamed:
        channel_id, payload = next(iter(delta["channels"].items()))
        assert (
            payload["metadata"]["observation_frame_basis"]
            == outcome.results["channels"][channel_id]["metadata"]["observation_frame_basis"]
        )
        if not signed_groups:
            assert delta["metadata"]["observation_frame_basis"]["axis"] == [1, 0, 0]
            assert delta["metadata"]["observation_frame_basis"]["origin_m"] == [0.05, 0.02, 0.03]
        counts = delta["metadata"]["provisional"]
        assert counts["completed_frequency_count"] == payload["frequency_status"].count("solved")
        count = len(request.options.frequencies_hz)
        assert counts["expected_frequency_count"] == count
        selections[channel_id] = payload["frequency_status"]
        expected = [
            "solved"
            if all(
                selections.get(c, ["interpolated"] * count)[i] == "solved"
                for c in ["left", "right"]
            )
            else "interpolated"
            for i in range(count)
        ]
        assert delta["frequency_status"] == expected
        merged = merge_provisional_results(merged, delta)
        assert merged["frequency_status"] == expected
    if not coverage:
        assert streamed[0][1]["metadata"]["provisional"]["completed_frequency_count"] == 8
        assert selections["left"] != selections["right"]
    assert merged["frequency_status"] == outcome.results["frequency_status"]


@pytest.mark.parametrize("parts", [((1, 3), (-1, 2)), ((-1, 3),)])
def test_signed_sum_auxiliary_aggregation_is_only_adaptive(parts):
    """Off preserves base's first-group averages/logs; fits need signed traces."""
    from types import SimpleNamespace

    groups = [
        (
            sign,
            SimpleNamespace(
                pressure_complex=np.full((2, 1, 1), value, dtype=complex),
                impedance=np.full(2, value, dtype=complex),
                surface_pressure_avg={2: np.full(2, value, dtype=complex)},
                solver_log=[{"group": value}],
                timings={"solve": value},
            ),
        )
        for sign, value in parts
    ]
    legacy = beat_imported._signed_sum(groups)
    adaptive = beat_imported._signed_sum(groups, adaptive=True)
    expected = sum(sign * value for sign, value in parts)
    np.testing.assert_array_equal(legacy.pressure_complex, expected)
    np.testing.assert_array_equal(adaptive.pressure_complex, expected)
    assert legacy.surface_pressure_avg is groups[0][1].surface_pressure_avg
    assert legacy.solver_log is groups[0][1].solver_log
    np.testing.assert_array_equal(adaptive.surface_pressure_avg[2], expected)
    assert adaptive.solver_log == [entry for _, group in groups for entry in group.solver_log]
