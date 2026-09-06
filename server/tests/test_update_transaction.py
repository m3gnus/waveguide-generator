"""What an interrupted layer swap leaves behind, and what is made of it.

The updater renames four directories to install an update, and nothing makes
that sequence atomic. `swap_staged_layers` has always restored the layers when a
rename *fails*; it cannot restore them when the process is killed between two
that succeeded, because there is no handler left to run. These tests cover the
record that survives that -- the transaction journal -- and the reconciliation
that reads it on the next start.

**What is proven here and what is not.** The kill tests below use a real
subprocess and `SIGKILL`, so no handler, `atexit` hook or `finally` block of the
dying process contributes anything: recovery is performed by a process that
starts afterwards and reads only the journal and the file system. That is a real
proof of *fresh-process* recovery. It is **not** a proof of power-loss
durability: the file system is never actually cut off, so a state that only a
lost write could produce is constructed here rather than observed. Those states
are enumerated deliberately (`_publish_states`) precisely because they cannot be
reached by killing a process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap

import pytest

from launchers import apply_update as apply_update_module
from launchers.apply_update import (
    INTERRUPTED_PUBLICATION_STATE,
    INVALID_JOURNAL_STATE,
    ROLLING_BACK_STATE,
    UNREADABLE_JOURNAL_STATE,
    begin_update_transaction,
    commit_transaction,
    journal_path,
    journal_temp_path,
    main as run_updater_cli,
    plan_layer_swap,
    read_journal,
    recover_transaction,
    rollback_previous_layers,
    swap_staged_layers,
    write_journal,
)


REPOSITORY_ROOT = Path(apply_update_module.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _never_open_a_real_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file may put a window on anybody's screen.

    Recovery reports a refusal through the updater's visible channel, and on
    macOS that is a blocking `osascript` modal. One earlier run of this work
    reached it: pytest sat on the dialog until the 300 s faulthandler timeout
    killed the run, and the modal outlived the run on the developer's desktop.

    The stub is installed here rather than in a shared conftest on purpose --
    this file's own lifecycle, so it cannot depend on, or collide with, a
    fixture another session owns.
    """

    monkeypatch.setattr(
        apply_update_module,
        "_show_update_failure_dialog",
        lambda message, platform_name: None,
    )


# ---------------------------------------------------------------------------
# A Linux-shaped installation: the bundle root *is* the resources directory, so
# nothing here invokes codesign and every assertion is about renames alone.
# ---------------------------------------------------------------------------


def _write_manifest(layer: Path, runtime_id: str) -> None:
    name = "APP-MANIFEST.json" if layer.name.startswith("app") else "RUNTIME-MANIFEST.json"
    payload = {"schemaVersion": 1, "version": "9.9.9", "runtimeId": runtime_id}
    (layer / name).write_text(json.dumps(payload), encoding="utf-8")


def _installation(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Return (resources, data_dir, staged_app, staged_runtime)."""

    resources = tmp_path / "WaveguideGenerator"
    data_dir = tmp_path / "data"
    staged = data_dir / "updates" / "9.9.9" / "staged"
    for layer, generation in (
        (resources / "app", "old0"),
        (resources / "runtime", "old0"),
        (staged / "app", "new1"),
        (staged / "runtime", "new1"),
    ):
        layer.mkdir(parents=True)
        (layer / "marker.txt").write_text(generation, encoding="utf-8")
        _write_manifest(layer, generation)
    (data_dir / "logs").mkdir(parents=True)
    # User data lives beside the journal and must survive every path below.
    (data_dir / "workspace").mkdir()
    (data_dir / "workspace" / "design.wg2").write_text("a design", encoding="utf-8")
    return resources, data_dir, staged / "app", staged / "runtime"


def _begin(resources: Path, data_dir: Path, staged_app: Path, staged_runtime: Path) -> dict:
    planned = plan_layer_swap(resources, staged_app, staged_runtime)
    return begin_update_transaction(
        data_dir=data_dir,
        bundle=resources,
        resources=resources,
        layers=planned,
        platform_name="linux",
    )


def _recover(resources: Path, data_dir: Path, **kwargs: object):
    return recover_transaction(
        data_dir=data_dir,
        resources=resources,
        platform_name="linux",
        **kwargs,  # type: ignore[arg-type]
    )


def _generations(resources: Path) -> dict[str, str]:
    return {
        name: (resources / name / "marker.txt").read_text(encoding="utf-8")
        for name in ("app", "runtime")
        if (resources / name).is_dir()
    }


def _user_data_intact(data_dir: Path) -> bool:
    design = data_dir / "workspace" / "design.wg2"
    return design.is_file() and design.read_text(encoding="utf-8") == "a design"


# ---------------------------------------------------------------------------
# Publication: the states a lost write can leave, which no kill can produce
# ---------------------------------------------------------------------------


def test_a_journal_caught_mid_publication_is_never_read_as_decided(tmp_path: Path) -> None:
    """The temporary file is evidence, and the record beside it stops deciding.

    Publishing the journal is a rename, and a rename is durable only once the
    directory entry is flushed -- which Windows cannot do at all. So the state
    "temporary file present, older record still at the published name" is
    reachable, and in it the published record may be the transaction that just
    finished *or* the one it was about to replace. Nothing on the disk says
    which, so neither may conclude anything.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    apply_update_module.set_journal_state(data_dir, "installed")
    assert read_journal(data_dir)["state"] == "installed"

    journal_temp_path(data_dir).write_text("{}", encoding="utf-8")

    record = read_journal(data_dir)
    assert record["state"] == INTERRUPTED_PUBLICATION_STATE
    allowed, detail = commit_transaction(data_dir, resources=resources)
    assert allowed is False
    assert "unresolved" in detail


def test_a_temporary_journal_alone_still_says_a_transaction_was_in_flight(
    tmp_path: Path,
) -> None:
    """No published record at all is not the same as no transaction."""

    _resources, data_dir, _staged_app, _staged_runtime = _installation(tmp_path)
    journal_temp_path(data_dir).write_text('{"schema": 1}', encoding="utf-8")

    record = read_journal(data_dir)
    assert record is not None
    assert record["state"] == INTERRUPTED_PUBLICATION_STATE


def test_a_failed_directory_flush_is_reported_rather_than_assumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal write says which guarantee it got, and says so in the log."""

    _resources, data_dir, _staged_app, _staged_runtime = _installation(tmp_path)
    monkeypatch.setattr(apply_update_module, "sync_directory", lambda *_a, **_k: False)
    logged: list[str] = []

    durability = write_journal(
        data_dir,
        {"schema": 1, "operation": "update", "state": "planned", "layers": []},
        log=logged.append,
    )

    assert durability.published is True
    assert durability.name_synced is False
    assert any("could not be flushed" in entry for entry in logged)


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        pytest.param("{ not json", UNREADABLE_JOURNAL_STATE, id="truncated"),
        pytest.param('["a list"]', INVALID_JOURNAL_STATE, id="not-an-object"),
        pytest.param('{"schema": 99}', INVALID_JOURNAL_STATE, id="future-schema"),
        pytest.param(
            '{"schema": 1, "operation": "delete-everything", "state": "installed",'
            ' "transaction": "t", "resources": "/r", "bundle": "/b", "layers": []}',
            INVALID_JOURNAL_STATE,
            id="unknown-operation",
        ),
        pytest.param(
            '{"schema": 1, "operation": "update", "state": "installed",'
            ' "transaction": "t", "resources": "/r", "bundle": "/b",'
            ' "layers": [{"name": "../../etc"}]}',
            INVALID_JOURNAL_STATE,
            id="layer-escaping-the-bundle",
        ),
        pytest.param(
            '{"schema": 1, "operation": "update", "state": "installed",'
            ' "transaction": "t", "resources": "/r", "bundle": "/b",'
            ' "layers": [{"name": "app"}, {"name": "app"}]}',
            INVALID_JOURNAL_STATE,
            id="repeated-layer",
        ),
    ),
)
def test_a_journal_that_could_not_have_been_written_here_decides_nothing(
    tmp_path: Path, body: str, expected: str
) -> None:
    """Every malformed record collapses to "unresolved", never to "installed".

    The dangerous direction is one-way: a record that wrongly reads as decided
    permits deleting the only copy of the previous version. A record that
    wrongly reads as open costs a rollback that was not needed.
    """

    resources, data_dir, _staged_app, _staged_runtime = _installation(tmp_path)
    journal_path(data_dir).write_text(body, encoding="utf-8")

    record = read_journal(data_dir)
    assert record["state"] == expected
    allowed, _detail = commit_transaction(data_dir, resources=resources)
    assert allowed is False, "an untrusted record must never authorise reclaiming"


# ---------------------------------------------------------------------------
# Scope: one data directory, more than one installation
# ---------------------------------------------------------------------------


def test_another_installations_transaction_is_left_strictly_alone(tmp_path: Path) -> None:
    """A shared data directory is not a shared installation.

    A second copy of the app, or a `--data-dir` aimed at an existing one, puts
    two installations behind one journal. Acting on the other copy's record
    would move directories on the strength of a file that never described them.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    other = tmp_path / "OtherCopy"
    (other / "app").mkdir(parents=True)
    write_journal(
        data_dir,
        {
            "schema": 1,
            "operation": "update",
            "state": "swapping",
            "transaction": "elsewhere",
            "resources": str(other),
            "bundle": str(other),
            "layers": [{"name": "app", "staged": str(staged_app)}],
        },
    )
    before = _generations(resources)

    outcome = _recover(resources, data_dir)

    assert outcome.action == "none"
    assert "another installation" in outcome.detail
    assert _generations(resources) == before
    assert json.loads(journal_path(data_dir).read_text(encoding="utf-8"))["transaction"] == (
        "elsewhere"
    ), "the other installation's record must survive for its owner"
    # This copy started healthily, so its own rollback material is spent; the
    # foreign record neither blocks that nor is destroyed by it.
    allowed, detail = commit_transaction(data_dir, resources=resources)
    assert allowed is True
    assert "another installation" in detail
    assert journal_path(data_dir).is_file()


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_an_update_that_never_moved_a_layer_is_abandoned_not_rolled_back(
    tmp_path: Path,
) -> None:
    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)

    outcome = _recover(resources, data_dir)

    assert outcome.action == "none"
    assert _generations(resources) == {"app": "old0", "runtime": "old0"}
    assert staged_app.is_dir() and staged_runtime.is_dir()
    assert read_journal(data_dir)["state"] == "aborted"


def test_a_swap_that_finished_is_confirmed_and_keeps_its_rollback_material(
    tmp_path: Path,
) -> None:
    """Finishing the renames is not proof; only a healthy start is.

    So recovery confirms the transaction and stops there: `.previous` stays
    until something reaches a working interface and calls `commit_transaction`.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)

    outcome = _recover(resources, data_dir)

    assert outcome.action == "completed"
    assert _generations(resources) == {"app": "new1", "runtime": "new1"}
    assert (resources / "app.previous").is_dir()
    assert (resources / "runtime.previous").is_dir()
    assert read_journal(data_dir)["state"] == "installed"
    allowed, _detail = commit_transaction(data_dir, resources=resources)
    assert allowed is True


def _strip_manifests(resources: Path) -> None:
    """Make an installation look like one built before the manifests carried ids.

    `layers_disagree` is the check that normally spots a half-restored
    installation, and it works by comparing the two `runtimeId` fields. Removing
    them is how a test can reach the case it cannot answer.
    """

    for manifest in resources.glob("*/*-MANIFEST.json"):
        manifest.unlink()


def test_a_rollback_interrupted_after_one_layer_is_finished_not_read_as_an_update(
    tmp_path: Path,
) -> None:
    """The shape a half-finished restore leaves is the shape a finished update leaves.

    One layer with a `.previous` beside it and one without is what a swap looks
    like after its first pair of renames -- and also what a *restore* looks like
    after its first layer. The manifests tell them apart whenever both carry a
    `runtimeId`, which every bundle this project builds does. This test removes
    them, which is the only way to reach the case that comparison cannot answer,
    and the marker written before any restore begins is what covers it.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)
    apply_update_module.set_journal_state(data_dir, ROLLING_BACK_STATE)
    # A restore that got through the runtime and stopped.
    (resources / "runtime").rename(resources / "runtime.failed")
    (resources / "runtime.previous").rename(resources / "runtime")
    _strip_manifests(resources)

    outcome = _recover(resources, data_dir)

    assert outcome.action == "rolled-back"
    assert _generations(resources) == {"app": "old0", "runtime": "old0"}
    assert not (resources / "app.previous").exists()
    assert read_journal(data_dir)["state"] == "rolled-back"


def test_a_partial_rollback_is_caught_by_the_manifests_when_the_marker_is_lost(
    tmp_path: Path,
) -> None:
    """The marker is one write, and a write can be lost.

    With the manifests present -- the shipped case -- the mismatch between the
    app's required runtime and the installed one is enough on its own, so the
    two mechanisms cover each other rather than duplicating each other.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)
    (resources / "runtime").rename(resources / "runtime.failed")
    (resources / "runtime.previous").rename(resources / "runtime")
    # The marker never reached the disk: the record still describes the swap.
    apply_update_module.set_journal_state(data_dir, "swapped")

    outcome = _recover(resources, data_dir)

    assert outcome.action == "rolled-back"
    assert _generations(resources) == {"app": "old0", "runtime": "old0"}


def test_a_rollback_that_finished_without_recording_it_is_not_a_failed_recovery(
    tmp_path: Path,
) -> None:
    """Both layers back, nothing left to restore, and the record still open.

    Reachable whenever the terminal state was the write that was lost. The
    installation is correct and complete, so recovery must say so and let it
    start -- reporting "nothing was available to restore" as a failure would
    refuse to open a perfectly good application.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)
    assert rollback_previous_layers(resources) is True
    apply_update_module.set_journal_state(data_dir, ROLLING_BACK_STATE)

    outcome = _recover(resources, data_dir)

    assert outcome.action == "none", outcome.detail
    assert _generations(resources) == {"app": "old0", "runtime": "old0"}
    assert read_journal(data_dir)["state"] == "rolled-back"
    allowed, _detail = commit_transaction(data_dir, resources=resources)
    assert allowed is True


def test_recovery_touches_nothing_outside_the_installation_it_was_given(
    tmp_path: Path,
) -> None:
    """No path recovery mutates comes out of the journal.

    The record names a staged directory, and a record can be edited. Every
    destination reconciliation writes to is derived from the caller's
    `resources`; the recorded paths are only ever asked whether they exist.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    sentinel = tmp_path / "elsewhere"
    sentinel.mkdir()
    (sentinel / "precious.txt").write_text("do not touch", encoding="utf-8")
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)
    record = read_journal(data_dir)
    for entry in record["layers"]:
        entry["staged"] = str(sentinel)
    record["state"] = "swapping"
    write_journal(data_dir, record)

    _recover(resources, data_dir)

    assert (sentinel / "precious.txt").read_text(encoding="utf-8") == "do not touch"
    assert sorted(path.name for path in sentinel.iterdir()) == ["precious.txt"]
    assert _user_data_intact(data_dir)


def test_an_open_transaction_stops_a_healthy_start_reclaiming_the_previous_layers(
    tmp_path: Path,
) -> None:
    """`.previous` is the only copy of the version that worked."""

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)

    allowed, detail = commit_transaction(data_dir, resources=resources)

    assert allowed is False
    assert "unresolved" in detail
    assert (resources / "app.previous").is_dir()


# ---------------------------------------------------------------------------
# Real processes, really killed
# ---------------------------------------------------------------------------


_KILL_DRIVER = textwrap.dedent(
    '''
    """Perform part of a real swap, then die without running anything."""

    import os
    import signal
    import sys
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])

    from launchers.apply_update import (
        _rename,
        begin_update_transaction,
        plan_layer_swap,
        swap_staged_layers,
    )

    resources = Path(sys.argv[2])
    data_dir = Path(sys.argv[3])
    staged_app = Path(sys.argv[4])
    staged_runtime = Path(sys.argv[5])
    kill_after = int(sys.argv[6])

    planned = plan_layer_swap(resources, staged_app, staged_runtime)
    begin_update_transaction(
        data_dir=data_dir,
        bundle=resources,
        resources=resources,
        layers=planned,
        platform_name="linux",
    )

    performed = 0


    def renamer(source, destination):
        global performed
        _rename(source, destination)
        performed += 1
        if performed == kill_after:
            os.kill(os.getpid(), signal.SIGKILL)


    swap_staged_layers(
        resources, staged_app, staged_runtime, renamer=renamer, journal_dir=data_dir
    )
    '''
)

_ROLLBACK_KILL_DRIVER = textwrap.dedent(
    '''
    """Start a real restore and die part-way through it."""

    import os
    import signal
    import sys
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])

    from launchers.apply_update import _rename, rollback_previous_layers, set_journal_state

    resources = Path(sys.argv[2])
    data_dir = Path(sys.argv[3])
    kill_after = int(sys.argv[4])

    set_journal_state(data_dir, "rolling-back")
    performed = 0


    def renamer(source, destination):
        global performed
        _rename(source, destination)
        performed += 1
        if performed == kill_after:
            os.kill(os.getpid(), signal.SIGKILL)


    rollback_previous_layers(resources, renamer=renamer)
    '''
)

_RECOVERY_DRIVER = textwrap.dedent(
    '''
    """Recover in a process that shares nothing with the one that died."""

    import sys
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])

    from launchers.apply_update import recover_transaction

    outcome = recover_transaction(
        data_dir=Path(sys.argv[3]),
        resources=Path(sys.argv[2]),
        platform_name="linux",
    )
    print(outcome.action)
    '''
)


def _run_driver(source: str, tmp_path: Path, name: str, *arguments: str) -> subprocess.Popen:
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
        [sys.executable, str(script), str(REPOSITORY_ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    ("kill_after", "description"),
    (
        (1, "after the installed runtime moved aside"),
        (2, "after the staged runtime became the live one"),
        (3, "after the installed app moved aside"),
        (4, "after the staged app became the live one"),
    ),
)
def test_a_killed_updater_is_recovered_by_a_process_that_starts_afterwards(
    tmp_path: Path, kill_after: int, description: str
) -> None:
    """SIGKILL at each rename, then recovery in a genuinely new process.

    `SIGKILL` cannot be handled, so nothing in the dying process contributes:
    no `finally`, no `atexit`, no rollback handler. Everything the recovery
    knows, it reads from the journal and the directories -- which is the whole
    point, because a machine that lost power leaves exactly that much.

    This proves fresh-process recovery. It does **not** prove power-loss
    durability: the file system was never interrupted, so every write these
    processes issued did land.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)

    killed = _run_driver(
        _KILL_DRIVER,
        tmp_path,
        "kill_driver.py",
        str(resources),
        str(data_dir),
        str(staged_app),
        str(staged_runtime),
        str(kill_after),
    )
    assert killed.returncode == -signal.SIGKILL, (
        f"the updater was expected to be killed {description}: {killed.stderr}"
    )
    # Prove the kill left something to recover, so a passing assertion below
    # cannot come from an interruption that happened to change nothing.
    interrupted = _generations(resources)
    if kill_after == 4:
        assert interrupted == {"app": "new1", "runtime": "new1"}
    elif kill_after == 2:
        assert interrupted == {"app": "old0", "runtime": "new1"}, (
            "the kill was expected to leave two generations installed at once"
        )
    else:
        assert len(interrupted) == 1, "the kill was expected to leave a layer missing"

    recovered = _run_driver(
        _RECOVERY_DRIVER, tmp_path, "recovery_driver.py", str(resources), str(data_dir)
    )

    assert recovered.returncode == 0, recovered.stderr
    action = recovered.stdout.strip()
    generations = _generations(resources)
    assert set(generations) == {"app", "runtime"}, "both layers must exist after recovery"
    assert len(set(generations.values())) == 1, (
        f"recovery left a mixed installation: {generations}"
    )
    if kill_after == 4:
        # Every rename had completed; there was nothing left to undo.
        assert action == "completed"
        assert generations == {"app": "new1", "runtime": "new1"}
    else:
        assert action == "rolled-back"
        assert generations == {"app": "old0", "runtime": "old0"}
    assert _user_data_intact(data_dir)


@pytest.mark.parametrize("kill_after", (1, 2, 3))
def test_a_killed_rollback_is_finished_by_a_process_that_starts_afterwards(
    tmp_path: Path, kill_after: int
) -> None:
    """A restore is as interruptible as the swap it undoes."""

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)

    killed = _run_driver(
        _ROLLBACK_KILL_DRIVER,
        tmp_path,
        "rollback_driver.py",
        str(resources),
        str(data_dir),
        str(kill_after),
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    assert list(resources.glob("*.previous")) or len(_generations(resources)) == 1, (
        "the kill was expected to leave the restore unfinished"
    )

    recovered = _run_driver(
        _RECOVERY_DRIVER, tmp_path, "recovery_driver.py", str(resources), str(data_dir)
    )

    assert recovered.returncode == 0, recovered.stderr
    assert _generations(resources) == {"app": "old0", "runtime": "old0"}
    assert not list(resources.glob("*.previous"))
    assert _user_data_intact(data_dir)


def _native_installation(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    """Build the installation the way *this* platform's bundle is laid out.

    Returns (bundle, resources, data_dir, staged_app, staged_runtime). The
    external helper reads `sys.platform` in its own process, so a test that
    drives its real command line has to give it a shape that platform resolves
    -- and on macOS that includes a bundle complete enough for the ad-hoc
    reseal, which is then genuinely exercised rather than stubbed.
    """

    if sys.platform == "darwin":
        bundle = tmp_path / "Waveguide Generator.app"
        resources = bundle / "Contents" / "Resources"
        (bundle / "Contents" / "MacOS").mkdir(parents=True)
        executable = bundle / "Contents" / "MacOS" / "Waveguide Generator"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        (bundle / "Contents" / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            "<key>CFBundleExecutable</key><string>Waveguide Generator</string>"
            "<key>CFBundleIdentifier</key><string>test.waveguide.generator</string>"
            "<key>CFBundleName</key><string>Waveguide Generator</string>"
            "<key>CFBundlePackageType</key><string>APPL</string>"
            "</dict></plist>\n",
            encoding="utf-8",
        )
    else:
        bundle = tmp_path / "WaveguideGenerator"
        resources = bundle

    data_dir = tmp_path / "data"
    staged = data_dir / "updates" / "9.9.9" / "staged"
    for layer, generation in (
        (resources / "app", "old0"),
        (resources / "runtime", "old0"),
        (staged / "app", "new1"),
        (staged / "runtime", "new1"),
    ):
        layer.mkdir(parents=True)
        (layer / "marker.txt").write_text(generation, encoding="utf-8")
        _write_manifest(layer, generation)
    (data_dir / "logs").mkdir(parents=True)
    (data_dir / "workspace").mkdir()
    (data_dir / "workspace" / "design.wg2").write_text("a design", encoding="utf-8")
    return bundle, resources, data_dir, staged / "app", staged / "runtime"


def test_the_external_recovery_helper_runs_with_no_application_layer_at_all(
    tmp_path: Path,
) -> None:
    """The helper's one job is the case where the app layer is missing.

    It used to import `shared.safe_names` at module scope, so a copy started to
    restore a missing `app` died on the import of a module that lives inside it.
    The validator is still imported eagerly -- nothing may import lazily once
    renaming has begun -- but its absence is now recorded instead of fatal, and
    installing launcher files, the one thing that needs it, refuses without it.

    Driven through the real command line, on this platform's real layout, in an
    isolated interpreter that cannot import anything from this checkout. On
    macOS the reseal at the end is the real `codesign`.
    """

    helper_root = tmp_path / "rollback"
    helper_root.mkdir()
    helper = helper_root / "apply_update.py"
    helper.write_bytes(Path(apply_update_module.__file__).read_bytes())

    bundle, resources, data_dir, staged_app, staged_runtime = _native_installation(tmp_path)
    planned = plan_layer_swap(resources, staged_app, staged_runtime)
    begin_update_transaction(
        data_dir=data_dir,
        bundle=bundle,
        resources=resources,
        layers=planned,
        platform_name=sys.platform,
    )
    # Interrupted with the app layer moved aside and not yet replaced.
    (resources / "runtime").rename(resources / "runtime.previous")
    staged_runtime.rename(resources / "runtime")
    (resources / "app").rename(resources / "app.previous")

    # -I isolates the interpreter, so nothing of this checkout is importable:
    # exactly the situation the copied helper runs in.
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
        [
            sys.executable,
            "-I",
            str(helper),
            "--recover",
            "--bundle",
            str(bundle),
            "--data-dir",
            str(data_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": ""},
    )

    assert "ModuleNotFoundError" not in completed.stderr, (
        "the helper must start without the app layer it was sent to restore"
    )
    assert completed.returncode == 0, completed.stderr
    assert (resources / "app" / "marker.txt").read_text(encoding="utf-8") == "old0"
    assert (resources / "runtime" / "marker.txt").read_text(encoding="utf-8") == "old0"
    assert _user_data_intact(data_dir)


def test_the_recover_command_line_takes_no_staged_directories_and_no_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It runs when nothing is running, so there is no parent to wait for."""

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    monkeypatch.setattr(apply_update_module.sys, "platform", "linux")
    _begin(resources, data_dir, staged_app, staged_runtime)

    assert (
        run_updater_cli(
            ["--recover", "--bundle", str(resources), "--data-dir", str(data_dir)]
        )
        == 0
    )
    assert read_journal(data_dir)["state"] == "aborted"

    with pytest.raises(SystemExit):
        run_updater_cli(
            [
                "--recover",
                "--bundle",
                str(resources),
                "--data-dir",
                str(data_dir),
                "--staged-app-dir",
                str(staged_app),
            ]
        )
    with pytest.raises(SystemExit):
        run_updater_cli(["--rollback", "--bundle", str(resources), "--data-dir", str(data_dir)])


# ---------------------------------------------------------------------------
# Every start mode, not only the one that opens a window
# ---------------------------------------------------------------------------


def test_recovery_runs_before_the_mode_branch_for_browser_and_terminal_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery that shipped only ran on the `--window` path.

    It lived inside `DesktopWindow._wait_for_frontend`, so `--browser` and
    `--no-gui` skipped it -- and those are the modes somebody reaches for when
    the window will not open, which after an interrupted update is exactly when
    it will not. It now runs before the branch that chooses a mode, so all four
    reach it, and before any of them has imported the server package.
    """

    from launchers.statusapp import __main__ as entry_point
    from launchers.statusapp import updater as updater_module

    bundle, resources, data_dir, staged_app, staged_runtime = _native_installation(tmp_path)
    planned = plan_layer_swap(resources, staged_app, staged_runtime)
    begin_update_transaction(
        data_dir=data_dir,
        bundle=bundle,
        resources=resources,
        layers=planned,
        platform_name=sys.platform,
    )
    (resources / "runtime").rename(resources / "runtime.previous")
    staged_runtime.rename(resources / "runtime")

    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_APP_ROOT", str(resources / "app"))
    monkeypatch.setenv("WG2_DATA_DIR", str(data_dir))

    outcome = updater_module.recover_interrupted_bundle_update([])

    assert outcome is not None
    assert outcome.action == "rolled-back", outcome.detail
    assert (resources / "runtime" / "marker.txt").read_text(encoding="utf-8") == "old0"
    assert (resources / "app" / "marker.txt").read_text(encoding="utf-8") == "old0"
    assert _user_data_intact(data_dir)

    # And the launcher asks for it before it chooses a mode at all.
    source = Path(entry_point.__file__).read_text(encoding="utf-8")
    branch = source.index('if "--no-gui" in arguments')
    call = source.index("refusal = _recover_interrupted_bundle_update(arguments)")
    assert call < branch, "recovery must run before the mode branch, not inside one"


def test_a_checkout_has_no_layers_to_recover(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a bundle has swappable layers; a checkout must not be touched."""

    from launchers.statusapp import updater as updater_module

    monkeypatch.delenv("WG2_BUNDLE", raising=False)
    assert updater_module.recover_interrupted_bundle_update([]) is None


def test_an_unresolvable_bundle_layout_is_reported_and_nothing_is_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle whose own shape cannot be resolved is not one to rename inside."""

    from launchers.statusapp import updater as updater_module

    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_APP_ROOT", str(tmp_path / "nowhere" / "app"))
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(updater_module.sys, "platform", "darwin")

    outcome = updater_module.recover_interrupted_bundle_update([])

    assert outcome is not None
    assert outcome.action == "none"
    assert "could not be resolved" in outcome.detail


def test_a_recovery_that_fails_stops_the_start_instead_of_reporting_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decided failure refuses; a mechanism that could not run does not.

    The distinction is deliberate. A launcher that will not start because its
    recovery step failed to import is strictly worse than one that starts
    without having run it, because the structural checks that predate the
    journal still run further in. A recovery that ran and concluded the
    installation is broken is a different thing, and that one refuses.
    """

    from launchers.statusapp import __main__ as entry_point

    reported: list[str] = []
    monkeypatch.setattr(entry_point, "_report_startup_failure", reported.append)
    monkeypatch.setattr(
        entry_point,
        "_log_startup_failure",
        lambda *_args, **_kwargs: None,
    )

    from launchers.statusapp import updater as updater_module

    monkeypatch.setattr(
        updater_module,
        "recover_interrupted_bundle_update",
        lambda *_a, **_k: apply_update_module.RecoveryOutcome("failed", "the layers are mixed"),
    )
    assert entry_point._recover_interrupted_bundle_update([]) == 1
    assert reported and "the layers are mixed" in reported[0]

    reported.clear()

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the recovery module is broken")

    monkeypatch.setattr(updater_module, "recover_interrupted_bundle_update", explode)
    assert entry_point._recover_interrupted_bundle_update([]) is None
    assert reported == []


def test_an_unreadable_record_does_not_block_the_installation_for_ever(
    tmp_path: Path,
) -> None:
    """A record that cannot be advanced must still stop blocking once decided.

    `set_journal_state` refuses to rewrite an untrusted record, and rightly:
    editing a truncated file from a partial view replaces the evidence with
    something the editor invented. But a transaction that is never marked
    decided protects its `.previous` for ever, refuses the next update, and
    fills the disk. So recovery removes such a record once it has acted.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)
    journal_path(data_dir).write_text("{ truncated", encoding="utf-8")

    outcome = _recover(resources, data_dir)

    assert outcome.action == "rolled-back"
    assert _generations(resources) == {"app": "old0", "runtime": "old0"}
    assert read_journal(data_dir) is None, "a decided transaction must stop blocking"
    allowed, _detail = commit_transaction(data_dir, resources=resources)
    assert allowed is True
    # And the next update is not refused by the leftover: an unresolved record
    # blocks `begin_update_transaction`, which is the second way a permanently
    # undecidable one would strand the installation.
    next_staged = data_dir / "updates" / "9.9.10" / "staged"
    (next_staged / "app").mkdir(parents=True)
    (next_staged / "runtime").mkdir(parents=True)
    _begin(resources, data_dir, next_staged / "app", next_staged / "runtime")


def test_a_publication_interrupted_record_is_also_cleared_once_decided(
    tmp_path: Path,
) -> None:
    """The same, for the state a lost directory flush leaves behind."""

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)
    apply_update_module.set_journal_state(data_dir, "installed")
    journal_temp_path(data_dir).write_text('{"schema": 1}', encoding="utf-8")

    outcome = _recover(resources, data_dir)

    assert outcome.action == "rolled-back"
    assert read_journal(data_dir) is None
    assert not journal_temp_path(data_dir).exists()


def test_a_refused_second_update_does_not_decide_the_first_ones_transaction(
    tmp_path: Path,
) -> None:
    """The record that refuses the new update is the one it must not overwrite.

    An unresolved transaction is a reason to refuse the next update -- and the
    refusal used to be answered by marking the journal aborted, which decided
    the very transaction whose rollback material was still the only copy of a
    working installation, and let the next healthy start delete it.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    first = _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)

    next_staged = data_dir / "updates" / "9.9.10" / "staged"
    (next_staged / "app").mkdir(parents=True)
    (next_staged / "runtime").mkdir(parents=True)
    reported: list[str] = []

    result = apply_update_module.apply_update(
        bundle=resources,
        data_dir=data_dir,
        staged_app=next_staged / "app",
        staged_runtime=next_staged / "runtime",
        parent_pid=1,
        platform_name="linux",
        relauncher=lambda *_a, **_k: None,
        confirm=lambda _process: None,
        waiter=lambda _pid: True,
        failure_reporter=reported.append,
    )

    assert result == 2
    record = read_journal(data_dir)
    assert record["transaction"] == first["transaction"]
    assert record["state"] not in {"aborted", "installed", "rolled-back"}, (
        "the earlier transaction must stay unresolved, and keep protecting its .previous"
    )
    allowed, _detail = commit_transaction(data_dir, resources=resources)
    assert allowed is False
    assert (resources / "app.previous").is_dir()


def test_a_replayed_record_of_a_finished_transaction_changes_nothing(
    tmp_path: Path,
) -> None:
    """A record restored from a backup, or left behind, must not act twice.

    Its state is terminal, so reconciliation stops at the first check and never
    reaches a directory. The installation it describes has long since moved on.
    """

    resources, data_dir, staged_app, staged_runtime = _installation(tmp_path)
    _begin(resources, data_dir, staged_app, staged_runtime)
    swap_staged_layers(resources, staged_app, staged_runtime, journal_dir=data_dir)
    replayed = read_journal(data_dir)
    replayed["state"] = "installed"
    write_journal(data_dir, replayed)
    assert commit_transaction(data_dir, resources=resources)[0] is True
    assert read_journal(data_dir) is None
    rollback_previous_layers(resources)
    before = _generations(resources)

    # The record comes back, exactly as it was when it was current.
    write_journal(data_dir, replayed)
    outcome = _recover(resources, data_dir)

    assert outcome.action == "none"
    assert "already installed" in outcome.detail
    assert _generations(resources) == before
    assert _user_data_intact(data_dir)
