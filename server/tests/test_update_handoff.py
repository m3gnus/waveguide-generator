"""Contracts for leaving the running venv before an installer mutates it."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from launchers.statusapp.updater import (
    UpdateHandoffError,
    consume_update_request,
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
