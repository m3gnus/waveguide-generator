"""FastAPI assembly for the Waveguide Generator shell."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import time

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from server.engines.registry import EngineRegistry, detect_engines
from server.design.schema import DesignConfig
from server.charts import mount_charts
from server.cadlink import mount_cadlink, mount_onshape
from server.design_io import mount_design_io
from server.exports import mount_exports
from server.jobs import mount_jobs
from server.mesh.gmsh_worker import prewarm_gmsh_worker, shutdown_gmsh_worker
from server.mesh.prewarm import prewarm_mesher, shutdown_mesher_prewarm
from server.platform.origin import (
    local_request_host,
    parse_extra_websocket_origins,
    request_origin_allowed,
)
from server.platform.paths import resolve_data_dir
from server.preview.service import mount_preview
from server.settings import mount_settings
from server.solver.symmetry import resolve_symmetry
from server.platform.paths import default_runs_dir
from server.workspace import mount_workspace
from server.workspace.api import MAX_EXPORT_REQUEST_BODY_BYTES
from server.updates import mount_updates


VERSION = str(
    json.loads(
        (Path(__file__).resolve().parents[1] / "shared" / "version.json").read_text(
            encoding="utf-8"
        )
    )["version"]
)
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
LEGACY_WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "output"
request_log = logging.getLogger("wg.requests")
DEFAULT_MAX_REQUEST_BODY_BYTES = 64 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = DEFAULT_MAX_REQUEST_BODY_BYTES
_WORKSPACE_EXPORT_PATH = "/api/workspace/write-export"


class _RequestBodyLimitMiddleware:
    """Enforce a global byte ceiling without buffering accepted request bodies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        path_limits: dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path_limits = dict(path_limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path_limit = self.path_limits.get(str(scope.get("path", "")))
        applied_limit = path_limit if path_limit is not None else self.max_body_bytes

        for name, raw_value in scope.get("headers", ()):
            if name.lower() != b"content-length":
                continue
            try:
                declared_size = int(raw_value)
            except ValueError:
                break
            if declared_size > applied_limit:
                await self._reject(
                    scope,
                    receive,
                    send,
                    applied_limit=applied_limit,
                    include_default=path_limit is None,
                )
                return
            break

        received = 0
        response_started = False
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > applied_limit:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if too_large:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except Exception:
            if not too_large:
                raise
        if too_large:
            if response_started:
                raise RuntimeError(
                    "request body exceeded the limit after the response started"
                ) from None
            await self._reject(
                scope,
                receive,
                send,
                applied_limit=applied_limit,
                include_default=path_limit is None,
            )

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        applied_limit: int,
        include_default: bool,
    ) -> None:
        if applied_limit % (1024 * 1024) == 0:
            label = f"{applied_limit // (1024 * 1024)} MB"
        else:
            label = f"{applied_limit} bytes"
        if include_default and applied_limit != DEFAULT_MAX_REQUEST_BODY_BYTES:
            label += " (64 MB production default)"
        await JSONResponse(
            status_code=413,
            content={"detail": f"Request body exceeds the {label} limit."},
        )(scope, receive, send)


async def prewarm_solver() -> None:
    """Start the solver warmup. Deliberately imported late and never awaited."""

    from server.solver.warmup import start_solver_warmup

    start_solver_warmup()


class _HashedAssetStaticFiles(StaticFiles):
    """Serve the SPA with cache lifetimes that match how Vite names files.

    Everything under ``/assets/`` carries a content hash in its filename, so a
    changed build is a changed URL and the old URL can never be stale.  Those
    are safe to mark immutable, which stops the browser revalidating three
    multi-hundred-kB chunks on every single page load.  ``index.html`` is the
    one file whose name never changes and is what points at the hashed names,
    so it must never be cached: a stale copy pins the whole app to a build
    that may no longer be on disk.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):  # type: ignore[no-untyped-def,override]
        response = super().file_response(full_path, stat_result, scope, status_code)
        # Decide from the file on disk rather than from the response: a
        # conditional request comes back as a 304 with no ``path`` attribute,
        # and a 304 that forgot to repeat ``immutable`` would teach the browser
        # to keep revalidating the very files this exists to stop revalidating.
        served = str(full_path).replace("\\", "/")
        response.headers["cache-control"] = (
            "public, max-age=31536000, immutable"
            if "/assets/" in served
            else "no-cache"
        )
        return response


def create_app(
    *,
    data_dir: str | Path | None = None,
    workspace_dir: str | Path | None = None,
    solver_warmup: bool = False,
    update_request_path: str | Path | None = None,
) -> FastAPI:
    """Assemble an app instance without creating persistent directories.

    Explicit callers may set ``solver_warmup`` to exercise AUTO's selected
    physical solver in a background thread at boot. It defaults to off because
    that native solve is not coordinated with either user jobs or application
    shutdown. The production launcher passes true only for an explicit
    ``WG2_SOLVER_WARMUP=1`` diagnostic opt-in.
    """

    started = time.monotonic()
    resolved_data_dir = resolve_data_dir(data_dir)
    application = FastAPI(title="Waveguide Generator", version=VERSION)
    application.state.started = started
    application.state.data_dir = resolved_data_dir
    extra_ws_origins = parse_extra_websocket_origins(
        os.environ.get("WG2_EXTRA_WS_ORIGINS")
    )
    engine_registry = EngineRegistry(detector=detect_engines)
    application.state.engine_registry = engine_registry
    logging.getLogger("wg").info("Waveguide Generator v%s application initialized", VERSION)
    # The SPA is 2.27 MB of JavaScript and 187 kB of CSS.  Even on loopback that
    # is worth compressing: it gzips to roughly a quarter of the bytes, and the
    # same middleware covers the multi-hundred-kB results payloads.  500 bytes
    # keeps the small JSON replies uncompressed, where framing would dominate.
    #
    # Level 1, not Starlette's default of 9.  Compression runs synchronously on
    # the event loop, and measured over the real dist assets a cold page load
    # costs 125 ms of loop CPU at level 9, 90 ms at level 6 and 36 ms at level
    # 1 -- while level 9 produces only 0.3% fewer bytes than level 6.  Trading
    # 0.3% of loopback bytes for 89 ms of first-paint latency is not a trade
    # worth making.
    application.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=1)
    application.add_middleware(
        _RequestBodyLimitMiddleware,
        max_body_bytes=MAX_REQUEST_BODY_BYTES,
        path_limits={_WORKSPACE_EXPORT_PATH: MAX_EXPORT_REQUEST_BODY_BYTES},
    )
    # One persistent owner thread services every gmsh call.  Prewarming here
    # retains the v1 off-main-thread ``interruptible=False`` invariant from
    # ``server/services/gmsh_worker.py:44-84`` and removes the first-build cliff.
    application.router.add_event_handler("startup", prewarm_gmsh_worker)
    # The mesher imports lazily at every call site, so without this the first
    # control a user touches pays for the whole import graph.
    application.router.add_event_handler("startup", prewarm_mesher)

    def _keep_sigpipe_ignored() -> None:
        # gmsh's C++ runtime installs its own signal handlers during the
        # prewarm above, and on Linux that can leave SIGPIPE at the default
        # action -- after which a client disconnecting mid-response kills the
        # whole server instead of raising BrokenPipeError. Restore Python's
        # startup state once the native stack is loaded.
        import os as _os
        import signal as _signal

        if _os.name == "posix":
            _signal.signal(_signal.SIGPIPE, _signal.SIG_IGN)

    application.router.add_event_handler("startup", _keep_sigpipe_ignored)
    # Likewise the engine probe: it is the page load's slowest request, and
    # leaving it lazy made it contend with the first symmetry resolution.
    application.router.add_event_handler("startup", engine_registry.prewarm)
    if solver_warmup:
        application.router.add_event_handler("startup", prewarm_solver)

    @application.middleware("http")
    async def origin_guard(request: Request, call_next):
        server = request.scope.get("server")
        bound_port = (
            server[1]
            if isinstance(server, (list, tuple)) and len(server) > 1
            else None
        )
        host = request.headers.get("host")
        if not local_request_host(
            host,
            scheme=request.url.scheme,
            bound_port=bound_port,
        ):
            request_log.warning(
                "Rejected %s %s for non-local Host %r; use the local application URL",
                request.method,
                request.url.path,
                host,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Non-local Host rejected. Open Waveguide Generator "
                    "from the loopback URL printed by the launcher."
                },
            )
        origin = request.headers.get("origin")
        if not request_origin_allowed(
            origin=origin,
            host=host,
            scheme=request.url.scheme,
            bound_port=bound_port,
            extra_origins=extra_ws_origins,
        ):
            request_log.warning(
                "Rejected %s %s from disallowed Origin %r; use the local application URL",
                request.method,
                request.url.path,
                origin,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Non-local Origin or unapproved cross-origin request "
                    "rejected. Open Waveguide Generator from its current loopback URL."
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
            (name for name in ("metal", "beat", "bempp", "dryrun") if name in available),
            None,
        )
        return {
            "engines": capabilities_cache,
            "engineSelection": {
                "default": "auto",
                "resolvedDefault": resolved,
                "full3dOrder": ["metal", "beat", "bempp", "dryrun"],
                "metalFastPath": "axisymmetric-meridian",
            },
        }

    @application.post("/api/design/symmetry")
    async def design_symmetry(design: DesignConfig) -> dict[str, object]:
        resolution = await asyncio.to_thread(resolve_symmetry, design)
        return resolution.as_dict()

    mount_preview(application, extra_ws_origins=extra_ws_origins)
    mount_design_io(application)
    mount_exports(application)
    mount_jobs(
        application,
        engine_registry,
        extra_ws_origins=extra_ws_origins,
    )
    if workspace_dir is not None:
        resolved_workspace_dir = Path(workspace_dir).expanduser().resolve()
        # Only the checkout's ``output`` is a former *export* destination. The
        # data directory's ``workspace`` is v1 task scratch -- one UUID folder
        # per migrated job, holding manifests and raw results -- and adopting
        # that as somebody's export folder would bury their runs among hundreds
        # of them, inside a directory Finder hides.
        legacy_workspace_dirs: tuple[Path, ...] = (LEGACY_WORKSPACE_DIR,)
    elif data_dir is None:
        resolved_workspace_dir = default_runs_dir()
        legacy_workspace_dirs = (LEGACY_WORKSPACE_DIR,)
    else:
        # Explicit data roots keep tests and embedded callers isolated unless
        # they also opt into a separate user-document workspace.
        resolved_workspace_dir = resolved_data_dir / "workspace"
        legacy_workspace_dirs = ()
    mount_workspace(
        application,
        default_path=resolved_workspace_dir,
        legacy_defaults=legacy_workspace_dirs,
    )
    mount_cadlink(application)
    mount_onshape(application)
    mount_charts(application)
    mount_settings(application)
    mount_updates(
        application,
        running_version=VERSION,
        data_dir=resolved_data_dir,
        repo_root=Path(__file__).resolve().parents[1],
        update_request_path=(
            Path(update_request_path) if update_request_path is not None else None
        ),
    )
    # Job tasks stop first; only then may their shared gmsh owner be finalized.
    application.router.add_event_handler("shutdown", shutdown_mesher_prewarm)
    application.router.add_event_handler("shutdown", engine_registry.shutdown_prewarm)
    application.router.add_event_handler("shutdown", shutdown_gmsh_worker)
    application.mount(
        "/", _HashedAssetStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend"
    )
    return application


def __getattr__(name: str) -> object:
    """Build the module-level ASGI app only if something actually asks for it.

    ``app = create_app()`` used to run at import time, which meant every
    ``import server.app`` -- including the one ``launch/serve.py`` does before
    it builds the app it will really serve -- constructed a second FastAPI
    instance, a second preview ThreadPoolExecutor and a second StaticFiles
    mount, then dropped them.  Worse, it resolved the data directory at import
    time, before ``main()`` has had the chance to honour ``--data-dir``, so the
    orphan pointed at the default location.

    The name still exists for ``uvicorn server.app:app``, which is the only
    thing that ever wanted it.  PEP 562 makes that lazy.
    """

    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
