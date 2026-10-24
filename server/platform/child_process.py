"""Child-process mechanics shared by disposable tasks and reusable workers.

Callers own commands, environment, protocols and wording. This module owns
bounded diagnostics, exit classification and lifecycle operations; policies
make the Popen and multiprocessing shutdown differences explicit. Low-level
POSIX sessions and Windows job objects remain in process_tree.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping

from server.platform.incidents import record_incident
from server.platform.process_tree import cancel_blocked_reads, kill_own_process_group


@dataclass(frozen=True)
class ExitWording:
    unknown: str
    signal: str
    unknown_signal: str
    code: str
    status: str
    status_names: Mapping[int, str] | None = None
    status_on_all_platforms: bool = False


def describe_exit(code: int | None, *, wording: ExitWording, windows: bool | None = None) -> str:
    if code is None:
        return wording.unknown
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            return wording.unknown_signal.format(number=-code, code=code)
        return wording.signal.format(name=name, number=-code, code=code)
    windows = os.name == "nt" if windows is None else windows
    if code >= 0x80000000 and (windows or wording.status_on_all_platforms):
        text = wording.status.format(code=code)
        name = (wording.status_names or {}).get(code)
        return text + (f" ({name})" if name else "")
    return wording.code.format(code=code)


class BoundedTail:
    """Byte tail; callers serialize access and choose how to render truncation."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def append(self, data: bytes) -> None:
        self.data += data
        if len(self.data) > self.limit:
            del self.data[: len(self.data) - self.limit]
            self.truncated = True

    def clear(self) -> None:
        self.data.clear()
        self.truncated = False


def posix_process_group(process: subprocess.Popen[bytes]) -> int | None:
    try:
        return os.getpgid(process.pid)
    except (OSError, AttributeError):
        return None


def _process_group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # A permission failure still proves that the group exists.
        return True
    return True


#: How long a tree stop waits for confirmed exit before reporting it unconfirmed.
TREE_STOP_GRACE_S = 2.0


def _wait_until(done: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if done():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _wait_for_tree_exit(
    process: subprocess.Popen[bytes], group: int | None, timeout: float
) -> bool:
    def gone() -> bool:
        # poll() also reaps the direct child. Without that, its zombie keeps
        # the process group looking alive even after every process was stopped.
        process.poll()
        return not (_process_group_alive(group) if group is not None else process.poll() is None)

    return _wait_until(gone, timeout)


def stop_job(job: Any, timeout: float = TREE_STOP_GRACE_S) -> bool:
    """Stop every member of a Windows job and report whether all were confirmed gone.

    ``job.stop`` (``process_tree.WindowsJob.stop``) is the one implementation;
    a job that cannot even be asked counts as unconfirmed.
    """

    try:
        return bool(job.stop(timeout))
    except Exception:
        return False


#: The incident kind for a process tree that a stop could not confirm gone.
TREE_SURVIVED_KIND = "process-tree-survived"


def record_unconfirmed_stop(phase: str, subject: str, **evidence: Any) -> None:
    """Keep a developer incident for a stop whose tree was not confirmed gone.

    For stops that have no answer to refuse (Stop, teardown, a start already
    failing): the user's outcome is unchanged and gets no technical text.
    """

    logging.getLogger("wg.solve").warning(
        "Could not confirm that %s and its helpers stopped: %s", subject, evidence
    )
    record_incident(TREE_SURVIVED_KIND, phase, evidence={"subject": subject, **evidence})


def terminate_process_tree(
    process: subprocess.Popen[bytes],
    job: Any = None,
    *,
    group: int | None = None,
    grace_seconds: float = 2.0,
) -> bool:
    """Stop the child and everything it started; return whether it is all gone.

    A child that spawned helpers and then hung must not leave them holding the
    STEP or the CPU.  On POSIX the child owns a fresh session, so its process
    group is exactly the tree and ``killpg`` reaches all of it; on Windows the
    job object terminates its members in one call, and ``job.stop`` waits until
    each of them has actually exited.  ``True`` means the whole tree was
    confirmed gone within the bounded waits; ``False`` means some part of it
    may still be running.
    """

    job_stopped = True if job is None else stop_job(job, grace_seconds)
    resolved_group = (
        group
        if group is not None
        else (posix_process_group(process) if os.name == "posix" else None)
    )
    if resolved_group is not None and resolved_group != os.getpgid(0):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(resolved_group, signal.SIGTERM)
    elif process.poll() is None:
        with contextlib.suppress(Exception):
            process.terminate()
    if _wait_for_tree_exit(process, resolved_group, grace_seconds):
        return job_stopped
    if resolved_group is not None and resolved_group != os.getpgid(0):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(resolved_group, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.kill()
    return _wait_for_tree_exit(process, resolved_group, grace_seconds) and job_stopped


def process_group_rss_bytes(group: int) -> int | None:
    """Sum resident memory over one process group, or ``None`` if unknown.

    ``RLIMIT_AS`` is not usable for this.  A plain Python process with gmsh
    imported reserves roughly 435 GB of address space on macOS against 52 MB
    resident, so an address-space cap set at the gate's resident figure would
    refuse every legitimate import.  ``ps`` reports the number the gate
    actually names, on both macOS and Linux, so the parent samples it.
    """

    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "pgid=,rss="],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    total = 0
    seen = False
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            if int(fields[0]) != group:
                continue
            total += int(fields[1]) * 1024
        except ValueError:
            continue
        seen = True
    return total if seen else None


class PipeDrain:
    """Keep only the tail of a child's native chatter.

    Native stderr is diagnostic text, never a verdict, and a child that prints
    forever must not be able to grow the *parent*.  So the pipe is drained on a
    thread into a fixed-size tail.
    """

    def __init__(
        self, stream: Any, limit: int, *, truncation_prefix: str, join_seconds: float = 1.0
    ) -> None:
        self._stream = stream
        self._buffer = BoundedTail(limit)
        self._truncation_prefix = truncation_prefix
        self._join_seconds = join_seconds
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # read1 returns whatever one read of the pipe brings. A buffered
        # read(n) keeps reading until it has n bytes, holding what it already
        # got in the reader's own buffer, so a cancelled read would take that
        # tail -- the diagnostic -- with it.
        read = getattr(self._stream, "read1", None) or self._stream.read
        try:
            with contextlib.suppress(Exception):
                while True:
                    chunk = read(8192)
                    if not chunk:
                        return
                    with self._lock:
                        self._buffer.append(chunk)
        finally:
            # Only this thread closes the stream: a close from another thread
            # blocks behind a read in progress, for as long as that read lasts.
            with contextlib.suppress(Exception):
                self._stream.close()

    def text(self) -> str:
        self._thread.join(timeout=self._join_seconds)
        # A process outside the tree that still holds the pipe would keep the
        # read blocked for good; cancel it so the thread ends and closes it.
        cancel_blocked_reads([self._thread])
        with self._lock:
            body = bytes(self._buffer.data).decode("utf-8", errors="replace")
            prefix = self._truncation_prefix if self._buffer.truncated else ""
        return prefix + body


def stderr_excerpt(
    raw: bytes, *, truncated: bool = False, lines_limit: int, line_chars: int, chars_limit: int
) -> str:
    """The last meaningful lines of a worker's stderr, bounded for a job record."""

    lines = raw.decode("utf-8", "replace").splitlines()
    if truncated and lines:
        # The bounded buffer may have cut its first line in half.
        lines = lines[1:]
    lines = [line.rstrip() for line in lines if line.strip()]
    lines = lines[-lines_limit:]
    lines = [line if len(line) <= line_chars else line[: line_chars - 3] + "..." for line in lines]
    text = "\n".join(lines)
    if len(text) > chars_limit:
        text = "..." + text[-(chars_limit - 3) :]
    return text


class FileStderr:
    """The parent's side of the worker's redirected stderr file."""

    def __init__(
        self, path: str, *, tail_bytes: int, file_cap_bytes: int, excerpt: Callable[..., str]
    ) -> None:
        self.path = path
        self._offset = 0
        self._buffer = BoundedTail(tail_bytes)
        self._file_cap_bytes = file_cap_bytes
        self._excerpt = excerpt
        self._lock = threading.Lock()

    def drain(self) -> None:
        """Forward what the worker wrote since the last call; keep the tail.

        Never raises: this is diagnostics, and a solve must not fail over it.
        """

        with self._lock:
            try:
                size = os.stat(self.path).st_size
                if size < self._offset:
                    self._offset = 0
                if size == self._offset:
                    return
                start = max(self._offset, size - self._file_cap_bytes)
                with open(self.path, "rb") as handle:
                    handle.seek(start)
                    data = handle.read(size - start)
                self._offset = start + len(data)
                if self._offset >= self._file_cap_bytes:
                    # Anything the worker writes between the read and this
                    # truncation is lost; only a flood gets here.
                    with open(self.path, "r+b") as handle:
                        handle.truncate(0)
                    self._offset = 0
            except OSError:
                return
            self._buffer.append(data)
            stream = sys.stderr
            if stream is not None:
                try:
                    stream.write(data.decode("utf-8", "replace"))
                    stream.flush()
                except (OSError, ValueError):
                    pass

    def clear_tail(self) -> None:
        """Start a new job's tail; earlier output is not this job's cause."""

        self.drain()
        with self._lock:
            self._buffer.clear()

    def excerpt(self) -> str:
        self.drain()
        with self._lock:
            return self._excerpt(bytes(self._buffer.data), truncated=self._buffer.truncated)

    def discard(self) -> None:
        """Forward the rest and remove the file. Call once the worker is gone."""

        self.drain()
        try:
            os.remove(self.path)
        except OSError:
            # Still open in a process that has not quite exited (Windows).
            # It lives in the session directory, which goes with the session.
            pass


def new_stderr_file(*, prefix: str, suffix: str) -> str | None:
    try:
        from server.platform.temp_session import temporary_directory_root

        handle, path = tempfile.mkstemp(
            prefix=prefix, suffix=suffix, dir=temporary_directory_root()
        )
    except OSError:
        return None
    os.close(handle)
    return path


def send_stderr_to(path: str | None) -> None:
    """In the worker: point file descriptor 2 -- native code's stderr as well
    as Python's -- at ``path``.

    A failure leaves stderr where it was: the capture is diagnostic, and a
    worker that cannot redirect can still solve.
    """

    if not path:
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
    except OSError:
        return
    try:
        if sys.stderr is not None:
            try:
                sys.stderr.flush()
            except (OSError, ValueError):
                pass
        # Inheritable, so a split sweep's own workers write here too.
        os.dup2(fd, 2)
        if os.name == "nt":
            # Native code that asks the OS for its stderr handle instead of
            # using the C runtime's descriptor 2 must find the file as well.
            import ctypes
            import msvcrt

            ctypes.windll.kernel32.SetStdHandle(
                ctypes.c_uint32(-12 & 0xFFFFFFFF), ctypes.c_void_p(msvcrt.get_osfhandle(2))
            )
        # On a Windows console, Python's stderr writes to the console directly,
        # not through descriptor 2; under pythonw there is no stderr at all.
        sys.stderr = os.fdopen(
            2, "w", encoding="utf-8", errors="backslashreplace", buffering=1, closefd=False
        )
    except OSError:
        pass
    finally:
        os.close(fd)


def exit_when_parent_does(*, exit_code: int, thread_name: str) -> None:
    """Stop the adopted tree when the multiprocessing parent's sentinel fires."""

    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def wait_and_exit() -> None:
        from multiprocessing.connection import wait

        wait([parent.sentinel])
        # Take the sweep's workers with us. ``os._exit`` runs no multiprocessing
        # cleanup, so without this they outlive the launcher -- and since this
        # process claimed its own session, the launcher's own group kill cannot
        # reach them either. No-op unless this process really did adopt a
        # session, so it cannot fire inside a test or on Windows, where the
        # parent's job object already tears the tree down.
        kill_own_process_group()
        os._exit(exit_code)

    threading.Thread(target=wait_and_exit, name=thread_name, daemon=True).start()


def windows_creation_flags(*, breakaway: bool = True) -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if breakaway:
        # Without breakaway, a child of an already-jobbed parent (a CI agent,
        # say) cannot be put in a job of its own. Some jobs forbid breakaway,
        # which is why the caller retries without it rather than refusing.
        flags |= 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    return flags


def start_popen(
    command: list[str], popen_kwargs: dict[str, Any], *, retry_windows: bool
) -> subprocess.Popen[bytes]:
    """Launch, optionally retrying Windows errors without breakaway flags.

    The retry mutates the supplied kwargs, as the disposable-task runner did.
    Domain callers translate OSError to their own refusal vocabulary.
    """
    try:
        return subprocess.Popen(command, **popen_kwargs)  # noqa: S603
    except OSError:
        if not retry_windows or os.name != "nt" or "creationflags" not in popen_kwargs:
            raise
        popen_kwargs["creationflags"] = windows_creation_flags(breakaway=False)
        return subprocess.Popen(command, **popen_kwargs)  # noqa: S603


def supervise(
    process: Any,
    *,
    wall_time_s: float,
    interval_s: float,
    stop: Callable[[], None],
    resource_check: Callable[[], Any],
    timeout_reason: Any,
    before_wait: Callable[[], None] | None = None,
) -> Any:
    """Wait for exit, enforcing the deadline before checking resource usage."""
    deadline = time.monotonic() + wall_time_s
    if before_wait is not None:
        before_wait()
    while True:
        try:
            process.wait(timeout=interval_s)
            return None
        except subprocess.TimeoutExpired:
            pass
        if time.monotonic() >= deadline:
            stop()
            return timeout_reason
        reason = resource_check()
        if reason is not None:
            stop()
            return reason


def terminate_worker(process: Any, *, join_seconds: float) -> None:
    """Stop a multiprocessing child before consuming its group announcement."""
    if process.is_alive():
        process.terminate()
        process.join(join_seconds)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(join_seconds)


def finish_worker_tree(
    process: Any, job: Any, group: int | None, *, kill_group: Callable[[int], Any],
    grace_seconds: float = TREE_STOP_GRACE_S,
) -> bool:
    """After protocol teardown, stop descendants and reap the direct child.

    Returns whether the descendants were confirmed gone within ``grace_seconds``.
    """
    stopped = True
    if job is not None:
        stopped = stop_job(job, grace_seconds)
        job.close()
    elif group is not None:
        kill_group(group)
    if not process.is_alive():
        process.join()
    if job is None and group is not None:
        # Reaped first: the direct child's zombie would keep the group alive.
        stopped = _wait_until(lambda: not _process_group_alive(group), grace_seconds)
    return stopped


def worker_exitcode(process: Any, *, join_seconds: float) -> int | None:
    if process is not None:
        try:
            process.join(join_seconds)
            return process.exitcode
        except (AssertionError, OSError, ValueError):
            pass
    return None


def close_quietly(connection: Any) -> None:
    if connection is not None:
        try:
            connection.close()
        except OSError:
            pass
