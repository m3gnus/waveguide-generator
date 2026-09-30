"""Qualify OpenCL in disposable processes before selecting it for BEMPP.

Enumeration, driver imports, context creation and kernel execution can all hang.
None runs in the capability thread. The eight-second total budget includes a
five-second limit per device, and each verdict is retained for this process.
"""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Mapping

PROBE_SECONDS = 5.0
TOTAL_SECONDS = 8.0
_RESULT_PREFIX = "WG_OPENCL_RESULT "
_selection_lock = threading.Lock()
_device_verdict_cache: dict[str, dict[str, Any]] = {}


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
    api.set_default_cpu_device(device["platform_index"], device["device_index"])
    from bempp_cl.core.opencl_kernels import default_cpu_device

    selected = default_cpu_device()
    if not selected.type & cl.device_type.CPU or selected.type & cl.device_type.GPU:
        raise RuntimeError("BEMPP refuses a non-CPU OpenCL device")
    if (selected.name.strip(), selected.vendor.strip(), selected.platform.name.strip()) != (
        device["name"], device["vendor"], device["platform"],
    ):
        raise RuntimeError("OpenCL inventory changed after qualification; refusing another device")
    # Singular, regular and potential assembly must use the same CPU device.
    reset_opencl_device()
    configure_opencl("cpu")


def bind_device(device: Mapping[str, Any]) -> None:
    """Bind a qualified device in the killable process that will actually solve."""
    _bind_device(json.dumps(dict(device), sort_keys=True))


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

    bind_device(device)
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


def _run_probe(mode: str, device: Mapping[str, Any] | None, timeout: float) -> dict[str, Any]:
    try:
        # Disable mesh-helper JIT only in this disposable eight-element probe;
        # OpenCL still builds and executes the actual shipped BEMPP kernels.
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), mode, json.dumps(device)],
            capture_output=True, text=True, timeout=timeout, check=True,
            env={**os.environ, "NUMBA_DISABLE_JIT": "1"},
        )
        line = next(line for line in reversed(completed.stdout.splitlines()) if line.startswith(_RESULT_PREFIX))
        return json.loads(line[len(_RESULT_PREFIX):])
    except subprocess.TimeoutExpired:
        return {"ok": False, "opencl_unavailable_reason": "smoke_test_timeout", "reason": f"OpenCL {mode} timed out after {timeout:.1f}s"}
    except (OSError, subprocess.CalledProcessError, StopIteration, ValueError) as exc:
        return {"ok": False, "opencl_unavailable_reason": "no_device" if mode == "inventory" else "smoke_test_failed", "reason": f"OpenCL {mode} probe failed: {type(exc).__name__}"}


def _device_verdict(device_json: str, timeout: float) -> dict[str, Any]:
    if device_json not in _device_verdict_cache:
        _device_verdict_cache[device_json] = _run_probe("smoke", json.loads(device_json), timeout)
    return _device_verdict_cache[device_json]


@lru_cache(maxsize=1)
def _qualified_opencl() -> dict[str, Any]:
    began = time.monotonic()
    found = _run_probe("inventory", None, min(2.0, TOTAL_SECONDS))
    if not found.get("ok"):
        return {"opencl_unavailable_reason": "no_device", **found}
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
        remaining = TOTAL_SECONDS - (time.monotonic() - began)
        if remaining <= 0:
            failures.append("OpenCL qualification time budget exhausted")
            unavailable_reason = "smoke_test_timeout"
            break
        verdict = _device_verdict(json.dumps(device, sort_keys=True), min(PROBE_SECONDS, remaining))
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


def qualified_opencl() -> dict[str, Any]:
    # lru_cache alone can duplicate the first call from simultaneous probes.
    with _selection_lock:
        return dict(_qualified_opencl())


def clear_cache() -> None:
    _qualified_opencl.cache_clear()
    _device_verdict_cache.clear()
    _bind_device.cache_clear()


if __name__ == "__main__":
    stage = "engine"
    try:
        import bempp_cl.api  # noqa: F401 - test the native engine import in the bounded child
        stage = "opencl"
        result = {"ok": True}
        if sys.argv[1] == "inventory":
            result["devices"] = inventory()
        else:
            result["smoke"] = smoke_test(json.loads(sys.argv[2]))
    except Exception as exc:
        result = {"ok": False, "stage": stage, "opencl_unavailable_reason": "no_device" if sys.argv[1] == "inventory" else "smoke_test_failed", "reason": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"}
    print(_RESULT_PREFIX + json.dumps(result))
