"""Qualify OpenCL in disposable processes before selecting it for BEMPP.

Enumeration, driver imports, context creation and kernel execution can all hang.
Native work runs in bounded children, waited on by the background capability
thread or the isolated solve/warmup worker, never by startup or the event loop.
Timeouts are transient; passes and definitive rejections persist for the process.
"""
from __future__ import annotations

from functools import lru_cache
import importlib
import json
import os
import queue
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Mapping

# Only driver enumeration/kernel execution consumes these budgets. Child
# interpreter startup and imports compete with the app's own cold imports.
INVENTORY_SECONDS = 10.0
PROBE_SECONDS = 20.0
TOTAL_SECONDS = 30.0
# Per child, including process creation. Cold Windows imports may be slow;
# a hung import must still end and use the existing transient timeout codes.
SPAWN_IMPORT_SECONDS = 60.0
# Initial attempt plus two retries, at least 5s after each timeout completes.
# Serialize attempts so concurrent capability/solve requests cannot pile up.
RETRY_INTERVAL_SECONDS = 5.0
MAX_TIMEOUT_ATTEMPTS = 3
OPENCL_UNAVAILABLE_REASONS = frozenset({
    "no_device", "inventory_timeout", "smoke_test_failed", "smoke_test_timeout", "pocl_windows",
})
TIMEOUT_REASONS = frozenset({"inventory_timeout", "smoke_test_timeout"})
_RESULT_PREFIX = "WG_OPENCL_RESULT "
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
    qualified_backend, device = execution_route()
    if backend not in {"numba", "opencl"} or backend != qualified_backend or opencl_device != "cpu":
        raise RuntimeError("BEMPP refuses GPU, implicit or unqualified execution; "
                           f"requested {backend!r}/{opencl_device!r}, qualified {qualified_backend}")
    if device is not None:
        bind_device(device, force=True)


def native_call(function: Any, *args: Any, execution_config: Any = None, **kwargs: Any) -> Any:
    """Lowest shared boundary for solves and potential/field evaluation."""
    backend = (getattr(execution_config, "assembly_backend", None)
               if execution_config is not None else kwargs.get("assembly_backend"))
    device = (getattr(execution_config, "opencl_device", None)
              if execution_config is not None else kwargs.get("opencl_device"))
    guard_execution(backend, device)
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


def check_computation(matrix: Any) -> dict[str, float]:
    """Reject zero, partial, non-finite and numerically wrong computation."""
    import numpy as np

    reference = reference_matrix()
    if matrix.shape != reference.shape or not np.all(np.isfinite(matrix)) or not np.any(matrix):
        raise RuntimeError("OpenCL smoke assembly is zero, non-finite or has the wrong shape")
    matrix_error = float(np.linalg.norm(matrix - reference) / np.linalg.norm(reference))
    rhs = np.arange(1, 9, dtype=np.float32)
    solution = np.linalg.solve(matrix, rhs)
    expected = np.linalg.solve(reference, rhs)
    solve_error = float(np.linalg.norm(solution - expected) / np.linalg.norm(expected))
    # Single precision across vendor kernels, comfortably below audible error.
    if not np.all(np.isfinite(solution)) or matrix_error > 2e-4 or solve_error > 5e-4:
        raise RuntimeError(f"OpenCL smoke disagrees with numba: matrix={matrix_error:.3g}, solve={solve_error:.3g}")
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


def _read_probe_output(stream: Any, events: Any) -> None:
    """Drain the merged pipe on every OS; discard logs rather than buffer them.

    Timestamp READY in the reader, so parent scheduling delay cannot extend
    the compute deadline. EOF is also timestamped to detect late results.
    """
    try:
        for line in stream:
            line = line.strip()
            if line == _READY_MARKER or line.startswith(_RESULT_PREFIX):
                events.put((line, time.monotonic()))
    finally:
        events.put((None, time.monotonic()))


def _run_probe(mode: str, device: Mapping[str, Any] | None, timeout: float) -> dict[str, Any]:
    child = reader = None
    ready_at = None
    began = time.monotonic()
    deadline = began + SPAWN_IMPORT_SECONDS
    phase = "spawn/import"
    result = None
    try:
        # One continuously drained pipe avoids stdout/stderr backpressure and
        # Windows select() limitations. No native work runs in the reader.
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), mode, json.dumps(device)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "NUMBA_DISABLE_JIT": "1"},
        )
        events = queue.Queue()
        reader = threading.Thread(target=_read_probe_output, args=(child.stdout, events), daemon=True)
        reader.start()
        while True:
            remaining = deadline - time.monotonic()
            try:
                line, observed_at = events.get(timeout=max(0.0, remaining))
            except queue.Empty:
                raise subprocess.TimeoutExpired(child.args, timeout) from None
            if observed_at > deadline:
                raise subprocess.TimeoutExpired(child.args, timeout)
            if line == _READY_MARKER and ready_at is None:
                ready_at = observed_at
                deadline = ready_at + timeout
                phase = "compute"
            elif line is None:
                child.wait(timeout=max(0.001, deadline - time.monotonic()))
                if child.returncode or result is None:
                    raise ValueError("Child exited without a successful protocol response")
                # Engine import failures may precede READY; successful native
                # work must always have a READY marker.
                if ready_at is None and result.get("ok"):
                    raise ValueError("Missing ready marker")
                return {**result, "_active_seconds": 0.0 if ready_at is None else observed_at - ready_at}
            elif line.startswith(_RESULT_PREFIX):
                result = json.loads(line[len(_RESULT_PREFIX):])
                if not isinstance(result, dict):
                    raise ValueError("Invalid probe response")
    except subprocess.TimeoutExpired:
        limit = SPAWN_IMPORT_SECONDS if ready_at is None else timeout
        return {"ok": False, "opencl_unavailable_reason": "inventory_timeout" if mode == "inventory" else "smoke_test_timeout", "reason": f"OpenCL {mode} {phase} timed out after {limit:.1f}s",
                "_active_seconds": 0.0 if ready_at is None else timeout}
    except (OSError, ValueError) as exc:
        return {"ok": False, "opencl_unavailable_reason": "no_device" if mode == "inventory" else "smoke_test_failed", "reason": f"OpenCL {mode} probe failed: {type(exc).__name__}",
                "_active_seconds": 0.0 if ready_at is None else min(timeout, time.monotonic() - ready_at)}
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()
            if reader is not None:
                reader.join()
            child.stdout.close()


def _device_verdict(device_json: str, timeout: float) -> dict[str, Any]:
    if device_json in _device_verdict_cache:
        return {**_device_verdict_cache[device_json], "_active_seconds": 0.0}
    verdict = _run_probe("smoke", json.loads(device_json), timeout)
    if verdict.get("opencl_unavailable_reason") not in TIMEOUT_REASONS:
        _device_verdict_cache[device_json] = verdict
    return verdict


def _qualified_opencl() -> dict[str, Any]:
    active_seconds = 0.0
    found = _run_probe("inventory", None, min(INVENTORY_SECONDS, TOTAL_SECONDS))
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
                      "separate (BEAT · CUDA on NVIDIA, Metal on Apple Silicon).",
        }
    failures = []
    unavailable_reason = "smoke_test_failed"
    for device in devices:
        remaining = TOTAL_SECONDS - active_seconds
        if remaining <= 0:
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
        if code == "smoke_test_timeout":
            unavailable_reason = code
        elif unavailable_reason != "smoke_test_timeout" and sys.platform == "win32" and any(
            token in device["platform"].lower() for token in ("pocl", "portable computing language")
        ):
            unavailable_reason = "pocl_windows"
        failures.append(f"{device['name']}: {verdict.get('reason', 'smoke failed')}")
    return {"ok": False, "opencl_unavailable_reason": unavailable_reason,
            "reason": "; ".join(failures)}


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


def qualified_opencl() -> dict[str, Any]:
    global _cached_verdict, _last_timeout, _timeout_attempts, _retry_after, _revision
    with _selection_lock:
        if _cached_verdict is not None:
            return dict(_cached_verdict)
        if _last_timeout is not None and not retry_due():
            return dict(_last_timeout)
        verdict = _qualified_opencl()
        if verdict.get("opencl_unavailable_reason") in TIMEOUT_REASONS:
            _timeout_attempts += 1
            _last_timeout = verdict
            _retry_after = time.monotonic() + RETRY_INTERVAL_SECONDS
            if _timeout_attempts >= MAX_TIMEOUT_ATTEMPTS:
                # Stop retrying a persistently hung runtime; retain its timeout
                # code for guidance, rather than misreporting an absent device.
                _cached_verdict = verdict
        else:
            _cached_verdict = verdict
            _last_timeout = None
            _timeout_attempts = 0
        _revision += 1
        return dict(verdict)


def clear_cache() -> None:
    global _cached_verdict, _last_timeout, _timeout_attempts, _retry_after, _revision
    with _selection_lock:
        _cached_verdict = _last_timeout = None
        _timeout_attempts = 0
        _retry_after = 0.0
        _revision += 1
        _device_verdict_cache.clear()
        _bind_device.cache_clear()


if __name__ == "__main__":
    stage = "engine"
    try:
        import bempp_cl.api  # noqa: F401 - test the native engine import in the bounded child
        stage = "opencl"
        import pyopencl  # noqa: F401
        if sys.argv[1] != "inventory":
            import numpy  # noqa: F401
            import bempp_cl.core.opencl_kernels  # noqa: F401
            import hornlab_bempp_bem.device  # noqa: F401
        print(_READY_MARKER, flush=True)
        result = {"ok": True}
        if sys.argv[1] == "inventory":
            result["devices"] = inventory()
        else:
            result["smoke"] = smoke_test(json.loads(sys.argv[2]))
    except Exception as exc:
        result = {"ok": False, "stage": stage, "opencl_unavailable_reason": "no_device" if sys.argv[1] == "inventory" else "smoke_test_failed", "reason": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"}
    print(_RESULT_PREFIX + json.dumps(result), flush=True)
