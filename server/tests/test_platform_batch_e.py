from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from server.platform import instance
from server.platform.instance import InstanceAlreadyRunning, InstanceLock
from server.platform.logging_setup import MAX_LOG_BYTES, setup_logging
from server.platform.paths import data_paths, ensure_data_layout, resolve_data_dir


def test_data_dir_macos_layout(tmp_path: Path) -> None:
    root = resolve_data_dir(system="Darwin", environ={}, home=tmp_path)
    assert root == tmp_path / "Library" / "Application Support" / "WaveguideGenerator2"


def test_data_dir_windows_layout(tmp_path: Path) -> None:
    appdata = tmp_path / "Roaming"
    assert resolve_data_dir(system="Windows", environ={"APPDATA": str(appdata)}) == (
        appdata / "WaveguideGenerator2"
    )


@pytest.mark.parametrize("xdg", [None, "xdg-data"])
def test_data_dir_linux_layout(tmp_path: Path, xdg: str | None) -> None:
    environ = {} if xdg is None else {"XDG_DATA_HOME": str(tmp_path / xdg)}
    expected_parent = tmp_path / ".local" / "share" if xdg is None else tmp_path / xdg
    assert resolve_data_dir(system="Linux", environ=environ, home=tmp_path) == (
        expected_parent / "WaveguideGenerator2"
    )


def test_data_dir_override_and_subdirectories(tmp_path: Path) -> None:
    override = tmp_path / "isolated-v2"
    paths = ensure_data_layout(environ={"WG2_DATA_DIR": str(override)})
    assert paths == data_paths(override)
    assert [path.is_dir() for path in (paths.root, paths.db, paths.logs, paths.locks)] == [
        True,
        True,
        True,
        True,
    ]
    assert "Waveguide Generator" not in str(paths.root)


def test_instance_lock_acquire_conflict_and_release(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path)
    first.acquire(3100)
    try:
        with pytest.raises(InstanceAlreadyRunning) as captured:
            InstanceLock(tmp_path).acquire(3101)
        assert captured.value.info.pid == first._pid
        assert captured.value.info.port == 3100
    finally:
        first.release()
    # Advisory locks retain the metadata inode after release; a new process
    # locks and overwrites that same file instead of racing an unlink/recreate.
    assert (tmp_path / "server.pid").exists()
    replacement = InstanceLock(tmp_path)
    replacement.acquire(3101)
    replacement.release()


def test_instance_lock_replaces_stale_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / "server.pid"
    lock_path.write_text(json.dumps({"pid": 999_999_999, "port": 3100}), encoding="utf-8")
    monkeypatch.setattr(instance, "_pid_is_running", lambda _pid: False)

    lock = InstanceLock(tmp_path)
    info = lock.acquire(3102)
    try:
        assert info.port == 3102
        assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == info.pid
    finally:
        lock.release()


def test_requested_port_precedence_and_validation() -> None:
    assert instance.requested_port(environ={}) == 3100
    assert instance.requested_port(environ={"WG2_PORT": "3210"}) == 3210
    assert instance.requested_port(3220, environ={"WG2_PORT": "3210"}) == 3220
    with pytest.raises(ValueError, match="1 to 65535"):
        instance.requested_port(environ={"WG2_PORT": "not-a-port"})


def test_port_fallback_scans_next_nine(monkeypatch: pytest.MonkeyPatch) -> None:
    probed: list[int] = []

    def available(port: int, _host: str) -> bool:
        probed.append(port)
        return port == 3103

    monkeypatch.setattr(instance, "port_is_available", available)
    assert instance.select_port(3100) == 3103
    assert probed == [3100, 3101, 3102, 3103]


def test_port_fallback_fails_after_nine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instance, "port_is_available", lambda _port, _host: False)
    with pytest.raises(OSError, match="3100 through 3109"):
        instance.select_port(3100)


def test_logging_rotates_five_megabyte_file(tmp_path: Path) -> None:
    paths = ensure_data_layout(tmp_path)
    log_path = paths.logs / "server.log"
    with log_path.open("wb") as handle:
        handle.truncate(MAX_LOG_BYTES)

    setup_logging(paths)
    logging.shutdown()
    assert (paths.logs / "server.log.1").stat().st_size == MAX_LOG_BYTES
