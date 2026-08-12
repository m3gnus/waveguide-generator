from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from scripts import run_update_guard
from server.platform.instance import DEFAULT_PORT, InstanceLock
from server.platform.paths import ensure_data_layout


def test_update_guard_refuses_to_run_while_server_lock_is_held(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("WG2_DATA_DIR", str(data_dir))
    paths = ensure_data_layout(data_dir)
    owner = InstanceLock(paths.locks)
    owner.acquire(DEFAULT_PORT)
    marker = tmp_path / "ran"
    try:
        result = run_update_guard.main(
            ["--", sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
        )
    finally:
        owner.release()

    assert result == 2
    assert not marker.exists()
    assert "must be closed" in capsys.readouterr().err


def test_update_guard_releases_lock_before_launch(
    tmp_path: Path, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("WG2_DATA_DIR", str(data_dir))
    launched = tmp_path / "launched"
    # The guard runs the launcher path directly, so it has to be something this
    # OS can execute on its own. Windows honours neither the shebang nor the
    # executable bit, and the launcher it ships there is not a shell script.
    if sys.platform == "win32":
        launcher = tmp_path / "launcher.bat"
        launcher.write_text(f'@echo off\r\ntype nul > "{launched}"\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "launcher.sh"
        launcher.write_text(f"#!/bin/sh\ntouch '{launched}'\n", encoding="utf-8")
        launcher.chmod(0o755)

    original_run = subprocess.run

    def checked_run(command, **kwargs):
        if command == [str(launcher)]:
            probe = InstanceLock(ensure_data_layout(data_dir).locks)
            probe.acquire(DEFAULT_PORT)
            probe.release()
        return original_run(command, **kwargs)

    monkeypatch.setattr(run_update_guard.subprocess, "run", checked_run)
    assert run_update_guard.main(
        ["--launch", str(launcher), "--", sys.executable, "-c", "pass"]
    ) == 0
    assert launched.exists()
