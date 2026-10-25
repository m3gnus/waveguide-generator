"""Atomic publication of small JSON records."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Literal
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


@contextmanager
def staged_json(
    path: Path,
    payload: Any,
    *,
    replace_attempts: int = 1,
    retry_seconds: float | Sequence[float] = 0.0,
    durable_directory: bool | Literal["strict"] = False,
    encode: Callable[[Any], bytes] = json_bytes,
    sync_file: bool = True,
    temporary_prefix: str | None = None,
    temporary_suffix: str = "",
    creation_mode: int = 0o600,
    before_replace: Callable[[Path], bool | None] | None = None,
    replace_failed: Callable[[Exception], bool] | None = None,
    cleanup_staged: Callable[[Path], None] | None = None,
    create_parent: bool = True,
) -> Iterator[Callable[[], bool]]:
    """Stage JSON, then let the caller explicitly publish it inside the context.

    The yielded function owns replace retries and directory durability. Exiting
    without publishing discards the stage. This lets paired-file callers stage
    both records before publishing either, without hiding another publication
    inside a hook. ``write_json_atomic`` is the single-file convenience wrapper.

    Bytes are already encoded JSON; other payloads use ``encode``. Native newline
    and formatting profiles belong to callers. ``sync_file=False`` preserves
    atomic-visibility-only records. Directory durability is best effort for True
    and propagates errors for "strict". Retry delays may be a constant or one
    delay per retry (the initial attempt has no delay).

    ``before_replace`` validates or journals the closed, flushed stage; False
    cancels publication. ``replace_failed`` returns True to retain recovery
    evidence. If compensation raises, retain the stage and chain that error
    from the original failure. ``cleanup_staged`` lets private registries retain
    their checked-root cleanup boundary. Cleanup failures never mask the write.
    Creation mode is filtered by umask. Naming options serve recovery scanners.
    Private/session directories can require an existing parent.
    """

    attempts = max(1, replace_attempts)
    if not isinstance(retry_seconds, (int, float)) and len(retry_seconds) < attempts - 1:
        raise ValueError("One retry delay is required for each additional attempt")
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    if creation_mode == 0o600:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=temporary_prefix if temporary_prefix is not None else f".{path.name}.",
            suffix=temporary_suffix, dir=path.parent,
        )
    else:
        prefix = temporary_prefix if temporary_prefix is not None else f".{path.name}."
        temporary_name = path.parent / f"{prefix}{uuid.uuid4().hex}{temporary_suffix}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary_name, flags, creation_mode)
    temporary = Path(temporary_name)
    replaced = False
    keep_for_recovery = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload if isinstance(payload, bytes) else encode(payload))
            stream.flush()
            if sync_file:
                os.fsync(stream.fileno())
        def publish() -> bool:
            nonlocal replaced
            if before_replace is not None and before_replace(temporary) is False:
                return False
            for attempt in range(attempts):
                try:
                    os.replace(temporary, path)
                    replaced = True
                    break
                except PermissionError:
                    if attempt + 1 >= attempts:
                        raise
                    delay = retry_seconds if isinstance(retry_seconds, (int, float)) else retry_seconds[attempt]
                    time.sleep(delay)
            if durable_directory:
                sync_directory(path.parent, strict=durable_directory == "strict")
            return True

        yield publish
    except Exception as exc:
        if replace_failed is not None:
            # A broken compensation cannot prove that discarding evidence is safe.
            keep_for_recovery = True
            try:
                keep_for_recovery = replace_failed(exc)
            except Exception as compensation_error:
                raise compensation_error from exc
        raise
    finally:
        if not replaced and not keep_for_recovery:
            try:
                if cleanup_staged is None:
                    temporary.unlink(missing_ok=True)
                else:
                    cleanup_staged(temporary)
            except Exception:
                pass  # a staged file left behind is named with a leading "."; it is never read


def write_json_atomic(path: Path, payload: Any, **options: Any) -> bool:
    """Publish one JSON file atomically; see ``staged_json`` for write policies.

    Return True on publication, False only when ``before_replace`` cancels it.
    """
    with staged_json(path, payload, **options) as publish:
        return publish()
