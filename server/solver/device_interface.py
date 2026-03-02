"""
Runtime selection for BEMPP operator device interfaces.

Supported modes:
- auto
- opencl_cpu
- opencl_gpu
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Dict, List, Optional, Tuple

from .deps import BEMPP_VARIANT, bempp_api

VALID_DEVICE_MODES = {"auto", "opencl_cpu", "opencl_gpu"}
_MODE_ALIASES = {
    "opencl": "opencl_cpu",
    "cpu_opencl": "opencl_cpu",
    "gpu_opencl": "opencl_gpu",
}
DEFAULT_DEVICE_MODE = str(os.environ.get("WG_DEVICE_MODE", "auto") or "auto").strip().lower()
_LOGGED_FALLBACK_REASONS: set[str] = set()
_AUTO_MODE_PRIORITY: Tuple[str, ...] = ("opencl_gpu", "opencl_cpu")


def _path_contains_whitespace(path: Path) -> bool:
    return any(ch.isspace() for ch in str(path))


def normalize_device_mode(mode: Optional[str]) -> str:
    raw = str(mode or DEFAULT_DEVICE_MODE or "auto").strip().lower()
    normalized = _MODE_ALIASES.get(raw, raw)
    if normalized not in VALID_DEVICE_MODES:
        return "auto"
    return normalized


def _opencl_prerequisite_reason() -> Optional[str]:
    if bempp_api is None:
        return "bempp runtime is unavailable."

    if BEMPP_VARIANT != "bempp_cl":
        return f"bempp variant '{BEMPP_VARIANT}' is not validated for OpenCL here."

    module_file = getattr(bempp_api, "__file__", None)
    if module_file and _path_contains_whitespace(Path(module_file).resolve()):
        return "bempp-cl path contains spaces; OpenCL kernel build options fail in this layout."

    try:
        import pyopencl as cl  # type: ignore

        if len(cl.get_platforms()) == 0:
            return "no OpenCL platforms found."
    except Exception as exc:  # pragma: no cover - depends on local runtime
        return f"pyopencl runtime unavailable ({exc})."

    return None


@lru_cache(maxsize=1)
def _opencl_inventory() -> Dict[str, object]:
    inventory: Dict[str, object] = {
        "base_ready": False,
        "base_reason": None,
        "cpu_available": False,
        "gpu_available": False,
        "cpu_device_name": None,
        "gpu_device_name": None,
        "cpu_reason": None,
        "gpu_reason": None,
    }
    base_reason = _opencl_prerequisite_reason()
    inventory["base_reason"] = base_reason
    if base_reason is not None:
        return inventory

    inventory["base_ready"] = True
    try:
        from bempp_cl.core.opencl_kernels import find_cpu_driver, find_gpu_driver  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime-specific
        inventory["base_reason"] = f"failed to import bempp-cl OpenCL kernels ({exc})."
        inventory["base_ready"] = False
        return inventory

    try:
        _ctx_cpu, cpu_device = find_cpu_driver()
        inventory["cpu_available"] = True
        inventory["cpu_device_name"] = str(getattr(cpu_device, "name", None) or "OpenCL CPU")
    except Exception as exc:  # pragma: no cover - runtime-specific
        inventory["cpu_reason"] = f"no suitable OpenCL CPU driver ({exc})."

    try:
        _ctx_gpu, gpu_device = find_gpu_driver()
        inventory["gpu_available"] = True
        inventory["gpu_device_name"] = str(getattr(gpu_device, "name", None) or "OpenCL GPU")
    except Exception as exc:  # pragma: no cover - runtime-specific
        inventory["gpu_reason"] = f"no suitable OpenCL GPU driver ({exc})."

    return inventory


def _mode_unavailable_reason(mode: str) -> Optional[str]:
    normalized = normalize_device_mode(mode)
    info = _opencl_inventory()
    base_reason = info.get("base_reason")
    if base_reason:
        return str(base_reason)

    # bempp-cl 0.4.x singular assembler currently relies on CPU OpenCL kernels.
    if not bool(info.get("cpu_available")):
        return str(info.get("cpu_reason") or "no suitable OpenCL CPU driver.")

    if normalized == "opencl_cpu":
        return None

    if normalized == "opencl_gpu":
        if bool(info.get("gpu_available")):
            return None
        return str(info.get("gpu_reason") or "no suitable OpenCL GPU driver.")

    return f"unsupported mode '{normalized}'."


@lru_cache(maxsize=1)
def opencl_unavailable_reason() -> Optional[str]:
    """Backward-compatible helper for legacy checks."""
    return _mode_unavailable_reason("opencl_cpu")


def _available_concrete_modes() -> List[str]:
    if bempp_api is None:
        return []
    modes: List[str] = []
    if _mode_unavailable_reason("opencl_cpu") is None:
        modes.append("opencl_cpu")
    if _mode_unavailable_reason("opencl_gpu") is None:
        modes.append("opencl_gpu")
    return modes


def available_mode_options() -> List[str]:
    concrete = _available_concrete_modes()
    if len(concrete) == 0:
        return ["auto"]
    return ["auto", *concrete]


def _mode_availability() -> Dict[str, Dict[str, object]]:
    inventory = _opencl_inventory()

    cpu_reason = _mode_unavailable_reason("opencl_cpu")
    gpu_reason = _mode_unavailable_reason("opencl_gpu")

    return {
        "auto": {
            "available": True,
            "reason": None,
            "priority": list(_AUTO_MODE_PRIORITY),
        },
        "opencl_cpu": {
            "available": cpu_reason is None,
            "reason": cpu_reason,
            "device_name": inventory.get("cpu_device_name"),
        },
        "opencl_gpu": {
            "available": gpu_reason is None,
            "reason": gpu_reason,
            "device_name": inventory.get("gpu_device_name"),
        },
    }


def _apply_opencl_mode(mode: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Configure bempp-cl OpenCL device type.

    Returns:
        (applied, interface, device_type, device_name)
    """
    if bempp_api is None:
        return False, "opencl", None, None

    normalized = normalize_device_mode(mode)
    if normalized not in {"opencl_cpu", "opencl_gpu"}:
        return False, "opencl", None, None

    try:
        from bempp_cl.core.opencl_kernels import (  # type: ignore
            default_cpu_context,
            default_cpu_device,
            default_gpu_context,
            default_gpu_device,
        )
    except Exception:
        return False, "opencl", None, None

    if normalized == "opencl_cpu":
        setattr(bempp_api, "BOUNDARY_OPERATOR_DEVICE_TYPE", "cpu")
        setattr(bempp_api, "POTENTIAL_OPERATOR_DEVICE_TYPE", "cpu")
        device = default_cpu_device()
        default_cpu_context()
        return True, "opencl", "cpu", str(getattr(device, "name", None) or "OpenCL CPU")

    # For GPU mode, keep boundary/potential on GPU while ensuring CPU context can
    # still be initialized for singular kernels in bempp-cl 0.4.x.
    setattr(bempp_api, "BOUNDARY_OPERATOR_DEVICE_TYPE", "gpu")
    setattr(bempp_api, "POTENTIAL_OPERATOR_DEVICE_TYPE", "gpu")
    device = default_gpu_device()
    default_gpu_context()
    default_cpu_device()
    default_cpu_context()
    return True, "opencl", "gpu", str(getattr(device, "name", None) or "OpenCL GPU")


def _auto_mode_choice(concrete_modes: List[str]) -> str:
    for candidate in _AUTO_MODE_PRIORITY:
        if candidate in concrete_modes:
            return candidate
    return concrete_modes[0] if concrete_modes else "opencl_cpu"


def _auto_fallback_reason(mode_availability: Dict[str, Dict[str, object]]) -> Optional[str]:
    gpu_reason = str(mode_availability.get("opencl_gpu", {}).get("reason") or "").strip()
    cpu_reason = str(mode_availability.get("opencl_cpu", {}).get("reason") or "").strip()

    if gpu_reason and cpu_reason:
        if gpu_reason == cpu_reason:
            return gpu_reason
        return f"OpenCL GPU unavailable: {gpu_reason} OpenCL CPU unavailable: {cpu_reason}"
    if gpu_reason:
        return f"OpenCL GPU unavailable: {gpu_reason}"
    if cpu_reason:
        return f"OpenCL CPU unavailable: {cpu_reason}"
    return None


@lru_cache(maxsize=16)
def _selected_device_profile(preferred: str = DEFAULT_DEVICE_MODE) -> Dict[str, object]:
    requested_mode = normalize_device_mode(preferred)
    concrete_modes = _available_concrete_modes()
    mode_availability = _mode_availability()
    fallback_reason: Optional[str] = None

    if len(concrete_modes) == 0:
        selected_mode = None
        fallback_reason = _auto_fallback_reason(mode_availability) or "OpenCL runtime is unavailable."
    elif requested_mode == "auto":
        selected_mode = _auto_mode_choice(concrete_modes)
    elif requested_mode in concrete_modes:
        selected_mode = requested_mode
    else:
        selected_mode = None
        unavailable = _mode_unavailable_reason(requested_mode)
        if unavailable:
            fallback_reason = f"requested mode '{requested_mode}' unavailable: {unavailable}"
        else:
            fallback_reason = f"requested mode '{requested_mode}' unavailable."

    selected_interface = "opencl" if selected_mode in {"opencl_cpu", "opencl_gpu"} else "unavailable"
    if selected_mode == "opencl_cpu":
        selected_device_type: Optional[str] = "cpu"
    elif selected_mode == "opencl_gpu":
        selected_device_type = "gpu"
    else:
        selected_device_type = None

    return {
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "selected_interface": selected_interface,
        "selected_device_type": selected_device_type,
        "fallback_reason": fallback_reason,
        "concrete_modes": concrete_modes,
        "available_modes": available_mode_options(),
        "mode_availability": mode_availability,
        "opencl_diagnostics": dict(_opencl_inventory()),
        "benchmark": {
            "ran": False,
            "winner_mode": None,
            "samples": {},
            "policy": "deterministic_priority",
            "priority": list(_AUTO_MODE_PRIORITY),
        },
    }


def _opencl_unavailable_warning(profile: Dict[str, object]) -> str:
    requested = str(profile.get("requested_mode") or "auto")
    fallback_reason = str(profile.get("fallback_reason") or "OpenCL runtime is unavailable.")
    return (
        f"OpenCL drivers are not available for requested mode '{requested}'. "
        f"{fallback_reason} Install/enable OpenCL drivers and verify pyopencl platform discovery."
    )


def _ensure_selected_mode_applied(profile: Dict[str, object]) -> Tuple[str, Optional[str], Optional[str]]:
    selected_mode = str(profile.get("selected_mode") or "")
    if selected_mode not in {"opencl_cpu", "opencl_gpu"}:
        raise RuntimeError(_opencl_unavailable_warning(profile))

    applied, interface, device_type, device_name = _apply_opencl_mode(selected_mode)
    if not applied:
        raise RuntimeError(
            f"OpenCL mode '{selected_mode}' is available but could not be initialized for bempp-cl operators."
        )
    return str(interface or "opencl"), device_type, device_name


def selected_device_interface(preferred: str = DEFAULT_DEVICE_MODE) -> str:
    """Select solver operator interface (`opencl`) with explicit OpenCL requirement."""
    profile = _selected_device_profile(preferred)
    try:
        interface, _device_type, _device_name = _ensure_selected_mode_applied(profile)
    except RuntimeError as exc:
        reason_text = str(exc)
        if reason_text not in _LOGGED_FALLBACK_REASONS:
            _LOGGED_FALLBACK_REASONS.add(reason_text)
            logger.warning("[BEM] OpenCL requirement warning: %s", reason_text)
        raise
    return interface


def boundary_device_interface(preferred: str = DEFAULT_DEVICE_MODE) -> str:
    return selected_device_interface(preferred)


def potential_device_interface(preferred: str = DEFAULT_DEVICE_MODE) -> str:
    return selected_device_interface(preferred)


def configure_opencl_safe_profile() -> Dict[str, object]:
    """
    Apply a conservative OpenCL CPU profile for runtime recovery.
    """
    profile: Dict[str, object] = {
        "profile": "safe_cpu",
        "applied": False,
        "device_name": None,
        "workgroup_size_multiple": None,
        "detail": None,
    }

    if bempp_api is None:
        profile["detail"] = "bempp runtime is unavailable."
        return profile

    if BEMPP_VARIANT != "bempp_cl":
        profile["detail"] = f"bempp variant '{BEMPP_VARIANT}' is not validated for OpenCL safe profile."
        return profile

    try:
        from bempp_cl.core.opencl_kernels import default_cpu_context, default_cpu_device  # type: ignore

        setattr(bempp_api, "BOUNDARY_OPERATOR_DEVICE_TYPE", "cpu")
        setattr(bempp_api, "POTENTIAL_OPERATOR_DEVICE_TYPE", "cpu")
        device = default_cpu_device()
        default_cpu_context()
        profile["device_name"] = getattr(device, "name", None)
    except Exception as exc:  # pragma: no cover - runtime-specific
        profile["detail"] = f"failed to initialize CPU OpenCL profile ({exc})"
        return profile

    dense = getattr(getattr(getattr(bempp_api, "GLOBAL_PARAMETERS", None), "assembly", None), "dense", None)
    if dense is not None and hasattr(dense, "workgroup_size_multiple"):
        try:
            setattr(dense, "workgroup_size_multiple", 1)
            profile["workgroup_size_multiple"] = int(getattr(dense, "workgroup_size_multiple"))
        except Exception as exc:  # pragma: no cover - runtime-specific
            profile["detail"] = f"failed to set workgroup_size_multiple=1 ({exc})"

    profile["applied"] = True
    return profile


def selected_device_metadata(preferred: str = DEFAULT_DEVICE_MODE) -> Dict[str, object]:
    profile = _selected_device_profile(preferred)
    warning_text: Optional[str] = None
    try:
        interface, device_type, device_name = _ensure_selected_mode_applied(profile)
    except RuntimeError as exc:
        warning_text = str(exc)
        interface = "unavailable"
        device_type = None
        device_name = None

    fallback_reason = profile.get("fallback_reason")
    if fallback_reason is None and warning_text:
        fallback_reason = warning_text
    selected_mode = profile.get("selected_mode")
    requested_mode = profile.get("requested_mode")
    concrete_modes = list(profile.get("concrete_modes") or [])
    available_modes = list(profile.get("available_modes") or [])
    benchmark = profile.get("benchmark") or {"ran": False, "winner_mode": None, "samples": {}}
    mode_availability = profile.get("mode_availability") if isinstance(profile.get("mode_availability"), dict) else {}
    opencl_diagnostics = profile.get("opencl_diagnostics") if isinstance(profile.get("opencl_diagnostics"), dict) else {}

    return {
        # New detailed fields.
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "interface": interface,
        "device_type": device_type,
        "device_name": device_name,
        "fallback_reason": fallback_reason,
        "concrete_modes": concrete_modes,
        "available_modes": available_modes,
        "mode_availability": mode_availability,
        "opencl_diagnostics": opencl_diagnostics,
        "benchmark": benchmark,
        "warning": warning_text,
        "opencl_available": any(mode.startswith("opencl_") for mode in concrete_modes),
        # Backward-compatible fields.
        "requested": requested_mode,
        "selected": interface if interface != "unavailable" else "opencl_unavailable",
        "runtime_selected": interface if interface != "unavailable" else "opencl_unavailable",
        "runtime_retry_attempted": False,
        "runtime_retry_outcome": "not_needed",
        "runtime_profile": "default",
    }


def clear_device_selection_caches() -> None:
    opencl_unavailable_reason.cache_clear()
    _opencl_inventory.cache_clear()
    _selected_device_profile.cache_clear()
    _LOGGED_FALLBACK_REASONS.clear()


def is_opencl_buffer_error(exc: BaseException) -> bool:
    """Return True when an exception indicates OpenCL buffer allocation failure."""
    message = str(exc).lower()
    patterns = (
        "invalid_buffer_size",
        "create_buffer failed",
        "clcreatebuffer",
        "cl_mem_object_allocation_failure",
        "out_of_resources",
        "out of resources",
        "out of host memory",
    )
    return any(pattern in message for pattern in patterns)
