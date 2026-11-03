"""A CAD run exports only the immutable STEP admitted by its solve."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import shutil
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from server.cadlink.intake.ingest import retained_snapshot_path
from server.exports.api import router
from server.tests._asgi import _post
from server.jobs.operation_progress import mount_operation_progress
from server.exports.returned_step import MISMATCH, UNAVAILABLE, ReturnedStepUnavailable, read_returned_step
from server.workspace.api import WorkspaceState


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


@pytest.fixture
def source(tmp_path):
    content = b"ISO-10303-21;\r\n/* original Fusion bytes \xff */\r\nEND-ISO-10303-21;\r\n"
    manifest = {"assembly": {"file": "assembly.step"}, "files": {"assembly.step": {
        "sha256": digest(content), "size_bytes": len(content),
    }}}
    raw = json.dumps(manifest).encode()
    manifest_hash = digest(raw)
    bundle = retained_snapshot_path(tmp_path, manifest_hash)
    bundle.mkdir(parents=True)
    (bundle / "wgreturn.json").write_bytes(raw)
    (bundle / "assembly.step").write_bytes(content)
    geometry = {"type": "imported", "ingest_id": "wgi_original",
                "manifest_sha256": manifest_hash, "artifact_sha256": digest(content)}
    record = {**geometry, "producer": "cad", "project": {"lineage_id": "original"},
              "solve_model_sha256": "sha256:prepared", "transformed_geometry_hash": "sha256:geometry"}
    run = tmp_path / "runs" / "horn" / "1_run"
    run.mkdir(parents=True)
    row = {"id": "job-step", "status": "complete", "config_json": {"geometry": geometry},
           "task_metadata": {"imported_geometry": {"ingest_id": "wgi_original", "anchor_lineage_id": "original",
                                                  "solve_model_sha256": "sha256:prepared"},
                             "archive": {"folder": "horn", "run": "1_run"}, "archived_path": str(run)}}
    records = {"wgi_original": record}
    store = SimpleNamespace(get_ingest=lambda key: {"record_json": json.dumps(records[key])} if key in records else None)
    return SimpleNamespace(content=content, bundle=bundle, run=run, row=row, store=store, records=records, data=tmp_path)


def read(source, **kwargs):
    return read_returned_step(source.row, source.store, source.data, source.data / "runs", **kwargs)


@pytest.fixture
def step_app(source):
    app = FastAPI()
    app.include_router(router)
    mount_operation_progress(app)
    app.state.data_dir = source.data
    app.state.cadlink_store = source.store
    app.state.jobs_runtime = SimpleNamespace(store=SimpleNamespace(get_job_row=lambda key: copy.deepcopy(source.row) if key == "job-step" else None))
    app.state.workspace = WorkspaceState(source.data, default_path=source.data / "runs", allow_adoption=False)
    return app


def test_returns_exact_bytes_not_the_current_document(source):
    (source.run.parent / "newer.step").write_bytes(b"different model")
    assert read(source) == source.content
    assert read(source, check_only=True) == b""


@pytest.mark.parametrize("pruned", ["step", "bundle", "bundle-and-record"])
def test_run_copy_survives_pruned_return(source, pruned):
    nested = source.run / "archive"
    nested.mkdir()
    (nested / "original.STP").write_bytes(source.content)
    if pruned == "step":
        (source.bundle / "assembly.step").unlink()
    else:
        shutil.rmtree(source.bundle)
    if pruned == "bundle-and-record":
        source.records.clear()
    assert read(source) == source.content


def test_unrecorded_run_location_uses_existing_archive_identity(source):
    source.row["task_metadata"].pop("archived_path")
    shutil.rmtree(source.bundle)
    (source.run / "saved.step").write_bytes(source.content)
    assert read(source) == source.content


def test_recorded_run_location_wins_over_current_runs_root(source):
    shutil.rmtree(source.bundle)
    (source.run / "saved.step").write_bytes(source.content)
    assert read_returned_step(source.row, source.store, source.data, source.data / "other-runs") == source.content


@pytest.mark.parametrize("damage", ["same-size-step", "manifest", "artifact-identity", "prepared-identity", "lineage", "ingest-identity"])
def test_refuses_changed_bytes_or_run_identity(source, damage):
    if damage == "same-size-step":
        (source.bundle / "assembly.step").write_bytes(b"x" * len(source.content))
    elif damage == "manifest":
        (source.bundle / "wgreturn.json").write_bytes(b"{}")
    elif damage == "artifact-identity":
        source.records["wgi_original"]["artifact_sha256"] = "sha256:" + "0" * 64
    elif damage == "prepared-identity":
        source.records["wgi_original"]["solve_model_sha256"] = "sha256:other"
    elif damage == "lineage":
        source.records["wgi_original"]["project"]["lineage_id"] = "other"
    else:
        source.row["task_metadata"]["imported_geometry"]["ingest_id"] = "wgi_other"
    with pytest.raises(ReturnedStepUnavailable, match="does not match"):
        read(source)


def test_mismatched_bundle_member_can_fall_back_to_verified_run_copy(source):
    (source.bundle / "assembly.step").write_bytes(b"x" * len(source.content))
    (source.run / "retained.step").write_bytes(source.content)
    assert read(source) == source.content


def test_pruned_and_mismatched_run_copy_is_refused(source):
    shutil.rmtree(source.bundle)
    (source.run / "wrong.step").write_bytes(b"x" * len(source.content))
    with pytest.raises(ReturnedStepUnavailable, match="does not match"):
        read(source)


def test_no_matching_run_copy_explains_how_to_recover(source):
    shutil.rmtree(source.bundle)
    (source.run.parent / "another-run").mkdir()
    (source.run.parent / "another-run" / "same.step").write_bytes(source.content)
    with pytest.raises(ReturnedStepUnavailable) as exc:
        read(source)
    assert str(exc.value) == UNAVAILABLE


@pytest.mark.parametrize("target", ["step", "bundle", "run-folder", "run-step"])
def test_does_not_follow_links(source, target):
    if target == "step":
        original = source.bundle / "assembly.step"
        saved = source.data / "linked.step"
        original.rename(saved)
        original.symlink_to(saved)
    elif target == "bundle":
        saved = source.bundle.with_suffix(".moved")
        source.bundle.rename(saved)
        source.bundle.symlink_to(saved, target_is_directory=True)
    else:
        shutil.rmtree(source.bundle)
        saved = source.data / "saved.step"
        saved.write_bytes(source.content)
        if target == "run-step":
            (source.run / "linked.step").symlink_to(saved)
        else:
            moved = source.run.with_name("moved")
            source.run.rename(moved)
            (moved / "saved.step").write_bytes(source.content)
            source.run.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ReturnedStepUnavailable):
        read(source)


@pytest.mark.parametrize("producer", ["mesh-file", "design-assembly", "native"])
def test_non_return_geometry_is_not_reinterpreted_as_a_cad_return(source, producer):
    source.records["wgi_original"]["producer"] = producer
    with pytest.raises(ReturnedStepUnavailable, match="no returned CAD STEP"):
        read(source)


@pytest.mark.parametrize("origin", ["mesh-file", "design-assembly"])
def test_pruned_non_cad_record_cannot_enable_returned_step(source, origin):
    source.records.clear()
    source.row["config_summary_json"] = {"import_origin": origin}
    with pytest.raises(ReturnedStepUnavailable, match="no returned CAD STEP"):
        read(source)


def test_cancellation_stops_before_reading_or_publishing(source):
    from server.jobs.request_cancellation import ClientDisconnected

    def cancelled():
        raise ClientDisconnected()
    with pytest.raises(ClientDisconnected):
        read(source, cancel_cb=cancelled)


def test_export_uses_the_bytes_it_verified_if_the_path_is_replaced(source, monkeypatch):
    from server.cadlink import retained_files

    real_read = retained_files.os.fdopen
    def replacing(descriptor, *args, **kwargs):
        opened = real_read(descriptor, *args, **kwargs)
        original = source.bundle / "assembly.step"
        original.unlink()
        original.write_bytes(b"replacement")
        return opened
    monkeypatch.setattr(retained_files.os, "fdopen", replacing)
    assert read(source) == source.content


@pytest.mark.parametrize("pruned", ["step", "bundle", "bundle-and-record"])
def test_endpoints_use_default_runs_folder_for_unrecorded_location(source, step_app, pruned):
    source.row["task_metadata"].pop("archived_path")
    (source.run / "saved.step").write_bytes(source.content)
    if pruned == "step":
        (source.bundle / "assembly.step").unlink()
    else:
        shutil.rmtree(source.bundle)
    if pruned == "bundle-and-record":
        source.records.clear()
    assert step_app.state.workspace.selected_path() is None

    async def scenario():
        status, body = await _post(step_app, "/api/export/runs/job-step/step/availability", None)
        assert status == 200
        assert json.loads(body) == {"available": True, "reason": None}
        status, body = await _post(step_app, "/api/export/runs/job-step/step", None)
        assert status == 200
        assert body == source.content
    asyncio.run(scenario())


@pytest.mark.parametrize("location", ["bundle", "recorded-run", "unrecorded-run"])
def test_endpoints_resolve_runs_root_only_for_unrecorded_fallback(source, step_app, location):
    disconnected = source.data / "disconnected-runs"
    disconnected.mkdir()
    step_app.state.workspace.select(disconnected)
    disconnected.rmdir()
    if location != "recorded-run":
        source.row["task_metadata"].pop("archived_path")
    if location != "bundle":
        shutil.rmtree(source.bundle)
        (source.run / "saved.step").write_bytes(source.content)

    async def scenario():
        status, body = await _post(step_app, "/api/export/runs/job-step/step/availability", None)
        assert status == 200
        availability = json.loads(body)
        status, body = await _post(step_app, "/api/export/runs/job-step/step", None)
        if location == "unrecorded-run":
            assert availability["available"] is False
            assert "Reconnect its Runs folder" in availability["reason"]
            assert status == 422
            assert json.loads(body)["detail"] == availability["reason"]
        else:
            assert availability == {"available": True, "reason": None}
            assert status == 200
            assert body == source.content
    asyncio.run(scenario())


def test_endpoint_checks_and_exports_without_building_geometry(source, step_app, monkeypatch):
    from server.exports import api

    def forbidden(*_args, **_kwargs):
        pytest.fail("returned STEP must never build geometry")
    monkeypatch.setattr(api, "build_step_solid", forbidden)
    monkeypatch.setattr(api, "build_step", forbidden)
    app = step_app

    async def scenario():
        status, body = await _post(app, "/api/export/runs/job-step/step/availability", None)
        assert status == 200
        assert json.loads(body) == {"available": True, "reason": None}
        operation_id = "25c273a4-850a-4d7c-bd6e-c36138125cad"
        status, body = await _post(app, "/api/export/runs/job-step/step", None,
                                   headers=((b"x-wg-operation-id", operation_id.encode()),))
        assert status == 200
        assert body == source.content
        status, body = await _post(app, f"/api/operation-progress/{operation_id}", None, method="GET")
        assert json.loads(body) == {"stages": ["locate", "verify", "write"], "total": 3}
        assert (await _post(app, "/api/export/runs/missing/step", None))[0] == 404
        (source.bundle / "assembly.step").write_bytes(b"x" * len(source.content))
        status, body = await _post(app, "/api/export/runs/job-step/step/availability", None)
        assert json.loads(body) == {"available": False, "reason": MISMATCH}
        status, body = await _post(app, "/api/export/runs/job-step/step", None)
        assert status == 422
        assert json.loads(body)["detail"] == MISMATCH
        shutil.rmtree(source.bundle)
        status, body = await _post(app, "/api/export/runs/job-step/step/availability", None)
        assert json.loads(body) == {"available": False, "reason": UNAVAILABLE}
    asyncio.run(scenario())
