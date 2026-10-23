"""Synthetic archive trees: no user folders or data are used."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from server.cadlink.store import CadLinkStore, STORE_FORMAT_VERSION
from server.workspace import archive_identity as identity
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.tests.test_textcfg import SOURCE
from server.cli.request import build_request
from server.design.textcfg import parse

UID = "wgu_01K00000000000000000000000"
OTHER = "wgu_01K00000000000000000000001"


def registry(tmp_path):
    store = CadLinkStore(tmp_path / "cadlink.db")
    root = tmp_path / "runs"
    root.mkdir()
    store.archive_root = lambda: root
    store.initialize()
    return store, root


def row(folder="Horn", run="1_Horn", key=f"design:{UID}", **config):
    return {
        "id": "run",
        "run_number": 1,
        "label": "Horn",
        "config_json": {"design_uid": UID, "design_name": "Horn", **config},
        "task_metadata": {"archive": {"key": key, "folder": folder, "run": run}},
    }


def test_keys_and_collisions_use_identity_not_slug(tmp_path):
    store, root = registry(tmp_path)
    assert identity.owner_key(lineage_id="wgl_a", design_uid=UID) == "lineage:wgl_a"
    assert identity.owner_key(design_uid=UID) == f"design:{UID}"
    assert identity.owner_key(model_id="mesh-file:sha256:abc") == "model:mesh-file:sha256:abc"
    assert identity.owner_key(name="Foo bar") != identity.owner_key(name="Foo_bar")
    for key, name, expected in [
        ("name:Foo bar", "Foo bar", "Foo_bar"),
        ("name:Foo_bar", "Foo_bar", "Foo_bar-2"),
        ("lineage:wgl_a", "Foo bar", "Foo_bar-3"),
        (f"design:{UID}", "foo_bar", "foo_bar-4"),
    ]:
        assert identity.claim_project_folder(store, key, name) == expected
    assert identity.claim_project_folder(store, "name:Foo bar", "Renamed") == "Foo_bar"
    assert list(root.iterdir()) == []  # Submission does not create folders.


@pytest.mark.parametrize("first", ["cad", "parametric"])
def test_cad_and_parametric_same_name_never_take_each_others_folder(tmp_path, first):
    store, root = registry(tmp_path)
    keys = {"cad": "lineage:wgl_a", "parametric": f"design:{UID}"}
    first_folder = identity.claim_project_folder(store, keys[first], "Shared")
    second = "cad" if first == "parametric" else "parametric"
    assert first_folder == "Shared"
    assert identity.claim_project_folder(store, keys[second], "Shared") == "Shared-2"


def test_uid_alias_adoption_and_first_link_preserve_the_design_folder(tmp_path):
    store, root = registry(tmp_path)
    old = root / "Original"
    old.mkdir()
    (old / "design.json").write_text(
        json.dumps({"schemaVersion": 1, "lineageId": None, "name": "Original"})
    )
    claimed = identity.submission_archive(
        store,
        {
            "design_uid": UID,
            "design_name": "Renamed",
            "client_metadata": {"legacy_design_name": "Original"},
        },
        {},
    )
    # The original undecorated name is the adoption target, even before first solve.
    assert claimed["folder"] == "Original"
    assert (
        identity.submission_archive(store, {"design_uid": UID, "design_name": "Again"}, {})[
            "folder"
        ]
        == "Original"
    )
    assert (
        identity.submission_archive(store, {"design_name": "Original"}, {})["folder"] == "Original"
    )
    # Opening the same unstamped file again adopts its alias; Duplicate has no hint.
    assert (
        identity.submission_archive(
            store,
            {
                "design_uid": OTHER,
                "design_name": "Original",
                "client_metadata": {"legacy_design_name": "Original"},
            },
            {},
        )["folder"]
        == "Original"
    )
    assert (
        identity.submission_archive(
            store, {"design_uid": "new-copy", "design_name": "Original"}, {}
        )["folder"]
        == "Original-2"
    )
    saved = store.save(
        requested=None,
        design_hash="sha256:" + "a" * 64,
        filename="Fusion-new.cfg",
        snapshot_builder=lambda link: "snapshot",
    )
    design_id = saved["identity"].design_id
    linked = identity.submission_archive(
        store, {"design_uid": UID, "design_name": "Fusion-new", "design_id": design_id}, {}
    )
    assert linked["folder"] == "Original"
    assert linked["key"] == "lineage:" + saved["identity"].lineage_id
    assert store.claim_archive_stem(saved["identity"].lineage_id, preferred="Changed") == "Original"
    identity.write_project_record(
        root, row("Original", key=linked["key"], design_id=design_id), store
    )
    assert (
        json.loads((old / "design.json").read_text())["lineageId"] == saved["identity"].lineage_id
    )


def test_capture_only_adoption_but_never_unowned_parametric_runs(tmp_path):
    store, root = registry(tmp_path)
    (root / "Untitled" / "cad").mkdir(parents=True)
    assert store.claim_archive_stem("wgl_a", preferred="Untitled") == "Untitled"
    (root / "Occupied" / "1_Horn").mkdir(parents=True)
    assert store.claim_archive_stem("wgl_b", preferred="Occupied") == "Occupied-2"
    (root / "Named").mkdir()
    (root / "Named" / "design.json").write_text(
        json.dumps({"schemaVersion": 1, "lineageId": None, "name": "Named"})
    )
    assert store.claim_archive_stem("wgl_c", preferred="Named") == "Named-2"


def test_registry_backfill_is_additive_and_keeps_existing_hex_stems(tmp_path):
    store, root = registry(tmp_path)
    store.record_lineage_cad_names("wgl_old", archive_stem="Older-0123456789ab")
    with store._lock, store._transaction() as conn:
        conn.execute(
            "INSERT INTO imported_models(model_id, producer, display_name, archive_stem, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("mesh-file:sha256:old", "mesh-file", "mesh", "mesh_deadbeef", "then", "then"),
        )
        conn.execute("DELETE FROM archive_folders")
    store.close()
    store.initialize()
    assert identity.registered_folder(store, "lineage:wgl_old") == "Older-0123456789ab"
    assert identity.registered_folder(store, "model:mesh-file:sha256:old") == "mesh_deadbeef"
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == STORE_FORMAT_VERSION == 11
        assert (
            conn.execute(
                "SELECT archive_stem FROM lineage_cad_names WHERE lineage_id=?", ("wgl_old",)
            ).fetchone()[0]
            == "Older-0123456789ab"
        )


def test_concurrent_claims_are_serialized(tmp_path):
    store, root = registry(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        folders = list(
            pool.map(
                lambda n: identity.claim_project_folder(store, f"design:{n}", "Same"), range(8)
            )
        )
    assert len(set(folders)) == 8
    assert set(folders) == {"Same", *(f"Same-{n}" for n in range(2, 9))}


def test_pointer_create_update_and_foreign_owner_is_left_alone(tmp_path):
    store, root = registry(tmp_path)
    identity.claim_project_folder(store, f"design:{UID}", "Horn")
    identity.write_project_record(root, row(), store)
    pointer = root / "Horn" / "design.json"
    assert json.loads(pointer.read_text())["designUid"] == UID
    identity.write_project_record(root, row(design_name="Renamed"), store)
    assert json.loads(pointer.read_text())["name"] == "Renamed"
    original = pointer.read_bytes()
    # This is the old 409 loop: a run already lives beside a different lineage.
    identity.write_project_record(root, row(key="lineage:wgl_foreign", design_uid=None), store)
    assert pointer.read_bytes() == original


def test_owner_shaped_legacy_paths_are_derived_without_writes(tmp_path):
    store, root = registry(tmp_path)
    for prefix in ("250728solana", "Untitled"):
        for n in range(1, 11):
            label = f"{prefix}{n}"
            folder = root / label / f"{n}_{label}"
            folder.mkdir(parents=True)
            (folder / "run.json").write_text("{}")
            legacy = {"run_number": n, "label": label, "config_json": {}, "task_metadata": {}}
            before = json.dumps(legacy, sort_keys=True)
            assert identity.job_archive_location(legacy)[1:] == (label, f"{n}_{label}")
            assert json.dumps(legacy, sort_keys=True) == before
    for label, stem, expected in [
        ("cad-import", None, "cad-import"),
        ("new v103", "Old_v10", "Old_v10"),
        ("driver", "driver_hash", "driver_hash"),
    ]:
        old = {
            "run_number": 40,
            "label": label,
            "task_metadata": {"imported_geometry": {"archive_stem": stem}},
        }
        assert identity.job_archive_location(old).folder == expected
    assert identity.job_archive_location(
        {"run_number": 9, "config_summary_json": {"formula_type": "OSSE"}}
    )[1:] == ("OSSE", "9_OSSE")


def test_archived_completion_survives_foreign_pointer_and_records_absolute_path(tmp_path):
    store, root = registry(tmp_path)
    jobs = JobStore(tmp_path / "jobs.db")
    jobs.initialize()
    entry = {
        **row(key="lineage:wgl_new", design_uid=None),
        "status": "complete",
        "created_at": "now",
        "updated_at": "now",
        "queued_at": "now",
        "config_summary_json": {},
    }
    (root / "Horn").mkdir()
    pointer = root / "Horn" / "design.json"
    pointer.write_text('{"schemaVersion":1,"lineageId":"wgl_someone_else"}')
    original = pointer.read_bytes()
    jobs.create_job(entry)
    runtime = JobRuntime(jobs, cadlink_store=store)

    # No solve: patch the completed synthetic run using the real archive boundary.
    async def exercise():
        await runtime.patch_metadata("run", {"archived_at": "done"})
        await runtime.patch_metadata("run", {"archived_at": "done"})
        persisted = jobs.get_job_row("run")
        await runtime.shutdown()
        return persisted

    persisted = asyncio.run(exercise())
    assert persisted["task_metadata"]["archived_at"] == "done"
    assert persisted["task_metadata"]["archived_path"] == str(root / "Horn" / "1_Horn")
    assert pointer.read_bytes() == original


def test_cli_sends_stamp_and_server_claims_it(tmp_path):
    store, root = registry(tmp_path)
    built = build_request(
        parse(f"; Waveguide Generator design-id: {UID}\n" + SOURCE), design_name="CLI Horn"
    )
    wire = built.request.model_dump(mode="json")
    assert wire["design_uid"] == UID
    assert identity.submission_archive(store, wire, {}) == {
        "key": f"design:{UID}",
        "folder": "CLI_Horn",
    }


def test_submission_records_both_segments_and_label_patch_keeps_them(tmp_path, monkeypatch):
    from server.tests.test_jobs_runtime import _request
    from server.engines.registry import EngineRegistry, EngineInfo

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    store, root = registry(tmp_path)
    jobs = JobStore(tmp_path / "jobs.db")
    runtime = JobRuntime(jobs, cadlink_store=store)
    runtime.engine_registry = EngineRegistry(
        detector=lambda: [EngineInfo("dryrun", True, "test", "test")]
    )
    monkeypatch.setattr(runtime, "_ensure_scheduler", lambda: None)
    request = _request().model_copy(
        update={"design_uid": UID, "design_name": "Stable", "label": "Stable1"}
    )

    async def exercise():
        job_id = await runtime.submit(request)
        before = jobs.get_job_row(job_id)
        assert before["task_metadata"]["archive"] == {
            "key": f"design:{UID}",
            "folder": "Stable",
            "run": "1_Stable1",
        }
        await runtime.patch_metadata(job_id, {"label": "Changed"})
        after = jobs.get_job_row(job_id)
        item = runtime._serialize_job(after, cadlink_store=store)
        assert item["archive_folder"] == "Stable"
        assert item["archive_run"] == "1_Stable1"
        assert after["task_metadata"]["archive"] == before["task_metadata"]["archive"]
        await runtime.shutdown()

    asyncio.run(exercise())


def test_unavailable_runs_root_uses_registry_and_case_collision_stays_distinct(tmp_path):
    store, root = registry(tmp_path)

    def unavailable():
        raise OSError("offline")

    store.archive_root = unavailable
    assert identity.claim_project_folder(store, "name:Case", "Case") == "Case"
    assert identity.claim_project_folder(store, "name:case", "case") == "case-2"


def test_shared_legacy_alias_cannot_give_two_lineages_the_same_mirror(tmp_path):
    store, root = registry(tmp_path)
    for uid in (UID, OTHER):
        identity.submission_archive(
            store,
            {
                "design_uid": uid,
                "design_name": "Horn",
                "client_metadata": {"legacy_design_name": "Horn"},
            },
            {},
        )
    assert identity.bind_design_lineage(store, "wgl_a", UID, "Horn") == "Horn"
    assert identity.bind_design_lineage(store, "wgl_b", OTHER, "Horn") == "Horn-2"
    assert identity.registered_folder(store, "lineage:wgl_b") == "Horn-2"
    assert store.get_lineage_cad_names("wgl_b")["archive_stem"] == "Horn-2"


def test_recovered_legacy_stem_and_hash_keep_exact_existing_browser_location(tmp_path):
    from server.tests.test_cadlink_projects import project, app_with_runs

    app, root = app_with_runs(tmp_path)
    store = app.state.cadlink_store
    design_id, lineage_id = project(app)
    store.record_lineage_cad_names(lineage_id, archive_stem="Older_v10")
    ingest = store.allocate_ingest(
        manifest_sha256="sha256:legacy",
        artifact_sha256="sha256:legacy",
        record_builder=lambda ingest_id, created_at: json.dumps(
            {
                "ingest_id": ingest_id,
                "anchor": {"design_id": design_id},
                "document": {"name": "Newest v103", "return_state_hash": "sha256:aaa"},
            }
        ),
    )
    old = {
        "id": "old",
        "run_number": 40,
        "label": "cad-import",
        "config_json": {"geometry": {"type": "imported", "ingest_id": ingest["ingest_id"]}},
        "config_summary_json": {"geometry_type": "imported"},
        "task_metadata": {},
    }
    before = json.dumps(old, sort_keys=True)
    source = JobRuntime._serialize_job(old, cadlink_store=store)["cad_source"]
    assert source["return_state_hash"] == "sha256:aaa"
    item = JobRuntime._serialize_job(old, cadlink_store=store)
    assert item["archive_folder"] == source["archive_stem"] == "Older_v10"
    assert item["archive_run"] == "40_cad-import"
    assert item["archive_key"] is None
    assert json.dumps(old, sort_keys=True) == before
    from server.tests.test_cadlink_projects import capture
    from server.cadlink.api import ArchiveRunDocumentRequest, archive_run_document
    from types import SimpleNamespace

    capture(root, tmp_path, "Older_v10", "aaa", at="2026-08-20T09:00:00Z")
    jobs = app.state.jobs_runtime.store
    jobs.initialize()
    jobs.create_job(
        {
            **old,
            "status": "complete",
            "created_at": "then",
            "updated_at": "then",
            "queued_at": "then",
        }
    )
    result = asyncio.run(
        archive_run_document(ArchiveRunDocumentRequest(jobId="old"), SimpleNamespace(app=app))
    )
    assert result == {"placed": True, "relativePath": "1_cad-import/1_cad-import.f3d"}
    assert "archive" not in jobs.get_job_row("old")["task_metadata"]


def test_untitled_document_uses_undecorated_default_name(tmp_path):
    store, root = registry(tmp_path)
    first = identity.submission_archive(
        store, {"design_uid": UID, "design_name": "", "label": "Untitled1"}, {}
    )
    second = identity.submission_archive(
        store, {"design_uid": UID, "design_name": "", "label": "Untitled2"}, {}
    )
    assert first == second == {"key": f"design:{UID}", "folder": "Untitled"}


def test_deliberate_same_name_design_does_not_take_the_legacy_name_alias(tmp_path):
    store, root = registry(tmp_path)
    original = root / "Horn"
    original.mkdir()
    (original / "design.json").write_text(
        json.dumps({"schemaVersion": 1, "lineageId": None, "name": "Horn"})
    )
    assert (
        identity.submission_archive(store, {"design_uid": UID, "design_name": "Horn"}, {})["folder"]
        == "Horn-2"
    )
    assert identity.registered_folder(store, "name:Horn") is None
    reopened = identity.submission_archive(
        store,
        {
            "design_uid": OTHER,
            "design_name": "Horn",
            "client_metadata": {"legacy_design_name": "Horn"},
        },
        {},
    )
    assert reopened["folder"] == "Horn"


@pytest.mark.parametrize("operation", ["reuse", "first-link"])
@pytest.mark.parametrize("foreign", ["pointer", "run"])
def test_offline_registration_rechecks_disk_when_root_returns(tmp_path, operation, foreign):
    store, root = registry(tmp_path)
    store.archive_root = lambda: None
    assert identity.claim_project_folder(store, f"design:{UID}", "Horn") == "Horn"
    store.archive_root = lambda: root
    folder = root / "Horn"
    folder.mkdir()
    (folder / "design.json").write_text(json.dumps({"designUid": OTHER if foreign == "pointer" else UID}))
    run = folder / "1_Old"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"design": {"uid": OTHER}}))
    before = {p: p.read_bytes() for p in folder.rglob("*.json")}
    if operation == "first-link":
        result = identity.bind_design_lineage(store, "wgl_new", UID, "Horn")
    else:
        result = identity.claim_project_folder(store, f"design:{UID}", "Horn")
    assert result == "Horn-2"
    assert all(p.read_bytes() == content for p, content in before.items())


def test_downgrade_mirror_collision_backfill_does_not_share_owner(tmp_path, caplog):
    store, root = registry(tmp_path)
    assert identity.claim_project_folder(store, f"design:{UID}", "Horn") == "Horn"
    old_run = root / "Horn" / "1_Old"
    old_run.mkdir(parents=True)
    (old_run / "run.json").write_text('{"design":{"lineageId":"wgl_old"}}')
    with store._transaction() as conn:
        conn.execute("INSERT INTO lineage_cad_names (lineage_id, archive_stem, created_at, updated_at) VALUES ('wgl_old', 'Horn', 'then', 'then')")
        identity.initialize_registry(conn)
    assert identity.registered_folder(store, "lineage:wgl_old") == "Horn-2"
    assert store.get_lineage_cad_names("wgl_old")["archive_stem"] == "Horn-2"
    assert identity.claim_project_folder(store, "lineage:wgl_old", "Horn") == "Horn-2"
    assert old_run.is_dir()
    assert "mirror ownership conflict" in caplog.text
    # The design must also avoid the old lineage's on-disk runs on next claim.
    assert identity.claim_project_folder(store, f"design:{UID}", "Horn") == "Horn-3"


@pytest.mark.parametrize("name", ["CON", "CON.txt", "PRN", "AUX", "NUL", "COM1", "COM9.log", "LPT1", "LPT9.txt", "name.", "name ", 'bad:name?', "x" * 300])
def test_new_allocations_are_portable_and_stable(tmp_path, name):
    from server.workspace.portable_paths import path_segments

    store, root = registry(tmp_path)
    folder = identity.claim_project_folder(store, f"design:{UID}", name)
    assert path_segments(folder, "folder") == [folder]
    assert identity.claim_project_folder(store, f"design:{UID}", "Renamed") == folder


@pytest.mark.parametrize("name", ["CON", "COM1", "name."])
def test_invalid_persisted_registration_repairs_only_future_runs(tmp_path, name):
    from server.workspace.portable_paths import path_segments

    store, root = registry(tmp_path)
    with store._transaction() as conn:
        conn.execute("INSERT INTO archive_folders VALUES (?, ?, 'then')", (f"design:{UID}", name))
    old = row(folder=name)
    folder = identity.claim_project_folder(store, f"design:{UID}", "Renamed")
    assert folder != name
    assert path_segments(folder, "folder") == [folder]
    assert identity.job_archive_location(old).folder == name
    assert identity.registered_folder(store, f"design:{UID}") == folder


def test_recorded_absolute_path_wins_over_repaired_mirror_at_completion(tmp_path):
    store, root = registry(tmp_path)
    old = row(folder="Horn-2", run="2_New")
    old["task_metadata"]["archived_path"] = str(root / "Horn" / "1_Old")
    current = tmp_path / "new-runs"
    current.mkdir()
    store.archive_root = lambda: current
    assert identity.job_archive_location(old)[1:] == ("Horn", "1_Old")
    assert identity.complete_archive(store, old) == str(root / "Horn" / "1_Old")
    assert (root / "Horn" / "design.json").is_file()
    assert not (root / "Horn-2").exists()
    assert list(current.iterdir()) == []
    root.rename(tmp_path / "offline")
    with pytest.raises(OSError, match="recorded archive location is unavailable"):
        identity.complete_archive(store, old)
    assert not root.exists()
