"""F8's managed CPU route, deterministic worker fields and durable sidecar."""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import time

import numpy as np
import pytest

from server.solver.beat_adapter.coupled_request import build_coupled_request
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.results import (
    acceleration_scale, coupled_envelope, map_coupled_sweep, ResultContractError,
)
from server.solver.context import SolverContext
from server.solver.coupled_system import ExteriorSurface, FemRegionInput
from server.mesh.fem_regions import FemRegionDomain
from server.solver import official_beat as bridge
from server.tests.beat_runtime.fake_host_worker import coupled_result
from server.tests.test_official_beat_bridge import runtime as runtime_fixture

runtime = runtime_fixture
FRAME = {"origin": [0., 0., 0.], "axis": [0., 0., 1.], "u": [1., 0., 0.], "v": [0., 1., 0.]}


def region_input(region_id="air", channel="fem", *, interface=False):
    points = np.array([[0., 0., 0.], [10., 0., 0.], [0., 10., 0.], [0., 0., 10.]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
    groups = {f"wg-fem-v1|region={region_id}|group=cone|kind=moving": faces[:1],
              f"wg-fem-v1|region={region_id}|group=walls|kind=rigid": faces[2:] if interface else faces[1:]}
    declarations = [{"group_id": "cone", "kind": "moving", "drive_channel_id": channel}]
    if interface:
        groups[f"wg-fem-v1|region={region_id}|group=port|kind=interface"] = faces[1:2]
        declarations.append({"group_id": "port", "kind": "interface"})
    record = {"region_id": region_id, "name": region_id,
              "medium": {"sound_speed_m_per_s": 343., "density_kg_per_m3": 1.21, "bulk_loss_factor": 0.},
              "surface_groups": declarations}
    return FemRegionInput(record, FemRegionDomain(region_id, points, np.array([[0, 1, 2, 3]]), groups))


def compiled(*, coupled=True, fem_channel="fem", **options):
    region = region_input(interface=coupled, channel=fem_channel)
    exterior = None
    if coupled:
        exterior = ExteriorSurface(region.domain.points_mm, region.domain.surfaces, {
            name: {"kind": "interface" if "kind=interface" in name else "moving" if "kind=rigid" in name else "rigid",
                   **({"drive_channel_id": "exterior"} if "kind=rigid" in name else {})}
            for name in region.domain.surfaces})
    return build_coupled_request(
        exterior, [region], layout=build_observations(angle_range=(0., 90., 3), sphere_grid=(3, 4), precision="float64"),
        frame=FRAME, frequencies_hz=[1000.123456, 100.], **options)


def events(request):
    yield {"type": "status", "message": "assembling"}
    for frequency in request.wire["frequencies_hz"]:
        yield {"type": "result", "result": coupled_result(request.wire, frequency)}
    yield {"type": "completed", "solved_count": len(request.wire["frequencies_hz"])}


def test_coupled_wire_passes_pinned_engine_contract():
    from beat_engine.beat_contract import validate_solve_request
    request = compiled()
    validate_solve_request(request.wire)
    assert request.wire["compiled_system"]["contract_version"] == 2
    assert list(request.channel_ports) == ["exterior", "fem"]
    assert [o["quantity"] for o in request.wire["outputs"]] == ["exterior_pressure"] * 3 + ["fem_nodal_pressure"]
    options = request.wire["solver_options"]
    assert options["precision"] == "float64"
    assert options["regular_quadrature_mode"] == "fixed"
    assert options["quadrature_order"] == options["singular_order"] == 4
    assert options["validation_diagnostics"] is False


@pytest.mark.parametrize("options,message", [
    ({"backend": "metal"}, "CPU"), ({"backend": "cuda"}, "CPU"),
    ({"precision": "float32"}, "float64"), ({"symmetry": "xy"}, "Unsupported BEAT symmetry"),
    ({"ground": object()}, "ground plane"),
])
def test_request_refusals(options, message):
    with pytest.raises(ValueError, match=message):
        compiled(**options)


def test_event_mapping_includes_a_fem_only_channel_and_preserves_complex_scale():
    request = compiled()
    sweep = map_coupled_sweep(events(request), request)
    assert list(sweep.channels) == ["exterior", "fem"]
    assert sweep.nodal_pressure.shape == (2, 2, 4)
    np.testing.assert_allclose(sweep.nodal_pressure[0, 1], 2 * (2 + 3j) * acceleration_scale(1000.123456))
    np.testing.assert_allclose(sweep.channels["fem"].pressure_complex[0], sweep.nodal_pressure[0, 1, 0])


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(freq_hz=0.), lambda r: r.update(excitation_port_ids=["wrong"]),
    lambda r: r["quantities"].reverse(), lambda r: r["diagnostics"].update(precision="float32"),
    lambda r: r["quantities"][-1]["metadata"].update(node_offsets=[1]),
    lambda r: r["quantities"][-1]["values"].update(dtype="complex64"),
    lambda r: r["quantities"][0].update(unit="dB"),
])
def test_malformed_events_refuse(mutate):
    request = compiled()
    raw = coupled_result(request.wire, request.wire["frequencies_hz"][0])
    mutate(raw)
    with pytest.raises(ResultContractError):
        map_coupled_sweep(iter([{"type": "result", "result": raw}]), request)


def test_managed_bridge_uses_fake_worker_and_reuses_session(runtime, monkeypatch):
    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    request = compiled()
    def mutate(raw):
        frequency = runtime.requests[-1]["frequencies_hz"][runtime.result_count % 2]
        raw.clear()
        raw.update(coupled_result(runtime.requests[-1], frequency))
    runtime.mutate = mutate
    progress, statuses = [], []
    first = bridge.solve_coupled_compiled(request, progress_callback=lambda *args: progress.append(args),
                                          status_callback=statuses.append)
    second = bridge.solve_coupled_compiled(request)
    assert len(first.channels["fem"].frequencies_hz) == len(second.channels["fem"].frequencies_hz) == 2
    assert len(runtime.workers) == 1
    assert progress == [(0, 2, 1000.123456), (1, 2, 100.)]
    assert statuses and all(not p.exists() for p in runtime.paths)


def test_bridge_refuses_before_worker_acquisition(runtime, monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "0")
    with pytest.raises(ValueError, match="disabled"):
        bridge.solve_coupled_compiled(compiled())
    assert not runtime.requests


def test_cancel_marker_retains_prefix_and_retires_staging(runtime, monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    request = compiled()
    runtime.mutate = lambda raw: (raw.clear(), raw.update(coupled_result(request.wire, 1000.123456)))
    sweep = bridge.solve_coupled_compiled(request, on_frequency_result=lambda *args: False)
    assert sweep.cancelled and sweep.nodal_pressure.shape == (1, 2, 4)
    assert all(stream.closed for stream in runtime.streams)
    assert all(not path.exists() for path in runtime.paths)


def test_callback_error_closes_stream(runtime, monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    request = compiled()
    runtime.mutate = lambda raw: (raw.clear(), raw.update(coupled_result(request.wire, 1000.123456)))
    def fail(*args):
        raise LookupError("caller stopped")
    with pytest.raises(LookupError, match="caller stopped"):
        bridge.solve_coupled_compiled(request, progress_callback=fail)
    assert all(stream.closed for stream in runtime.streams)


def test_sealed_interior_channel_returns_fields_without_exterior_outputs():
    request = compiled(coupled=False)
    from beat_engine.beat_contract import validate_solve_request
    validate_solve_request(request.wire)
    sweep = map_coupled_sweep(events(request), request)
    assert sweep.nodal_pressure.shape == (2, 1, 4)
    assert np.all(sweep.channels["fem"].pressure_complex == 0)


def test_node_layout_for_multiple_regions():
    request = compiled(coupled=False)
    regions = [region_input("one", "one"), region_input("two", "two")]
    request = build_coupled_request(None, regions, layout=request.layout, frame=FRAME, frequencies_hz=[100.])
    sweep = map_coupled_sweep(events(request), request)
    assert sweep.nodal_pressure.shape == (1, 2, 8)
    assert list(sweep.channels) == ["one", "two"]


def test_envelope_validates_and_contains_fem_only_drive():
    from server.jobs.result_contracts import RESULT_ENVELOPE_ADAPTER
    from server.jobs.runtime import JobRuntime
    from server.tests.test_imported_jobs import _request

    request = compiled()
    job_request = _request("wgi_" + "0" * 26)
    job_request.options.engine = "beat-cpu"
    context = SolverContext.from_imported_request(job_request, quadrants=1234, source_motion="normal")
    response = coupled_envelope(map_coupled_sweep(events(request), request), request,
        context=context, geometry=job_request.geometry, record={},
        config=bridge.response_config(request, context), started=time.time())
    enriched = JobRuntime._with_request_metadata(response, job_request)
    envelope = RESULT_ENVELOPE_ADAPTER.validate_python(enriched)
    assert envelope.result_contract_version == 2
    assert envelope.channels["fem"]["metadata"]["source_ids"] == []
    assert enriched["metadata"]["fem"] == {"engine": "beat-cpu", "precision": "float64"}
    assert "impedance" not in envelope.channels["fem"]


def test_saved_exterior_choice_is_bound_to_excluded_regions():
    from server.cadlink.joblane.setup import solve_request_for, validate_setup
    from server.tests.test_imported_jobs import _request
    request = _request("wgi_" + "0" * 26)
    geometry = request.geometry.model_dump(mode="json", exclude={
        "type", "ingest_id", "manifest_sha256", "artifact_sha256", "acknowledged_findings"})
    setup = validate_setup({"options": request.options.model_dump(mode="json"), "geometry": {**geometry, "exterior_only": True,
                                          "exterior_only_region_ids": ["one"]}})
    def bind(regions):
        return solve_request_for(setup, ingest_id=request.geometry.ingest_id,
            manifest_sha256=request.geometry.manifest_sha256, artifact_sha256=request.geometry.artifact_sha256,
            acknowledged_findings=[], fem_region_ids=regions)
    assert bind(["one"]).geometry.exterior_only
    assert not bind(["one", "new"]).geometry.exterior_only
    assert bind([]).geometry.exterior_only
    legacy = validate_setup({"options": request.options.model_dump(mode="json"), "geometry": {**geometry, "exterior_only": True}})
    assert not solve_request_for(legacy, ingest_id=request.geometry.ingest_id,
        manifest_sha256=request.geometry.manifest_sha256, artifact_sha256=request.geometry.artifact_sha256,
        acknowledged_findings=[], fem_region_ids=["new"]).geometry.exterior_only
    assert setup.geometry["exterior_only"]  # Immutable saved revision was not rewritten.


def test_fusion_defaults_accept_fem_only_design():
    from server.cadlink.joblane.default_setup import default_setup
    from server.cadlink.joblane.setup import solve_request_for
    region = region_input().record
    region["mesh_size_mm"] = 8.
    setup = default_setup({"sources": [], "scope": {"fem_regions": [region]}})
    assert setup.geometry["drive_channels"] == []
    assert not setup.geometry["exterior_only"]
    solve_request_for(setup, ingest_id="wgi_" + "0" * 26, manifest_sha256="sha256:" + "0" * 64,
                      artifact_sha256="sha256:" + "0" * 64, acknowledged_findings=[])


@pytest.mark.parametrize("backend,provider,flag,expected", [
    ("cpu", "official", None, True), ("cpu", "official", "1", True), ("cpu", "hbb", "1", True),
    ("cpu", "official", "0", False), ("metal", "official", "1", False),
    ("cuda", "official", "1", False), ("rocm", "official", "1", False),
])
def test_registry_only_offers_cpu_fem(monkeypatch, backend, provider, flag, expected):
    from server.engines.registry import _beat_engine_info
    monkeypatch.setenv("WG2_BEAT_PROVIDER", provider)
    if flag is None:
        monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    else:
        monkeypatch.setenv("WG2_FEM_REGIONS", flag)
    info = _beat_engine_info(backend, {"available": True, "state": "ready"})
    assert ("fem-regions" in info.imported_features) is expected


@pytest.mark.parametrize("change,code", [
    ("ground", "fem_domain_unsupported"),
    ("reduced", "fem_domain_unsupported"), ("reduced_transducer", "fem_domain_unsupported"),
])
def test_job_refusals(monkeypatch, change, code):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    from server.tests.test_imported_jobs import _request
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    request = _request("wgi_" + "0" * 26)
    request.options.engine = "beat-cpu"
    record = {"fem_regions": [region_input(channel="left").record]}
    if change == "ground":
        request.options.ground_plane.enabled = True
    if change == "reduced":
        record["symmetry"] = {"domain_planes": ["z0"]}
    if change == "reduced_transducer":
        # x0 alone is admitted; a transducer on the reduced model is not yet.
        record["symmetry"] = {"domain_planes": ["x0"]}
        request.geometry.drive_channels[0].exterior_transducer = object()
    refusal = _imported_request_refusal(request, record)
    assert refusal and refusal[0] == code
    if change == "reduced_transducer":
        assert "full model" in refusal[1]



@pytest.mark.parametrize("accuracy", ["fast", "accurate"])
def test_auto_dispatch_skips_gpu_for_fem(monkeypatch, tmp_path, accuracy):
    import asyncio
    from server.jobs.runtime import resolve_imported_submission
    from server.engines.registry import EngineInfo
    from server.tests.test_imported_jobs import _request, _record
    from server.tests.test_imported_beat import MESH
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    path = tmp_path / "exterior.msh"
    path.write_text(MESH)
    record = _record(path)
    record["fem_regions"] = [region_input(channel="left").record]
    record["symmetry"] = {"applied": {"cut_planes": [], "quadrants": 1234}}
    infos = [EngineInfo(name=name, version=None, available=True, reason="fixture", geometry_sources=("imported",),
                       imported_features=features, symmetry_domains=("full",))
             for name, features in (("beat-metal", ()), ("beat-cpu", ("fem-regions",)))]
    class Registry:
        async def capabilities(self):
            return infos
        async def get_engine(self, name):
            return SimpleNamespace(name=name)
    request = _request("wgi_" + "0" * 26)
    request.options.engine = "auto"
    request.options.accuracy = accuracy
    resolution = asyncio.run(resolve_imported_submission(request, Registry(),
        imported_record=record, imported_msh_text=MESH, symmetry_metadata={"resolved_quadrants": 1234}))
    assert resolution.request.options.engine == "beat-cpu"


@pytest.mark.parametrize("engine", ["bempp", "beat-metal"])
@pytest.mark.parametrize("accuracy", ["fast", "accurate"])
def test_fem_plan_offers_cpu_despite_incompatible_selected_backend(monkeypatch, tmp_path, engine, accuracy):
    import asyncio
    from server.jobs.runtime import ImportedSolveRefusal, plan_imported_submission, resolve_imported_submission
    from server.engines.registry import EngineInfo
    from server.tests.test_imported_jobs import _request, _record
    from server.tests.test_imported_beat import MESH

    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    path = tmp_path / "exterior.msh"
    path.write_text(MESH)
    record = _record(path)
    record["fem_regions"] = [region_input(channel="left", interface=True).record]
    record["symmetry"] = {"applied": {"cut_planes": [], "quadrants": 1234}}
    infos = [EngineInfo(name=name, version=None, available=True, reason="fixture", geometry_sources=("imported",),
                       imported_features=features, symmetry_domains=("full",))
             for name, features in (("bempp", ()), ("beat-metal", ()), ("beat-cpu", ("fem-regions",)))]

    class Registry:
        async def capabilities(self):
            return infos

        async def get_engine(self, name):
            assert name == "beat-cpu"
            return SimpleNamespace(name=name)

    request = _request("wgi_" + "0" * 26)
    request.options.engine = engine
    request.options.accuracy = accuracy
    kwargs = {"imported_record": record, "imported_msh_text": MESH,
              "symmetry_metadata": {"resolved_quadrants": 1234}}
    plan = asyncio.run(plan_imported_submission(request, Registry(), **kwargs))
    verdicts = {verdict["name"]: verdict for verdict in plan["engines"]}
    assert verdicts["beat-cpu"]["solves"] is True
    for name in ("bempp", "beat-metal"):
        assert verdicts[name]["solves"] is False
        assert verdicts[name]["code"] == "imported_feature_unsupported_by_engine"
        assert "coupled FEM regions (official CPU float64)" in verdicts[name]["reason"]
    assert plan["engine"] is None
    assert plan["code"] == "fem_backend_unsupported"
    assert "choose BEAT CPU, or choose exterior-only" in plan["reason"]
    with pytest.raises(ImportedSolveRefusal, match="choose BEAT CPU, or choose exterior-only") as caught:
        asyncio.run(resolve_imported_submission(request, Registry(), **kwargs))
    assert caught.value.reason_code == "fem_backend_unsupported"
    assert request.options.engine == engine


def test_sidecar_is_lossless_and_follows_result_retention(tmp_path):
    from server.jobs.store import JobStore
    from server.tests.test_jobs_store import _job, _result_envelope
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("one"))
    arrays = {"pressure": np.array([[[1 + 2j, 3 - 4j]]]), "points_mm_0": np.array([[1., 2., 3.]])}
    data = BytesIO()
    np.savez_compressed(data, **arrays)
    assert store.store_fem_field("one", data.getvalue())
    assert store.get_job_row("one")["task_metadata"]["has_fem_field_artifact"]
    with np.load(BytesIO(store.get_fem_field("one")), allow_pickle=False) as loaded:
        np.testing.assert_array_equal(loaded["pressure"], arrays["pressure"])
    store.store_results("one", _result_envelope())
    store.update_job("one", status="complete", completed_at="2000-01-01T00:00:00")
    store.prune_terminal_jobs(retention_days=1, max_terminal_jobs=100, prune_results=False)
    assert store.get_fem_field("one") is not None
    store.prune_terminal_jobs(retention_days=1, max_terminal_jobs=100, prune_results=True)
    assert store.get_fem_field("one") is None
    assert not store._fem_field_path("one").exists()


@pytest.mark.parametrize("operation", ["delete", "delete_status", "recover", "failed", "rated"])
def test_sidecar_cleanup_and_rated_retention(tmp_path, operation):
    from server.jobs.store import JobStore
    from server.tests.test_jobs_store import _job, _result_envelope
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("one", "running" if operation == "recover" else "queued"))
    store.store_fem_field("one", b"field")
    if operation == "recover":
        store.recover_on_startup("interrupted")
    elif operation == "delete":
        store.delete_job_with_event("one")
    elif operation == "delete_status":
        store.update_job("one", status="complete")
        store.delete_jobs_by_status_with_events(["complete"])
    elif operation == "failed":
        store.update_job("one", status="error")
        store.prune_terminal_jobs(retention_days=1, max_terminal_jobs=100, prune_results=False)
    else:
        store.store_results("one", _result_envelope())
        store.update_job("one", status="complete", completed_at="2000-01-01T00:00:00")
        store.mutate_job_metadata("one", {"rating": 1})
        store.prune_terminal_jobs(retention_days=1, max_terminal_jobs=0, prune_results=True)
    assert store._fem_field_path("one").exists() is (operation == "rated")


@pytest.mark.parametrize("voltage", [False, True])
def test_imported_job_dispatch_stores_fields_and_keeps_exterior_only_route(runtime, monkeypatch, tmp_path, voltage):
    import asyncio
    from dataclasses import replace
    from server.engines.registry import EngineInfo
    from server.solver.base import EngineRunResult
    from server.jobs.result_contracts import RESULT_ENVELOPE_ADAPTER
    from server.tests.test_imported_jobs import _runtime_fixture, _request
    from server.tests.test_jobs_store import _result_envelope
    from server.solver.beat_adapter import coupled_request

    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    from server.tests.test_fem_electrodynamic import driver

    built = compiled(fem_channel="exterior", **({"transducers": {"exterior": driver()}} if voltage else {}))
    built = replace(built, wire={**built.wire, "frequencies_hz": [100., 500., 1000.]})
    def compile_request(*args, **kwargs):
        assert kwargs["resolved_quadrants"] == 1234
        return built
    monkeypatch.setattr(coupled_request, "build_imported_coupled_request", compile_request)
    def mutate(raw):
        wire = runtime.requests[-1]
        index = runtime.result_count % 3
        raw.clear()
        raw.update(coupled_result(wire, wire["frequencies_hz"][index]))
    runtime.mutate = mutate
    import threading
    from server.solver import combine
    original_pack, original_serialize = np.savez_compressed, combine.serialize_channel_bases
    worker_operations = []
    loop_thread = None
    def pack(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        worker_operations.append("basis-npz" if isinstance(args[0], BytesIO) else "field-npz")
        return original_pack(*args, **kwargs)
    def serialize(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        worker_operations.append("basis")
        return original_serialize(*args, **kwargs)
    monkeypatch.setattr(np, "savez_compressed", pack)
    monkeypatch.setattr(combine, "serialize_channel_bases", serialize)
    exterior_runs = []
    class Engine:
        name = "beat-cpu"
        async def run(self, request, **kwargs):
            exterior_runs.append(request.geometry.exterior_only)
            return EngineRunResult(_result_envelope("multi_channel"))
    class Registry:
        async def capabilities(self):
            return (EngineInfo("beat-cpu", True, "fixture", "test", geometry_sources=("imported",),
                               imported_features=("fem-regions", "exterior-transducers"), symmetry_domains=("full",)),)
        async def get_engine(self, name):
            return Engine()
    async def scenario():
        nonlocal loop_thread
        loop_thread = threading.get_ident()
        job_runtime, ingest_id, record = await _runtime_fixture(tmp_path, {
            "fem_regions": [region_input(channel="exterior").record],
            "evidence": {"fem_air_volumes": [], "fem_regions": [{"region_id": "air"}]},
            "symmetry": {"cut_planes": []},
        })
        job_runtime.engine_registry = Registry()
        request = _request(ingest_id, drive_channels=[{"id": "exterior", "source_ids": ["source-a", "source-b", "source-c"]}])
        if voltage:
            request.geometry.drive_channels[0].exterior_transducer = driver()
            request.geometry.drive_channels[0].motion = "axial"
        request.options.engine = "auto"
        try:
            first = await job_runtime.submit(request)
            await job_runtime.wait_idle()
            row = job_runtime.store.get_job_row(first)
            assert row["status"] == "complete", row["error_message"]
            assert row["task_metadata"]["has_fem_field_artifact"]
            envelope = RESULT_ENVELOPE_ADAPTER.validate_python(job_runtime.store.get_results(first))
            assert envelope.channel_order == ["exterior"]
            with np.load(BytesIO(job_runtime.store.get_fem_field(first)), allow_pickle=False) as field:
                assert "experimental" not in field.files
                assert field["schema"].item() == "wg-fem-field-store-v2"
                assert field["drive_normalization"].tolist() == ["voltage_rms" if voltage else "unit_acceleration"]
                if voltage:
                    assert field["transducer_reference_voltage_v"].item() == 2.83
                else:
                    assert "transducer_reference_voltage_v" not in field.files
                assert field["pressure"].shape == (3, 1, 4)
                np.testing.assert_array_equal(field["tetrahedra_0"], [[0, 1, 2, 3]])
            assert not exterior_runs
            override = request.model_copy(deep=True)
            override.geometry.exterior_only = True
            second = await job_runtime.submit(override)
            await job_runtime.wait_idle()
            assert job_runtime.store.get_job_row(second)["status"] == "complete"
            assert job_runtime.store.get_fem_field(second) is None
            assert exterior_runs == [True]
            assert len(runtime.requests) == 1
            assert worker_operations == (["field-npz"] if voltage else ["field-npz", "basis", "basis-npz"])
        finally:
            await job_runtime.shutdown()
    asyncio.run(scenario())


def test_field_publication_failure_cleans_staging_and_never_advertises(tmp_path, monkeypatch):
    from server.jobs import store as stores
    from server.tests.test_jobs_store import _job
    store = stores.JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("one"))
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(stores.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        store.store_fem_field("one", b"field")
    assert not store.get_job_row("one")["task_metadata"].get("has_fem_field_artifact")
    assert list(store._fem_field_path("one").parent.iterdir()) == []


def test_imported_builder_reads_verified_artifacts_and_reassigns_painted_channels(tmp_path):
    import meshio
    from server.mesh.artifact import mesh_text_sha256, ImportedMeshArtifactError
    from server.solver.beat_adapter.coupled_request import build_imported_coupled_request
    request = compiled()
    region = request.regions[0]
    def publish(path, groups, tetrahedra=None):
        names = list(groups)
        cells = [("triangle", np.concatenate(list(groups.values())))]
        tags = [np.concatenate([np.full(len(groups[name]), i + 1) for i, name in enumerate(names)])]
        data = {name: [i + 1, 2] for i, name in enumerate(names)}
        if tetrahedra is not None:
            cells.append(("tetra", tetrahedra))
            tags.append(np.full(len(tetrahedra), len(names) + 1))
            data["wg-fem-v1|region=air|volume"] = [len(names) + 1, 3]
        meshio.write(path, meshio.Mesh(region.domain.points_mm, cells,
                     cell_data={"gmsh:physical": tags, "gmsh:geometrical": tags}, field_data=data),
                     file_format="gmsh22", binary=False)
        text = path.read_text()
        return {"mesh_store_path": str(path), "mesh_content_sha256": mesh_text_sha256(text),
                "mesh_size_bytes": len(text.encode())}
    fem = publish(tmp_path / "air.msh", region.domain.surfaces, region.domain.tetrahedra)
    port = next(name for name in region.domain.surfaces if "kind=interface" in name)
    rigid = next(name for name in region.domain.surfaces if "kind=rigid" in name)
    cone = next(name for name in region.domain.surfaces if "kind=moving" in name)
    painted = "wg-import-v1|tag=101|source_id=source|instance_id=null|role=LF"
    groups = {port: region.domain.surfaces[port], painted: region.domain.surfaces[rigid],
              cone: region.domain.surfaces[cone]}
    ext = publish(tmp_path / "exterior.msh", groups)
    ext["physical_names"] = {port: {"kind": "interface"}, cone: {"kind": "rigid"},
                              painted: {"kind": "moving", "drive_channel_id": "old-channel"}}
    record = {"fem_regions": [{**region.record, **fem}], "coupled_exterior": ext,
              "normalisation": {"assembly_frame_is_solver_frame": True}}
    record["fem_regions"][0]["surface_groups"][0]["drive_channel_id"] = "new-channel"
    geometry = SimpleNamespace(drive_channels=[SimpleNamespace(id="new-channel", source_ids=["source"])])
    context = SolverContext(design=None, frequency_range=(100., 1000.), num_frequencies=2)
    built = build_imported_coupled_request(record, geometry, context, frequencies_hz=[100.])
    assert list(built.channel_ports) == ["new-channel"]
    # Skipping the front cannot silently turn its rear diaphragm into a separate driver.
    with pytest.raises(ValueError, match="FEM diaphragm.*new-channel.*setup channels"):
        build_imported_coupled_request(record, SimpleNamespace(drive_channels=[]), context, frequencies_hz=[100.])
    (tmp_path / "air.msh").write_text("corrupt")
    with pytest.raises(ImportedMeshArtifactError, match="digest mismatch"):
        build_imported_coupled_request(record, geometry, context, frequencies_hz=[100.])


def test_cancel_before_first_result_and_failure_close_managed_stream(runtime, monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    runtime.cancel_after = 0
    with pytest.raises(bridge.OfficialBeatUnavailable, match="before any results"):
        bridge.solve_coupled_compiled(compiled())
    assert all(stream.closed for stream in runtime.streams)
    runtime.cancel_after = None
    runtime.fail = True
    with pytest.raises(ResultContractError, match="assembly failed"):
        bridge.solve_coupled_compiled(compiled())
    assert all(stream.closed for stream in runtime.streams)


def test_combined_exterior_channels_keep_the_existing_filtered_sum():
    from server.tests.test_imported_jobs import _request
    from server.jobs.models import ChannelCombineSpec
    request = compiled()
    region = region_input("second", "right")
    built = build_coupled_request(None, [region_input("first", "left"), region],
                                 layout=request.layout, frame=FRAME, frequencies_hz=[100., 1000.])
    job = _request("wgi_" + "0" * 26)
    job.geometry.combine = ChannelCombineSpec.model_validate({
        "id": "system", "members": ["left", "right"], "crossovers_hz": [500.],
        "level_match": False, "align": False})
    context = SolverContext.from_imported_request(job, quadrants=1234, source_motion="normal")
    response = coupled_envelope(map_coupled_sweep(events(built), built), built,
                               context=context, geometry=job.geometry, record={},
                               config=bridge.response_config(built, context), started=time.time())
    assert response["channel_order"] == ["left", "right", "system"]
    assert response["channels"]["system"]["metadata"]["derived_from_channels"] == ["left", "right"]
    assert response["metadata"]["solver_engine"]["formulation"] == "interior_fem"


@pytest.mark.parametrize("coupled,cancel", [(True, False), (True, True), (False, False)])
def test_fake_host_worker_contract_negotiation_without_sockets(tmp_path, monkeypatch, coupled, cancel):
    from server.solver.beat_runtime import manager, readiness, registry
    from server.tests.beat_runtime.fake_host_worker import EngineWorker, events as recorded_events
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    key = registry.host_key({"backend": "cpu", "julia_executable": str(tmp_path / "julia"),
        "julia_identity": "fixture", "solver_script": str(tmp_path / "solver.jl"),
        "julia_project": str(tmp_path / "project"), "julia_sysimage": None, "julia_threads": 2,
        "engine_fingerprint": "fixture", "runtime_fingerprint": "fixture",
        "environment": {"TEST_COUPLED": "1", "TEST_EVENTS": str(tmp_path / "events"),
                        **({"TEST_COUPLED_PAUSE_AFTER_FIRST": "1"} if cancel else {})}})
    monkeypatch.setattr(manager, "resolve_key", lambda *a, **k: key)
    monkeypatch.setattr(readiness, "backend_readiness", lambda *a, **k:
                        readiness.BackendReadiness(True, "ready", "fixture proof"))
    owned = manager.WorkerManager(mode="child", engine_factory=EngineWorker)
    isolated_manager = SimpleNamespace(get_worker=lambda *a, **k: owned.get_worker(
        *a, **k, environment=dict(key["environment"])))
    try:
        result = bridge.solve_coupled_compiled(compiled(coupled=coupled), worker_manager=isolated_manager,
                     on_frequency_result=(lambda *a: False) if cancel else None)
        assert result.cancelled is cancel
        assert result.nodal_pressure.shape[0] == (1 if cancel else 2)
        assert any(e["type"] == "closed" and e["terminal"] for e in recorded_events(key))
    finally:
        owned.shutdown()


def admission(request, regions, *, sources=("source-a", "source-b", "source-c")):
    from server.jobs.runtime.imported_refusals import _imported_request_refusal
    return _imported_request_refusal(request, {
        "fem_regions": [r.record for r in regions],
        "source_tags": {s: i + 101 for i, s in enumerate(sources)},
        "domain_interpretation": {"observations": {"rigid_rims": []}},
    })


@pytest.fixture
def fem_admission(monkeypatch):
    from server.tests.test_imported_jobs import _request
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    request = _request("wgi_" + "0" * 26)
    request.options.engine = "beat-cpu"
    return request


@pytest.mark.parametrize("same_region", [False, True])
def test_admission_refuses_multiple_fem_diaphragms_on_one_channel(fem_admission, same_region):
    regions = [region_input("front", "left"), region_input("rear", "left")]
    if same_region:
        regions = regions[:1]
        regions[0].record["surface_groups"].append({
            "group_id": "second", "kind": "moving", "drive_channel_id": "left"})
    code, message = admission(fem_admission, regions)
    assert code == "fem_multiple_diaphragms_unsupported"
    assert "left" in message and "front/cone" in message
    assert ("front/second" if same_region else "rear/cone") in message
    assert "more than one FEM diaphragm on one drive channel is not supported yet" in message


def test_painted_front_and_one_fem_rear_share_one_ideal_port(fem_admission, monkeypatch):
    # Admission proceeds beyond the cheap drive checks; avoid unrelated mesh/frame checks.
    from server.jobs.runtime import imported_refusals
    monkeypatch.setattr(imported_refusals, "_imported_open_half_refusal", lambda *args: None)
    assert admission(fem_admission, [region_input("rear", "left")]) is None
    request = compiled()
    exterior_groups = request.wire["compiled_system"]["components"]
    assert len(exterior_groups) == 2
    region = region_input(interface=True, channel="exterior")
    exterior = ExteriorSurface(region.domain.points_mm, region.domain.surfaces, {
        name: {"kind": "interface" if "kind=interface" in name else "moving" if "kind=rigid" in name else "rigid",
               **({"drive_channel_id": "exterior"} if "kind=rigid" in name else {})}
        for name in region.domain.surfaces})
    shared = build_coupled_request(exterior, [region], layout=request.layout, frame=FRAME, frequencies_hz=[100.])
    assert list(shared.channel_ports) == ["exterior"]
    assert len(shared.wire["compiled_system"]["components"][0]["boundary_ids"]) == 2


def test_runtime_builder_backstop_refuses_two_fem_diaphragms():
    request = compiled(coupled=False)
    with pytest.raises(ValueError, match="more than one FEM diaphragm"):
        build_coupled_request(None, [region_input("front", "left"), region_input("rear", "left")],
                              layout=request.layout, frame=FRAME, frequencies_hz=[100.])


@pytest.mark.parametrize("channels", [[], [{"id": "renamed", "source_ids": ["source-a", "source-b", "source-c"]}]])
def test_fem_channel_must_follow_painted_setup(fem_admission, channels):
    from server.jobs.models import DriveChannel
    fem_admission.geometry.drive_channels = [DriveChannel.model_validate(c) for c in channels]
    code, message = admission(fem_admission, [region_input("rear", "left")])
    assert code == "fem_channel_mismatch"
    assert "rear/cone" in message and "left" in message and "setup channels" in message
    if channels:
        assert "renamed" in message


def test_fem_only_design_retains_its_channels(fem_admission, monkeypatch):
    from server.jobs.runtime import imported_refusals
    monkeypatch.setattr(imported_refusals, "_imported_open_half_refusal", lambda *args: None)
    fem_admission.geometry.drive_channels = []
    assert admission(fem_admission, [region_input("rear", "standalone")], sources=()) is None


@pytest.mark.parametrize("fem_channel", ["system", "left"])
def test_admission_refuses_combine_collision_before_engine_selection(fem_admission, fem_channel):
    import asyncio
    from server.jobs.models import ChannelCombineSpec
    from server.jobs.runtime import resolve_imported_submission, ImportedSolveRefusal
    fem_admission.geometry.combine = ChannelCombineSpec.model_validate({
        "id": fem_channel, "members": ["left", "right"], "crossovers_hz": [500.]})
    class NoEngine:
        async def capabilities(self):
            raise AssertionError("must refuse before selecting an engine")
    record = {"fem_regions": [region_input("rear", fem_channel).record],
              "source_tags": {"source-a": 101, "source-b": 102, "source-c": 103}}
    with pytest.raises(ImportedSolveRefusal, match="fem_combine_channel_collision.*" + fem_channel):
        asyncio.run(resolve_imported_submission(fem_admission, NoEngine(), imported_record=record))


@pytest.mark.parametrize("driven,code", [(False, "fem_no_driven_surface"), (True, "fem_missing_channels")])
def test_cheap_admission_checks_driven_surface_and_missing_channels(fem_admission, driven, code):
    region = region_input("rear", "left")
    if not driven:
        region.record["surface_groups"] = []
    refusal = admission(fem_admission, [region], sources=())
    assert refusal[0] == code
    assert ("right" if driven else "no driven surface") in refusal[1]


@pytest.mark.parametrize("change", ["driver", "weights", "axial", "cardioid"])
def test_stricter_fem_drive_limit_is_explicit(fem_admission, change):
    channel = fem_admission.geometry.drive_channels[0]
    if change == "driver":
        channel.driver = object()
    elif change == "weights":
        channel.patch_weights = {"source-a": 1.}
    elif change == "axial":
        channel.motion = "axial"
    else:
        fem_admission.geometry.passive_cardioid_rear_volume_l = 10.
    refusal = admission(fem_admission, [region_input("rear", "left")])
    assert refusal[0] == "fem_drive_unsupported"
    assert {"driver": "legacy driver", "weights": "patch weights", "axial": "axial ideal",
            "cardioid": "passive-cardioid"}[change] in refusal[1]


@pytest.mark.parametrize("channel_index", [0, 1])
def test_exterior_transducer_is_admitted_in_coupled_mode(fem_admission, channel_index, monkeypatch):
    monkeypatch.setattr("server.jobs.runtime.imported_refusals._imported_open_half_refusal", lambda *args: None)
    channel = fem_admission.geometry.drive_channels[channel_index]
    channel.exterior_transducer = object()
    refusal = admission(fem_admission, [region_input("rear", "left")])
    assert refusal is None


def test_admission_names_mixed_media(fem_admission):
    regions = [region_input("front", "left"), region_input("rear", "right")]
    regions[1].record["medium"]["sound_speed_m_per_s"] = 340.
    assert admission(fem_admission, regions)[0] == "fem_mixed_media"


@pytest.mark.parametrize("operation", ["replace", "compress"])
def test_streamed_field_publication_failure_is_atomic(tmp_path, monkeypatch, operation):
    from server.jobs import store as stores
    from server.tests.test_jobs_store import _job
    store = stores.JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("one"))
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(stores.os if operation == "replace" else np,
                        "replace" if operation == "replace" else "savez_compressed", fail)
    with pytest.raises(OSError, match="disk full"):
        store.store_fem_field_arrays("one", {"pressure": np.array([[[1 + 2j]]])})
    assert not store.get_job_row("one")["task_metadata"].get("has_fem_field_artifact")
    assert list(store._fem_field_path("one").parent.iterdir()) == []


def test_fem_field_compression_runs_without_the_store_lock(tmp_path):
    import threading
    from server.jobs import store as stores
    from server.tests.test_jobs_store import _job

    store = stores.JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(_job("one"))
    lock_free = []

    def write(stream):
        # Another thread must be able to take the store lock while this runs.
        result = []

        def take_and_release():
            got = store._lock.acquire(timeout=1)
            if got:
                store._lock.release()
            result.append(got)

        probe = threading.Thread(target=take_and_release)
        probe.start()
        probe.join()
        lock_free.append(bool(result and result[0]))
        stream.write(b"field")

    assert store._publish_fem_field("one", write)
    assert lock_free == [True]
    assert store.get_fem_field("one") == b"field"


def test_fem_without_setup_channels_or_diaphragms_has_named_refusal(fem_admission):
    fem_admission.geometry.drive_channels = []
    region = region_input()
    region.record["surface_groups"] = []
    assert admission(fem_admission, [region], sources=())[0] == "fem_no_driven_surface"


@pytest.mark.parametrize("enabled", [False, True])
def test_registry_uses_shared_fem_flag_helper(monkeypatch, enabled):
    from server.engines.registry import _beat_engine_info
    from server.platform import feature_flags
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setenv("WG2_FEM_REGIONS", "0" if enabled else "1")
    monkeypatch.setattr(feature_flags, "fem_regions_enabled", lambda: enabled)
    info = _beat_engine_info("cpu", {"available": True, "state": "ready"})
    assert ("fem-regions" in info.imported_features) is enabled


@pytest.mark.parametrize("engine,available,features", [
    (None, False, ()), ("beat-cpu", False, ("fem-regions",)),
    ("beat-metal", True, ()), ("beat-cpu", True, ()),
])
def test_default_fem_admission_refuses_without_a_capable_engine(
    tmp_path, monkeypatch, engine, available, features,
):
    import asyncio
    from server.engines.registry import EngineInfo
    from server.jobs.runtime.core import EngineUnavailableError, resolve_imported_submission
    from server.tests.test_imported_jobs import _request, _record
    from server.tests.test_imported_beat import MESH

    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    class Registry:
        async def capabilities(self):
            return () if engine is None else (EngineInfo(
                engine, available, "fixture", None, geometry_sources=("imported",),
                imported_features=features, symmetry_domains=("full",)),)
        async def get_engine(self, name):
            pytest.fail("a refused FEM job must not acquire an engine")
    request = _request("wgi_" + "0" * 26)
    request.options.engine = "auto"
    request.geometry.drive_channels = request.geometry.drive_channels[:1]
    path = tmp_path / "exterior.msh"
    path.write_text(MESH)
    record = _record(path)
    record["fem_regions"] = [region_input(channel="left").record]
    record["symmetry"] = {"applied": {"cut_planes": [], "quadrants": 1234}}
    with pytest.raises(EngineUnavailableError, match="available official BEAT CPU engine"):
        asyncio.run(resolve_imported_submission(request, Registry(), imported_record=record,
                                                imported_msh_text=MESH))


@pytest.mark.parametrize("state,remedy", [
    ("preparing", "wait for BEAT CPU preparation to finish"),
    ("unprepared", "Prepare BEAT CPU"),
])
def test_fem_unavailable_remedy_tracks_cpu_preparation(monkeypatch, state, remedy):
    import asyncio
    from server.engines.registry import EngineInfo
    from server.jobs.runtime import EngineUnavailableError, resolve_imported_submission
    from server.tests.test_imported_jobs import _request

    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    class Registry:
        async def capabilities(self):
            return (EngineInfo("beat-cpu", False, "fixture", None, runtime_state=state),)
    request = _request("wgi_" + "0" * 26)
    request.options.engine = "beat-cpu"
    request.geometry.drive_channels = []
    record = {"fem_regions": [region_input().record],
              "domain_interpretation": {"observations": {"planes": {
                  plane: {"rigid_cut_rim_edges": 0, "solver_plane": plane}
                  for plane in ("x0", "y0", "z0")}}}}
    with pytest.raises(EngineUnavailableError, match=remedy):
        asyncio.run(resolve_imported_submission(request, Registry(), imported_record=record))
