"""Read-only, versioned discovery endpoints for external WG clients."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import Response

from server.design.schema import DesignConfig


_CATALOG_PATH = Path(__file__).with_name("parameter-catalog.v1.json")
router = APIRouter()


@lru_cache(maxsize=1)
def parameter_catalog_text() -> str:
    return _CATALOG_PATH.read_text(encoding="utf-8")


def _json_response(content: str) -> Response:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Cache-Control": "public, max-age=3600",
            "ETag": f'"sha256:{digest}"',
        },
    )


@router.get("/api/integration/v1/parameters", response_model=None)
async def parameter_catalog() -> Response:
    return _json_response(parameter_catalog_text())


@lru_cache(maxsize=1)
def design_schema_text() -> str:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "schema": DesignConfig.model_json_schema(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


@router.get("/api/integration/v1/design-schema", response_model=None)
async def design_schema() -> Response:
    return _json_response(design_schema_text())


def mount_integration(application: FastAPI) -> None:
    application.include_router(router)


__all__ = [
    "design_schema",
    "design_schema_text",
    "mount_integration",
    "parameter_catalog",
    "parameter_catalog_text",
    "router",
]
