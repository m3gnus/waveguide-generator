"""User choices are durable CAD requests, including across the old error format."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from server.jobs.cad_preparation import job_cad_state
from server.jobs.models import CadSolveAgainRequest
from server.jobs.runtime import JobConflictError
from server.jobs.store import JobStore
from server.mesh.builder import _enforce_artifact_triangle_ceiling
from server.mesh.errors import MeshTooLarge

from cad_backends import JobsHarness
from test_cad_preparation import _received, _revision, _setup
from test_job_cad_lane import _cad, _row, _settle, _second_return


@pytest.fixture
def h(tmp_path: Path):
    harness = JobsHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def test_clear_failed_preserves_new_and_legacy_holds(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    job = h.latest_job()
    state = job_cad_state(job)
    assert state is not None and state["held"] is True
    assert state["next_step"] and state["options"]
    assert (job["status"], job["stage"], job["stage_message"]) == ("held", "held", "Waiting for you")
    assert _cad(job)["hold"]["code"] == "setup_required"
    assert h._loop.run(h.runtime.clear_failed()) == []
    assert h.jobs_store.get_job_row(job["id"]) is not None
    # Earlier releases stored precisely the same refusal as an error.
    metadata = job["task_metadata"]
    metadata["cad"].pop("hold")
    h.jobs_store.update_job(job["id"], status="error", stage="error", task_metadata_json=json.dumps(metadata))
    old = _row(h, job["id"])
    assert job_cad_state(old)["held"] is True
    assert h._loop.run(h.runtime.clear_failed()) == []
    # A genuine error without a refusal still clears.
    metadata["cad"].pop("refusal")
    h.jobs_store.update_job(job["id"], task_metadata_json=json.dumps(metadata))
    assert job_cad_state(_row(h, job["id"]))["held"] is False
    assert h._loop.run(h.runtime.clear_failed()) == [job["id"]]


def _lineage(h: JobsHarness, job_id: str, lineage: str) -> None:
    row = _row(h, job_id)
    metadata = row["task_metadata"]
    metadata["cad"].setdefault("snapshot", {})["project_lineage_id"] = lineage
    h.jobs_store.update_job(job_id, task_metadata_json=json.dumps(metadata))


def test_newer_request_retires_only_older_holds_of_its_lineage(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    first = h.latest_job()["id"]
    _lineage(h, first, "project-a")
    # Accept another Fusion press without allowing its lane to race the setup.
    h.blocked = "Update pending"
    _second_return(h)
    second = h._loop.run(h.runtime.accept_cad_solve(
        h._intent("cmd-2"), "cad-solve:cmd-2", prepare=False,
        cad_record={"snapshot": {"project_lineage_id": "project-a"}},
    ))
    assert second != first
    retired = _row(h, first)
    assert retired["status"] == "cancelled" and retired["stage"] == "superseded"
    assert job_cad_state(retired)["reason"] == "superseded"
    assert not job_cad_state(retired)["held"]
    assert _cad(retired)["superseded_by"] == second
    with pytest.raises(JobConflictError, match="replaced"):
        h._loop.run(h.runtime.solve_cad_again(first))
    _lineage(h, second, "project-b")
    # An older preparation finishing after the newer press is retired too.
    h.jobs_store.update_job(first, status="preparing", cancellation_requested=False)
    h.jobs_store.refuse_preparing_job(first, code="setup_required", message="Choose settings")
    assert h.jobs_store.retire_held_cad_requests(first) == []  # unrelated lineage
    _lineage(h, second, "project-a")
    assert len(h.jobs_store.retire_held_cad_requests(first)) == 1
    assert _row(h, first)["status"] == "cancelled"


def test_solve_anyway_continues_and_records_explicit_override(h: JobsHarness) -> None:
    _received(h)
    seen = []
    original = h.ingest

    def mesh(*args, **kwargs):
        override = kwargs["prep_options"].get("mesh_limit_override", False)
        seen.append(override)
        _enforce_artifact_triangle_ceiling(28_944, mesh_limit_override=override)
        return original(*args, **kwargs)

    summary = h.prepare(setup_revision_id=_revision(h.store, _setup()), ingest=mesh)
    parent = h.latest_job()
    assert summary["reason"] == "mesh_too_large"
    state = job_cad_state(parent)
    assert state["options"][0] == {"id": "solve_anyway", "label": "Solve anyway"}
    assert "Sources & Mesh…" in state["next_step"] and "model size" in state["next_step"]
    h._ingest_override = mesh
    child = h._loop.run(h.runtime.solve_cad_again(parent["id"], **CadSolveAgainRequest(mesh_limit_override=True).press()))
    _settle(h)
    row = _row(h, child)
    assert row["status"] == "queued" and row["run_number"] == 1
    assert _cad(row)["mesh_limit_override"] == {"accepted": True, "source": "solve_anyway"}
    assert seen == [False, True]
    # The same explicit press recovers the same child after binding.
    assert h._loop.run(h.runtime.solve_cad_again(parent["id"], mesh_limit_override=True)) == child
    with pytest.raises(MeshTooLarge):
        _enforce_artifact_triangle_ceiling(28_944)


def test_schema_six_upgrade_keeps_historical_jobs_and_snapshots_before_rebuild(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    row = h.latest_job()
    db = h.jobs_store.db_path
    h.jobs_store.update_job(row["id"], status="error", stage="error")
    h.jobs_store.close()
    # A real schema-6 CHECK, while retaining all existing rows and child tables.
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql=replace(sql, '''held'',', '') WHERE name='simulation_jobs'")
        conn.execute("PRAGMA user_version=6")
        conn.execute("PRAGMA writable_schema=OFF")
    reopened = JobStore(db)
    reopened.initialize()
    try:
        assert reopened.get_job_row(row["id"])["status"] == "error"
        assert job_cad_state(reopened.get_job_row(row["id"]))["held"] is True
        snapshot = Path(str(db) + ".pre-schema-7.bak")
        assert snapshot.exists()
        with sqlite3.connect(snapshot) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        reopened.update_job(row["id"], status="preparing")
        assert reopened.refuse_preparing_job(row["id"], code="setup_required", message="Settings")
        assert reopened.get_job_row(row["id"])["status"] == "held"
    finally:
        reopened.close()
