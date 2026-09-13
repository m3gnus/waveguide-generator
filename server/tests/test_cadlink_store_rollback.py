"""A release that rolls back must still open the CAD Link registry.

An update's rollback, and a later Return to Stable, replace the application
but leave ``cadlink.db`` in the data directory. Every release refuses a
``PRAGMA user_version`` above the highest it knows, so a build that raised it
would leave the release it rolled back to with every CAD Link operation
failing. The operation store (schema 12) only adds a table, so the file keeps
the format v0.3.2 reads (docs/reference/UPDATE-TRANSACTION-CONTRACT.md §6).
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

from server.cadlink.operations import PREPARE_AND_SOLVE, request_digest
from server.cadlink.store import CadLinkStore


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
    store.close()
    print(json.dumps({
        "server": server.__file__,
        "snapshot": design["snapshot_text"] if design else None,
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
        pytest.skip(f"git could not read {tag} here ({exc}); the frozen reader is tested")
    if archive.returncode != 0:
        pytest.skip(
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


def test_v032_reopens_a_registry_this_build_upgraded_and_nothing_is_lost(
    tmp_path: Path,
) -> None:
    tree = _released_server("v0.3.2", tmp_path / "released")
    driver = tmp_path / "released_store_driver.py"
    driver.write_text(_RELEASED_STORE_DRIVER, encoding="utf-8")
    db_path = tmp_path / "data" / "db" / "cadlink.db"
    db_path.parent.mkdir(parents=True)

    # The released build creates the registry; this build then upgrades it.
    _run_released(tree, driver, str(db_path))
    assert _user_version(db_path) == 11
    store = CadLinkStore(db_path)
    try:
        design_id, snapshot = _save_design(store)
        _accept_solve(store, "cmd-before-rollback")
    finally:
        store.close()

    # The rollback: the released build opens it and reads this build's work.
    rolled_back = _run_released(tree, driver, str(db_path), design_id)
    assert rolled_back["snapshot"] == snapshot

    # The update again: nothing the operation store held was lost.
    store = CadLinkStore(db_path)
    try:
        store.initialize()
        assert store.get_design(design_id)["snapshot_text"] == snapshot
        assert store.get_operation("cmd-before-rollback") is not None
    finally:
        store.close()
    assert _user_version(db_path) == 11
