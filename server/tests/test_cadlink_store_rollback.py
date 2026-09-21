"""A release that rolls back must still open the CAD Link registry.

An update's rollback, and a later Return to Stable, replace the application
but leave ``cadlink.db`` in the data directory. Every release refuses a
``PRAGMA user_version`` above the highest it knows, so a build that raised it
would leave the release it rolled back to with every CAD Link operation
failing. The operation store (schema 12) only adds a table, so the file keeps
the format v0.3.2 reads (docs/reference/UPDATE-TRANSACTION-CONTRACT.md §6).

The rollback test runs each release a user can roll back to
(``CADLINK_ROLLBACK_TAGS``: v0.3.2 and the published v0.3.3-rc.1) from its own
tag, against a registry in which this build filled every table it added, and
checks every row and column of them after the rollback and after rolling
forward again.
"""

from __future__ import annotations

from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import textwrap

import pytest

from server.cadlink.operations import (
    ACCEPTED,
    NEEDS_USER_INPUT,
    PREPARE_AND_SOLVE,
    REQUEST_RETURN,
    normalize_request,
    request_digest,
)
from server.cadlink.store import _OPERATION_COLUMNS, CadLinkStore

from _release_tags import CADLINK_ROLLBACK_TAGS, skip_or_fail_missing_tag


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: What v0.3.2 opens: ``version not in {0, ..., 11}`` refuses
#: (``v0.3.2:server/cadlink/store.py``). Frozen here so the promise is tested
#: where the tag is unreachable.
RELEASED_READER_ACCEPTS = frozenset(range(12))


def _user_version(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _tables(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _open(db_path: Path) -> None:
    store = CadLinkStore(db_path)
    try:
        store.initialize()
    finally:
        store.close()


def _save_design(store: CadLinkStore) -> tuple[str, str]:
    saved = store.save(
        requested=None,
        design_hash="sha256:" + "3" * 64,
        filename="design.cfg",
        snapshot_builder=lambda identity: f"DesignId={identity.design_id}",
        saved_at="2026-09-13T10:00:00Z",
    )
    return saved["identity"].design_id, saved["text"]


def _accept_solve(store: CadLinkStore, operation_id: str) -> None:
    inputs = {
        "return_id": "wgr_1",
        "bundle_path": "wgreturn/speaker.wgreturn",
        "manifest_sha256": "sha256:" + "4" * 64,
    }
    digest = request_digest(PREPARE_AND_SOLVE, {}, inputs)
    _row, outcome = store.accept_operation(operation_id, PREPARE_AND_SOLVE, digest, {}, inputs)
    assert outcome == "created"


@pytest.mark.parametrize("start", ["fresh", "v11", "written-as-12"])
def test_every_open_leaves_a_format_the_previous_release_reads(
    tmp_path: Path, start: str
) -> None:
    db_path = tmp_path / "cadlink.db"
    if start != "fresh":
        _open(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            if start == "v11":
                conn.execute("DROP TABLE cad_operations")
                conn.execute("PRAGMA user_version = 11")
            else:
                # What builds between the operation store and this fix wrote.
                conn.execute("PRAGMA user_version = 12")
            conn.commit()

    _open(db_path)

    assert _user_version(db_path) in RELEASED_READER_ACCEPTS
    assert _user_version(db_path) == 11
    assert "cad_operations" in _tables(db_path)


_RELEASED_STORE_DRIVER = textwrap.dedent(
    """
    import json
    import sys

    sys.path.insert(0, sys.argv[1])
    import server
    from server.cadlink.store import CadLinkStore

    store = CadLinkStore(sys.argv[2])
    store.initialize()
    design = store.get_design(sys.argv[3]) if len(sys.argv) > 3 else None
    saved = None
    if len(sys.argv) > 4:
        # The rolled-back release goes on working: it saves a design of its own
        # into the registry this build wrote.
        saved = store.save(
            requested=None,
            design_hash="sha256:" + "5" * 64,
            filename="saved-after-rollback.cfg",
            snapshot_builder=lambda identity: f"DesignId={identity.design_id}",
            saved_at="2026-09-21T10:00:00Z",
        )["identity"].design_id
    store.close()
    print(json.dumps({
        "server": server.__file__,
        "snapshot": design["snapshot_text"] if design else None,
        "saved": saved,
    }))
    """
)


def _released_server(tag: str, destination: Path) -> Path:
    try:
        archive = subprocess.run(  # noqa: S603 - fixed program and arguments
            [  # noqa: S607 - git from PATH, as every repository script finds it
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "archive",
                "--format=tar",
                tag,
                "server",
                "shared",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        skip_or_fail_missing_tag(
            f"git could not read {tag} here ({exc}); the frozen reader is tested"
        )
    if archive.returncode != 0:
        skip_or_fail_missing_tag(
            f"{tag} is not reachable from this checkout (a CI checkout has one commit and "
            "no tags); the frozen reader is still tested"
        )
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as released:
        released.extractall(destination, filter="data")
    return destination


def _run_released(tree: Path, driver: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
        [sys.executable, str(driver), str(tree), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=driver.parent,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    # The released code ran, not this checkout's.
    assert Path(str(result["server"])).resolve().is_relative_to(tree.resolve())
    return result


#: The tables this build adds to a registry a release reads. No release reads
#: or writes them, so a rollback must leave every row as it was.
M1_TABLES = (
    "cad_operations",
    "cad_preparations",
    "cad_setup_revisions",
    "cad_project_setups",
    "cad_settings",
    "cad_frame_confirmations",
)


def _populate(store: CadLinkStore, design_id: str) -> None:
    """Rows in every table this build added, through the store's own writers.

    A solve taken all the way to its job (snapshot, preparation, approval,
    bound request, outcome); a solve waiting for the user; a Fusion-bound
    request claimed over the live protocol (``claim_json``); a legacy outcome;
    a setup revision, the project setup that names it, a setting and a frame
    confirmation.
    """

    lineage_id = store.get_design(design_id)["lineage_id"]
    revision = store.create_setup_revision('{"setup": 1}', "sha256:" + "6" * 64)["revision_id"]
    store.record_project_setup(lineage_id, "sha256:" + "7" * 64, revision)
    store.set_setting("solverSelection", {"engine": "metal"})
    store.record_frame_confirmation(
        "wgl_frame", {"anchor": "instance-1", "requirement": "unlinked-v1"}, "+z"
    )

    _accept_solve(store, "cmd-solved")
    store.record_snapshot("cmd-solved", {"manifest_sha256": "sha256:" + "4" * 64})
    assert store.note_snapshot_unreadable("cmd-solved", "2026-09-21T09:00:00+00:00")
    generation = store.claim("cmd-solved", 0)
    assert generation == 1
    assert store.record_preparation(
        "cmd-solved", generation, preparation_id="wgi_prepared", snapshot_sha256="sha256:" + "4" * 64,
        setup_revision_id=revision, ingest_id="wgi_prepared", report_sha256="sha256:" + "8" * 64,
        blocking_finding_ids=["finding-1"], meshing_semantics="semantics-1",
    ) is not None
    assert store.add_approvals("cmd-solved", "wgi_prepared", ["finding-1"], generation=generation)
    assert store.bind_request(
        "cmd-solved", generation, setup_revision_id=revision, request_json='{"request": 1}'
    ) is not None
    assert store.record_outcome(
        "cmd-solved", generation, ACCEPTED, job_id="job-1", outcome={"message": "submitted"}
    ) is not None

    _accept_solve(store, "cmd-waiting")
    waiting = store.claim("cmd-waiting", 0)
    assert store.record_outcome(
        "cmd-waiting", waiting, NEEDS_USER_INPUT, reason="setup_required",
        outcome={"message": "Choose the solve settings"},
    ) is not None

    target, inputs = normalize_request(
        REQUEST_RETURN,
        {
            "document_id": "urn:doc", "design_id": design_id, "instance_id": "instance-1",
            "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:" + "9" * 64},
        },
        {},
    )
    store.accept_operation(
        "fusion-request", REQUEST_RETURN, request_digest(REQUEST_RETURN, target, inputs), target, inputs
    )
    assert store.claim_fusion_request(
        "fusion-request", 0,
        {"installationId": "inst-1", "sessionId": "session-1", "claimId": "claim-1"},
    ) == 1

    store.record_legacy_outcome("cmd-legacy", kind=PREPARE_AND_SOLVE, state=ACCEPTED, job_id="job-0")


def _rows(db_path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Every row of every table, every column, read from the file."""

    with closing(sqlite3.connect(db_path)) as conn:
        tables = sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        return {
            table: sorted(
                conn.execute(f"SELECT * FROM {table}").fetchall(),  # noqa: S608 - names from sqlite_master
                key=repr,
            )
            for table in tables
        }


def _columns_left_empty(db_path: Path, table: str) -> list[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        return [
            column
            for column in columns
            if conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608 - from PRAGMA
            ).fetchone()[0] == 0
        ]


@pytest.mark.parametrize("tag", CADLINK_ROLLBACK_TAGS)
def test_a_release_reopens_a_registry_this_build_filled_and_nothing_is_lost(
    tmp_path: Path, tag: str
) -> None:
    tree = _released_server(tag, tmp_path / "released")
    driver = tmp_path / "released_store_driver.py"
    driver.write_text(_RELEASED_STORE_DRIVER, encoding="utf-8")
    db_path = tmp_path / "data" / "db" / "cadlink.db"
    db_path.parent.mkdir(parents=True)

    # The released build creates the registry; this build then upgrades it and
    # fills every table it added.
    _run_released(tree, driver, str(db_path))
    assert _user_version(db_path) == 11
    store = CadLinkStore(db_path)
    try:
        design_id, snapshot = _save_design(store)
        _populate(store, design_id)
    finally:
        store.close()
    before = _rows(db_path)
    for table in M1_TABLES:
        assert before[table], f"{table} has no rows to lose"
    # Every column of the operation store holds something, claim columns included.
    assert _columns_left_empty(db_path, "cad_operations") == []
    with closing(sqlite3.connect(db_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(cad_operations)")}
    assert {name for name, _declaration in _OPERATION_COLUMNS} <= columns
    for table in M1_TABLES[1:]:
        assert _columns_left_empty(db_path, table) == [], table

    # The rollback: the released build opens it, reads this build's design,
    # and saves one of its own.
    rolled_back = _run_released(tree, driver, str(db_path), design_id, "save")
    assert rolled_back["snapshot"] == snapshot
    assert _user_version(db_path) == 11
    during = _rows(db_path)
    for table in M1_TABLES:
        assert during[table] == before[table], f"{tag} changed {table}"

    # The update again: every row this build wrote is as it was, and the
    # released build's own design is there too.
    store = CadLinkStore(db_path)
    try:
        store.initialize()
        assert store.get_design(design_id)["snapshot_text"] == snapshot
        assert store.get_design(str(rolled_back["saved"])) is not None
        solved = store.get_operation("cmd-solved")
        assert (solved["state"], solved["job_id"], solved["preparation_id"]) == (
            "accepted", "job-1", "wgi_prepared",
        )
        assert json.loads(store.get_operation("fusion-request")["claim_json"])["claimId"] == "claim-1"
    finally:
        store.close()
    after = _rows(db_path)
    for table in M1_TABLES:
        assert after[table] == before[table], f"rolling forward from {tag} changed {table}"
    assert after["designs"] == during["designs"]
    assert len(after["designs"]) == len(before["designs"]) + 1
    assert _user_version(db_path) == 11
