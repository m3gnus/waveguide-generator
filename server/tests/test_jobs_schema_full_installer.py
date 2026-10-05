"""Jobs schema 6 across the full-installer update path (0.3.4 and later).

The layer bridge (``launchers/apply_update.py``) owns an automatic jobs restore
for its own transaction; ``test_update_transaction.py`` covers it. A full
installer sends no layer transaction and never touches ``db/``. Its two
recovery cases differ:

* the install fails before the new app starts: the native rollback keeps the
  previous root and the jobs database is exactly what the old app left;
* the install committed and the new app migrated: there is no automatic
  rollback after commit, so an older app returns only by reinstallation. It
  refuses schema 6 clearly and changes nothing, and the snapshot restores by
  the manual procedure. No surviving layer journal adopts that snapshot.
"""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import textwrap

import pytest

from launchers import apply_update as apply_update_module
from launchers.apply_update import (
    begin_update_transaction,
    read_journal,
    set_journal_state,
    swap_staged_layers,
)
from server.jobs import store as store_module
from server.jobs.store import JobStore

from test_cadlink_store_rollback import _released_server, _run_released
from test_job_status_preparing import (
    _RELEASED_JOB_STORE_DRIVER,
    _fill_old_database,
    _old_shaped_database,
    _preparing,
    _snapshot,
)

#: The release a full-installer rollback or reinstall returns to.
INSTALLER_PATH_RELEASE = "v0.3.4"

_REFUSAL_DRIVER = textwrap.dedent(
    """\
    import json
    import sys
    sys.path.insert(0, sys.argv[1])
    import server
    from server.jobs.store import JobStore
    store = JobStore(sys.argv[2])
    try:
        store.initialize()
    except RuntimeError as exc:
        print(json.dumps({"refusal": str(exc), "server": server.__file__}))
    else:
        raise AssertionError("The older release opened schema 6")
    finally:
        store.close()
    """
)


def _db_files(db: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(db.parent.iterdir()) if path.is_file()}


def _user_version(db: Path) -> int:
    with closing(sqlite3.connect(db)) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _released_reads(tmp_path: Path, db: Path) -> dict:
    tree = _released_server(INSTALLER_PATH_RELEASE, tmp_path / "release")
    driver = tmp_path / "released-driver.py"
    driver.write_text(_RELEASED_JOB_STORE_DRIVER, encoding="utf-8")
    return _run_released(tree, driver, str(db))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer helper; Windows setup is a native gate")
def test_installer_failure_before_the_new_app_starts_leaves_the_jobs_database_untouched(tmp_path: Path) -> None:
    from scripts.tests.test_installer_helper import helper, native_layout, set_native_entry
    from scripts.tests.test_installer_journal import sandbox
    from scripts.tests.test_linux_bundle_install_update import installed_dir, late_failure_env, run

    env = sandbox(tmp_path)
    assert run(native_layout(tmp_path, env, "old"), env, "--no-launch").returncode == 0
    root = installed_dir(env)
    # The helper's data folder is <data>/update-install; the jobs DB is beside it.
    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    before_rows = _snapshot(db)
    before_files = _db_files(db)
    new_started = tmp_path / "new-app-started"
    payload = native_layout(tmp_path, env, "new")
    set_native_entry(payload, new_started)

    proc, family, _recovery, work = helper(root, payload, late_failure_env(tmp_path, env), tmp_path)
    try:
        assert proc.wait(timeout=60) == 0
        outcome = json.loads((work / "outcome.json").read_text())
        assert outcome["result"] == "failed" and outcome["previousKept"] is True
    finally:
        family.stop()
        if proc.poll() is None:
            proc.wait(timeout=5)

    assert (root / "version.txt").read_text() == "old"
    assert not new_started.exists(), "the new app must not have started"
    # Nothing on this path snapshots, migrates or journals the jobs database.
    assert _db_files(db) == before_files
    assert _user_version(db) == 5 and _snapshot(db) == before_rows
    assert not list(db.parent.glob("*.pre-schema-6.bak*"))
    assert not list((tmp_path / "data").glob("update-transaction-*"))
    # The kept previous release opens it unchanged.
    assert _released_reads(tmp_path, db)["ids"] == ["a", "b", "c", "d"]
    assert _snapshot(db) == before_rows


def _installed_by_full_installer(tmp_path: Path, *, stale_layer_journal: bool) -> tuple[Path, Path]:
    """An installation the full installer replaced, optionally with a surviving layer journal."""

    resources = tmp_path / "WaveguideGenerator"
    data_dir = tmp_path / "data"
    staged = data_dir / "updates" / "0.3.4" / "staged"
    for layer, version in (
        (resources / "app", "0.3.2"),
        (resources / "runtime", "0.3.2"),
        (staged / "app", "0.3.4"),
        (staged / "runtime", "0.3.4"),
    ):
        layer.mkdir(parents=True)
        name = "APP-MANIFEST.json" if layer.name == "app" else "RUNTIME-MANIFEST.json"
        (layer / name).write_text(json.dumps({"schemaVersion": 1, "version": version,
                                              "commit": "c" + version, "runtimeId": "r" + version}))
    (data_dir / "logs").mkdir(parents=True)
    if stale_layer_journal:
        # A bridge-path 0.3.2 -> 0.3.4 transaction whose record outlived it.
        begin_update_transaction(data_dir=data_dir, bundle=resources, resources=resources,
                                 layers=[(resources / "app", staged / "app"),
                                         (resources / "runtime", staged / "runtime")],
                                 platform_name="linux")
        swap_staged_layers(resources, staged / "app", staged / "runtime")
        set_journal_state(data_dir, resources, "installed")
    # The full installer replaces the whole root with 0.3.5 in place.
    for layer in ("app", "runtime"):
        target = resources / layer
        name = "APP-MANIFEST.json" if layer == "app" else "RUNTIME-MANIFEST.json"
        (target / name).write_text(json.dumps({"schemaVersion": 1, "version": "0.3.5",
                                               "commit": "c0.3.5", "runtimeId": "r0.3.5"}))
        shutil.rmtree(resources / (layer + ".previous"), ignore_errors=True)
    return resources, data_dir


@pytest.mark.parametrize("stale_layer_journal", [False, True], ids=["no-journal", "stale-journal"])
def test_after_the_new_app_migrated_an_older_app_refuses_and_the_snapshot_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale_layer_journal: bool
) -> None:
    resources, data_dir = _installed_by_full_installer(tmp_path, stale_layer_journal=stale_layer_journal)
    db = data_dir / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    before_rows = _snapshot(db)
    journal_before = read_journal(data_dir, resources)
    monkeypatch.setattr(store_module, "app_root", lambda: resources / "app")

    # The new app's first start migrates, snapshotting first, then accepts work.
    store = JobStore(db)
    store.initialize()
    snapshot = store.rollback_snapshot_path
    store.create_job(_preparing())
    store.checkpoint()
    store.close()
    assert _user_version(db) == 6
    assert _snapshot(snapshot) == before_rows
    # No layer transaction installed this build, so none owns its snapshot.
    assert read_journal(data_dir, resources) == journal_before
    apply_update_module.restore_jobs_upgrade_snapshot(data_dir, resources)
    assert _user_version(db) == 6

    # Reinstalling the older release is the only way back on this path. It
    # refuses clearly, naming the newer install, and writes nothing.
    upgraded = _db_files(db)
    tree = _released_server(INSTALLER_PATH_RELEASE, tmp_path / "release")
    refusal = tmp_path / "refusal.py"
    refusal.write_text(_REFUSAL_DRIVER, encoding="utf-8")
    message = _run_released(tree, refusal, str(db))["refusal"]
    assert "created by a newer version" in message and "supports schemas up to 5" in message
    assert "last opened by" in message
    assert _db_files(db) == upgraded

    # The manual procedure (SHUTDOWN-AND-RECOVERY.md) restores the pre-upgrade jobs.
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    shutil.copy2(snapshot, db)
    assert _released_reads(tmp_path, db)["ids"] == ["a", "b", "c", "d"]
    assert _snapshot(db) == before_rows


def test_the_layer_journal_still_records_a_snapshot_for_the_build_it_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources, data_dir = _installed_by_full_installer(tmp_path, stale_layer_journal=True)
    # The same journal, read by the build it did install, is the bridge case.
    (resources / "app" / "APP-MANIFEST.json").write_text(json.dumps(
        {"schemaVersion": 1, "version": "0.3.4", "commit": "c0.3.4", "runtimeId": "r0.3.4"}))
    db = data_dir / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    monkeypatch.setattr(store_module, "app_root", lambda: resources / "app")
    store = JobStore(db)
    store.initialize()
    store.close()
    journal = read_journal(data_dir, resources)
    assert journal["jobsUpgradeSnapshot"]["transaction"] == journal["transaction"]


def test_a_build_without_the_bridge_helper_still_snapshots_and_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    before_rows = _snapshot(db)
    # After B+2 the bridge files leave the app layer (UPDATER-PLAN.md section 5).
    monkeypatch.setitem(sys.modules, "launchers.apply_update", None)
    store = JobStore(db)
    store.initialize()
    store.close()
    assert _user_version(db) == 6
    assert _snapshot(store.rollback_snapshot_path) == before_rows
