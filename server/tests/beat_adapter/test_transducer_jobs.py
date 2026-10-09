"""Voltage job acceptance, geometry refusal and native result semantics."""

import numpy as np
import pytest
from pydantic import ValidationError

from server.jobs.models import DriveChannel, SolveRequest
from server.solver import beat_transducer_imported as jobs
from server.solver.beat_adapter.transducers import TransducerSweep, parse_transducer_frequency
from server.tests.beat_adapter.test_transducers import raw

PARAMETERS = dict(
    version=1,
    re_ohm=6.0,
    le_h=0.0005,
    bl_n_per_a=7.0,
    mmd_kg=0.02,
    cms_m_per_n=0.001,
    rms_n_s_per_m=1.5,
    motion_axis=[1.0, 0.0, 0.0],
)


def request(**changes):
    wire = dict(
        geometry=dict(
            type="imported",
            ingest_id="wgi_" + "0" * 26,
            manifest_sha256="sha256:" + "1" * 64,
            artifact_sha256="sha256:" + "2" * 64,
            drive_channels=[
                dict(
                    id="driver",
                    source_ids=["a", "b"],
                    motion="axial",
                    exterior_transducer=PARAMETERS,
                ),
                dict(
                    id="shorted",
                    source_ids=["c", "d"],
                    motion="axial",
                    exterior_transducer=PARAMETERS,
                ),
            ],
            mesh=dict(
                rigid_size_mm=8.0, transition_mm=20.0, source_size_mm={s: 3.0 for s in "abcd"}
            ),
        ),
        options=dict(
            engine="beat-cpu",
            frequencies_hz=[100.0, 500.0],
            polar_config=dict(
                enabled_axes=["horizontal"], spherical_theta_count=5, spherical_phi_count=8
            ),
        ),
    )
    for key, value in changes.items():
        wire[key] = value
    return SolveRequest.model_validate(wire)


@pytest.fixture
def solid(make_mesh):
    return make_mesh(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
        [2, 3, 4, 5],
    )


@pytest.fixture
def record():
    return {
        "source_tags": dict(a=2, b=3, c=4, d=5),
        "symmetry": {"cut_planes": []},
        "normalisation": {"assembly_frame_is_solver_frame": True},
    }


def test_legacy_channel_serialization_exact():
    assert DriveChannel(id="old", source_ids=["a"]).model_dump(mode="json") == {
        "id": "old",
        "source_ids": ["a"],
        "motion": "normal",
        "driver": None,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("mmd_kg", 0.0),
        ("re_ohm", True),
        ("le_h", -1.0),
        ("motion_axis", [0.0, 0.0, 0.0]),
        ("cms_m_per_n", float("nan")),
    ],
)
def test_refuse_invalid_si_model(field, value):
    with pytest.raises(ValidationError):
        DriveChannel(
            id="d",
            source_ids=["a"],
            motion="axial",
            exterior_transducer={**PARAMETERS, field: value},
        )


@pytest.mark.parametrize(
    "option,value",
    [
        ("engine", "auto"),
        ("engine", "metal"),
        ("adaptive_frequency_sampling", True),
        ("mesh_ladder", "auto"),
        ("ground_plane", {"enabled": True}),
    ],
)
def test_refuse_unsupported_job_options(option, value):
    payload = request().model_dump(mode="json")
    payload["options"][option] = value
    with pytest.raises(ValidationError, match="exterior transducer"):
        SolveRequest.model_validate(payload)


def test_hybrid_and_ceiling_refused():
    payload = request().model_dump(mode="json")
    payload["geometry"]["drive_channels"][1].pop("exterior_transducer")
    with pytest.raises(ValidationError, match="every drive channel"):
        SolveRequest.model_validate(payload)
    payload = request().model_dump(mode="json")
    payload["geometry"]["max_drive_voltage_v"] = 10.0
    with pytest.raises(ValidationError, match="ceilings"):
        SolveRequest.model_validate(payload)


def test_geometry_preflight_is_opt_in_and_no_axis_guess(solid, record, monkeypatch):
    monkeypatch.setattr(jobs, "official_selected", lambda: True)
    channels = request().geometry.drive_channels
    assert jobs.preflight(record, solid, channels, backend="cpu") is None
    assert jobs.preflight(
        {**record, "symmetry": {"cut_planes": ["x0"]}}, solid, channels, backend="cpu"
    )
    assert jobs.preflight(record, solid, channels, backend="cuda")
    inward = solid.replace("1 3 2", "1 2 3")
    assert jobs.preflight(record, inward, channels, backend="cpu")
    monkeypatch.setattr(jobs, "official_selected", lambda: False)
    assert jobs.preflight(record, solid, channels, backend="cpu")


def test_job_route_keeps_rms_pressure_all_coupled_traces_and_matrix(solid, record, monkeypatch):
    from server.solver import official_beat

    monkeypatch.setattr(jobs, "official_selected", lambda: True)
    captured, partial = [], []

    def solve(compiled, **kwargs):
        captured.append(compiled)
        rows = tuple(
            parse_transducer_frequency(raw(compiled, f), compiled, f) for f in [100.0, 500.0]
        )
        kwargs["on_frequency_result"](TransducerSweep(rows[:1], False, 2))
        kwargs["on_frequency_result"](TransducerSweep(rows, False, 2))
        return TransducerSweep(rows, False, 2)

    monkeypatch.setattr(official_beat, "solve_transducer_compiled", solve)
    # A second client-side driver calculation must never run.
    monkeypatch.setattr(
        "server.solver.imported_channels.apply_channel_driver",
        lambda *a, **k: pytest.fail("second LEM"),
    )
    result = jobs.solve(
        solid,
        request(),
        record,
        backend="cpu",
        result_callback=lambda revision, frame: partial.append((revision, frame)),
    )
    assert captured[0].wire["solver_options"]["precision"] == "float64"
    assert captured[0].wire["compiled_system"]["components"][0]["parameters"]["motion_axis"] == [
        1.0,
        0.0,
        0.0,
    ]
    assert result["channel_order"] == ["driver", "shorted"]
    channel = result["channels"]["driver"]
    assert channel["spl_on_axis"]["spl"][0] == pytest.approx(20 * np.log10(abs(2 + 3j) / 20e-6))
    assert channel["impedance"]["imaginary"][0] > 0  # one conversion to engineering convention
    assert channel["metadata"]["impedance_units"] == "ohms"
    trace = channel["metadata"]["beat_transducer"]
    assert trace["transducer_ids"] == ["transducer:driver", "transducer:shorted"]
    assert len(trace["velocity_rms_m_per_s"][0]) == 2
    assert result["metadata"]["radiation_impedance_matrix"]["values"][0][0][1]["imaginary"] == -1
    assert partial[0][1]["channels"]["driver"]["frequencies"] == [100.0]
    from server.jobs.runtime import _extend_provisional_results

    accumulated = {}
    for revision, frame in partial:
        _extend_provisional_results(accumulated, frame)
    assert [revision for revision, _ in partial] == [0, 1]
    assert accumulated["frequencies"] == [100.0, 500.0]
    assert accumulated["channels"]["driver"]["frequencies"] == [100.0, 500.0]
    assert result["_field_trace_unavailable_reason"]


def test_metal_wire_precision_echo_and_mixed_quantity_dtypes(solid, record, monkeypatch):
    from server.solver.beat_adapter.observations import build_observations
    from server.solver.beat_adapter.transducers import build_transducer_request
    from server.tests.beat_adapter.conftest import wire

    frame = {"origin": [0, 0, 0], "axis": [0, 0, 1], "u": [1, 0, 0], "v": [0, 1, 0]}
    compiled = build_transducer_request(
        solid,
        drivers=jobs.drivers_for(record, request().geometry.drive_channels),
        layout=build_observations(sphere_grid=None),
        frame=frame,
        frequencies_hz=[100.1234567],
        backend="metal",
        precision="float32",
    )
    data = raw(compiled, float(np.float32(100.1234567)))
    for quantity in data["quantities"]:
        if quantity["quantity"] == "exterior_pressure":
            shape = quantity["values"]["shape"]
            quantity["values"] = wire(np.full(shape, 2 + 3j), "float32")
    row = parse_transducer_frequency(data, compiled, 100.1234567)
    assert row.frequency_hz == 100.1234567
    assert row.current_rms_a.dtype == np.complex128


def test_capability_requires_official_provider(monkeypatch):
    from server.engines.registry import _beat_engine_info
    from server.jobs.runtime import _imported_features_needed, _imported_capability_blocker

    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    needed = _imported_features_needed(request().geometry)
    assert needed == {"exterior-transducers"}
    for backend in ["cpu", "metal"]:
        assert "exterior-transducers" in _beat_engine_info(backend, {}).imported_features
    assert "exterior-transducers" not in _beat_engine_info("cuda", {}).imported_features
    monkeypatch.delenv("WG2_BEAT_PROVIDER")
    info = _beat_engine_info("cpu", {})
    assert (
        _imported_capability_blocker(info, 1234, needed)[0]
        == "imported_feature_unsupported_by_engine"
    )


def test_result_phase_and_cancelled_prefix(solid, record, monkeypatch):
    from server.solver import official_beat

    monkeypatch.setattr(jobs, "official_selected", lambda: True)

    def solve(compiled, **kwargs):
        row = parse_transducer_frequency(raw(compiled, 100.0), compiled, 100.0)
        return TransducerSweep((row,), True, 2)

    monkeypatch.setattr(official_beat, "solve_transducer_compiled", solve)
    result = jobs.solve(solid, request(), record, backend="cpu")
    assert result["metadata"]["cancelled"] is True
    assert result["frequencies"] == [100.0]
    metadata = result["channels"]["driver"]["metadata"]
    assert metadata["phase_time_convention"] == "exp(+ikr)"
    assert metadata["impedance_phase_convention"] == "engineering_exp_plus_jwt"


def test_job_persistence_and_retry_use_the_explicit_new_contract(tmp_path, solid, monkeypatch):
    import asyncio
    import json
    from server.engines.registry import EngineInfo
    from server.solver.beat import BeatEngine
    from server.solver import official_beat
    from server.tests.test_imported_jobs import _runtime_fixture, _geometry

    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")

    def native(compiled, **kwargs):
        rows = []
        for f in compiled.wire["frequencies_hz"]:
            rows.append(parse_transducer_frequency(raw(compiled, f), compiled, f))
            kwargs["on_frequency_result"](TransducerSweep(tuple(rows), False, 2))
        return TransducerSweep(tuple(rows), False, 2)

    monkeypatch.setattr(official_beat, "solve_transducer_compiled", native)

    class Registry:
        async def capabilities(self):
            return (
                EngineInfo(
                    name="beat-cpu",
                    available=True,
                    reason="fixture",
                    version="test",
                    geometry_sources=("parametric", "imported"),
                    imported_features=("exterior-transducers",),
                    symmetry_domains=("full",),
                ),
            )

        async def get_engine(self, name):
            return BeatEngine(backend="cpu")

        async def unavailable_reason(self, name):
            return None

    async def scenario():
        from server.mesh.imported import polar_grid_from_symmetry

        symmetry = {"cut_planes": [], "planes": {}}
        runtime, ingest_id, _ = await _runtime_fixture(
            tmp_path,
            record_changes={
                "symmetry": symmetry,
                "polar_grid_derivation": polar_grid_from_symmetry(symmetry),
                "source_tags": {"source-a": 2, "source-b": 3, "source-c": 4},
            },
            mesh_text=solid,
        )
        runtime.engine_registry = Registry()
        geometry = _geometry(ingest_id)
        geometry["drive_channels"] = [
            dict(
                id="left",
                source_ids=["source-a", "source-b"],
                motion="axial",
                exterior_transducer=PARAMETERS,
            ),
            dict(
                id="right", source_ids=["source-c"], motion="axial", exterior_transducer=PARAMETERS
            ),
        ]
        submitted = SolveRequest.model_validate(
            {
                "geometry": geometry,
                "options": {
                    "engine": "beat-cpu",
                    "frequencies_hz": [100.0, 500.0],
                    "polar_config": {"angle_range": [-180.0, 180.0, 37]},
                },
            }
        )
        try:
            job_id = await runtime.submit(submitted)
            for _ in range(100):
                row = runtime.store.get_job_row(job_id)
                if row["status"] in {"complete", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.01)
            assert row["status"] == "complete", row.get("error")
            text = runtime.store.get_results_text(job_id)
            persisted = json.loads(text)
            assert persisted["frequencies"] == [100.0, 500.0]
            assert persisted["metadata"]["beat_transducer_contract"] == 1
            assert persisted["channels"]["left"]["metadata"]["beat_transducer"]["version"] == 1
            item = await runtime.get_job(job_id)
            assert item["cad_setup"]["drive_channels"][0]["exterior_transducer"]["mmd_kg"] == 0.02
            child = await runtime.retry(job_id)
            assert child != job_id
            replay = runtime.store.get_job_row(child)
            assert replay["parent_job_id"] == job_id
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())
