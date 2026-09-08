"""Every relaunch the updater performs must carry its own fresh grant.

These tests run the *real* startup gate as the relaunched child. The updater
holds the installation's update claim across the whole transaction, so a child
that starts while it is held is refused unless it presents a single-use grant
the updater minted for that start. An injected relauncher that only records its
argument cannot see any of that: it never takes the claim and never spends a
grant, so it reports a successful relaunch for a start the product refuses.

Both application shapes are exercised, on either host. macOS reaches the layers
through ``Contents/Resources`` and relaunches through ``open``; Windows keeps
the layers beside a launcher executable that is refreshed with the runtime and
carries its relaunch arguments in the environment. Every path here is built
with :class:`pathlib.Path`, so no separator is ever written into a value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

import pytest

from launchers import update_lock
from launchers.apply_update import (
    WINDOWS_LAUNCHER_NAME,
    apply_update,
    resources_directory,
    write_journal,
)
from launchers.statusapp.updater import recover_interrupted_bundle_update


@pytest.fixture(autouse=True)
def _grants_live_in_the_test_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep the claim and its grants out of the real per-user cache directory.

    ``update_lock`` derives both the lock file and the grant files from
    ``cache_root``, which is the host's own cache directory. A test that used it
    would write into the developer's installation state and would fail wherever
    that directory is not writable -- and the grant writer answers an
    unwritable directory by logging and carrying on, so such a failure arrives
    as a missing key rather than as an error.
    """

    root = tmp_path / "cache"
    monkeypatch.setattr(update_lock, "cache_root", lambda **_kwargs: root)
    return root


@dataclass(frozen=True, slots=True)
class Installation:
    """One installed application, in the shape the named platform gives it."""

    platform_name: str
    bundle: Path
    resources: Path
    data_dir: Path
    staged_app: Path
    staged_runtime: Path

    @property
    def app_layer(self) -> Path:
        return self.resources / "app"


def _write_layer(layer: Path, marker: str) -> None:
    layer.mkdir(parents=True)
    (layer / "marker.txt").write_text(marker, encoding="utf-8")


def _installation(tmp_path: Path, platform_name: str) -> Installation:
    """Build the macOS bundle or the Windows folder, whichever was asked for."""

    root = tmp_path / (
        "Waveguide Generator.app" if platform_name == "darwin" else "Waveguide Generator"
    )
    resources = resources_directory(root, platform_name)
    staged_app = tmp_path / "data" / "updates" / "9.9.9" / "staged" / "app"
    staged_runtime = staged_app.parent / "runtime"
    for layer, marker in (
        (resources / "app", "old app"),
        (resources / "runtime", "old runtime"),
        (staged_app, "new app"),
        (staged_runtime, "new runtime"),
    ):
        _write_layer(layer, marker)
    if platform_name == "win32":
        # The launcher lives beside the layers rather than inside the runtime,
        # and the swap refreshes it from the runtime that arrived with it.
        (resources / WINDOWS_LAUNCHER_NAME).write_text("old launcher", encoding="utf-8")
        (resources / "runtime" / "pythonw.exe").write_text(
            "old launcher", encoding="utf-8"
        )
        (staged_runtime / "pythonw.exe").write_text("new launcher", encoding="utf-8")
        (staged_runtime / "RUNTIME-MANIFEST.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "platform": "windows-x86_64",
                    # Plain member names: a launcher entry is platform-neutral
                    # and never carries a separator of either host.
                    "launcherFiles": [
                        {"source": "pythonw.exe", "destination": WINDOWS_LAUNCHER_NAME}
                    ],
                }
            ),
            encoding="utf-8",
        )
    return Installation(
        platform_name=platform_name,
        bundle=root,
        resources=resources,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
    )


@dataclass
class Start:
    """One relaunch, and what the real startup gate made of it."""

    command: list[str]
    grant: str | None
    action: str
    detail: str

    @property
    def refused(self) -> bool:
        return self.action == "failed"


@dataclass
class StartupGate:
    """A relauncher that actually starts the child through the product's gate.

    ``recover_interrupted_bundle_update`` is the function every start mode
    passes through before it chooses one, and it is what refuses a start into an
    installation somebody else is writing. Calling it here -- in this process,
    which is the one holding the claim, so the claim really is held against it
    -- is what makes these tests measure authorization rather than the fact that
    a lambda was called.
    """

    installation: Installation
    starts: list[Start] = field(default_factory=list)
    #: The relaunch numbers whose child is reported as having died on us.
    fail_start_numbers: frozenset[int] = frozenset()

    def relaunch(
        self,
        command: Sequence[str],
        platform_name: str,
        *,
        environment: Mapping[str, str] | None = None,
        **_kwargs: object,
    ) -> Start:
        child_environment = dict(environment or {})
        child_environment.update(
            WG2_BUNDLE="1",
            WG2_APP_ROOT=str(self.installation.app_layer),
            WG2_DATA_DIR=str(self.installation.data_dir),
        )
        outcome = recover_interrupted_bundle_update(
            environ=child_environment, platform_name=platform_name
        )
        assert outcome is not None, "the child did not recognise a bundle start"
        start = Start(
            command=list(command),
            grant=child_environment.get(update_lock.RELAUNCH_ENVIRONMENT_VARIABLE),
            action=outcome.action,
            detail=outcome.detail,
        )
        self.starts.append(start)
        return start

    def confirm(self, start: Start) -> str | None:
        if len(self.starts) in self.fail_start_numbers:
            return "exited with code 1 within 6 seconds of starting"
        if start.refused:
            # A refused start exits; the updater must not read that as healthy.
            return "exited with code 4 within 6 seconds of starting"
        return None


def _signing_runner(
    command: Sequence[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    """Answer the macOS reseal without running codesign on a test fixture."""

    return subprocess.CompletedProcess(list(command), 0, "", "")


def _relaunch_command(installation: Installation) -> list[str]:
    if installation.platform_name == "darwin":
        return ["/usr/bin/open", "-n", str(installation.bundle)]
    return [str(installation.resources / WINDOWS_LAUNCHER_NAME)]


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_an_update_cancelled_before_any_change_reopens_the_current_version(
    tmp_path: Path, platform_name: str
) -> None:
    """A pre-swap refusal must leave the application open, not closed.

    The updater stopped the application, then refused before it moved anything,
    and it still holds the claim because it is entitled to. The version on the
    disk is the one that was already there, so reopening it is safe -- but the
    start it performs is subject to the same gate as any other, and only a fresh
    grant gets it past.
    """

    installation = _installation(tmp_path, platform_name)
    gate = StartupGate(installation)
    logs: list[str] = []

    with update_lock.claim_update(installation.resources):
        exit_code = apply_update(
            bundle=installation.bundle,
            data_dir=installation.data_dir,
            # Never staged, so planning refuses before the first rename.
            staged_app=tmp_path / "staged-that-was-never-written",
            staged_runtime=None,
            parent_pid=0,
            platform_name=platform_name,
            environ={"PATH": "x"},
            runner=_signing_runner,
            relauncher=gate.relaunch,
            confirm=gate.confirm,
            waiter=lambda _pid: True,
            logger=logs.append,
            failure_reporter=lambda _message: None,
        )

    assert exit_code == 2
    assert len(gate.starts) == 1
    start = gate.starts[0]
    assert start.command == _relaunch_command(installation)
    assert start.grant, "the cancelled update supplied the child no grant"
    assert not start.refused, start.detail
    # Single use: the child spent it, so nothing can spend it again.
    assert (
        update_lock.consume_relaunch_grant(installation.resources, start.grant) is False
    )
    assert "The current version was reopened." in "\n".join(logs)
    assert (installation.resources / "app" / "marker.txt").read_text(
        encoding="utf-8"
    ) == "old app"


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_a_rolled_back_update_reopens_the_restored_version_with_a_second_grant(
    tmp_path: Path, platform_name: str
) -> None:
    """The restored version needs its own grant; the first one has been spent.

    The swap succeeded, the updater minted a grant, and the new version would
    not stay running. The rollback then restores the previous layers and reopens
    them -- a second start, while the claim is still held, and the grant the
    first start consumed cannot authorize it.
    """

    installation = _installation(tmp_path, platform_name)
    # The updated application is the one reported as having died, so the
    # rollback runs; the restored one is left to the gate to judge.
    gate = StartupGate(installation, fail_start_numbers=frozenset({1}))
    logs: list[str] = []

    with update_lock.claim_update(installation.resources):
        exit_code = apply_update(
            bundle=installation.bundle,
            data_dir=installation.data_dir,
            staged_app=installation.staged_app,
            staged_runtime=installation.staged_runtime,
            parent_pid=0,
            platform_name=platform_name,
            environ={"PATH": "x"},
            runner=_signing_runner,
            relauncher=gate.relaunch,
            confirm=gate.confirm,
            waiter=lambda _pid: True,
            logger=logs.append,
            failure_reporter=lambda _message: None,
        )

    assert exit_code == 5
    assert [start.command for start in gate.starts] == [
        _relaunch_command(installation)
    ] * 2
    first, second = gate.starts
    assert first.grant and second.grant
    assert second.grant != first.grant, "the restored start reused a spent grant"
    assert not second.refused, second.detail
    assert (
        update_lock.consume_relaunch_grant(installation.resources, second.grant) is False
    )
    assert (installation.resources / "app" / "marker.txt").read_text(
        encoding="utf-8"
    ) == "old app"
    if platform_name == "win32":
        assert (installation.resources / WINDOWS_LAUNCHER_NAME).read_text(
            encoding="utf-8"
        ) == "old launcher"
    assert "The previous version was restored and reopened." in "\n".join(logs)


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_an_unresolved_transaction_is_never_authorized_by_two_directories(
    tmp_path: Path, platform_name: str
) -> None:
    """Both layers existing is not evidence that the installation is settled.

    An update refused because an *earlier* transaction was never decided leaves
    an installation whose ``app`` and ``runtime`` are both present and whose
    state nobody has reconciled. Authorizing a start into it would hand the
    child the one token that tells it to skip reconciliation -- so this path
    relaunches nothing, says why, and leaves the next start to recover it.
    """

    installation = _installation(tmp_path, platform_name)
    write_journal(
        installation.data_dir,
        installation.resources,
        {
            "schema": 1,
            "transaction": "abandoned-by-a-power-cut",
            "operation": "update",
            "state": "swapped",
            "platform": platform_name,
            "bundle": str(installation.bundle),
            "resources": str(installation.resources),
            "layers": [],
        },
    )
    gate = StartupGate(installation)
    logs: list[str] = []

    with update_lock.claim_update(installation.resources):
        exit_code = apply_update(
            bundle=installation.bundle,
            data_dir=installation.data_dir,
            staged_app=installation.staged_app,
            staged_runtime=installation.staged_runtime,
            parent_pid=0,
            platform_name=platform_name,
            environ={"PATH": "x"},
            runner=_signing_runner,
            relauncher=gate.relaunch,
            confirm=gate.confirm,
            waiter=lambda _pid: True,
            logger=logs.append,
            failure_reporter=lambda _message: None,
        )

    assert exit_code == 2
    assert gate.starts == []
    recorded = "\n".join(logs)
    assert "still unresolved" in recorded
    assert "was not reopened" in recorded
    # Nothing was minted, so no grant is lying about waiting to be spent.
    directory = update_lock.cache_root() / update_lock.LOCK_DIRECTORY
    assert sorted(path.name for path in directory.glob("relaunch-*")) == []


def test_the_journal_guard_ignores_another_installations_record(
    tmp_path: Path,
) -> None:
    """A record naming a different installation decides nothing about this one.

    One data directory can be shared by two copies of the application. The
    journal is addressed by installation, but a file copied between data
    directories can still be read here, and acting on it would refuse a start
    this installation is entitled to.
    """

    installation = _installation(tmp_path, "darwin")
    write_journal(
        installation.data_dir,
        installation.resources,
        {
            "schema": 1,
            "transaction": "belongs-to-the-other-copy",
            "operation": "update",
            "state": "swapped",
            "platform": "darwin",
            "bundle": str(tmp_path / "Elsewhere.app"),
            "resources": str(tmp_path / "Elsewhere.app" / "Contents" / "Resources"),
            "layers": [],
        },
    )
    gate = StartupGate(installation)
    logs: list[str] = []

    with update_lock.claim_update(installation.resources):
        exit_code = apply_update(
            bundle=installation.bundle,
            data_dir=installation.data_dir,
            staged_app=tmp_path / "staged-that-was-never-written",
            staged_runtime=None,
            parent_pid=0,
            platform_name="darwin",
            runner=_signing_runner,
            relauncher=gate.relaunch,
            confirm=gate.confirm,
            waiter=lambda _pid: True,
            logger=logs.append,
            failure_reporter=lambda _message: None,
        )

    assert exit_code == 2
    assert len(gate.starts) == 1
    assert gate.starts[0].grant
    assert not gate.starts[0].refused, gate.starts[0].detail


def test_a_grant_that_cannot_be_written_still_leaves_the_update_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authorization is best effort; it may cost a relaunch, never an update.

    The cache directory can be unwritable -- a sandbox, a roaming profile that
    did not arrive. The updater says so and carries on: the child is refused and
    the user opens the application again, which is the same outcome the defect
    produced, but reported rather than silent.
    """

    installation = _installation(tmp_path, "darwin")

    def refuse(*_args: Any, **_kwargs: Any) -> str:
        raise PermissionError(13, "the cache directory is not writable")

    monkeypatch.setattr(
        "launchers.apply_update.grant_relaunch", refuse, raising=True
    )
    gate = StartupGate(installation)
    logs: list[str] = []

    with update_lock.claim_update(installation.resources):
        exit_code = apply_update(
            bundle=installation.bundle,
            data_dir=installation.data_dir,
            staged_app=tmp_path / "staged-that-was-never-written",
            staged_runtime=None,
            parent_pid=0,
            platform_name="darwin",
            runner=_signing_runner,
            relauncher=gate.relaunch,
            confirm=gate.confirm,
            waiter=lambda _pid: True,
            logger=logs.append,
            failure_reporter=lambda _message: None,
        )

    assert exit_code == 2
    assert len(gate.starts) == 1
    assert gate.starts[0].grant is None
    assert gate.starts[0].refused
    assert "Could not authorize the relaunch" in "\n".join(logs)
    # The installation itself is untouched: nothing was staged, so nothing moved.
    assert (installation.resources / "app" / "marker.txt").read_text(
        encoding="utf-8"
    ) == "old app"
