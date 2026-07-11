"""Network configuration helpers for the local FastAPI application."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)
MUTATING_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_backend_host() -> str:
    """Return the configured bind host, defaulting to loopback for desktop safety."""
    return str(os.getenv("MWG_BACKEND_HOST") or DEFAULT_BACKEND_HOST).strip() or DEFAULT_BACKEND_HOST


def get_backend_port() -> int:
    """Return the configured backend port."""
    raw = str(os.getenv("MWG_BACKEND_PORT") or DEFAULT_BACKEND_PORT).strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_BACKEND_PORT
    return port if 1 <= port <= 65535 else DEFAULT_BACKEND_PORT


def get_cors_origins() -> list[str]:
    """Return explicit browser origins allowed to call the local backend."""
    raw = str(os.getenv("MWG_CORS_ORIGINS") or "").strip()
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    return list(dict.fromkeys(origin.strip() for origin in raw.split(",") if origin.strip()))


def add_cors_middleware(app: FastAPI) -> None:
    """Restrict browser access to explicitly configured frontend origins."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin state-changing requests before they reach a route."""

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method in MUTATING_HTTP_METHODS
            and origin is not None
            and origin not in get_cors_origins()
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin is not allowed for this request."},
            )
        return await call_next(request)


def add_origin_guard_middleware(app: FastAPI) -> None:
    """Protect all mutating routes while preserving Origin-less local tooling."""
    app.add_middleware(OriginGuardMiddleware)
