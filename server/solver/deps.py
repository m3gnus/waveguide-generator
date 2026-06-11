"""
Dependency discovery and compatibility checks for backend solver components.
"""

from __future__ import annotations

import logging
import re
import sys
from importlib import metadata
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_PYTHON_MIN = (3, 10, 0)
SUPPORTED_PYTHON_MAX_EXCLUSIVE = (3, 15, 0)
SUPPORTED_GMSH_MIN = (4, 11, 0)
SUPPORTED_GMSH_MAX_EXCLUSIVE = (5, 0, 0)

SUPPORTED_DEPENDENCY_MATRIX: Dict[str, Dict[str, str]] = {
    "python": {"range": ">=3.10,<3.15"},
    "hornlab_waveguide_mesher": {
        "range": "pinned git commit 2eb7b85",
        "required_for": "/api/mesh/build",
    },
    "hornlab_metal_bem": {
        "range": "pinned git commit 59528f5",
        "required_for": "/api/solve backend",
    },
    "hornlab_bempp_bem": {
        "range": "pinned git commit 796bef4",
        "required_for": "/api/solve fallback backend (non-Apple-Silicon)",
    },
    "gmsh_python": {"range": ">=4.11,<5.0", "required_for": "hornlab-waveguide-mesher"},
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
    HORNLAB_MESHER_VERSION = _distribution_version("hornlab-waveguide-mesher")
    HORNLAB_MESHER_AVAILABLE = HORNLAB_MESHER_VERSION is not None
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

HORNLAB_BEMPP_BEM_VERSION = None
try:
    import hornlab_bempp_bem  # type: ignore  # noqa: F401
    HORNLAB_BEMPP_BEM_AVAILABLE = True
    HORNLAB_BEMPP_BEM_VERSION = _distribution_version("hornlab-bempp-bem")
except ImportError:
    HORNLAB_BEMPP_BEM_AVAILABLE = False


if not PYTHON_SUPPORTED:
    logger.warning(
        "Unsupported Python runtime %s; supported range is >=3.10,<3.15.", PYTHON_VERSION
    )
if GMSH_AVAILABLE and not GMSH_SUPPORTED:
    logger.warning(
        "Unsupported gmsh Python package version %s; supported range is >=4.11,<5.0.",
        GMSH_VERSION or "unknown",
    )
if not HORNLAB_MESHER_AVAILABLE:
    logger.warning(
        "hornlab-waveguide-mesher runtime not available or wrong hornlab_mesher package is installed."
    )
if not HORNLAB_METAL_BEM_AVAILABLE:
    logger.warning("hornlab-metal-bem runtime not available.")
if not HORNLAB_BEMPP_BEM_AVAILABLE:
    logger.warning("hornlab-bempp-bem runtime not available.")


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
            "hornlab_bempp_bem": {
                "available": HORNLAB_BEMPP_BEM_AVAILABLE,
                "version": HORNLAB_BEMPP_BEM_VERSION,
                "supported": HORNLAB_BEMPP_BEM_AVAILABLE,
                "ready": HORNLAB_BEMPP_BEM_AVAILABLE,
            },
        },
    }
