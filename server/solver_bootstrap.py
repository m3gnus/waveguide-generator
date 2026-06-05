"""Runtime dependency status flags for backend solver integrations."""

import logging

logger = logging.getLogger(__name__)

from solver.deps import (
    BEMPP_RUNTIME_READY,
    HORNLAB_MESHER_AVAILABLE as HORNLAB_MESHER_PACKAGE_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    get_dependency_status,
)
from solver.metal_solver import is_metal_solver_available

METAL_SOLVER_AVAILABLE = is_metal_solver_available()
METAL_SOLVER_READY = METAL_SOLVER_AVAILABLE

SOLVER_AVAILABLE: bool = BEMPP_RUNTIME_READY or METAL_SOLVER_READY
if BEMPP_RUNTIME_READY:
    logger.info(
        "[BEM] Device auto policy: conservative supported ordering "
        "(opencl_cpu, then opencl_gpu when CPU and GPU contexts are both validated)"
    )
elif METAL_SOLVER_READY:
    logger.info("[BEM] Metal BEM backend is ready; BEMPP/OpenCL is optional.")
else:
    logger.warning(
        "No BEM solver backend is ready. Install/enable Metal BEM or install bempp-cl/OpenCL."
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
