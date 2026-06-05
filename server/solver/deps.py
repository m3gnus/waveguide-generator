"""
Dependency discovery and compatibility checks for backend solver components.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from importlib import metadata
from typing import Dict, Optional, Tuple

# On Windows, Python 3.8+ no longer searches PATH for DLL dependencies.
# If the VC++ Redistributable is not installed system-wide, bempp-cl can fail
# to load with "DLL load failed". As a
# workaround, add directories on PATH that contain the required MSVC
# runtime DLLs (e.g. MSVCP140.dll, api-ms-win-crt-*) to the DLL search
# list so the extension modules can find them.
if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    for _dir in os.environ.get("PATH", "").split(os.pathsep):
        if _dir and os.path.isfile(os.path.join(_dir, "MSVCP140.dll")):
            try:
                os.add_dll_directory(_dir)
            except OSError:
                pass

logger = logging.getLogger(__name__)

SUPPORTED_PYTHON_MIN = (3, 10, 0)
SUPPORTED_PYTHON_MAX_EXCLUSIVE = (3, 15, 0)
SUPPORTED_GMSH_MIN = (4, 11, 0)
SUPPORTED_GMSH_MAX_EXCLUSIVE = (5, 0, 0)
SUPPORTED_BEMPP_CL_MIN = (0, 4, 0)
SUPPORTED_BEMPP_CL_MAX_EXCLUSIVE = (0, 5, 0)

SUPPORTED_DEPENDENCY_MATRIX: Dict[str, Dict[str, str]] = {
    "python": {"range": ">=3.10,<3.15"},
    "hornlab_waveguide_mesher": {
        "range": "pinned git commit 334e51f",
        "required_for": "/api/mesh/build",
    },
    "hornlab_metal_bem": {
        "range": "pinned git commit 0cc9c74",
        "required_for": "default /api/solve backend when ready",
    },
    "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "hornlab-waveguide-mesher"},
    "bempp_cl": {
        "range": ">=0.4,<0.5",
        "required_for": "BEMPP fallback /api/solve backend",
    },
}


def _parse_version_tuple(raw: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if raw is None:
        return None
    parts = [int(item) for item in re.findall(r"\d+", str(raw))]
    if not parts:
        return None
    major = parts[0]
    minor = parts[1] if len(parts) > 1 else 0
    patch = parts[2] if len(parts) > 2 else 0
    return (major, minor, patch)


def _in_supported_range(
    version: Optional[Tuple[int, int, int]],
    min_version: Tuple[int, int, int],
    max_exclusive: Tuple[int, int, int],
) -> bool:
    if version is None:
        return False
    return min_version <= version < max_exclusive


def _distribution_version(*names: str) -> Optional[str]:
    for name in names:
        if not name:
            continue
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return None


PYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
PYTHON_VERSION_TUPLE = (
    sys.version_info.major,
    sys.version_info.minor,
    sys.version_info.micro,
)
PYTHON_SUPPORTED = _in_supported_range(
    PYTHON_VERSION_TUPLE,
    SUPPORTED_PYTHON_MIN,
    SUPPORTED_PYTHON_MAX_EXCLUSIVE,
)


try:
    import gmsh  # type: ignore
    GMSH_AVAILABLE = True
except ImportError:
    GMSH_AVAILABLE = False
    gmsh = None

GMSH_VERSION = None
GMSH_VERSION_TUPLE = None
if GMSH_AVAILABLE:
    GMSH_VERSION = getattr(gmsh, "__version__", None) or _distribution_version("gmsh")
    GMSH_VERSION_TUPLE = _parse_version_tuple(GMSH_VERSION)

GMSH_SUPPORTED = GMSH_AVAILABLE and PYTHON_SUPPORTED and _in_supported_range(
    GMSH_VERSION_TUPLE,
    SUPPORTED_GMSH_MIN,
    SUPPORTED_GMSH_MAX_EXCLUSIVE,
)

HORNLAB_MESHER_VERSION = None
try:
    from hornlab_mesher.config_builder import build_from_config as _hornlab_mesher_build_from_config  # type: ignore  # noqa: F401
    HORNLAB_MESHER_AVAILABLE = True
    HORNLAB_MESHER_VERSION = _distribution_version("hornlab-waveguide-mesher")
except ImportError:
    HORNLAB_MESHER_AVAILABLE = False

HORNLAB_MESHER_RUNTIME_READY = HORNLAB_MESHER_AVAILABLE and GMSH_SUPPORTED

HORNLAB_METAL_BEM_VERSION = None
try:
    import hornlab_metal_bem  # type: ignore  # noqa: F401
    HORNLAB_METAL_BEM_AVAILABLE = True
    HORNLAB_METAL_BEM_VERSION = _distribution_version("hornlab-metal-bem")
except ImportError:
    HORNLAB_METAL_BEM_AVAILABLE = False


BEMPP_VARIANT = None
BEMPP_VERSION = None
BEMPP_VERSION_TUPLE = None
BEMPP_IMPORT_ERROR = None

try:
    # Preferred package for this project.
    import bempp_cl.api as bempp_api  # type: ignore
    BEMPP_AVAILABLE = True
    BEMPP_VARIANT = "bempp_cl"
    BEMPP_VERSION = (
        _distribution_version("bempp-cl", "bempp_cl")
        or getattr(bempp_api, "__version__", None)
    )
except ImportError:
    BEMPP_AVAILABLE = False
    bempp_api = None
except Exception as exc:
    # bempp-cl can fail during module initialization even when installed
    # (for example numba cache/runtime errors). Treat that as unavailable so
    # backend startup and Metal solver discovery can continue.
    BEMPP_AVAILABLE = False
    BEMPP_IMPORT_ERROR = str(exc)
    bempp_api = None

if BEMPP_AVAILABLE:
    BEMPP_VERSION_TUPLE = _parse_version_tuple(BEMPP_VERSION)

BEMPP_SUPPORTED = BEMPP_AVAILABLE and PYTHON_SUPPORTED and _in_supported_range(
    BEMPP_VERSION_TUPLE,
    SUPPORTED_BEMPP_CL_MIN,
    SUPPORTED_BEMPP_CL_MAX_EXCLUSIVE,
)

BEMPP_RUNTIME_READY = BEMPP_AVAILABLE and BEMPP_SUPPORTED

if not PYTHON_SUPPORTED:
    logger.warning(
        "Unsupported Python runtime %s; supported range is >=3.10,<3.15.", PYTHON_VERSION
    )
if GMSH_AVAILABLE and not GMSH_SUPPORTED:
    logger.warning(
        "Unsupported gmsh Python package version %s; supported range is >=4.11,<5.0.",
        GMSH_VERSION or "unknown",
    )
if BEMPP_AVAILABLE and not BEMPP_SUPPORTED:
    logger.warning(
        "Unsupported bempp runtime %s %s; supported range is %s.",
        BEMPP_VARIANT or "unknown",
        BEMPP_VERSION or "unknown",
        ">=0.4,<0.5",
    )
if not BEMPP_AVAILABLE:
    if BEMPP_IMPORT_ERROR:
        logger.warning("bempp runtime import failed: %s", BEMPP_IMPORT_ERROR)
    else:
        logger.warning("bempp runtime not available (install bempp-cl >=0.4,<0.5).")
if not HORNLAB_MESHER_AVAILABLE:
    logger.warning("hornlab-waveguide-mesher runtime not available.")
if not HORNLAB_METAL_BEM_AVAILABLE:
    logger.warning("hornlab-metal-bem runtime not available.")


def get_dependency_status() -> Dict[str, Dict[str, object]]:
    return {
        "supportedMatrix": SUPPORTED_DEPENDENCY_MATRIX,
        "runtime": {
            "python": {
                "version": PYTHON_VERSION,
                "supported": PYTHON_SUPPORTED,
            },
            "gmsh_python": {
                "available": GMSH_AVAILABLE,
                "version": GMSH_VERSION,
                "supported": GMSH_SUPPORTED,
                "ready": GMSH_SUPPORTED,
            },
            "hornlab_waveguide_mesher": {
                "available": HORNLAB_MESHER_AVAILABLE,
                "version": HORNLAB_MESHER_VERSION,
                "supported": HORNLAB_MESHER_AVAILABLE,
                "ready": HORNLAB_MESHER_RUNTIME_READY,
            },
            "hornlab_metal_bem": {
                "available": HORNLAB_METAL_BEM_AVAILABLE,
                "version": HORNLAB_METAL_BEM_VERSION,
                "supported": HORNLAB_METAL_BEM_AVAILABLE,
                "ready": HORNLAB_METAL_BEM_AVAILABLE,
            },
            "bempp": {
                "available": BEMPP_AVAILABLE,
                "variant": BEMPP_VARIANT,
                "version": BEMPP_VERSION,
                "supported": BEMPP_SUPPORTED,
                "ready": BEMPP_RUNTIME_READY,
                "import_error": BEMPP_IMPORT_ERROR,
            },
        },
    }
