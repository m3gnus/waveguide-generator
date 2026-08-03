"""FastAPI assembly for the Waveguide Generator v2 shell."""

from __future__ import annotations

from dataclasses import asdict
import ipaddress
import logging
from pathlib import Path
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.engines.registry import detect_engines
from server.platform.paths import resolve_data_dir


VERSION = "2.0.0"
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
request_log = logging.getLogger("wg2.requests")


def _local_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_app(*, data_dir: str | Path | None = None) -> FastAPI:
    """Assemble an app instance without creating persistent directories."""

    started = time.monotonic()
    resolved_data_dir = resolve_data_dir(data_dir)
    application = FastAPI(title="Waveguide Generator v2", version=VERSION)
    application.state.started = started
    application.state.data_dir = resolved_data_dir

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
        return {"engines": [asdict(engine) for engine in detect_engines()]}

    application.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return application


app = create_app()
