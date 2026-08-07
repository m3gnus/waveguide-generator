"""Application logging to stderr and a small rotating disk log.

Both destinations sit behind a queue. ``logging.StreamHandler`` flushes on
every record and ``RotatingFileHandler`` both flushes and does a size check
for rollover on every record, and all of that ran on whichever thread called
the logger -- which, for the request log and every job event, is the event
loop. A ``QueueHandler`` costs the caller one lock and one ``put``; a single
listener thread pays for the formatting and the I/O.
"""

from __future__ import annotations

import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
import queue
import sys

from .paths import DataPaths, ensure_data_layout


MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_FILENAME = "server.log"

#: The live listener, so a second ``setup_logging`` (tests, a re-entered main)
#: stops the previous one instead of leaking its thread and its file handle.
_listener: QueueListener | None = None


def _rotate_if_needed(log_path: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    try:
        oversized = log_path.stat().st_size >= max_bytes
    except FileNotFoundError:
        return
    if oversized:
        os.replace(log_path, log_path.with_name(f"{log_path.name}.1"))


def _stop_listener() -> tuple[logging.Handler, ...]:
    """Stop the listener thread and return the handlers it was feeding.

    Deliberately does not close them: ``flush_logs`` re-attaches the same
    handlers to the root logger so records emitted after shutdown still land,
    and only ``setup_logging`` -- which is replacing them outright -- closes.
    """

    global _listener
    listener = _listener
    _listener = None
    if listener is None:
        return ()
    try:
        listener.stop()
    except Exception:  # pragma: no cover - stopping twice must never raise
        pass
    return tuple(listener.handlers)


def setup_logging(
    data: DataPaths | str | os.PathLike[str] | None = None,
    *,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure the process root logger and return the WG2 logger."""

    paths = data if isinstance(data, DataPaths) else ensure_data_layout(data)
    log_path = paths.logs / LOG_FILENAME
    _rotate_if_needed(log_path)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )
    root = logging.getLogger()
    root.setLevel(level)

    for handler in tuple(root.handlers):
        if getattr(handler, "_wg2_handler", False):
            root.removeHandler(handler)
            handler.close()
    for retired in _stop_listener():
        try:
            retired.close()
        except (OSError, ValueError):  # pragma: no cover
            pass

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=1,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    # Unbounded: dropping a log line to protect the queue would lose exactly the
    # diagnostics someone is reading the log for. The listener drains far faster
    # than this server can produce records, so the queue stays shallow.
    global _listener
    record_queue: queue.SimpleQueue = queue.SimpleQueue()
    _listener = QueueListener(
        record_queue, stderr_handler, file_handler, respect_handler_level=True
    )
    _listener.start()

    # No formatter here on purpose. ``QueueHandler.prepare`` runs the record
    # through its own formatter and replaces ``record.msg`` with the result, so
    # a formatter on both ends stamps the timestamp, level and logger name
    # twice. Formatting belongs to the sink handlers, which is also where it
    # costs the listener thread rather than the caller.
    queue_handler = QueueHandler(record_queue)
    queue_handler._wg2_handler = True  # type: ignore[attr-defined]
    root.addHandler(queue_handler)

    logger = logging.getLogger("wg2")
    logger.info("Logging to %s", log_path)
    return logger


def log_sinks() -> tuple[logging.Handler, ...]:
    """The handlers that really write, which now sit behind the queue.

    Once the listener owns them they are no longer attached to the root logger,
    so anything inspecting logging configuration -- diagnostics, tests --
    needs this rather than ``logging.getLogger().handlers``.
    """

    listener = _listener
    if listener is None:
        return tuple(
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "_wg2_handler", False)
        )
    return tuple(listener.handlers)


def flush_logs() -> None:
    """Drain the queue and flush every destination, best-effort at shutdown.

    Stopping the listener is what actually guarantees the queued records reach
    disk: flushing the ``QueueHandler`` only flushes the queue's own (empty)
    buffer. The handlers are left attached and usable afterwards -- a later
    record still formats and writes, it simply writes synchronously from the
    calling thread once the listener is gone, which is the right trade for the
    handful of lines emitted after this point.
    """

    retired = _stop_listener()
    if retired:
        # Anything logged from here on has no listener to carry it, so put the
        # real handlers back on the root logger to keep late records visible.
        root = logging.getLogger()
        for handler in tuple(root.handlers):
            if getattr(handler, "_wg2_handler", False):
                root.removeHandler(handler)
        for handler in retired:
            handler._wg2_handler = True  # type: ignore[attr-defined]
            root.addHandler(handler)

    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except (OSError, ValueError):
            pass
