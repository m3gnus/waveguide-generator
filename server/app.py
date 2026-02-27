"""
MWG Horn BEM Solver Backend — application assembly.

This module is intentionally thin: it wires together the FastAPI app,
registers routers, sets up CORS, and manages the startup/shutdown lifecycle.
All route handlers, service logic, and state live in the sub-packages.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, os.getenv("MWG_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ── Re-export solver bootstrap (keeps "from app import X" and patch("app.X") working) ──
from solver_bootstrap import (  # noqa: E402
    SOLVER_AVAILABLE,
    BEMPP_RUNTIME_READY,
    GMSH_OCC_RUNTIME_READY,
    WAVEGUIDE_BUILDER_AVAILABLE,
    BEMSolver,
    build_waveguide_mesh,
    normalize_mesh_validation_mode,
    normalize_device_mode,
    get_dependency_status,
)

# ── Re-export models (keeps "from app import SimulationRequest" working) ───────
from models import (  # noqa: E402
    BoundaryCondition,
    MeshData,
    PolarConfig,
    SimulationRequest,
    JobStatus,
    SimulationResults,
    WaveguideParamsRequest,
    ChartsRenderRequest,
    DirectivityRenderRequest,
)

# ── Re-export runtime state (keeps "from app import jobs, db, ..." working) ───
from services.job_runtime import (  # noqa: E402
    jobs,
    job_queue,
    running_jobs,
    jobs_lock,
    scheduler_loop_running,
    max_concurrent_jobs,
    db,
    db_initialized,
    ensure_db_ready,
    startup_jobs_runtime,
    _drain_scheduler_queue,
    _merge_job_cache_from_db,
    _set_job_fields,
    update_progress,
    update_job_stage,
    _remove_from_queue,
    _build_config_summary,
    _serialize_job_item,
    _parse_status_filters,
    _is_terminal_status,
    _now_iso,
)

# ── Re-export update service (keeps "from app import get_update_status" working) ─
from services.update_service import (  # noqa: E402
    _run_git,
    get_update_status,
)

# ── Re-export simulation runner ────────────────────────────────────────────────
from services.simulation_runner import (  # noqa: E402
    run_simulation,
    _validate_occ_adaptive_bem_shell,
)

# ── Re-export route handlers for backward compat with tests ───────────────────
from api.routes_misc import (  # noqa: E402
    health_check,
    check_updates,
    render_charts,
    render_directivity,
    root,
)
from api.routes_mesh import (  # noqa: E402
    build_mesh_from_params,
)
from api.routes_simulation import (  # noqa: E402
    submit_simulation,
    stop_simulation,
    get_job_status,
    get_results,
    get_mesh_artifact,
    list_jobs,
    clear_failed_jobs,
    delete_job,
)

# ── Routers ────────────────────────────────────────────────────────────────────
from api.routes_misc import router as _misc_router  # noqa: E402
from api.routes_mesh import router as _mesh_router  # noqa: E402
from api.routes_simulation import router as _sim_router  # noqa: E402


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    await startup_jobs_runtime()
    yield


# ── FastAPI application ────────────────────────────────────────────────────────
app = FastAPI(title="MWG Horn BEM Solver", version="1.0.0", lifespan=app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(_misc_router)
app.include_router(_mesh_router)
app.include_router(_sim_router)


if __name__ == "__main__":
    import uvicorn

    print("Starting MWG Horn BEM Solver Backend...")
    print(f"Solver available: {SOLVER_AVAILABLE}")
    print(f"OCC builder ready: {WAVEGUIDE_BUILDER_AVAILABLE and GMSH_OCC_RUNTIME_READY}")
    if not SOLVER_AVAILABLE:
        print("Warning: bempp-cl not installed. Install it to enable simulations.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
