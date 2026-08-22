"""Contracts for leaving the running venv before an installer mutates it."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from launchers.statusapp.updater import (
    WINDOWS_CREATE_NEW_PROCESS_GROUP,
    WINDOWS_CREATE_NO_WINDOW,
    WINDOWS_DETACHED_PROCESS,
    BundleUpdateRequest,
    UpdateHandoffError,
    consume_update_request,
    launch_bundle_update_handoff,
    launch_rollback_handoff,
    launch_update_handoff,
    rollback_interpreter,
    rollback_renamed_directories,
)


def test_request_reader_rejects_untrusted_tags_and_consumes_bad_files(tmp_path: Path) -> None:
    request = tmp_path / "update.json"
    request.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "install_release",
                "tag": "v2.0.1; touch owned",
                "readyAtEpoch": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(UpdateHandoffError, match="invalid"):
        consume_update_request(request, now=1)

    assert not request.exists()


def test_handoff_refuses_an_invalid_tag_before_starting_any_process(tmp_path: Path) -> None:
    with pytest.raises(UpdateHandoffError, match="invalid"):
        launch_update_handoff(
            tmp_path,
            "v2.0.1 & owned",
            123,
            environ={},
            platform_name="darwin",
        )


def test_macos_handoff_opens_a_waiting_terminal_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = tmp_path / "installers" / "macos" / "install-wg.command"
    installer.parent.mkdir(parents=True)
    installer.write_text("#!/bin/bash\n", encoding="utf-8")
    opened: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        opened.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    launch_update_handoff(
        tmp_path,
        "v2.0.1",
        4321,
        environ={},
        platform_name="darwin",
    )

    assert opened and opened[0][0] == "open"
    helper = Path(opened[0][1])
    script = helper.read_text(encoding="utf-8")
    assert "parent_pid=4321" in script
    assert "--tag v2.0.1" in script
    assert str(installer) in script
    helper.unlink()


def test_windows_handoff_uses_a_separate_console_and_fixed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = tmp_path / "installers" / "windows" / "install-and-update.bat"
    installer.parent.mkdir(parents=True)
    installer.write_text("@echo off\r\n", encoding="utf-8")
    started: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command: list[str], **kwargs: object) -> object:
        started.append((command, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    launch_update_handoff(
        tmp_path,
        "v2.0.1",
        4321,
        environ={"COMSPEC": "cmd.exe"},
        server_args=("--data-dir", str(tmp_path / "custom data")),
        platform_name="win32",
    )

    command, options = started[0]
    assert command[:4] == ["cmd.exe", "/d", "/c", "call"]
    assert Path(command[4]).suffix == ".bat"
    environment = options["env"]
    assert isinstance(environment, dict)
    assert environment["WG_UPDATE_PARENT_PID"] == "4321"
    assert environment["WG_UPDATE_INSTALLER"] == str(installer)
    assert environment["WG_UPDATE_TAG"] == "v2.0.1"
    assert environment["WG2_DATA_DIR"] == str((tmp_path / "custom data").resolve())
    Path(command[4]).unlink()


def test_bundle_request_accepts_only_existing_staged_paths_inside_the_data_dir(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    staged_app = data / "updates" / "2.0.1" / "staged" / "app"
    staged_app.mkdir(parents=True)
    request = tmp_path / "update.json"
    request.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "apply_bundle",
                "version": "2.0.1",
                "stagedAppDir": str(staged_app),
                "stagedRuntimeDir": None,
            }
        ),
        encoding="utf-8",
    )

    consumed = consume_update_request(request, data_dir=data)

    assert consumed == BundleUpdateRequest("2.0.1", staged_app.resolve(), None)
    assert not request.exists()

    request.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "apply_bundle",
                "version": "2.0.1",
                "stagedAppDir": str(tmp_path / "outside"),
                "stagedRuntimeDir": None,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(UpdateHandoffError, match="invalid"):
        consume_update_request(request, data_dir=data)
    assert not request.exists()


def test_bundle_handoff_uses_the_staged_updater_and_new_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    app_layer = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources" / "app"
    app_layer.mkdir(parents=True)
    staged = data / "updates" / "2.0.1" / "staged"
    staged_app = staged / "app"
    staged_runtime = staged / "runtime"
    (staged_app / "launchers").mkdir(parents=True)
    (staged_app / "launchers" / "apply_update.py").write_text("# updater\n", encoding="utf-8")
    (staged_runtime / "bin").mkdir(parents=True)
    python = staged_runtime / "bin" / "python3.13"
    python.write_text("python\n", encoding="utf-8")
    started: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command: list[str], **options: object) -> object:
        started.append((command, options))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    launch_bundle_update_handoff(
        app_layer,
        BundleUpdateRequest("2.0.1", staged_app.resolve(), staged_runtime.resolve()),
        4321,
        environ={"WG2_DATA_DIR": str(data)},
        server_args=("--port", "3110"),
        platform_name="darwin",
    )

    command, options = started[0]
    assert command[0] == str(python)
    assert command[-2:] == ["--relaunch-arg=--port", "--relaunch-arg=3110"]
    assert command[1] == str(staged_app / "launchers" / "apply_update.py")
    assert command[command.index("--parent-pid") + 1] == "4321"
    assert command[command.index("--bundle") + 1] == str(tmp_path / "Waveguide Generator.app")
    assert options["start_new_session"] is True


def test_windows_bundle_handoff_runs_from_staged_runtime_before_ntfs_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    app_layer = tmp_path / "Waveguide Generator" / "app"
    app_layer.mkdir(parents=True)
    staged = data / "updates" / "2.0.1" / "staged"
    staged_app = staged / "app"
    staged_runtime = staged / "runtime"
    (staged_app / "launchers").mkdir(parents=True)
    (staged_app / "launchers" / "apply_update.py").write_text("# updater\n", encoding="utf-8")
    staged_runtime.mkdir(parents=True)
    python = staged_runtime / "python.exe"
    python.write_bytes(b"python")
    started: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **options: started.append((command, options)) or object(),
    )

    launch_bundle_update_handoff(
        app_layer,
        BundleUpdateRequest("2.0.1", staged_app.resolve(), staged_runtime.resolve()),
        4321,
        environ={"WG2_DATA_DIR": str(data)},
        platform_name="win32",
    )

    command, options = started[0]
    assert command[0] == str(python)
    assert command[1] == str(staged_app / "launchers" / "apply_update.py")
    assert command[command.index("--bundle") + 1] == str(tmp_path / "Waveguide Generator")
    assert command[command.index("--staged-runtime-dir") + 1] == str(staged_runtime)
    assert options["creationflags"] != 0
    assert "start_new_session" not in options
    # Never the staged app directory. Windows keeps an open handle on a
    # process's current directory, and this process exists precisely to rename
    # that directory into place; starting it there makes the swap fail with
    # WinError 32 and roll straight back.
    assert options["cwd"] == str(data.resolve())
    assert options["cwd"] != str(staged_app)


def _windows_rollback_installation(tmp_path: Path) -> tuple[Path, Path]:
    """A Windows folder that has rolled forward, plus its data directory."""

    bundle = tmp_path / "Waveguide Generator"
    for name in ("app", "app.previous", "runtime", "runtime.previous"):
        (bundle / name).mkdir(parents=True)
    # The staged runtime the updater itself ran from is gone: the swap moved it
    # into place as ``runtime``. This is the only interpreter left outside the
    # directories the rollback renames.
    (bundle / "Waveguide Generator.exe").write_bytes(b"pythonw")
    (bundle / "runtime" / "python.exe").write_bytes(b"python")
    (bundle / "runtime.previous" / "python.exe").write_bytes(b"python")
    data = tmp_path / "data"
    data.mkdir()
    return bundle, data


def test_windows_rollback_handoff_runs_outside_every_directory_it_renames(
    tmp_path: Path,
) -> None:
    """The failed application cannot roll itself back, so it hands off.

    Windows permits renaming a directory whose DLLs are mapped -- verified on a
    real machine -- but not deleting one, and the process that has them mapped
    is the one whose exit releases them. So the restore moves to an independent
    process that starts only after that exit, and it must not stand on anything
    it moves: not its interpreter, not its script, not its working directory.
    """

    bundle, data = _windows_rollback_installation(tmp_path)
    source = tmp_path / "source" / "apply_update.py"
    source.parent.mkdir()
    source.write_text("# the standalone updater\n", encoding="utf-8")
    started: list[tuple[list[str], dict[str, object]]] = []

    command = launch_rollback_handoff(
        bundle,
        data,
        4321,
        environ={"WG2_DATA_DIR": str(data)},
        server_args=("--port", "3110"),
        platform_name="win32",
        source_script=source,
        process_factory=lambda command, **options: started.append((command, options)) or object(),
    )

    spawned, options = started[0]
    assert spawned == command
    # The renamed pythonw.exe at the bundle root: outside runtime and
    # runtime.previous, and the bundle root itself is never renamed.
    assert command[0] == str(bundle / "Waveguide Generator.exe")
    # The script is a copy in the data directory, because the original lives in
    # the ``app`` layer this helper renames.
    script = data / "rollback" / "apply_update.py"
    assert command[1] == str(script)
    assert script.read_text(encoding="utf-8") == "# the standalone updater\n"
    assert "--rollback" in command
    assert command[command.index("--bundle") + 1] == str(bundle)
    assert command[command.index("--parent-pid") + 1] == "4321"
    assert command[-2:] == ["--relaunch-arg=--port", "--relaunch-arg=3110"]

    # Never inside the bundle: Windows keeps an open handle on a process's
    # current directory, and every directory in there is about to move.
    assert options["cwd"] == str(data)
    for directory in rollback_renamed_directories(bundle, "win32"):
        assert not Path(options["cwd"]).is_relative_to(directory)
        assert not Path(command[0]).is_relative_to(directory)
        assert not Path(command[1]).is_relative_to(directory)

    creationflags = options["creationflags"]
    assert isinstance(creationflags, int)
    assert creationflags & WINDOWS_DETACHED_PROCESS
    assert creationflags & WINDOWS_CREATE_NEW_PROCESS_GROUP
    assert creationflags & WINDOWS_CREATE_NO_WINDOW
    assert "start_new_session" not in options
    assert options["stdin"] is subprocess.DEVNULL
    assert options["close_fds"] is True
    assert (data / "logs" / "rollback-handoff.log").is_file()


def test_the_rollback_helper_refuses_an_interpreter_inside_a_renamed_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard, not the comment, is what keeps this true.

    A future runtime layout that put the launcher back inside ``runtime`` would
    reintroduce exactly the bug this handoff exists to remove, silently.
    """

    bundle, _data = _windows_rollback_installation(tmp_path)
    monkeypatch.setattr(sys, "executable", str(bundle / "runtime.previous" / "python.exe"))

    with pytest.raises(UpdateHandoffError, match="directory the rollback renames"):
        rollback_interpreter(bundle, "linux")

    monkeypatch.setattr(sys, "executable", str(tmp_path / "outside" / "python"))
    with pytest.raises(UpdateHandoffError, match="interpreter is missing"):
        rollback_interpreter(bundle, "linux")

    assert rollback_interpreter(bundle, "win32") == (bundle / "Waveguide Generator.exe").resolve()


def test_the_rollback_helper_refuses_a_data_directory_inside_the_bundle(
    tmp_path: Path,
) -> None:
    bundle, _data = _windows_rollback_installation(tmp_path)
    inside = bundle / "data"
    inside.mkdir()

    with pytest.raises(UpdateHandoffError, match="inside the bundle"):
        launch_rollback_handoff(
            bundle,
            inside,
            4321,
            environ={},
            platform_name="win32",
            process_factory=lambda *_args, **_kwargs: pytest.fail("nothing may start"),
        )


def test_a_rollback_handoff_that_cannot_start_is_reported_not_swallowed(
    tmp_path: Path,
) -> None:
    bundle, data = _windows_rollback_installation(tmp_path)
    source = tmp_path / "apply_update.py"
    source.write_text("# updater\n", encoding="utf-8")

    def refuse(command: list[str], **_options: object) -> object:
        raise OSError(8, "Not enough memory resources are available")

    with pytest.raises(UpdateHandoffError, match="Could not start the rollback helper"):
        launch_rollback_handoff(
            bundle,
            data,
            4321,
            environ={},
            platform_name="win32",
            source_script=source,
            process_factory=refuse,
        )
