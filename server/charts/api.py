"""Theme-aware PNG export backed exclusively by the pinned hornlab-plots package."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import hashlib
import math
import threading
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from server.contracts.canonical import canonical_ascii_json

from .directivity import render_directivity_heatmap_b64


# Matches the live chart palette in frontend/src/results/EChart.tsx; the two
# are a pair and must move together.
#
# The default is the interface's own dark theme, and the frontend resolves its
# "Match interface" setting to console or vellum before it gets here, so an
# export lands on the same surfaces as the window it was taken from.
DEFAULT_CHART_THEME = "console"
THEME_LABELS = {
    "classic": "Classic",
    "hornlab": "Arctic Night",
    "granite": "Light Paper",
    "abyss": "Dark Studio",
    "blueprint": "Blueprint",
    "journal": "Journal",
    "contrast": "High Contrast",
    "sepia": "Sepia",
    "phosphor": "CRT Green",
    "ember": "Warm Charcoal",
    "console": "Console (app dark)",
    "vellum": "Vellum (app light)",
}
THEME_NAMES = tuple(THEME_LABELS)


def _theme(value: str | None) -> str:
    name = str(value or DEFAULT_CHART_THEME).strip().lower() or DEFAULT_CHART_THEME
    if name not in THEME_LABELS:
        raise ValueError(f"theme must be one of: {', '.join(THEME_NAMES)}")
    return name


class ChartModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def reject_nested_nonfinite_numbers(cls, value: Any) -> Any:
        """Keep NaN/Infinity out of the free-form legacy chart mappings too."""

        stack = [value]
        seen: set[int] = set()
        while stack:
            item = stack.pop()
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("chart numeric values must be finite")
            if isinstance(item, Mapping):
                marker = id(item)
                if marker not in seen:
                    seen.add(marker)
                    stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                marker = id(item)
                if marker not in seen:
                    seen.add(marker)
                    stack.extend(item)
        return value


class ChartsReferencePayload(ChartModel):
    label: str | None = None
    frequencies: list[float] = Field(default_factory=list)
    spl: list[float | None] = Field(default_factory=list)
    sound_speed_m_per_s: float | None = None
    di: list[float | None] | dict[str, Any] = Field(default_factory=list)
    di_frequencies: list[float] = Field(default_factory=list)
    impedance_frequencies: list[float] = Field(default_factory=list)
    impedance_real: list[float | None] = Field(default_factory=list)
    impedance_imaginary: list[float | None] = Field(default_factory=list)
    impedance_units: str | None = None
    impedance_normalization: str | None = None
    beam_shape: dict[str, Any] | None = None


class ChartsRenderRequest(ChartModel):
    frequencies: list[float] = Field(default_factory=list)
    spl: list[float | None] = Field(default_factory=list)
    phase_degrees: list[float | None] = Field(default_factory=list)
    phase_reference_distance_m: float | None = None
    phase_time_convention: str | None = None
    sound_speed_m_per_s: float | None = None
    di: list[float | None] | dict[str, Any] = Field(default_factory=list)
    di_frequencies: list[float] = Field(default_factory=list)
    impedance_frequencies: list[float] = Field(default_factory=list)
    impedance_real: list[float | None] = Field(default_factory=list)
    impedance_imaginary: list[float | None] = Field(default_factory=list)
    impedance_units: str | None = None
    impedance_normalization: str | None = None
    directivity: dict[str, Any] = Field(default_factory=dict)
    beam_shape: dict[str, Any] | None = None
    reference: ChartsReferencePayload | None = None
    theme: str = DEFAULT_CHART_THEME

    @field_validator("theme", mode="before")
    @classmethod
    def validate_theme(cls, value: Any) -> str:
        return _theme(value)


class DirectivityRenderRequest(ChartModel):
    frequencies: list[float]
    directivity: dict[str, Any]
    reference_frequencies: list[float] | None = None
    reference_directivity: dict[str, Any] | None = None
    reference_label: str | None = None
    reference_level: float = -6.0
    display_range_db: tuple[float, float] = (-30.0, 0.0)
    # 0 means no graticule: ``directivity.py`` draws none for a step <= 0.
    angle_guide_step: float = Field(default=10.0, ge=0.0, le=180.0)
    theme: str = DEFAULT_CHART_THEME

    @field_validator("display_range_db")
    @classmethod
    def validate_display_range(cls, value: tuple[float, float]) -> tuple[float, float]:
        if value[0] >= value[1]:
            raise ValueError(
                "The directivity display range is reversed or empty. "
                "Set its lower dB level below its upper level."
            )
        return value

    @field_validator("theme", mode="before")
    @classmethod
    def validate_theme(cls, value: Any) -> str:
        return _theme(value)


router = APIRouter(tags=["chart-export"])
_render_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: OrderedDict[str, Any] = OrderedDict()
_CACHE_LIMIT = 32


def _plots() -> Any:
    try:
        import hornlab_plots
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-plots is unavailable; install the pinned server requirements"
        ) from exc
    return hornlab_plots


def _key(kind: str, payload: dict[str, Any]) -> str:
    encoded = canonical_ascii_json(payload).encode()
    return f"{kind}:{hashlib.sha256(encoded).hexdigest()}"


def _cached(key: str) -> Any | None:
    with _cache_lock:
        value = _cache.pop(key, None)
        if value is not None:
            _cache[key] = value
        return value


def _store(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = value
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)


def _render_charts(payload: dict[str, Any]) -> dict[str, str]:
    with _render_lock:
        rendered = _plots().render_all_charts_b64(payload, theme=payload["theme"])
    return {
        name: f"data:image/png;base64,{image}"
        for name, image in rendered.items()
        if image is not None
    }


def _render_directivity(payload: dict[str, Any]) -> str | None:
    kwargs: dict[str, Any] = {
        "reference_level": payload["reference_level"],
        "display_range_db": payload["display_range_db"],
        "angle_guide_step": payload["angle_guide_step"],
        "theme": payload["theme"],
    }
    if payload.get("reference_directivity") is not None:
        kwargs.update(
            reference_frequencies=payload.get("reference_frequencies"),
            reference_directivity=payload["reference_directivity"],
            reference_label=payload.get("reference_label"),
        )
    with _render_lock:
        return render_directivity_heatmap_b64(
            _plots(), payload["frequencies"], payload["directivity"], **kwargs
        )


@router.get("/api/themes")
async def themes() -> dict[str, Any]:
    return {
        "themes": [
            {"name": name, "label": label, "default": name == DEFAULT_CHART_THEME}
            for name, label in THEME_LABELS.items()
        ],
        "default": DEFAULT_CHART_THEME,
    }


@router.post("/api/render-charts")
async def render_charts(request: ChartsRenderRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    key = _key("charts", payload)
    cached = _cached(key)
    if cached is not None:
        return {"charts": cached}
    try:
        result = await asyncio.to_thread(_render_charts, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _store(key, result)
    return {"charts": result}


@router.post("/api/render-directivity")
async def render_directivity(request: DirectivityRenderRequest) -> dict[str, str]:
    if not request.frequencies or not request.directivity:
        raise HTTPException(status_code=422, detail="This run has no directivity to draw. Turn on a polar plane under Directivity map, then solve again.")
    payload = request.model_dump(mode="json")
    key = _key("directivity", payload)
    cached = _cached(key)
    if cached is not None:
        return {"image": cached}
    try:
        rendered = await asyncio.to_thread(_render_directivity, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if rendered is None:
        raise HTTPException(status_code=400, detail="This run has no directivity to draw. Turn on a polar plane under Directivity map, then solve again.")
    image = f"data:image/png;base64,{rendered}"
    _store(key, image)
    return {"image": image}


def mount_charts(application: FastAPI) -> None:
    application.include_router(router)


__all__ = [
    "ChartsReferencePayload",
    "ChartsRenderRequest",
    "DEFAULT_CHART_THEME",
    "DirectivityRenderRequest",
    "THEME_NAMES",
    "mount_charts",
    "router",
]
