"""Runtime dependency status flags for backend solver integrations."""

import logging

logger = logging.getLogger(__name__)

from solver.deps import (
    HORNLAB_MESHER_AVAILABLE as HORNLAB_MESHER_PACKAGE_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    get_dependency_status,
)
from solver.bempp_solver import is_bempp_solver_available
from solver.metal_solver import is_metal_solver_available

METAL_SOLVER_AVAILABLE = is_metal_solver_available()
METAL_SOLVER_READY = METAL_SOLVER_AVAILABLE
BEMPP_SOLVER_AVAILABLE = is_bempp_solver_available()
BEMPP_SOLVER_READY = BEMPP_SOLVER_AVAILABLE

SOLVER_AVAILABLE: bool = METAL_SOLVER_READY or BEMPP_SOLVER_READY
if METAL_SOLVER_READY:
    logger.info("[BEM] Metal BEM backend is ready.")
elif BEMPP_SOLVER_READY:
    logger.info("[BEM] BEMPP BEM backend is ready.")
else:
    logger.warning(
        "No BEM solver backend is ready. Install/enable hornlab-metal-bem or "
        "install BEMPP fallback with: pip install -r server/requirements-bempp.txt"
    )

try:
    from solver.mesher_adapter import build_waveguide_mesh as _waveguide_mesh_builder
except ImportError:
    _HORNLAB_MESHER_ADAPTER_AVAILABLE = False
else:
    _HORNLAB_MESHER_ADAPTER_AVAILABLE = True

HORNLAB_MESHER_AVAILABLE = (
    HORNLAB_MESHER_PACKAGE_AVAILABLE and _HORNLAB_MESHER_ADAPTER_AVAILABLE
)
