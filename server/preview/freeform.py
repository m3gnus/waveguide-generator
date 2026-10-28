"""Mesh-free edits and comparisons of the mesher's resolved FREEFORM curves."""

from __future__ import annotations

from bisect import bisect_left
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import Field

from server.design.schema import DesignConfig, FreeformConfig, FreeformPoint, StrictModel
from server.preview.translate import design_to_mesher_config


class FreeformError(StrictModel):
    content_key: str | None
    detail: str | list[dict[str, Any]]


class _ContentKeyRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def with_content_key(request):
            try:
                return await handler(request)
            except (HTTPException, RequestValidationError) as exc:
                try:
                    payload = await request.json()
                except ValueError:
                    payload = None
                key = payload.get("content_key") if isinstance(payload, dict) else None
                if not isinstance(key, str):
                    key = None
                # Input may contain non-finite numbers, and need not be echoed
                # in a validation error (nor repeated as a whole design).
                detail = exc.detail if isinstance(exc, HTTPException) else jsonable_encoder([
                    {name: value for name, value in error.items() if name not in {"input", "ctx"}}
                    for error in exc.errors()
                ])
                status = exc.status_code if isinstance(exc, HTTPException) else 422
                return JSONResponse(status_code=status, content={"content_key": key, "detail": detail})

        return with_content_key


class CurvesRequest(StrictModel):
    design: DesignConfig
    content_key: str


class InsertRequest(CurvesRequest):
    plane: Literal["H", "V"]
    z_mm: float = Field(strict=True, allow_inf_nan=False)
    mode: Literal["exact", "refit"] = "exact"


class CompareRequest(StrictModel):
    before: DesignConfig
    after: DesignConfig
    content_key: str


class CurveAnchor(StrictModel):
    t: float
    z: float
    r: float
    join: Literal["smooth", "corner"]
    explicitIn: bool
    explicitOut: bool
    handleIn: tuple[float, float] | None
    handleOut: tuple[float, float] | None
    angleInDeg: float | None
    angleOutDeg: float | None


class CurveSpan(StrictModel):
    straight: bool
    bezier: list[tuple[float, float]]


class Inflection(StrictModel):
    zStartMm: float
    zEndMm: float
    tangentDropDeg: float


class PlaneCurve(StrictModel):
    anchors: list[CurveAnchor]
    spans: list[CurveSpan]
    samples: list[tuple[float, float]]
    inflections: list[Inflection]


class CurvesResponse(StrictModel):
    content_key: str
    H: PlaneCurve
    V: PlaneCurve
    warnings: list[str]


class ComparisonExtents(StrictModel):
    before: tuple[float, float]
    after: tuple[float, float]
    extents_equal: bool
    overlap: tuple[float, float] | None
    comparison_domain: Literal["full", "overlap_only", "no_overlap"]


class ComparisonMethod(StrictModel):
    estimate: str
    uniform_samples: int
    grid: str
    peak_refinement: str


class Deviation(StrictModel):
    extents: ComparisonExtents
    method: ComparisonMethod
    sample_count: int
    curve_deviation_mm: float | None
    curve_at_z_mm: float | None
    evaluator_deviation_mm: float | None
    evaluator_at_z_mm: float | None


class CompareResponse(StrictModel):
    content_key: str
    H: Deviation
    V: Deviation


class InsertResponse(StrictModel):
    content_key: str
    design: DesignConfig
    deviation: Deviation


def _root(design: DesignConfig) -> FreeformConfig:
    if not isinstance(design.root, FreeformConfig):
        raise ValueError("FREEFORM curve operations require a FREEFORM design")
    return design.root


def _params(design: DesignConfig) -> dict:
    root = _root(design)
    # Editing coordinates and handle vectors are intrinsic design mm. The
    # build translator scales them; this API deliberately queries at scale 1.
    unscaled = DesignConfig(root=root.model_copy(update={"scale": None}))
    return design_to_mesher_config(unscaled)["profile"]


def _curves(design: DesignConfig) -> dict:
    from hornlab_mesher import freeform_profile_curves

    result = freeform_profile_curves(_params(design))
    length = float(_root(design).length.constant_value())
    for plane in ("H", "V"):
        for anchor in result[plane]["anchors"]:
            anchor["t"] = anchor["z"] / length
    return result


def _compare(before: DesignConfig, after: DesignConfig) -> dict[str, Deviation]:
    from hornlab_mesher import freeform_compare_profiles

    comparison = freeform_compare_profiles(_params(before), _params(after))
    result = {}
    for plane in ("H", "V"):
        # The mesher owns the sampling and extrema. The grid itself can exceed
        # half a megabyte and is not needed to present a comparison.
        summary = dict(comparison[plane])
        summary["sample_count"] = len(summary.pop("samples"))
        result[plane] = Deviation.model_validate(summary)
    return result


def curves(request: CurvesRequest) -> CurvesResponse:
    try:
        return CurvesResponse(content_key=request.content_key, **_curves(request.design))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def compare(request: CompareRequest) -> CompareResponse:
    try:
        return CompareResponse(content_key=request.content_key, **_compare(request.before, request.after))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def insert(request: InsertRequest) -> InsertResponse:
    from hornlab_mesher import freeform_evaluate_profile, freeform_insert_anchor

    plane = request.plane
    try:
        root = _root(request.design)
        if root.shared_profile:
            plane = "H"
        before = _curves(request.design)
        field = "profile_h" if plane == "H" else "profile_v"
        profile = getattr(root, field)
        length = float(root.length.constant_value())
        z = request.z_mm
        if not 0 < z < length or any(z == a["z"] for a in before[plane]["anchors"]):
            raise ValueError(f"FREEFORM profile{plane} insert z must be strictly inside a span, not on an anchor")
        if len(profile.points) >= 64:
            raise ValueError(f"FREEFORM profile{plane} insertion would exceed 64 anchors")
        params = _params(request.design)
        index = bisect_left([a["z"] for a in before[plane]["anchors"]], z)
        if request.mode == "exact":
            try:
                resolved = freeform_insert_anchor(params[f"profile{plane}"], z=z,
                    default_throat_angle_deg=params.get("a0", 15.5))
            except ValueError as exc:
                raise ValueError(str(exc).replace("profileH", f"profile{plane}")) from exc
            points = []
            for row_index, row in enumerate(resolved["points"]):
                if row_index == index:
                    values = {"t": z / length, "r": row["r"], "join": row.get("join")}
                else:
                    old_index = row_index if row_index < index else row_index - 1
                    values = profile.points[old_index].model_dump(mode="json")
                for handle_field, key in (("handle_in", "handleIn"), ("handle_out", "handleOut")):
                    if key in row:
                        values[handle_field] = row[key]
                points.append(FreeformPoint.model_validate(values))
        else:
            radius = float(freeform_evaluate_profile(params, plane, z))
            # Refit means a fresh automatic fit for the whole edited plane.
            points = [point.model_copy(update={"handle_in": None, "handle_out": None})
                      for point in profile.points]
            points.insert(index, FreeformPoint(t=z / length, r=radius))
        # Validate the entire returned design and both resolved curves, keeping
        # the inactive V and every unrelated authored setting intact.
        payload = request.design.model_dump(mode="json")
        payload[field] = profile.model_copy(update={"points": points}).model_dump(mode="json")
        updated = DesignConfig.model_validate(payload)
        deviation = _compare(request.design, updated)[plane]
        return InsertResponse(content_key=request.content_key, design=updated, deviation=deviation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def mount_freeform(application: FastAPI) -> None:
    router = APIRouter(prefix="/api/design/freeform", tags=["freeform"],
        route_class=_ContentKeyRoute, responses={422: {"model": FreeformError}})
    router.add_api_route("/curves", curves, methods=["POST"], response_model=CurvesResponse)
    router.add_api_route("/insert", insert, methods=["POST"], response_model=InsertResponse)
    router.add_api_route("/compare", compare, methods=["POST"], response_model=CompareResponse)
    application.include_router(router)
