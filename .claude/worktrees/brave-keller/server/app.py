"""MWG Horn BEM Solver Backend application assembly."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes_mesh import router as mesh_router
from api.routes_misc import router as misc_router
from api.routes_simulation import router as simulation_router
from services.job_runtime import startup_jobs_runtime
from solver_bootstrap import (
    GMSH_OCC_RUNTIME_READY,
    SOLVER_AVAILABLE,
    WAVEGUIDE_BUILDER_AVAILABLE,
)

logging.basicConfig(
    level=getattr(logging, os.getenv("MWG_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    await startup_jobs_runtime()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="MWG Horn BEM Solver", version="1.0.0", lifespan=app_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(misc_router)
    app.include_router(mesh_router)
    app.include_router(simulation_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    print("Starting MWG Horn BEM Solver Backend...")
    print(f"Solver available: {SOLVER_AVAILABLE}")
    print(f"OCC builder ready: {WAVEGUIDE_BUILDER_AVAILABLE and GMSH_OCC_RUNTIME_READY}")
    if not SOLVER_AVAILABLE:
        print(
            "Warning: bempp-cl not installed. /api/solve is unavailable until bempp-cl is installed."
        )
    uvicorn.run(app, host="0.0.0.0", port=8000)
