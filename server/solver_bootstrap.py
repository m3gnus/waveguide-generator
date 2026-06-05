"""Runtime dependency status flags for backend solver integrations."""

import logging

logger = logging.getLogger(__name__)

from solver.deps import (
    BEMPP_RUNTIME_READY,
    HORNLAB_MESHER_AVAILABLE as HORNLAB_MESHER_PACKAGE_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    get_dependency_status,
)
from solver.metal_solver import is_metal_solver_available, metal_backend_status

SOLVER_AVAILABLE: bool = BEMPP_RUNTIME_READY
if SOLVER_AVAILABLE:
    logger.info(
        "[BEM] Device auto policy: conservative supported ordering "
        "(opencl_cpu, then opencl_gpu when CPU and GPU contexts are both validated)"
    )
else:
    logger.warning("BEM solver not available. Install bempp-cl to enable simulations.")

try:
    from solver.mesher_adapter import build_waveguide_mesh as _waveguide_mesh_builder
except ImportError:
    _HORNLAB_MESHER_ADAPTER_AVAILABLE = False
else:
    _HORNLAB_MESHER_ADAPTER_AVAILABLE = True

HORNLAB_MESHER_AVAILABLE = (
    HORNLAB_MESHER_PACKAGE_AVAILABLE and _HORNLAB_MESHER_ADAPTER_AVAILABLE
)

METAL_SOLVER_AVAILABLE = is_metal_solver_available()
METAL_SOLVER_READY = METAL_SOLVER_AVAILABLE
