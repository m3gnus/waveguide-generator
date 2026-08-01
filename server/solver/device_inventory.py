"""Honest OpenCL device inventory and per-device-mode acceleration readiness.

The rest of the backend has historically collapsed three independent facts into
a single "OpenCL available" boolean:

1. whether the ``pyopencl`` binding is importable,
2. whether an OpenCL **CPU** device exists and can actually be initialized,
3. whether an OpenCL **GPU** device exists and can actually be initialized.

``solver.bempp_solver.opencl_runtime_status`` counts devices of *any* type but
only ever probes ``configure_opencl("cpu")``, so a CPU-only-OpenCL host reports
"available" and a user-requested ``device_mode="opencl_gpu"`` solve then fails
at solve time. This module keeps the three facts separate and always probes the
device type a solve would actually select.

Only the standard library is imported at module scope. ``pyopencl`` and
``hornlab_bempp_bem`` are optional and are imported lazily inside guarded
helpers, so importing this module has no side effects and every public function
works when neither package is installed. All public functions are total: they
never raise, and any failure is reported as a structured ``reason`` string.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Mirrors ``contracts.VALID_DEVICE_MODES`` / ``contracts.DEVICE_MODE_ALIASES``.
# Duplicated deliberately: ``server/contracts`` pulls in pydantic, and this
# module must stay standard-library-only so installers and doctors can import
# it before the application dependencies are present.
VALID_DEVICE_MODES = ("auto", "opencl_cpu", "opencl_gpu")
DEVICE_MODE_ALIASES = {
    "opencl": "opencl_cpu",
    "cpu_opencl": "opencl_cpu",
    "gpu_opencl": "opencl_gpu",
}

# Canonical ``cl.device_type`` bit values, used when the live enum cannot be
# read off the binding (e.g. a stub or an unusual build).
_DEVICE_TYPE_BITS = (("cpu", 2), ("gpu", 4), ("accelerator", 8))

_MAX_REASON_CHARS = 400
_MAX_LABEL_CHARS = 160

_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s'\"<>|]*")
_UNC_PATH_RE = re.compile(r"\\\\[^\s'\"<>|]+")
_POSIX_PATH_RE = re.compile(r"(?<![\w.$-])/(?:[\w.+-]+/)+[\w.+-]*")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize(value: Any, *, limit: int = _MAX_LABEL_CHARS) -> str:
    """Collapse a value to a short, path-free, control-char-free string.

    Device/driver strings and exception messages routinely embed absolute
    filesystem paths (which leak usernames and host layout). Those are replaced
    with a ``<path>`` placeholder before the text is handed to any reporter.
    """
    try:
        text = str(value)
    except Exception:
        return "<unprintable>"
    text = _CONTROL_RE.sub(" ", text)
    text = _WINDOWS_PATH_RE.sub("<path>", text)
    text = _UNC_PATH_RE.sub("<path>", text)
    text = _POSIX_PATH_RE.sub("<path>", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: max(limit - 3, 1)].rstrip() + "..."
    return text


def _describe_exception(exc: BaseException) -> str:
    message = _sanitize(exc, limit=_MAX_REASON_CHARS)
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def _import_pyopencl() -> tuple[Any | None, str | None]:
    """Return ``(pyopencl_module, failure_reason)``; never raises."""
    try:
        import pyopencl  # type: ignore
    except Exception as exc:
        return None, f"pyopencl is not importable ({_describe_exception(exc)})."
    return pyopencl, None


def _load_configure_opencl() -> tuple[Any | None, str | None]:
    """Return ``(configure_opencl, failure_reason)``; never raises.

    This is the exact entry point the BEMPP solve path uses to bind bempp-cl to
    a concrete OpenCL device type.
    """
    try:
        from hornlab_bempp_bem.device import configure_opencl  # type: ignore
    except Exception as exc:
        return None, (
            "hornlab_bempp_bem.device.configure_opencl is unavailable "
            f"({_describe_exception(exc)})."
        )
    return configure_opencl, None


def opencl_kernel_build_probe(device_type: str) -> dict[str, Any]:
    """Compile a kernel the way bempp-cl does, and report whether it works.

    Creating an OpenCL context is not sufficient evidence that a solve can run.
    bempp-cl passes its source include directory to ``clBuildProgram`` as an
    unquoted ``-I`` option. When the install path contains a space -- ordinary
    on Windows, and true of this project's own default folder name on every
    platform -- the option splits and the build fails with
    ``INVALID_BUILD_OPTIONS``, even though ``configure_opencl`` succeeded and
    every device query looked healthy.

    Two failure modes get normalized here:

    * ``clBuildProgram ... INVALID_BUILD_OPTIONS`` caused by a space in the
      include path. Reported with the actionable cause rather than the raw
      compiler dump.
    * ``KeyError: 'PYOPENCL_CACHE_FAILURE_FATAL'``. pyopencl's cache error
      handler reads that variable without a default, so its own error path
      raises and masks the real compiler error entirely.

    Never raises.
    """
    module, reason = _import_pyopencl()
    if module is None:
        return {"ok": False, "reason": reason or "pyopencl is not importable."}

    try:
        from bempp_cl.core.opencl_kernels import default_context  # type: ignore
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"bempp-cl OpenCL kernels are unavailable ({_describe_exception(exc)}).",
        }

    # Apply the upstream workarounds first, then read back the include path
    # bempp-cl will actually use, so this probe reflects reality rather than a
    # guess. See solver.bempp_compat for what is patched and why.
    try:
        from .bempp_compat import apply_bempp_opencl_workarounds

        apply_bempp_opencl_workarounds()
    except Exception:
        pass

    include_dir = None
    try:
        from bempp_cl.core import opencl_kernels as _bempp_kernels  # type: ignore

        candidate = getattr(_bempp_kernels, "_INCLUDE_PATH", None)
        if isinstance(candidate, str) and candidate:
            include_dir = candidate
    except Exception:
        include_dir = None

    # Use the exact -I string bempp-cl will pass. If that is going to break, it
    # must break here, during readiness, not midway through a user's sweep.
    options = [f"-I {include_dir}"] if include_dir else []
    source = "__kernel void wg_probe(__global float *o){ o[get_global_id(0)] = 1.0f; }"

    try:
        context = default_context(device_type)
        module.Program(context, source).build(options=options)
    except KeyError as exc:
        if "PYOPENCL_CACHE_FAILURE_FATAL" in str(exc):
            return {
                "ok": False,
                "reason": (
                    "OpenCL kernel build failed and pyopencl's cache error handler "
                    "raised KeyError('PYOPENCL_CACHE_FAILURE_FATAL'), hiding the real "
                    "compiler error. Set PYOPENCL_CACHE_FAILURE_FATAL=0 to surface it."
                ),
            }
        return {"ok": False, "reason": f"OpenCL kernel build failed ({_describe_exception(exc)})."}
    except Exception as exc:
        text = str(exc)
        if "INVALID_BUILD_OPTIONS" in text:
            spaced = bool(include_dir and " " in include_dir)
            detail = (
                " The bempp-cl include path contains a space and bempp-cl does not "
                "quote its -I option, so the compiler splits it. Reinstall the "
                "project under a path with no spaces, or use the numba assembly "
                "backend."
                if spaced
                else ""
            )
            return {
                "ok": False,
                "reason": f"OpenCL kernel build failed with INVALID_BUILD_OPTIONS.{detail}",
            }
        return {
            "ok": False,
            "reason": f"OpenCL kernel build failed ({_describe_exception(exc)}).",
        }

    return {"ok": True, "reason": "OpenCL kernel compiled successfully."}


def _numba_runtime() -> dict[str, Any]:
    """Probe the always-present numba assembly fallback; never raises."""
    try:
        import numba  # type: ignore
    except Exception as exc:
        return {
            "available": False,
            "reason": (
                "numba is not importable "
                f"({_describe_exception(exc)}); reinstall with "
                "pip install -r server/requirements-bempp.txt"
            ),
        }
    version = _sanitize(getattr(numba, "__version__", "unknown"), limit=40)
    return {"available": True, "reason": f"numba {version} is available."}


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _device_type_label(module: Any, raw_type: Any) -> str:
    """Map a ``cl.device_type`` bitfield to ``cpu``/``gpu``/``accelerator``/``other``."""
    enum = _safe_attr(module, "device_type")
    try:
        value = int(raw_type)
    except Exception:
        text = str(raw_type).strip().lower()
        for label, _bit in _DEVICE_TYPE_BITS:
            if label in text:
                return label
        return "other"
    for label, fallback_bit in _DEVICE_TYPE_BITS:
        bit = _safe_int(_safe_attr(enum, label.upper(), fallback_bit), fallback_bit)
        if bit and (value & bit) == bit:
            return label
    return "other"


def _device_fp64(device: Any) -> bool:
    config = _safe_attr(device, "double_fp_config")
    if config is not None:
        try:
            return int(config) != 0
        except Exception:
            pass
    extensions = _safe_attr(device, "extensions", "") or ""
    try:
        return "cl_khr_fp64" in str(extensions)
    except Exception:
        return False


def _describe_device(module: Any, device: Any) -> dict[str, Any]:
    global_mem_bytes = _safe_int(_safe_attr(device, "global_mem_size", 0), 0)
    return {
        "name": _sanitize(_safe_attr(device, "name", "unknown device")),
        "type": _device_type_label(module, _safe_attr(device, "type")),
        "vendor": _sanitize(_safe_attr(device, "vendor", "unknown vendor")),
        "driverVersion": _sanitize(_safe_attr(device, "driver_version", "unknown"), limit=80),
        "computeUnits": _safe_int(_safe_attr(device, "max_compute_units", 0), 0),
        "globalMemMB": global_mem_bytes // (1024 * 1024),
        "fp64": _device_fp64(device),
    }


def _empty_inventory(*, binding_available: bool, binding_reason: str | None, reason: str) -> dict[str, Any]:
    return {
        "bindingAvailable": binding_available,
        "bindingReason": binding_reason,
        "platforms": [],
        "platformCount": 0,
        "deviceCount": 0,
        "cpuDeviceCount": 0,
        "gpuDeviceCount": 0,
        "reason": reason,
    }


def opencl_device_inventory() -> dict[str, Any]:
    """Enumerate OpenCL platforms and devices without initializing anything.

    Reports the binding fact (``bindingAvailable``) separately from the device
    facts (``cpuDeviceCount`` / ``gpuDeviceCount``). Enumeration failures — at
    the platform list, at a single platform's device list, or on an individual
    device query — are captured as ``reason`` strings rather than raised.
    """
    try:
        module, binding_reason = _import_pyopencl()
        if module is None:
            reason = binding_reason or "pyopencl is not importable."
            return _empty_inventory(
                binding_available=False,
                binding_reason=reason,
                reason=reason,
            )

        try:
            raw_platforms = list(module.get_platforms())
        except Exception as exc:
            return _empty_inventory(
                binding_available=True,
                binding_reason=None,
                reason=f"OpenCL platform enumeration failed ({_describe_exception(exc)}).",
            )

        platforms: list[dict[str, Any]] = []
        partial_failures: list[str] = []
        cpu_count = 0
        gpu_count = 0
        device_count = 0

        for raw_platform in raw_platforms:
            entry: dict[str, Any] = {
                "name": _sanitize(_safe_attr(raw_platform, "name", "unknown platform")),
                "vendor": _sanitize(_safe_attr(raw_platform, "vendor", "unknown vendor")),
                "version": _sanitize(_safe_attr(raw_platform, "version", "unknown"), limit=80),
                "devices": [],
            }
            try:
                raw_devices = list(raw_platform.get_devices())
            except Exception as exc:
                entry["reason"] = (
                    f"Device enumeration failed ({_describe_exception(exc)})."
                )
                partial_failures.append(f"{entry['name']}: {entry['reason']}")
                platforms.append(entry)
                continue

            for raw_device in raw_devices:
                try:
                    described = _describe_device(module, raw_device)
                except Exception as exc:
                    partial_failures.append(
                        f"{entry['name']}: device query failed ({_describe_exception(exc)})."
                    )
                    continue
                entry["devices"].append(described)
                device_count += 1
                if described["type"] == "cpu":
                    cpu_count += 1
                elif described["type"] == "gpu":
                    gpu_count += 1
            platforms.append(entry)

        if device_count == 0:
            reason = "No OpenCL devices were found."
        else:
            reason = (
                f"Enumerated {len(platforms)} OpenCL platform(s) and {device_count} "
                f"device(s): {cpu_count} CPU, {gpu_count} GPU."
            )
        if partial_failures:
            reason = f"{reason} Partial enumeration failures: " + "; ".join(
                partial_failures[:4]
            )

        return {
            "bindingAvailable": True,
            "bindingReason": None,
            "platforms": platforms,
            "platformCount": len(platforms),
            "deviceCount": device_count,
            "cpuDeviceCount": cpu_count,
            "gpuDeviceCount": gpu_count,
            "reason": _sanitize(reason, limit=_MAX_REASON_CHARS),
        }
    except Exception as exc:  # pragma: no cover - defensive: must never raise
        return _empty_inventory(
            binding_available=False,
            binding_reason=None,
            reason=f"OpenCL inventory probe failed ({_describe_exception(exc)}).",
        )


def _normalize_device_mode(device_mode: Any) -> str:
    raw = str(device_mode or "auto").strip().lower()
    return DEVICE_MODE_ALIASES.get(raw, raw)


def _resolve_device_type(device_mode: str) -> str:
    """Resolve a request device_mode to the concrete OpenCL device type.

    Mirrors ``solver.bempp_solver._opencl_device_from_request`` exactly:

        return "gpu" if device_mode == "opencl_gpu" else "cpu"

    So the precedence is: only an explicit ``opencl_gpu`` selects the GPU;
    ``auto`` and ``opencl_cpu`` both resolve to the CPU device. In particular
    ``auto`` does **not** prefer a GPU even when one is present. Keep this in
    sync with that function; do not invent a different rule here.
    """
    return "gpu" if device_mode == "opencl_gpu" else "cpu"


def device_mode_readiness(device_mode: str) -> dict[str, Any]:
    """Report whether a solve with ``device_mode`` could initialize its device.

    Resolves the concrete OpenCL device type the solve path would select, then
    probes ``hornlab_bempp_bem.device.configure_opencl`` for *that* type only.
    A working CPU device never makes ``opencl_gpu`` report ready. Never raises.
    """
    normalized = _normalize_device_mode(device_mode)
    try:
        if normalized not in VALID_DEVICE_MODES:
            return {
                "deviceMode": _sanitize(device_mode, limit=64),
                "resolvedDeviceType": None,
                "ready": False,
                "deviceName": None,
                "reason": (
                    f"Unknown device_mode {_sanitize(device_mode, limit=64)!r}; "
                    "expected one of: " + ", ".join(VALID_DEVICE_MODES) + "."
                ),
            }

        resolved = _resolve_device_type(normalized)
        result: dict[str, Any] = {
            "deviceMode": normalized,
            "resolvedDeviceType": resolved,
            "ready": False,
            "deviceName": None,
            "reason": "",
        }

        inventory = opencl_device_inventory()
        if not inventory.get("bindingAvailable"):
            result["reason"] = (
                f"{inventory.get('bindingReason') or 'pyopencl is not importable.'} "
                "Install the OpenCL extra (pip install 'hornlab-bempp-bem[opencl]') "
                "or keep the numba assembly fallback."
            )
            return result

        cpu_count = _safe_int(inventory.get("cpuDeviceCount"), 0)
        gpu_count = _safe_int(inventory.get("gpuDeviceCount"), 0)
        matching = gpu_count if resolved == "gpu" else cpu_count
        if matching <= 0:
            if resolved == "gpu":
                hint = (
                    "Use device_mode='opencl_cpu' or 'auto', or install a GPU OpenCL "
                    "driver (ICD) for this host."
                )
            else:
                hint = (
                    "Install a CPU OpenCL runtime (pocl on Linux, the Intel CPU runtime "
                    "on Windows, pocl via Homebrew on macOS -- though Apple Silicon "
                    "normally uses the Metal backend instead), or keep the numba "
                    "assembly fallback."
                )
            result["reason"] = (
                f"device_mode={normalized!r} resolves to an OpenCL {resolved.upper()} "
                f"device, but the runtime exposes {matching} {resolved.upper()} "
                f"device(s) (found {cpu_count} CPU, {gpu_count} GPU). {hint}"
            )
            return result

        configure_opencl, load_reason = _load_configure_opencl()
        if configure_opencl is None:
            result["reason"] = (
                f"{load_reason or 'configure_opencl is unavailable.'} "
                "Install with: pip install -r server/requirements-bempp.txt"
            )
            return result

        try:
            device_name = configure_opencl(resolved)
        except Exception as exc:
            result["reason"] = (
                f"OpenCL {resolved.upper()} device initialization failed for "
                f"device_mode={normalized!r} ({_describe_exception(exc)})."
            )
            return result

        # A context is not a solve. bempp-cl must also be able to COMPILE its
        # assembly kernel on this device; that step fails independently of
        # device initialization (see opencl_kernel_build_probe).
        build = opencl_kernel_build_probe(resolved)
        if not build.get("ok"):
            result["deviceName"] = _sanitize(device_name) if device_name else None
            result["reason"] = (
                f"OpenCL {resolved.upper()} device initialized "
                f"({result['deviceName'] or 'unnamed device'}) but the assembly "
                f"kernel cannot be compiled: {build.get('reason')}"
            )
            return result

        result["ready"] = True
        result["deviceName"] = _sanitize(device_name) if device_name else None
        result["reason"] = (
            f"OpenCL {resolved.upper()} device initialized and assembly kernel "
            f"compiled for device_mode={normalized!r}: "
            f"{result['deviceName'] or 'unnamed device'}."
        )
        return result
    except Exception as exc:  # pragma: no cover - defensive: must never raise
        return {
            "deviceMode": normalized,
            "resolvedDeviceType": None,
            "ready": False,
            "deviceName": None,
            "reason": f"Device readiness probe failed ({_describe_exception(exc)}).",
        }


def acceleration_summary() -> dict[str, Any]:
    """Report OpenCL CPU, OpenCL GPU and numba as three independent facts.

    ``effectiveBackend`` names the highest-capability backend that is actually
    ready (``opencl_gpu`` > ``opencl_cpu`` > ``numba``). Note that a solve with
    ``device_mode="auto"`` still selects the CPU device — see
    ``_resolve_device_type`` and ``bempp_solver._opencl_device_from_request`` —
    so ``opencl_gpu`` is only ever reached when the user explicitly requests it.

    ``acceleratedByGpu`` is True only when a real OpenCL GPU device initialized.
    A CPU OpenCL device must never be reported as GPU acceleration. Never raises.
    """
    try:
        cpu = device_mode_readiness("opencl_cpu")
        gpu = device_mode_readiness("opencl_gpu")
        numba = _numba_runtime()

        opencl_cpu = {
            "available": bool(cpu.get("ready")),
            "reason": cpu.get("reason") or "",
            "deviceName": cpu.get("deviceName"),
        }
        opencl_gpu = {
            "available": bool(gpu.get("ready")),
            "reason": gpu.get("reason") or "",
            "deviceName": gpu.get("deviceName"),
        }

        if opencl_gpu["available"]:
            effective_backend = "opencl_gpu"
        elif opencl_cpu["available"]:
            effective_backend = "opencl_cpu"
        else:
            effective_backend = "numba"

        return {
            "openclCpu": opencl_cpu,
            "openclGpu": opencl_gpu,
            "numba": numba,
            "effectiveBackend": effective_backend,
            "acceleratedByGpu": bool(opencl_gpu["available"]),
        }
    except Exception as exc:  # pragma: no cover - defensive: must never raise
        reason = f"Acceleration probe failed ({_describe_exception(exc)})."
        return {
            "openclCpu": {"available": False, "reason": reason, "deviceName": None},
            "openclGpu": {"available": False, "reason": reason, "deviceName": None},
            "numba": {"available": False, "reason": reason},
            "effectiveBackend": "numba",
            "acceleratedByGpu": False,
        }


__all__ = [
    "DEVICE_MODE_ALIASES",
    "VALID_DEVICE_MODES",
    "acceleration_summary",
    "device_mode_readiness",
    "opencl_device_inventory",
]
