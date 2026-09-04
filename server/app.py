"""FastAPI assembly for the Waveguide Generator shell."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from server.engines.registry import EngineRegistry, detect_engines
from server.design.schema import DesignConfig
from server.charts import mount_charts
from server.diagnostics import mount_diagnostics
from server.diagnostics.api import CLIENT_LOG_PATH, MAX_CLIENT_LOG_BODY_BYTES
from server.diagnostics.capabilities import capabilities_payload
from server.cadlink import mount_cadlink, mount_onshape
from server.design_io import mount_design_io
from server.drivers import mount_drivers
from server.exports import mount_exports
from server.jobs import mount_jobs
from server.integration import mount_integration
from server.mesh.api import mount_solver_mesh
from server.mesh.gmsh_worker import prewarm_gmsh_worker, shutdown_gmsh_worker
from server.mesh.prewarm import prewarm_mesher, shutdown_mesher_prewarm
from server.platform.origin import (
    local_request_host,
    parse_extra_websocket_origins,
    request_origin_allowed,
)
from server.platform.acl_migration import repair_legacy_acls
from server.platform.paths import app_root, default_runs_dir, resolve_data_dir
from shared.build_identity import build_identity, build_label
from server.preview.service import mount_preview
from server.settings import mount_settings
from server.solver.symmetry import resolve_symmetry
from server.workspace import mount_workspace
from server.workspace.api import MAX_EXPORT_REQUEST_BODY_BYTES
from server.updates import mount_updates


APP_ROOT = app_root()
VERSION = str(
    json.loads(
        (APP_ROOT / "shared" / "version.json").read_text(encoding="utf-8")
    )["version"]
)
#: ``VERSION`` names the last release tag, not this build: the installer tracks
#: a branch, so hundreds of commits share one version string. Report BUILD
#: wherever a human might quote it back to us in a bug report. See
#: ``shared/build_identity.py``.
BUILD = build_label(APP_ROOT)
BUILD_IDENTITY = build_identity(APP_ROOT)
FRONTEND_DIST = APP_ROOT / "frontend" / "dist"
LEGACY_WORKSPACE_DIR = APP_ROOT / "output"
request_log = logging.getLogger("wg.requests")
DEFAULT_MAX_REQUEST_BODY_BYTES = 64 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = DEFAULT_MAX_REQUEST_BODY_BYTES
_WORKSPACE_EXPORT_PATH = "/api/workspace/write-export"
#: ``(method, path)`` pairs whose successful responses log at DEBUG.
#:
#: uvicorn's own access log is off (``launch/serve.py``), so ``log_request``
#: below is the only thing writing a line per request -- and at idle, with the
#: packaged app open and nobody touching it, these five were ~8 lines a second
#: appended to ``server.log`` forever. That is a continuous trickle of small
#: writes that keeps the disk out of its low-power states, and it buries the
#: lines somebody opened the log to find under megabytes of the ones they did
#: not. None of the five says anything after the first time it is read: the
#: health probe, the shell document, and the three CAD-link pollers all mean
#: "still here".
#:
#: Keyed by method as well as path so a route that is idle chatter one way and
#: a real action the other keeps its voice. The chatter itself is being fixed
#: at the callers; this is the backstop for the next poller that appears.
QUIET_REQUEST_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/"),
        ("GET", "/api/cadlink/solve-command"),
        ("GET", "/api/cadlink/returns"),
        ("POST", "/api/cadlink/fusion-status"),
    }
)


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


async def resolve_prewarm_engine(
    engine_registry: EngineRegistry, requested: str | None
) -> str | None:
    """The engine this host's first solve will actually use.

    AUTO's answer while the picker is on AUTO, and the user's own choice when
    it is not. That distinction is the whole point: both prewarm hooks used to
    ask AUTO unconditionally, which is the right question only for a user who
    never touched the picker. A Mac user who has explicitly selected BEAT gets
    ``metal`` from AUTO -- correctly, Metal is faster there -- so the hook
    warmed an engine that user's first solve never reached and left BEAT's
    40 s of Julia startup, package loading and JIT to be paid on it instead.

    ``EngineRegistry.resolve`` returns ``None`` for an engine this host cannot
    run, and that is what makes the fallback safe: a saved choice that no
    longer probes available -- a GPU that has gone, a package that was not
    installed on this machine -- warms whatever AUTO would reach rather than
    warming nothing at all.

    ``requested`` is the saved preference, already read by ``worker_prewarm``:
    it needs the answer before this coroutine can give it one (that is the
    head start), and reading the store twice could hand the two halves
    different answers.
    """

    if requested is not None and requested != "auto":
        selected = await engine_registry.resolve(requested, solver_mode=None)
        if selected is not None:
            return selected
        logging.getLogger("wg.solver.warmup").info(
            "Selected engine %r is not available here; warming AUTO's choice instead",
            requested,
        )
    return await engine_registry.resolve("auto", solver_mode=None)


async def worker_prewarm(
    engine_registry: EngineRegistry,
    settings: object | None,
    *,
    engine: str,
    warm: Callable[[str | None], bool],
    owns: Callable[[str], bool] | None = None,
) -> None:
    """Warm one engine's worker, starting before the probe when we may.

    The capability snapshot costs 2.7-6.6 s per boot on the reference Mac, and
    until now every prewarm waited out all of it before it could even begin --
    so a warmup meant to finish before the user's first solve was still
    compiling at T+19 s while they solved at T+3-8 s, and whoever won the
    single Julia worker paid the compile. Nothing about that wait is needed
    when the user has *explicitly* named an engine: the saved preference is the
    engine their first solve will use, and it is readable from disk in
    microseconds.

    ``owns`` says which saved names belong to this hook, and defaults to the
    one ``engine`` names. BEAT needs more than that: its four execution
    backends are four selectable engines, so a user who picked one has
    ``beat-metal`` saved, not ``beat`` -- and an equality test would have
    silently withheld the head start from exactly the users this was measured
    for, since BEAT's is the long warmup.

    So a saved name starts its warmup immediately, on a thread, and the probe
    is reconciled with it afterwards rather than gating it. The reconciliation
    keeps the pre-existing handling of an engine this host cannot run: a
    warmup for a gone GPU or an uninstalled package raises inside
    ``prewarm_*_for_engine``, which catches it, logs the reason and returns
    False -- and then, exactly as before, whatever AUTO resolved to is warmed
    instead. The cost of guessing early is therefore a failed warmup that is
    logged, never a missing one.
    """

    from server.solver.warmup import persisted_engine_preference

    log = logging.getLogger("wg.solver.warmup")
    claims = owns if owns is not None else (lambda name: name == engine)
    requested = await asyncio.to_thread(persisted_engine_preference, settings)
    head_start: asyncio.Task[bool] | None = None
    if requested is not None and claims(requested):
        log.info(
            "%s worker prewarm starting from the saved engine preference without "
            "waiting for the capability probe",
            engine,
        )
        head_start = asyncio.create_task(asyncio.to_thread(warm, requested))
    try:
        try:
            resolved = await resolve_prewarm_engine(engine_registry, requested)
        except Exception as exc:  # noqa: BLE001 - a prewarm is an optimisation
            log.info("%s worker prewarm could not resolve an engine: %s", engine, exc)
            resolved = None
        # Always awaited, so a head start is never left running behind a return.
        if head_start is not None and (
            await head_start or (resolved is not None and claims(resolved))
        ):
            return
        if resolved is not None:
            await asyncio.to_thread(warm, resolved)
    finally:
        # Only reachable when this coroutine was cancelled -- the shutdown hook
        # cancels it -- and awaiting a task does not cancel that task, so
        # without this the head start would outlive the app as a pending task.
        if head_start is not None and not head_start.done():
            head_start.cancel()


async def bempp_worker_prewarm(
    engine_registry: EngineRegistry, settings: object | None = None
) -> None:
    """Warm the killable BEMPP worker once the engine to warm is known.

    Takes the answer from the registry rather than probing for it. An earlier
    revision called ``resolve_auto_engine()`` on its own thread and so ran a
    second ``detect_engines()`` 2 ms after ``EngineRegistry.prewarm`` began the
    first -- two concurrent cold probes, because ``lru_cache`` does not
    serialise a miss and ``circsym_status`` is not cached at all.
    ``capabilities()`` is guarded by an ``asyncio.Lock``, so awaiting it here
    joins the one probe instead of racing it.
    """

    from server.solver.warmup import prewarm_bempp_worker_for_engine

    await worker_prewarm(
        engine_registry, settings, engine="bempp", warm=prewarm_bempp_worker_for_engine
    )


async def beat_worker_prewarm(
    engine_registry: EngineRegistry, settings: object | None = None
) -> None:
    """Warm the BEAT Julia worker once the engine to warm is known.

    The same shape as ``bempp_worker_prewarm``, and for the same reason: BEAT
    keeps one persistent Julia worker for the life of this process, so its
    startup, package loading, engine compilation and GPU kernel compilation
    are paid once -- but until this hook existed nothing paid them, and a GPU
    host's first solve waited through all of it before its first frequency.
    Both hooks are registered; each returns immediately unless
    ``resolve_prewarm_engine`` named its own engine, so at most one ever warms
    anything.

    BEAT is the engine the head start in ``worker_prewarm`` was measured for:
    its warmup is the long one, and a user who selected it explicitly is
    exactly the user whose first solve is otherwise blocked behind it.
    """

    from server.solver.beat import is_beat_engine
    from server.solver.warmup import prewarm_beat_worker_for_engine

    await worker_prewarm(
        engine_registry,
        settings,
        engine="beat",
        warm=prewarm_beat_worker_for_engine,
        # Every ``beat-*`` variant, plus the legacy bare name: each backend is
        # its own engine and its own Julia worker, and all of them are this
        # hook's to warm.
        owns=is_beat_engine,
    )


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
    logging.getLogger("wg").info("Waveguide Generator %s application initialized", BUILD)
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
        path_limits={
            _WORKSPACE_EXPORT_PATH: MAX_EXPORT_REQUEST_BODY_BYTES,
            CLIENT_LOG_PATH: MAX_CLIENT_LOG_BODY_BYTES,
        },
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
    # The BEMPP worker's own initialization is the single largest thing between
    # a server start and a user's first result, and it is paid in a process the
    # parent can kill. Warm it by default; ``prewarm_solver`` below stays an
    # opt-in because it warms in-process, where shutdown cannot bound the wait.
    async def prewarm_bempp_worker() -> None:
        """Schedule the worker prewarm. Startup runs before the socket is served.

        Unlike ``prewarm_solver`` this is registered by default. Every BEMPP
        solve runs in that worker child, the child's one-off initialization is
        25-61 s on the reference Windows VM, and a child -- unlike an
        in-process warmup thread -- can be killed without its native code
        cooperating, so warming it costs shutdown nothing.
        ``WG2_SOLVER_WARMUP=0`` switches it off; the suite sets that in
        ``server/tests/conftest.py``.
        """

        application.state.bempp_prewarm_task = asyncio.create_task(
            bempp_worker_prewarm(
                engine_registry, getattr(application.state, "settings", None)
            )
        )

    async def prewarm_beat_worker() -> None:
        """Schedule the BEAT worker prewarm alongside the BEMPP one.

        Registered by default for the same reason: the work happens outside
        this process, and ``shutdown_beat_worker`` below lets go of it in
        bounded time -- detaching from a persistent host, terminating a child
        under ``HORNLAB_BEAT_PERSISTENT_HOST=0`` -- so warming it cannot
        lengthen a Quit.
        """

        application.state.beat_prewarm_task = asyncio.create_task(
            beat_worker_prewarm(
                engine_registry, getattr(application.state, "settings", None)
            )
        )

    async def shutdown_beat_worker() -> None:
        """Stop the prewarm and let go of the Julia worker without killing it.

        ``hornlab_beat_bem`` keeps its workers in a module-level registry with
        no exit hook of its own, and the prewarm means one can be alive for a
        session that never solved -- so this hook has to run either way.

        What it releases changed with the pin to ``94deec1``: the worker now
        lives in a persistent host process that outlives this one, so
        ``detach_workers`` closes our connections and leaves the Julia runtime
        running for the next launch to adopt, instead of throwing away a warm
        runtime the user has already paid for. The hosts retire themselves
        after ``HORNLAB_BEAT_WORKER_IDLE_S``, so nothing accumulates, and
        ``HORNLAB_BEAT_PERSISTENT_HOST=0`` puts the worker back in a child
        process -- which this hook terminates, exactly as it always did, since
        such a worker has nothing to detach from.

        ``detach_workers`` is ``shutdown_workers(detach=True)``; the named
        sibling is what the package's application contract points a quit hook
        at, and it says at the call site which of the two lifetimes we mean.

        The guard below is what keeps an older ``hornlab_beat_bem`` -- one
        predating ``detach_workers`` -- from turning Quit into a traceback:
        importing a name a module does not have raises ``ImportError``, which
        is already in the tuple.
        """

        task = getattr(application.state, "beat_prewarm_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        try:
            from hornlab_beat_bem import detach_workers
        except (ImportError, OSError):
            return
        await asyncio.to_thread(detach_workers)

    async def shutdown_bempp_worker() -> None:
        """Stop the prewarm and the worker with the app, not at interpreter exit.

        ``bempp_process`` registers an ``atexit`` hook, but a launcher killed
        with TerminateProcess never runs one, and the prewarm means a worker
        can be alive even for a session that never solved. Closing here bounds
        the wait at ``_JOIN_SECONDS`` and then terminates -- which is the
        property that lets the worker be warmed by default at all.
        """

        task = getattr(application.state, "bempp_prewarm_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        from server.solver.bempp_process import shutdown_bempp_process

        await asyncio.to_thread(shutdown_bempp_process)

    application.router.add_event_handler("startup", prewarm_bempp_worker)
    application.router.add_event_handler("startup", prewarm_beat_worker)
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
        # A quietened route that starts answering 4xx or 5xx is exactly what
        # someone reads this log to discover, so failure keeps INFO wherever it
        # came from; only the boring successes drop. 3xx counts as boring on
        # purpose -- the 304 a browser gets revalidating the shell document is
        # the same idle traffic wearing a different status code.
        quiet = (
            response.status_code < 400
            and (request.method, request.url.path) in QUIET_REQUEST_ROUTES
        )
        request_log.log(
            logging.DEBUG if quiet else logging.INFO,
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
            "build": BUILD,
            "commit": BUILD_IDENTITY["commit"],
            "uptime": max(0.0, time.monotonic() - started),
            "data_dir": str(resolved_data_dir),
        }

    @application.get("/api/capabilities")
    async def capabilities() -> dict[str, object]:
        # One definition, shared with the problem report
        # (``server/diagnostics/capabilities.py``). Two copies of this would
        # drift, and the copy that drifted would be the one in the bug report.
        return await capabilities_payload(engine_registry)

    @application.post("/api/design/symmetry")
    async def design_symmetry(design: DesignConfig) -> dict[str, object]:
        resolution = await asyncio.to_thread(resolve_symmetry, design)
        return resolution.as_dict()

    mount_solver_mesh(application)
    mount_preview(application, extra_ws_origins=extra_ws_origins)
    mount_design_io(application)
    mount_exports(application)
    mount_jobs(
        application,
        engine_registry,
        extra_ws_origins=extra_ws_origins,
    )
    mount_integration(application)
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
    workspace_state = mount_workspace(
        application,
        default_path=resolved_workspace_dir,
        legacy_defaults=legacy_workspace_dirs,
    )

    async def _repair_legacy_acls() -> None:
        # Files this application published before `publish_staging_directory`
        # carry a private, non-inheriting ACL, and become unreadable to the app
        # itself the first time their owner changes. See
        # `server/platform/acl_repair.py`. Off the loop: it is filesystem work,
        # and on a large workspace it is not instantaneous.
        try:
            workspace_root = workspace_state.path()
        except OSError:
            # An unavailable selected workspace is the workspace layer's
            # problem to report, not a reason to skip the data directory.
            workspace_root = None
        await asyncio.to_thread(
            repair_legacy_acls, resolved_data_dir, workspace_root
        )

    if os.name == "nt":
        application.router.add_event_handler("startup", _repair_legacy_acls)
    mount_cadlink(application)
    mount_onshape(application)
    mount_charts(application)
    settings_store = mount_settings(application)
    mount_drivers(application)
    mount_updates(
        application,
        running_version=VERSION,
        data_dir=resolved_data_dir,
        repo_root=APP_ROOT,
        update_request_path=(
            Path(update_request_path) if update_request_path is not None else None
        ),
        # The update channel is remembered here rather than in the browser
        # because it has to survive the update it controls.
        settings=settings_store,
    )
    # Last, because a problem report describes every store above it and reads
    # them off ``application.state`` rather than building a second copy.
    mount_diagnostics(
        application,
        version=VERSION,
        # ``label`` is the one string a user should ever be asked to quote:
        # ``build_label`` already renders version, short commit and dirtiness
        # into something safe to paste, and a bare version number identifies
        # hundreds of commits.
        build={**BUILD_IDENTITY, "label": BUILD},
        data_dir=resolved_data_dir,
    )
    # Job tasks stop first; only then may their shared gmsh owner be finalized.
    application.router.add_event_handler("shutdown", shutdown_mesher_prewarm)
    application.router.add_event_handler("shutdown", engine_registry.shutdown_prewarm)
    application.router.add_event_handler("shutdown", shutdown_gmsh_worker)
    application.router.add_event_handler("shutdown", shutdown_bempp_worker)
    application.router.add_event_handler("shutdown", shutdown_beat_worker)
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
