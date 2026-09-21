"""Filesystem trust-boundary checks for the WG-bound shipping path."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
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
from server.app import create_app
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
    private_paths._checked_paths.clear()
    yield
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims"):
        getattr(solve_command, name).clear()
    private_paths._checked_paths.clear()


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


def test_a_regular_file_swapped_to_a_symlink_is_refused_without_reading_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"outside": "must not be read"}), encoding="utf-8")
    real_lstat = Path.lstat

    def swap_after_lstat(path: Path, *args: Any, **kwargs: Any):
        metadata = real_lstat(path, *args, **kwargs)
        if path == request and stat.S_ISREG(metadata.st_mode):
            request.unlink()
            request.symlink_to(target)
        return metadata

    monkeypatch.setattr(Path, "lstat", swap_after_lstat)
    result = solve_command._read_payload(request)

    assert isinstance(result, solve_command._UnsafePayload)
    assert "symbolic link" in result.reason


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO")
def test_a_regular_file_swapped_to_a_fifo_is_refused_without_blocking(
    tmp_path: Path,
) -> None:
    request = tmp_path / "request.json"
    source = f"""
import os
from pathlib import Path
import stat
from server.cadlink.solve_command import _read_payload, _UnsafePayload

request = Path({str(request)!r})
request.write_text('{{}}', encoding='utf-8')
real_lstat = Path.lstat
def swap_after_lstat(path, *args, **kwargs):
    metadata = real_lstat(path, *args, **kwargs)
    if path == request and stat.S_ISREG(metadata.st_mode):
        request.unlink()
        os.mkfifo(request)
    return metadata
Path.lstat = swap_after_lstat
result = _read_payload(request)
assert isinstance(result, _UnsafePayload), result
print(result.reason)
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert "not a regular file" in completed.stdout


def test_a_file_that_grows_after_fstat_is_bounded_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    real_fstat = os.fstat
    appended = False

    def grow_after_fstat(descriptor: int):
        nonlocal appended
        metadata = real_fstat(descriptor)
        if not appended:
            appended = True
            with request.open("ab") as stream:
                stream.write(b" " * (EXPECTED_MAX_REQUEST_BYTES + 1))
        return metadata

    monkeypatch.setattr(solve_command.os, "fstat", grow_after_fstat)
    result = solve_command._read_payload(request)

    assert isinstance(result, solve_command._UnsafePayload)
    assert str(EXPECTED_MAX_REQUEST_BYTES) in result.reason


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

    store = CadLinkStore.for_data_dir(data_dir)
    try:
        collect_solve_deliveries(data_dir, store)
        store.initialize()
    finally:
        store.close()

    assert [stat.S_IMODE(path.stat().st_mode) for path in paths] == [0o700] * 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_private_directory_tightening_does_not_repeat_chmod_or_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "existing"
    path.mkdir(mode=0o755)
    path.chmod(0o755)
    real_chmod = Path.chmod
    chmod_calls: list[Path] = []

    def record_chmod(candidate: Path, *args: Any, **kwargs: Any) -> None:
        if candidate == path:
            chmod_calls.append(candidate)
            raise PermissionError(errno.EACCES, "injected chmod failure")
        real_chmod(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", record_chmod)
    with caplog.at_level(logging.WARNING, logger="wg.paths"):
        ensure_private_directory(path, data_root=tmp_path)
        ensure_private_directory(path, data_root=tmp_path)

    assert chmod_calls == [path]
    assert caplog.text.count("Could not tighten permissions") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_recreated_directory_is_tightened(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    path = data_dir / "ipc" / "wglink"
    ensure_private_directory(path, parents=True, data_root=data_dir)
    path.rmdir()
    path.mkdir(mode=0o755)
    path.chmod(0o755)

    fusion_delivery.ipc_folder(data_dir, create=True)

    assert stat.S_IMODE(path.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory descriptors")
def test_parent_swap_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    inbox = _inbox(data_dir)
    original = _valid_request("original-request")
    outside_request = _valid_request("outside-request")
    (inbox / "request.json").write_text(json.dumps(original), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "request.json").write_text(
        json.dumps(outside_request), encoding="utf-8"
    )
    saved_inbox = inbox.with_name("saved-inbox")
    real_open = os.open
    swapped = False

    def swap_after_directory_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        descriptor = real_open(path, flags, *args, **kwargs)
        if not swapped and Path(path) == inbox.resolve() and flags & os.O_DIRECTORY:
            inbox.rename(saved_inbox)
            inbox.symlink_to(outside, target_is_directory=True)
            swapped = True
        return descriptor

    monkeypatch.setattr(solve_command.os, "open", swap_after_directory_open)
    store = CadLinkStore.for_data_dir(data_dir)
    try:
        collect_solve_deliveries(data_dir, store)
        assert swapped
        assert store.get_operation(original["commandId"]) is not None
        assert store.get_operation(outside_request["commandId"]) is None
    finally:
        store.close()

    assert (outside / "request.json").is_file()
    assert list(saved_inbox.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and modes")
@pytest.mark.parametrize("component", ["data", "ipc", "wglink"])
def test_an_existing_inbox_under_a_symlinked_ancestor_is_not_tightened(
    tmp_path: Path, component: str
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    data_dir = tmp_path / "data"
    if component == "data":
        data_dir.symlink_to(real, target_is_directory=True)
    elif component == "ipc":
        data_dir.mkdir()
        (data_dir / "ipc").symlink_to(real, target_is_directory=True)
    else:
        (data_dir / "ipc").mkdir(parents=True)
        (data_dir / "ipc" / "wglink").symlink_to(real, target_is_directory=True)
    inbox = data_dir / "ipc" / "wglink" / SOLVE_REQUESTS_DIRECTORY
    inbox.mkdir(parents=True, exist_ok=True)
    inbox.chmod(0o755)

    solve_command._deliveries(data_dir)

    assert stat.S_IMODE(inbox.stat().st_mode) == 0o755


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and modes")
def test_a_database_under_a_symlinked_data_root_is_not_tightened(tmp_path: Path) -> None:
    real = tmp_path / "real"
    db = real / "db"
    db.mkdir(parents=True)
    db.chmod(0o755)
    data_dir = tmp_path / "data"
    data_dir.symlink_to(real, target_is_directory=True)
    store = CadLinkStore.for_data_dir(data_dir)
    try:
        store.initialize()
    finally:
        store.close()

    assert stat.S_IMODE(db.stat().st_mode) == 0o755


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
@pytest.mark.parametrize("error", [errno.EPERM, errno.EROFS, errno.ENOTSUP])
def test_chmod_failure_does_not_abort_startup_fusion_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: int,
) -> None:
    application = create_app(data_dir=tmp_path)
    db = tmp_path / "db"
    db.mkdir(exist_ok=True)
    db.chmod(0o755)
    staged = fusion_delivery.ipc_folder(tmp_path, create=True) / fusion_delivery.HANDOFFS.directory
    staged.mkdir()
    (staged / ".pending.json.stage.tmp").write_text(
        json.dumps({"schemaVersion": 3, "operationId": "pending"}), encoding="utf-8"
    )
    real_chmod = Path.chmod

    def fail_db_chmod(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == db:
            raise OSError(error, "injected chmod failure")
        real_chmod(path, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", fail_db_chmod)
    startup = next(
        handler
        for handler in application.router.on_startup
        if handler.__name__ == "advertise_fusion_delivery_on_startup"
    )
    try:
        with caplog.at_level(logging.WARNING, logger="wg.paths"):
            asyncio.run(startup())
        assert application.state.cadlink_store.get_operation("missing") is None
        assert (db / "cadlink.db").is_file()
    finally:
        application.state.cadlink_store.close()

    assert caplog.text.count("Could not tighten permissions") == 1


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
