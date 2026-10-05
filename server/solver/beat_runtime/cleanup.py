"""Authenticate live hosts before IPC shutdown; never signal recorded PIDs."""

from __future__ import annotations

import errno
import socket
import stat
import time
from pathlib import Path
from typing import Any

from . import paths
from .ipc import Endpoint, receive_frame, remaining_time, send_frame, validate_identifier
from .registry import (
    HostRecord, RecordRefused, SpawnLock, hello_message, pid_alive, process_start_identity,
    read_record, record_path, spawn_lock_path, unlink_record, validate_hello, validate_record,
)


def connect_authenticated(
    record: HostRecord, expected_key: dict[str, Any], directory: Path | None = None,
    *, timeout: float = 2.0, deadline: float | None = None,
) -> socket.socket:
    """Verify a fresh host HMAC within one deadline; never transmit the token."""
    validate_record(record, expected_key, directory)
    deadline = time.monotonic() + timeout if deadline is None else deadline
    try:
        connection = record.endpoint.connect(remaining_time(deadline))
    except OSError as exc:
        raise ConnectionError(exc.errno, f"Host endpoint unavailable: {exc}") from exc
    try:
        message = hello_message(record)
        connection.settimeout(remaining_time(deadline))
        send_frame(connection, message)
        validate_hello(record, receive_frame(connection, deadline=deadline), message["nonce"])
        return connection
    except (OSError, ValueError) as exc:
        connection.close()
        raise RecordRefused(f"Unverified live host {record.identifier} refused: {exc}") from exc


def _same_process(record: HostRecord) -> bool:
    if not pid_alive(record.pid):
        return False
    start = process_start_identity(record.pid)
    # An unavailable identity is uncertainty, never authority to prune.
    return start is None or start == record.pid_start


def _require_lock(lock: SpawnLock, identifier: str, directory: Path | None) -> None:
    if not lock.held or lock.path.absolute() != spawn_lock_path(identifier, directory):
        raise RecordRefused("Cleanup requires this slot's held spawn lock")


def cleanup_host(
    record: HostRecord, expected_key: dict[str, Any], directory: Path | None = None,
    *, timeout: float = 2.0, lock: SpawnLock | None = None,
) -> bool:
    """Prune a dead matching record or shut down an authenticated live host.

    Pass a held slot SpawnLock to reuse spawn exclusion; otherwise this wrapper
    acquires it and raises LockBusy on contention timeout. One deadline bounds
    authentication, shutdown and exit. Unverified live/foreign/successor records
    remain. Dead/reused PIDs permit pruning only after refused/missing endpoints.
    """
    validate_record(record, expected_key, directory)
    deadline = time.monotonic() + timeout
    if lock is not None:
        _require_lock(lock, record.identifier, directory)
        return _cleanup_locked(record, expected_key, directory, deadline)
    with SpawnLock(spawn_lock_path(record.identifier, directory), timeout=timeout):
        return _cleanup_locked(record, expected_key, directory, deadline)


def _cleanup_locked(
    record: HostRecord, expected_key: dict[str, Any], directory: Path | None, deadline: float,
) -> bool:
    current = read_record(record.identifier, directory)
    if current is None:
        return False
    if current.as_dict() != record.as_dict():
        raise RecordRefused("Host record changed while waiting for spawn exclusion")
    try:
        connection = connect_authenticated(record, expected_key, directory, deadline=deadline)
    except ConnectionError as exc:
        if exc.errno not in {errno.ENOENT, errno.ECONNREFUSED} or _same_process(record):
            raise RecordRefused(f"Unverified live host refused: {exc}") from exc
        connection = None
    except RecordRefused:
        # Something else answers a recorded loopback port (port reuse after the
        # host died). The recorded host provably no longer exists, so prune;
        # nothing is signalled. A live or unidentifiable host stays refused.
        if _same_process(record):
            raise
        connection = None
    if connection is not None:
        with connection:
            try:
                message = hello_message(record, operation="shutdown")
                connection.settimeout(remaining_time(deadline))
                send_frame(connection, message)
                validate_hello(record, receive_frame(connection, deadline=deadline), message["nonce"],
                               operation="shutdown")
            except (OSError, ValueError) as exc:
                raise RecordRefused(f"Host shutdown refused: {exc}") from exc
        while _same_process(record):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecordRefused("Authenticated host still live; record retained")
            time.sleep(min(0.02, remaining))
    current = read_record(record.identifier, directory)
    if current is None:
        return True
    if current.as_dict() != record.as_dict():
        raise RecordRefused("Successor host record retained")
    if record.endpoint.kind == "unix":
        _remove_socket(record.endpoint.path)
    unlink_record(record_path(record.identifier, directory))
    return True


def _remove_socket(path: Path) -> None:
    paths.checked_root(path.parent)
    try:
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise RecordRefused("Endpoint is not a socket; retained")
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass


def sweep_orphan_socket(
    identifier: str, directory: Path | None = None, *, timeout: float = 2.0,
    lock: SpawnLock | None = None,
) -> bool:
    """Under spawn exclusion, unlink only a refused Unix socket without a record."""
    validate_identifier(identifier)
    root = paths.worker_dir() if directory is None else paths.checked_root(directory).absolute()
    if lock is None:
        with SpawnLock(spawn_lock_path(identifier, root), timeout=timeout) as held:
            return sweep_orphan_socket(identifier, root, timeout=timeout, lock=held)
    _require_lock(lock, identifier, root)
    if read_record(identifier, root) is not None:
        raise RecordRefused("Socket has a host record; authenticated cleanup required")
    path = root / f"{identifier}.sock"
    try:
        before = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(before.st_mode):
        raise RecordRefused("Orphan endpoint is not a socket; retained")
    try:
        connection = Endpoint("unix", path=path).connect(timeout)
    except OSError as exc:
        if exc.errno not in {errno.ENOENT, errno.ECONNREFUSED}:
            raise RecordRefused(f"Orphan endpoint is unverified: {exc}") from exc
    else:
        connection.close()
        raise RecordRefused("Orphan endpoint is listening; retained")
    try:
        after = path.lstat()
    except FileNotFoundError:
        return True
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise RecordRefused("Orphan endpoint changed; retained")
    _remove_socket(path)
    return True
