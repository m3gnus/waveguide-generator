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
import subprocess
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


def _held(path: Path) -> PermissionError:
    error = PermissionError(13, "The process cannot access the file because it is being used", str(path))
    error.winerror = 32  # ERROR_SHARING_VIOLATION, as Windows raises it
    return error


def _restored_by_hand(db: Path, snapshot: Path) -> None:
    """The manual recovery procedure: the snapshot copied back over the live DB."""

    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    shutil.copy2(snapshot, db)


def _work_in_the_older_release(db: Path) -> None:
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE simulation_jobs SET label = 'older release work' WHERE id = 'c'")
        conn.commit()


@pytest.mark.parametrize("held_for", ["transient", "whole-start"])
def test_a_held_previous_snapshot_never_stops_a_second_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held_for: str
) -> None:
    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    store = JobStore(db)
    store.initialize()
    store.close()
    snapshot = store.rollback_snapshot_path
    _restored_by_hand(db, snapshot)
    # Work done in the older release after the manual restore.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE simulation_jobs SET label = 'after restore' WHERE id = 'a'")
        conn.commit()
    second_rows = _snapshot(db)
    first_rows = _snapshot(snapshot)
    first_snapshot = snapshot.read_bytes()

    real_unlink = Path.unlink
    refusals = []

    def unlink(self, missing_ok=False):
        # A handle without delete sharing: the copy succeeds, the removal not.
        if self == snapshot and (held_for == "whole-start" or not refusals):
            refusals.append(self)
            raise _held(snapshot)
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(store_module, "_HELD_FILE_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(store_module, "_HELD_FILE_RETRY_INTERVAL", 0.01)
    store = JobStore(db)
    store.initialize()
    store.close()

    assert _user_version(db) == 6
    held_copies = list(db.parent.glob(snapshot.name + ".held-*"))
    if held_for == "transient":
        # Retried like the bridge's renames: rotated, then published normally.
        assert len(refusals) == 1 and not held_copies
        assert _snapshot(Path(str(snapshot) + ".1")) == first_rows
        assert _snapshot(snapshot) == second_rows
    else:
        # Kept where it is, unchanged, with a complete copy at .bak.1; this
        # upgrade's snapshot is written beside it.
        assert len(refusals) > 1 and snapshot.read_bytes() == first_snapshot
        assert _snapshot(Path(str(snapshot) + ".1")) == first_rows
        assert len(held_copies) == 1 and _snapshot(held_copies[0]) == second_rows
    assert not list(db.parent.glob(".jobs-rollback-*"))


def test_a_held_orphan_from_an_earlier_snapshot_is_logged_and_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    orphan = db.parent / ".jobs-rollback-orphan"
    orphan.write_bytes(b"left by a crashed snapshot")
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self == orphan:
            raise _held(self)
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    store = JobStore(db)
    store.initialize()
    store.close()
    assert _user_version(db) == 6 and orphan.exists()
    assert any(str(orphan) in record.getMessage() for record in caplog.records)


class _FakeClock:
    """``store_module.time`` stand-in: sleeping advances the clock, nothing waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds


def _second_upgrade_with_invalid_snapshot(tmp_path: Path) -> tuple[Path, Path, dict]:
    """A schema-5 DB again, beside an unreadable snapshot with every sidecar."""

    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    snapshot = db.with_name(db.name + ".pre-schema-6.bak")
    snapshot.write_bytes(b"not a database")
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(snapshot) + suffix).write_bytes(b"stale " + suffix.encode())
    return db, snapshot, _snapshot(db)


def _unreadable_snapshot(monkeypatch: pytest.MonkeyPatch, snapshot: Path):
    """Held sidecars are as untouchable for SQLite as for a rename: the
    validity probe fails without consuming them. Returns the real connect."""

    real_connect = sqlite3.connect

    def connect(target, *args, **kwargs):
        if str(target).split("?")[0].endswith(snapshot.name):
            raise sqlite3.DatabaseError("file is not a database")
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", connect)
    return real_connect


def test_a_held_stale_sidecar_never_sits_beside_a_new_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, snapshot, rows = _second_upgrade_with_invalid_snapshot(tmp_path)
    wal = Path(str(snapshot) + "-wal")
    real_replace = store_module.os.replace

    def replace(source, destination):
        if Path(source) == wal:
            raise _held(wal)
        return real_replace(source, destination)

    real_connect = _unreadable_snapshot(monkeypatch, snapshot)
    monkeypatch.setattr(store_module.os, "replace", replace)
    monkeypatch.setattr(store_module, "time", _FakeClock())
    store = JobStore(db)
    store.initialize()
    store.close()
    monkeypatch.setattr(store_module.sqlite3, "connect", real_connect)

    assert _user_version(db) == 6
    assert wal.read_bytes() == b"stale -wal"
    # The invalid main moved away, but the new snapshot is not published at
    # the live name while the old WAL is still there.
    assert not snapshot.exists()
    held = list(db.parent.glob(snapshot.name + ".held-*"))
    assert len(held) == 1 and _snapshot(held[0]) == rows


@pytest.mark.parametrize("fallback", ["written", "also-held"])
def test_a_failed_publication_after_rotation_reports_where_each_snapshot_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, fallback: str
) -> None:
    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    store = JobStore(db)
    store.initialize()
    store.close()
    snapshot = store.rollback_snapshot_path
    _restored_by_hand(db, snapshot)
    _work_in_the_older_release(db)
    first_rows = _snapshot(snapshot)
    real_replace = store_module.os.replace
    rotated = Path(str(snapshot) + ".1")

    def replace(source, destination):
        source = Path(source)
        if source.name.startswith(".jobs-rollback-") and Path(destination) != rotated and (
            Path(destination) == snapshot or fallback == "also-held"
        ):
            raise _held(snapshot)
        return real_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", replace)
    monkeypatch.setattr(store_module, "time", _FakeClock())
    store = JobStore(db)
    store.initialize()  # never fails startup
    store.close()

    assert _user_version(db) == 6
    assert _snapshot(rotated) == first_rows and not snapshot.exists()
    held = list(db.parent.glob(snapshot.name + ".held-*"))
    message = " ".join(record.getMessage() for record in caplog.records)
    assert f"previous snapshot is at {rotated}" in message
    if fallback == "written":
        assert len(held) == 1
    else:
        assert not held and "no rollback snapshot" in message
    assert not list(db.parent.glob(".jobs-rollback-*"))


def test_every_held_file_shares_one_retry_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, snapshot, _rows = _second_upgrade_with_invalid_snapshot(tmp_path)
    real_replace = store_module.os.replace

    def replace(source, destination):
        if Path(source).name.startswith(snapshot.name):
            raise _held(Path(source))
        return real_replace(source, destination)

    clock = _FakeClock()
    real_connect = _unreadable_snapshot(monkeypatch, snapshot)
    monkeypatch.setattr(store_module.os, "replace", replace)
    monkeypatch.setattr(store_module, "time", clock)
    store = JobStore(db)
    store.initialize()
    store.close()
    monkeypatch.setattr(store_module.sqlite3, "connect", real_connect)
    assert all(Path(str(snapshot) + suffix).exists() for suffix in ("", "-wal", "-shm", "-journal"))
    # Four held files (main and three sidecars) used to wait 20 s each.
    assert _user_version(db) == 6
    assert clock.slept <= store_module._HELD_FILE_RETRY_SECONDS + store_module._HELD_FILE_RETRY_INTERVAL


def _wal_mode_previous_snapshot(tmp_path: Path) -> tuple[Path, Path, dict]:
    """A first upgrade's snapshot, turned WAL-mode as earlier builds wrote it, then a manual restore."""

    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    store = JobStore(db)
    store.initialize()
    store.close()
    snapshot = store.rollback_snapshot_path
    with closing(sqlite3.connect(snapshot)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    _restored_by_hand(db, snapshot)
    return db, snapshot, _snapshot(db)


class _HoldingClock(_FakeClock):
    """Lets the holder go after ``release_after`` seconds of retrying, if at all."""

    def __init__(self, holder: sqlite3.Connection, release_after: float | None) -> None:
        super().__init__()
        self.holder, self.release_after = holder, release_after

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        if self.release_after is not None and self.slept >= self.release_after and self.holder is not None:
            self.holder.rollback()
            self.holder.close()
            self.holder = None


@pytest.mark.parametrize("released", [True, False], ids=["released-within-budget", "held-whole-start"])
def test_a_snapshot_held_by_another_connection_is_held_not_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, released: bool
) -> None:
    db, snapshot, rows = _wal_mode_previous_snapshot(tmp_path)
    original = snapshot.read_bytes()
    holder = sqlite3.connect(snapshot, timeout=0)
    holder.execute("PRAGMA locking_mode=EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    clock = _HoldingClock(holder, 1.0 if released else None)
    monkeypatch.setattr(store_module, "time", clock)
    try:
        store = JobStore(db)
        store.initialize()
        store.close()
    finally:
        if clock.holder is not None:
            clock.holder.rollback()
            clock.holder.close()

    assert _user_version(db) == 6
    # Never quarantined as invalid, and its main file never rewritten.
    assert not list(db.parent.glob(snapshot.name + ".invalid-*"))
    held = list(db.parent.glob(snapshot.name + ".held-*"))
    if released:
        assert _snapshot(Path(str(snapshot) + ".1")) == rows
        assert not held and _snapshot(snapshot) == rows
    else:
        assert snapshot.read_bytes() == original and not Path(str(snapshot) + ".1").exists()
        assert len(held) == 1 and _snapshot(held[0]) == rows


def test_a_wal_mode_previous_snapshot_rotates_as_one_standalone_copy(tmp_path: Path) -> None:
    db, snapshot, rows = _wal_mode_previous_snapshot(tmp_path)
    store = JobStore(db)
    store.initialize()
    store.close()
    rotated = Path(str(snapshot) + ".1")
    assert _snapshot(rotated) == rows and _snapshot(snapshot) == rows
    for path in (snapshot, rotated):
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert not any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


REPOSITORY = Path(__file__).resolve().parents[2]
WAL_ONLY_LABEL = "committed only in the wal"

_CRASHING_UPGRADE = textwrap.dedent(
    """\
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[3])
    from server.jobs import store as m
    from server.jobs.store import JobStore

    db, step = Path(sys.argv[1]), sys.argv[2]
    target = db.with_name(db.name + ".pre-schema-6.bak")
    rotated = Path(str(target) + ".1")
    real_replace, real_unlink = m._replace_unless_held, m._unlink_unless_held

    def replace(source, destination, deadline):
        if step == "before-copy-published" and Path(destination) == rotated:
            os._exit(17)
        moved = real_replace(source, destination, deadline)
        if (step, Path(destination)) in {("copy-published", rotated), ("published", target)}:
            os._exit(17)
        return moved

    def unlink(path, deadline):
        removed = real_unlink(path, deadline)
        if (step, Path(path)) in {("main-removed", target), ("wal-removed", Path(str(target) + "-wal"))}:
            os._exit(17)
        return removed

    m._replace_unless_held, m._unlink_unless_held = replace, unlink
    JobStore(db).initialize()
    os._exit(0)
    """
)


def _label_in_set(main: Path, scratch: Path) -> str | None:
    """Job a's label as SQLite reads the set, on a copy so nothing is touched."""

    if not main.exists():
        return None
    scratch.mkdir()
    for suffix in ("", "-wal", "-shm", "-journal"):
        if Path(str(main) + suffix).exists():
            shutil.copyfile(Path(str(main) + suffix), scratch / ("copy.db" + suffix))
    with closing(sqlite3.connect(scratch / "copy.db")) as conn:
        return conn.execute("SELECT label FROM simulation_jobs WHERE id = 'a'").fetchone()[0]


def _upgrade_child(db: Path, step: str) -> int:
    driver = db.parent.parent / "crashing_upgrade.py"
    driver.write_text(_CRASHING_UPGRADE, encoding="utf-8")
    return subprocess.run([sys.executable, str(driver), str(db), step, str(REPOSITORY)],
                          cwd=REPOSITORY, stdin=subprocess.DEVNULL, capture_output=True, timeout=120).returncode


@pytest.mark.parametrize("step", ["before-copy-published", "copy-published", "main-removed", "wal-removed", "published"])
def test_a_crash_at_any_rotation_step_loses_no_committed_snapshot_row(tmp_path: Path, step: str) -> None:
    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    store = JobStore(db)
    store.initialize()
    store.close()
    snapshot = store.rollback_snapshot_path
    _restored_by_hand(db, snapshot)
    _work_in_the_older_release(db)
    # An earlier build's WAL-mode snapshot whose last commit is only in its WAL.
    subprocess.run([sys.executable, "-c", textwrap.dedent(f"""\
        import os, sqlite3, sys
        c = sqlite3.connect(sys.argv[1])
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA wal_autocheckpoint=0")
        c.execute("UPDATE simulation_jobs SET label = '{WAL_ONLY_LABEL}' WHERE id = 'a'")
        c.commit()
        os._exit(0)
        """), str(snapshot)], check=True, stdin=subprocess.DEVNULL)
    assert Path(str(snapshot) + "-wal").stat().st_size > 0
    main_only = tmp_path / "main-only.db"
    shutil.copyfile(snapshot, main_only)
    with closing(sqlite3.connect(main_only)) as conn:
        assert conn.execute("SELECT label FROM simulation_jobs WHERE id = 'a'").fetchone()[0] != WAL_ONLY_LABEL
    live_rows = _snapshot(db)
    rotated = Path(str(snapshot) + ".1")

    assert _upgrade_child(db, step) == 17, "the crash point was not reached"
    found = {_label_in_set(snapshot, tmp_path / "crash-bak"), _label_in_set(rotated, tmp_path / "crash-bak1")}
    assert WAL_ONLY_LABEL in found, found
    assert _user_version(db) == 5  # the migration never committed

    assert _upgrade_child(db, "none") == 0
    assert _user_version(db) == 6
    assert _label_in_set(rotated, tmp_path / "after-bak1") == WAL_ONLY_LABEL
    assert _snapshot(snapshot) == live_rows
    assert not any(Path(str(snapshot) + suffix).exists() for suffix in ("-wal", "-journal"))
    assert not any(Path(str(rotated) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def test_sidecars_left_without_their_main_file_are_set_aside(tmp_path: Path) -> None:
    db = tmp_path / "data" / "db" / "simulations.db"
    _old_shaped_database(db)
    _fill_old_database(db)
    snapshot = db.with_name(db.name + ".pre-schema-6.bak")
    Path(str(snapshot) + "-wal").write_bytes(b"orphaned wal")
    before = _snapshot(db)
    store = JobStore(db)
    store.initialize()
    store.close()
    orphans = list(db.parent.glob(snapshot.name + ".orphan-*-wal"))
    assert len(orphans) == 1 and orphans[0].read_bytes() == b"orphaned wal"
    assert not Path(str(snapshot) + "-wal").exists()
    # The new snapshot never pairs with the orphaned WAL.
    assert _snapshot(snapshot) == before
