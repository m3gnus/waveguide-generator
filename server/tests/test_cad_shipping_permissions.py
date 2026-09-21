"""Filesystem trust-boundary checks for the WG-bound shipping path."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import stat
import sys
from typing import Any

import pytest

from server.cadlink import fusion_delivery, solve_command
from server.cadlink.solve_command import (
    CLAIM_PREFIX,
    SOLVE_REQUESTS_DIRECTORY,
    collect_solve_deliveries,
    ipc_folder,
)
from server.cadlink.store import CadLinkStore
from server.platform import private_paths
from server.platform.private_paths import ensure_private_directory


EXPECTED_MAX_REQUEST_BYTES = 64 * 1024


def _inbox(data_dir: Path) -> Path:
    path = ipc_folder(data_dir, create=True) / SOLVE_REQUESTS_DIRECTORY
    ensure_private_directory(path)
    return path


def _valid_request(command_id: str = "cmd-safe") -> dict[str, Any]:
    return {
        "schemaVersion": 3,
        "target": "waveguide-generator",
        "commandId": command_id,
        "operationId": command_id,
        "returnId": "",
        "bundlePath": "wgreturn/example.wgreturn",
        "manifestSha256": "sha256:" + "a" * 64,
        "requestedAt": "2026-09-21T12:00:00Z",
    }


@pytest.fixture(autouse=True)
def _fresh_reader_state():
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims"):
        getattr(solve_command, name).clear()
    yield
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims"):
        getattr(solve_command, name).clear()


def test_a_symlink_claim_is_refused_and_unlinked_without_touching_its_target(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    inbox = _inbox(data_dir)
    target = tmp_path / "outside.json"
    original = json.dumps(_valid_request())
    target.write_text(original, encoding="utf-8")
    claim = inbox / f"{CLAIM_PREFIX}linked.json"
    try:
        claim.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    store = CadLinkStore.for_data_dir(data_dir)
    refused: list[dict[str, Any]] = []
    try:
        collect_solve_deliveries(data_dir, store, refuse=refused.append)
    finally:
        store.close()

    assert not claim.exists() and not claim.is_symlink()
    assert target.read_text(encoding="utf-8") == original
    assert len(refused) == 1
    assert "symbolic link" in refused[0]["reason"]


def test_an_oversize_request_is_refused_logged_and_removed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "data"
    request = _inbox(data_dir) / "cmd-large.json"
    request.write_bytes(b"{" + b" " * EXPECTED_MAX_REQUEST_BYTES)
    store = CadLinkStore.for_data_dir(data_dir)
    refused: list[dict[str, Any]] = []
    try:
        with caplog.at_level(logging.WARNING, logger="server.cadlink.solve_command"):
            collect_solve_deliveries(data_dir, store, refuse=refused.append)
    finally:
        store.close()

    assert not request.exists()
    assert len(refused) == 1
    assert str(EXPECTED_MAX_REQUEST_BYTES) in refused[0]["reason"]
    assert "Refused the CAD Link request file" in caplog.text


def test_a_directory_with_a_request_name_is_left_alone(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    request_directory = _inbox(data_dir) / "cmd-directory.json"
    request_directory.mkdir()
    store = CadLinkStore.for_data_dir(data_dir)
    refused: list[dict[str, Any]] = []
    try:
        collect_solve_deliveries(data_dir, store, refuse=refused.append)
    finally:
        store.close()

    assert request_directory.is_dir()
    assert refused == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_fusion_delivery_creates_an_owner_only_ipc_folder(tmp_path: Path) -> None:
    folder = fusion_delivery.ipc_folder(tmp_path / "data", create=True)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_new_shipping_directories_are_owner_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = CadLinkStore.for_data_dir(data_dir)
    try:
        collect_solve_deliveries(data_dir, store)
        store.initialize()
    finally:
        store.close()

    paths = (
        data_dir / "ipc" / "wglink",
        data_dir / "ipc" / "wglink" / SOLVE_REQUESTS_DIRECTORY,
        data_dir / "db",
    )
    assert [stat.S_IMODE(path.stat().st_mode) for path in paths] == [0o700] * 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_owned_shipping_directories_with_loose_modes_are_tightened(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    paths = (
        data_dir / "ipc" / "wglink",
        data_dir / "ipc" / "wglink" / SOLVE_REQUESTS_DIRECTORY,
        data_dir / "db",
    )
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o755)

    fusion_delivery.ipc_folder(data_dir, create=True)
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o700
    paths[0].chmod(0o755)
    ipc_folder(data_dir, create=True)
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o700
    paths[0].chmod(0o755)

    store = CadLinkStore.for_data_dir(data_dir)
    try:
        collect_solve_deliveries(data_dir, store)
        store.initialize()
    finally:
        store.close()

    assert [stat.S_IMODE(path.stat().st_mode) for path in paths] == [0o700] * 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership")
def test_a_directory_not_owned_by_wg_is_not_chmodded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "foreign"
    path.mkdir(mode=0o755)
    path.chmod(0o755)
    current_uid = os.getuid()
    monkeypatch.setattr(private_paths.os, "getuid", lambda: current_uid + 1)

    with caplog.at_level(logging.WARNING, logger="wg.paths"):
        ensure_private_directory(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o755
    assert "does not own" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and modes")
def test_a_symlinked_data_folder_is_used_as_it_is(tmp_path: Path) -> None:
    """A db folder kept on another disk through a symlink must not stop WG starting."""

    real = tmp_path / "elsewhere" / "db"
    real.mkdir(parents=True, mode=0o755)
    real.chmod(0o755)
    data = tmp_path / "data"
    data.mkdir()
    (data / "db").symlink_to(real, target_is_directory=True)

    store = CadLinkStore(data / "db" / "cadlink.db")
    store.initialize()

    assert (real / "cadlink.db").is_file()
    # The user's target is left exactly as they made it.
    assert stat.S_IMODE(real.stat().st_mode) == 0o755
