from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from server.cadlink.store import CadLinkStore
from server.jobs.models import (
    ChannelCombineSpec,
    ImportedGeometrySource,
    JobStatusResponse,
    SolveRequest,
)
from server.jobs.api import create_jobs_router
from server.jobs.runtime import ImportedSolveRefusal, JobRuntime, _replay_request
from server.jobs.store import JobStore
from server.mesh.imported import polar_grid_from_symmetry
from server.solver import metal
from server.solver.base import EngineRunResult
from server.solver.imported import (
    ImportedSymmetryUnsupportedError,
    mesh_text_sha256,
)
from server.solver.result_mapping import REFERENCE_RHO_C


MANIFEST_SHA = "sha256:" + "1" * 64
ARTIFACT_SHA = "sha256:" + "2" * 64
REPORT_SHA = "sha256:" + "3" * 64


def _geometry(ingest_id: str) -> dict[str, Any]:
    return {
        "type": "imported",
        "ingest_id": ingest_id,
        "manifest_sha256": MANIFEST_SHA,
        "artifact_sha256": ARTIFACT_SHA,
        "drive_channels": [
            {"id": "left", "source_ids": ["source-a", "source-b"]},
            {"id": "right", "source_ids": ["source-c"]},
        ],
        "mesh": {
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {
                "source-a": 3.0,
                "source-b": 3.0,
                "source-c": 4.0,
            },
        },
    }


def _request(ingest_id: str, **geometry_changes: Any) -> SolveRequest:
    geometry = _geometry(ingest_id)
    geometry.update(geometry_changes)
    return SolveRequest.model_validate(
        {
            "geometry": geometry,
            "options": {
                "engine": "metal",
                "frequencies_hz": [100.0, 500.0, 1000.0],
                "polar_config": {"angle_range": [-180.0, 180.0, 37]},
            },
        }
    )


def test_geometry_union_round_trip_and_legacy_rewrite() -> None:
    imported = _request("wgi_" + "0" * 26)
    assert isinstance(imported.geometry, ImportedGeometrySource)
    assert SolveRequest.model_validate(imported.model_dump(mode="json")) == imported

    legacy = SolveRequest.model_validate(
        {"design": {"formula": "OSSE", "L": 120, "a": 40}, "design_revision": 7}
    )
    dumped = legacy.model_dump(mode="json")
    assert dumped["geometry"]["type"] == "parametric"
    assert dumped["geometry"]["design_revision"] == 7
    assert "design" not in dumped

    with pytest.raises(ValidationError, match="cannot be combined"):
        SolveRequest.model_validate(
            {"geometry": dumped["geometry"], "design": dumped["geometry"]["design"]}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SolveRequest.model_validate(
            {
                "geometry": {
                    **_geometry("wgi_" + "0" * 26),
                    "design": dumped["geometry"]["design"],
                },
                "options": {"frequencies_hz": [100, 200]},
            }
        )


def test_passive_cardioid_request_fields_are_additive_and_keep_areas_distinct() -> None:
    request = _request(
        "wgi_" + "0" * 26,
        passive_cardioid_rear_volume_l=6.0,
        passive_cardioid_port_length_mm=25.0,
        model_port_area_m2=0.05,
        bem_port_area_m2=0.009471859930646809,
        port_area_source="user",
        passive_cardioid_foam_resistance_pa_s_m3=10_000.0,
        passive_cardioid_invert_port=True,
        passive_cardioid_coupled=True,
    )
    geometry = request.geometry
    assert isinstance(geometry, ImportedGeometrySource)
    assert geometry.passive_cardioid_enabled is True
    assert geometry.model_port_area_m2 == 0.05
    assert geometry.bem_port_area_m2 == 0.009471859930646809
    assert request.model_dump(mode="json")["geometry"]["passive_cardioid_coupled"] is True

    with pytest.raises(ValidationError, match="requires model_port_area_m2 to equal"):
        _request(
            "wgi_" + "0" * 26,
            passive_cardioid_rear_volume_l=6.0,
            passive_cardioid_port_length_mm=25.0,
            model_port_area_m2=0.05,
            bem_port_area_m2=0.01,
            port_area_source="bem_aperture",
            passive_cardioid_foam_resistance_pa_s_m3=0.0,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("passive_cardioid_port_length_mm", 25.0),
        ("model_port_area_m2", 0.05),
        ("bem_port_area_m2", 0.01),
        ("port_area_source", "user"),
        ("passive_cardioid_foam_resistance_pa_s_m3", 10_000.0),
        ("passive_cardioid_invert_port", False),
        ("passive_cardioid_coupled", True),
    ],
)
def test_passive_cardioid_fields_are_rejected_when_disabled(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError, match="require passive_cardioid_rear_volume_l"):
        _request("wgi_" + "0" * 26, **{field: value})


def test_coupled_cardioid_reserves_derived_channel_id() -> None:
    geometry = _geometry("wgi_" + "0" * 26)
    geometry["drive_channels"][0]["id"] = "passive_cardioid"
    geometry.update(
        {
            "passive_cardioid_rear_volume_l": 6.0,
            "passive_cardioid_port_length_mm": 25.0,
            "model_port_area_m2": 0.05,
            "bem_port_area_m2": 0.01,
            "port_area_source": "user",
            "passive_cardioid_foam_resistance_pa_s_m3": 10_000.0,
            "passive_cardioid_coupled": True,
        }
    )

    with pytest.raises(ValidationError, match="reserved for coupled output"):
        SolveRequest.model_validate(
            {"geometry": geometry, "options": {"frequencies_hz": [100, 200]}}
        )


def test_coupled_cardioid_refuses_combine_containing_mf_channel() -> None:
    """The MF basis is unit-acceleration under a coupled campaign, so a
    crossover naming it would sum two different drive domains."""

    mf_channel = SimpleNamespace(id="mf")
    combine = SimpleNamespace(members=["mf", "hf"])

    refusal = metal._cardioid_combine_refusal(mf_channel, combine)

    assert refusal is not None
    assert "'mf'" in refusal
    assert "passive_cardioid" in refusal


def test_coupled_cardioid_allows_a_combine_that_omits_the_mf_channel() -> None:
    """Other driver-bearing channels are scaled normally, so refusing them too
    would reject a configuration that is physically fine."""

    mf_channel = SimpleNamespace(id="mf")

    assert (
        metal._cardioid_combine_refusal(
            mf_channel, SimpleNamespace(members=["hf", "superhf"])
        )
        is None
    )
    # No coupled campaign, or no crossover at all, is likewise nothing to refuse.
    assert metal._cardioid_combine_refusal(None, SimpleNamespace(members=["mf"])) is None
    assert metal._cardioid_combine_refusal(mf_channel, None) is None


def test_passive_cardioid_aperture_mapping_resolves_imported_lr_names() -> None:
    aperture_tags, port_names, mf_source_id = metal._passive_cardioid_apertures(
        {"PORT_EXIT_L": 10, "PORT_EXIT_R": 11, "source-mf": 101},
        {"sources": [{"id": "source-mf", "role": "MF"}]},
    )

    assert aperture_tags == {
        "PORT_EXIT_L": [10],
        "PORT_EXIT_R": [11],
        "MF": [101],
    }
    assert port_names == ["PORT_EXIT_L", "PORT_EXIT_R"]
    assert mf_source_id == "source-mf"


def test_frequency_reconciliation_rejects_same_length_different_values() -> None:
    with pytest.raises(ValueError, match="grids cannot be reconciled"):
        metal._frequency_value_indices(
            np.asarray([100.0, 200.0]),
            np.asarray([100.0, 250.0]),
            consumer_name="consumer",
            requested_name="campaign",
        )


def test_passive_cardioid_campaign_reconciles_grid_and_writes_face_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @dataclass(frozen=True)
    class Config:
        progress_callback: Any = None
        on_frequency_result: Any = None
        source_velocity_profiles: Any = None

    calls: dict[str, Any] = {}

    def solve_matrix(
        _mesh: Path,
        aperture_tags: dict[str, list[int]],
        frequencies_hz: np.ndarray,
        config: Config,
    ) -> Any:
        calls["aperture_tags"] = aperture_tags
        calls["frequencies_hz"] = frequencies_hz.copy()
        for index, frequency in enumerate(frequencies_hz):
            config.progress_callback(index, len(frequencies_hz), float(frequency))
        count = len(frequencies_hz)
        matrix = np.zeros((count, 2, 2), dtype=np.complex128)
        matrix[:, 0, 0] = 10.0 + 1.0j
        matrix[:, 1, 1] = 20.0 + 2.0j
        matrix[:, 0, 1] = matrix[:, 1, 0] = 3.0 + 0.5j
        return metal.radiation_impedance.RadiationImpedanceResult(
            frequencies_hz=np.asarray(frequencies_hz, dtype=np.float64),
            aperture_names=["PORT_EXIT", "MF"],
            aperture_area_m2={"PORT_EXIT": 0.01, "MF": 0.02},
            impedance_matrix=matrix,
            solver_logs=[],
        )

    monkeypatch.setattr(
        metal.radiation_impedance, "solve_aperture_matrix", solve_matrix
    )
    geometry = _request(
        "wgi_" + "0" * 26,
        passive_cardioid_rear_volume_l=6.0,
        passive_cardioid_port_length_mm=25.0,
        model_port_area_m2=0.05,
        bem_port_area_m2=0.01,
        port_area_source="user",
        passive_cardioid_foam_resistance_pa_s_m3=10_000.0,
    ).geometry
    assert isinstance(geometry, ImportedGeometrySource)
    record = {
        "source_tags": {"PORT_EXIT": 10, "source-mf": 101},
        "sources": [{"id": "source-mf", "role": "MF"}],
        "mesh": {
            "metadata": {
                "mesh_frequency_validation": {
                    "per_source": {
                        "source-mf": {"effective_max_valid_frequency_hz": 600.0}
                    }
                }
            }
        },
    }
    stages: list[tuple[str, float, str]] = []
    cancellations = 0

    def cancel() -> None:
        nonlocal cancellations
        cancellations += 1

    campaign = metal._run_passive_cardioid_campaign(
        tmp_path / "mesh.msh",
        Config(),
        geometry,
        record,
        np.asarray([100.0, 500.0, 1000.0]),
        stage_callback=lambda *args: stages.append(args),
        cancellation_callback=cancel,
    )

    assert calls["aperture_tags"] == {"PORT_EXIT": [10], "MF": [101]}
    assert calls["frequencies_hz"].tolist() == [100.0, 500.0]
    assert campaign["consumer_indices"].tolist() == [0, 1]
    assert cancellations == 4
    assert {stage for stage, _fraction, _message in stages} == {
        "radiation_impedance"
    }
    with np.load(BytesIO(campaign["artifact"]), allow_pickle=False) as data:
        assert set(data.files) == {
            "frequencies_hz",
            "aperture_names",
            "aperture_area_m2",
            "aperture_tag",
            "solver_impedance_matrix",
            "engineering_impedance_matrix",
            "in_phase_termination_load",
            "in_phase_aperture_names",
            "reciprocity_max_abs",
            "reciprocity_max_rel",
            "passivity_min_eig",
            "passivity_min_eig_reciprocal",
            "passivity_ok",
        }
        # READ every value, do not merely list the names. np.savez accepts a
        # dict and stores it as a 0-d object array; the NAME then appears in
        # data.files exactly like a real array, and only the read fails with
        # "Object arrays cannot be loaded when allow_pickle=False". A names-only
        # assertion therefore stays green on an archive no consumer can open --
        # which is precisely what shipped before this loop existed. Every
        # consumer reads with allow_pickle=False, so every key must survive it,
        # and this guards additions nobody has written yet.
        for name in data.files:
            value = data[name]
            assert value.dtype != object, f"{name} is an object array"
        assert data["aperture_names"].tolist() == ["PORT_EXIT", "MF"]
        assert data["aperture_tag"].tolist() == [10, 101]
        # The in-phase reduction covers the ports only; MF contributes mutual
        # columns to the matrix but is not part of the port-only load.
        assert data["in_phase_aperture_names"].tolist() == ["PORT_EXIT"]
def test_legacy_top_level_parametric_wire_is_accepted_through_http_api() -> None:
    class CapturingRuntime:
        def __init__(self) -> None:
            self.request: SolveRequest | None = None

        async def start(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        async def submit(self, request: SolveRequest) -> str:
            self.request = request
            return "job-legacy"

    runtime = CapturingRuntime()
    app = FastAPI()
    app.include_router(create_jobs_router(runtime))  # type: ignore[arg-type]
    payload = json.dumps(
        {
            "design": {"formula": "OSSE", "L": 120, "a": 40},
            "design_revision": 4,
            "options": {"engine": "dryrun"},
        }
    ).encode("utf-8")

    async def post() -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/solve",
                "raw_path": b"/api/solve",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"127.0.0.1"),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 80),
            },
            receive,
            send,
        )
        start = next(item for item in sent if item["type"] == "http.response.start")
        body = b"".join(
            item.get("body", b"")
            for item in sent
            if item["type"] == "http.response.body"
        )
        return int(start["status"]), json.loads(body)

    status, body = asyncio.run(post())
    assert status == 200
    assert body == {"job_id": "job-legacy"}
    assert runtime.request is not None
    assert runtime.request.geometry.type == "parametric"
    assert runtime.request.design_revision == 4


def test_solve_request_design_accessor_is_deliberate() -> None:
    parametric = SolveRequest.model_validate(
        {"design": {"formula": "OSSE", "L": 120, "a": 40}}
    )
    assert parametric.design is parametric.geometry.design

    imported = _request("wgi_" + "0" * 26)
    with pytest.raises(
        AttributeError,
        match="geometry type 'imported'.*request.geometry.*ImportedGeometrySource",
    ):
        _ = imported.design
    assert getattr(imported, "design", None) is None


def test_polar_derivation_maps_only_solver_observation_axes() -> None:
    derivation = polar_grid_from_symmetry(
        {
            "planes": {
                "x0": {"accepted": True},
                "y0": {"accepted": False},
                "z0": {"accepted": True},
            },
            "cut_planes": ["x0"],
        }
    )
    assert set(derivation["axes"]) == {"horizontal", "vertical", "diagonal"}
    assert derivation["axes"]["horizontal"]["minimum_deg"] == 0.0
    assert derivation["axes"]["vertical"]["minimum_deg"] == -180.0
    assert derivation["axes"]["diagonal"]["minimum_deg"] == -180.0
    assert "z" not in derivation["axes"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "drive_channels": [
                    {"id": "same", "source_ids": ["source-a", "source-b"]},
                    {"id": "same", "source_ids": ["source-c"]},
                ]
            },
            "channel ids must be unique",
        ),
        (
            {
                "drive_channels": [
                    {"id": "a", "source_ids": ["source-a", "source-b"]},
                    {"id": "b", "source_ids": ["source-b", "source-c"]},
                ]
            },
            "exactly one drive channel",
        ),
        (
            {
                "mesh": {
                    "rigid_size_mm": 8,
                    "transition_mm": 20,
                    "source_size_mm": {"source-a": 3, "source-b": 3},
                }
            },
            "cover exactly",
        ),
        (
            {
                "drive_channels": [
                    {"id": "a", "source_ids": ["source-a"], "motion": "radial"}
                ],
                "mesh": {
                    "rigid_size_mm": 8,
                    "transition_mm": 20,
                    "source_size_mm": {"source-a": 3},
                },
            },
            "normal.*axial",
        ),
    ],
)
def test_imported_channel_and_mesh_validation(change: dict[str, Any], message: str) -> None:
    geometry = _geometry("wgi_" + "0" * 26)
    geometry.update(change)
    with pytest.raises(ValidationError, match=message):
        SolveRequest.model_validate(
            {"geometry": geometry, "options": {"frequencies_hz": [100, 200]}}
        )


def _record(mesh_path: Path, *, findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msh_text = mesh_path.read_text(encoding="utf-8")
    symmetry = {
        "cut_planes": ["x0"],
        "planes": {
            "x0": {"accepted": True},
            "y0": {"accepted": False},
            "z0": {"accepted": False},
        },
    }
    return {
        "manifest_sha256": MANIFEST_SHA,
        "artifact_sha256": ARTIFACT_SHA,
        "report_sha256": REPORT_SHA,
        "mesh_store_path": str(mesh_path),
        "mesh_cache_key": "4" * 64,
        "mesh_content_sha256": mesh_text_sha256(msh_text),
        "mesh_sizes": _geometry("wgi_" + "0" * 26)["mesh"],
        "skipped_source_ids": [],
        "sources": [
            {"id": "source-a", "required": True},
            {"id": "source-b", "required": True},
            {"id": "source-c", "required": False},
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
            "throat_frame": {
                "axis": [0.0, 0.0, 1.0],
                "origin_m": [0.0, 0.08, 0.0],
                "u": [1.0, 0.0, 0.0],
                "v": [0.0, 1.0, 0.0],
                "mouth_center_m": [0.0, 0.08, 0.0],
                "source_center_m": [0.0, 0.08, 0.0],
            },
        },
        "symmetry": symmetry,
        "polar_grid_derivation": polar_grid_from_symmetry(symmetry),
        "mesh": {
            "stats": {"triangle_count": 3},
            "metadata": {
                "mesh_frequency_validation": {
                    "frequency_policy": "global_warn_source_hard",
                    "global_max_valid_frequency_hz": 800.0,
                    "per_source": {
                        source_id: {"effective_max_valid_frequency_hz": 1200.0}
                        for source_id in ("source-a", "source-b", "source-c")
                    },
                }
            },
        },
        "evidence": {"fem_air_volumes": []},
        "findings": findings or [],
    }


def _roled_record(mesh_path: Path) -> dict[str, Any]:
    """A record whose sources carry driver bands, a name, and a rigid role.

    ``left`` drives source-a and source-b, ``right`` drives source-c, so this
    exercises a multi-source channel taking the first band it finds beside a
    structural role that names no driver.
    """

    record = _record(mesh_path)
    record["sources"] = [
        {"id": "source-a", "required": True, "role": "hf", "label": "HF throat"},
        {"id": "source-b", "required": True, "role": "rigid"},
        {"id": "source-c", "required": False, "role": "LF"},
    ]
    return record


def _identity_record_changes() -> dict[str, Any]:
    matrix = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return {
        "sources": [
            {"id": "source-a", "required": True, "instance_id": "instance-a"},
            {"id": "source-b", "required": True, "instance_id": "instance-a"},
            {"id": "source-c", "required": False, "instance_id": "instance-b"},
        ],
        # Deliberately omit schema_version to prove that first-slice ingestion
        # rows remain readable while downstream provenance is always versioned.
        "identity": {
            "selected_instance_id": "instance-a",
            "solver_anchor_instance_id": "instance-a",
            "instances": [
                {
                    "instance_id": "instance-a",
                    "design_id": "wgd-shared",
                    "body_object_ids": ["body-a"],
                    "assembly_from_link": matrix,
                    "source_ids": ["source-a", "source-b"],
                    "default_drive_channel_ids": ["left"],
                },
                {
                    "instance_id": "instance-b",
                    "design_id": "wgd-shared",
                    "body_object_ids": ["body-b"],
                    "assembly_from_link": matrix,
                    "source_ids": ["source-c"],
                    "default_drive_channel_ids": ["right"],
                },
            ],
        },
    }


class _PausedRegistry:
    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()

    async def get_engine(self, _name: str) -> Any:
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(name="metal")
        await self.release.wait()
        return None

    async def unavailable_reason(self, _name: str) -> str | None:
        return None


class _AlwaysRegistry:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    async def get_engine(self, _name: str) -> Any:
        return self.engine

    async def unavailable_reason(self, _name: str) -> str | None:
        return None


async def _runtime_fixture(
    tmp_path: Path, record_changes: dict[str, Any] | None = None
) -> tuple[JobRuntime, str, dict[str, Any]]:
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n", encoding="utf-8")
    record = _record(mesh_path)
    record.update(record_changes or {})
    cad_store = CadLinkStore(tmp_path / "cadlink.db")

    def build(ingest_id: str, created_at: str) -> str:
        return json.dumps({**record, "ingest_id": ingest_id, "created_at": created_at})

    row = cad_store.allocate_ingest(
        manifest_sha256=MANIFEST_SHA,
        artifact_sha256=ARTIFACT_SHA,
        record_builder=build,
    )
    runtime = JobRuntime(
        JobStore(tmp_path / "jobs.db"),
        engine_registry=_PausedRegistry(),  # type: ignore[arg-type]
        cadlink_store=cad_store,
    )
    return runtime, str(row["ingest_id"]), record


def test_coupled_cardioid_topology_is_rejected_during_submission(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        mesh_sizes = {
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {
                "source-a": 3.0,
                "source-mf": 3.0,
                "PORT_EXIT": 4.0,
            },
        }
        runtime, ingest_id, _ = await _runtime_fixture(
            tmp_path,
            {
                "sources": [
                    {"id": "source-a", "role": "HF", "required": True},
                    {"id": "source-mf", "role": "MF", "required": True},
                    {"id": "PORT_EXIT", "role": "OTHER", "required": False},
                ],
                "source_tags": {"source-a": 101, "source-mf": 102, "PORT_EXIT": 103},
                "mesh_sizes": mesh_sizes,
            },
        )
        request = _request(
            ingest_id,
            drive_channels=[
                {"id": "hf", "source_ids": ["source-a"]},
                # A driver-less MF channel used to fail only after both solves.
                {"id": "mf", "source_ids": ["source-mf"]},
                {"id": "port", "source_ids": ["PORT_EXIT"]},
            ],
            mesh=mesh_sizes,
            passive_cardioid_rear_volume_l=6.0,
            passive_cardioid_port_length_mm=25.0,
            model_port_area_m2=0.05,
            bem_port_area_m2=0.01,
            port_area_source="user",
            passive_cardioid_foam_resistance_pa_s_m3=10_000.0,
            passive_cardioid_coupled=True,
        )
        try:
            with pytest.raises(ImportedSolveRefusal) as caught:
                await runtime.submit(request)
            assert caught.value.reason_code == "passive_cardioid_topology"
            assert "driver model" in str(caught.value)
            assert runtime.store.list_jobs()[1] == 0
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_submit_persists_ingestion_mesh_summary_and_availability(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, ingest_id, _record_data = await _runtime_fixture(tmp_path)
        try:
            request = _request(ingest_id)
            request.options.frequencies_hz = [100.0, 20_000.0]
            job_id = await runtime.submit(request)
            row = runtime.store.get_job_row(job_id)
            assert row is not None
            assert row["config_summary_json"]["formula_type"] == "cad-import"
            assert "global_frequency_caveat" not in row["task_metadata"]["imported_geometry"]
            assert runtime.store.get_mesh_artifact(job_id).startswith("$MeshFormat")
            serialized = runtime._serialize_job(row)
            assert serialized["design_availability"]["source"] == "cad-import"
            assert serialized["design_availability"]["reopenable"] is False
            assert serialized["cad_setup"] == row["config_json"]["geometry"]
            assert _replay_request(row).model_dump(mode="json") == SolveRequest.model_validate(
                row["config_json"]
            ).model_dump(mode="json")
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_legacy_imported_job_recovers_project_provenance_from_ingest(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime, _first_ingest_id, record = await _runtime_fixture(tmp_path)
        cad_store = runtime.cadlink_store
        assert cad_store is not None
        saved = cad_store.save(
            requested=None,
            design_hash="sha256:" + "d" * 64,
            filename="Tritonia-V.cfg",
            snapshot_builder=lambda _identity: "legacy snapshot",
        )
        identity = saved["identity"]
        cad_store.record_lineage_cad_names(
            identity.lineage_id,
            bundle_stem="Tritonia-V",
            archive_stem="Tritonia-V-project",
        )

        def build(ingest_id: str, created_at: str) -> str:
            return json.dumps({
                **record,
                "ingest_id": ingest_id,
                "created_at": created_at,
                "anchor": {
                    **record["anchor"],
                    "design_id": identity.design_id,
                },
                "document": {
                    "name": "Tritonia V",
                    "return_state_hash": "sha256:return-state",
                },
            })

        ingest = cad_store.allocate_ingest(
            manifest_sha256=MANIFEST_SHA,
            artifact_sha256=ARTIFACT_SHA,
            record_builder=build,
        )
        try:
            job_id = await runtime.submit(_request(str(ingest["ingest_id"])))
            runtime.store.mutate_job_metadata(job_id, {
                # This is the shape written by the affected historical jobs.
                "imported_geometry": {
                    "ingest_id": ingest["ingest_id"],
                    "anchor_design_id": identity.design_id,
                },
            })

            item = await runtime.get_job(job_id)

            assert item["cad_source"] == {
                "ingest_id": ingest["ingest_id"],
                "design_id": identity.design_id,
                "lineage_id": identity.lineage_id,
                "archive_stem": "Tritonia-V-project",
                "manifest_sha256": MANIFEST_SHA,
                "document_name": "Tritonia V",
                "return_state_hash": "sha256:return-state",
                "identity": None,
            }
            assert item["cad_setup"] == runtime.store.get_job_row(job_id)[
                "config_json"
            ]["geometry"]
            assert (
                JobStatusResponse.model_validate(item).cad_source.lineage_id
                == identity.lineage_id
            )
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_imported_run_retains_versioned_instance_body_transform_source_and_drive_provenance(
    tmp_path: Path,
) -> None:
    class IdentityEngine:
        name = "metal"

        async def run(self, *_args: Any, **_kwargs: Any) -> EngineRunResult:
            return EngineRunResult(
                results={
                    "frequencies": [],
                    "channels": {
                        "left": {"frequencies": [], "metadata": {"drive_channel_id": "left"}},
                        "right": {"frequencies": [], "metadata": {"drive_channel_id": "right"}},
                    },
                    "channel_order": ["left", "right"],
                    "metadata": {},
                }
            )

    async def scenario() -> None:
        runtime, ingest_id, _ = await _runtime_fixture(
            tmp_path, _identity_record_changes()
        )
        runtime.engine_registry = _AlwaysRegistry(IdentityEngine())  # type: ignore[assignment]
        try:
            job_id = await runtime.submit(_request(ingest_id))
            await runtime.wait_idle()
            row = runtime.store.get_job_row(job_id)
            assert row["status"] == "complete", row.get("error_message")
            identity = row["task_metadata"]["imported_geometry"]["identity"]
            assert identity["schema_version"] == 1
            assert identity["selected_instance_id"] == "instance-a"
            assert identity["instances"][1]["body_object_ids"] == ["body-b"]
            assert identity["instances"][1]["assembly_from_link"][3] == [0.0, 0.0, 0.0, 1.0]
            assert identity["drive_channels"] == [
                {
                    "drive_channel_id": "left",
                    "source_ids": ["source-a", "source-b"],
                    "instance_ids": ["instance-a"],
                },
                {
                    "drive_channel_id": "right",
                    "source_ids": ["source-c"],
                    "instance_ids": ["instance-b"],
                },
            ]
            assert runtime._serialize_job(row)["cad_source"]["identity"] == identity
            results = await runtime.get_results(job_id)
            assert results["provenance"]["cad_identity"] == identity
            assert results["channels"]["left"]["metadata"]["cad_identity"] == identity
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_submission_refuses_a_contradictory_cad_source_owner(tmp_path: Path) -> None:
    async def scenario() -> None:
        changes = _identity_record_changes()
        changes["sources"][0]["instance_id"] = "instance-b"
        runtime, ingest_id, _ = await _runtime_fixture(tmp_path, changes)
        try:
            with pytest.raises(ImportedSolveRefusal) as caught:
                await runtime.submit(_request(ingest_id))
            assert caught.value.reason_code == "cad_identity_invalid"
            assert "contradicts" in str(caught.value)
            assert runtime.store.list_jobs()[1] == 0
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_current_report_acknowledgement_and_imported_retry(tmp_path: Path) -> None:
    class HoldingEngine:
        name = "metal"

        async def run(self, *_args: Any, **_kwargs: Any) -> EngineRunResult:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def scenario() -> None:
        runtime, ingest_id, _ = await _runtime_fixture(
            tmp_path,
            {"findings": [{"id": "finding-a", "blocking": True}]},
        )
        runtime.engine_registry = _AlwaysRegistry(HoldingEngine())  # type: ignore[assignment]
        try:
            request = _request(
                ingest_id,
                acknowledged_findings=[f"{REPORT_SHA}:finding-a"],
            )
            source_id = await runtime.submit(request)
            retry_id = await runtime.retry(source_id)
            retried = runtime.store.get_job_row(retry_id)
            assert retried["parent_job_id"] == source_id
            assert retried["config_json"]["geometry"]["ingest_id"] == ingest_id
            assert retried["config_json"]["geometry"]["acknowledged_findings"] == [
                f"{REPORT_SHA}:finding-a"
            ]
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_unlinked_freshness_finding_does_not_block_submission(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, ingest_id, _ = await _runtime_fixture(
            tmp_path,
            {
                "freshness": {
                    "verdict": "unlinked",
                    "instances": [],
                    "finding_id": "unlinked-mode",
                },
                "findings": [
                    {
                        "id": "unlinked-mode",
                        "kind": "freshness",
                        "blocking": False,
                        "verdict": "unlinked",
                    }
                ],
            },
        )
        try:
            job_id = await runtime.submit(_request(ingest_id))
            assert runtime.store.get_job_row(job_id) is not None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_missing_ingestion_record_is_typed(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=_PausedRegistry(),  # type: ignore[arg-type]
            cadlink_store=CadLinkStore(tmp_path / "cadlink.db"),
        )
        try:
            with pytest.raises(ImportedSolveRefusal) as caught:
                await runtime.submit(_request("wgi_" + "0" * 26))
            assert caught.value.reason_code == "ingest_not_found"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("required", [False, True], ids=["optional", "required"])
def test_skipped_sources_must_be_optional(tmp_path: Path, required: bool) -> None:
    async def scenario() -> None:
        sources = [
            {"id": "source-a", "required": True},
            {"id": "source-b", "required": True},
            {"id": "source-c", "required": required},
        ]
        mesh_sizes = {
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {"source-a": 3.0, "source-b": 3.0},
        }
        runtime, ingest_id, _ = await _runtime_fixture(
            tmp_path,
            {
                "sources": sources,
                "skipped_source_ids": ["source-c"],
                "mesh_sizes": mesh_sizes,
            },
        )
        geometry = _geometry(ingest_id)
        geometry["drive_channels"] = [
            {"id": "left", "source_ids": ["source-a", "source-b"]}
        ]
        geometry["mesh"] = mesh_sizes
        geometry["skipped_source_ids"] = ["source-c"]
        request = SolveRequest.model_validate(
            {
                "geometry": geometry,
                "options": {
                    "engine": "metal",
                    "frequencies_hz": [100, 1000],
                    "polar_config": {"angle_range": [-180, 180, 37]},
                },
            }
        )
        try:
            if required:
                with pytest.raises(ImportedSolveRefusal) as caught:
                    await runtime.submit(request)
                assert caught.value.reason_code == "required_source_skipped"
            else:
                job_id = await runtime.submit(request)
                assert runtime.store.get_mesh_artifact(job_id) is not None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_required_fem_volume_needs_exterior_only_override(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, ingest_id, _ = await _runtime_fixture(
            tmp_path,
            {"evidence": {"fem_air_volumes": [{"file": "fem/air.step"}]}},
        )
        try:
            with pytest.raises(ImportedSolveRefusal) as caught:
                await runtime.submit(_request(ingest_id))
            assert caught.value.reason_code == "fem_required"
            request = _request(ingest_id, exterior_only=True)
            assert await runtime.submit(request)
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mutate_request", "record_changes", "reason"),
    [
        (
            lambda value, _record: value.geometry.__setattr__("manifest_sha256", "sha256:" + "9" * 64),
            {},
            "ingest_sha_mismatch",
        ),
        (
            lambda value, _record: value.geometry.__setattr__(
                "acknowledged_findings", ["sha256:" + "8" * 64 + ":finding-a"]
            ),
            {"findings": [{"id": "finding-a", "blocking": True}]},
            "unacknowledged_findings",
        ),
        (
            lambda value, _record: value.options.polar_config.__setattr__(
                "angle_range", (0.0, 180.0, 37)
            ),
            {},
            "polar_grid_narrowing",
        ),
        (
            lambda value, _record: value.options.__setattr__("engine", "bempp"),
            {},
            "imported_engine_unsupported",
        ),
        (
            lambda value, _record: value.options.__setattr__("engine", "circsym"),
            {},
            "imported_circsym_unsupported",
        ),
        (
            lambda value, _record: value.options.__setattr__("solver_mode", "circsym"),
            {},
            "imported_circsym_unsupported",
        ),
        (
            lambda _value, _record: None,
            {"infinite_baffle": True},
            "imported_infinite_baffle_unsupported",
        ),
        (
            lambda value, _record: value.options.polar_config.__setattr__(
                "enabled_axes", ["horizontal"]
            ),
            {},
            "polar_grid_narrowing",
        ),
        (
            lambda value, _record: value.options.polar_config.__setattr__(
                "inclination", 30.0
            ),
            {},
            "imported_diagonal_inclination_unsupported",
        ),
        (
            lambda value, _record: value.options.__setattr__("engine", "dryrun"),
            {},
            "imported_engine_unsupported",
        ),
        (
            lambda value, _record: value.geometry.mesh.__setattr__(
                "rigid_size_mm", 9.0
            ),
            {},
            "mesh_sizes_mismatch",
        ),
        (
            lambda value, _record: value.options.__setattr__("symmetry", "full"),
            {},
            "imported_symmetry_mismatch",
        ),
        (
            lambda _value, _record: None,
            {"sources": None},
            "ingest_record_incomplete",
        ),
        (
            lambda _value, _record: None,
            {"skipped_source_ids": ["source-c"]},
            "skipped_sources_mismatch",
        ),
        (
            lambda _value, _record: None,
            {
                "sources": [
                    {"id": "source-a", "required": True},
                    {"id": "source-b", "required": True},
                    {"id": "source-c", "required": False},
                    {"id": "source-d", "required": True},
                ]
            },
            "drive_source_coverage",
        ),
        (
            lambda _value, record: Path(record["mesh_store_path"]).unlink(),
            {},
            "ingest_mesh_unavailable",
        ),
        (
            lambda _value, record: Path(record["mesh_store_path"]).write_text(
                "corrupt", encoding="utf-8"
            ),
            {},
            "ingest_mesh_unavailable",
        ),
    ],
)
def test_imported_submit_refusals(
    tmp_path: Path,
    mutate_request: Any,
    record_changes: dict[str, Any],
    reason: str,
) -> None:
    async def scenario() -> None:
        runtime, ingest_id, record = await _runtime_fixture(tmp_path, record_changes)
        try:
            request = _request(ingest_id)
            mutate_request(request, record)
            with pytest.raises(ImportedSolveRefusal) as caught:
                await runtime.submit(request)
            assert caught.value.reason_code == reason
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_z0_cut_refuses_at_submit_and_solver_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def submit_scenario() -> tuple[SolveRequest, dict[str, Any]]:
        runtime, ingest_id, record = await _runtime_fixture(
            tmp_path, {"symmetry": {"cut_planes": ["z0"]}}
        )
        try:
            with pytest.raises(ImportedSolveRefusal) as caught:
                await runtime.submit(_request(ingest_id))
            assert caught.value.reason_code == "imported_symmetry_unsupported"
            return _request(ingest_id), record
        finally:
            await runtime.shutdown()

    request, record = asyncio.run(submit_scenario())
    record["symmetry"] = {"cut_planes": ["z0"]}
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    with pytest.raises(ImportedSymmetryUnsupportedError, match="z0"):
        metal.solve_imported_metal_from_msh_text("msh", request, record)


def _native_result() -> SimpleNamespace:
    frequencies = np.asarray([100.0, 200.0])
    return SimpleNamespace(
        frequencies_hz=frequencies,
        observation_angles_deg=np.asarray([-180.0, 0.0, 180.0]),
        observation_planes=["horizontal"],
        pressure_complex=np.ones((2, 1, 3), dtype=np.complex128) * 20.0e-6,
        directivity_db=np.zeros((2, 1, 3)),
        impedance=np.ones(2, dtype=np.complex128) * (1j * REFERENCE_RHO_C),
        solver_log=[],
        timings={},
        native_diagnostics=[],
    )


@pytest.mark.parametrize(
    "wrap_source_result",
    [False, True],
    ids=["single-source-fast-path", "multi-source-wrapper"],
)
def test_single_channel_imported_stream_accepts_metal_entry_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrap_source_result: bool,
) -> None:
    streamed: list[tuple[int, dict[str, Any]]] = []
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))

    def solve_multi(
        _mesh: str,
        sources: list[dict[int, complex]],
        config: Any,
        frequencies_hz: list[float] | None = None,
    ) -> list[SimpleNamespace]:
        assert sources == [{101: 1.0 + 0.0j}]
        assert frequencies_hz == [100.0, 200.0]
        complete = _native_result()
        source_entry = {
            "observation_angles_deg": complete.observation_angles_deg,
            "observation_planes": complete.observation_planes,
            "observation_pressure_complex": complete.pressure_complex[0],
            "observation_directivity_db": complete.directivity_db[0],
            "impedance": complete.impedance[0],
        }
        entry = {"source_results": [source_entry]} if wrap_source_result else source_entry
        assert config.on_frequency_result(0, 100.0, entry) is True
        return [complete]

    monkeypatch.setattr(metal, "native_solve_multi_source", solve_multi)
    request = _request(
        "wgi_" + "0" * 26,
        drive_channels=[{"id": "left", "source_ids": ["source-a"]}],
        mesh={
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {"source-a": 3.0},
        },
    )
    request.options.frequencies_hz = [100.0, 200.0]
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")

    response = metal.solve_imported_metal_from_msh_text(
        "msh",
        request,
        _roled_record(mesh_path),
        result_callback=lambda index, result: streamed.append((index, result)),
    )

    assert response["result_kind"] == "multi_channel"
    assert response["channel_order"] == ["left"]
    assert response["channels"]["left"]["metadata"]["role"] == "HF"
    assert len(streamed) == 1
    assert streamed[0][0] == 0
    provisional = streamed[0][1]
    assert provisional["result_kind"] == "multi_channel"
    assert provisional["channel_order"] == ["left"]
    assert list(provisional["channels"]) == ["left"]
    # A live frame is labelled the same way the finished channel is.
    assert provisional["channels"]["left"]["metadata"]["role"] == "HF"
    assert provisional["channels"]["left"]["metadata"]["source_labels"] == ["HF throat"]


def test_multi_source_orchestration_uses_channel_bases_and_anchor_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )

    def config(**kwargs: Any) -> SimpleNamespace:
        captured["config"] = kwargs
        return SimpleNamespace(**kwargs)

    def solve_multi(
        _mesh: str,
        sources: list[dict[int, complex]],
        _config: Any,
        frequencies_hz: list[float] | None = None,
    ) -> list[SimpleNamespace]:
        captured["sources"] = sources
        captured["frequencies_hz"] = frequencies_hz
        results = [_native_result() for _ in sources]
        for index, result in enumerate(results, start=1):
            result.surface_pressure_complex = np.full(
                (2, 3), complex(index, -index), dtype=np.complex128
            )
            result.surface_neumann_complex = np.full(
                (2, 3), complex(-index, index), dtype=np.complex128
            )
        return results

    monkeypatch.setattr(metal, "native_config", config)
    monkeypatch.setattr(metal, "native_solve_multi_source", solve_multi)
    monkeypatch.setattr(
        metal,
        "build_solver_mesh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("imported jobs must never rebuild geometry")
        ),
    )
    request = _request("wgi_" + "0" * 26)
    request.options.frequencies_hz = [100.0, 200.0]
    request.geometry.drive_channels[1].motion = "axial"
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")
    record = _record(mesh_path)
    record["mesh"]["stats"]["vertex_count"] = 3

    async def run() -> EngineRunResult:
        return await metal.MetalEngine().run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_args: None,
            imported_record=record,
        )

    outcome = asyncio.run(run())
    response = outcome.results

    assert captured["sources"] == [
        {101: 1.0 + 0.0j, 102: 1.0 + 0.0j},
        {103: 1.0 + 0.0j},
    ]
    assert captured["config"]["frame_override"].origin.tolist() == [0.0, 0.08, 0.0]
    assert captured["config"]["native_symmetry_plane"] == "yz"
    assert set(captured["config"]["source_velocity_profiles"]) == {101, 102, 103}
    assert isinstance(captured["config"]["source_velocity_profiles"][103], metal.AxialProfile)
    assert response["channel_order"] == ["left", "right"]
    assert set(response["channels"]) == {"left", "right"}
    assert response["result_kind"] == "multi_channel"
    assert response["result_contract_version"] == 2
    assert response["metadata"]["result_contract_version"] == 2
    assert "impedance" not in response["channels"]["left"]
    assert response["channels"]["left"]["metadata"]["impedance_omitted"] == (
        "multi-source channel: per-patch impedance is not a channel impedance"
    )
    assert "impedance" in response["channels"]["right"]
    assert response["metadata"]["observation_origin_effective"] == "throat"
    assert response["metadata"]["observation_frame_basis"]["origin_m"] == [
        0.0,
        0.08,
        0.0,
    ]
    assert outcome.field_traces is not None
    assert [channel.channel_id for channel in outcome.field_traces.channels] == [
        "left",
        "right",
    ]
    np.testing.assert_array_equal(
        outcome.field_traces.channels[0].pressure_p1,
        np.full((2, 3), 1 - 1j, dtype=np.complex128),
    )

    allocated = {"1", "101", "102", "103"}

    def assert_no_tag_keys(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = (*path, str(key))
                if path != ("metadata", "tag_map"):
                    assert str(key) not in allocated, next_path
                assert_no_tag_keys(item, next_path)
        elif isinstance(value, list):
            for item in value:
                assert_no_tag_keys(item, path)

    assert_no_tag_keys(response)


def test_channel_driver_scaling_includes_retained_pressure_and_neumann_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scales = np.asarray([2 + 3j, -1 + 0.5j], dtype=np.complex128)
    monkeypatch.setattr(
        metal,
        "channel_drive_scaling",
        lambda *_args, **_kwargs: (scales, {}),
    )
    result = _native_result()
    result.surface_pressure_complex = np.ones((2, 3), dtype=np.complex128)
    result.surface_neumann_complex = np.full((2, 2), 4 - 2j, dtype=np.complex128)
    channel = SimpleNamespace(
        source_ids=["source-a"],
        driver=object(),
    )

    metal._apply_channel_driver(
        channel,
        result,
        {
            "sources": [
                {
                    "id": "source-a",
                    "observed": {"total_area_mm2": 1000.0},
                }
            ]
        },
        {"source-a": 101},
        drive_voltage_v=2.83,
        rg_ohm=0.0,
    )

    np.testing.assert_array_equal(
        result.surface_pressure_complex,
        np.ones((2, 3), dtype=np.complex128) * scales[:, None],
    )
    np.testing.assert_array_equal(
        result.surface_neumann_complex,
        np.full((2, 2), 4 - 2j, dtype=np.complex128) * scales[:, None],
    )


def test_frequency_validity_estimates_are_not_exposed_as_result_caveats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(
        metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _mesh, sources, _config, frequencies_hz=None: [
            _native_result() for _ in sources
        ],
    )
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")
    record = _record(mesh_path)
    record["mesh"]["metadata"]["mesh_frequency_validation"][
        "requested_max_frequency_hz"
    ] = 20_000.0

    above = _request("wgi_" + "0" * 26)
    above.options.frequencies_hz = [100.0, 20_000.0]
    above_response = metal.solve_imported_metal_from_msh_text("msh", above, record)
    assert "global_frequency_caveat" not in above_response["metadata"]


def test_unlinked_frame_fallback_and_real_mixed_motion_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    real_native_config = metal.native_config
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})

    def solve_multi(
        _mesh: str,
        sources: list[dict[int, complex]],
        config: Any,
        frequencies_hz: list[float] | None = None,
    ) -> list[SimpleNamespace]:
        captured["config"] = config
        return [_native_result() for _ in sources]

    monkeypatch.setattr(metal, "native_solve_multi_source", solve_multi)
    assert real_native_config is not None
    request = _request("wgi_" + "0" * 26)
    request.options.frequencies_hz = [100.0, 200.0]
    request.geometry.drive_channels[1].motion = "axial"
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")
    record = _record(mesh_path)
    record["anchor"] = {"instance_id": None, "design_id": None, "throat_frame": None}
    record["normalisation"] = {"assembly_frame_is_solver_frame": True}
    response = metal.solve_imported_metal_from_msh_text("msh", request, record)
    assert captured["config"].frame_override.origin.tolist() == [0.0, 0.0, 0.0]
    assert response["metadata"]["observation_origin_effective"] == "throat"


def test_execution_uses_job_mesh_after_import_cache_is_deleted(tmp_path: Path) -> None:
    class CapturingEngine:
        name = "metal"

        async def run(
            self,
            _request: SolveRequest,
            *,
            cancel_cb: Any,
            stage_cb: Any,
            imported_record: dict[str, Any],
            artifact_cb: Any = None,
        ) -> EngineRunResult:
            assert imported_record["_execution_mesh_source"] == "job-artifact"
            assert imported_record["_execution_msh_text"].startswith("$MeshFormat")
            return EngineRunResult(
                results={"channels": {"left": {}, "right": {}}, "metadata": {}},
                msh_text=imported_record["_execution_msh_text"],
                mesh_stats={"triangle_count": 3},
            )

    async def scenario() -> None:
        runtime, ingest_id, record = await _runtime_fixture(tmp_path)
        runtime.engine_registry = _AlwaysRegistry(CapturingEngine())  # type: ignore[assignment]
        try:
            job_id = await runtime.submit(_request(ingest_id))
            Path(record["mesh_store_path"]).unlink()
            for _ in range(100):
                row = runtime.store.get_job_row(job_id)
                if row["status"] in {"complete", "error"}:
                    break
                await asyncio.sleep(0.01)
            assert row["status"] == "complete", row.get("error_message")
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def _native_result_3f() -> SimpleNamespace:
    frequencies = np.asarray([100.0, 500.0, 1000.0])
    return SimpleNamespace(
        frequencies_hz=frequencies,
        observation_angles_deg=np.asarray([-180.0, 0.0, 180.0]),
        observation_planes=["horizontal"],
        pressure_complex=np.ones((3, 1, 3), dtype=np.complex128) * 20.0e-6,
        directivity_db=np.zeros((3, 1, 3)),
        impedance=np.ones(3, dtype=np.complex128) * (1j * REFERENCE_RHO_C),
        solver_log=[],
        timings={},
        native_diagnostics=[],
    )


def _coupled_failure_fixture() -> tuple[SolveRequest, dict[str, Any]]:
    driver = {
        "sd_cm2": 210.0,
        "bl_t_m": 10.5,
        "re_ohm": 5.3,
        "le_mh": 0.5,
        "mmd_g": 12.0,
        "cms_m_per_n": 4.0e-4,
        "rms_kg_per_s": 1.2,
    }
    request = _request(
        "wgi_" + "0" * 26,
        drive_channels=[
            {"id": "mf", "source_ids": ["source-mf"], "driver": driver},
            {"id": "port", "source_ids": ["PORT_EXIT"]},
        ],
        mesh={
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {"source-mf": 3.0, "PORT_EXIT": 3.0},
        },
        passive_cardioid_rear_volume_l=6.0,
        passive_cardioid_port_length_mm=25.0,
        model_port_area_m2=0.05,
        bem_port_area_m2=0.01,
        port_area_source="user",
        passive_cardioid_foam_resistance_pa_s_m3=10_000.0,
        passive_cardioid_coupled=True,
    )
    record = {
        "sources": [
            {
                "id": "source-mf",
                "role": "MF",
                "observed": {"total_area_mm2": 21_000.0},
            },
            {"id": "PORT_EXIT", "role": "OTHER"},
        ],
        "source_tags": {"source-mf": 101, "PORT_EXIT": 10},
        "symmetry": {"cut_planes": ["x0"]},
        "normalisation": {"assembly_frame_is_solver_frame": True},
        "mesh": {"metadata": {"mesh_frequency_validation": {"per_source": {}}}},
    }
    return request, record


def test_cardioid_campaign_failure_keeps_main_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, record = _coupled_failure_fixture()
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _mesh, sources, _config, frequencies_hz=None: [
            _native_result_3f() for _source in sources
        ],
    )
    monkeypatch.setattr(
        metal,
        "_run_passive_cardioid_campaign",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("matrix campaign exploded")
        ),
    )

    response = metal.solve_imported_metal_from_msh_text("msh", request, record)

    assert response["channel_order"] == ["mf", "port"]
    assert set(response["channels"]) == {"mf", "port"}
    assert response["metadata"]["passive_cardioid"] == {
        "enabled": True,
        "coupled": True,
        "status": "failed",
        "reason": "matrix campaign exploded",
    }
    assert "_radiation_impedance_npz" not in response


def test_cardioid_campaign_degradation_does_not_swallow_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CancelledAtCheckpoint(RuntimeError):
        pass

    request, record = _coupled_failure_fixture()
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _mesh, sources, _config, frequencies_hz=None: [
            _native_result_3f() for _source in sources
        ],
    )
    monkeypatch.setattr(
        metal,
        "_run_passive_cardioid_campaign",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CancelledAtCheckpoint("cancelled")
        ),
    )

    def cancel() -> None:
        raise CancelledAtCheckpoint("cancelled")

    with pytest.raises(CancelledAtCheckpoint, match="cancelled"):
        metal.solve_imported_metal_from_msh_text(
            "msh", request, record, cancellation_callback=cancel
        )


def test_coupled_cardioid_adds_derived_channel_and_defers_mf_driver_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = {
        "sd_cm2": 210.0,
        "bl_t_m": 10.5,
        "re_ohm": 5.3,
        "le_mh": 0.5,
        "mmd_g": 12.0,
        "cms_m_per_n": 4.0e-4,
        "rms_kg_per_s": 1.2,
        "xmax_mm": 5.0,
    }
    request = _request(
        "wgi_" + "0" * 26,
        drive_channels=[
            {"id": "mf", "source_ids": ["source-mf"], "driver": driver},
            {"id": "port", "source_ids": ["PORT_EXIT"]},
        ],
        mesh={
            "rigid_size_mm": 8.0,
            "transition_mm": 20.0,
            "source_size_mm": {"source-mf": 3.0, "PORT_EXIT": 3.0},
        },
        passive_cardioid_rear_volume_l=6.0,
        passive_cardioid_port_length_mm=25.0,
        model_port_area_m2=0.05,
        bem_port_area_m2=0.01,
        port_area_source="user",
        passive_cardioid_foam_resistance_pa_s_m3=10_000.0,
        passive_cardioid_invert_port=True,
        passive_cardioid_coupled=True,
    )
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")
    record = _record(mesh_path)
    record["sources"] = [
        {
            "id": "source-mf",
            "role": "MF",
            "observed": {"total_area_mm2": 21_000.0},
        },
        {
            "id": "PORT_EXIT",
            "role": "OTHER",
            "observed": {"total_area_mm2": 10_000.0},
        },
    ]
    record["source_tags"] = {"source-mf": 101, "PORT_EXIT": 10}
    record["mesh"]["metadata"]["mesh_frequency_validation"]["per_source"] = {}

    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})

    def solve_multi(
        _mesh: str,
        sources: list[dict[int, complex]],
        _config: Any,
        frequencies_hz: list[float] | None = None,
    ) -> list[SimpleNamespace]:
        assert sources == [{101: 1.0 + 0.0j}, {10: 1.0 + 0.0j}]
        mf = _native_result_3f()
        port = _native_result_3f()
        port.pressure_complex *= 0.5
        return [mf, port]

    def solve_matrix(
        _mesh: Path,
        aperture_tags: dict[str, list[int]],
        frequencies_hz: np.ndarray,
        _config: Any,
    ) -> Any:
        assert aperture_tags == {"PORT_EXIT": [10], "MF": [101]}
        matrix = np.zeros((len(frequencies_hz), 2, 2), dtype=np.complex128)
        matrix[:, 0, 0] = 100.0 + 20.0j
        matrix[:, 1, 1] = 120.0 + 25.0j
        matrix[:, 0, 1] = matrix[:, 1, 0] = 10.0 + 2.0j
        return metal.radiation_impedance.RadiationImpedanceResult(
            frequencies_hz=np.asarray(frequencies_hz, dtype=np.float64),
            aperture_names=["PORT_EXIT", "MF"],
            aperture_area_m2={"PORT_EXIT": 0.01, "MF": 0.021},
            impedance_matrix=matrix,
            solver_logs=[],
        )

    monkeypatch.setattr(metal, "native_solve_multi_source", solve_multi)
    monkeypatch.setattr(
        metal.radiation_impedance, "solve_aperture_matrix", solve_matrix
    )
    response = metal.solve_imported_metal_from_msh_text("msh", request, record)

    assert response["channel_order"] == ["mf", "port", "passive_cardioid"]
    assert response["channels"]["mf"]["metadata"]["driver_coupling_deferred_to"] == (
        "passive_cardioid"
    )
    derived = response["channels"]["passive_cardioid"]
    assert derived["metadata"]["impedance_units"] == "ohms"
    assert derived["metadata"]["impedance_drive"] == "voltage"
    assert derived["metadata"]["impedance_phase_convention"] == (
        "engineering_exp_plus_jwt"
    )
    assert derived["metadata"]["passive_cardioid"]["port_area_source"] == "user"
    assert derived["metadata"]["driver"]["spec"]["xmax_mm"] == 5.0
    assert len(derived["impedance"]["real"]) == 3
    assert response["metadata"]["passive_cardioid"]["coupled"] is True
    assert isinstance(response["_radiation_impedance_npz"], bytes)


def test_channels_carry_their_ingest_band_role_and_source_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _mesh, sources, _config, frequencies_hz=None: [
            _native_result_3f() for _ in sources
        ],
    )
    request = _request("wgi_" + "0" * 26)
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")

    roled = metal.solve_imported_metal_from_msh_text(
        "msh", request, _roled_record(mesh_path)
    )
    left = roled["channels"]["left"]["metadata"]
    right = roled["channels"]["right"]["metadata"]
    assert left["role"] == "HF"
    assert left["source_ids"] == ["source-a", "source-b"]
    assert left["source_labels"] == ["HF throat", "source-b"]
    assert right["role"] == "LF"
    assert "source_labels" not in right

    unroled_record = _roled_record(mesh_path)
    unroled_record["sources"][2] = {"id": "source-c", "required": False}
    unroled = metal.solve_imported_metal_from_msh_text(
        "msh", request, unroled_record
    )
    assert unroled["channels"]["right"]["metadata"]["role"] is None


def test_combined_channel_is_appended_with_contract_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _mesh, sources, _config, frequencies_hz=None: [
            _native_result_3f() for _ in sources
        ],
    )
    request = _request(
        "wgi_" + "0" * 26,
        # level_match off keeps identical unit fields an exact allpass sum,
        # so the SPL identity below is exact rather than grid-dependent.
        combine={
            "members": ["left", "right"],
            "crossovers_hz": [500.0],
            "level_match": False,
        },
    )
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")
    record = _roled_record(mesh_path)

    async def run() -> dict[str, Any]:
        outcome = await metal.MetalEngine().run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_args: None,
            imported_record=record,
        )
        return outcome.results

    response = asyncio.run(run())

    assert response["channel_order"] == ["left", "right", "combined"]
    combined = response["channels"]["combined"]
    assert "impedance" not in combined
    assert combined["metadata"]["impedance_omitted"] == (
        "combined channel: member drives differ; no single impedance exists"
    )
    payload = combined["metadata"]["combine"]
    assert payload["type"] == "filtered_time_aligned_sum"
    assert payload["members"] == ["left", "right"]
    assert payload["member_roles"] == ["HF", "LF"]
    assert payload["crossovers_hz"] == [500.0]
    assert combined["metadata"]["derived_from_channels"] == ["left", "right"]
    assert combined["metadata"]["drive_channel_id"] == "combined"
    assert combined["metadata"]["source_ids"] == ["source-a", "source-b", "source-c"]
    assert combined["metadata"]["phase_time_convention"] == "exp(+ikr)"
    assert combined["frequencies"] == [100.0, 500.0, 1000.0]
    assert combined["spl_on_axis"]["spl"][0] is not None
    assert "horizontal" in combined["directivity"]
    # Identical unit fields through an LR4 pair sum to an allpass: the
    # combined on-axis SPL matches the members'.
    member_spl = response["channels"]["left"]["spl_on_axis"]["spl"]
    assert combined["spl_on_axis"]["spl"] == pytest.approx(member_spl, abs=1e-6)
    # The members keep their own contract untouched.
    assert "combine" not in response["channels"]["left"]["metadata"]


def test_combine_wire_validation_refuses_structural_defects() -> None:
    with pytest.raises(ValidationError, match="unknown drive channels"):
        _request(
            "wgi_" + "0" * 26,
            combine={"members": ["left", "missing"], "crossovers_hz": [500.0]},
        )
    with pytest.raises(ValidationError, match="collides with a drive channel id"):
        _request(
            "wgi_" + "0" * 26,
            combine={
                "id": "left",
                "members": ["left", "right"],
                "crossovers_hz": [500.0],
            },
        )
    with pytest.raises(ValidationError, match="exactly one crossover"):
        _request(
            "wgi_" + "0" * 26,
            combine={"members": ["left", "right"], "crossovers_hz": [300.0, 500.0]},
        )
    with pytest.raises(ValidationError, match="strictly ascending"):
        ChannelCombineSpec(
            members=["lf", "mf", "hf"], crossovers_hz=[500.0, 300.0]
        )
    with pytest.raises(ValidationError, match="must be unique"):
        _request(
            "wgi_" + "0" * 26,
            combine={"members": ["left", "left"], "crossovers_hz": [500.0]},
        )


def test_combine_crossover_outside_the_solved_band_refuses() -> None:
    with pytest.raises(ValidationError, match="outside the solved band"):
        _request(
            "wgi_" + "0" * 26,
            combine={"members": ["left", "right"], "crossovers_hz": [5000.0]},
        )
    # A per-channel spec is held to the same band.
    with pytest.raises(ValidationError, match="outside the solved band"):
        _request(
            "wgi_" + "0" * 26,
            combine={
                "members": ["left", "right"],
                "channels": {
                    "left": {"lp": {"family": "lr", "order": 4, "fc_hz": 5000.0}},
                    "right": {"hp": {"family": "lr", "order": 4, "fc_hz": 5000.0}},
                },
            },
        )


def test_combine_spec_v2_validates_and_expands_the_legacy_form() -> None:
    legacy = ChannelCombineSpec(members=["lf", "mf", "hf"], crossovers_hz=[300.0, 3000.0])
    resolved = legacy.resolved()
    assert resolved["reference"] == "hf"
    assert resolved["channels"]["mf"] == {
        "hp": {"family": "lr", "order": 4, "fc_hz": 300.0},
        "lp": {"family": "lr", "order": 4, "fc_hz": 3000.0},
        "gain": {"mode": "auto"},
        "delay": {"mode": "auto"},
        "invert": None,
    }
    assert legacy.linked_crossovers_hz() == [300.0, 3000.0]
    assert legacy.corner_frequencies_hz() == [300.0, 3000.0]

    off = ChannelCombineSpec(
        members=["lf", "hf"], crossovers_hz=[900.0], level_match=False, align=False
    ).resolved()["channels"]
    assert off["lf"]["gain"] == {"mode": "manual", "db": 0.0}
    assert off["lf"]["delay"] == {"mode": "manual", "ms": 0.0}

    per_channel = ChannelCombineSpec.model_validate(
        {
            "members": ["lf", "hf"],
            "reference": "lf",
            "channels": {
                "lf": {
                    "lp": {"family": "butterworth", "order": 3, "fc_hz": 800.0},
                    "gain": {"mode": "manual", "db": -1.5},
                },
                "hf": {
                    "hp": {"family": "bessel", "order": 4, "fc_hz": 1200.0},
                    "delay": {"mode": "manual", "ms": 0.4},
                    "invert": True,
                },
            },
        }
    )
    assert per_channel.resolved_reference == "lf"
    # An unlinked pair has no single crossover to report.
    assert per_channel.linked_crossovers_hz() == [None]
    assert per_channel.corner_frequencies_hz() == [800.0, 1200.0]

    with pytest.raises(ValidationError, match="crossovers_hz or a per-channel"):
        ChannelCombineSpec(members=["lf", "hf"])
    with pytest.raises(ValidationError, match="must name exactly the members"):
        ChannelCombineSpec.model_validate(
            {"members": ["lf", "hf"], "channels": {"lf": {}}}
        )
    with pytest.raises(ValidationError, match="is not one of the members"):
        ChannelCombineSpec.model_validate(
            {"members": ["lf", "hf"], "crossovers_hz": [900.0], "reference": "sub"}
        )
    with pytest.raises(ValidationError, match="must sit below its low-pass"):
        ChannelCombineSpec.model_validate(
            {
                "members": ["lf", "hf"],
                "channels": {
                    "lf": {"lp": {"family": "lr", "order": 4, "fc_hz": 900.0}},
                    "hf": {
                        "hp": {"family": "lr", "order": 4, "fc_hz": 900.0},
                        "lp": {"family": "lr", "order": 4, "fc_hz": 400.0},
                    },
                },
            }
        )
    with pytest.raises(ValidationError, match="supports orders"):
        ChannelCombineSpec.model_validate(
            {
                "members": ["lf", "hf"],
                "channels": {
                    "lf": {"lp": {"family": "lr", "order": 3, "fc_hz": 900.0}},
                    "hf": {"hp": {"family": "lr", "order": 4, "fc_hz": 900.0}},
                },
            }
        )
    with pytest.raises(ValidationError, match="a manual gain needs db"):
        ChannelCombineSpec.model_validate(
            {
                "members": ["lf", "hf"],
                "crossovers_hz": [900.0],
                "channels": {
                    "lf": {"gain": {"mode": "manual"}},
                    "hf": {},
                },
            }
        )


def test_recombine_from_stored_bases_updates_and_adds_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.jobs.models import ChannelCombineSpec
    from server.solver.recombine import RecombineError, recombine_stored_results

    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _mesh, sources, _config, frequencies_hz=None: [
            _native_result_3f() for _ in sources
        ],
    )
    # Solved WITHOUT a combine spec: bases alone must allow adding one later.
    request = _request("wgi_" + "0" * 26)
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")
    record = _roled_record(mesh_path)

    async def run() -> Any:
        return await metal.MetalEngine().run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_args: None,
            imported_record=record,
        )

    outcome = asyncio.run(run())
    assert outcome.channel_bases is not None
    assert outcome.results["channel_order"] == ["left", "right"]

    spec = ChannelCombineSpec(members=["left", "right"], crossovers_hz=[500.0])
    updated = recombine_stored_results(
        outcome.results, outcome.channel_bases, spec, request
    )
    assert updated["channel_order"] == ["left", "right", "combined"]
    payload = updated["channels"]["combined"]["metadata"]["combine"]
    assert payload["crossovers_hz"] == [500.0]
    assert payload["member_roles"] == ["HF", "LF"]
    assert updated["channels"]["combined"]["metadata"]["recombined"] is True
    # The members keep the bands and names the solve stamped on them.
    assert updated["channels"]["left"]["metadata"]["role"] == "HF"
    assert updated["channels"]["left"]["metadata"]["source_labels"] == [
        "HF throat",
        "source-b",
    ]
    assert updated["channels"]["right"]["metadata"]["role"] == "LF"
    assert "impedance" not in updated["channels"]["combined"]
    # The original envelope is not mutated in place.
    assert outcome.results["channel_order"] == ["left", "right"]

    # Recombining again with a different crossover replaces the channel.
    respec = ChannelCombineSpec(members=["left", "right"], crossovers_hz=[800.0])
    again = recombine_stored_results(updated, outcome.channel_bases, respec, request)
    assert again["channel_order"] == ["left", "right", "combined"]
    assert again["channels"]["combined"]["metadata"]["combine"]["crossovers_hz"] == [800.0]

    # The route's body model is this spec, so the per-channel form reaches the
    # solver through exactly the same door as the legacy triple.
    per_channel = ChannelCombineSpec.model_validate(
        {
            "members": ["left", "right"],
            "reference": "left",
            "channels": {
                "left": {
                    "lp": {"family": "butterworth", "order": 3, "fc_hz": 600.0},
                    "gain": {"mode": "manual", "db": -1.0},
                },
                "right": {
                    "hp": {"family": "butterworth", "order": 3, "fc_hz": 600.0},
                    "delay": {"mode": "manual", "ms": 0.2},
                },
            },
        }
    )
    per_channel_results = recombine_stored_results(
        updated, outcome.channel_bases, per_channel, request
    )
    per_channel_payload = per_channel_results["channels"]["combined"]["metadata"][
        "combine"
    ]
    assert per_channel_payload["type"] == "filtered_time_aligned_sum"
    assert per_channel_payload["reference"] == "left"
    assert per_channel_payload["crossovers_hz"] == [600.0]
    assert per_channel_payload["channels"]["left"]["lp"] == {
        "family": "butterworth",
        "order": 3,
        "fc_hz": 600.0,
    }
    assert per_channel_payload["gains_db"]["left"] == pytest.approx(-1.0)
    assert per_channel_payload["delays_ms"]["right"] == pytest.approx(0.2)
    assert set(per_channel_payload["pairs"]) == {"left-right"}

    with pytest.raises(RecombineError, match="outside the solved band"):
        recombine_stored_results(
            updated,
            outcome.channel_bases,
            ChannelCombineSpec(members=["left", "right"], crossovers_hz=[5000.0]),
            request,
        )
    with pytest.raises(RecombineError, match="outside the solved band"):
        recombine_stored_results(
            updated,
            outcome.channel_bases,
            ChannelCombineSpec.model_validate(
                {
                    "members": ["left", "right"],
                    "channels": {
                        "left": {
                            "lp": {"family": "lr", "order": 4, "fc_hz": 5000.0}
                        },
                        "right": {
                            "hp": {"family": "lr", "order": 4, "fc_hz": 5000.0}
                        },
                    },
                }
            ),
            request,
        )
    with pytest.raises(RecombineError, match="unknown drive channels"):
        recombine_stored_results(
            updated,
            outcome.channel_bases,
            ChannelCombineSpec(members=["left", "missing"], crossovers_hz=[500.0]),
            request,
        )


def test_driver_channel_scales_fields_and_reports_electrical_impedance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _mesh, sources, _config, frequencies_hz=None: [
            _native_result_3f() for _ in sources
        ],
    )
    driver = {
        "sd_cm2": 210.0,
        "bl_t_m": 10.5,
        "re_ohm": 5.3,
        "le_mh": 0.5,
        "mmd_g": 12.0,
        "cms_m_per_n": 4.0e-4,
        "rms_kg_per_s": 1.2,
    }
    request = _request(
        "wgi_" + "0" * 26,
        drive_channels=[
            {"id": "left", "source_ids": ["source-a", "source-b"]},
            {"id": "right", "source_ids": ["source-c"], "driver": driver},
        ],
    )
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh", encoding="utf-8")
    record = _record(mesh_path)
    record["sources"] = [
        {"id": "source-a", "required": True},
        {"id": "source-b", "required": True},
        {
            "id": "source-c",
            "required": False,
            "observed": {"total_area_mm2": 21_000.0},
        },
    ]

    async def run() -> Any:
        return await metal.MetalEngine().run(
            request,
            cancel_cb=lambda: None,
            stage_cb=lambda *_args: None,
            imported_record=record,
        )

    outcome = asyncio.run(run())
    channels = outcome.results["channels"]

    # The undriven multi-source channel keeps the unit-drive contract.
    plain = channels["left"]
    assert "impedance" not in plain
    assert "driver" not in plain["metadata"]

    driven = channels["right"]
    metadata = driven["metadata"]
    assert metadata["impedance_units"] == "ohms"
    assert metadata["impedance_quantity"] == "electrical_input_impedance"
    assert metadata["impedance_phase_convention"] == "engineering_exp_plus_jwt"
    assert metadata["impedance_drive"] == "voltage"
    assert metadata["drive"] == {"voltage_v": 2.83, "rg_ohm": 0.0}
    assert metadata["driver"]["source_id"] == "source-c"
    assert metadata["driver"]["source_area_m2"] == pytest.approx(0.021)
    assert metadata["driver"]["spec"]["sd_cm2"] == 210.0
    assert "electrical_impedance_ohm" not in metadata["driver"]
    z_real = driven["impedance"]["real"]
    assert len(z_real) == 3 and all(value > 0 for value in z_real)

    # The voltage scaling moved the absolute level away from the unit basis.
    unit_spl = plain["spl_on_axis"]["spl"]
    driven_spl = driven["spl_on_axis"]["spl"]
    assert all(
        abs(a - b) > 1.0 for a, b in zip(driven_spl, unit_spl, strict=True)
    )

    # Bases carry the scaled fields, so recombination stays consistent.
    from server.solver.combine import deserialize_channel_bases

    bundle = deserialize_channel_bases(outcome.channel_bases)
    scaled = bundle["results_by_id"]["right"].pressure_complex
    assert not np.allclose(scaled, np.ones_like(scaled) * 20.0e-6)


def test_imported_native_leak_check_follows_the_verified_ingest_record() -> None:
    """The native rim check is enabled exactly where ingestion vouched for it.

    It used to be hard-disabled for every imported solve, so a leaking reduced
    domain was mirrored and solved in silence. It now follows the ingestion
    record's own off-plane open-edge count, and stays off where an open rim is
    real geometry or where the record predates the count.
    """

    check = metal._imported_check_open_edges
    assert check({"mesh": {"integrity": {"off_plane_open_edge_count": 0}}}) is True
    assert check({"mesh": {"integrity": {"off_plane_open_edge_count": 12}}}) is False
    assert check({"mesh": {"integrity": {"valid": True}}}) is False
    assert check({"mesh": {}}) is False
    assert check({}) is False
