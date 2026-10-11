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
the child does with the STEP lives in :mod:`server.cadlink.intake.child_main`.

Layout of one invocation::

    <root>/input/source.step      read-only copy, checksum verified both ends
    <root>/staging/result.json    the child's one structured answer
    <root>/staging/out/           the only place a child may leave a file
    <root>/staging/tmp/           child scratch: cwd, TMPDIR and HOME point here

Everything under ``<root>`` is removed when the invocation ends, refusal or
not.  In a launched server ``<root>`` lives in the process's temporary
session, so a forced exit that skips that removal leaves it where the next
start sweeps it (``server/platform/temp_session.py``).  The parent keeps the
registry, the cache, and the final atomic moves; the child never learns where
any of them are.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile
import threading
from typing import Any, Iterator, Mapping, Sequence

from server.cadlink.build_flags import FEM_REGIONS_ENV
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
from server.platform.job_start import ContainedStartError, JobStart, windows_job_start
from server.platform.paths import app_root
from server.platform.process_tree import create_windows_job
from server.platform import child_process, process_tree
from server.platform.child_process import (
    posix_process_group as _posix_process_group,
    process_group_rss_bytes,
    windows_creation_flags as _windows_creation_flags,
)
from server.platform.temp_session import remove_tree, temporary_directory_root


CHILD_ENTRYPOINT = "server.cadlink.intake.child_main"
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

logger = logging.getLogger(__name__)


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
        #: How the child exited, once it has; ``None`` for a refusal made
        #: before a child ran. Diagnostic like ``diagnostics``.
        self.returncode: int | None = None


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
#: The dense-solver memory ceiling's two settings (``server/mesh/builder.py``).
#: An imported mesh is built, and held to that ceiling, in this child, so an
#: operator's override has to reach it exactly as it reaches a parametric
#: build. A byte count and a flag; neither carries any authority.
_MEMORY_PASSTHROUGH = (
    "WG2_DENSE_SOLVER_MEMORY_LIMIT_BYTES",
    "WG2_DENSE_SOLVER_MEMORY_LIMIT_UNSAFE",
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
    ) + _MEMORY_PASSTHROUGH + (FEM_REGIONS_ENV,)
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
    # No PYTHONPATH: ``child_command`` makes ``server`` importable, and an
    # interpreter with a ``._pth`` would ignore one anyway.
    # A child must not write into the repository, and __pycache__ would.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["WG_ISOLATED_CAD_CHILD"] = "1"
    return env


#: Puts the app root first on ``sys.path``, then runs the entrypoint as ``-m``
#: would. ``-m`` alone found ``server`` only through PYTHONPATH, and both of
#: the Windows bundle's interpreters carry an isolated ``._pth``, which makes
#: CPython ignore PYTHONPATH and leave the working directory off the path.
#: The runtime interpreter's ``._pth`` lists no app directory at all, so a
#: server started under it had a child that died before it could answer.
_BOOTSTRAP = (
    "import sys; root, name = sys.argv[1:3]; del sys.argv[1:3]; "
    "sys.path.insert(0, root); "
    "import runpy; runpy.run_module(name, run_name='__main__', alter_sys=True)"
)


def child_command(entrypoint: str = CHILD_ENTRYPOINT, *, root: Path = _REPO_ROOT) -> list[str]:
    """Return the argv that starts one child, on any bundled interpreter.

    ``-s`` drops user site-packages and ``-B`` keeps ``__pycache__`` out of the
    app layer. The root travels as an argument rather than inside the code, so
    no path is ever quoted into Python source.
    """

    return [sys.executable, "-s", "-B", "-c", _BOOTSTRAP, str(root), entrypoint]


_EXIT_WORDING = child_process.ExitWording(
    unknown="with no recorded code",
    signal="on signal {name} ({code})",
    unknown_signal="on signal {number} ({code})",
    code="with code {code}",
    status="with code 0x{code:08X}",
    status_on_all_platforms=True,
)


def describe_exit(returncode: int | None) -> str:
    return child_process.describe_exit(returncode, wording=_EXIT_WORDING)


# -- process-tree control --------------------------------------------------


def terminate_process_tree(
    process: subprocess.Popen[bytes], job: Any = None, *, group: int | None = None
) -> None:
    child_process.terminate_process_tree(
        process, job, group=group, grace_seconds=_TERMINATE_GRACE_S
    )


class _BoundedDrain(child_process.PipeDrain):
    def __init__(self, stream: Any, limit: int = MAX_RETAINED_STDERR_BYTES) -> None:
        super().__init__(
            stream, limit, truncation_prefix="...(earlier output discarded)...\n"
        )


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
    result_path: Path,
    *,
    stage: str,
    result_bytes: int,
    returncode: int | None,
    diagnostics: str,
) -> dict[str, Any]:
    if not result_path.is_file() or result_path.is_symlink():
        raise ChildRefusal(
            stage,
            f"the child exited {describe_exit(returncode)} without writing a "
            "structured result",
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
    # The one staging root that keeps `mkdtemp`: it is a sandbox for an
    # untrusted child, it lives in a temporary directory rather than beside a
    # destination, and nothing here is published anywhere a later run has to
    # read. Its 0o700 is the point, not the hazard `server/platform/staging.py`
    # describes. In a launched server that directory is the process's own
    # session: the `finally` below is skipped by a forced exit, and there the
    # next start's sweep removes what it left.
    root = Path(tempfile.mkdtemp(prefix="wg-cad-child-", dir=temporary_directory_root()))
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
        # Not a plain rmtree: on Windows it cannot delete the read-only STEP.
        remove_tree(root)
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
    command = child_command(entrypoint)
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
        # No breakaway: the status window's job allows it for the official BEAT
        # host alone (``launchers/statusapp/controller.py``). This child nests
        # its own job inside whatever job the server is in, so the launcher's
        # job close still reaches it however the server ended.
        popen_kwargs["creationflags"] = _windows_creation_flags(breakaway=False)

    # On Windows the child is created suspended and confined before it runs.
    # In the bundle sys.executable is the native stub, which starts the real
    # interpreter as its own child at once; a job assigned after Popen returns
    # can miss that interpreter, which would then run outside every limit.
    started = JobStart()
    try:
        with windows_job_start(
            lambda _pid, handle: _assign_windows_job(handle, budget),
            required=True,
            subject="the isolated CAD child",
        ) as started:
            process = start_child_process(command, popen_kwargs, stage=stage)
    except ContainedStartError as exc:
        # The child was stopped before it ran a single instruction.
        raise ChildRefusal(stage, str(exc)) from exc

    process_group = _posix_process_group(process) if os.name == "posix" else None
    job = started.job
    if os.name == "nt" and not started.fired:
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
        # The drain owns the pipe and closes it at EOF. Closing it from here
        # while a read is blocked would wait for that read, which never ends
        # if something outside the tree still holds the far end.
        diagnostics = drain.text()
        if job is not None:
            with contextlib.suppress(Exception):
                job.close()

    try:
        return _verified_answer(
            envelope,
            staging=staging,
            out_dir=out_dir,
            budget=budget,
            allowed_artifacts=allowed_artifacts,
            stage=stage,
            killed_for=killed_for,
            returncode=process.returncode,
            diagnostics=diagnostics,
        )
    except ChildRefusal as exc:
        exc.returncode = process.returncode
        _log_child_refusal(str(envelope["task"]), exc)
        raise


def _log_child_refusal(task: str, refusal: ChildRefusal) -> None:
    """Put a refused child's exit and last output in the server log.

    Callers turn the refusal into an answer that carries only its stage and
    wording. That is right for the user and useless for diagnosis: a child
    that could not import its own package reads exactly like one OCC killed.
    The exit and the output tail are what tell them apart. The tail is the
    untrusted child's text, so every line of it is indented: none can pass for
    a record of the server's own.
    """

    lines = refusal.diagnostics.strip("\n").splitlines() or ["(none)"]
    logger.warning(
        "isolated CAD %s task refused at %s; the child exited %s: %s\n"
        "child output (tail):\n%s",
        task,
        refusal.stage,
        describe_exit(refusal.returncode),
        refusal.detail,
        "\n".join("    | " + line for line in lines),
    )


def _verified_answer(
    envelope: Mapping[str, Any],
    *,
    staging: Path,
    out_dir: Path,
    budget: ChildBudget,
    allowed_artifacts: Sequence[str],
    stage: str,
    killed_for: str | None,
    returncode: int | None,
    diagnostics: str,
) -> ChildOutcome:
    """Accept an exited child's answer only if every check on it holds."""

    if killed_for is not None:
        raise ChildRefusal(stage, killed_for, diagnostics=diagnostics)

    envelope_out = _read_result(
        staging / "result.json",
        stage=stage,
        result_bytes=budget.result_bytes,
        returncode=returncode,
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
    process: subprocess.Popen[bytes], budget: ChildBudget, *,
    job: Any = None, group: int | None = None,
) -> str | None:
    def resolve_group() -> None:
        nonlocal group
        if group is None and os.name == "posix":
            group = _posix_process_group(process)

    def resource_check() -> str | None:
        if group is not None:
            resident = process_group_rss_bytes(group)
            if resident is not None and resident > budget.memory_bytes:
                return (
                    f"the isolated CAD child reached {resident:,} bytes resident, over "
                    f"its {budget.memory_bytes:,} byte budget, and was stopped"
                )
        return None

    return child_process.supervise(
        process, wall_time_s=budget.wall_time_s, interval_s=_WATCHDOG_INTERVAL_S,
        stop=lambda: terminate_process_tree(process, job, group=group),
        resource_check=resource_check,
        before_wait=resolve_group,
        timeout_reason=(
            f"the isolated CAD child exceeded its {budget.wall_time_s:g} second "
            "deadline and was stopped with its descendants"
        ),
    )


# -- Windows job objects ---------------------------------------------------


_configure_windows_job_api = process_tree._declare_job_api


def start_child_process(
    command: list[str], popen_kwargs: dict[str, Any], *, stage: str
) -> subprocess.Popen[bytes]:
    try:
        return child_process.start_popen(command, popen_kwargs, retry_windows=True)
    except OSError as exc:
        raise ChildRefusal(
            stage, f"could not start the isolated CAD child: {exc}"
        ) from exc


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


def _assign_windows_job(process: subprocess.Popen[bytes] | int, budget: ChildBudget) -> Any:
    return create_windows_job(
        process, memory_bytes=budget.memory_bytes, active_process_limit=32,
        suppress_errors=False, error_before_close=True,
    )


__all__ = [
    "CHILD_ENTRYPOINT",
    "INSPECT_BUDGET",
    "MESH_BUDGET",
    "PROTOCOL_VERSION",
    "ChildBudget",
    "ChildOutcome",
    "ChildRefusal",
    "child_command",
    "child_environment",
    "describe_exit",
    "isolated_step_task",
    "process_group_rss_bytes",
    "terminate_process_tree",
]
