"""FastAPI assembly for the Waveguide Generator v2 shell."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
from pathlib import Path
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.engines.registry import EngineRegistry, detect_engines
from server.charts import mount_charts
from server.design_io import mount_design_io
from server.exports import mount_exports
from server.jobs import mount_jobs
from server.mesh.gmsh_worker import prewarm_gmsh_worker, shutdown_gmsh_worker
from server.platform.origin import local_origin
from server.platform.paths import resolve_data_dir
from server.preview.service import mount_preview
from server.workspace import mount_workspace


VERSION = str(
    json.loads(
        (Path(__file__).resolve().parents[1] / "shared" / "version.json").read_text(
            encoding="utf-8"
        )
    )["version"]
)
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
request_log = logging.getLogger("wg2.requests")
_local_origin = local_origin


def create_app(*, data_dir: str | Path | None = None) -> FastAPI:
    """Assemble an app instance without creating persistent directories."""

    started = time.monotonic()
    resolved_data_dir = resolve_data_dir(data_dir)
    application = FastAPI(title="Waveguide Generator v2", version=VERSION)
    application.state.started = started
    application.state.data_dir = resolved_data_dir
    engine_registry = EngineRegistry(detector=detect_engines)
    application.state.engine_registry = engine_registry
    logging.getLogger("wg2").info("Waveguide Generator v%s application initialized", VERSION)
    # One persistent owner thread services every gmsh call.  Prewarming here
    # retains the v1 off-main-thread ``interruptible=False`` invariant from
    # ``server/services/gmsh_worker.py:44-84`` and removes the first-build cliff.
    application.router.add_event_handler("startup", prewarm_gmsh_worker)

    @application.middleware("http")
    async def origin_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and not _local_origin(origin):
            request_log.warning(
                "Rejected %s %s from non-local Origin %r; use the local application URL",
                request.method,
                request.url.path,
                origin,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Non-local Origin rejected. Open Waveguide Generator v2 "
                    "from http://127.0.0.1 or http://localhost."
                },
            )
        return await call_next(request)

    @application.middleware("http")
    async def log_request(request: Request, call_next):
        began = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            request_log.exception(
                "%s %s failed after %.1f ms",
                request.method,
                request.url.path,
                (time.monotonic() - began) * 1000,
            )
            raise
        request_log.info(
            "%s %s -> %d in %.1f ms",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - began) * 1000,
        )
        return response

    @application.get("/health")
    async def health() -> dict[str, object]:
        return {
            "version": VERSION,
            "uptime": max(0.0, time.monotonic() - started),
            "data_dir": str(resolved_data_dir),
        }

    @application.get("/api/capabilities")
    async def capabilities() -> dict[str, object]:
        capabilities_cache = [
            asdict(engine) for engine in await engine_registry.capabilities()
        ]
        available = {
            item["name"]
            for item in capabilities_cache
            if item.get("available") is True
        }
        resolved = next(
            (name for name in ("metal", "bempp", "dryrun") if name in available),
            None,
        )
        return {
            "engines": capabilities_cache,
            "engineSelection": {
                "default": "auto",
                "resolvedDefault": resolved,
                "full3dOrder": ["metal", "bempp", "dryrun"],
                "circsymRequires": "circsym",
            },
        }

    mount_preview(application)
    mount_design_io(application)
    mount_exports(application)
    mount_jobs(application, engine_registry)
    mount_workspace(application)
    mount_charts(application)
    # Job tasks stop first; only then may their shared gmsh owner be finalized.
    application.router.add_event_handler("shutdown", shutdown_gmsh_worker)
    application.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return application


app = create_app()
