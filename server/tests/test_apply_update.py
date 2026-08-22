"""Transactional standalone-layer swap and relaunch contracts."""

from __future__ import annotations

from pathlib import Path
import subprocess

from launchers.apply_update import (
    apply_update,
    build_parser,
    cleanup_previous_layers,
    relaunch_command,
    rollback_previous_layers,
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
        relauncher=lambda command, platform_name: relaunched.append(
            (list(command), platform_name)
        ),
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
    assert relaunch_command(folder, "win32") == [
        str(folder / "Waveguide Generator.exe")
    ]
    # open(1) passes neither env nor argv through LaunchServices, so the
    # original --port/--data-dir must travel as explicit --args.
    arguments = ("--port", "3110", "--data-dir", "/somewhere/data")
    assert relaunch_command(bundle, "darwin", arguments) == [
        "open", "-n", str(bundle), "--args", *arguments
    ]
    assert relaunch_command(folder, "win32", arguments) == [
        str(folder / "Waveguide Generator.exe"), *arguments
    ]
    assert build_parser().parse_args(
        [
            "--bundle", str(bundle), "--data-dir", "d", "--staged-app-dir", "s",
            "--parent-pid", "1", "--relaunch-arg=--port", "--relaunch-arg=3110",
        ]
    ).relaunch_args == ["--port", "3110"]
