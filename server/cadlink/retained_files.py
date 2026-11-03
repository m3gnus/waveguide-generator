"""Checksum verification shared by retained CAD downloads and exports."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Any, BinaryIO, Callable, Mapping


def copy_verified_member(
    path: Path, evidence: Mapping[str, Any], *, copy_to: BinaryIO | None = None,
    checksum_out: list[str] | None = None, cancel_cb: Callable[[], None] = lambda: None,
) -> bool:
    """Copy and hash the same regular-file handle; never reopen verified bytes."""

    size = evidence.get("size_bytes")
    if not evidence.get("sha256") or (size is not None and (type(size) is not int or size < 0)):
        return False
    checksum = hashlib.sha256()
    copied = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        file_stat = os.fstat(stream.fileno())
        if not stat.S_ISREG(file_stat.st_mode) or (size is not None and file_stat.st_size != size):
            return False
        # A pruned bundle leaves its admitted checksum in the solve request,
        # but may leave no size. Bound that read by the opened file's size.
        limit = size if size is not None else file_stat.st_size
        while True:
            cancel_cb()
            chunk = stream.read(min(1024 * 1024, limit - copied + 1))
            if not chunk:
                break
            copied += len(chunk)
            if copied > limit:
                return False
            checksum.update(chunk)
            if copy_to is not None:
                copy_to.write(chunk)
    actual = "sha256:" + checksum.hexdigest()
    verified = copied == limit and actual == evidence["sha256"]
    if verified and checksum_out is not None:
        checksum_out.append(actual)
    return verified
