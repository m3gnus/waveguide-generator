from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.design.schema import DesignConfig
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs import mesh_provenance
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.tests.test_jobs_api import _complete_job, _request, _request_with_headers
from server.tests.test_jobs_runtime import _bare_request, _CompletingBempp
from server.app import create_app

_CURRENT_HASH = "sha256:" + "a" * 64
_OLD_HASH = "sha256:" + "b" * 64


def test_real_mesher_uses_the_same_geometry_identity_as_cad_export() -> None:
    from server.exports.geometry_identity import geometry_hash_for_design, mesher_version

    design = DesignConfig.model_validate({"formula": "OSSE", "L": 120})
    changed = DesignConfig.model_validate({"formula": "OSSE", "L": 160})
    provenance = mesh_provenance.record_for_design(design)
    assert provenance is not None
    assert provenance["mesher_version"] == mesher_version()
    assert provenance["geometry_hash"] == geometry_hash_for_design(design)
    assert mesh_provenance.classify_open(provenance, design) == "current"
    assert mesh_provenance.classify_open(provenance, changed) == "stale"


def test_identity_uses_the_shared_geometry_hash_and_one_version(monkeypatch) -> None:
    design = DesignConfig.model_validate({"formula": "OSSE"})
    seen = []
    monkeypatch.setattr(mesh_provenance, "mesher_version", lambda: "1.2.3")
    monkeypatch.setattr(mesh_provenance, "mesher_identity", lambda: "git:source")
    monkeypatch.setattr(mesh_provenance, "geometry_hash_for_design", lambda value, *, mesher_version_value: seen.append((value, mesher_version_value)) or _CURRENT_HASH)
    record = mesh_provenance.record_for_design(design)
    assert record == {"mesher_version": "1.2.3", "geometry_hash": _CURRENT_HASH, "mesher_source": "git:source"}
    assert mesh_provenance.classify_open(record, design) == "current"
    assert seen == [(design, "1.2.3"), (design, "1.2.3")]
    assert mesh_provenance.classify_open({**record, "mesher_version": "1.2.2"}, design) == "stale"
    assert mesh_provenance.classify_open({**record, "geometry_hash": _OLD_HASH}, design) == "stale"
    assert mesh_provenance.classify_open({**record, "mesher_source": "git:older"}, design) == "stale"
    assert mesh_provenance.classify_open({**record, "geometry_hash": "malformed"}, design) == "unknown"
    assert mesh_provenance.classify_open({**record, "mesher_source": "unmeasured:nonce"}, design) == "unknown"
    monkeypatch.setattr(mesh_provenance, "mesher_identity", lambda: "unmeasured:nonce")
    assert mesh_provenance.classify_open(record, design) == "unknown"
    assert "mesher_source" not in mesh_provenance.record_for_design(design)


@pytest.mark.parametrize("provenance", [None, {}, {"mesher_version": "1"}, {"geometry_hash": "sha256:a"}, {"mesher_version": "unknown", "geometry_hash": "sha256:a"}])
def test_missing_evidence_never_claims_a_stale_mesh(provenance, monkeypatch) -> None:
    monkeypatch.setattr(mesh_provenance, "mesher_version", lambda: "2")
    assert mesh_provenance.classify_open(provenance, DesignConfig.model_validate({"formula": "OSSE"})) == "unknown"


def test_unknown_current_version_never_claims_a_stale_mesh(monkeypatch) -> None:
    monkeypatch.setattr(mesh_provenance, "mesher_version", lambda: "unknown")
    assert mesh_provenance.classify_open({"mesher_version": "1", "geometry_hash": _CURRENT_HASH}, DesignConfig.model_validate({"formula": "OSSE"})) == "unknown"
    assert mesh_provenance.record_for_design(DesignConfig.model_validate({"formula": "OSSE"})) is None


def test_parametric_solve_persists_the_same_provenance_on_job_and_result(tmp_path: Path, monkeypatch) -> None:
    from server.jobs.runtime import core

    provenance = {"mesher_version": "1.2.3", "geometry_hash": "sha256:geometry"}
    monkeypatch.setattr(core, "record_for_design", lambda _design: provenance)

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"), engine_registry=EngineRegistry(
            detector=lambda: [EngineInfo("bempp", True, "test", "test")],
            factory=lambda _name: _CompletingBempp(),
        ))
        try:
            job_id = await runtime.submit(_bare_request(engine="bempp"))
            await runtime.wait_idle()
            assert (await runtime.get_job(job_id))["provenance"] == provenance
            result = await runtime.get_results(job_id)
            assert all(result["provenance"][key] == value for key, value in provenance.items())
            assert result["provenance"]["request_sha256"]
            assert runtime.store.get_job_row(job_id)["task_metadata"]["provenance"] == provenance
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_provenance_updates_preserve_other_producers_fields(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    try:
        store.create_job(_complete_job("job"))
        store.mutate_job_metadata("job", {"provenance": {"solver_source": {"backend": "metal"}}})
        store.mutate_job_metadata("job", {"provenance": {"mesher_version": "1", "geometry_hash": "sha256:a"}})
        assert store.get_job_row("job")["task_metadata"]["provenance"] == {
            "solver_source": {"backend": "metal"}, "mesher_version": "1", "geometry_hash": "sha256:a",
        }
    finally:
        store.close()


def test_provenance_failure_does_not_fail_the_solve(tmp_path: Path, monkeypatch) -> None:
    from server.jobs.runtime import core

    def unavailable(_design):
        raise ValueError("identity unavailable")

    monkeypatch.setattr(core, "record_for_design", unavailable)

    async def scenario() -> None:
        runtime = JobRuntime(JobStore(tmp_path / "jobs.db"), engine_registry=EngineRegistry(
            detector=lambda: [EngineInfo("bempp", True, "test", "test")],
            factory=lambda _name: _CompletingBempp(),
        ))
        try:
            job_id = await runtime.submit(_bare_request(engine="bempp"))
            await runtime.wait_idle()
            assert (await runtime.get_job(job_id))["status"] == "complete"
            assert await runtime.mesh_freshness(job_id) == "unknown"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_mesh_freshness_endpoint_handles_old_changed_and_missing_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mesh_provenance, "mesher_version", lambda: "2")
    monkeypatch.setattr(mesh_provenance, "geometry_hash_for_design", lambda *_args, **_kwargs: _CURRENT_HASH)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path, solver_warmup=False)
        runtime = app.state.jobs_runtime
        await runtime.start()
        try:
            old = _complete_job("old")
            current = _complete_job("current")
            current["task_metadata"]["provenance"] = {"mesher_version": "2", "geometry_hash": _CURRENT_HASH}
            changed = _complete_job("changed")
            changed["task_metadata"]["provenance"] = {"mesher_version": "1", "geometry_hash": _OLD_HASH}
            for row in (old, current, changed):
                runtime.store.create_job(row)
            for job_id, expected in (("old", "unknown"), ("current", "current"), ("changed", "stale")):
                status, raw, headers = await _request_with_headers(app, "GET", f"/api/jobs/{job_id}/mesh-freshness")
                assert status == 200
                assert json.loads(raw) == {"freshness": expected}
                assert headers["cache-control"] == "no-store"
            status, _ = await _request(app, "GET", "/api/jobs/missing/mesh-freshness")
            assert status == 404
            monkeypatch.setattr(mesh_provenance, "geometry_hash_for_design", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad geometry")))
            status, raw = await _request(app, "GET", "/api/jobs/current/mesh-freshness")
            assert status == 200
            assert json.loads(raw) == {"freshness": "unknown"}
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())
