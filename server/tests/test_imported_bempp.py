"""BEMPP's imported-geometry adapter, against a recording stand-in for the solve.

Everything here runs without an OpenCL device. The package's own
``SolveConfig``, ``ObservationConfig`` and ``ObservationFrame`` are used, so a
misspelt or unsupported option fails here as it would in production; only the
sweep itself is replaced, by a stand-in that records the frame, the drive and
the mirror each sweep is handed. The numerical comparison against Metal and an
analytic reference is the qualification suite's job, and runs only where
OpenCL exists.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from server.contracts.conventions import PHASE_TIME_CONVENTION
from server.engines import registry
from server.jobs.models import SolveRequest
from server.mesh.artifact import ImportedMeshArtifactError
from server.mesh.imported import polar_grid_from_symmetry
from server.solver import bempp, bempp_imported, bempp_process
from server.solver.base import EngineRunResult
from server.solver.bempp import BemppUnavailable
from server.solver.combine import deserialize_channel_bases
from server.solver.imported import mesh_text_sha256
from server.solver.result_mapping import REFERENCE_RHO_C

pytest.importorskip("hornlab_bempp_bem")


MANIFEST_SHA = "sha256:" + "1" * 64
ARTIFACT_SHA = "sha256:" + "2" * 64

#: One rigid face and one face per source tag, all on the positive side so the
#: same mesh can stand for a half or a quarter. The stand-in never assembles it.
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

#: A CAD-authored placement: the horn points along +x. u x v = axis.
SIDEWAYS_FRAME = {
    "axis": [1.0, 0.0, 0.0],
    "u": [0.0, 1.0, 0.0],
    "v": [0.0, 0.0, 1.0],
    "origin_m": [0.05, 0.02, 0.03],
    "mouth_center_m": [0.09, 0.02, 0.03],
    "source_center_m": [0.05, 0.02, 0.03],
}

OPENCL = {
    "available": True,
    "reason": "mock OpenCL CPU device",
    "version": "0.1.1",
    "assembly_backend": "opencl",
}


def _record(
    *, planes: list[str] | None = None, open_edges: int | None = 0, frame: dict[str, Any] | None = None
) -> dict[str, Any]:
    symmetry = {
        "cut_planes": list(planes or []),
        "planes": {name: {"accepted": name in (planes or [])} for name in ("x0", "y0", "z0")},
    }
    integrity: dict[str, Any] = {"orientation_valid": True}
    if open_edges is not None:
        integrity["off_plane_open_edge_count"] = open_edges
    return {
        "manifest_sha256": MANIFEST_SHA,
        "artifact_sha256": ARTIFACT_SHA,
        "mesh_content_sha256": mesh_text_sha256(MESH),
        "_execution_msh_text": MESH,
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
        "anchor": {"instance_id": "i", "design_id": None, "throat_frame": dict(frame or SIDEWAYS_FRAME)},
        "symmetry": symmetry,
        "polar_grid_derivation": polar_grid_from_symmetry(symmetry),
        "mesh": {"stats": {"triangle_count": 4, "vertex_count": 4}, "metadata": {}, "integrity": integrity},
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
                "engine": "bempp",
                "frequencies_hz": [100.0, 500.0, 1000.0],
                "polar_config": {"angle_range": [-180.0, 180.0, 37]},
            },
        }
    )


class _RecordingBempp:
    """Stands in for ``hornlab_bempp_bem``; records what each sweep receives."""

    def __init__(self, *, silent: bool = False) -> None:
        self.solves: list[dict[str, Any]] = []
        self.silent = silent

    def solve_frequencies(self, path: str, frequencies: list[float], config: Any) -> SimpleNamespace:
        scale = 0.0 if self.silent else float(len(self.solves) + 1)
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
        )
        for index, frequency in enumerate(frequencies):
            config.progress_callback(index, count, frequency)
            callback = getattr(config, "on_frequency_result", None)
            if callback is not None:
                callback(
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


def _install(monkeypatch: pytest.MonkeyPatch, package: _RecordingBempp, status: dict[str, Any] | None = None) -> None:
    # The real configuration classes; only the sweep and the probe stand in.
    assert bempp._load_api(), "hornlab-bempp-bem must import for these tests"
    monkeypatch.setattr(bempp, "bempp_solve_frequencies", package.solve_frequencies)
    monkeypatch.setattr(bempp, "bempp_status", lambda: dict(status or OPENCL))


@pytest.fixture
def recording_bempp(monkeypatch: pytest.MonkeyPatch) -> _RecordingBempp:
    package = _RecordingBempp()
    _install(monkeypatch, package)
    return package


def _solve(request: SolveRequest, record: dict[str, Any], streamed: list[Any] | None = None) -> dict[str, Any]:
    def result_cb(index: int, payload: dict[str, Any]) -> None:
        if streamed is not None:
            streamed.append((index, payload))

    return bempp_imported.solve_imported_bempp_from_msh_text(
        MESH, request, record, result_callback=result_cb
    )


def test_each_channel_is_one_sweep_driving_its_own_tags_in_the_records_frame(
    recording_bempp: _RecordingBempp,
) -> None:
    envelope = _solve(_request(), _record(planes=["x0"]))

    first, second = (solve["config"] for solve in recording_bempp.solves)
    # Every member source of a channel at unit weight -- Metal's drive -- and
    # each channel with its own motion.
    assert first.velocity_sources == {101: 1.0, 102: 1.0}
    assert second.velocity_sources == {103: 1.0}
    assert getattr(first, "source_motion", "normal") == "normal"
    assert getattr(second, "source_motion", "normal") == "normal"
    assert getattr(second, "source_axes", None) is None
    for config in (first, second):
        assert list(config.frame_override.axis) == pytest.approx([1.0, 0.0, 0.0])
        assert list(config.frame_override.origin) == pytest.approx([0.05, 0.02, 0.03])
        assert list(config.frame_override.mouth_center) == pytest.approx([0.09, 0.02, 0.03])
        assert config.native_symmetry_plane == "yz"
        assert config.assembly_backend == "opencl"
        assert config.workers == 1
        # Metal re-checks the executed mesh for a free rim; so does BEMPP.
        assert config.require_closed_mesh is True
    assert recording_bempp.solves[0]["text"] == MESH

    assert envelope["result_kind"] == "multi_channel"
    assert all(
        channel["metadata"]["phase_time_convention"] == PHASE_TIME_CONVENTION
        for channel in envelope["channels"].values()
    )
    assert envelope["channel_order"] == ["left", "right"]
    engine = envelope["metadata"]["solver_engine"]
    assert (engine["engine"], engine["assembly_backend"]) == ("bempp", "opencl")
    assert "complex" in str(engine["formulation"]).lower()
    assert envelope["metadata"]["observation_frame_basis"]["axis"] == [1.0, 0.0, 0.0]
    assert envelope["metadata"]["symmetry_planes_used"] == ["x0"]
    # A multi-source channel has no single impedance; a single-source one keeps it.
    assert "impedance" not in envelope["channels"]["left"]
    assert "impedance" in envelope["channels"]["right"]
    bases = deserialize_channel_bases(envelope["_channel_bases_npz"])
    assert bases["channel_ids"] == ["left", "right"]
    assert np.asarray(bases["results_by_id"]["right"].pressure_complex) == pytest.approx(
        2.0 * np.asarray(bases["results_by_id"]["left"].pressure_complex)
    )


def _axial_request() -> SolveRequest:
    return _request(
        drive_channels=[
            {"id": "left", "source_ids": ["source-a", "source-b"]},
            {"id": "right", "source_ids": ["source-c"], "motion": "axial"},
        ]
    )


def _real_config_has_source_axes() -> bool:
    import dataclasses

    config = getattr(bempp, "SolveConfig", None)
    if config is None:
        try:
            bempp._load_api()
        except Exception:  # noqa: BLE001 - only a probe
            return False
        config = getattr(bempp, "SolveConfig", None)
    try:
        return "source_axes" in {field.name for field in dataclasses.fields(config)}
    except TypeError:
        return False


@pytest.mark.skipif(
    not _real_config_has_source_axes(),
    reason="installed hornlab-bempp-bem lacks SolveConfig.source_axes",
)
def test_an_axial_channel_hands_the_package_its_explicit_axes_on_the_real_config(
    recording_bempp: _RecordingBempp,
) -> None:
    _solve(_axial_request(), _record())

    first, second = (solve["config"] for solve in recording_bempp.solves)
    assert getattr(first, "source_axes", None) is None
    assert second.source_motion == "axial"
    assert set(second.source_axes) == {103}


def _stub_config(monkeypatch: pytest.MonkeyPatch, *, accepts_axes: bool = True) -> None:
    """A permissive SolveConfig, for what the adapter passes, not what the module allows."""

    def config(**kwargs: Any) -> SimpleNamespace:
        if not accepts_axes and "source_axes" in kwargs:
            raise TypeError("SolveConfig.__init__() got an unexpected keyword argument 'source_axes'")
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(bempp, "SolveConfig", config)


def test_an_axial_channel_passes_one_explicit_snapped_axis_per_source_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _RecordingBempp()
    _install(monkeypatch, package)
    _stub_config(monkeypatch)

    envelope = _solve(_axial_request(), _record())

    first, second = (solve["config"] for solve in package.solves)
    assert not hasattr(first, "source_axes")
    assert second.source_motion == "axial"
    (tag,) = second.source_axes
    assert tag == 103
    axis = np.asarray(second.source_axes[103])
    # MESH's tag 103 is a tilted face: its axis is its unit net area vector,
    # not a frame axis, and needs no sign vote.
    net = np.asarray([-0.0004, -0.0002, 0.0002]) * 0 + _net_area_vector(MESH, 103)
    np.testing.assert_allclose(axis, net / np.linalg.norm(net), atol=1e-12)
    right = envelope["channels"]["right"]["metadata"]
    assert right["source_motion"] == "axial"
    assert right["axial_contract"] == "per-source-axis-v2"
    assert right["source_axes"][0]["tag"] == 103
    assert right["source_axes"][0]["axis"] == pytest.approx(list(axis))
    assert "axial_contract" not in envelope["channels"]["left"]["metadata"]
    stored = json.loads(str(np.load(io.BytesIO(envelope["_channel_bases_npz"]))["metadata::right"].item()))
    assert stored["axial_contract"] == "per-source-axis-v2" and stored["source_motion"] == "axial"


def _net_area_vector(msh_text: str, tag: int) -> np.ndarray:
    from server.solver.imported import _msh_triangles

    coordinates, corners, tags = _msh_triangles(msh_text)
    points = coordinates[corners[tags == tag]]
    return 0.5 * np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]).sum(axis=0)


def test_a_module_without_source_axes_refuses_and_never_falls_back_to_the_frame_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _RecordingBempp()
    _install(monkeypatch, package)
    _stub_config(monkeypatch, accepts_axes=False)

    with pytest.raises(BemppUnavailable, match="does not support per-source axial axes"):
        _solve(_axial_request(), _record())

    # Only the normal channel ran; nothing was driven along a frame axis.
    assert all(getattr(solve["config"], "source_motion", "normal") == "normal" for solve in package.solves)
    package.solves.clear()
    # A drive with no axial channel does not need the option at all.
    _solve(_request(), _record())
    assert len(package.solves) == 2


def test_a_mirror_cut_source_axis_is_projected_and_an_uncut_tilted_one_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _RecordingBempp()
    _install(monkeypatch, package)
    _stub_config(monkeypatch)

    # Tag 103 has an edge on x = 0, so the x0 mirror cuts it: its axis loses the
    # x component. (Node 4 moves onto the plane to make that edge.)
    cut = MESH.replace("4 0.01 0.02 0.03", "4 0 0.02 0.03")
    cut_record = _record(planes=["x0"])
    cut_record["_execution_msh_text"] = cut
    cut_record["mesh_content_sha256"] = mesh_text_sha256(cut)
    bempp_imported.solve_imported_bempp_from_msh_text(cut, _axial_request(), cut_record)
    (config,) = [solve["config"] for solve in package.solves if getattr(solve["config"], "source_axes", None)]
    assert config.source_axes[103][0] == 0.0

    # A source wholly off the plane, tilted across it, is not: the reduction is
    # refused, never projected.
    off_plane = MESH.replace("2 0 0.02 0", "2 0.01 0.02 0").replace("3 0 0 0.03", "3 0.02 0 0.03").replace(
        "4 0.01 0.02 0.03", "4 0.03 0.02 0.03"
    )
    record = _record(planes=["x0"])
    record["_execution_msh_text"] = off_plane
    record["mesh_content_sha256"] = mesh_text_sha256(off_plane)
    with pytest.raises(ValueError, match="not in the symmetry"):
        bempp_imported.solve_imported_bempp_from_msh_text(off_plane, _axial_request(), record)


@pytest.mark.parametrize(
    ("planes", "native"),
    [([], None), (["x0"], "yz"), (["y0"], "xz"), (["x0", "y0"], "yz+xz")],
)
def test_every_cut_set_runs_on_the_packages_own_mirror(
    recording_bempp: _RecordingBempp, planes: list[str], native: str | None
) -> None:
    _solve(_request(), _record(planes=planes))

    assert {solve["config"].native_symmetry_plane for solve in recording_bempp.solves} == {native}


@pytest.mark.parametrize(
    ("open_edges", "expected"),
    [(0, None), (3, "3 open edges off its mirror planes"), (1, "1 open edge off"), (None, "no open-edge count")],
)
def test_a_free_rim_or_a_record_that_cannot_show_it_has_none_is_refused_before_a_job(
    open_edges: int | None, expected: str | None
) -> None:
    record = _record(open_edges=open_edges)

    refusal = bempp_imported.imported_bempp_preflight(record)

    if expected is None:
        assert refusal is None
    else:
        assert refusal is not None and expected in refusal
    assert bempp.BemppEngine().imported_preflight(record, MESH) == refusal


def test_a_free_rim_is_refused_at_the_solve_as_well(recording_bempp: _RecordingBempp) -> None:
    with pytest.raises(BemppUnavailable, match="open edges off its mirror planes"):
        _solve(_request(), _record(open_edges=2))

    assert recording_bempp.solves == []


def test_a_host_that_would_assemble_on_numba_neither_offers_nor_solves_imported_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _RecordingBempp()
    _install(monkeypatch, package, status={**OPENCL, "assembly_backend": "numba"})

    assert "imported" not in bempp.geometry_sources_for({"assembly_backend": "numba"})
    assert "imported" not in bempp.geometry_sources_for({})
    assert "imported" in bempp.geometry_sources_for({"assembly_backend": "opencl"})
    with pytest.raises(BemppUnavailable, match="OpenCL device only"):
        _solve(_request(), _record())
    assert package.solves == []


@pytest.mark.parametrize(("backend", "declares"), [("opencl", True), ("numba", False), (None, False)])
def test_the_registry_declares_imported_bempp_from_its_probe(
    monkeypatch: pytest.MonkeyPatch, backend: str | None, declares: bool
) -> None:
    monkeypatch.setattr(
        bempp, "bempp_status", lambda: {**OPENCL, "assembly_backend": backend}
    )

    info = next(item for item in registry.detect_engines(environ={}) if item.name == "bempp")

    assert ("imported" in info.geometry_sources) is declares
    assert info.imported_features == ()


def test_an_all_zero_opencl_result_is_refused_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _RecordingBempp(silent=True))

    with pytest.raises(BemppUnavailable, match="entirely silent"):
        _solve(_request(), _record())


def test_streamed_frames_count_each_channel_and_carry_the_axis_once(
    recording_bempp: _RecordingBempp,
) -> None:
    streamed: list[Any] = []

    _solve(_request(), _record(), streamed)

    revisions = [index for index, _frame in streamed]
    assert revisions == list(range(6))
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for _index, frame in streamed:
        (channel_id,) = frame["channels"]
        by_channel.setdefault(channel_id, []).append(frame)
    for position, channel_id in enumerate(["left", "right"], start=1):
        frames = by_channel[channel_id]
        assert [f["metadata"]["provisional"]["completed_frequency_count"] for f in frames] == [1, 2, 3]
        assert {f["metadata"]["provisional"]["expected_frequency_count"] for f in frames} == {3}
        assert {f["metadata"]["provisional"]["channel"]["index"] for f in frames} == {position}
    assert all("frequencies" in frame for frame in by_channel["left"])
    assert not any("frequencies" in frame for frame in by_channel["right"])


def test_the_ground_plane_stays_refused(recording_bempp: _RecordingBempp) -> None:
    grounded = _request()
    grounded.options.ground_plane.enabled = True
    with pytest.raises(BemppUnavailable, match="ground plane"):
        _solve(grounded, _record())
    assert recording_bempp.solves == []


def test_the_passive_cardioid_campaign_is_refused_on_bempp(recording_bempp: _RecordingBempp) -> None:
    request = _request(
        drive_channels=[{"id": "left", "source_ids": ["source-a", "source-b", "source-c"]}],
        passive_cardioid_rear_volume_l=6.0,
        passive_cardioid_port_length_mm=25.0,
        model_port_area_m2=0.05,
        bem_port_area_m2=0.009471859930646809,
        port_area_source="user",
        passive_cardioid_foam_resistance_pa_s_m3=10_000.0,
    )

    with pytest.raises(BemppUnavailable, match="passive cardioid"):
        _solve(request, _record())
    assert recording_bempp.solves == []


def test_the_engine_routes_an_imported_request_through_the_killable_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_worker(msh_text: str, request: Any, record: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(msh_text=msh_text, request=request, record=record, kwargs=kwargs)
        return {"metadata": {}, "_channel_bases_npz": b"npz", "_field_traces": None}

    monkeypatch.setattr(bempp_process, "solve_imported_bempp_in_process", fake_worker)
    artifacts: list[Any] = []

    async def artifact_cb(text: str, stats: Any) -> None:
        artifacts.append((text, stats))

    outcome = asyncio.run(
        bempp.BemppEngine().run(
            _request(),
            cancel_cb=lambda: None,
            stage_cb=lambda *_: None,
            artifact_cb=artifact_cb,
            imported_record=_record(),
        )
    )

    assert isinstance(outcome, EngineRunResult)
    # The verified record mesh, never a re-mesh; and not shipped twice.
    assert seen["msh_text"] == MESH
    assert "_execution_msh_text" not in seen["record"]
    assert artifacts == [(MESH, {"triangle_count": 4, "vertex_count": 4})]
    assert outcome.channel_bases == b"npz"
    assert "_channel_bases_npz" not in outcome.results


def test_a_tampered_record_mesh_is_refused_before_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    reached: list[str] = []

    async def fake_worker(msh_text: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        reached.append(msh_text)
        return {"metadata": {}}

    monkeypatch.setattr(bempp_process, "solve_imported_bempp_in_process", fake_worker)
    record = {**_record(), "_execution_msh_text": MESH.replace("0.01 0 0", "0.02 0 0", 1)}

    with pytest.raises(ImportedMeshArtifactError, match="digest mismatch"):
        asyncio.run(
            bempp.BemppEngine().run(
                _request(), cancel_cb=lambda: None, stage_cb=lambda *_: None, imported_record=record
            )
        )
    assert reached == []


def test_the_worker_dispatches_an_imported_payload_to_the_imported_solve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def imported(msh_text: str, request: Any, record: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("imported")
        assert (msh_text, request, record) == ("m", "q", {"r": 1})
        assert set(kwargs) == {"field_trace_cap_bytes", "stage_callback", "result_callback"}
        return {"kind": "imported"}

    def design(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("design")
        return {"kind": "design"}

    monkeypatch.setattr(bempp_imported, "solve_imported_bempp_from_msh_text", imported)
    monkeypatch.setattr(bempp, "solve_bempp_from_msh_text", design)

    def noop(*_args: Any) -> None:
        return None

    assert bempp_process._solve_payload(
        {"kind": "imported", "msh_text": "m", "request": "q", "record": {"r": 1}}, stage=noop, result=noop
    ) == {"kind": "imported"}
    assert bempp_process._solve_payload({"msh_text": "m", "context": "c"}, stage=noop, result=noop) == {
        "kind": "design"
    }
    assert calls == ["imported", "design"]


def test_a_module_lacking_source_axes_is_refused_up_front_before_any_channel_solves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses

    @dataclasses.dataclass
    class OldConfig:
        freq_min_hz: float = 0.0

    package = _RecordingBempp()
    _install(monkeypatch, package)
    monkeypatch.setattr(bempp, "SolveConfig", OldConfig)
    channels = _axial_request().geometry.drive_channels
    reason = "Installed hornlab-bempp-bem does not support per-source axial axes"

    assert reason in str(bempp.BemppEngine().imported_preflight(_record(), MESH, drive_channels=channels))
    # No axial channel, nothing to refuse.
    assert bempp.BemppEngine().imported_preflight(_record(), MESH, drive_channels=_request().geometry.drive_channels) is None
    assert bempp.BemppEngine().imported_preflight(_record(), MESH) is None
    with pytest.raises(BemppUnavailable, match=reason):
        _solve(_axial_request(), _record())
    # The normal channel listed first did not solve either.
    assert package.solves == []


def test_a_source_that_only_touches_the_mirror_at_a_vertex_is_not_projected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MESH's tag 103 has one node on x = 0 and a tilted face: it is a whole
    source elsewhere, so its across-mirror axis refuses the reduction."""

    _install(monkeypatch, _RecordingBempp())
    _stub_config(monkeypatch)
    with pytest.raises(ValueError, match="not in the symmetry"):
        bempp_imported.solve_imported_bempp_from_msh_text(
            MESH, _axial_request(), _record(planes=["x0"])
        )
