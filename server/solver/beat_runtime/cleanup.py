"""Authenticate live hosts before IPC shutdown; never signal recorded PIDs."""

from __future__ import annotations

import socket
import stat
import time
from pathlib import Path
from typing import Any

from .ipc import receive_frame, send_frame
from .registry import (
    HostRecord, RecordRefused, SpawnLock, hello_message, pid_alive, read_record,
    record_path, spawn_lock_path, validate_hello, validate_record,
)


def connect_authenticated(
    record: HostRecord, expected_key: dict[str, Any], directory: Path | None = None,
    *, timeout: float = 2.0,
) -> socket.socket:
    """Return a connection only after provider/key/token/host-PID verification."""
    validate_record(record, expected_key, directory)
    try:
        connection = record.endpoint.connect(timeout)
    except OSError as exc:
        raise ConnectionError(f"Host endpoint unavailable: {exc}") from exc
    try:
        send_frame(connection, hello_message(record))
        validate_hello(record, receive_frame(connection))
        return connection
    except (OSError, ValueError) as exc:
        connection.close()
        raise RecordRefused(f"Unverified live host {record.identifier} refused: {exc}") from exc


def cleanup_host(
    record: HostRecord, expected_key: dict[str, Any], directory: Path | None = None,
    *, timeout: float = 2.0,
) -> bool:
    """Prune a dead matching record or shut down an authenticated live host.

    Retain unverified live, foreign and successor records and report refusal.
    Lock covers recheck, authentication, shutdown and removal. The future host
    must report hello identity, acknowledge shutdown and exit within timeout.
    """
    validate_record(record, expected_key, directory)
    with SpawnLock(spawn_lock_path(record.identifier, directory), timeout=timeout):
        current = read_record(record.identifier, directory)
        if current is None:
            return False
        if current.as_dict() != record.as_dict():
            raise RecordRefused("Host record changed while waiting for spawn exclusion")
        try:
            connection = connect_authenticated(record, expected_key, directory, timeout=timeout)
        except ConnectionError as exc:
            if pid_alive(record.pid):
                raise RecordRefused(f"Unverified live host refused: {exc}") from exc
            connection = None
        if connection is not None:
            with connection:
                try:
                    send_frame(connection, {"op": "shutdown"})
                    reply = receive_frame(connection)
                    if reply is None or reply.get("type") != "shutdown_ok":
                        raise RecordRefused("Authenticated host did not acknowledge shutdown")
                except (OSError, ValueError) as exc:
                    raise RecordRefused(f"Host shutdown refused: {exc}") from exc
            deadline = time.monotonic() + timeout
            while pid_alive(record.pid):
                if time.monotonic() >= deadline:
                    raise RecordRefused("Authenticated host still live; record retained")
                time.sleep(0.02)
        # Host may have removed its own record, or a successor may be present.
        current = read_record(record.identifier, directory)
        if current is None:
            return True
        if current.as_dict() != record.as_dict():
            raise RecordRefused("Successor host record retained")
        if record.endpoint.kind == "unix":
            endpoint = record.endpoint.path
            try:
                if not stat.S_ISSOCK(endpoint.lstat().st_mode):
                    raise RecordRefused("Endpoint is not a socket; retained")
                endpoint.unlink()
            except FileNotFoundError:
                pass
        record_path(record.identifier, directory).unlink()
        return True
