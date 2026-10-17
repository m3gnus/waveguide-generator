"""Prepared-mesh collection: what keeps a mesh, what goes, and in which order."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from server.cadlink.operations import TERMINAL_STATES
from server.cadlink.store import CadLinkStore
from server.jobs.model_solve import ModelSolveRequest, _bind_press, _bound_press
from server.jobs.store import JobStore
from server.mesh.prepared_gc import (
    PreparedMeshCollector,
    collect_prepared_meshes,
    prepared_mesh_use,
)
from test_mesh_import import register_mesh, registration

NOW = datetime.now(timezone.utc).replace(microsecond=0)
OLD = NOW - timedelta(hours=48)


def stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


@pytest.fixture
def stores(tmp_path):
    cad = CadLinkStore.for_data_dir(tmp_path)
    cad.initialize()
    jobs = JobStore.for_data_dir(tmp_path)
    jobs.initialize()
    return cad, jobs


def collect(stores, tmp_path, **kwargs):
    cad, jobs = stores
    return collect_prepared_meshes(
        cad, jobs, tmp_path, terminal_states=TERMINAL_STATES, now=NOW, **kwargs
    )


def sql(cad, statement, parameters=()):
    with cad._lock, cad._transaction() as conn:
        conn.execute(statement, parameters)


def age(cad, *ingest_ids, moment=OLD):
    for ingest_id in ingest_ids:
        sql(cad, "UPDATE ingests SET created_at = ?, last_used_at = NULL WHERE ingest_id = ?",
            (stamp(moment), ingest_id))


def age_tree(path: Path, moment=OLD):
    for root, directories, files in os.walk(path, topdown=False):
        for name in files + directories:
            os.utime(Path(root) / name, (moment.timestamp(), moment.timestamp()), follow_symlinks=False)
    os.utime(path, (moment.timestamp(), moment.timestamp()), follow_symlinks=False)


def file_preparations(cad, tmp_path):
    """Two preparations of one file: the older one is no model's latest."""

    old = register_mesh(cad, tmp_path, registration(units="mm"))
    new = register_mesh(cad, tmp_path, registration(units="cm"))
    age(cad, old["ingest_id"], new["ingest_id"])
    return old, new


def folder(record) -> Path:
    return Path(record["mesh_store_path"]).parent


def ingest_ids(cad):
    return {json.loads(text)["ingest_id"] for text in cad.ingest_records()}


def add_job(jobs, job_id, *, status="complete", config=None, metadata=None):
    jobs.create_job({
        "id": job_id, "status": status, "created_at": stamp(NOW), "updated_at": stamp(NOW),
        "queued_at": stamp(NOW), "config_json": config or {}, "config_summary_json": {},
        "task_metadata": metadata or {},
    })


def delete_job(jobs, job_id):
    assert jobs.delete_job_with_event(job_id)[0]


HEX = "ab" * 32


def cad_preparation(cad, tmp_path, *, key="c" * 64, hex_digest=HEX, mesh=None):
    """A CAD ingest whose solve mesh, sidecar, display mesh and snapshot are on disk."""

    imports = tmp_path / "imports"
    mesh_path = Path(mesh) if mesh is not None else imports / "meshes" / f"{key}.msh"
    if mesh is None:
        mesh_path.parent.mkdir(parents=True, exist_ok=True)
        mesh_path.write_text("$MeshFormat\n")
        mesh_path.with_suffix(".json").write_text("{}")
    viewport = imports / "viewports" / f"{key[::-1]}.msh"
    viewport.parent.mkdir(parents=True, exist_ok=True)
    viewport.write_text("view")
    viewport.with_suffix(".json").write_text("{}")
    bundle = imports / "bundles" / f"{hex_digest}.wgreturn"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "wgreturn.json").write_text("{}")

    def build(ingest_id, created_at):
        return json.dumps({
            "ingest_id": ingest_id, "created_at": created_at,
            "manifest_sha256": f"sha256:{hex_digest}", "artifact_sha256": "sha256:" + "d" * 64,
            "bundle_store_path": str(bundle), "mesh_store_path": str(mesh_path),
            "viewport_mesh": {"available": True, "store_path": str(viewport)},
        })

    row = cad.allocate_ingest(
        manifest_sha256=f"sha256:{hex_digest}", artifact_sha256="sha256:" + "d" * 64,
        record_builder=build,
    )
    age(cad, row["ingest_id"])
    return json.loads(row["record_json"])


# -- what is deleted --------------------------------------------------------------


def test_an_unreferenced_mesh_past_its_grace_period_goes_with_its_files(stores, tmp_path):
    cad, _ = stores
    old, new = file_preparations(cad, tmp_path)
    assert (folder(old) / "original.msh").is_file()

    report = collect(stores, tmp_path)

    assert report.removed_ingests == [old["ingest_id"]]
    assert cad.get_ingest(old["ingest_id"]) is None
    assert not folder(old).exists()
    assert report.removed_bytes > 0
    # The model's latest preparation stays, files and all.
    assert cad.get_ingest(new["ingest_id"]) is not None
    assert (folder(new) / "original.msh").is_file() and (folder(new) / "solve.msh").is_file()


# -- each keep reason keeps its mesh ------------------------------------------------


def _keep_by_job(status, where):
    def arrange(cad, jobs, old, new):
        geometry = {"type": "imported", "ingest_id": old["ingest_id"]}
        if where == "config":
            add_job(jobs, "j1", status=status, config={"geometry": geometry})
        elif where == "imported_geometry":
            add_job(jobs, "j1", status=status,
                    metadata={"imported_geometry": {"ingest_id": old["ingest_id"]}, "archived_at": "x"})
        else:  # a CAD intent naming its prepared mesh
            add_job(jobs, "j1", status=status,
                    config={"type": "cad_intent", "bundle_path": f"ingest/{old['ingest_id']}"})
    return arrange


def _keep_latest(cad, jobs, old, new):
    # Delete the newer one's row: the older becomes the model's only, and latest.
    sql(cad, "UPDATE imported_models SET latest_ingest_id = ?", (old["ingest_id"],))


def _keep_unfinished_operation(cad, jobs, old, new):
    sql(cad, "INSERT INTO cad_operations (operation_id, kind, state, legacy, created_at, updated_at) "
             "VALUES ('op-1', 'solve', 'needs_user_input', 1, ?, ?)", (stamp(OLD), stamp(OLD)))
    sql(cad, "INSERT INTO cad_preparations (preparation_id, operation_id, attempt_generation, "
             "snapshot_sha256, ingest_id, blocking_findings_json, created_at) "
             "VALUES (?, 'op-1', 1, 'sha256:x', ?, '[]', ?)",
        (old["ingest_id"], old["ingest_id"], stamp(OLD)))


def _keep_setup_revision(cad, jobs, old, new):
    sql(cad, "INSERT INTO cad_setup_revisions (revision_id, content_sha256, setup_json, created_at) "
             "VALUES ('rev-1', 'sha256:rev', ?, ?)",
        (json.dumps({"pinned_prepared_id": old["ingest_id"]}), stamp(OLD)))


def _keep_press_binding(cad, jobs, old, new):
    # The press's job is a run of the newer mesh; the press itself names the older.
    add_job(jobs, "j1", config={"geometry": {"ingest_id": new["ingest_id"]}})
    cad.set_setting("model-solve-press:press-1", {
        "job_id": "j1", "press_sha256": "sha256:p", "model_id": "m", "prepared_id": old["ingest_id"],
    })


def _keep_grace(cad, jobs, old, new):
    age(cad, old["ingest_id"], moment=NOW - timedelta(hours=23))


def _keep_last_use(cad, jobs, old, new):
    cad.touch_ingest(old["ingest_id"])


@pytest.mark.parametrize(
    "arrange",
    [
        _keep_by_job("complete", "config"),
        _keep_by_job("error", "config"),
        _keep_by_job("cancelled", "config"),
        _keep_by_job("queued", "config"),
        _keep_by_job("complete", "imported_geometry"),
        _keep_by_job("preparing", "intent"),
        _keep_latest,
        _keep_unfinished_operation,
        _keep_setup_revision,
        _keep_press_binding,
        _keep_grace,
        _keep_last_use,
    ],
    ids=[
        "complete-job", "error-job", "cancelled-job", "queued-job", "archived-imported-geometry",
        "cad-intent-bundle-path", "latest", "unfinished-operation", "setup-revision",
        "press-binding", "grace", "last-use",
    ],
)
def test_each_keep_reason_keeps_its_mesh(stores, tmp_path, arrange):
    cad, jobs = stores
    old, new = file_preparations(cad, tmp_path)
    arrange(cad, jobs, old, new)

    report = collect(stores, tmp_path)

    assert old["ingest_id"] not in report.removed_ingests
    assert cad.get_ingest(old["ingest_id"]) is not None
    assert (folder(old) / "solve.msh").is_file() and (folder(old) / "original.msh").is_file()


def test_a_finished_operation_keeps_nothing(stores, tmp_path):
    cad, _ = stores
    old, _ = file_preparations(cad, tmp_path)
    _keep_unfinished_operation(cad, None, old, None)
    sql(cad, "UPDATE cad_operations SET state = 'accepted'")

    assert collect(stores, tmp_path).removed_ingests == [old["ingest_id"]]


def test_a_replay_in_progress_holds_collection_off(stores, tmp_path):
    cad, _ = stores
    old, _ = file_preparations(cad, tmp_path)
    with prepared_mesh_use():
        report = collect(stores, tmp_path)
    assert report.skipped == "busy" and report.removed_ingests == []
    assert folder(old).is_dir()
    assert collect(stores, tmp_path).removed_ingests == [old["ingest_id"]]


def test_a_dedupe_marks_the_preparation_it_answers_with_used(stores, tmp_path):
    cad, _ = stores
    old, _ = file_preparations(cad, tmp_path)
    again = register_mesh(cad, tmp_path, registration(units="mm"))
    assert again["ingest_id"] == old["ingest_id"]

    assert old["ingest_id"] not in collect(stores, tmp_path).removed_ingests


# -- CAD meshes shared through the cache key -------------------------------------


def test_a_shared_mesh_path_survives_while_any_ingest_names_it(stores, tmp_path):
    cad, jobs = stores
    first = cad_preparation(cad, tmp_path)
    second = cad_preparation(cad, tmp_path)
    mesh = Path(first["mesh_store_path"])
    assert mesh == Path(second["mesh_store_path"])
    add_job(jobs, "j1", config={"geometry": {"ingest_id": second["ingest_id"]}})

    report = collect(stores, tmp_path)

    assert report.removed_ingests == [first["ingest_id"]]
    assert mesh.is_file() and mesh.with_suffix(".json").is_file()
    assert Path(first["bundle_store_path"]).is_dir()

    delete_job(jobs, "j1")
    report = collect(stores, tmp_path)

    assert report.removed_ingests == [second["ingest_id"]]
    assert not mesh.exists() and not mesh.with_suffix(".json").exists()
    assert not Path(second["viewport_mesh"]["store_path"]).exists()
    # Nothing anywhere names the snapshot any more.
    assert not Path(second["bundle_store_path"]).exists()


def test_a_snapshot_an_operation_names_is_kept(stores, tmp_path):
    cad, _ = stores
    record = cad_preparation(cad, tmp_path)
    sql(cad, "INSERT INTO cad_operations (operation_id, kind, state, legacy, outcome_json, "
             "created_at, updated_at) VALUES ('op-9', 'receive_snapshot', 'accepted', 1, ?, ?, ?)",
        (json.dumps({"manifest_sha256": f"sha256:{HEX}"}), stamp(OLD), stamp(OLD)))

    assert collect(stores, tmp_path).removed_ingests == [record["ingest_id"]]
    assert not Path(record["mesh_store_path"]).exists()
    assert Path(record["bundle_store_path"]).is_dir()


# -- crash leftovers ---------------------------------------------------------------


def test_orphan_folders_older_than_the_grace_period_are_swept(stores, tmp_path):
    cad, _ = stores
    files = tmp_path / "imports" / "files"
    stale, fresh, staging = files / "wgi_STALEORPHAN", files / "wgi_FRESHORPHAN", files / ".mesh-abc"
    for path in (stale, fresh, staging):
        path.mkdir(parents=True)
        (path / "solve.msh").write_text("mesh")
    age_tree(stale)
    age_tree(staging)
    bundles = tmp_path / "imports" / "bundles"
    orphan_bundle, named_bundle = bundles / f"{'e' * 64}.wgreturn", bundles / f"{'f' * 64}.wgreturn"
    for path in (orphan_bundle, named_bundle):
        path.mkdir(parents=True)
        (path / "wgreturn.json").write_text("{}")
        age_tree(path)
    cad.set_setting("unrelated", {"manifest_sha256": "sha256:" + "f" * 64})

    report = collect(stores, tmp_path)

    assert not stale.exists() and not staging.exists() and not orphan_bundle.exists()
    assert fresh.is_dir() and named_bundle.is_dir()
    assert str(stale) in report.removed_paths


def test_a_crash_between_the_row_and_its_files_leaves_only_an_orphan_folder(stores, tmp_path):
    cad, _ = stores
    old, new = file_preparations(cad, tmp_path)

    def crash(row, _pass):
        raise OSError("power cut")

    report = collect(stores, tmp_path, remove_files=crash)

    # The row went first; its files are an orphan, never a row naming nothing.
    assert report.removed_ingests == [old["ingest_id"]]
    assert cad.get_ingest(old["ingest_id"]) is None
    assert folder(old).is_dir()
    for text in cad.ingest_records():
        assert Path(json.loads(text)["mesh_store_path"]).is_file()

    age_tree(folder(old))
    report = collect(stores, tmp_path)
    assert not folder(old).exists()
    assert folder(new).is_dir()


# -- never through a link, never outside imports ----------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_collection_never_follows_a_symlink(stores, tmp_path):
    cad, _ = stores
    old, new = file_preparations(cad, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    # The prepared mesh's own folder replaced by a link out of the data dir.
    target = folder(old)
    for child in target.iterdir():
        child.unlink()
    target.rmdir()
    target.symlink_to(outside, target_is_directory=True)
    # An orphan link, and a link inside an orphan folder.
    (tmp_path / "imports" / "files" / "wgi_LINKORPHAN").symlink_to(outside, target_is_directory=True)
    holder = tmp_path / "imports" / "files" / "wgi_HOLDER"
    holder.mkdir()
    (holder / "inner").symlink_to(outside, target_is_directory=True)
    age_tree(holder)

    report = collect(stores, tmp_path)

    assert (outside / "keep.txt").read_text() == "precious"
    assert target.is_symlink() and holder.is_dir()
    assert report.refused


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_an_imports_root_that_is_a_link_stops_the_pass(stores, tmp_path):
    cad, _ = stores
    old, _ = file_preparations(cad, tmp_path)
    files = tmp_path / "imports" / "files"
    moved = tmp_path / "moved-files"
    files.rename(moved)
    files.symlink_to(moved, target_is_directory=True)

    report = collect(stores, tmp_path)

    assert report.skipped == "imports_root_is_a_link"
    assert cad.get_ingest(old["ingest_id"]) is not None
    assert (moved / old["ingest_id"]).is_dir()


def test_a_mesh_path_outside_the_imports_roots_is_never_deleted(stores, tmp_path):
    cad, _ = stores
    elsewhere = tmp_path / "elsewhere.msh"
    elsewhere.write_text("not ours")
    record = cad_preparation(cad, tmp_path, mesh=str(elsewhere))

    report = collect(stores, tmp_path)

    assert report.removed_ingests == [record["ingest_id"]]
    assert elsewhere.read_text() == "not ours"
    assert any("outside" in reason for reason in report.refused)


# -- restart and concurrency --------------------------------------------------------


def test_nothing_is_collected_while_an_update_restart_is_pending(stores, tmp_path):
    cad, _ = stores
    old, _ = file_preparations(cad, tmp_path)

    report = collect(stores, tmp_path, restart_pending=lambda: True)

    assert report.skipped == "restart_pending"
    assert cad.get_ingest(old["ingest_id"]) is not None and folder(old).is_dir()


def test_an_ingest_waits_for_a_running_collection(stores, tmp_path):
    cad, _ = stores
    old, _ = file_preparations(cad, tmp_path)
    entered: list[bool] = []
    waiting: list[threading.Thread] = []

    def ingest() -> None:
        with prepared_mesh_use():
            entered.append(folder(old).exists())

    def remove_files(row, active_pass):
        # Mid-pass: an ingest starting now waits until the pass is over.
        worker = threading.Thread(target=ingest)
        worker.start()
        worker.join(0.2)
        assert entered == []
        waiting.append(worker)
        active_pass.remove_files(row)

    collect(stores, tmp_path, remove_files=remove_files)
    waiting[0].join(5)
    # It ran only after the pass had finished with the files.
    assert entered == [False]


# -- press bindings ---------------------------------------------------------------


def _press(cad, key, job_id, written):
    cad.set_setting(f"model-solve-press:{key}", {"job_id": job_id, "press_sha256": "sha256:p",
                                                 "model_id": "m"})
    sql(cad, "UPDATE cad_settings SET updated_at = ? WHERE key = ?",
        (stamp(written), f"model-solve-press:{key}"))


def test_press_bindings_are_pruned_when_their_job_is_gone_or_past_the_bound(stores, tmp_path):
    cad, jobs = stores
    add_job(jobs, "j-live")
    _press(cad, "recent", "j-live", NOW - timedelta(days=1))
    _press(cad, "orphan", "j-gone", NOW - timedelta(days=1))
    _press(cad, "ancient", "j-live", NOW - timedelta(days=31))
    cad.set_setting("solver-selection", {"engine": "metal"})

    report = collect(stores, tmp_path)

    assert report.pruned_press_bindings == 2
    assert cad.get_setting("model-solve-press:recent") is not None
    assert cad.get_setting("model-solve-press:orphan") is None
    assert cad.get_setting("model-solve-press:ancient") is None
    assert cad.get_setting("solver-selection") == {"engine": "metal"}


def test_a_recent_press_replay_still_finds_its_job_after_collection(stores, tmp_path):
    cad, jobs = stores
    old, new = file_preparations(cad, tmp_path)
    add_job(jobs, "j-press", config={"geometry": {"ingest_id": new["ingest_id"]}})
    body = ModelSolveRequest.model_validate({
        "prepared_id": new["ingest_id"], "setup_revision_id": "rev-1", "client_request_id": "press-7",
    })
    model_id = "mesh-file:" + new["artifact_sha256"]
    _bind_press(cad, model_id, body, "j-press")

    collect(stores, tmp_path)

    runtime = SimpleNamespace(store=jobs)
    assert _bound_press(runtime, cad, model_id, body) == "j-press"
    assert cad.get_setting("model-solve-press:press-7")["prepared_id"] == new["ingest_id"]


# -- when it runs -------------------------------------------------------------------


def test_the_collector_runs_off_the_request_path_and_coalesces(stores, tmp_path):
    cad, jobs = stores
    old, _ = file_preparations(cad, tmp_path)
    collector = PreparedMeshCollector(
        cad, jobs, tmp_path, terminal_states=TERMINAL_STATES, restart_pending=lambda: False,
        startup_delay=0,
    )

    async def scenario():
        collector.request()
        collector.request()  # coalesced into one more pass
        await collector._task

    asyncio.run(scenario())
    assert cad.get_ingest(old["ingest_id"]) is None and not folder(old).exists()


def test_deleting_a_run_schedules_collection(tmp_path):
    from fastapi import FastAPI

    from server.jobs.api import create_jobs_router
    from server.jobs.runtime import JobRuntime
    from test_jobs_api import _request

    runtime = JobRuntime(JobStore.for_data_dir(tmp_path))
    runtime.store.initialize()
    add_job(runtime.store, "j-done")
    requested: list[int] = []
    app = FastAPI()
    app.include_router(create_jobs_router(runtime, after_delete=lambda: requested.append(1)))

    async def scenario():
        try:
            status, _ = await _request(app, "DELETE", "/api/jobs/j-done")
            assert status == 200
            status, _ = await _request(app, "DELETE", "/api/jobs/j-done")
            assert status == 404
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())
    assert requested == [1]
