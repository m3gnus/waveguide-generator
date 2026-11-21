"""Chart images, rendered only by the pinned hornlab-plots package.

Every saved or exported chart image the application makes comes through here:
the per-chart PNG (copy and export on a result card, the balloon and beam map
included), the ``png`` export format, and the charts embedded in the HTML run
report. ``/api/render-chart`` draws a chart described as data;
``/api/render-directivity`` draws a directivity map on the canonical heatmap. The screen keeps its
own interactive charts; this module is the one place their images are drawn.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import hashlib
import math
import threading
from collections import OrderedDict
import logging
from typing import Annotated, Any, Literal, Union

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from server.contracts.canonical import canonical_ascii_json

from .directivity import render_directivity_heatmap_b64


# Exported images default to the classic HornLab report look (white page,
# near-black ink). The export theme preference can still choose any theme here;
# its "Match interface" setting resolves to console or vellum in the frontend,
# whose live charts mirror those two palettes (frontend/src/results/EChart.tsx).
DEFAULT_CHART_THEME = "classic"
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


# Bounds on what one chart may ask hornlab-plots to draw. A real chart is far
# inside them (a solve has a few hundred frequencies, a card a few dozen
# curves); a request outside them is a program fault, refused before it can
# hold the render lock that every chart export shares.
MAX_SPEC_SERIES = 64
MAX_SPEC_POINTS = 20_000
MAX_SPEC_TOTAL_POINTS = 200_000
MAX_SPEC_BANDS = 64
MAX_SPEC_HLINES = 16
MAX_SPEC_SHADE = 8
MAX_POLAR_RINGS = 60
MAX_FREQUENCY_HZ = 1.0e6
MAX_LOG_DECADES = 8
MAX_SPEC_TEXT = 200
MAX_SPEC_TEXT_SENT = 10_000


def _shortened(text: str) -> str:
    """A long title or label is shortened, not refused: the screen shows it fine."""
    return text if len(text) <= MAX_SPEC_TEXT else f"{text[:MAX_SPEC_TEXT - 1].rstrip()}…"


# Up to MAX_SPEC_TEXT_SENT characters are accepted (a hard bound against
# abuse) and drawn as at most MAX_SPEC_TEXT.
_Text = Annotated[str, Field(max_length=MAX_SPEC_TEXT_SENT), AfterValidator(_shortened)]
_Value = Annotated[float, Field(ge=-1.0e9, le=1.0e9)]
_Values = Annotated[list[_Value | None], Field(max_length=MAX_SPEC_POINTS)]
_ColorRole = Annotated[int, Field(ge=0, le=63)] | Literal["accent", "ink", "muted"]


def _ordered(low: float | None, high: float | None, what: str) -> None:
    if low is not None and high is not None and not high > low:
        raise ValueError(f"the {what} range is reversed or empty; set its maximum above its minimum")


class ChartSpecAxis(ChartModel):
    label: _Text = ""
    scale: Literal["log", "linear"] = "linear"
    min: _Value | None = None
    max: _Value | None = None

    @model_validator(mode="after")
    def sane_range(self) -> "ChartSpecAxis":
        _ordered(self.min, self.max, "axis")
        if self.scale == "log":
            if self.min is not None and not 0.0 < self.min:
                raise ValueError("a log frequency axis must start above 0 Hz")
            if self.max is not None and self.max > MAX_FREQUENCY_HZ:
                raise ValueError(f"a log frequency axis must end at or below {MAX_FREQUENCY_HZ:g} Hz")
            if self.min is not None and self.max is not None and math.log10(self.max / self.min) > MAX_LOG_DECADES:
                raise ValueError(f"a log frequency axis may span at most {MAX_LOG_DECADES} decades")
        return self


class ChartSpecLineStyle(ChartModel):
    color: _ColorRole = 0
    style: Literal["solid", "dashed", "dotted"] = "solid"
    width: Annotated[float, Field(gt=0.0, le=20.0)] = 2.0
    opacity: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0


class ChartSpecLineSeries(ChartSpecLineStyle):
    label: _Text | None = None
    x: _Values
    y: _Values
    axis: Literal[0, 1] = 0
    z: Annotated[float, Field(ge=-10.0, le=20.0)] | None = None
    markers: Annotated[list[Annotated[int, Field(ge=0, lt=MAX_SPEC_POINTS)]], Field(max_length=MAX_SPEC_POINTS)] | None = None


class ChartSpecBand(ChartModel):
    x0: _Value
    x1: _Value
    label: _Text | None = None


class ChartSpecHLine(ChartModel):
    y: _Value
    axis: Literal[0, 1] = 0


class LineChartSpec(ChartModel):
    kind: Literal["lines"]
    title: _Text = ""
    subtitle: _Text | None = None
    x: ChartSpecAxis = Field(default_factory=lambda: ChartSpecAxis(scale="log"))
    y: ChartSpecAxis = Field(default_factory=ChartSpecAxis)
    y2: ChartSpecAxis | None = None
    series: Annotated[list[ChartSpecLineSeries], Field(max_length=MAX_SPEC_SERIES)]
    bands: Annotated[list[ChartSpecBand], Field(max_length=MAX_SPEC_BANDS)] | None = None
    hlines: Annotated[list[ChartSpecHLine], Field(max_length=MAX_SPEC_HLINES)] | None = None

    @model_validator(mode="after")
    def bounded_points(self) -> "LineChartSpec":
        if sum(len(series.x) for series in self.series) > MAX_SPEC_TOTAL_POINTS:
            raise ValueError(f"a chart may draw at most {MAX_SPEC_TOTAL_POINTS} points")
        if self.x.scale == "log" and any(
            value is not None and not 0.0 < value <= MAX_FREQUENCY_HZ
            for series in self.series for value in series.x
        ):
            raise ValueError(f"log frequency samples must lie above 0 and at or below {MAX_FREQUENCY_HZ:g} Hz")
        return self


class ChartSpecRadius(ChartModel):
    min: Annotated[float, Field(ge=-1000.0, le=1000.0)]
    max: Annotated[float, Field(ge=-1000.0, le=1000.0)]
    step: Annotated[float, Field(gt=0.0)] | None = None
    unit: _Text = "dB"

    @model_validator(mode="after")
    def bounded_rings(self) -> "ChartSpecRadius":
        _ordered(self.min, self.max, "polar radius")
        if self.step is not None and (self.max - self.min) / self.step > MAX_POLAR_RINGS:
            raise ValueError(f"the polar ring step may give at most {MAX_POLAR_RINGS} rings")
        return self


class ChartSpecPolarSeries(ChartSpecLineStyle):
    label: _Text | None = None
    angle: Annotated[list[Annotated[float, Field(ge=-360.0, le=360.0)] | None], Field(max_length=MAX_SPEC_POINTS)]
    r: _Values


class ChartSpecShade(ChartModel):
    from_: Annotated[float, Field(ge=-360.0, le=360.0, alias="from")]
    to: Annotated[float, Field(ge=-360.0, le=360.0)]
    label: _Text | None = None

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)


class PolarChartSpec(ChartModel):
    kind: Literal["polar"]
    title: _Text = ""
    subtitle: _Text | None = None
    r: ChartSpecRadius
    series: Annotated[list[ChartSpecPolarSeries], Field(max_length=MAX_SPEC_SERIES)]
    shade: Annotated[list[ChartSpecShade], Field(max_length=MAX_SPEC_SHADE)] | None = None


MAX_GRID_THETA = 361
MAX_GRID_PHI = 721


class SphericalChartSpec(ChartModel):
    """One frequency of a solved balloon grid: the forward beam map or the 3D balloon."""

    kind: Literal["beam_map", "balloon"]
    title: _Text = ""
    subtitle: _Text | None = None
    theta_deg: Annotated[list[Annotated[float, Field(ge=0.0, le=180.0)]], Field(min_length=2, max_length=MAX_GRID_THETA)]
    phi_deg: Annotated[list[Annotated[float, Field(ge=-360.0, le=720.0)]], Field(min_length=3, max_length=MAX_GRID_PHI)]
    levels_db: Annotated[list[Annotated[list[_Value | None], Field(max_length=MAX_GRID_PHI)]], Field(max_length=MAX_GRID_THETA)]
    range_db: Annotated[float, Field(gt=0.0, le=120.0)] = 30.0

    @model_validator(mode="after")
    def grid_shape(self) -> "SphericalChartSpec":
        if len(self.levels_db) != len(self.theta_deg) or any(len(row) != len(self.phi_deg) for row in self.levels_db):
            raise ValueError("levels_db must have one row per theta angle and one value per phi angle")
        if len(self.theta_deg) * len(self.phi_deg) > MAX_SPEC_TOTAL_POINTS:
            raise ValueError(f"a spherical grid may have at most {MAX_SPEC_TOTAL_POINTS} samples")
        return self


class ChartSpecRenderRequest(ChartModel):
    """One chart described as data; hornlab_plots.spec documents the shape.

    Typed and bounded here so a malformed or oversized spec is a 422 naming
    the field, never a renderer exception or minutes under the render lock.
    """

    spec: Annotated[Union[LineChartSpec, PolarChartSpec, SphericalChartSpec], Field(discriminator="kind")]
    theme: str = DEFAULT_CHART_THEME

    @field_validator("theme", mode="before")
    @classmethod
    def validate_theme(cls, value: Any) -> str:
        return _theme(value)


router = APIRouter(tags=["chart-export"])
_log = logging.getLogger(__name__)
CHART_SPEC_RENDER_FAILED = "WG could not draw this chart image. Try again; if it keeps failing, send a problem report."
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


def _render_chart_spec(payload: dict[str, Any]) -> str | None:
    with _render_lock:
        return _plots().chart_spec_b64(payload["spec"], theme=payload["theme"])


@router.get("/api/themes")
async def themes() -> dict[str, Any]:
    return {
        "themes": [
            {"name": name, "label": label, "default": name == DEFAULT_CHART_THEME}
            for name, label in THEME_LABELS.items()
        ],
        "default": DEFAULT_CHART_THEME,
    }


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


@router.post("/api/render-chart")
async def render_chart(request: ChartSpecRenderRequest) -> dict[str, str]:
    # by_alias: the shaded sector's start is "from" on the wire and to the
    # renderer; exclude_none: an absent option is the renderer's default.
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    key = _key("spec", payload)
    cached = _cached(key)
    if cached is not None:
        return {"image": cached}
    try:
        rendered = await asyncio.to_thread(_render_chart_spec, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        # The request validated, so this is the renderer refusing the spec: a
        # program fault. The user gets a fixed sentence; the log keeps why.
        _log.warning("hornlab-plots refused a chart spec: %s", exc, exc_info=True)
        raise HTTPException(status_code=422, detail=CHART_SPEC_RENDER_FAILED) from exc
    if rendered is None:
        raise HTTPException(status_code=422, detail="This chart has no values to draw. Choose a chart with data, then export again.")
    image = f"data:image/png;base64,{rendered}"
    _store(key, image)
    return {"image": image}


def mount_charts(application: FastAPI) -> None:
    application.include_router(router)


__all__ = [
    "ChartSpecRenderRequest",
    "LineChartSpec",
    "PolarChartSpec",
    "SphericalChartSpec",
    "DEFAULT_CHART_THEME",
    "DirectivityRenderRequest",
    "THEME_NAMES",
    "mount_charts",
    "router",
]
