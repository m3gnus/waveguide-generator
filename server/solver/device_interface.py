"""
Runtime selection for BEMPP operator device interfaces.

The backend prefers OpenCL, but bempp-cl 0.4.x requires an OpenCL CPU device
for boundary operator assembly. On systems with GPU-only OpenCL (common on
Apple Silicon), forced OpenCL fails at runtime.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from .deps import BEMPP_VARIANT, bempp_api


def _path_contains_whitespace(path: Path) -> bool:
    return any(ch.isspace() for ch in str(path))


@lru_cache(maxsize=1)
def opencl_unavailable_reason() -> Optional[str]:
    """Return why OpenCL cannot be safely used, or None if supported."""
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

    try:
        # bempp-cl's OpenCL singular assembler requires a CPU OpenCL device.
        from bempp_cl.core.opencl_kernels import find_cpu_driver  # type: ignore

        find_cpu_driver()
    except Exception as exc:  # pragma: no cover - depends on local runtime
        return f"no suitable OpenCL CPU driver ({exc})."

    return None


@lru_cache(maxsize=1)
def selected_device_interface(preferred: str = "opencl") -> str:
    """Select the solver device interface with safe runtime fallback."""
    mode = str(preferred or "opencl").strip().lower()
    if mode not in {"opencl", "numba"}:
        mode = "opencl"

    if mode == "numba":
        return "numba"

    reason = opencl_unavailable_reason()
    if reason:
        print(f"[BEM] OpenCL unavailable; using numba backend. Reason: {reason}")
        return "numba"
    return "opencl"


def boundary_device_interface() -> str:
    return selected_device_interface("opencl")


def potential_device_interface() -> str:
    return selected_device_interface("opencl")


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


def selected_device_metadata() -> Dict[str, object]:
    mode = selected_device_interface("opencl")
    reason = opencl_unavailable_reason() if mode != "opencl" else None
    return {
        "requested": "opencl",
        "selected": mode,
        "fallback_reason": reason,
        "runtime_retry_attempted": False,
        "runtime_retry_outcome": "not_needed",
        "runtime_profile": "default",
    }


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
