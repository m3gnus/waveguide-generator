"""Bounded CAD history listings retain the exact head and export summaries."""

from __future__ import annotations

import sqlite3

import pytest

from server.cadlink.store import CadLinkStore


def listing_store(designs: int = 180, exports_per_design: int = 100) -> CadLinkStore:
    store = CadLinkStore(":memory:")
    store.initialize()
    conn = store._connect()
    conn.executemany(
        """INSERT INTO designs
           (design_id, lineage_id, edit_version, design_hash, snapshot_text,
            filename, created_at, updated_at)
           VALUES (?, ?, 1, 'hash', 'saved snapshot', ?, ?, ?)""",
        [(f"d{i:05}", f"l{i // 3:05}", f"design-{i}.cfg", str(i // 2), str(i // 2))
         for i in range(designs)],
    )
    conn.executemany(
        """INSERT INTO exports
           (export_id, bundle_id, design_id, sequence, edit_version, design_hash,
            geometry_hash, artifact_sha256, manifest_json, snapshot_text,
            idempotency_key, created_at)
           VALUES (?, ?, ?, ?, 1, 'hash', 'geometry', 'artifact', '{}',
                   'export snapshot', ?, ?)""",
        [(f"e{i}-{j}", f"b{i}-{j}", f"d{i:05}", j, f"k{i}-{j}", f"t{j:05}")
         for i in range(designs) if i % 4
         for j in range(exports_per_design)],
    )
    conn.commit()
    return store


@pytest.fixture
def store():
    value = listing_store()
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize("projects", [False, True])
@pytest.mark.parametrize("limit", [0, 1, 7, 500, 501])
def test_listing_preserves_order_ties_bounds_and_export_summaries(store, projects, limit):
    conn = store._connect()
    expected = [dict(row) for row in conn.execute(
        """SELECT design_id, lineage_id, edit_version, design_hash, filename,
                  branched_from_design_id, branched_from_edit_version,
                  created_at, updated_at FROM designs
           ORDER BY updated_at DESC, design_id DESC"""
    )]
    if projects:
        seen = set()
        heads = []
        for row in expected:
            if row["lineage_id"] not in seen:
                heads.append(row)
                seen.add(row["lineage_id"])
        expected = heads
    expected = expected[:max(1, min(limit, 500))]
    for row in expected:
        index = int(row["design_id"][1:])
        row.update(export_count=100 if index % 4 else 0,
                   last_exported_at="t00099" if index % 4 else None)

    def no_snapshots(action, table, column, *_):
        if action == sqlite3.SQLITE_READ and column in {"snapshot_text", "manifest_json"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(no_snapshots)
    try:
        actual = store.list_projects(limit=limit) if projects else store.list_designs(limit=limit)
    finally:
        conn.set_authorizer(None)
    assert actual == expected


@pytest.mark.parametrize("projects", [False, True])
def test_small_listing_does_not_aggregate_exports_of_unlisted_designs(store, projects):
    # An instruction budget catches whole-history aggregation without timing
    # assertions that depend on the release gate's load on the shared machine.
    steps = 0

    def budget():
        nonlocal steps
        steps += 1
        return int(steps > 50)

    conn = store._connect()
    conn.set_progress_handler(budget, 1000)
    try:
        rows = store.list_projects(limit=1) if projects else store.list_designs(limit=1)
    finally:
        conn.set_progress_handler(None, 0)
    assert len(rows) == 1
    assert rows[0]["design_id"] == "d00019"
    assert rows[0]["export_count"] == 100


@pytest.mark.parametrize("projects", [False, True])
def test_listing_caps_results_at_500_heads(projects):
    store = listing_store(designs=1503, exports_per_design=0)
    try:
        rows = store.list_projects(limit=501) if projects else store.list_designs(limit=501)
        assert len(rows) == 500
    finally:
        store.close()
