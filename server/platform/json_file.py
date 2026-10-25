"""Atomic publication of small JSON records."""

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable
import uuid


def json_bytes(
    payload: Any, *, indent: int | None = 2, sort_keys: bool = False,
    ensure_ascii: bool = True, allow_nan: bool = True,
    trailing_newline: bool = True, newline: str | None = "\n",
    default: Callable[[Any], Any] | None = None,
) -> bytes:
    """The exact bytes ``write_json_atomic`` publishes, on every platform.

    The default uses LF for callers that journal or verify these bytes by
    checksum. ``newline=None`` preserves native text-mode newlines for legacy
    formats; those bytes must not be used as the default checksum profile.
    """

    text = json.dumps(payload, indent=indent, sort_keys=sort_keys,
                      ensure_ascii=ensure_ascii, allow_nan=allow_nan, default=default)
    if trailing_newline:
        text += "\n"
    return text.replace("\n", os.linesep if newline is None else newline).encode("utf-8")


def sync_directory(directory: Path, *, strict: bool = False) -> None:
    """Flush a folder's entries to disk (POSIX), so a rename outlives a power cut."""

    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        if strict:
            raise
        return
    try:
        os.fsync(descriptor)
    except OSError:
        if strict:
            raise
    finally:
        os.close(descriptor)


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    replace_attempts: int = 1,
    retry_seconds: float | Callable[[int], float] = 0.0,
    durable_directory: bool = False,
    encode: Callable[[Any], bytes] = json_bytes,
    sync_file: bool = True,
    temporary_prefix: str | None = None,
    temporary_suffix: str = "",
    creation_mode: int = 0o600,
    before_replace: Callable[[Path], bool | None] | None = None,
    replace_failed: Callable[[Exception], bool] | None = None,
    retryable: Callable[[OSError], bool] | None = None,
) -> bool:
    """Publish a small cross-process setting without exposing a torn file.

    ``replace_attempts`` retries the final replace while another process holds
    the target (Windows raises ``PermissionError`` while a reader has it open),
    ``retry_seconds`` apart; a record another process polls needs that.
    ``durable_directory`` also flushes the folder after the replace, so the new
    name outlives a power cut. Raises ``OSError`` when the record could not be
    published; its staged file is removed then, unless the removal fails too.

    ``encode`` preserves a caller's existing JSON wire format (or captured JSON
    bytes). ``sync_file=False`` preserves callers that only need atomic visibility.
    A ``before_replace`` hook runs after the staged file is closed and flushed;
    returning False cancels publication. On failure, ``replace_failed`` may return
    True to retain the staged JSON as recovery evidence. Neither hook owns retries.
    A successful publication returns True; a cancelled one returns False.
    Temporary naming options are for recovery scanners, not visible file names.
    ``creation_mode`` is filtered by the process umask, as with normal file
    creation; records are private unless their existing reader contract differs.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if creation_mode == 0o600:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=temporary_prefix if temporary_prefix is not None else f".{path.name}.",
            suffix=temporary_suffix, dir=path.parent,
        )
    else:
        prefix = temporary_prefix if temporary_prefix is not None else f".{path.name}."
        temporary_name = path.parent / f"{prefix}{uuid.uuid4().hex}{temporary_suffix}"
        descriptor = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, creation_mode)
    temporary = Path(temporary_name)
    replaced = False
    keep_for_recovery = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encode(payload))
            stream.flush()
            if sync_file:
                os.fsync(stream.fileno())
        if before_replace is not None and before_replace(temporary) is False:
            return False
        for attempt in range(max(1, replace_attempts)):
            try:
                os.replace(temporary, path)
                replaced = True
                break
            except OSError as exc:
                can_retry = retryable(exc) if retryable is not None else isinstance(exc, PermissionError)
                if not can_retry or attempt + 1 >= replace_attempts:
                    raise
                time.sleep(retry_seconds(attempt) if callable(retry_seconds) else retry_seconds)
        if durable_directory:
            sync_directory(path.parent)
        return True
    except Exception as exc:
        if replace_failed is not None:
            keep_for_recovery = replace_failed(exc)
        raise
    finally:
        if not replaced and not keep_for_recovery:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # a staged file left behind is named with a leading "."; it is never read
