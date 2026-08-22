"""Transactional standalone-layer swap and relaunch contracts."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from launchers.apply_update import (
    WINDOWS_CREATE_NEW_PROCESS_GROUP,
    WINDOWS_DETACHED_PROCESS,
    apply_update,
    build_parser,
    cleanup_previous_layers,
    refresh_launcher_files,
    relaunch_application,
    relaunch_command,
    rollback_previous_layers,
    staged_launcher_files,
)


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
    relaunched: list[tuple[list[str], str]] = []

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
        relauncher=lambda command, platform_name: relaunched.append((list(command), platform_name)),
        waiter=lambda _pid: True,
    )

    assert result == 0
    assert (resources / "app" / "marker.txt").read_text() == "new app"
    assert (resources / "runtime" / "marker.txt").read_text() == "new runtime"
    assert (resources / "app.previous" / "marker.txt").read_text() == "old app"
    assert (resources / "runtime.previous" / "marker.txt").read_text() == "old runtime"
    assert [command[0] for command in commands] == ["xattr", "/usr/bin/codesign"]
    assert relaunched == [(["open", "-n", str(bundle)], "darwin")]


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
        relauncher=lambda command, _platform: relaunched.append(list(command)),
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
    assert relaunch_command(folder, "win32", arguments) == [
        str(folder / "Waveguide Generator.exe"),
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
    relaunched: list[tuple[list[str], str]] = []

    result = apply_update(
        bundle=folder,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=None,
        parent_pid=42,
        relaunch_arguments=("--port", "3110"),
        platform_name="win32",
        runner=lambda command, **_kwargs: (
            repair_commands.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
        relauncher=lambda command, platform_name: relaunched.append((list(command), platform_name)),
        waiter=lambda _pid: True,
    )

    assert result == 0
    assert repair_commands == []
    assert (folder / "app" / "marker.txt").read_text() == "new app"
    assert (folder / "app.previous" / "marker.txt").read_text() == "old app"
    assert relaunched == [([str(folder / "Waveguide Generator.exe"), "--port", "3110"], "win32")]


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


def _unexpected_relauncher(command: object, platform_name: str) -> None:
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
        relauncher=lambda command, platform_name: None,
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
        relauncher=lambda command, platform_name: None,
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
        relauncher=lambda command, platform_name: None,
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
