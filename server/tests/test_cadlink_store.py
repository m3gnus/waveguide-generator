"""Transactional CAD-link registry contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
import sqlite3

import pytest

from server.cadlink.identity import SaveIdentity
from server.cadlink.roles import inventory_sha256
from server.cadlink.store import STORE_FORMAT_VERSION, CadLinkStore


def _save(
    store: CadLinkStore,
    requested: SaveIdentity | None = None,
    *,
    marker: str = "one",
    filename: str = "design.cfg",
    saved_at: str = "2026-08-10T14:22:31Z",
) -> dict[str, object]:
    return store.save(
        requested=requested,
        design_hash="sha256:" + hashlib.sha256(marker.encode()).hexdigest(),
        filename=filename,
        snapshot_builder=lambda identity: (
            f"DesignId={identity.design_id};Version={identity.edit_version};{marker}"
        ),
        saved_at=saved_at,
    )


def _request(result: dict[str, object], *, version: int | None = None) -> SaveIdentity:
    identity = result["identity"]
    return SaveIdentity.model_validate(
        {
            "designId": identity.design_id,  # type: ignore[union-attr]
            "lineageId": identity.lineage_id,  # type: ignore[union-attr]
            "baseEditVersion": version or identity.edit_version,  # type: ignore[union-attr]
        }
    )


def test_registry_schema_is_separate_versioned_and_snapshot_preserving(tmp_path: Path) -> None:
    store = CadLinkStore.for_data_dir(tmp_path)
    first = _save(store)
    store.close()

    db_path = tmp_path / "db" / "cadlink.db"
    assert db_path.exists()
    assert not (tmp_path / "db" / "simulations.db").exists()
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == STORE_FORMAT_VERSION
    assert {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "designs",
        "exports",
        "export_reservations",
        "ingests",
        "onshape_links",
        "lineage_cad_names",
        "cad_operations",
    }
    snapshot = conn.execute("SELECT snapshot_text FROM designs").fetchone()[0]
    assert snapshot == first["text"]
    conn.close()


def test_v2_registry_with_rows_migrates_to_current_without_data_loss(tmp_path: Path) -> None:
    store = CadLinkStore.for_data_dir(tmp_path)
    saved = _save(store)
    design_id = saved["identity"].design_id  # type: ignore[union-attr]
    exported = store.allocate_export(
        design_id=design_id,
        geometry_hash="sha256:geometry",
        artifact_sha256="sha256:artifact",
        manifest_json="{}",
        idempotency_key="migration-row",
    )
    store.close()

    db_path = tmp_path / "db" / "cadlink.db"
    connection = sqlite3.connect(db_path)
    connection.execute("DROP TABLE ingests")
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()

    migrated = CadLinkStore(db_path)
    migrated.initialize()
    assert migrated.get_design(design_id)["snapshot_text"] == saved["text"]  # type: ignore[index]
    assert migrated.get_export(exported["export_id"])["idempotency_key"] == "migration-row"  # type: ignore[index]
    migrated.close()

    connection = sqlite3.connect(db_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == STORE_FORMAT_VERSION
    assert connection.execute("SELECT COUNT(*) FROM designs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 1
    connection.close()


def test_save_cas_updates_head_then_auto_forks_shared_lineage(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    first = _save(store)
    second = _save(store, _request(first), marker="two")
    fork = _save(store, _request(first), marker="fork")

    first_identity = first["identity"]
    second_identity = second["identity"]
    fork_identity = fork["identity"]
    assert second_identity.design_id == first_identity.design_id  # type: ignore[union-attr]
    assert second_identity.edit_version == 2  # type: ignore[union-attr]
    assert fork["forked"] is True
    assert fork_identity.design_id != first_identity.design_id  # type: ignore[union-attr]
    assert fork_identity.lineage_id == first_identity.lineage_id  # type: ignore[union-attr]
    assert fork_identity.edit_version == 1  # type: ignore[union-attr]
    row = store.get_design(fork_identity.design_id)  # type: ignore[union-attr]
    assert row is not None
    assert row["branched_from_design_id"] == first_identity.design_id  # type: ignore[union-attr]
    assert row["branched_from_edit_version"] == 1


def test_project_listing_orders_heads_without_loading_snapshot_text(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    first = _save(store, marker="first")
    first_id = first["identity"].design_id  # type: ignore[union-attr]
    store.allocate_export(
        design_id=first_id,
        geometry_hash="sha256:geometry",
        artifact_sha256="sha256:artifact",
        manifest_json="{}",
        idempotency_key="project-list-export",
        created_at="2026-08-10T15:00:00Z",
    )
    second_hash = "sha256:" + hashlib.sha256(b"second").hexdigest()
    second = store.save(
        requested=None,
        design_hash=second_hash,
        filename="newer.cfg",
        snapshot_builder=lambda identity: f"DesignId={identity.design_id};newer",
        saved_at="2026-08-11T14:22:31Z",
    )

    rows = store.list_designs()

    assert [row["design_id"] for row in rows] == [
        second["identity"].design_id,  # type: ignore[union-attr]
        first_id,
    ]
    assert "snapshot_text" not in rows[0]
    assert rows[1]["export_count"] == 1
    assert rows[1]["last_exported_at"] == "2026-08-10T15:00:00Z"


def test_project_listing_uses_only_the_newest_head_in_each_lineage(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    first = _save(
        store,
        marker="first",
        filename="first-name.cfg",
        saved_at="2026-08-10T10:00:00Z",
    )
    _save(
        store,
        _request(first),
        marker="updated",
        filename="older-head-name.cfg",
        saved_at="2026-08-11T10:00:00Z",
    )
    fork = _save(
        store,
        _request(first),
        marker="fork",
        filename="canonical-name.cfg",
        saved_at="2026-08-12T10:00:00Z",
    )

    rows = store.list_projects()

    assert len(store.list_designs()) == 2
    assert [row["design_id"] for row in rows] == [fork["identity"].design_id]  # type: ignore[union-attr]
    assert rows[0]["filename"] == "canonical-name.cfg"


def test_exact_lineage_lookup_returns_the_newest_head(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    first = _save(store, marker="first", saved_at="2026-08-10T10:00:00Z")
    fork = _save(
        store,
        _request(first),
        marker="fork",
        saved_at="2026-08-12T10:00:00Z",
    )
    lineage_id = first["identity"].lineage_id  # type: ignore[union-attr]

    latest = store.find_latest_design_for_lineage(lineage_id)

    assert latest is not None
    assert latest["design_id"] == fork["identity"].design_id  # type: ignore[union-attr]
    assert store.find_latest_design_for_lineage("wgl_absent") is None


def test_export_sequences_are_atomic_and_idempotent_across_store_instances(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cadlink.db"
    seed_store = CadLinkStore(db_path)
    saved = _save(seed_store)
    design_id = saved["identity"].design_id  # type: ignore[union-attr]
    seed_store.close()

    def allocate(index: int) -> dict[str, object]:
        store = CadLinkStore(db_path)
        try:
            return store.allocate_export(
                design_id=design_id,
                geometry_hash=f"sha256:geometry{index}",
                artifact_sha256=f"sha256:artifact{index}",
                manifest_json=f'{{"attempt":{index}}}',
                idempotency_key=f"attempt-{index}",
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(allocate, range(16)))
    assert sorted(row["sequence"] for row in rows) == list(range(1, 17))
    assert len({row["export_id"] for row in rows}) == 16

    retry_store = CadLinkStore(db_path)
    original = retry_store.allocate_export(
        design_id=design_id,
        geometry_hash="sha256:first",
        artifact_sha256="sha256:first",
        manifest_json='{"stored":true}',
        idempotency_key="retry-key",
    )
    retry = retry_store.allocate_export(
        design_id=design_id,
        geometry_hash="sha256:different",
        artifact_sha256="sha256:different",
        manifest_json='{"stored":false}',
        idempotency_key="retry-key",
    )
    assert retry == original
    assert retry_store.get_export(original["export_id"]) == original
    assert retry_store.get_export(original["bundle_id"]) == original
    assert retry_store.find_export_by_idempotency_key("retry-key") == original
    assert retry_store.find_export_by_artifact("sha256:first") == original
    assert original["snapshot_text"] == saved["text"]

    built = retry_store.allocate_export(
        design_id=design_id,
        geometry_hash="sha256:built",
        artifact_sha256="sha256:built",
        manifest_builder=lambda facts: json.dumps(facts, sort_keys=True),
        idempotency_key="built-manifest",
    )
    manifest = json.loads(built["manifest_json"])
    assert manifest["exportId"] == built["export_id"]
    assert manifest["bundleId"] == built["bundle_id"]
    assert manifest["sequence"] == built["sequence"]
    retry_store.close()


def test_export_builder_receives_allocated_identity_and_is_skipped_on_retry(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _save(store)
    design_id = saved["identity"].design_id  # type: ignore[union-attr]
    calls: list[dict[str, object]] = []

    def build(facts):
        calls.append(dict(facts))
        return {
            "manifest_json": json.dumps(facts, sort_keys=True),
            "geometry_hash": "sha256:geometry",
            "artifact_sha256": "sha256:artifact",
        }

    original = store.allocate_export(
        design_id=design_id,
        idempotency_key="built-at-allocation",
        export_builder=build,
    )
    retry = store.allocate_export(
        design_id=design_id,
        idempotency_key="built-at-allocation",
        export_builder=lambda _facts: (_ for _ in ()).throw(AssertionError("rebuilt")),
    )

    assert retry == original
    assert len(calls) == 1
    assert calls[0]["exportId"] == original["export_id"]
    assert calls[0]["sequence"] == 1
    assert original["geometry_hash"] == "sha256:geometry"
    assert original["artifact_sha256"] == "sha256:artifact"


def test_concurrent_export_builders_are_serialized_with_gapless_parent_linkage(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cadlink.db"
    seed = CadLinkStore(db_path)
    saved = _save(seed)
    design_id = saved["identity"].design_id  # type: ignore[union-attr]
    seed.close()

    def allocate(index: int):
        store = CadLinkStore(db_path)
        try:
            return store.allocate_export(
                design_id=design_id,
                idempotency_key=f"concurrent-{index}",
                export_builder=lambda facts: (
                    time.sleep(0.02)
                    or {
                        "manifest_json": json.dumps(facts),
                        "geometry_hash": f"sha256:geometry-{index}",
                        "artifact_sha256": f"sha256:artifact-{index}",
                    }
                ),
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(allocate, range(2)))

    ordered = sorted(rows, key=lambda row: row["sequence"])
    assert [row["sequence"] for row in ordered] == [1, 2]
    assert ordered[0]["parent_export_id"] is None
    assert ordered[1]["parent_export_id"] == ordered[0]["export_id"]


def test_concurrent_same_key_runs_exactly_one_export_builder(tmp_path: Path) -> None:
    db_path = tmp_path / "cadlink.db"
    seed = CadLinkStore(db_path)
    saved = _save(seed)
    design_id = saved["identity"].design_id  # type: ignore[union-attr]
    seed.close()
    calls = 0
    calls_lock = threading.Lock()

    def allocate(_index: int):
        store = CadLinkStore(db_path)

        def build(facts):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.03)
            return {
                "manifest_json": json.dumps(facts),
                "geometry_hash": "sha256:geometry",
                "artifact_sha256": "sha256:artifact",
            }

        try:
            return store.allocate_export(
                design_id=design_id,
                idempotency_key="same-key",
                export_builder=build,
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(allocate, range(2)))

    assert rows[0] == rows[1]
    assert calls == 1


def test_slow_export_builder_does_not_block_registry_reads(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _save(store)
    design_id = saved["identity"].design_id  # type: ignore[union-attr]
    building = threading.Event()
    release = threading.Event()

    def build(facts):
        building.set()
        assert release.wait(timeout=30), "the test must release its export builder"
        return {
            "manifest_json": json.dumps(facts),
            "geometry_hash": "sha256:geometry",
            "artifact_sha256": "sha256:artifact",
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            store.allocate_export,
            design_id=design_id,
            idempotency_key="slow-build",
            export_builder=build,
        )
        try:
            assert building.wait(timeout=5)
            # Prove the read completes while the builder is still blocked,
            # rather than comparing two tiny wall-clock windows on a busy host.
            design = pool.submit(store.get_design, design_id).result(timeout=5)
            assert not future.done()
        finally:
            release.set()
        exported = future.result(timeout=5)

    assert design is not None
    assert exported["sequence"] == 1


def test_failed_export_build_keeps_the_same_retryable_reservation(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    saved = _save(store)
    design_id = saved["identity"].design_id  # type: ignore[union-attr]
    attempts: list[dict[str, object]] = []

    def fail(facts):
        attempts.append(dict(facts))
        raise RuntimeError("STEP generator failed")

    with pytest.raises(RuntimeError, match="STEP generator failed"):
        store.allocate_export(
            design_id=design_id,
            idempotency_key="retry-build",
            export_builder=fail,
        )

    connection = sqlite3.connect(tmp_path / "cadlink.db")
    assert connection.execute(
        "SELECT state FROM export_reservations WHERE idempotency_key = 'retry-build'"
    ).fetchone() == ("retryable",)
    connection.close()

    exported = store.allocate_export(
        design_id=design_id,
        idempotency_key="retry-build",
        export_builder=lambda facts: (
            attempts.append(dict(facts))
            or {
                "manifest_json": json.dumps(facts),
                "geometry_hash": "sha256:geometry",
                "artifact_sha256": "sha256:artifact",
            }
        ),
    )

    assert attempts[0] == attempts[1]
    assert exported["export_id"] == attempts[0]["exportId"]
    assert exported["sequence"] == 1


def test_lineage_cad_names_are_claimed_once_and_span_a_fork(tmp_path: Path) -> None:
    """A linked CAD document depends on these names, so they never move."""

    store = CadLinkStore(tmp_path / "cadlink.db")
    first = _save(store)
    fork = _save(store, _request(first), marker="fork")
    lineage_id = first["identity"].lineage_id  # type: ignore[union-attr]
    assert fork["identity"].lineage_id == lineage_id  # type: ignore[union-attr]
    assert store.get_lineage_cad_names(lineage_id) is None

    claimed = store.record_lineage_cad_names(lineage_id, parameter_slug="demo_horn")
    later = store.record_lineage_cad_names(
        lineage_id, parameter_slug="tritonia", bundle_stem="demo_horn"
    )

    assert claimed["parameter_slug"] == "demo_horn"
    assert claimed["bundle_stem"] is None
    assert later["parameter_slug"] == "demo_horn"
    assert later["bundle_stem"] == "demo_horn"


def test_v9_archive_stem_migration_resolves_portable_collisions_stably(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cadlink.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE lineage_cad_names (
          lineage_id TEXT PRIMARY KEY,
          parameter_slug TEXT,
          bundle_stem TEXT,
          archive_stem TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    rows = [
        ("wgl_a", "Horn A"),
        ("wgl_b", "Horn+A"),
        ("wgl_c", "Horn"),
        ("wgl_d", "horn"),
    ]
    connection.executemany(
        "INSERT INTO lineage_cad_names VALUES (?, NULL, NULL, ?, ?, ?)",
        [
            (lineage_id, stem, "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z")
            for lineage_id, stem in rows
        ],
    )
    connection.execute("PRAGMA user_version = 9")
    connection.commit()
    connection.close()

    migrated = CadLinkStore(db_path)
    migrated.initialize()
    claimed = {
        lineage_id: migrated.get_lineage_cad_names(lineage_id)["archive_stem"]  # type: ignore[index]
        for lineage_id, _stem in rows
    }
    assert claimed["wgl_a"] == "Horn_A"
    assert claimed["wgl_b"].startswith("Horn_A-")
    assert claimed["wgl_c"] == "Horn"
    assert claimed["wgl_d"].startswith("horn-")
    assert len({stem.casefold() for stem in claimed.values()}) == 4
    migrated.close()

    restarted = CadLinkStore(db_path)
    restarted.initialize()
    assert {
        lineage_id: restarted.get_lineage_cad_names(lineage_id)["archive_stem"]  # type: ignore[index]
        for lineage_id, _stem in rows
    } == claimed
    restarted.close()

    connection = sqlite3.connect(db_path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == STORE_FORMAT_VERSION
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type = 'index' AND name = 'lineage_cad_names_by_archive_stem'"
    ).fetchone()[0] == 1
    connection.close()


def test_archive_stems_are_unique_under_concurrent_portable_claims(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cadlink.db"
    seed = CadLinkStore(db_path)
    seed.initialize()
    seed.close()
    requested = [
        ("wgl_a", "Horn A"),
        ("wgl_b", "Horn+A"),
        ("wgl_c", "Horn"),
        ("wgl_d", "horn"),
    ]

    def claim(item: tuple[str, str]) -> tuple[str, str]:
        lineage_id, preferred = item
        store = CadLinkStore(db_path)
        try:
            result = store.claim_archive_stem(lineage_id, preferred=preferred)
            assert result is not None
            return lineage_id, result
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = dict(pool.map(claim, requested))

    assert len({stem.casefold() for stem in claimed.values()}) == 4
    assert all(" " not in stem and "+" not in stem for stem in claimed.values())
    restarted = CadLinkStore(db_path)
    restarted.initialize()
    try:
        assert {
            lineage_id: restarted.claim_archive_stem(
                lineage_id, preferred="renamed after restart"
            )
            for lineage_id, _preferred in requested
        } == claimed
    finally:
        restarted.close()


def test_latest_lineage_export_reaches_across_designs(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    first = _save(store)
    fork = _save(store, _request(first), marker="fork")
    lineage_id = first["identity"].lineage_id  # type: ignore[union-attr]
    assert store.find_latest_export_for_lineage(lineage_id) is None

    for index, saved in enumerate((first, fork)):
        store.allocate_export(
            design_id=saved["identity"].design_id,  # type: ignore[union-attr]
            geometry_hash="sha256:geometry",
            artifact_sha256="sha256:artifact",
            manifest_json=json.dumps({"n": index}),
            idempotency_key=f"key-{index}",
            created_at=f"2026-08-1{index}T00:00:00Z",
        )

    latest = store.find_latest_export_for_lineage(lineage_id)
    assert latest is not None
    assert latest["design_id"] == fork["identity"].design_id  # type: ignore[union-attr]
    assert store.find_latest_export_for_lineage("wgl_absent") is None


# -- model records: ingests.model_id and imported_models ------------------------------


def _old_ingest(db_path: Path, ingest_id: str, record: object, created_at: str) -> None:
    """An ingest as a release before model records writes it: model_id stays NULL."""

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO ingests (ingest_id, manifest_sha256, artifact_sha256, record_json, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (
                ingest_id, "sha256:" + "m" * 64, "sha256:" + "a" * 64,
                record if isinstance(record, str) else json.dumps(record), created_at,
            ),
        )


def _file_record(filename: str, stem: str, artifact: str = "1") -> dict[str, object]:
    return {
        "producer": "mesh-file",
        "artifact_sha256": "sha256:" + artifact * 64,
        "source_file": {"filename": filename},
        "archive_stem": stem,
        "sources": [{"id": "group_2", "role": "SOURCE", "required": True}],
    }


def _models(db_path: Path) -> tuple[dict[str, object], list[tuple[object, ...]]]:
    with sqlite3.connect(db_path) as conn:
        filed = dict(conn.execute("SELECT ingest_id, model_id FROM ingests").fetchall())
        models = conn.execute("SELECT * FROM imported_models ORDER BY model_id").fetchall()
    return filed, models


def _reopen(store: CadLinkStore) -> None:
    store.close()
    store.initialize()


def test_backfill_files_every_older_ingest_under_its_model(tmp_path: Path) -> None:
    db_path = tmp_path / "cadlink.db"
    store = CadLinkStore(db_path)
    try:
        saved = _save(store)
        design_id = saved["identity"].design_id  # type: ignore[union-attr]
        design_lineage = saved["identity"].lineage_id  # type: ignore[union-attr]
        document_lineage = store.claim_cad_document_lineage("urn:fusion:doc", "Speaker")
        # Another project exists (an open one, say); nothing may land in it by default.
        _save(store, marker="open project", filename="open.cfg")
        _old_ingest(db_path, "wgi_file_old", _file_record("first.msh", "first_aaaa"), "2026-10-01T10:00:00Z")
        _old_ingest(db_path, "wgi_file_new", _file_record("renamed.msh", "renamed_bbbb"), "2026-10-02T10:00:00Z")
        _old_ingest(db_path, "wgi_project", {"project": {
            "lineage_id": "wgl_project", "document_name": "Box", "archive_stem": "box",
        }}, "2026-10-01T11:00:00Z")
        _old_ingest(db_path, "wgi_anchor", {"anchor": {"design_id": design_id}}, "2026-10-01T12:00:00Z")
        _old_ingest(db_path, "wgi_document", {
            "anchor": {"design_id": None}, "document": {"native_id": "urn:fusion:doc", "name": "Speaker"},
        }, "2026-10-01T13:00:00Z")
        _old_ingest(db_path, "wgi_unknown", {"anchor": {"design_id": "wgd_gone"}}, "2026-10-01T14:00:00Z")
        _old_ingest(db_path, "wgi_native", {"producer": "native"}, "2026-10-01T15:00:00Z")
        _old_ingest(db_path, "wgi_broken", "{not json", "2026-10-01T16:00:00Z")

        _reopen(store)

        filed, _rows = _models(db_path)
        file_model = "mesh-file:sha256:" + "1" * 64
        assert filed == {
            "wgi_file_old": file_model,
            "wgi_file_new": file_model,
            "wgi_project": "wgl_project",
            "wgi_anchor": design_lineage,
            "wgi_document": document_lineage,
            # Checked and unfiled: never the open project.
            "wgi_unknown": "",
            "wgi_native": "",
            "wgi_broken": "",
        }
        model = store.get_model(file_model)
        assert model is not None
        assert (model["producer"], model["display_name"], model["archive_stem"], model["latest_ingest_id"]) == (
            "mesh-file", "renamed.msh", "first_aaaa", "wgi_file_new",
        )
        assert (model["created_at"], model["updated_at"]) == ("2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z")
        project = store.get_model("wgl_project")
        assert project is not None
        assert (project["producer"], project["display_name"], project["archive_stem"]) == ("cad", "Box", "box")
        assert store.get_model(document_lineage)["display_name"] == "Speaker"  # type: ignore[index]
        assert store.get_model("") is None
        assert [row["ingest_id"] for row in store.list_model_ingests(file_model)] == [
            "wgi_file_new", "wgi_file_old",
        ]
    finally:
        store.close()


def test_backfill_is_idempotent_and_never_refiles_a_filed_row(tmp_path: Path) -> None:
    db_path = tmp_path / "cadlink.db"
    store = CadLinkStore(db_path)
    try:
        store.initialize()
        _old_ingest(db_path, "wgi_one", _file_record("one.msh", "one"), "2026-10-01T10:00:00Z")
        _old_ingest(db_path, "wgi_unfiled", {"anchor": {}}, "2026-10-01T11:00:00Z")
        _reopen(store)
        first = _models(db_path)
        # A row filed once keeps its model, even one the rule would now file elsewhere.
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE ingests SET model_id = 'wgl_kept' WHERE ingest_id = 'wgi_unfiled'")
        _reopen(store)
        _reopen(store)
        filed, models = _models(db_path)
        assert models == first[1]
        assert filed == {**first[0], "wgi_unfiled": "wgl_kept"}
        # A later build's own ingest is filed as it is recorded.
        row = store.allocate_ingest(
            manifest_sha256="sha256:" + "m" * 64, artifact_sha256="sha256:" + "1" * 64,
            record_builder=lambda ingest_id, created_at: json.dumps(_file_record("two.msh", "two")),
        )
        assert row["model_id"] == "mesh-file:sha256:" + "1" * 64
        assert store.get_model(row["model_id"])["latest_ingest_id"] == row["ingest_id"]  # type: ignore[index]
    finally:
        store.close()


def test_file_setups_move_to_their_model_and_the_newest_wins(tmp_path: Path) -> None:
    db_path = tmp_path / "cadlink.db"
    store = CadLinkStore(db_path)
    try:
        store.initialize()
        for ingest_id, created in (("wgi_a", "2026-10-01T10:00:00Z"), ("wgi_b", "2026-10-02T10:00:00Z")):
            _old_ingest(db_path, ingest_id, _file_record(f"{ingest_id}.msh", ingest_id), created)
        # A file whose ingest is gone keeps its old row and moves nowhere.
        old_rows = [
            ("mesh-file:wgi_b", "sha256:spec-b", "wgs_newer", "2026-10-05T10:00:00Z"),
            ("mesh-file:wgi_a", "sha256:spec-a", "wgs_older", "2026-10-04T10:00:00Z"),
            ("mesh-file:wgi_gone", "sha256:spec-c", "wgs_gone", "2026-10-06T10:00:00Z"),
            ("wgl_cad", "sha256:inventory", "wgs_cad", "2026-10-03T10:00:00Z"),
        ]
        with sqlite3.connect(db_path) as conn:
            conn.executemany("INSERT INTO cad_project_setups VALUES (?, ?, ?, ?)", old_rows)
        _reopen(store)

        model_id = "mesh-file:sha256:" + "1" * 64
        inventory = inventory_sha256([{"id": "group_2", "role": "SOURCE", "required": True}])
        assert store.get_model_setup(model_id, inventory)["revision_id"] == "wgs_newer"  # type: ignore[index]
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT * FROM cad_project_setups ORDER BY lineage_id").fetchall()
        assert set(old_rows) <= set(rows)
        assert len(rows) == len(old_rows) + 1
        # Once the user saves a setup of their own, reopening never puts the old one back.
        store.record_model_setup(model_id, inventory, "wgs_mine")
        _reopen(store)
        _reopen(store)
        assert store.get_model_setup(model_id, inventory)["revision_id"] == "wgs_mine"  # type: ignore[index]
    finally:
        store.close()


def test_a_pathological_record_is_unfiled_and_never_stops_the_registry_opening(tmp_path: Path) -> None:
    db_path = tmp_path / "cadlink.db"
    store = CadLinkStore(db_path)
    try:
        store.initialize()
        # Nested past the JSON decoder's recursion limit, a record that is not
        # an object, fields of the wrong types, and a good row after them all.
        _old_ingest(db_path, "wgi_deep", "[" * 200_000 + "]" * 200_000, "2026-10-01T10:00:00Z")
        _old_ingest(db_path, "wgi_list", [1, 2, 3], "2026-10-01T11:00:00Z")
        _old_ingest(db_path, "wgi_types", {"producer": ["mesh-file"], "project": "wgl_x",
                                           "anchor": 7, "document": None}, "2026-10-01T12:00:00Z")
        _old_ingest(db_path, "wgi_good", _file_record("good.msh", "good"), "2026-10-01T13:00:00Z")
        _reopen(store)
        filed, _models_rows = _models(db_path)
        assert filed == {"wgi_deep": "", "wgi_list": "", "wgi_types": "",
                         "wgi_good": "mesh-file:sha256:" + "1" * 64}
        assert store.get_model("mesh-file:sha256:" + "1" * 64) is not None
    finally:
        store.close()
