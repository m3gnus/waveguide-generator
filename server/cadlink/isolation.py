"""Run one untrusted-STEP task in a disposable child process.

``docs/plans/STEP-PARSER-ISOLATION.md`` is the gate.  Its point is that the
persistent Gmsh worker thread is a *serialization* mechanism: it makes gmsh
calls happen one at a time and owns their signal handling, but an OCC
segfault, a runaway allocation, or an infinite loop in there is a dead or
wedged server.  A thread cannot be killed and cannot be given its own memory
budget.  A process can be, so external STEP gets a process.

The harness is deliberately dumb about geometry.  It knows how to start a
child that cannot see credentials or the data directory, feed it exactly one
checksum-verified STEP, stop it when it misbehaves, and refuse everything it
sends back that is not a small, well-named, correctly-sized artifact.  What
the child does with the STEP lives in :mod:`server.cadlink.child_main`.

Layout of one invocation::

    <root>/input/source.step      read-only copy, checksum verified both ends
    <root>/staging/result.json    the child's one structured answer
    <root>/staging/out/           the only place a child may leave a file
    <root>/staging/tmp/           child scratch: cwd, TMPDIR and HOME point here

Everything under ``<root>`` is removed when the invocation ends, refusal or
not.  The parent keeps the registry, the cache, and the final atomic moves;
the child never learns where any of them are.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator, Mapping, Sequence

from server.cadlink.limits import (
    INSPECT_MEMORY_BYTES,
    INSPECT_TIMEOUT_S,
    MAX_CHILD_RESULT_BYTES,
    MAX_CONCURRENT_STEP_CHILDREN,
    MAX_RETAINED_STDERR_BYTES,
    MAX_STAGED_ARTIFACT_BYTES,
    MAX_STEP_INPUT_BYTES,
    MESH_MEMORY_BYTES,
    MESH_TIMEOUT_S,
)
from server.platform.paths import app_root


CHILD_ENTRYPOINT = "server.cadlink.child_main"
PROTOCOL_VERSION = 1
_REPO_ROOT = app_root()
_COPY_CHUNK_BYTES = 1024 * 1024
#: How often the parent samples the child's resident memory and deadline.
_WATCHDOG_INTERVAL_S = 0.5
#: Grace between the polite stop and the unconditional one.
_TERMINATE_GRACE_S = 2.0

#: The gate allows one external-STEP child at a time, process-wide. A bounded
#: semaphore rather than a lock so a release bug is an error, not a silent
#: doubling of the concurrency the gate set.
_CHILD_SLOT = threading.BoundedSemaphore(MAX_CONCURRENT_STEP_CHILDREN)


class ChildRefusal(RuntimeError):
    """An isolated task did not produce a clean, verifiable success.

    Every way a child can fail -- timeout, native crash, resource kill,
    malformed result, an artifact that fails verification -- arrives here.  The
    caller turns it into an ordinary stage-labelled ingest refusal.  There is
    deliberately no "retry with looser limits" and no in-process fallback: the
    whole point of the boundary is that the answer to untrusted geometry
    misbehaving is *no*.
    """

    def __init__(
        self,
        stage: str,
        detail: str,
        *,
        error_type: str = "",
        details: Mapping[str, Any] | None = None,
        diagnostics: str = "",
    ) -> None:
        super().__init__(f"{stage}: {detail}" if stage else detail)
        self.stage = stage
        self.detail = detail
        self.error_type = error_type
        #: Whatever extra fields the child attached to its refusal, so a typed
        #: exception can be rebuilt on this side without the parent having to
        #: know every exception the mesher can raise.
        self.details: dict[str, Any] = dict(details or {})
        #: A bounded tail of the child's native stdout/stderr. Diagnostic text
        #: only -- it is never read as a verdict.
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class ChildBudget:
    """One child's wall time, resident memory, and output ceilings."""

    wall_time_s: float
    memory_bytes: int
    result_bytes: int = MAX_CHILD_RESULT_BYTES
    artifact_bytes: int = MAX_STAGED_ARTIFACT_BYTES


INSPECT_BUDGET = ChildBudget(
    wall_time_s=INSPECT_TIMEOUT_S, memory_bytes=INSPECT_MEMORY_BYTES
)
MESH_BUDGET = ChildBudget(wall_time_s=MESH_TIMEOUT_S, memory_bytes=MESH_MEMORY_BYTES)


@dataclass(frozen=True)
class ChildOutcome:
    """A verified child answer, valid only inside the harness context."""

    result: dict[str, Any]
    #: Verified staged files by member name. They live under the harness's
    #: temporary root and vanish when the context manager exits, so a caller
    #: that wants one must copy or read it before leaving the block.
    artifacts: dict[str, Path]
    diagnostics: str


# -- child environment -----------------------------------------------------

#: Variables that carry no authority and that native toolchains genuinely need.
#: Everything else is dropped, so a new credential variable is excluded by
#: default rather than by remembering to add it to a denylist.
_POSIX_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "LC_NUMERIC", "TZ")
_LINKER_PASSTHROUGH = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")
_WINDOWS_PASSTHROUGH = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


def child_environment(
    staging: Path, *, environ: Mapping[str, str] | None = None, system: str | None = None
) -> dict[str, str]:
    """Build the child's environment from an allowlist, not a denylist.

    The child gets no ``ONSHAPE_*`` key pair, no ``WG_*`` data directory, no
    database URL, and no inherited home: ``HOME`` and the Windows profile
    variables are re-pointed into staging so a library that looks for a config
    file finds an empty directory instead of the user's credential store.
    """

    source = dict(os.environ if environ is None else environ)
    resolved = platform.system() if system is None else system
    names = (
        _WINDOWS_PASSTHROUGH
        if resolved == "Windows"
        else _POSIX_PASSTHROUGH + _LINKER_PASSTHROUGH
    )
    env = {name: source[name] for name in names if source.get(name)}

    scratch = str(staging / "tmp")
    home = str(staging / "home")
    env["TMPDIR"] = scratch
    env["TMP"] = scratch
    env["TEMP"] = scratch
    if resolved == "Windows":
        env["USERPROFILE"] = home
        env["APPDATA"] = str(Path(home) / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(Path(home) / "AppData" / "Local")
    else:
        env["HOME"] = home
    # ``-s`` already drops user site-packages; PYTHONPATH is how the child
    # finds ``server`` without inheriting whatever the parent's was.
    env["PYTHONPATH"] = str(_REPO_ROOT)
    # A child must not write into the repository, and __pycache__ would.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["WG_ISOLATED_CAD_CHILD"] = "1"
    return env


# -- process-tree control --------------------------------------------------


def _posix_process_group(process: subprocess.Popen[bytes]) -> int | None:
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


def _wait_for_tree_exit(
    process: subprocess.Popen[bytes], group: int | None, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        # poll() also reaps the direct child. Without that, its zombie keeps
        # the process group looking alive even after every process was stopped.
        process.poll()
        alive = _process_group_alive(group) if group is not None else process.poll() is None
        if not alive:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def terminate_process_tree(
    process: subprocess.Popen[bytes], job: Any = None, *, group: int | None = None
) -> None:
    """Stop the child and everything it started.

    A child that spawned helpers and then hung must not leave them holding the
    STEP or the CPU.  On POSIX the child owns a fresh session, so its process
    group is exactly the tree and ``killpg`` reaches all of it; on Windows the
    job object terminates its members in one call.
    """

    if job is not None:
        with contextlib.suppress(Exception):
            job.terminate()
    resolved_group = (
        group if group is not None else (_posix_process_group(process) if os.name == "posix" else None)
    )
    if resolved_group is not None and resolved_group != os.getpgid(0):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(resolved_group, signal.SIGTERM)
    elif process.poll() is None:
        with contextlib.suppress(Exception):
            process.terminate()
    if _wait_for_tree_exit(process, resolved_group, _TERMINATE_GRACE_S):
        return
    if resolved_group is not None and resolved_group != os.getpgid(0):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(resolved_group, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.kill()
    _wait_for_tree_exit(process, resolved_group, _TERMINATE_GRACE_S)


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


class _BoundedDrain:
    """Keep only the tail of a child's native chatter.

    Native stderr is diagnostic text, never a verdict, and a child that prints
    forever must not be able to grow the *parent*.  So the pipe is drained on a
    thread into a fixed-size tail.
    """

    def __init__(self, stream: Any, limit: int = MAX_RETAINED_STDERR_BYTES) -> None:
        self._stream = stream
        self._limit = limit
        self._tail = bytearray()
        self._truncated = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        with contextlib.suppress(Exception):
            while True:
                chunk = self._stream.read(8192)
                if not chunk:
                    return
                with self._lock:
                    self._tail += chunk
                    if len(self._tail) > self._limit:
                        del self._tail[: len(self._tail) - self._limit]
                        self._truncated = True

    def text(self) -> str:
        self._thread.join(timeout=1.0)
        with self._lock:
            body = bytes(self._tail).decode("utf-8", errors="replace")
            prefix = "...(earlier output discarded)...\n" if self._truncated else ""
        return prefix + body


# -- result and artifact verification --------------------------------------


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r} in the child result")
        result[key] = value
    return result


def _safe_member_name(name: str) -> bool:
    """A staged artifact name is one plain filename and nothing else."""

    if not name or name in {".", ".."}:
        return False
    if "/" in name or "\\" in name or ":" in name:
        return False
    if name.startswith("."):
        return False
    return Path(name).name == name


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _stage_step_input(
    source: Path,
    destination: Path,
    *,
    stage: str,
    expected_sha256: str | None,
    expected_size_bytes: int | None,
) -> tuple[str, int]:
    """Copy and hash one opened STEP descriptor under the input ceiling.

    The bundle reader's checksum is evidence about specific bytes. Reopening a
    mutable CAD folder later without carrying that checksum across the process
    boundary would turn path identity into byte identity. This routine binds
    the child copy back to the verified size and digest while never allocating
    more than one chunk in the parent.
    """

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ChildRefusal(stage, f"could not open the STEP to inspect: {exc}") from exc

    digest = hashlib.sha256()
    copied = 0
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as source_handle:
            before = os.fstat(source_handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ChildRefusal(stage, f"the STEP to inspect is not a regular file: {source}")
            if before.st_size > MAX_STEP_INPUT_BYTES:
                raise ChildRefusal(
                    stage,
                    f"the STEP to inspect is {before.st_size:,} bytes, over the "
                    f"{MAX_STEP_INPUT_BYTES:,} byte limit for one STEP input",
                )
            if expected_size_bytes is not None and before.st_size != expected_size_bytes:
                raise ChildRefusal(
                    stage,
                    "the STEP changed after bundle verification: expected "
                    f"{expected_size_bytes:,} bytes, found {before.st_size:,}",
                )
            with destination.open("xb") as destination_handle:
                while True:
                    chunk = source_handle.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > MAX_STEP_INPUT_BYTES:
                        raise ChildRefusal(
                            stage,
                            f"the STEP grew beyond the {MAX_STEP_INPUT_BYTES:,} byte "
                            "limit while it was being staged",
                        )
                    destination_handle.write(chunk)
                    digest.update(chunk)
            after = os.fstat(source_handle.fileno())
    except ChildRefusal:
        raise
    except OSError as exc:
        raise ChildRefusal(stage, f"could not stage the STEP for inspection: {exc}") from exc

    if (
        copied != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or after.st_ctime_ns != before.st_ctime_ns
    ):
        raise ChildRefusal(stage, "the STEP changed while it was being staged")
    if expected_size_bytes is not None and copied != expected_size_bytes:
        raise ChildRefusal(
            stage,
            "the STEP changed after bundle verification: expected "
            f"{expected_size_bytes:,} bytes, copied {copied:,}",
        )
    checksum = "sha256:" + digest.hexdigest()
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ChildRefusal(
            stage,
            "the STEP checksum changed after bundle verification: expected "
            f"{expected_sha256}, copied {checksum}",
        )
    return checksum, copied


def _verify_artifacts(
    declared: Any,
    out_dir: Path,
    *,
    allowed: Sequence[str],
    stage: str,
    artifact_bytes: int,
    diagnostics: str,
) -> dict[str, Path]:
    if declared is None:
        declared = {}
    if not isinstance(declared, dict):
        raise ChildRefusal(stage, "the child result's artifact table is not an object",
                           diagnostics=diagnostics)
    permitted = set(allowed)
    verified: dict[str, Path] = {}
    for name, record in declared.items():
        if not isinstance(name, str) or not _safe_member_name(name):
            raise ChildRefusal(
                stage,
                f"the child declared an unsafe artifact name {name!r}; only plain "
                "file names inside its staging directory are accepted",
                diagnostics=diagnostics,
            )
        if name not in permitted:
            raise ChildRefusal(
                stage,
                f"the child declared an unexpected artifact {name!r}; this task may "
                f"only produce {sorted(permitted)}",
                diagnostics=diagnostics,
            )
        if not isinstance(record, dict):
            raise ChildRefusal(
                stage, f"artifact {name!r} has no record object", diagnostics=diagnostics
            )
        path = out_dir / name
        if path.is_symlink():
            raise ChildRefusal(
                stage,
                f"artifact {name!r} is a symlink; a staged artifact must be a real "
                "file inside staging",
                diagnostics=diagnostics,
            )
        if not path.is_file():
            raise ChildRefusal(
                stage, f"declared artifact {name!r} is missing", diagnostics=diagnostics
            )
        size = path.stat().st_size
        if size > artifact_bytes:
            raise ChildRefusal(
                stage,
                f"artifact {name!r} is {size:,} bytes, over the {artifact_bytes:,} "
                "byte limit for one staged artifact",
                diagnostics=diagnostics,
            )
        declared_size = record.get("size_bytes")
        if not isinstance(declared_size, int) or isinstance(declared_size, bool):
            raise ChildRefusal(
                stage, f"artifact {name!r} declares no integer size", diagnostics=diagnostics
            )
        if declared_size != size:
            raise ChildRefusal(
                stage,
                f"artifact {name!r} size mismatch: declared {declared_size}, actual {size}",
                diagnostics=diagnostics,
            )
        declared_digest = record.get("sha256")
        if not isinstance(declared_digest, str) or _sha256_file(path) != declared_digest:
            raise ChildRefusal(
                stage,
                f"artifact {name!r} does not match its declared checksum",
                diagnostics=diagnostics,
            )
        verified[name] = path

    # Anything the child left behind that it did not declare is a refusal, not
    # a stray file to ignore: an undeclared member is exactly what an escape
    # looks like.
    for candidate in out_dir.rglob("*"):
        relative = candidate.relative_to(out_dir).as_posix()
        if candidate.is_symlink():
            raise ChildRefusal(
                stage,
                f"the child left a symlink {relative!r} in its staging directory",
                diagnostics=diagnostics,
            )
        if candidate.is_file() and relative not in verified:
            raise ChildRefusal(
                stage,
                f"the child left an undeclared file {relative!r} in its staging directory",
                diagnostics=diagnostics,
            )
    return verified


def _read_result(
    result_path: Path, *, stage: str, result_bytes: int, diagnostics: str
) -> dict[str, Any]:
    if not result_path.is_file() or result_path.is_symlink():
        raise ChildRefusal(
            stage,
            "the child exited without writing a structured result",
            diagnostics=diagnostics,
        )
    size = result_path.stat().st_size
    if size > result_bytes:
        raise ChildRefusal(
            stage,
            f"the child result is {size:,} bytes, over the {result_bytes:,} byte "
            "limit for a child's structured result",
            diagnostics=diagnostics,
        )
    try:
        envelope = json.loads(
            result_path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite number {token!r} in the child result")
            ),
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ChildRefusal(
            stage, f"the child result is not readable JSON: {exc}", diagnostics=diagnostics
        ) from exc
    if not isinstance(envelope, dict):
        raise ChildRefusal(stage, "the child result is not an object", diagnostics=diagnostics)
    if envelope.get("protocol") != PROTOCOL_VERSION:
        raise ChildRefusal(
            stage,
            f"the child result declares protocol {envelope.get('protocol')!r}, "
            f"not {PROTOCOL_VERSION}",
            diagnostics=diagnostics,
        )
    return envelope


# -- the harness -----------------------------------------------------------


@contextlib.contextmanager
def isolated_step_task(
    task: str,
    payload: Mapping[str, Any],
    *,
    step_path: str | Path,
    budget: ChildBudget,
    allowed_artifacts: Sequence[str] = (),
    stage: str,
    entrypoint: str = CHILD_ENTRYPOINT,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
) -> Iterator[ChildOutcome]:
    """Run one task in a fresh child and yield its verified answer.

    ``entrypoint`` exists so the acceptance tests can drive a child that
    misbehaves on purpose through the identical harness.  It changes what the
    child runs, never what the harness enforces.
    """

    source = Path(step_path)
    if not source.is_file() or source.is_symlink():
        raise ChildRefusal(stage, f"the STEP to inspect is not a regular file: {source}")

    acquired = _CHILD_SLOT.acquire(timeout=budget.wall_time_s)
    if not acquired:
        raise ChildRefusal(
            stage,
            "another external-STEP import is already running; the gate allows "
            f"{MAX_CONCURRENT_STEP_CHILDREN} at a time",
        )
    root = Path(tempfile.mkdtemp(prefix="wg-cad-child-"))
    try:
        staged_input = root / "input"
        staging = root / "staging"
        out_dir = staging / "out"
        for directory in (staged_input, out_dir, staging / "tmp", staging / "home"):
            directory.mkdir(parents=True, exist_ok=True)
        child_source = staged_input / "source.step"
        checksum, staged_size = _stage_step_input(
            source,
            child_source,
            stage=stage,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        )
        with contextlib.suppress(OSError):
            # Read-only for the child. Advisory rather than enforced -- the
            # child runs as the same user -- but it turns an accidental
            # in-place edit into an error instead of a silent mutation.
            child_source.chmod(0o400)
        envelope = {
            "protocol": PROTOCOL_VERSION,
            "task": task,
            "source": {
                "path": str(child_source),
                "sha256": checksum,
                "size_bytes": staged_size,
            },
            "staging": str(staging),
            "out_dir": str(out_dir),
            "result_path": str(staging / "result.json"),
            "limits": {
                "memory_bytes": budget.memory_bytes,
                "artifact_bytes": budget.artifact_bytes,
                "result_bytes": budget.result_bytes,
                "wall_time_s": budget.wall_time_s,
            },
            "payload": dict(payload),
        }

        outcome = _run_child(
            envelope,
            staging=staging,
            out_dir=out_dir,
            budget=budget,
            allowed_artifacts=allowed_artifacts,
            stage=stage,
            entrypoint=entrypoint,
        )
        yield outcome
    finally:
        shutil.rmtree(root, ignore_errors=True)
        _CHILD_SLOT.release()


def _run_child(
    envelope: Mapping[str, Any],
    *,
    staging: Path,
    out_dir: Path,
    budget: ChildBudget,
    allowed_artifacts: Sequence[str],
    stage: str,
    entrypoint: str,
) -> ChildOutcome:
    command = [sys.executable, "-s", "-B", "-m", entrypoint]
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        # One pipe for both: native chatter interleaves anyway and the answer
        # never travels this way.
        "stderr": subprocess.STDOUT,
        "cwd": str(staging / "tmp"),
        "env": {
            **child_environment(staging),
            # The child applies this as RLIMIT_FSIZE, so a runaway write is a
            # kernel error inside the child rather than a full disk outside it.
            "WG_CHILD_MAX_FILE_BYTES": str(budget.artifact_bytes),
        },
        "close_fds": True,
    }
    if os.name == "posix":
        # A fresh session makes the child's process group the whole tree.
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = _windows_creation_flags()

    process = start_child_process(command, popen_kwargs, stage=stage)

    process_group = _posix_process_group(process) if os.name == "posix" else None
    job = None
    if os.name == "nt":
        try:
            job = _required_windows_job(process, budget, stage=stage)
        except ChildRefusal:
            # The child is still blocked waiting for its stdin envelope. Stop
            # it before surfacing the refusal: no uncontained process is ever
            # allowed to continue merely because the job API failed.
            with contextlib.suppress(Exception):
                if process.stdin is not None:
                    process.stdin.close()
            with contextlib.suppress(Exception):
                if process.stdout is not None:
                    process.stdout.close()
            raise
    drain = _BoundedDrain(process.stdout)
    killed_for: str | None = None
    try:
        # The child blocks on stdin until this write lands, so confinement is
        # already in place before it has been told what to do.
        assert process.stdin is not None
        try:
            process.stdin.write(json.dumps(envelope, allow_nan=False).encode("utf-8"))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        killed_for = _supervise(process, budget, job=job, group=process_group)
    finally:
        # Do this even after a clean exit. The root may have returned a valid
        # result while one of its native helpers still owns the process group
        # and can mutate staging.
        terminate_process_tree(process, job, group=process_group)
        # Drain first, close second: the tail is the only diagnostic there is.
        diagnostics = drain.text()
        with contextlib.suppress(Exception):
            if process.stdout is not None:
                process.stdout.close()
        if job is not None:
            with contextlib.suppress(Exception):
                job.close()

    if killed_for is not None:
        raise ChildRefusal(stage, killed_for, diagnostics=diagnostics)

    returncode = process.returncode
    envelope_out = _read_result(
        staging / "result.json",
        stage=stage,
        result_bytes=budget.result_bytes,
        diagnostics=diagnostics,
    )
    if not envelope_out.get("ok"):
        error = envelope_out.get("error")
        error = error if isinstance(error, dict) else {}
        message = error.get("message")
        raise ChildRefusal(
            stage,
            str(message)
            if isinstance(message, str) and message
            else "the isolated CAD child refused the input",
            error_type=str(error.get("type") or ""),
            details={
                key: value for key, value in error.items() if key not in {"type", "message"}
            },
            diagnostics=diagnostics,
        )
    if returncode != 0:
        raise ChildRefusal(
            stage,
            f"the isolated CAD child claimed success but exited with code {returncode}",
            diagnostics=diagnostics,
        )
    if envelope_out.get("task") != envelope["task"]:
        raise ChildRefusal(
            stage,
            "the child answered a different task than it was asked",
            diagnostics=diagnostics,
        )
    result = envelope_out.get("result")
    if not isinstance(result, dict):
        raise ChildRefusal(stage, "the child result has no result object", diagnostics=diagnostics)
    artifacts = _verify_artifacts(
        envelope_out.get("artifacts"),
        out_dir,
        allowed=allowed_artifacts,
        stage=stage,
        artifact_bytes=budget.artifact_bytes,
        diagnostics=diagnostics,
    )
    return ChildOutcome(result=result, artifacts=artifacts, diagnostics=diagnostics)


def _supervise(
    process: subprocess.Popen[bytes],
    budget: ChildBudget,
    *,
    job: Any = None,
    group: int | None = None,
) -> str | None:
    """Poll the deadline and the resident memory; return why it was killed."""

    deadline = time.monotonic() + budget.wall_time_s
    if group is None and os.name == "posix":
        group = _posix_process_group(process)
    while True:
        try:
            process.wait(timeout=_WATCHDOG_INTERVAL_S)
            return None
        except subprocess.TimeoutExpired:
            pass
        if time.monotonic() >= deadline:
            terminate_process_tree(process, job, group=group)
            return (
                f"the isolated CAD child exceeded its {budget.wall_time_s:g} second "
                "deadline and was stopped with its descendants"
            )
        if group is not None:
            resident = process_group_rss_bytes(group)
            if resident is not None and resident > budget.memory_bytes:
                terminate_process_tree(process, job, group=group)
                return (
                    f"the isolated CAD child reached {resident:,} bytes resident, over "
                    f"its {budget.memory_bytes:,} byte budget, and was stopped"
                )


# -- Windows job objects ---------------------------------------------------


def start_child_process(
    command: list[str], popen_kwargs: dict[str, Any], *, stage: str
) -> subprocess.Popen[bytes]:
    """Launch one child, retrying once when Windows forbids job breakaway.

    A parent that is itself inside a job object -- hosted CI runners are, and
    that is where this path is naturally exercised -- refuses
    ``CREATE_BREAKAWAY_FROM_JOB`` with ``ERROR_ACCESS_DENIED``. Losing the
    breakaway costs a nested job, not the boundary, so it is worth one retry
    rather than refusing every import.

    Tests must launch children through this rather than calling ``Popen`` with
    ``_windows_creation_flags()`` themselves: a test that reimplements the first
    attempt without the retry passes on a developer machine and fails on any
    runner that is inside a job.
    """

    try:
        return subprocess.Popen(command, **popen_kwargs)  # noqa: S603
    except OSError as exc:
        if os.name != "nt" or "creationflags" not in popen_kwargs:
            raise ChildRefusal(
                stage, f"could not start the isolated CAD child: {exc}"
            ) from exc
        popen_kwargs["creationflags"] = _windows_creation_flags(breakaway=False)
        try:
            return subprocess.Popen(command, **popen_kwargs)  # noqa: S603
        except OSError as retry_exc:
            raise ChildRefusal(
                stage, f"could not start the isolated CAD child: {retry_exc}"
            ) from retry_exc


def _windows_creation_flags(*, breakaway: bool = True) -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if breakaway:
        # Without breakaway, a child of an already-jobbed parent (a CI agent,
        # say) cannot be put in a job of its own. Some jobs forbid breakaway,
        # which is why the caller retries without it rather than refusing.
        flags |= 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    return flags


class _WindowsJob:
    """A job object holding the child and everything it starts.

    Windows has no ``killpg``.  The job object is the equivalent and is
    strictly better: the memory limit is enforced by the kernel at commit time
    rather than sampled, and ``KILL_ON_JOB_CLOSE`` means the tree dies with the
    parent even if the parent dies without running any cleanup at all.
    """

    def __init__(self, handle: Any, kernel32: Any) -> None:
        self._handle = handle
        self._kernel32 = kernel32

    def terminate(self) -> None:
        self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        self._kernel32.CloseHandle(self._handle)


def _required_windows_job(
    process: subprocess.Popen[bytes], budget: ChildBudget, *, stage: str
) -> Any:
    """Return the containment job or refuse; Windows has no safe fallback."""

    try:
        job = _assign_windows_job(process, budget)
    except Exception as exc:  # pragma: no cover - concrete failures are Windows-only
        terminate_process_tree(process)
        raise ChildRefusal(
            stage, f"could not confine the isolated CAD child in a Windows job: {exc}"
        ) from exc
    if job is None:
        # Retain the guard even though the production implementation now raises
        # detailed errors. It makes future platform wrappers fail closed too.
        terminate_process_tree(process)
        raise ChildRefusal(
            stage, "could not confine the isolated CAD child in a Windows job"
        )
    return job


def _configure_windows_job_api(kernel32: Any, ctypes: Any, wintypes: Any) -> None:
    """Declare the pointer-sized Win32 ABI used by the job wrapper.

    ``ctypes`` otherwise assumes ``c_int`` for every argument and return value.
    That is not a safe approximation for ``HANDLE`` on 64-bit Windows: it can
    truncate both the job returned by ``CreateJobObjectW`` and the process
    handle passed to ``AssignProcessToJobObject``.
    """

    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _assign_windows_job(process: subprocess.Popen[bytes], budget: ChildBudget) -> Any:
    """Create a memory-capped, kill-on-close job and put the child in it."""

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError as exc:  # pragma: no cover - Windows only
        raise RuntimeError("Windows job-object support is unavailable") from exc

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    limit_job_memory = 0x00000200
    limit_process_memory = 0x00000100
    limit_kill_on_job_close = 0x00002000
    limit_active_process = 0x00000008
    extended_limit_information = 9

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    _configure_windows_job_api(kernel32, ctypes, wintypes)
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = (
        limit_job_memory | limit_process_memory | limit_kill_on_job_close | limit_active_process
    )
    limits.BasicLimitInformation.ActiveProcessLimit = 32
    limits.ProcessMemoryLimit = budget.memory_bytes
    limits.JobMemoryLimit = budget.memory_bytes
    if not kernel32.SetInformationJobObject(
        handle, extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error, "SetInformationJobObject failed")
    if not kernel32.AssignProcessToJobObject(handle, int(process._handle)):  # type: ignore[attr-defined]
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error, "AssignProcessToJobObject failed")
    return _WindowsJob(handle, kernel32)


__all__ = [
    "CHILD_ENTRYPOINT",
    "INSPECT_BUDGET",
    "MESH_BUDGET",
    "PROTOCOL_VERSION",
    "ChildBudget",
    "ChildOutcome",
    "ChildRefusal",
    "child_environment",
    "isolated_step_task",
    "process_group_rss_bytes",
    "terminate_process_tree",
]
