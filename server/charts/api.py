"""Theme-aware PNG export backed exclusively by the pinned hornlab-plots package."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from concurrent.futures import Future
import hashlib
import io
import json
import math
import threading
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    angle_guide_step: float = Field(default=10.0, gt=0.0, le=180.0)
    theme: str = DEFAULT_CHART_THEME

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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
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


def _claim_preview(key: str) -> tuple[str | Future[str], bool]:
    """Return a cached preview or atomically claim responsibility for rendering it."""

    with _cache_lock:
        value = _cache.pop(key, None)
        if value is not None:
            _cache[key] = value
            return value, False
        future: Future[str] = Future()
        _cache[key] = future
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
        return future, True


def _discard_pending_preview(key: str, pending: Future[str]) -> None:
    with _cache_lock:
        if _cache.get(key) is pending:
            del _cache[key]


def _render_charts_unlocked(payload: dict[str, Any]) -> dict[str, str]:
    rendered = _plots().render_all_charts_b64(payload, theme=payload["theme"])
    return {
        name: f"data:image/png;base64,{image}"
        for name, image in rendered.items()
        if image is not None
    }


def _render_charts(payload: dict[str, Any]) -> dict[str, str]:
    with _render_lock:
        return _render_charts_unlocked(payload)


def _render_directivity(payload: dict[str, Any]) -> str | None:
    kwargs: dict[str, Any] = {
        "reference_level": payload["reference_level"],
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


def _preview(theme: str) -> str:
    # hornlab-plots applies themes through matplotlib.rc_context, which mutates
    # process-global rcParams. Keep its panel renders and our pyplot composition
    # in one critical section so another theme cannot leak into either phase.
    with _render_lock:
        frequencies = [100.0, 300.0, 1000.0, 3000.0, 10_000.0]
        angles = [-90.0, -45.0, 0.0, 45.0, 90.0]
        directivity = {
            "horizontal": [
                [
                    [
                        angle,
                        -12.0
                        * (abs(angle) / max(20.0, 80.0 - index * 10.0)) ** 1.8,
                    ]
                    for angle in angles
                ]
                for index in range(len(frequencies))
            ]
        }
        payload = {
            "frequencies": frequencies,
            "spl": [91.0, 94.0, 96.0, 95.0, 92.0],
            "di": [3.0, 4.0, 6.0, 9.0, 13.0],
            "di_frequencies": frequencies,
            "impedance_frequencies": frequencies,
            "impedance_real": [1.2, 1.1, 1.0, 1.05, 1.15],
            "impedance_imaginary": [-0.3, -0.1, 0.0, 0.1, 0.25],
            "directivity": directivity,
            "theme": theme,
        }
        rendered = _render_charts_unlocked(payload)
        images = [value.split(",", 1)[1] for value in rendered.values()][:4]
        if not images:
            raise RuntimeError("hornlab-plots produced no theme-preview panels")
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plots = _plots()
        theme_object = plots.get_theme(theme)
        figure, axes = plt.subplots(2, 2, figsize=(12, 7))
        try:
            figure.patch.set_facecolor(theme_object.figure_bg)
            for axis, encoded in zip(axes.ravel(), images):
                axis.axis("off")
                axis.imshow(
                    plt.imread(io.BytesIO(base64.b64decode(encoded)), format="png")
                )
            for axis in axes.ravel()[len(images) :]:
                axis.axis("off")
            figure.tight_layout()
            output = io.BytesIO()
            figure.savefig(
                output, format="png", dpi=100, facecolor=figure.get_facecolor()
            )
        finally:
            plt.close(figure)
        return base64.b64encode(output.getvalue()).decode("ascii")


@router.get("/api/themes")
async def themes() -> dict[str, Any]:
    return {
        "themes": [
            {"name": name, "label": label, "default": name == DEFAULT_CHART_THEME}
            for name, label in THEME_LABELS.items()
        ],
        "default": DEFAULT_CHART_THEME,
    }


@router.get("/api/theme-preview")
async def theme_preview(theme: str | None = Query(default=None)) -> dict[str, str]:
    try:
        name = _theme(theme)
        key = f"preview:{name}"
        entry, claimed = _claim_preview(key)
        if claimed:
            assert isinstance(entry, Future)
            try:
                image = await asyncio.to_thread(_preview, name)
            except BaseException as exc:
                entry.set_exception(exc)
                _discard_pending_preview(key, entry)
                raise
            entry.set_result(image)
            _store(key, image)
        elif isinstance(entry, Future):
            image = await asyncio.wrap_future(entry)
        else:
            image = entry
        return {"theme": name, "image": f"data:image/png;base64,{image}"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        raise HTTPException(status_code=422, detail="Missing frequencies or directivity data")
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
        raise HTTPException(status_code=400, detail="No directivity patterns to render")
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
