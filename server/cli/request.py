"""Build headless solve requests with one shared settings precedence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.design.solve_block import has_solve_blocks, solve_options_from_blocks
from server.design.textcfg import ParsedDesign
from server.jobs.models import SolveOptions, SolveRequest


class OverlayDocument(BaseModel):
    """Versioned, intentionally partial solve-options overlay."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(alias="schemaVersion")
    options: dict[str, Any]

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("schemaVersion must be the integer 1")
        return value


@dataclass(frozen=True, slots=True)
class RequestBuild:
    request: SolveRequest
    settings_source: str


def _overlay_options(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read overlay file: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"overlay is not valid JSON at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        ) from exc
    return OverlayDocument.model_validate(value).options


def _apply_overlay(options: SolveOptions, values: dict[str, Any]) -> SolveOptions:
    merged = options.model_dump(mode="json")
    for key, value in values.items():
        if key == "polar_config" and isinstance(value, dict):
            merged[key] = {**merged["polar_config"], **value}
        else:
            merged[key] = value
    return SolveOptions.model_validate(merged)


def build_request(
    parsed: ParsedDesign,
    *,
    overlay: Path | None = None,
    engine: str | None = None,
) -> RequestBuild:
    """Resolve file blocks, overlay, and engine flag in authoritative order."""

    file_settings = has_solve_blocks(parsed.extra_blocks)
    options = solve_options_from_blocks(parsed.extra_blocks)
    if overlay is not None:
        options = _apply_overlay(options, _overlay_options(overlay))
    if engine is not None:
        values = options.model_dump(mode="json")
        values["engine"] = engine
        options = SolveOptions.model_validate(values)

    if overlay is not None:
        settings_source = "file+overlay" if file_settings else "defaults+overlay"
    else:
        settings_source = "file" if file_settings else "defaults"
    request = SolveRequest.model_validate(
        {
            "design": parsed.semantic_data(),
            "options": options.model_dump(mode="json"),
        }
    )
    return RequestBuild(request=request, settings_source=settings_source)


__all__ = ["OverlayDocument", "RequestBuild", "build_request"]
