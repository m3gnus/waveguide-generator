"""Reopening a run: ``GET /api/runs/{id}/workspace`` (server/jobs/run_workspace.py)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.imported_setup import setup_digest, solve_request_for, validate_setup
from server.jobs.model_solve import _request_revision
from server.jobs.models import SolveRequest
from server.jobs.run_workspace import derived_setup, mount_run_workspace

from cad_backends import JobsHarness
from test_cad_preparation import _setup
from test_jobs_api import _request
from test_mesh_import import register_mesh, registration, request as file_request
from test_model_solve import _cad_model, _models_app, _settle, _solve
from test_models_api import app_for, setup_for


def _job(jobs: Any, job_id: str, config: dict[str, Any], metadata: dict[str, Any] | None = None,
         status: str = "complete") -> None:
    jobs.create_job({
        "id": job_id, "status": status, "created_at": "2026-08-01T10:00:00",
        "updated_at": "2026-08-01T10:00:00", "queued_at": "2026-08-01T10:00:00",
        "config_json": config, "config_summary_json": {}, "task_metadata": metadata or {},
    })


async def _workspace(app: Any, job_id: str, expected: int = 200) -> dict[str, Any]:
    status, raw = await _request(app, "GET", f"/api/runs/{job_id}/workspace")
    assert status == expected, (status, raw)
    return json.loads(raw)


def _file_scenario(tmp_path: Path, scenario) -> None:
    async def run() -> None:
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        mount_run_workspace(app)
        runtime._ensure_scheduler = lambda: None
        try:
            await scenario(store, app, runtime)
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(run())


# -- file runs -------------------------------------------------------------------------


def test_a_file_run_opens_with_its_recorded_revision_mesh_and_original(tmp_path) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        model_id = "mesh-file:" + record["artifact_sha256"]
        setup = setup_for(record)
        status, raw = await _request(app, "POST", f"/api/models/{model_id}/solve", body={
            "prepared_id": record["ingest_id"], "client_request_id": "mesh-1", "setup": setup,
        })
        assert status == 200, raw
        job_id = json.loads(raw)["job_id"]
        recorded = runtime.store.get_job_row(job_id)["task_metadata"]["imported_geometry"]["setup_revision_id"]

        opened = await _workspace(app, job_id)
        assert "refusal" not in opened
        assert opened["model"]["model_id"] == model_id and opened["model"]["producer"] == "mesh-file"
        assert opened["prepared"]["ingest_id"] == record["ingest_id"]
        assert opened["prepared"]["model_id"] == model_id
        assert opened["prepared"]["msh_text"]  # the viewport shows it without another read
        assert opened["original_available"] is True
        assert opened["setup"] == {
            "revision_id": recorded, "content": json.loads(store.get_setup_revision(recorded)["setup_json"]),
            "derived": False, "exact": True,
        }
        assert opened["solve_options"]["engine"] == "metal"

        # An unedited Solve of the reopened run names that revision: the same run in flight.
        status, raw = await _request(app, "POST", f"/api/models/{model_id}/solve", body={
            "prepared_id": record["ingest_id"], "client_request_id": "mesh-2",
            "setup_revision_id": opened["setup"]["revision_id"],
        })
        assert (status, json.loads(raw)["job_id"]) == (200, job_id)

    _file_scenario(tmp_path, scenario)


@pytest.mark.parametrize("metadata", [
    {},  # before imported-geometry metadata
    {"imported_geometry": {"ingest_id": "named-below"}},  # before setup revisions
])
def test_a_file_run_from_before_setup_revisions_derives_one_deterministically(tmp_path, metadata) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        exact = file_request(record).model_dump(mode="json")
        if metadata:
            metadata["imported_geometry"]["ingest_id"] = record["ingest_id"]
        _job(runtime.store, "old-file-run", exact, metadata)
        before = {row["revision_id"] for row in _revisions(store)}

        first = await _workspace(app, "old-file-run")
        again = await _workspace(app, "old-file-run")
        assert "refusal" not in first
        assert first["setup"]["derived"] is True
        assert first["setup"] == again["setup"]
        # Content-addressed: it is the request without the prepared mesh, stored once.
        content, exact_setup = derived_setup(SolveRequest.model_validate(exact).model_dump(mode="json"))
        assert exact_setup is True and first["setup"]["exact"] is True and "note" not in first["setup"]
        expected = validate_setup(content)
        stored = store.get_setup_revision(first["setup"]["revision_id"])
        assert stored["content_sha256"] == setup_digest(expected)
        assert {row["revision_id"] for row in _revisions(store)} - before == {first["setup"]["revision_id"]}
        # Nothing is lost: every geometry and option field of the request is in it.
        content = first["setup"]["content"]
        assert content["geometry"]["drive_channels"][0]["source_ids"] == ["group_2"]
        assert content["options"]["frequencies_hz"] == [100, 200]
        assert not {"type", "ingest_id", "manifest_sha256", "artifact_sha256"} & set(content["geometry"])
        # It is the revision a retry of this run records.
        assert _request_revision(store, SolveRequest.model_validate(exact)) == first["setup"]["revision_id"]
        # The run's job row is not rewritten.
        assert runtime.store.get_job_row("old-file-run")["task_metadata"] == metadata

    _file_scenario(tmp_path, scenario)


def _revisions(store: CadLinkStore) -> list[dict[str, Any]]:
    with store._lock:
        rows = store._connect().execute("SELECT revision_id FROM cad_setup_revisions").fetchall()
    return [dict(row) for row in rows]


def test_a_missing_ingest_unknown_run_and_parametric_run_are_refusals_not_errors(tmp_path) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        exact = file_request(record).model_dump(mode="json")
        exact["geometry"]["ingest_id"] = "wgi_" + "9" * 26
        _job(runtime.store, "gone", exact, {"imported_geometry": {
            "ingest_id": exact["geometry"]["ingest_id"], "model_id": "mesh-file:" + record["artifact_sha256"],
        }})
        gone = await _workspace(app, "gone")
        assert gone["refusal"]["code"] == "prepared_unavailable"
        assert "prepared" not in gone
        # What could be read still is: the model, its original, and the run's settings.
        assert gone["model"]["model_id"] == "mesh-file:" + record["artifact_sha256"]
        assert gone["original_available"] is True
        assert gone["setup"]["derived"] is True
        assert "Prepare the model again" in gone["refusal"]["message"]

        # Neither the ingest nor any record of its model.
        orphan = {**exact, "geometry": {**exact["geometry"], "ingest_id": "wgi_" + "8" * 26}}
        _job(runtime.store, "orphan", orphan)
        refused = await _workspace(app, "orphan")
        assert refused["refusal"]["code"] == "prepared_unavailable"
        assert "model" not in refused and refused["original_available"] is False
        assert "Import the geometry again" in refused["refusal"]["message"]

        body = await _workspace(app, "no-such-run", expected=404)
        assert body["error"]["code"] == "run_not_found"

        _job(runtime.store, "parametric", {"design": {"formula": "osse"}, "options": {}})
        parametric = await _workspace(app, "parametric")
        assert parametric["refusal"]["code"] == "parametric_run"

    _file_scenario(tmp_path, scenario)


def test_a_recorded_revision_that_is_gone_is_rebuilt_but_not_claimed_exact(tmp_path) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        _job(runtime.store, "lost", file_request(record).model_dump(mode="json"), {"imported_geometry": {
            "ingest_id": record["ingest_id"], "setup_revision_id": "wgs_" + "0" * 26,
        }})
        opened = await _workspace(app, "lost")
        assert "refusal" not in opened
        assert opened["setup"]["derived"] is True and opened["setup"]["exact"] is False
        assert "no longer retained" in opened["setup"]["note"]
        assert opened["setup"]["revision_id"] != "wgs_" + "0" * 26

    _file_scenario(tmp_path, scenario)


def test_a_request_that_is_not_a_setup_is_refused_as_unreadable(tmp_path) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        broken = file_request(record).model_dump(mode="json")
        broken["geometry"]["drive_channels"] = "not channels"
        _job(runtime.store, "broken", broken)
        opened = await _workspace(app, "broken")
        assert opened["refusal"]["code"] == "setup_unreadable"
        assert "setup" not in opened

    _file_scenario(tmp_path, scenario)


def test_an_exterior_only_fem_run_keeps_its_exclusion(tmp_path) -> None:
    async def scenario(store, app, runtime):
        original = register_mesh(store, tmp_path, registration())
        row = store.allocate_ingest(
            manifest_sha256=original["manifest_sha256"], artifact_sha256=original["artifact_sha256"],
            record_builder=lambda ingest_id, created_at: json.dumps({
                **original, "ingest_id": ingest_id, "created_at": created_at,
                "evidence": {"fem_regions": [{"region_id": "fem-b"}, {"region_id": "fem-a"}], "fem_air_volumes": []},
            }),
        )
        record = json.loads(row["record_json"])
        request = file_request(record, exterior_only=True).model_dump(mode="json")
        assert "exterior_only_region_ids" not in request["geometry"]
        _job(runtime.store, "fem", request)
        opened = await _workspace(app, "fem")
        geometry = opened["setup"]["content"]["geometry"]
        assert geometry["exterior_only"] is True
        assert geometry["exterior_only_region_ids"] == ["fem-a", "fem-b"]
        assert opened["setup"]["exact"] is True
        # A re-solve of that setup keeps the FEM air out.
        resolved = solve_request_for(
            validate_setup(opened["setup"]["content"]), ingest_id=record["ingest_id"],
            manifest_sha256=record["manifest_sha256"], artifact_sha256=record["artifact_sha256"],
            acknowledged_findings=[], fem_region_ids=["fem-a", "fem-b"],
        )
        assert resolved.geometry.exterior_only is True

        # Without its prepared mesh the exclusion cannot be known: not exact.
        gone = {**request, "geometry": {**request["geometry"], "ingest_id": "wgi_" + "7" * 26}}
        _job(runtime.store, "fem-gone", gone)
        lost = await _workspace(app, "fem-gone")
        assert lost["setup"]["exact"] is False and "FEM exclusion" in lost["setup"]["note"]
        assert lost["refusal"]["code"] == "prepared_unavailable"

    _file_scenario(tmp_path, scenario)


def test_a_file_run_whose_original_is_gone_still_opens_but_cannot_be_prepared(tmp_path) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        _job(runtime.store, "run", file_request(record).model_dump(mode="json"))
        (Path(record["mesh_store_path"]).parent / "original.msh").unlink()
        opened = await _workspace(app, "run")
        assert "refusal" not in opened
        assert opened["original_available"] is False and opened["prepared"]["msh_text"]

    _file_scenario(tmp_path, scenario)


# -- CAD runs --------------------------------------------------------------------------


@pytest.fixture
def harness(tmp_path: Path):
    h = JobsHarness(tmp_path)
    yield h
    h.close()


def _cad_app(harness: JobsHarness):
    app = _models_app(harness)
    mount_run_workspace(app)
    return app


def _open(harness: JobsHarness, app: Any, job_id: str, expected: int = 200) -> dict[str, Any]:
    return harness._loop.run(_workspace(app, job_id, expected))


def test_a_cad_run_opens_with_the_revision_its_solve_recorded(harness) -> None:
    app = _cad_app(harness)
    model = _cad_model(harness)
    status, accepted = _solve(harness, app, model, client_request_id="press-1", setup=_setup(engine="metal"))
    assert status == 200, accepted
    _settle(harness)
    row = harness.jobs_store.get_job_row(accepted["job_id"])
    recorded = row["task_metadata"]["cad"]["setup"]["revision_id"]

    # The lane's own preparation (this harness files it under no model).
    opened = _open(harness, app, accepted["job_id"])
    assert "refusal" not in opened
    assert opened["prepared"]["ingest_id"] == row["config_json"]["geometry"]["ingest_id"]
    assert "msh_text" not in opened["prepared"]  # a CAD mesh is read through CAD Link
    assert opened["setup"]["revision_id"] == recorded and opened["setup"]["derived"] is False
    assert opened["setup"]["content"]["options"]["engine"] == "metal"

    # A bound run of the project's prepared mesh opens under its model.
    ingest = harness.store.get_ingest(model["prepared_id"])
    config = {**row["config_json"], "geometry": {
        **row["config_json"]["geometry"], "ingest_id": model["prepared_id"],
        "manifest_sha256": ingest["manifest_sha256"], "artifact_sha256": ingest["artifact_sha256"],
    }}
    _job(harness.jobs_store, "bound", config, {"cad": row["task_metadata"]["cad"]})
    bound = _open(harness, app, "bound")
    assert "refusal" not in bound
    assert bound["model"]["model_id"] == model["model_id"] and bound["model"]["producer"] == "cad"
    assert bound["prepared"]["model_id"] == model["model_id"]
    assert bound["original_available"] is False  # this harness keeps no bundle
    assert bound["setup"]["revision_id"] == recorded


def test_a_cad_run_from_before_setup_revisions_and_an_unfiled_one_open(harness) -> None:
    app = _cad_app(harness)
    model = _cad_model(harness)
    ingest = harness.store.get_ingest(model["prepared_id"])
    exact = {
        "geometry": {
            "type": "imported", "ingest_id": model["prepared_id"],
            "manifest_sha256": ingest["manifest_sha256"], "artifact_sha256": ingest["artifact_sha256"],
            **_setup()["geometry"],
        },
        "options": {"engine": "metal", "frequencies_hz": [500.0, 1000.0]},
    }
    _job(harness.jobs_store, "old-cad", exact, {"imported_geometry": {"anchor_lineage_id": model["model_id"]}})
    first = _open(harness, app, "old-cad")
    assert "refusal" not in first
    assert first["model"]["model_id"] == model["model_id"]
    assert first["setup"]["derived"] is True
    assert _open(harness, app, "old-cad")["setup"]["revision_id"] == first["setup"]["revision_id"]
    assert first["setup"]["content"]["geometry"]["mesh"]["rigid_size_mm"] == 20.0

    # A return of a design this workspace does not know: filed under no model.
    row = harness.store.allocate_ingest(
        manifest_sha256=ingest["manifest_sha256"], artifact_sha256=ingest["artifact_sha256"],
        record_builder=lambda ingest_id, created_at: json.dumps({
            **{k: v for k, v in json.loads(ingest["record_json"]).items() if k not in ("project", "anchor")},
            "ingest_id": ingest_id, "created_at": created_at,
        }),
    )
    assert row["model_id"] == ""
    unfiled = {**exact, "geometry": {**exact["geometry"], "ingest_id": row["ingest_id"]}}
    _job(harness.jobs_store, "unfiled-cad", unfiled)
    opened = _open(harness, app, "unfiled-cad")
    assert "refusal" not in opened and "model" not in opened
    assert opened["prepared"]["model_id"] == "unfiled"


def test_a_cad_solve_that_never_had_a_request(harness) -> None:
    app = _cad_app(harness)
    model = _cad_model(harness)
    intent = {"type": "cad_intent", "operation_id": "op", "bundle_path": model["bundle_path"],
              "manifest_sha256": "sha256:x", "return_id": "r"}
    _job(harness.jobs_store, "never-bound", intent, {"cad": {}}, status="error")
    refused = _open(harness, app, "never-bound")
    assert refused["refusal"]["code"] == "no_request"

    # One that held a setup but whose preparation is gone: its settings, and why it cannot solve.
    from test_cad_preparation import _revision

    revision = _revision(harness.store, _setup(engine="metal"))
    _job(harness.jobs_store, "held", {**intent, "setup_revision_id": revision}, {"cad": {}}, status="error")
    held = _open(harness, app, "held")
    assert held["setup"]["revision_id"] == revision
    assert held["refusal"]["code"] == "prepared_unavailable"
