"""Local HTTP guards and request logging without consuming the receive channel."""

from __future__ import annotations

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from server.integration.contracts import error_envelope
from server.platform.origin import local_request_host, request_origin_allowed
from server.updates.restart import UPDATE_RESTART_PENDING


request_log = logging.getLogger("wg.requests")
#: ``(method, path)`` pairs whose successful responses log at DEBUG.
#:
#: uvicorn's own access log is off (``launch/serve.py``), so ``RequestMiddleware``
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


#: Routes refused while an update restart is approved (contract §4.2) that are
#: not in a router with the latch of its own. ``POST /api/cadlink/ingest`` starts
#: work that outlives its request -- a deferred viewport, and a capture of the
#: CAD document that copies tens of megabytes -- and it is refused before its
#: handler runs, so the return stays on disk exactly as it was.
RESTART_GATED_POSTS = frozenset({"/api/cadlink/ingest", "/api/source-editor/export"})


class RequestMiddleware:
    """Preserve restart -> logging -> origin order; WS guards stay in routers."""

    def __init__(self, app: ASGIApp, *, extra_origins: frozenset[str]) -> None:
        self.app = app
        self.extra_origins = extra_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        # The solve and retry routes refuse in their own router; see
        # ``RESTART_GATED_POSTS`` for the rest.
        if request.method == "POST" and request.url.path in RESTART_GATED_POSTS:
            approval = getattr(request.app.state, "update_restart", None)
            refusal = approval.refusal() if approval is not None else None
            if refusal is None:
                refusal = request.app.state.retirement_hold.refusal()
            if refusal is not None:
                response = JSONResponse(
                    status_code=409,
                    content=error_envelope(
                        code=UPDATE_RESTART_PENDING,
                        stage="submission",
                        message=refusal,
                        retryable=True,
                    ),
                )
                await response(scope, receive, send)
                return

        began = time.monotonic()
        response_started = False

        async def logged_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                # Only boring successes drop to DEBUG; failures stay visible.
                quiet = status < 400 and (request.method, request.url.path) in QUIET_REQUEST_ROUTES
                request_log.log(
                    logging.DEBUG if quiet else logging.INFO,
                    "%s %s -> %d in %.1f ms",
                    request.method,
                    request.url.path,
                    status,
                    (time.monotonic() - began) * 1000,
                )
            await send(message)

        try:
            response = self._origin_rejection(request)
            if response is not None:
                await response(scope, receive, logged_send)
            else:
                await self.app(scope, receive, logged_send)
        except Exception:
            if not response_started:
                request_log.exception(
                    "%s %s failed after %.1f ms",
                    request.method,
                    request.url.path,
                    (time.monotonic() - began) * 1000,
                )
            raise

    def _origin_rejection(self, request: Request) -> JSONResponse | None:
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
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": "Non-local Host rejected. Open Waveguide Generator "
                    "from the loopback URL printed by the launcher."
                },
            )
            return response
        origin = request.headers.get("origin")
        if not request_origin_allowed(
            origin=origin,
            host=host,
            scheme=request.url.scheme,
            bound_port=bound_port,
            extra_origins=self.extra_origins,
        ):
            request_log.warning(
                "Rejected %s %s from disallowed Origin %r; use the local application URL",
                request.method,
                request.url.path,
                origin,
            )
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": "Non-local Origin or unapproved cross-origin request "
                    "rejected. Open Waveguide Generator from its current loopback URL."
                },
            )
            return response
        return None

