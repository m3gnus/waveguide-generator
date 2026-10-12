"""One backend owner per data folder: find it, or start it, exactly once.

A client (the CLI, the Fusion add-in launcher) submits to the backend owning its
exact data folder. InstanceLock on ``<data>/locks/server.pid``, taken by the
supported backend, is the authority. Discovery never creates or locks that file:
it reads metadata, then confirms that ``GET /health`` reports the same folder
(filesystem identity) and pid. Missing, stale or mismatched answers mean no
confirmed owner yet; the preferred metadata port may change during startup.

Starting is serialised on ``<data>/locks/backend-start.lock``. Exit 2 means
another starter owns InstanceLock: wait for it until the overall deadline,
respawning (0.5 s apart) only when that owner is retiring or gone. A
matching-pid backend reporting a different folder is a typed refusal only at
the deadline. Never work around conflicts with a private folder.
A timeout terminates and reaps our unconfirmed child; success retains the owner.
Windows CLI clients can opt into a contained lifetime after breakaway refusal.

``start_argv`` must start the supported backend on this exact data folder with
``--no-browser`` (see ``launchers.backend_start``).
Stdlib and platform process/locking helpers only, without loading the application.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .instance import (
    LOCK_FILENAME,
    LOCK_OPEN_FLAGS,
    InstanceInfo,
    lock_exclusive,
    pid_is_running,
    read_lock_info,
    unlock,
)

from .paths import display_path

START_LOCK_FILENAME = "backend-start.lock"
LOG_FILENAME = "backend-start.log"
HEALTH_TIMEOUT = 2.0
POLL_INTERVAL = 0.1
TERMINATE_GRACE = 1.0
EXIT_ALREADY_RUNNING = 2


@dataclass(frozen=True, slots=True)
class BackendHandle:
    base_url: str
    pid: int
    port: int
    data_dir: Path
    version: str
    commit: str
    contained: bool = False


class BackendOwnerError(RuntimeError):
    """Base class; the message always says what to do next."""


class BackendDataDirMismatch(BackendOwnerError):
    """At the deadline, the metadata pid serves a different data folder."""


class BackendStartTimeout(BackendOwnerError):
    """No confirmed owner became healthy before the deadline."""


class BackendSpawnError(BackendOwnerError):
    """The backend could not be started."""


def _locks_dir(data_dir: Path) -> Path:
    return data_dir / "locks"


def _owner_info(data_dir: Path) -> InstanceInfo | None:
    try:
        return read_lock_info(_locks_dir(data_dir) / LOCK_FILENAME)
    except RuntimeError:
        return None


def _owner_description(data_dir: Path) -> str:
    info = _owner_info(data_dir)
    if info is None:
        return "Instance-lock metadata is not available yet."
    return f"Instance-lock metadata names pid {info.pid}, port {info.port}."


def _fetch_health(port: int) -> dict[str, object] | None:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health")
    try:
        with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def _same_path(left: object, right: Path) -> bool:
    if not isinstance(left, (str, os.PathLike)):
        return False
    try:
        try:
            return os.path.samefile(left, right)
        except FileNotFoundError:
            return Path(left).resolve() == right.resolve()
    except (OSError, ValueError):
        return False


def _observe(data_dir: Path) -> tuple[InstanceInfo | None, dict[str, object] | None]:
    info = _owner_info(data_dir)
    health = _fetch_health(info.port) if info is not None and info.port > 0 else None
    return info, health


def _validate(
    data_dir: Path, info: InstanceInfo | None, health: dict[str, object] | None
) -> BackendHandle | None:
    if info is None or health is None or health.get("retiring") is True:
        return None
    if health.get("pid") != info.pid or not _same_path(health.get("data_dir"), data_dir):
        return None
    return BackendHandle(
        base_url=f"http://127.0.0.1:{info.port}",
        pid=info.pid,
        port=info.port,
        data_dir=data_dir,
        version=str(health.get("version", "")),
        commit=str(health.get("commit", "")),
        contained=health.get("lifetime") == "contained",
    )


def _discover(data_dir: Path) -> BackendHandle | None:
    return _validate(data_dir, *_observe(data_dir))


def discover_backend(data_dir: str | os.PathLike[str]) -> BackendHandle | None:
    """Confirm the metadata pid's healthy backend for this folder, or return None.

    Never create or acquire server.pid's lock, nor refuse transient mismatches.
    """

    return _discover(Path(data_dir).resolve())


class _StartLock:
    """Blocking cross-process lock with a timeout, built on the instance helpers."""

    def __init__(self, path: Path):
        self.path = path
        self._descriptor: int | None = None

    def acquire(self, deadline: float) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, LOCK_OPEN_FLAGS, 0o600)
        except OSError as exc:
            raise BackendOwnerError(
                f"Could not open backend start lock {self.path}: {exc}. "
                "Check that the data folder is writable and its locks directory is accessible."
            ) from exc
        try:
            while True:
                try:
                    lock_exclusive(descriptor)
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BackendStartTimeout(
                            f"Another client is starting the backend (lock {self.path}) and it "
                            f"did not finish in time. {_owner_description(self.path.parent.parent)} "
                            "Check the backend log in the data folder's logs directory, then try again."
                        ) from None
                    time.sleep(POLL_INTERVAL)
                    continue
                except OSError as exc:
                    raise BackendOwnerError(
                        f"Could not acquire backend start lock {self.path}: {exc}. "
                        "Check permissions and filesystem locking support for the data folder."
                    ) from exc
                self._descriptor = descriptor
                return
        except BaseException:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            unlock(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def _breakaway_refusal(exc: BaseException) -> BaseException | None:
    from server.platform.process_tree import BreakawayRefused

    while exc is not None:
        if isinstance(exc, BreakawayRefused):
            return exc
        exc = exc.__cause__
    return None


def _spawn(
    argv: Sequence[str], data_dir: Path, env: Mapping[str, str] | None = None,
    *, allow_contained: bool = False,
) -> tuple[subprocess.Popen[bytes], Path]:
    try:
        return _spawn_attempt(argv, data_dir, env, contained=False)
    except BackendSpawnError as exc:
        if not allow_contained or _breakaway_refusal(exc) is None:
            raise
        # The first attempt has already stopped its unconfirmed tree. Retry
        # through the same suspended start and nested job, with a shorter lifetime.
        return _spawn_attempt(argv, data_dir, env, contained=True)


def _spawn_attempt(
    argv: Sequence[str], data_dir: Path, env: Mapping[str, str] | None,
    *, contained: bool,
) -> tuple[subprocess.Popen[bytes], Path]:
    log_dir = data_dir / "logs"
    log_path = log_dir / LOG_FILENAME
    kwargs: dict[str, object] = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    job = None
    process = None
    child_env = env
    if contained or "WG2_BACKEND_CONTAINED" in (os.environ if env is None else env):
        child_env = dict(os.environ if env is None else env)
        child_env.pop("WG2_BACKEND_CONTAINED", None)
        if contained:
            child_env["WG2_BACKEND_CONTAINED"] = "1"
    try:
        if sys.platform == "win32":
            from server.platform.process_tree import windows_backend_breakaway_flags

            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | (0 if contained else windows_backend_breakaway_flags())
            )
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log_file:
            if contained:
                log_file.write(b"job breakaway: refused (contained)\n")
                log_file.flush()
            # The Windows entry is itself a stub: own its interpreter before
            # it runs, but allow a confirmed backend to survive this client.
            from server.platform.job_start import windows_job_start
            from server.platform.process_tree import confine_to_windows_job

            def admit(pid, process_handle):
                from server.platform.process_tree import require_windows_job_detached

                if not contained:
                    require_windows_job_detached(process_handle)
                return confine_to_windows_job(
                    pid, subject="the starting backend", kill_on_close=False, allow_breakaway=True
                )

            with windows_job_start(
                admit, required=True, subject="the starting backend",
            ) as started:
                process = subprocess.Popen(  # noqa: S603
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    env=child_env,
                    **kwargs,  # type: ignore[arg-type]
                )
            job = started.job
            if sys.platform == "win32" and not started.fired:
                from server.platform.process_tree import require_windows_job_detached

                if not contained:
                    require_windows_job_detached(process._handle)
                job = confine_to_windows_job(
                    process.pid, subject="the starting backend", kill_on_close=False, allow_breakaway=True
                )
                if job is None:
                    _terminate_child(process)
                    raise OSError("Could not contain the backend process tree")
            process._wg_start_job = job  # type: ignore[attr-defined]
    except BaseException as exc:
        if process is not None:
            _terminate_child(process)
        if job is not None:
            job.terminate()
            job.close()
        from server.platform.job_start import ContainedStartError
        if not isinstance(exc, (OSError, ContainedStartError)):
            raise
        detail = str(exc).rstrip().rstrip(".") + "."
        if _breakaway_refusal(exc) is not None:
            raise BackendSpawnError(
                f'Could not start the backend ("{display_path(argv[0])}"): {detail} '
                "Open Waveguide Generator from the Start menu and try again, "
                "or run outside the CI/IDE job that forbids breakaway."
            ) from exc
        raise BackendSpawnError(
            f'Could not start the backend ("{display_path(argv[0])}"): {detail} Check the installation, '
            "or start Waveguide Generator manually."
        ) from exc
    return process, log_path


def _log_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 4096))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return "(start log unavailable)"


def _terminate_child(process: subprocess.Popen[bytes]) -> str:
    job = getattr(process, "_wg_start_job", None)
    if job is not None:
        job.terminate()
    if process.poll() is not None:
        process.wait()
        return ""
    try:
        process.terminate()
    except ProcessLookupError:
        pass  # The child exited between poll() and terminate(); still reap it.
    try:
        process.wait(timeout=TERMINATE_GRACE)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return f" Terminated and waited for unconfirmed spawned child pid {process.pid}."


def ensure_backend(
    data_dir: str | os.PathLike[str],
    start_argv: Sequence[str],
    *,
    timeout: float = 60,
    start_env: Mapping[str, str] | None = None,
    allow_contained: bool = False,
) -> BackendHandle:
    """Find or start the owner; start_argv must use this folder and --no-browser.

    Exit 2 waits for a live owner, retrying when it is retiring or gone. Discovery
    mismatches are transient; a matching-pid folder mismatch refuses at deadline.
    ``start_env`` is the launcher's prepared child environment. An interrupted
    wait terminates/reaps only our unconfirmed spawned child, then releases the
    start lock; a confirmed owner is retained. ``allow_contained`` lets CLI
    clients accept a backend tied to a Windows job that refuses breakaway.
    """

    root = Path(data_dir).resolve()
    deadline = time.monotonic() + timeout
    found = _discover(root)
    if found is not None:
        return found

    start_lock = _StartLock(_locks_dir(root) / START_LOCK_FILENAME)
    start_lock.acquire(deadline)
    try:
        found = _discover(root)
        if found is not None:
            return found
        if allow_contained:
            return _start_and_wait(root, start_argv, deadline, start_env, allow_contained=True)
        return _start_and_wait(root, start_argv, deadline, start_env)
    finally:
        start_lock.release()


def _start_and_wait(
    root: Path, argv: Sequence[str], deadline: float, env: Mapping[str, str] | None = None,
    *, allow_contained: bool = False,
) -> BackendHandle:
    if allow_contained:
        process, log_path = _spawn(argv, root, env, allow_contained=True)
    else:
        process, log_path = _spawn(argv, root) if env is None else _spawn(argv, root, env)
    try:
        while True:
            info, health = _observe(root)
            found = _validate(root, info, health)
            if found is not None:
                return found
            code = process.poll()
            if code is not None and code not in (0, EXIT_ALREADY_RUNNING):
                raise BackendSpawnError(
                    f"The backend exited with code {code} before it was ready. "
                    f"See {log_path}. Start log tail:\n{_log_tail(log_path)}"
                )
            if time.monotonic() >= deadline:
                cleanup = _terminate_child(process)
                # A same-pid backend serving another folder should be impossible for
                # the supported backend. Refuse only now: preferred-port conflicts
                # and metadata updates are normal during a legitimate startup.
                if (
                    info is not None
                    and health is not None
                    and health.get("pid") == info.pid
                    and not _same_path(health.get("data_dir"), root)
                ):
                    raise BackendDataDirMismatch(
                        f"The backend pid {info.pid} on port {info.port} reports data folder "
                        f"{health.get('data_dir')!r}, not {str(root)!r}, at the startup deadline. "
                        "Stop that backend or correct its data-folder configuration; do not "
                        f"work around this with a different data folder. See {log_path}.{cleanup}"
                    )
                raise BackendStartTimeout(
                    "The backend did not become ready within the timeout. "
                    f"{_owner_description(root)} See {log_path}.{cleanup}"
                )
            if code == EXIT_ALREADY_RUNNING and (
                info is None or not pid_is_running(info.pid)
                or (health is not None and health.get("pid") == info.pid
                    and health.get("retiring") is True)
            ):
                process.wait()
                job = getattr(process, "_wg_start_job", None)
                if job is not None:
                    job.close()
                    process._wg_start_job = None
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
                if time.monotonic() < deadline:
                    process, log_path = _spawn(argv, root) if env is None else _spawn(argv, root, env)
                continue
            time.sleep(POLL_INTERVAL)
    except BaseException:
        # Signals use this same cleanup as timeout. Confirm once more in case
        # ownership became healthy just as the caller stopped waiting.
        found = _discover(root)
        job = getattr(process, "_wg_start_job", None)
        confirmed_child = found is not None and (
            found.pid == process.pid or (job is not None and job.contains(found.pid))
        )
        if not confirmed_child:
            _terminate_child(process)
        raise
    finally:
        job = getattr(process, "_wg_start_job", None)
        if job is not None:
            job.close()
