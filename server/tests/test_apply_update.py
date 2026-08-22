"""Transactional standalone-layer swap and relaunch contracts."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from launchers import apply_update as apply_update_module
from launchers.apply_update import (
    ApplyUpdateError,
    WINDOWS_CREATE_NEW_PROCESS_GROUP,
    WINDOWS_DETACHED_PROCESS,
    apply_update,
    append_update_log,
    build_parser,
    cleanup_previous_layers,
    refresh_launcher_files,
    relaunch_application,
    relaunch_command,
    rollback_previous_layers,
    process_exists,
    staged_launcher_files,
    swap_staged_layers,
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
    assert [command[0] for command in commands] == [
        "/usr/bin/xattr",
        "/usr/bin/codesign",
        "/usr/bin/codesign",
    ]
    assert "--verify" in commands[-1]
    assert relaunched == [(["/usr/bin/open", "-n", str(bundle)], "darwin")]


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
        failure_reporter=lambda _message: None,
    )

    assert result == 2
    assert (resources / "app" / "marker.txt").read_text() == "old app"
    assert (resources / "runtime" / "marker.txt").read_text() == "old runtime"
    assert not (resources / "app.previous").exists()
    assert not (resources / "runtime.previous").exists()
    assert staged_app.is_dir() and staged_runtime.is_dir()
    assert relaunched == [["/usr/bin/open", "-n", str(bundle)]]
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

    assert relaunch_command(bundle, "darwin") == ["/usr/bin/open", "-n", str(bundle)]
    assert relaunch_command(folder, "win32") == [
        str(folder / "Waveguide Generator.exe"),
        "-m",
        "launchers.desktop",
    ]
    # open(1) passes neither env nor argv through LaunchServices, so the
    # original --port/--data-dir must travel as explicit --args.
    arguments = ("--port", "3110", "--data-dir", "/somewhere/data")
    assert relaunch_command(bundle, "darwin", arguments) == [
        "/usr/bin/open",
        "-n",
        str(bundle),
        "--args",
        *arguments,
    ]
    # A bare "--port" would be read as an interpreter option by the renamed
    # pythonw.exe, so the module must be named explicitly.
    assert relaunch_command(folder, "win32", arguments) == [
        str(folder / "Waveguide Generator.exe"),
        "-m",
        "launchers.desktop",
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
    assert relaunched == [
        (
            [
                str(folder / "Waveguide Generator.exe"),
                "-m",
                "launchers.desktop",
                "--port",
                "3110",
            ],
            "win32",
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

    relaunched: list[list[str]] = []
    exit_code = apply_update(
        bundle=root,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, _platform: relaunched.append(list(command)),
        waiter=lambda _pid: True,
        failure_reporter=lambda _message: None,
    )

    assert exit_code == 4
    assert not (tmp_path / "escaped.exe").exists()
    # The refusal restores the version that was running.
    assert (root / "app" / "marker.txt").read_text(encoding="utf-8") == "old app"
    assert (root / "runtime" / "marker.txt").read_text(encoding="utf-8") == "old runtime"
    assert (root / "Waveguide Generator.exe").read_text(encoding="utf-8") == "old launcher"
    assert relaunched == [[str(root / "Waveguide Generator.exe"), "-m", "launchers.desktop"]]


def test_a_macos_runtime_update_has_no_launcher_files_to_refresh(tmp_path: Path) -> None:
    bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    (staged_runtime / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "platform": "macos-arm64"}), encoding="utf-8"
    )

    assert refresh_launcher_files(resources) == []
    assert staged_launcher_files(staged_runtime) == []


def test_windows_process_probe_never_uses_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    probed: list[int] = []

    def destructive_kill(_pid: int, _signal: int) -> None:
        raise AssertionError("os.kill must not be reached on Windows")

    monkeypatch.setattr(apply_update_module.os, "kill", destructive_kill)

    assert process_exists(
        4321,
        platform_name="win32",
        windows_probe=lambda pid: probed.append(pid) or True,
    )
    assert probed == [4321]


@pytest.mark.parametrize(
    "name",
    ["APP", "app.previous", "file:stream", "NUL", "foo.", "foo "],
)
def test_windows_special_launcher_destinations_are_refused(
    tmp_path: Path,
    name: str,
) -> None:
    _root, _staged_app, staged_runtime = _windows_layout(
        tmp_path,
        launcher_files=[{"source": "pythonw.exe", "destination": name}],
    )

    with pytest.raises(ApplyUpdateError, match="unsafe|protected"):
        staged_launcher_files(staged_runtime)


@pytest.mark.parametrize(
    "entries",
    [
        [
            {"source": "pythonw.exe", "destination": "one.exe"},
            {"source": "PYTHONW.EXE", "destination": "two.exe"},
        ],
        [
            {"source": "pythonw.exe", "destination": "same.exe"},
            {"source": "python313.dll", "destination": "SAME.EXE"},
        ],
    ],
)
def test_case_aliasing_launcher_sources_and_destinations_are_refused(
    tmp_path: Path,
    entries: list[dict[str, str]],
) -> None:
    _root, _staged_app, staged_runtime = _windows_layout(tmp_path, launcher_files=entries)

    with pytest.raises(ApplyUpdateError, match="repeats launcher"):
        staged_launcher_files(staged_runtime)


def test_refresh_revalidates_launcher_names_at_the_write_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _staged_app, _staged_runtime = _windows_layout(tmp_path)
    monkeypatch.setattr(
        apply_update_module,
        "staged_launcher_files",
        lambda _runtime: [("pythonw.exe", "APP")],
    )

    with pytest.raises(ApplyUpdateError, match="protected"):
        refresh_launcher_files(root)

    assert (root / "app").is_dir()


def test_rollback_reverses_every_change_when_a_launcher_restore_fails(
    tmp_path: Path,
) -> None:
    root, staged_app, staged_runtime = _windows_layout(tmp_path)
    assert (
        apply_update(
            bundle=root,
            data_dir=tmp_path / "data",
            staged_app=staged_app,
            staged_runtime=staged_runtime,
            parent_pid=4321,
            platform_name="win32",
            runner=_unexpected_runner,
            relauncher=lambda _command, _platform: None,
            waiter=lambda _pid: True,
        )
        == 0
    )
    launcher_previous = root / "Waveguide Generator.exe.previous"
    launcher = root / "Waveguide Generator.exe"

    def fail_launcher_restore(source: Path, destination: Path) -> None:
        if source == launcher_previous and destination == launcher:
            raise OSError("injected sharing violation")
        source.rename(destination)

    def raising_logger(_message: str) -> None:
        raise OSError("disk full")

    assert rollback_previous_layers(root, renamer=fail_launcher_restore, log=raising_logger) is False
    assert (root / "app" / "marker.txt").read_text() == "new app"
    assert (root / "runtime" / "marker.txt").read_text() == "new runtime"
    assert launcher.read_text(encoding="utf-8") == "new launcher"
    assert launcher_previous.read_text(encoding="utf-8") == "old launcher"
    assert not list(root.glob("*.failed"))


def test_rollback_requires_both_live_layers_before_reporting_success(tmp_path: Path) -> None:
    resources = tmp_path / "bundle"
    app = resources / "app"
    previous = resources / "app.previous"
    app.mkdir(parents=True)
    previous.mkdir()
    (app / "marker").write_text("new", encoding="utf-8")
    (previous / "marker").write_text("old", encoding="utf-8")

    assert rollback_previous_layers(resources) is False
    assert (app / "marker").read_text(encoding="utf-8") == "new"
    assert (previous / "marker").read_text(encoding="utf-8") == "old"


def test_raising_logger_cannot_suppress_launcher_failure_rollback(tmp_path: Path) -> None:
    root, staged_app, staged_runtime = _windows_layout(
        tmp_path,
        launcher_files=[{"source": "pythonw.exe", "destination": "APP"}],
    )
    relaunched: list[list[str]] = []

    def raising_logger(_message: str) -> None:
        raise OSError("disk full")

    exit_code = apply_update(
        bundle=root,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, _platform: relaunched.append(list(command)),
        waiter=lambda _pid: True,
        logger=raising_logger,
        failure_reporter=raising_logger,
    )

    assert exit_code == 4
    assert (root / "app" / "marker.txt").read_text() == "old app"
    assert (root / "runtime" / "marker.txt").read_text() == "old runtime"
    assert (root / "Waveguide Generator.exe").read_text() == "old launcher"
    assert relaunched == [[str(root / "Waveguide Generator.exe"), "-m", "launchers.desktop"]]


def test_append_update_log_is_no_throw_when_the_log_directory_cannot_exist(
    tmp_path: Path,
) -> None:
    data_file = tmp_path / "not-a-directory"
    data_file.write_text("occupied", encoding="utf-8")

    append_update_log(data_file, "must not raise")


def test_post_mutation_logger_failure_does_not_change_a_successful_update(
    tmp_path: Path,
) -> None:
    root, staged_app, staged_runtime = _windows_layout(tmp_path)
    relaunched: list[list[str]] = []

    def raising_logger(_message: str) -> None:
        raise OSError("disk full")

    exit_code = apply_update(
        bundle=root,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        platform_name="win32",
        runner=_unexpected_runner,
        relauncher=lambda command, _platform: relaunched.append(list(command)),
        waiter=lambda _pid: True,
        logger=raising_logger,
    )

    assert exit_code == 0
    assert (root / "app" / "marker.txt").read_text() == "new app"
    assert (root / "runtime" / "marker.txt").read_text() == "new runtime"
    assert relaunched == [[str(root / "Waveguide Generator.exe"), "-m", "launchers.desktop"]]


def test_runtime_is_swapped_before_app_to_reduce_the_mixed_generation_window(
    tmp_path: Path,
) -> None:
    _bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    moves: list[tuple[str, str]] = []

    def record(source: Path, destination: Path) -> None:
        moves.append((source.name, destination.name))
        source.rename(destination)

    swap_staged_layers(resources, staged_app, staged_runtime, renamer=record)

    assert moves == [
        ("runtime", "runtime.previous"),
        ("runtime", "runtime"),
        ("app", "app.previous"),
        ("app", "app"),
    ]


def test_failed_required_codesign_rolls_back_resigns_and_reopens_old_bundle(
    tmp_path: Path,
) -> None:
    bundle, resources, staged_app, staged_runtime = _layout(tmp_path)
    commands: list[list[str]] = []
    failed_sign = False

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal failed_sign
        commands.append(command)
        if "--sign" in command and not failed_sign:
            failed_sign = True
            return subprocess.CompletedProcess(command, 1, "", "injected signing failure")
        return subprocess.CompletedProcess(command, 0, "", "")

    relaunched: list[list[str]] = []
    reported: list[str] = []
    exit_code = apply_update(
        bundle=bundle,
        data_dir=tmp_path / "data",
        staged_app=staged_app,
        staged_runtime=staged_runtime,
        parent_pid=123,
        platform_name="darwin",
        runner=run,
        relauncher=lambda command, _platform: relaunched.append(list(command)),
        waiter=lambda _pid: True,
        failure_reporter=reported.append,
    )

    assert exit_code == 5
    assert (resources / "app" / "marker.txt").read_text() == "old app"
    assert (resources / "runtime" / "marker.txt").read_text() == "old runtime"
    assert not list(resources.glob("*.previous"))
    assert ["--verify" in command for command in commands].count(True) == 1
    assert relaunched == [["/usr/bin/open", "-n", str(bundle)]]
    assert "previous version was restored and reopened" in reported[0]
