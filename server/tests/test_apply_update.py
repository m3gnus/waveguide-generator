"""Transactional standalone-layer swap and relaunch contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from launchers.apply_update import (
    RENAME_RETRY_INTERVAL,
    WINDOWS_CREATE_NEW_PROCESS_GROUP,
    WINDOWS_DETACHED_PROCESS,
    _remove,
    _rename,
    apply_update,
    build_parser,
    cleanup_previous_layers,
    confirm_relaunch,
    main as run_updater_cli,
    process_exists,
    refresh_launcher_files,
    relaunch_application,
    relaunch_command,
    relaunch_environment,
    rollback_bundle,
    rollback_previous_layers,
    staged_launcher_files,
    wait_for_parent,
)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 handle semantics")
def test_the_liveness_probe_survives_every_windows_case(tmp_path: Path) -> None:
    """os.kill cannot answer this on Windows, and not for the usual reason.

    Signal 0 does not terminate the target -- measured on 3.13.3 and 3.13.12,
    contrary to the comment this repository carried for a while. What actually
    breaks is that Win32 keeps a process object resolvable while anyone holds a
    handle to it, so a dead process reads as running and a bounded wait becomes
    a hang. GetExitCodeProcess fixes that and then mistakes exit code 259 for
    STILL_ACTIVE. Only waiting on the handle gets every case right.
    """

    def spawn(code: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen([sys.executable, "-c", code])

    # Dead, but this test still holds the handle: the case os.kill gets wrong.
    reaped = spawn("pass")
    reaped.wait()
    time.sleep(0.5)
    assert process_exists(reaped.pid) is False
    assert wait_for_parent(reaped.pid, timeout=2.0) is True

    # Exited with 259, which is STILL_ACTIVE: the case GetExitCodeProcess gets wrong.
    unlucky = spawn("raise SystemExit(259)")
    unlucky.wait()
    time.sleep(0.5)
    assert process_exists(unlucky.pid) is False

    # A genuinely running process must still read as running.
    alive = spawn("import time; time.sleep(30)")
    try:
        time.sleep(0.8)
        assert process_exists(alive.pid) is True
        # ... and the probe must not have killed it, which is the myth's claim.
        assert alive.poll() is None
    finally:
        alive.kill()
        alive.wait()

    assert process_exists(4294967) is False
    assert process_exists(0) is False
    assert process_exists(-1) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_the_posix_probe_still_uses_signal_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def vanished(pid: int, signal_number: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", vanished)
    assert process_exists(4321) is False
    assert wait_for_parent(4321, timeout=0.0) is True

    def refused(pid: int, signal_number: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", refused)
    # A process we may not signal is still a process.
    assert process_exists(4321) is True


class _AccessDenied(OSError):
    """Renaming a directory something still holds open, as Windows reports it."""

    winerror = 5  # ERROR_ACCESS_DENIED


def test_a_transient_directory_lock_is_waited_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The departing server can still be releasing mapped modules at swap time.

    The updater waits only for its parent pid, so the server subprocess that
    parent owned may not be finished exiting when the first rename lands.
    """

    source = tmp_path / "runtime"
    source.mkdir()
    destination = tmp_path / "runtime.previous"
    real_replace = os.replace
    attempts: list[int] = []

    def flaky(src: object, dst: object) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise _AccessDenied(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    slept: list[float] = []

    _rename(source, destination, sleeper=slept.append)

    assert destination.is_dir()
    assert len(attempts) == 3
    assert slept == [RENAME_RETRY_INTERVAL, RENAME_RETRY_INTERVAL]


def test_a_lock_that_never_clears_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        os, "replace", lambda src, dst: (_ for _ in ()).throw(_AccessDenied(13, "denied"))
    )
    ticks = iter([0.0, 5.0, 25.0])

    with pytest.raises(OSError):
        _rename(
            tmp_path / "runtime",
            tmp_path / "runtime.previous",
            clock=lambda: next(ticks),
            sleeper=lambda _seconds: None,
        )


def test_an_unrelated_rename_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoSuchFile(OSError):
        winerror = 2  # ERROR_FILE_NOT_FOUND

    attempts: list[int] = []

    def missing(src: object, dst: object) -> None:
        attempts.append(1)
        raise _NoSuchFile(2, "The system cannot find the file specified")

    monkeypatch.setattr(os, "replace", missing)

    with pytest.raises(OSError):
        _rename(tmp_path / "a", tmp_path / "b", sleeper=lambda _seconds: None)
    assert len(attempts) == 1


def _layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    bundle = tmp_path / "Waveguide Generator.app"
    resources = bundle / "Contents" / "Resources"
    app = resources / "app"
    runtime = resources / "runtime"
    staged_app = tmp_path / "data" / "updates" / "2.0.1" / "staged" / "app"
    staged_runtime = staged_app.parent / "runtime"
    for path, marker in (
        (app, "old app"),
        (runtime, "old runtime"),
        (staged_app, "new app"),
        (staged_runtime, "new runtime"),
    ):
        path.mkdir(parents=True)
        (path / "marker.txt").write_text(marker, encoding="utf-8")
    return bundle, resources, staged_app, staged_runtime


def test_successful_swap_keeps_previous_layers_and_uses_injected_relauncher(
    tmp_path: Path,
) -> None:
    bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    commands: list[list[str]] = []
    relaunched: list[tuple[list[str], str, object]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = apply_update(
        bundle=bundle,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=123,
        platform_name="darwin",
        runner=run,
        relauncher=lambda command, platform_name, **_kwargs: relaunched.append(
            (list(command), platform_name, _kwargs.get("environment"))
        ),
        waiter=lambda _pid: True,
    )

    assert result == 0
    assert (resources / "app" / "marker.txt").read_text() == "new app"
    assert (resources / "runtime" / "marker.txt").read_text() == "new runtime"
    assert (resources / "app.previous" / "marker.txt").read_text() == "old app"
    assert (resources / "runtime.previous" / "marker.txt").read_text() == "old runtime"
    assert [command[0] for command in commands] == ["xattr", "/usr/bin/codesign"]
    # macOS keeps its argv route: LaunchServices passes no environment at all.
    assert relaunched == [(["open", "-n", str(bundle)], "darwin", None)]


def test_failed_second_rename_restores_the_old_layout_and_does_not_relaunch(
    tmp_path: Path,
) -> None:
    bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    relaunched: list[list[str]] = []

    def fail_new_app(source: Path, destination: Path) -> None:
        if source == staged_app.resolve() and destination == resources / "app":
            raise OSError("injected rename failure")
        source.rename(destination)

    result = apply_update(
        bundle=bundle,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=123,
        platform_name="darwin",
        renamer=fail_new_app,
        relauncher=lambda command, _platform, **_kwargs: relaunched.append(list(command)),
        waiter=lambda _pid: True,
    )

    assert result == 2
    assert (resources / "app" / "marker.txt").read_text() == "old app"
    assert (resources / "runtime" / "marker.txt").read_text() == "old runtime"
    assert not (resources / "app.previous").exists()
    assert not (resources / "runtime.previous").exists()
    assert staged_app.is_dir() and staged_runtime.is_dir()
    assert relaunched == []
    log = (tmp_path / "data" / "logs" / "update.log").read_text(encoding="utf-8")
    assert "injected rename failure" in log
    assert "restored" in log


def test_healthy_cleanup_and_startup_rollback_manage_previous_layers(
    tmp_path: Path,
) -> None:
    _bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    (resources / "app").rename(resources / "app.previous")
    staged_app.rename(resources / "app")
    (resources / "runtime").rename(resources / "runtime.previous")
    staged_runtime.rename(resources / "runtime")

    assert rollback_previous_layers(resources) is True
    assert (resources / "app" / "marker.txt").read_text() == "old app"
    assert (resources / "runtime" / "marker.txt").read_text() == "old runtime"

    (resources / "app.previous").mkdir()
    (resources / "runtime.previous").mkdir()
    removed = cleanup_previous_layers(resources)

    assert {path.name for path in removed} == {"app.previous", "runtime.previous"}
    assert not any(path.exists() for path in removed)


def test_relaunch_commands_name_the_bundle_or_windows_executable(tmp_path: Path) -> None:
    bundle = tmp_path / "Waveguide Generator.app"
    folder = tmp_path / "Waveguide Generator"

    assert relaunch_command(bundle, "darwin") == ["open", "-n", str(bundle)]
    assert relaunch_command(folder, "win32") == [str(folder / "Waveguide Generator.exe")]
    # open(1) passes neither env nor argv through LaunchServices, so the
    # original --port/--data-dir must travel as explicit --args.
    arguments = ("--port", "3110", "--data-dir", "/somewhere/data")
    assert relaunch_command(bundle, "darwin", arguments) == [
        "open",
        "-n",
        str(bundle),
        "--args",
        *arguments,
    ]
    assert build_parser().parse_args(
        [
            "--bundle",
            str(bundle),
            "--data-dir",
            "d",
            "--staged-app-dir",
            "s",
            "--parent-pid",
            "1",
            "--relaunch-arg=--port",
            "--relaunch-arg=3110",
        ]
    ).relaunch_args == ["--port", "3110"]


def test_the_windows_relaunch_never_puts_arguments_on_the_launcher_command_line(
    tmp_path: Path,
) -> None:
    """``Waveguide Generator.exe`` is a renamed ``pythonw.exe``.

    CPython parses everything after the executable as an interpreter option, so
    the previous ``[exe, "--port", "3110"]`` died with ``unknown option
    --port`` before any application code ran -- an update that reported success
    and left nothing on screen. Verified on a real bundle. An argument would
    also make ``sys.argv[0]`` non-empty, which is precisely how the bundle
    bootstrap tells a double-click apart from a server worker reusing the
    launcher as ``sys.executable``, so even a valid ``-m`` invocation would
    skip WG2_BUNDLE, WG2_APP_ROOT and the cache redirection.
    """

    folder = tmp_path / "Waveguide Generator"
    exe = str(folder / "Waveguide Generator.exe")
    data = tmp_path / "resolved data"

    assert relaunch_command(folder, "win32") == [exe]
    assert relaunch_command(folder, "win32", ("--port", "3110")) == [exe]
    assert relaunch_command(folder, "win32", ("--data-dir", "/somewhere")) == [exe]

    # No arguments at all: exactly the Explorer double-click, inheriting the
    # environment untouched.
    assert relaunch_environment((), "win32", environ={"PATH": "x"}) == (None, [])

    environment, dropped = relaunch_environment(("--port", "3110"), "win32", environ={"PATH": "x"})
    assert environment == {"PATH": "x", "WG2_PORT": "3110"}
    assert dropped == []

    # The updater already holds the resolved data directory the departing
    # process was using, so the override survives without re-deriving it.
    environment, dropped = relaunch_environment(
        ("--data-dir", "~/raw"), "win32", data_dir=data, environ={}
    )
    assert environment == {"WG2_DATA_DIR": str(data)}
    assert dropped == []

    # Both spellings the status controller itself accepts.
    environment, dropped = relaunch_environment(
        ("--port=3110", "--data-dir=/raw"), "win32", data_dir=data, environ={}
    )
    assert environment == {"WG2_PORT": "3110", "WG2_DATA_DIR": str(data)}
    assert dropped == []


def test_arguments_the_environment_cannot_carry_are_reported_not_smuggled(
    tmp_path: Path,
) -> None:
    """--browser is out of scope, but it must not take the relaunch down with it.

    Forwarding it verbatim made the launcher refuse its own command line; the
    launcher now starts, and the argument that could not travel is named in
    update.log instead of disappearing.
    """

    environment, dropped = relaunch_environment(
        ("--browser", "--no-browser", "--port", "not-a-port"), "win32", environ={}
    )

    assert environment is None
    assert dropped == ["--browser", "--no-browser", "--port", "not-a-port"]

    folder = tmp_path / "Waveguide Generator"
    relaunched: list[list[str]] = []
    app = folder / "app"
    staged_app = tmp_path / "data" / "updates" / "9.9.9" / "staged" / "app"
    for path in (app, staged_app):
        path.mkdir(parents=True)

    apply_update(
        bundle=folder,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=None,
        parent_pid=1,
        relaunch_arguments=("--browser",),
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, _platform, **_kwargs: relaunched.append(list(command)),
        waiter=lambda _pid: True,
    )

    assert relaunched == [[str(folder / "Waveguide Generator.exe")]]
    log = (tmp_path / "data" / "logs" / "update.log").read_text(encoding="utf-8")
    assert "could not carry these arguments" in log
    assert "--browser" in log


def test_macos_relaunch_arguments_are_untouched_by_the_windows_fix(tmp_path: Path) -> None:
    bundle = tmp_path / "Waveguide Generator.app"
    arguments = ("--port", "3110", "--data-dir", "/somewhere/data", "--browser")

    assert relaunch_command(bundle, "darwin", arguments) == [
        "open",
        "-n",
        str(bundle),
        "--args",
        *arguments,
    ]
    # Nothing is translated and nothing is dropped: argv is the macOS route.
    assert relaunch_environment(arguments, "darwin", data_dir=tmp_path) == (None, [])


class _ExitedChild:
    def __init__(self, code: int | None) -> None:
        self._code = code

    def poll(self) -> int | None:
        return self._code


def test_a_relaunch_that_dies_immediately_is_not_reported_as_a_success(
    tmp_path: Path,
) -> None:
    """Popen returning proves only that CreateProcess succeeded."""

    folder = tmp_path / "Waveguide Generator"
    app = folder / "app"
    staged_app = tmp_path / "data" / "updates" / "9.9.9" / "staged" / "app"
    for path in (app, staged_app):
        path.mkdir(parents=True)

    exit_code = apply_update(
        bundle=folder,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=None,
        parent_pid=1,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, _platform, **_kwargs: _ExitedChild(2),
        waiter=lambda _pid: True,
        confirm=lambda process: confirm_relaunch(
            process, timeout=0.0, sleeper=lambda _seconds: None
        ),
    )

    assert exit_code == 5
    log = (tmp_path / "data" / "logs" / "update.log").read_text(encoding="utf-8")
    assert "did not stay" in log
    assert "exited with code 2" in log


def test_a_relaunch_that_keeps_running_reports_success() -> None:
    ticks = iter([0.0, 0.1, 9.0])

    assert (
        confirm_relaunch(
            _ExitedChild(None),
            clock=lambda: next(ticks),
            sleeper=lambda _seconds: None,
        )
        is None
    )
    # A relaunch stub with no poll() -- every injected test double -- is not
    # something to fail on.
    assert confirm_relaunch(object()) is None


def test_windows_apply_skips_macos_repairs_and_injects_the_exe_relaunch(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "Waveguide Generator"
    app = folder / "app"
    runtime = folder / "runtime"
    staged_app = tmp_path / "data" / "updates" / "2.0.1" / "staged" / "app"
    for path, marker in (
        (app, "old app"),
        (runtime, "runtime"),
        (staged_app, "new app"),
    ):
        path.mkdir(parents=True)
        (path / "marker.txt").write_text(marker, encoding="utf-8")
    repair_commands: list[list[str]] = []
    relaunched: list[tuple[list[str], str, object]] = []

    result = apply_update(
        bundle=folder,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=None,
        parent_pid=42,
        relaunch_arguments=("--port", "3110"),
        platform_name="win32",
        environ={"PATH": "x"},
        runner=lambda command, **_kwargs: (
            repair_commands.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
        relauncher=lambda command, platform_name, **_kwargs: relaunched.append(
            (list(command), platform_name, _kwargs.get("environment"))
        ),
        waiter=lambda _pid: True,
    )

    assert result == 0
    assert repair_commands == []
    assert (folder / "app" / "marker.txt").read_text() == "new app"
    assert (folder / "app.previous" / "marker.txt").read_text() == "old app"
    # The launcher is started the way Explorer starts it, with --port carried
    # in the environment the child inherits.
    assert relaunched == [
        (
            [str(folder / "Waveguide Generator.exe")],
            "win32",
            {"PATH": "x", "WG2_PORT": "3110"},
        )
    ]


def test_windows_relaunch_is_detached_without_a_console() -> None:
    started: list[tuple[list[str], dict[str, object]]] = []

    def popen(command: list[str], **options: object) -> object:
        started.append((command, options))
        return object()

    command = [r"bundle\Waveguide Generator.exe", "--port", "3110"]
    relaunch_application(command, "win32", process_factory=popen)  # type: ignore[arg-type]

    assert started[0][0] == command
    options = started[0][1]
    assert options["creationflags"] == (WINDOWS_DETACHED_PROCESS | WINDOWS_CREATE_NEW_PROCESS_GROUP)
    assert options["stdin"] is subprocess.DEVNULL
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    assert options["close_fds"] is True


def _unexpected_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    raise AssertionError(f"No external command belongs in a Windows update: {command}")


def _unexpected_relauncher(command: object, platform_name: str, **_kwargs: object) -> None:
    raise AssertionError(f"A refused update must not relaunch: {command} ({platform_name})")


def _windows_layout(tmp_path: Path, *, launcher_files: object = None) -> tuple[Path, Path, Path]:
    """A Windows application folder whose launcher sits outside the runtime."""

    root = tmp_path / "Waveguide Generator"
    staged_app = tmp_path / "data" / "updates" / "0.2.5" / "staged" / "app"
    staged_runtime = staged_app.parent / "runtime"
    for path, marker in (
        (root / "app", "old app"),
        (root / "runtime", "old runtime"),
        (staged_app, "new app"),
        (staged_runtime, "new runtime"),
    ):
        path.mkdir(parents=True)
        (path / "marker.txt").write_text(marker, encoding="utf-8")
    (root / "Waveguide Generator.exe").write_text("old launcher", encoding="utf-8")
    (root / "python313.dll").write_text("old dll", encoding="utf-8")
    (root / "runtime" / "pythonw.exe").write_text("old launcher", encoding="utf-8")
    (staged_runtime / "pythonw.exe").write_text("new launcher", encoding="utf-8")
    (staged_runtime / "python313.dll").write_text("new dll", encoding="utf-8")
    entries = (
        [
            {"source": "pythonw.exe", "destination": "Waveguide Generator.exe"},
            {"source": "python313.dll", "destination": "python313.dll"},
        ]
        if launcher_files is None
        else launcher_files
    )
    payload: dict[str, object] = {"schemaVersion": 1, "platform": "windows-x86_64"}
    if entries is not False:
        payload["launcherFiles"] = entries
    (staged_runtime / "RUNTIME-MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    return root, staged_app, staged_runtime


def test_a_windows_runtime_update_refreshes_the_launcher_beside_it(tmp_path: Path) -> None:
    root, staged_app, staged_runtime = _windows_layout(tmp_path)

    exit_code = apply_update(
        bundle=root,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, platform_name, **_kwargs: None,
        waiter=lambda _pid: True,
    )

    assert exit_code == 0
    # The launcher and its interpreter DLL now come from the installed runtime,
    # so neither outlives the Python it was taken from.
    assert (root / "Waveguide Generator.exe").read_text(encoding="utf-8") == "new launcher"
    assert (root / "python313.dll").read_text(encoding="utf-8") == "new dll"
    assert (root / "Waveguide Generator.exe.previous").read_text(encoding="utf-8") == "old launcher"


def test_a_failed_windows_start_restores_the_launcher_with_the_layers(tmp_path: Path) -> None:
    root, staged_app, staged_runtime = _windows_layout(tmp_path)
    apply_update(
        bundle=root,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, platform_name, **_kwargs: None,
        waiter=lambda _pid: True,
    )

    assert rollback_previous_layers(root) is True

    assert (root / "Waveguide Generator.exe").read_text(encoding="utf-8") == "old launcher"
    assert (root / "python313.dll").read_text(encoding="utf-8") == "old dll"
    assert (root / "runtime" / "marker.txt").read_text(encoding="utf-8") == "old runtime"
    assert not list(root.glob("*.previous"))


def test_a_healthy_windows_start_removes_the_saved_launcher_files(tmp_path: Path) -> None:
    root, staged_app, staged_runtime = _windows_layout(tmp_path)
    apply_update(
        bundle=root,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, platform_name, **_kwargs: None,
        waiter=lambda _pid: True,
    )

    removed = cleanup_previous_layers(root)

    assert not list(root.glob("*.previous"))
    assert {path.name for path in removed} == {
        "app.previous",
        "runtime.previous",
        "Waveguide Generator.exe.previous",
        "python313.dll.previous",
    }


def test_an_unsafe_launcher_file_name_is_refused_and_rolls_the_update_back(
    tmp_path: Path,
) -> None:
    root, staged_app, staged_runtime = _windows_layout(
        tmp_path,
        launcher_files=[{"source": "pythonw.exe", "destination": "../escaped.exe"}],
    )

    exit_code = apply_update(
        bundle=root,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=_unexpected_relauncher,
        waiter=lambda _pid: True,
    )

    assert exit_code == 4
    assert not (tmp_path / "escaped.exe").exists()
    # The refusal restores the version that was running.
    assert (root / "app" / "marker.txt").read_text(encoding="utf-8") == "old app"
    assert (root / "runtime" / "marker.txt").read_text(encoding="utf-8") == "old runtime"
    assert (root / "Waveguide Generator.exe").read_text(encoding="utf-8") == "old launcher"


def test_a_macos_runtime_update_has_no_launcher_files_to_refresh(tmp_path: Path) -> None:
    bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    (staged_runtime / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "platform": "macos-arm64"}), encoding="utf-8"
    )

    assert refresh_launcher_files(resources) == []
    assert staged_launcher_files(staged_runtime) == []


# The complete set the Windows runtime manifest declares, which is exactly the
# set a rollback has to put back: a launcher that outlives its interpreter is
# what the refresh mechanism exists to prevent.
WINDOWS_LAUNCHER_FILES = (
    "Waveguide Generator.exe",
    "python313.dll",
    "python3.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
)


def _rolled_forward_windows_install(tmp_path: Path) -> Path:
    """A Windows folder mid-update: new layers installed, old ones beside them."""

    root = tmp_path / "Waveguide Generator"
    for name, marker in (
        ("app", "new app"),
        ("app.previous", "old app"),
        ("runtime", "new runtime"),
        ("runtime.previous", "old runtime"),
    ):
        layer = root / name
        layer.mkdir(parents=True)
        (layer / "marker.txt").write_text(marker, encoding="utf-8")
    (root / "runtime" / "DLLs").mkdir()
    (root / "runtime" / "DLLs" / "libcrypto-3-x64.dll").write_text("new", encoding="utf-8")
    for name in WINDOWS_LAUNCHER_FILES:
        (root / name).write_text(f"new {name}", encoding="utf-8")
        (root / f"{name}.previous").write_text(f"old {name}", encoding="utf-8")
    return root


def _undeletable_failed_copies(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Make deleting anything named ``*.failed*`` fail as Windows makes it fail."""

    refused: list[Path] = []

    def guarded(path: Path) -> None:
        if ".failed" in path.name:
            refused.append(path)
            raise PermissionError(
                13,
                "Access is denied",
                str(path / "DLLs" / "libcrypto-3-x64.dll"),
            )
        _remove(path)

    monkeypatch.setattr("launchers.apply_update._remove", guarded)
    return refused


def test_a_failed_copy_that_cannot_be_deleted_never_blocks_the_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact shape of the observed 0.2.6 failure.

    ``rollback_previous_layers`` used to ``rmtree`` the displaced layer inside
    its critical path. Windows refuses to delete a file whose image is mapped
    into a live process, so that raised WinError 5 for
    ``runtime.failed/DLLs/libcrypto-3-x64.dll`` -- and because it raised, the
    loop that restores the top-level launcher files never ran. The installation
    was left with a 0.2.5 app and runtime under a 0.2.6 ``vcruntime140.dll``.
    Deletion is now the last thing tried, and never fatal.
    """

    root = _rolled_forward_windows_install(tmp_path)
    refused = _undeletable_failed_copies(monkeypatch)
    entries: list[str] = []

    assert rollback_previous_layers(root, log=entries.append) is True

    assert (root / "app" / "marker.txt").read_text(encoding="utf-8") == "old app"
    assert (root / "runtime" / "marker.txt").read_text(encoding="utf-8") == "old runtime"
    for name in WINDOWS_LAUNCHER_FILES:
        assert (root / name).read_text(encoding="utf-8") == f"old {name}"
    assert not list(root.glob("*.previous"))
    # The undeletable copies stayed on disk instead of aborting the restore.
    assert refused
    assert (root / "runtime.failed").is_dir()
    assert "\n".join(entries).count("Deferred removal of") == len(refused)


def test_rollback_restores_every_top_level_launcher_file(tmp_path: Path) -> None:
    root = _rolled_forward_windows_install(tmp_path)
    entries: list[str] = []

    assert rollback_previous_layers(root, log=entries.append) is True

    restored = {
        name
        for name in WINDOWS_LAUNCHER_FILES
        if (root / name).read_text(encoding="utf-8") == f"old {name}"
    }
    assert restored == set(WINDOWS_LAUNCHER_FILES)
    assert f"and {len(WINDOWS_LAUNCHER_FILES)} launcher files" in "\n".join(entries)
    # Every step is on the record, not just the outcome.
    assert all(
        any(f"Restored the previous launcher file: {root / name}" in entry for entry in entries)
        for name in WINDOWS_LAUNCHER_FILES
    )


def test_one_unrestorable_launcher_file_does_not_strand_the_other_five(
    tmp_path: Path,
) -> None:
    root = _rolled_forward_windows_install(tmp_path)
    stuck = root / "msvcp140.dll"
    entries: list[str] = []

    def refuse_one(source: Path, destination: Path) -> None:
        if source == stuck.with_name(stuck.name + ".previous"):
            raise PermissionError(13, "Access is denied")
        os.replace(source, destination)

    assert rollback_previous_layers(root, renamer=refuse_one, log=entries.append) is True

    for name in WINDOWS_LAUNCHER_FILES:
        expected = "new" if name == stuck.name else "old"
        assert (root / name).read_text(encoding="utf-8") == f"{expected} {name}"
    assert any(f"Could not restore the previous launcher file {stuck}" in e for e in entries)
    # The one that could not be restored was put back, never left missing.
    assert not (root / "msvcp140.dll.failed").exists()


def test_a_layer_that_cannot_be_restored_leaves_the_installation_as_it_was(
    tmp_path: Path,
) -> None:
    """Both layers or neither: a 0.2.5 app under a 0.2.6 runtime is not a state."""

    root = _rolled_forward_windows_install(tmp_path)
    entries: list[str] = []

    def refuse_the_runtime(source: Path, destination: Path) -> None:
        if source == root / "runtime.previous":
            raise PermissionError(13, "Access is denied")
        os.replace(source, destination)

    assert rollback_previous_layers(root, renamer=refuse_the_runtime, log=entries.append) is False

    assert (root / "app" / "marker.txt").read_text(encoding="utf-8") == "new app"
    assert (root / "app.previous" / "marker.txt").read_text(encoding="utf-8") == "old app"
    assert (root / "runtime" / "marker.txt").read_text(encoding="utf-8") == "new runtime"
    assert (root / "runtime.previous" / "marker.txt").read_text(encoding="utf-8") == "old runtime"
    # The launcher files belong to the layers, so they did not move either.
    for name in WINDOWS_LAUNCHER_FILES:
        assert (root / name).read_text(encoding="utf-8") == f"new {name}"
    assert not list(root.glob("*.failed*"))
    assert any("left as the rollback found them" in entry for entry in entries)


def test_the_next_healthy_start_clears_the_deferred_failed_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where the deletion Windows refused finally happens."""

    root = _rolled_forward_windows_install(tmp_path)
    _undeletable_failed_copies(monkeypatch)
    assert rollback_previous_layers(root) is True
    leftovers = sorted(path.name for path in root.glob("*.failed*"))
    assert "runtime.failed" in leftovers

    monkeypatch.undo()
    removed = cleanup_previous_layers(root)

    assert not list(root.glob("*.failed*"))
    assert sorted(path.name for path in removed) == leftovers


def test_a_rollback_helper_waits_for_the_failed_application_before_moving_anything(
    tmp_path: Path,
) -> None:
    root = _rolled_forward_windows_install(tmp_path)
    data = tmp_path / "data"
    observed: list[str] = []
    relaunched: list[tuple[list[str], object]] = []

    def waiter(pid: int) -> bool:
        # Nothing the helper does may have happened before this returns: the
        # DLLs it has to move are still mapped until the failed app exits.
        observed.append(f"waited for {pid}")
        observed.append((root / "app" / "marker.txt").read_text(encoding="utf-8"))
        return True

    exit_code = rollback_bundle(
        bundle=root,
        data_dir=data,
        parent_pid=4321,
        relaunch_arguments=("--port", "3110"),
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, _platform, **kwargs: relaunched.append(
            (list(command), kwargs.get("environment"))
        ),
        waiter=waiter,
        environ={"PATH": "x"},
    )

    assert exit_code == 0
    assert observed == ["waited for 4321", "new app"]
    assert (root / "app" / "marker.txt").read_text(encoding="utf-8") == "old app"
    assert (root / "Waveguide Generator.exe").read_text(encoding="utf-8") == (
        "old Waveguide Generator.exe"
    )
    assert relaunched == [
        ([str(root / "Waveguide Generator.exe")], {"PATH": "x", "WG2_PORT": "3110"})
    ]
    log = (data / "logs" / "update.log").read_text(encoding="utf-8")
    assert "Rollback helper" in log
    assert "Restored the previous bundle layers" in log
    assert "Relaunched Waveguide Generator" in log


def test_a_failed_application_that_never_exits_cancels_the_rollback(tmp_path: Path) -> None:
    root = _rolled_forward_windows_install(tmp_path)
    data = tmp_path / "data"

    exit_code = rollback_bundle(
        bundle=root,
        data_dir=data,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=_unexpected_relauncher,
        waiter=lambda _pid: False,
    )

    assert exit_code == 1
    assert (root / "app" / "marker.txt").read_text(encoding="utf-8") == "new app"
    assert (root / "app.previous").is_dir()
    assert "rollback was cancelled" in (data / "logs" / "update.log").read_text(encoding="utf-8")


def test_a_rollback_that_fails_does_not_reopen_the_broken_version(tmp_path: Path) -> None:
    root = _rolled_forward_windows_install(tmp_path)
    data = tmp_path / "data"

    def refuse(source: Path, destination: Path) -> None:
        raise PermissionError(13, "Access is denied")

    exit_code = rollback_bundle(
        bundle=root,
        data_dir=data,
        parent_pid=4321,
        platform_name="win32",
        renamer=refuse,
        runner=_unexpected_runner,
        relauncher=_unexpected_relauncher,
        waiter=lambda _pid: True,
    )

    assert exit_code == 2
    assert "did not restore the previous version" in (data / "logs" / "update.log").read_text(
        encoding="utf-8"
    )


def test_the_rollback_command_line_needs_no_staged_directories(tmp_path: Path) -> None:
    arguments = build_parser().parse_args(
        [
            "--rollback",
            "--bundle",
            str(tmp_path / "Waveguide Generator"),
            "--data-dir",
            str(tmp_path / "data"),
            "--parent-pid",
            "4321",
            "--relaunch-arg=--port",
            "--relaunch-arg=3110",
        ]
    )

    assert arguments.rollback is True
    assert arguments.staged_app_dir is None
    assert arguments.relaunch_args == ["--port", "3110"]

    with pytest.raises(SystemExit):
        run_updater_cli(
            [
                "--bundle",
                "b",
                "--data-dir",
                "d",
                "--parent-pid",
                "1",
                "--rollback",
                "--staged-app-dir",
                "s",
            ]
        )
