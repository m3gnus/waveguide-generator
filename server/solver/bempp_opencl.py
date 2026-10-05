"""Qualify OpenCL in disposable processes before selecting it for BEMPP.

Enumeration, driver imports, context creation and kernel execution can all hang.
Native work runs in bounded children, waited on by the background capability
thread or the isolated solve/warmup worker, never by startup or the event loop.
Timeouts and internal probe errors are transient; passes and definitive
rejections persist for the process.
"""
from __future__ import annotations

from contextvars import ContextVar
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
import importlib
import json
import logging
import os
import queue
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import time
import traceback
from typing import Any, Mapping

# Only driver enumeration/kernel execution consumes these budgets. Child
# interpreter startup and imports compete with the app's own cold imports.
INVENTORY_SECONDS = 10.0
PROBE_SECONDS = 20.0
TOTAL_SECONDS = 30.0
# Per child, including process creation. Cold Windows imports may be slow;
# a hung import must still end and use the existing transient timeout codes.
SPAWN_IMPORT_SECONDS = 60.0
# Initial attempt plus two retries, at least 5s after each transient failure.
# Serialize attempts so concurrent capability/solve requests cannot pile up.
RETRY_INTERVAL_SECONDS = 5.0
MAX_TIMEOUT_ATTEMPTS = 3
# Failure evidence stays separate from the short capability/UI reason. Native
# compiler exceptions contain their build log, options and saved source path on
# later lines; retain those within a fixed parent/child diagnostic budget.
PROBE_DIAGNOSTIC_CHARS = 16 * 1024


def _bounded_diagnostic(text: str) -> str:
    if len(text) <= PROBE_DIAGNOSTIC_CHARS:
        return text
    marker = "\n[... OpenCL diagnostic truncated ...]\n"
    head = (PROBE_DIAGNOSTIC_CHARS - len(marker)) // 2
    tail = PROBE_DIAGNOSTIC_CHARS - len(marker) - head
    return text[:head] + marker + text[-tail:]


def qualification_max_seconds() -> float:
    """Inventory + smoke startup, shared active budget, then retry cooldowns.

    INVENTORY_SECONDS and PROBE_SECONDS split TOTAL_SECONDS; they do not add
    another active budget. Compute dynamically so publication follows limits.
    """
    return MAX_TIMEOUT_ATTEMPTS * _attempt_max_seconds() + (
        max(0, MAX_TIMEOUT_ATTEMPTS - 1) * RETRY_INTERVAL_SECONDS
    )


def _attempt_max_seconds() -> float:
    return 2 * SPAWN_IMPORT_SECONDS + TOTAL_SECONDS


OPENCL_UNAVAILABLE_REASONS = frozenset({
    "no_device", "inventory_timeout", "smoke_test_failed", "smoke_test_timeout", "pocl_windows", "probe_error",
})
TRANSIENT_REASONS = frozenset({"inventory_timeout", "smoke_test_timeout", "probe_error"})
_READY_MARKER = "WG_OPENCL_READY"
_selection_lock = threading.Lock()
_device_verdict_cache: dict[str, dict[str, Any]] = {}
_cached_verdict: dict[str, Any] | None = None
_last_timeout: dict[str, Any] | None = None
_timeout_attempts = 0
_retry_after = 0.0
_revision = 0


def inventory() -> list[dict[str, Any]]:
    """Stable platform/device indices, regardless of CPU or runtime vendor."""
    import pyopencl as cl

    devices = []
    for pi, platform in enumerate(cl.get_platforms()):
        try:
            entries = platform.get_devices()
        except Exception:
            continue
        for di, device in enumerate(entries):
            kind = "gpu" if device.type & cl.device_type.GPU else (
                "cpu" if device.type & cl.device_type.CPU else None
            )
            if kind is None:
                continue
            extensions = set(str(device.extensions).split())
            devices.append({
                "platform_index": pi, "device_index": di, "type": kind,
                "platform": platform.name.strip(), "name": device.name.strip(),
                "vendor": device.vendor.strip(),
                "fp64": bool(device.double_fp_config or extensions & {"cl_khr_fp64", "cl_amd_fp64"}),
            })
    return devices


def rank_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Owner decision: BEMPP is the engine for CPU-only computers and does not
    # work well on GPU OpenCL drivers. Never qualify or select a GPU.
    # Keep CPU enumeration order (platform/device indices), with no vendor
    # preference: Intel's CPU runtime also works on AMD Ryzen CPUs.
    return [device for device in devices if device["type"] == "cpu"]


@lru_cache(maxsize=None)
def _bind_device(device_json: str) -> None:
    device = json.loads(device_json)
    if device["type"] != "cpu":
        raise RuntimeError("BEMPP refuses GPU OpenCL devices; only CPU devices are eligible")
    import pyopencl as cl
    import bempp_cl.api as api
    from hornlab_bempp_bem.device import configure_opencl, reset_opencl_device

    candidate = cl.get_platforms()[device["platform_index"]].get_devices()[device["device_index"]]
    if not candidate.type & cl.device_type.CPU or candidate.type & cl.device_type.GPU:
        raise RuntimeError("BEMPP refuses a non-CPU OpenCL device")
    if (candidate.name.strip(), candidate.vendor.strip(), candidate.platform.name.strip()) != (
        device["name"], device["vendor"], device["platform"],
    ):
        raise RuntimeError("OpenCL inventory changed after qualification; refusing another device")
    kernels = importlib.import_module("bempp_cl.core.opencl_kernels")
    # Inspect the pinned slots without calling a default getter: an unset slot
    # would enumerate an ALL-device context and could choose its last GPU.
    selected = getattr(kernels, "_DEFAULT_CPU_DEVICE", None)
    context = getattr(kernels, "_DEFAULT_CPU_CONTEXT", None)
    if selected != candidate or list(getattr(context, "devices", [])) != [candidate]:
        api.set_default_cpu_device(device["platform_index"], device["device_index"])
    selected = kernels.default_cpu_device()
    if not selected.type & cl.device_type.CPU or selected.type & cl.device_type.GPU:
        raise RuntimeError("BEMPP refuses a non-CPU OpenCL device")
    if (selected.name.strip(), selected.vendor.strip(), selected.platform.name.strip()) != (
        device["name"], device["vendor"], device["platform"],
    ):
        raise RuntimeError("OpenCL inventory changed after qualification; refusing another device")
    # Singular, regular and potential assembly must use the same CPU device.
    reset_opencl_device()
    configure_opencl("cpu")


def bind_device(device: Mapping[str, Any], *, force: bool = False) -> None:
    """Bind a qualified device in the killable process that will actually solve."""
    binder = _bind_device.__wrapped__ if force else _bind_device
    binder(json.dumps(dict(device), sort_keys=True))


def execution_route(verdict: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
    """The sole backend/device decision, from WG's compute qualification.

    The optional verdict lets capability reporting use the same probe snapshot.
    Native callers always resolve the current process's own qualification.
    """
    verdict = qualified_opencl() if verdict is None else verdict
    if not verdict.get("ok"):
        if verdict.get("stage") == "engine":
            raise RuntimeError(f"BEMPP engine unavailable: {verdict.get('reason')}")
        return "numba", None
    device = verdict.get("device")
    if not isinstance(device, Mapping) or device.get("type") != "cpu":
        raise RuntimeError("BEMPP refuses GPU or unqualified OpenCL devices")
    return "opencl", dict(device)


def guard_execution(backend: str | None, opencl_device: str | None) -> None:
    """Refuse implicit/default selection and bind only the qualified CPU.

    Revalidate before every native call: a cached bind cannot protect against
    a library replacing its default slot between assembly and evaluation.
    Retain a matching one-CPU context so warmed kernels remain reusable.
    Numba never enumerates or initializes an OpenCL device here.
    """
    if backend not in {"numba", "opencl"} or opencl_device != "cpu":
        raise RuntimeError("BEMPP refuses GPU, implicit or unqualified execution; "
                           f"requested {backend!r}/{opencl_device!r}")
    if backend == "numba":
        return
    qualified_backend, device = execution_route()
    if qualified_backend != "opencl" or device is None:
        raise RuntimeError("BEMPP refuses unqualified OpenCL execution")
    bind_device(device, force=True)


def _keep_bempp_scratch_in_session() -> None:
    """Move bempp-cl's import-time scratch directory into WG's session.

    ``bempp_cl.api`` runs ``TMP_PATH = tempfile.mkdtemp()`` when imported and
    never removes it, so every process that imported it left one empty ``tmp*``
    directory in the system temporary directory: five after one start and a
    solve in the 0.3.4 Windows rehearsal. Only bempp's own shapes and viewers
    write there. In the server and its BEMPP worker the empty directory is
    removed and ``TMP_PATH`` pointed at the session, which is swept however the
    process ends. The OpenCL check child is handled in ``_child_main``.
    """

    try:
        from server.platform.temp_session import spawned_directory_root
        import bempp_cl.api as api
    except (ImportError, OSError):
        return
    root = spawned_directory_root()
    current = getattr(api, "TMP_PATH", None)
    if root is None or not isinstance(current, str) or current == root:
        return
    try:
        # Only ever bempp's own empty directory; anything it wrote there stays.
        os.rmdir(current)
    except OSError:
        return
    api.TMP_PATH = root


def _initialize_frequency_worker(session_root: str | None, initializer: Any, initargs: tuple) -> None:
    """Adopt the solve worker's session before a spawned sweep starts work."""
    from server.platform.temp_session import adopt_parent_session

    adopt_parent_session(session_root)
    if initializer is not None:
        initializer(*initargs)
    _keep_bempp_scratch_in_session()


def _frequency_pool(*args: Any, **kwargs: Any) -> ProcessPoolExecutor:
    from server.platform.temp_session import spawned_directory_root

    initializer = kwargs.pop("initializer", None)
    initargs = kwargs.pop("initargs", ())
    return ProcessPoolExecutor(
        *args, initializer=_initialize_frequency_worker,
        initargs=(spawned_directory_root(), initializer, initargs), **kwargs,
    )


def native_call(function: Any, *args: Any, execution_config: Any = None, **kwargs: Any) -> Any:
    """Lowest shared boundary for solves and potential/field evaluation."""
    backend = (getattr(execution_config, "assembly_backend", None)
               if execution_config is not None else kwargs.get("assembly_backend"))
    device = (getattr(execution_config, "opencl_device", None)
              if execution_config is not None else kwargs.get("opencl_device"))
    guard_execution(backend, device)
    _keep_bempp_scratch_in_session()
    # The pinned sweep creates its spawn pool without an initializer. Keep
    # this integration in WG so the dependency need not know about sessions.
    try:
        from hornlab_bempp_bem import sweep
    except (ImportError, OSError):
        pass
    else:
        sweep.ProcessPoolExecutor = _frequency_pool
    return function(*args, **kwargs)


def reference_matrix() -> Any:
    """Pinned bempp-cl 0.4.2 numba/single DP0 Helmholtz SLP, k=1, q4/q4.

    Unit octahedron (regular_sphere(0)); four entries by number of shared
    vertices. The opposite faces exercise regular assembly and touching faces
    exercise singular assembly. Stored values avoid a numba JIT at startup.
    """
    import numpy as np

    faces = np.array([[2, 1, 3, 0, 5, 5, 5, 5],
                      [4, 4, 4, 4, 2, 1, 3, 0],
                      [0, 2, 1, 3, 0, 2, 1, 3]])
    values = np.array([0.01342794206 + 0.04447117820j,
                       0.03088685125 + 0.04836596176j,
                       0.06467797607 + 0.05236354843j,
                       0.17065817118 + 0.05646510422j])
    return np.array([[values[len(set(a) & set(b))] for b in faces.T] for a in faces.T])


class ComputationFailed(RuntimeError):
    """The device ran, but its numerical result failed qualification."""


def check_computation(matrix: Any) -> dict[str, float]:
    """Reject zero, partial, non-finite and numerically wrong computation."""
    import numpy as np

    reference = reference_matrix()
    if matrix.shape != reference.shape or not np.all(np.isfinite(matrix)) or not np.any(matrix):
        raise ComputationFailed("OpenCL smoke assembly is zero, non-finite or has the wrong shape")
    matrix_error = float(np.linalg.norm(matrix - reference) / np.linalg.norm(reference))
    rhs = np.arange(1, 9, dtype=np.float32)
    try:
        solution = np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError as exc:
        raise ComputationFailed("OpenCL smoke assembly is singular") from exc
    expected = np.linalg.solve(reference, rhs)
    solve_error = float(np.linalg.norm(solution - expected) / np.linalg.norm(expected))
    # Single precision across vendor kernels, comfortably below audible error.
    if not np.all(np.isfinite(solution)) or matrix_error > 2e-4 or solve_error > 5e-4:
        raise ComputationFailed(f"OpenCL smoke disagrees with numba: matrix={matrix_error:.3g}, solve={solve_error:.3g}")
    return {"matrix_relative_error": matrix_error, "solve_relative_error": solve_error}


def smoke_test(device: Mapping[str, Any]) -> dict[str, float]:
    if device["type"] != "cpu":
        raise RuntimeError("BEMPP refuses GPU OpenCL devices; only CPU devices are eligible")
    import numpy as np
    import bempp_cl.api as api

    bind_device(device, force=True)
    grid = api.shapes.regular_sphere(0)
    space = api.function_space(grid, "DP", 0)
    parameters = api.DefaultParameters()
    parameters.quadrature.regular = 4
    parameters.quadrature.singular = 4
    matrix = np.asarray(api.operators.boundary.helmholtz.single_layer(
        space, space, space, 1.0, parameters=parameters, assembler="dense",
        device_interface="opencl", precision="single",
    ).weak_form().to_dense())
    return check_computation(matrix)


# Each registry owns a cancellation token carried into its executor threads.
# Holding this lock through spawn closes the shutdown-versus-registration race.
_probe_lock = threading.Lock()
_probe_owner: ContextVar[threading.Event | None] = ContextVar("opencl_probe_owner", default=None)
_attempt_deadline: ContextVar[float] = ContextVar("opencl_attempt_deadline", default=float("inf"))
_active_probes: dict[_ProbeHandle, threading.Event | None] = {}
# Each run's result directory, by owner. A stop waits for the runs to remove
# their own; a run still busy when the wait ends has its directory removed here.
_active_channels: dict[Any, threading.Event | None] = {}
_channels_idle = threading.Condition(_probe_lock)
SHUTDOWN_CLEANUP_SECONDS = 3.0


class ProbeCancelled(RuntimeError):
    """Shutdown is not a driver verdict and must never enter the cache."""


def _check_cancelled() -> None:
    owner = _probe_owner.get()
    if owner is not None and owner.is_set():
        raise ProbeCancelled("OpenCL qualification stopped")


def owned_qualification(owner: threading.Event, function: Any, *args: Any, **kwargs: Any) -> Any:
    token = _probe_owner.set(owner)
    try:
        _check_cancelled()
        return function(*args, **kwargs)
    finally:
        _probe_owner.reset(token)


class _ProbeHandle:
    def __init__(self, child: Any, readers: Any, events: Any, job: Any = None) -> None:
        self.child, self.readers, self.events, self.job = child, readers, events, job
        self.lock = threading.Lock()
        self.closed = False

    def close(self, *, graceful: bool = False) -> None:
        with self.lock:
            if self.closed:
                return
            try:
                if self.job is not None:
                    # The whole tree: in the Windows bundle the child is the
                    # native stub and the interpreter holding the pipes is its
                    # own child (server.platform.process_tree.popen_in_windows_job).
                    self.job.terminate()
                if self.child.poll() is None:
                    if graceful:
                        self.child.terminate()
                        try:
                            self.child.wait(timeout=0.25)
                        except subprocess.TimeoutExpired:
                            self.child.kill()
                    else:
                        self.child.kill()
                # Always reap, even if poll/terminate already observed an exit.
                self.child.wait(timeout=1.5)
                for reader in self.readers:
                    reader.join(timeout=1.0)
                stuck = [reader for reader in self.readers if reader.is_alive()]
                if stuck:
                    # A process the child started still holds the pipes (no job
                    # could be made). That is no verdict on the device, and
                    # raising here replaced the caller's verdict and wedged the
                    # registry on "Checking OpenCL…". Cancel the blocked reads
                    # so the readers end and the pipes can be closed; bounded,
                    # it never waits on the other process.
                    from server.platform.process_tree import cancel_blocked_reads

                    stuck = cancel_blocked_reads(stuck)
                    logging.getLogger(__name__).warning(
                        "The OpenCL check's output was still held after its process "
                        "was stopped (pid %s); a process it started may still be "
                        "running. %s", self.child.pid,
                        "Its output did not close." if stuck else "Its reads were cancelled.",
                    )
                if stuck:
                    # A read that could not be cancelled: closing its stream
                    # would block on the reader's buffer lock, so the daemon
                    # reader keeps it until the pipe ends.
                    pass
                else:
                    self.child.stdout.close()
                    self.child.stderr.close()
                self.closed = True
            finally:
                if self.job is not None:
                    # Kill-on-close: whatever is still in the job dies with it.
                    self.job.close()
                    self.job = None


REMOVE_ATTEMPTS = 20
REMOVE_RETRY_SECONDS = 0.05


def _remove_channel(channel: Any) -> None:
    """Remove a run's result directory, retrying while Windows still holds it.

    A file the child just closed, or one an indexer or virus scanner opened,
    can refuse deletion for a moment on Windows. A directory that still cannot
    be removed is logged, and left for the session's removal or the next start.
    """

    for attempt in range(REMOVE_ATTEMPTS):
        try:
            channel.cleanup()
        except OSError as exc:
            error = exc
        else:
            if not os.path.exists(channel.name):
                return
            error = None
        if attempt + 1 < REMOVE_ATTEMPTS:
            time.sleep(REMOVE_RETRY_SECONDS)
    logging.getLogger(__name__).warning(
        "Could not remove the OpenCL check's directory %s%s; it is left for the "
        "next start to remove", channel.name, f" ({error})" if error else "",
    )


def shutdown_qualification(owner: threading.Event) -> None:
    """Terminate/reap only this registry's children; safe on repeated shutdown."""
    with _probe_lock:
        owner.set()
        handles = [handle for handle, token in _active_probes.items() if token is owner]
    for handle in handles:
        handle.close(graceful=True)
        with _probe_lock:
            _active_probes.pop(handle, None)
        # Cancellation is checked before this synthetic EOF is consumed.
        handle.events.put((None, time.monotonic()))
    # The run's own finally removes its result directory after the child is
    # reaped. Wait for that, so a clean stop leaves nothing in the session
    # directory; whatever is still registered after the wait is removed here.
    deadline = time.monotonic() + SHUTDOWN_CLEANUP_SECONDS
    with _channels_idle:
        while any(token is owner for token in _active_channels.values()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _channels_idle.wait(remaining)
        leftovers = [channel for channel, token in _active_channels.items() if token is owner]
    for channel in leftovers:
        _remove_channel(channel)


def _read_probe_output(stream: Any, events: Any) -> None:
    """Timestamp only complete stdout READY lines, without trimming prefixes.

    Read bounded pieces and discard library logs, including oversized lines.
    EOF is timestamped too, so late completion cannot extend the deadline.
    """
    partial = False
    try:
        while line := stream.readline(4096):
            complete = line.endswith("\n")
            if complete and not partial and line.rstrip("\r\n") == _READY_MARKER:
                events.put((_READY_MARKER, time.monotonic()))
            partial = not complete
    except (OSError, ValueError):
        pass  # a cancelled read (cancel_blocked_reads) or a closed stream: EOF
    finally:
        events.put((None, time.monotonic()))


def _read_probe_stderr(stream: Any, tail: list[str]) -> None:
    """Continuously drain stderr on Windows/POSIX; retain at most 4 KB."""
    try:
        while chunk := stream.read(1024):
            tail[0] = (tail[0] + chunk)[-4096:]
    except (OSError, ValueError):
        pass  # a cancelled read (cancel_blocked_reads) or a closed stream: EOF


def _validate_probe_result(result: Any, mode: str) -> None:
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        raise ValueError("Invalid probe response")
    if result["ok"]:
        if mode == "inventory":
            devices = result.get("devices")
            if not isinstance(devices, list) or any(
                not isinstance(device, dict)
                or device.get("type") not in {"cpu", "gpu"}
                or any(type(device.get(key)) is not int or device[key] < 0
                       for key in ("platform_index", "device_index"))
                or any(not isinstance(device.get(key), str)
                       for key in ("platform", "name", "vendor"))
                for device in devices
            ):
                raise ValueError("Invalid inventory response")
        elif not isinstance(result.get("smoke"), dict):
            raise ValueError("Invalid smoke response")
    elif (result.get("opencl_unavailable_reason") not in OPENCL_UNAVAILABLE_REASONS
          or not isinstance(result.get("reason"), str)):
        raise ValueError("Invalid failure response")
    if "probe_diagnostic" in result and (not isinstance(result["probe_diagnostic"], str)
                                         or len(result["probe_diagnostic"]) > PROBE_DIAGNOSTIC_CHARS):
        raise ValueError("Invalid probe diagnostic")


def _run_probe(mode: str, device: Mapping[str, Any] | None, timeout: float) -> dict[str, Any]:
    # Parent-only imports: the standalone native child never allocates a
    # channel or starts a check of its own.
    from server.platform.process_tree import popen_in_windows_job
    from server.platform.temp_session import parent_session_lost, spawned_directory_root

    handle = None
    ready_at = None
    began = time.monotonic()
    deadline = min(began + SPAWN_IMPORT_SECONDS, _attempt_deadline.get())
    phase = "spawn/import"
    response = None
    stderr_tail = [""]
    channel = None
    try:
        # Created and registered in one step under the lock a stop takes, so a
        # stop either sees this directory or the run is refused before making it.
        with _probe_lock:
            _check_cancelled()
            if parent_session_lost():
                # A worker outliving the server's session: the server is
                # stopping. Make nothing, rather than a directory loose in the
                # system temporary directory that nothing would remove.
                raise ProbeCancelled("the server's temporary session is gone")
            # In the BEMPP solve worker too, which the server spawns: the
            # parent's session, never the bare temporary directory.
            # Resolve once and require a session: adoption can fail, or the
            # parent can disappear after the lost-session check.
            channel = tempfile.TemporaryDirectory(
                prefix="wg2-opencl-", dir=spawned_directory_root(required=True)
            )
            _active_channels[channel] = _probe_owner.get()
        result_path = Path(channel.name) / "result.json"
        # Independent readers avoid backpressure on both pipes, on every OS.
        # The result travels through an atomic file, never through library logs.
        with _probe_lock:
            _check_cancelled()
            child, job = popen_in_windows_job(
                [sys.executable, str(Path(__file__).resolve()), mode, json.dumps(device), str(result_path)],
                subject="the OpenCL check",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                env={**os.environ, "NUMBA_DISABLE_JIT": "1", "PYOPENCL_COMPILER_OUTPUT": "0"},
            )
            events = queue.Queue()
            readers = [
                threading.Thread(target=_read_probe_output, args=(child.stdout, events), daemon=True),
                threading.Thread(target=_read_probe_stderr, args=(child.stderr, stderr_tail), daemon=True),
            ]
            handle = _ProbeHandle(child, readers, events, job)
            _active_probes[handle] = _probe_owner.get()
            for reader in readers:
                reader.start()
        while True:
            remaining = deadline - time.monotonic()
            try:
                line, observed_at = events.get(timeout=max(0.0, remaining))
            except queue.Empty:
                raise subprocess.TimeoutExpired(child.args, timeout) from None
            _check_cancelled()
            if observed_at > deadline:
                raise subprocess.TimeoutExpired(child.args, timeout)
            if line == _READY_MARKER and ready_at is None:
                ready_at = observed_at
                deadline = min(ready_at + timeout, _attempt_deadline.get())
                phase = "compute"
            elif line is None:
                child.wait(timeout=max(0.001, deadline - time.monotonic()))
                if child.returncode:
                    raise ValueError(f"Child exited with status {child.returncode}")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                _validate_probe_result(result, mode)
                # Engine import failures may precede READY; successful native
                # work must always have a READY marker.
                if ready_at is None and (result["ok"] or result.get("opencl_unavailable_reason") in {
                    "smoke_test_failed", "pocl_windows",
                }):
                    raise ValueError("Missing ready marker")
                response = {**result, "_active_seconds": 0.0 if ready_at is None else observed_at - ready_at}
                return response
    except subprocess.TimeoutExpired:
        limit = SPAWN_IMPORT_SECONDS if ready_at is None else timeout
        response = {"ok": False, "opencl_unavailable_reason": "inventory_timeout" if mode == "inventory" else "smoke_test_timeout", "reason": f"OpenCL {mode} {phase} timed out after {limit:.1f}s.",
                    "_active_seconds": 0.0 if ready_at is None else timeout}
        return response
    except ProbeCancelled:
        raise
    except Exception as exc:
        detail = str(exc).splitlines()[0][:200] if str(exc) else ""
        response = {"ok": False, "opencl_unavailable_reason": "probe_error", "reason": f"WG's OpenCL {mode} check could not complete (internal error: {type(exc).__name__}{': ' + detail if detail else ''}).",
                    "_active_seconds": 0.0 if ready_at is None else min(timeout, time.monotonic() - ready_at)}
        return response
    finally:
        try:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    # Raised here it would replace this run's verdict, and a
                    # verdict is what bounds the retries; log it instead.
                    logging.getLogger(__name__).warning(
                        "Could not stop the OpenCL %s check's process cleanly", mode, exc_info=True,
                    )
                finally:
                    with _probe_lock:
                        _active_probes.pop(handle, None)
            if response is not None:
                if stderr_tail[0]:
                    response["probe_stderr"] = stderr_tail[0]
                if not response["ok"]:
                    logging.getLogger(__name__).warning(
                        "OpenCL %s check: %s%s%s", mode, response["reason"],
                        f"\nProbe diagnostic:\n{response['probe_diagnostic']}" if response.get("probe_diagnostic") else "",
                        f"\nProbe stderr: {stderr_tail[0]}" if stderr_tail[0] else "",
                    )
        finally:
            if channel is not None:
                try:
                    _remove_channel(channel)
                finally:
                    with _channels_idle:
                        _active_channels.pop(channel, None)
                        _channels_idle.notify_all()


def _device_verdict(device_json: str, timeout: float) -> dict[str, Any]:
    if device_json in _device_verdict_cache:
        return {**_device_verdict_cache[device_json], "_active_seconds": 0.0}
    verdict = _run_probe("smoke", json.loads(device_json), timeout)
    if verdict.get("opencl_unavailable_reason") == "probe_error":
        # WG's own check failing is not a verdict on the device. One immediate
        # second run lets a one-off failure (a child that died while PoCL
        # compiled its kernels, say) end on the device's real answer.
        # One budget across both runs: the second gets only what the first left.
        first = verdict
        remaining = timeout - first.get("_active_seconds", 0.0)
        if remaining <= 0:
            return first
        verdict = _run_probe("smoke", json.loads(device_json), remaining)
        verdict["_active_seconds"] = verdict.get("_active_seconds", 0.0) + first.get("_active_seconds", 0.0)
        if verdict.get("opencl_unavailable_reason") == "probe_error":
            verdict["reason"] = f"{verdict['reason']} First attempt: {first['reason']}"
            if first.get("probe_diagnostic") or verdict.get("probe_diagnostic"):
                verdict["probe_diagnostic"] = _bounded_diagnostic(
                    f"{verdict.get('probe_diagnostic', '')}\nFirst attempt diagnostic:\n{first.get('probe_diagnostic', '')}"
                )
    if verdict.get("opencl_unavailable_reason") not in TRANSIENT_REASONS:
        _device_verdict_cache[device_json] = verdict
    return verdict


def _qualified_opencl() -> dict[str, Any]:
    # Multiple CPU ICDs may each require another cold child. Bound their
    # combined startup too, so the published lifecycle ceiling remains true.
    token = _attempt_deadline.set(time.monotonic() + _attempt_max_seconds())
    try:
        return _probe_devices()
    finally:
        _attempt_deadline.reset(token)


def _probe_devices() -> dict[str, Any]:
    active_seconds = 0.0
    inventory_budget = min(INVENTORY_SECONDS, TOTAL_SECONDS)
    found = _run_probe("inventory", None, inventory_budget)
    if found.get("opencl_unavailable_reason") == "probe_error":
        # As for the smoke check: one immediate second run before reporting,
        # within the same inventory budget.
        first_seconds = found.get("_active_seconds", 0.0)
        if inventory_budget - first_seconds > 0:
            found = _run_probe("inventory", None, inventory_budget - first_seconds)
            found["_active_seconds"] = found.get("_active_seconds", 0.0) + first_seconds
    active_seconds += found.pop("_active_seconds", 0.0)
    if not found.get("ok"):
        return {**found, "opencl_unavailable_reason": found.get("opencl_unavailable_reason", "no_device")}
    devices = rank_devices(found["devices"])
    if not devices:
        return {
            "ok": False, "opencl_unavailable_reason": "no_device",
            "reason": "No CPU OpenCL device is present. A GPU OpenCL device does not "
                      "substitute: BEMPP is the engine for CPU-only computers and "
                      "does not work well on GPU OpenCL drivers. GPU engines are "
                      "separate (BEAT · CUDA on NVIDIA and BEAT · ROCm on AMD, both not yet qualified; Metal on Apple Silicon).",
        }
    failures = []
    diagnostics = []
    unavailable_reason = "smoke_test_failed"
    for device in devices:
        remaining = TOTAL_SECONDS - active_seconds
        if remaining <= 0 or time.monotonic() >= _attempt_deadline.get():
            failures.append("OpenCL qualification time budget exhausted")
            unavailable_reason = "smoke_test_timeout"
            break
        verdict = _device_verdict(json.dumps(device, sort_keys=True), min(PROBE_SECONDS, remaining))
        active_seconds += verdict.get("_active_seconds", 0.0)
        if verdict.get("ok"):
            return {"ok": True, "device": device, "smoke": verdict["smoke"],
                    "opencl_unavailable_reason": None,
                    "reason": f"OpenCL CPU {device['vendor']} {device['name']} passed BEMPP assembly/solve smoke"}
        code = verdict.get("opencl_unavailable_reason", "smoke_test_failed")
        if code in TRANSIENT_REASONS:
            unavailable_reason = code
        elif unavailable_reason not in TRANSIENT_REASONS and code == "smoke_test_failed" and sys.platform == "win32" and any(
            token in device["platform"].lower() for token in ("pocl", "portable computing language")
        ):
            unavailable_reason = "pocl_windows"
        failures.append(f"{device['name']}: {verdict.get('reason', 'smoke failed')}")
        if verdict.get("probe_stderr"):
            failures.append(f"Probe stderr: {verdict['probe_stderr']}")
        if verdict.get("probe_diagnostic"):
            diagnostics.append(f"{device['name']} ({verdict.get('stage', 'unknown stage')}):\n{verdict['probe_diagnostic']}")
    return {"ok": False, "opencl_unavailable_reason": unavailable_reason,
            "reason": "; ".join(failures),
            **({"probe_diagnostic": _bounded_diagnostic("\n".join(diagnostics))} if diagnostics else {})}


def retry_pending() -> bool:
    """Cheap state inspection; never waits on the probe lock/event loop."""
    return _last_timeout is not None and _cached_verdict is None


def retry_delay() -> float:
    return max(0.0, _retry_after - time.monotonic())


def retry_due() -> bool:
    return retry_pending() and time.monotonic() >= _retry_after


def qualification_revision() -> int:
    """Let registry snapshots notice a qualification done by another caller."""
    return _revision


def _internal_error_verdict(exc: BaseException) -> dict[str, Any]:
    detail = str(exc).splitlines()[0][:200] if str(exc) else ""
    return {
        "ok": False, "opencl_unavailable_reason": "probe_error",
        "reason": "WG's OpenCL check could not complete (internal error: "
                  f"{type(exc).__name__}{': ' + detail if detail else ''}).",
    }


def _record_verdict(verdict: dict[str, Any]) -> None:
    """Count one attempt. The caller holds ``_selection_lock``."""
    global _cached_verdict, _last_timeout, _timeout_attempts, _retry_after, _revision
    if verdict.get("opencl_unavailable_reason") in TRANSIENT_REASONS:
        _timeout_attempts += 1
        _last_timeout = verdict
        _retry_after = time.monotonic() + RETRY_INTERVAL_SECONDS
        if _timeout_attempts >= MAX_TIMEOUT_ATTEMPTS:
            # Bound repeated timeouts/internal errors; retain the true code
            # rather than misreporting an absent or numerically bad device.
            _cached_verdict = verdict
    else:
        _cached_verdict = verdict
        _last_timeout = None
        _timeout_attempts = 0
    _revision += 1


def qualified_opencl() -> dict[str, Any]:
    with _selection_lock:
        _check_cancelled()
        if _cached_verdict is not None:
            return dict(_cached_verdict)
        if _last_timeout is not None and not retry_due():
            return dict(_last_timeout)
        try:
            verdict = _qualified_opencl()
        except ProbeCancelled:
            raise
        except Exception as exc:
            # Never leave an attempt unrecorded: retry_pending() would stay
            # true with no verdict behind it, and the attempt cap could not end
            # the retries. An internal failure is a transient probe error.
            logging.getLogger(__name__).warning("OpenCL qualification failed internally", exc_info=True)
            verdict = _internal_error_verdict(exc)
        _check_cancelled()
        _record_verdict(verdict)
        return dict(verdict)


def record_failed_attempt(exc: BaseException, expected_revision: int) -> bool:
    """Count a status check that raised in place of an attempt that was due.

    The registry retries BEMPP on this module's interval and attempt cap. A
    raise that recorded nothing while a retry was due would leave it due
    forever, and the interface reads ``retry_pending()`` as "still checking".
    Nothing is counted, and False returned, when there is nothing to stand in
    for: a final verdict, a verdict newer than ``expected_revision`` (the call
    ran an attempt, or another caller did), or a retry that is not yet due.
    Decided under the attempt lock, so a concurrent attempt is never doubled.
    """
    with _selection_lock:
        if (_cached_verdict is not None or _revision != expected_revision
                or (_last_timeout is not None and not retry_due())):
            return False
        detail = str(exc).splitlines()[0][:200] if str(exc) else ""
        # Not an OpenCL verdict: the status check failed around it.
        _record_verdict({
            "ok": False, "opencl_unavailable_reason": "probe_error",
            "reason": "WG's BEMPP status check could not complete (internal error: "
                      f"{type(exc).__name__}{': ' + detail if detail else ''}).",
        })
        return True


def clear_cache() -> None:
    global _cached_verdict, _last_timeout, _timeout_attempts, _retry_after, _revision
    with _selection_lock:
        _cached_verdict = _last_timeout = None
        _timeout_attempts = 0
        _retry_after = 0.0
        _revision += 1
        _device_verdict_cache.clear()
        _bind_device.cache_clear()


def _child_result(mode: str, device: Mapping[str, Any] | None) -> dict[str, Any]:
    stage = "engine"
    try:
        import bempp_cl.api  # noqa: F401 - test the native engine import in the bounded child
        stage = "opencl"
        import pyopencl  # noqa: F401
        if mode != "inventory":
            import numpy  # noqa: F401
            import bempp_cl.core.opencl_kernels  # noqa: F401
            import hornlab_bempp_bem.device  # noqa: F401
        print(_READY_MARKER, flush=True)
        result = {"ok": True}
        if mode == "inventory":
            result["devices"] = inventory()
        else:
            result["smoke"] = smoke_test(device)
    except Exception as exc:
        code = "smoke_test_failed" if isinstance(exc, ComputationFailed) else "probe_error"
        # A missing ICD is an expected inventory result, not an internal error.
        if mode == "inventory" and getattr(exc, "code", None) == -1001:
            code = "no_device"  # CL_PLATFORM_NOT_FOUND_KHR
        result = {"ok": False, "stage": stage, "opencl_unavailable_reason": code,
                  "reason": f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else 'check failed'}"[:240],
                  "probe_diagnostic": _bounded_diagnostic("".join(traceback.format_exception(exc)))}
    return result


def _write_probe_result(path: Path, result: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    os.replace(temporary, path)


def _child_main(mode: str, device: Mapping[str, Any] | None, path: Path) -> None:
    # ``import bempp_cl.api`` makes a scratch directory with ``tempfile.mkdtemp()``
    # and never removes it. This disposable child keeps no state in the system
    # temporary directory, so point it at the check's own channel directory,
    # which the parent removes however the child ends.
    if path.parent.is_dir():
        tempfile.tempdir = str(path.parent)
    result = _child_result(mode, device)
    _validate_probe_result(result, mode)
    _write_probe_result(path, result)
    if not result["ok"] and result.get("probe_diagnostic"):
        # Retain direct compiler evidence in a captured child stderr artifact
        # even if native failure teardown subsequently aborts. A nonzero exit
        # still invalidates the report; this output never approves a device.
        sys.stderr.write(f"OpenCL probe diagnostic:\n{result['probe_diagnostic']}\n")
    sys.stdout.flush()
    sys.stderr.flush()
    if result["ok"]:
        # Native work and its numerical checks are complete, and the atomic
        # report is closed. PoCL can abort while tearing down LLVM contexts at
        # interpreter shutdown; this disposable child needs no further cleanup.
        # A computation, report or flush failure never reaches this exit. The
        # parent still refuses every child that crashes, even with a result file.
        os._exit(0)


if __name__ == "__main__":
    _child_main(sys.argv[1], json.loads(sys.argv[2]), Path(sys.argv[3]))
