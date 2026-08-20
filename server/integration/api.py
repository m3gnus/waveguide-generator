"""Read-only, versioned discovery endpoints for external WG clients."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from server.design.schema import DesignConfig


_CATALOG_PATH = Path(__file__).with_name("parameter-catalog.v1.json")
router = APIRouter()


class _DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogValidationAuthority(_DiscoveryModel):
    request_schema: str
    design_schema: str
    validate_cli: str
    note: str


class CatalogCondition(_DiscoveryModel):
    path: str | None = None
    operator: str
    value: JsonValue | None = None
    conditions: list["CatalogCondition"] = Field(default_factory=list)


class CatalogEditorBounds(_DiscoveryModel):
    minimum: float
    maximum: float


class CatalogOption(_DiscoveryModel):
    value: JsonValue
    label: str
    requires_feature: str | None
    degraded_without: str | None
    degraded_label: str | None


class ParameterDescriptor(_DiscoveryModel):
    id: str
    legacy_key: str
    path: str | None
    mirror_paths: list[str]
    label: str
    section: str
    symbol: str | None
    kind: Literal["number", "select", "indicator", "table", "text"]
    unit: str | None
    families: list[Literal["R-OSSE", "OSSE", "ICW", "FREEFORM"]]
    accepts_expression: bool
    writable: bool
    default_by_family: dict[str, JsonValue]
    editor_bounds: CatalogEditorBounds | None
    step: float | None
    precision: int | None
    options: list[CatalogOption]
    description: str
    visible_when: CatalogCondition | None = None
    disabled_when: CatalogCondition | None = None
    disabled_reason: str | None = None


class ParameterCatalog(_DiscoveryModel):
    schema_version: Literal[1]
    catalog_version: Literal[1]
    design_families: list[Literal["R-OSSE", "OSSE", "ICW", "FREEFORM"]]
    validation_authority: CatalogValidationAuthority
    parameters: list[ParameterDescriptor]


class JsonSchemaReference(_DiscoveryModel):
    ref: str = Field(alias="$ref")


class JsonSchemaDiscriminator(_DiscoveryModel):
    property_name: str = Field(alias="propertyName")
    mapping: dict[str, str]


class DesignSchemaDocument(_DiscoveryModel):
    """Discoverable root of WG's generated JSON Schema document."""

    dialect: Literal["https://json-schema.org/draft/2020-12/schema"] = Field(
        alias="$schema"
    )
    definitions: dict[str, dict[str, Any]] = Field(alias="$defs")
    wg_schema_version: Literal[1] = Field(alias="x-wg-schema-version")
    description: str
    discriminator: JsonSchemaDiscriminator
    one_of: list[JsonSchemaReference] = Field(alias="oneOf")
    title: str


class _JsonSchemaResponse(JSONResponse):
    media_type = "application/schema+json"


@lru_cache(maxsize=1)
def parameter_catalog_text() -> str:
    return _CATALOG_PATH.read_text(encoding="utf-8")


def _json_response(content: str, *, media_type: str = "application/json") -> Response:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "ETag": f'"sha256:{digest}"',
        },
    )


@router.get("/api/integration/v1/parameters", response_model=ParameterCatalog)
async def parameter_catalog() -> Response:
    return _json_response(parameter_catalog_text())


@lru_cache(maxsize=1)
def design_schema_text() -> str:
    payload: dict[str, Any] = DesignConfig.model_json_schema()
    payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    payload["x-wg-schema-version"] = 1
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


@router.get(
    "/api/integration/v1/design-schema",
    response_model=DesignSchemaDocument,
    response_class=_JsonSchemaResponse,
)
async def design_schema() -> Response:
    return _json_response(
        design_schema_text(),
        media_type="application/schema+json",
    )


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
