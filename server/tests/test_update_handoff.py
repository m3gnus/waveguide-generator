"""Contracts for leaving the running venv before an installer mutates it."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from launchers.statusapp.updater import (
    BundleUpdateRequest,
    UpdateHandoffError,
    consume_update_request,
    launch_bundle_update_handoff,
    launch_update_handoff,
)


def test_request_reader_rejects_untrusted_tags_and_consumes_bad_files(tmp_path: Path) -> None:
    request = tmp_path / "update.json"
    request.write_text(json.dumps({
        "schemaVersion": 1,
        "kind": "install_release",
        "tag": "v2.0.1; touch owned",
        "readyAtEpoch": 0,
    }), encoding="utf-8")

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
    assert command[command.index("--bundle") + 1] == str(
        tmp_path / "Waveguide Generator.app"
    )
    assert options["start_new_session"] is True
