"""Transactional CAD-link registry contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
import sqlite3

from server.cadlink.identity import SaveIdentity
from server.cadlink.store import CadLinkStore


def _save(
    store: CadLinkStore,
    requested: SaveIdentity | None = None,
    *,
    marker: str = "one",
) -> dict[str, object]:
    return store.save(
        requested=requested,
        design_hash="sha256:" + hashlib.sha256(marker.encode()).hexdigest(),
        filename="design.cfg",
        snapshot_builder=lambda identity: (
            f"DesignId={identity.design_id};Version={identity.edit_version};{marker}"
        ),
        saved_at="2026-08-10T14:22:31Z",
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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "designs",
        "exports",
        "ingests",
        "onshape_links",
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
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
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
