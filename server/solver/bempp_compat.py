"""Workarounds for upstream bugs that break bempp-cl OpenCL on Windows.

Two independent upstream defects make the OpenCL assembly path unusable on an
ordinary Windows install whose path contains a space -- which includes any
install under a folder like ``C:\\Users\\me\\My Projects\\``.

1. bempp-cl passes its kernel include directory to ``clBuildProgram`` unquoted::

       # bempp_cl/core/opencl_kernels.py
       compile_options += ["-I", _INCLUDE_PATH]

   pyopencl joins the option list on spaces (``pyopencl/__init__.py``,
   ``b" ".join(...)``), so a path containing a space becomes two options and the
   OpenCL compiler rejects the build::

       clBuildProgram failed: INVALID_BUILD_OPTIONS
       Unrecognized build options: - Workspace\\...\\sources\\include

   pyopencl quotes its *own* include path, which is why only bempp-cl's is
   affected. Quoting ``_INCLUDE_PATH`` is sufficient and is a no-op on paths
   without spaces.

2. pyopencl's build-cache error handler reads an environment variable that it
   never guarantees is set::

       if os.environ["PYOPENCL_CACHE_FAILURE_FATAL"]:

   so when a build fails, the handler itself raises
   ``KeyError: 'PYOPENCL_CACHE_FAILURE_FATAL'`` and destroys the real compiler
   diagnostic. Providing a default keeps the true error visible.

Both patches are idempotent and safe to call repeatedly. Neither changes any
numerics: the first only fixes how a path is quoted on a compiler command line,
the second only affects error reporting.
"""

from __future__ import annotations

import os
from typing import Any

_CACHE_FAILURE_FATAL_ENV = "PYOPENCL_CACHE_FAILURE_FATAL"

_state: dict[str, Any] = {
    "applied": False,
    "includePathQuoted": False,
    "cacheEnvDefaulted": False,
    "reason": "not applied",
}


def _quote_include_path() -> tuple[bool, str]:
    """Quote bempp-cl's kernel include path. Returns ``(changed, reason)``."""
    try:
        from bempp_cl.core import opencl_kernels  # type: ignore
    except Exception as exc:
        return False, f"bempp_cl.core.opencl_kernels is unavailable ({type(exc).__name__})."

    current = getattr(opencl_kernels, "_INCLUDE_PATH", None)
    if not isinstance(current, str) or not current:
        return False, "bempp-cl _INCLUDE_PATH is missing or not a string."

    if current.startswith('"') and current.endswith('"'):
        return False, "bempp-cl include path is already quoted."

    if " " not in current:
        # Quoting would still be correct, but leaving it untouched keeps this
        # shim a no-op on the hosts that never needed it.
        return False, "bempp-cl include path contains no space; no change needed."

    opencl_kernels._INCLUDE_PATH = f'"{current}"'
    return True, "Quoted bempp-cl include path for clBuildProgram."


def apply_bempp_opencl_workarounds() -> dict[str, Any]:
    """Apply both workarounds. Never raises; returns a status dict."""
    if _state["applied"]:
        return dict(_state)

    cache_defaulted = False
    if _CACHE_FAILURE_FATAL_ENV not in os.environ:
        # Empty string is falsy, which is what pyopencl's check expects when a
        # cache write fails but the build itself should still be reported.
        os.environ[_CACHE_FAILURE_FATAL_ENV] = ""
        cache_defaulted = True

    try:
        quoted, reason = _quote_include_path()
    except Exception as exc:  # pragma: no cover - defensive
        quoted, reason = False, f"include-path patch failed ({type(exc).__name__}: {exc})."

    _state.update(
        applied=True,
        includePathQuoted=quoted,
        cacheEnvDefaulted=cache_defaulted,
        reason=reason,
    )
    return dict(_state)


def workaround_status() -> dict[str, Any]:
    """Report what was applied, for diagnostics."""
    return dict(_state)
