"""Typed, lossless design models for Waveguide Generator configurations.

The field inventory mirrors the v1 writer in ``src/export/mwgConfig.js:46-280``,
the parser aliases in ``src/config/index.js:25-76,358-486``, and the solver
adapter payload in ``server/solver/mesher_adapter.py:190-424``.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)


_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_SAFE_FUNCTIONS = {
    "abs": abs,
    "acos": math.acos,
    "asin": math.asin,
    "atan": math.atan,
    "ceil": math.ceil,
    "cos": math.cos,
    "exp": math.exp,
    "floor": math.floor,
    "log": math.log,
    "max": max,
    "min": min,
    "pow": pow,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}


def _constant_expression_value(raw: str) -> float | None:
    """Evaluate scalar-only ATH syntax without accepting arbitrary Python."""

    candidate = raw.strip()
    if _NUMBER.fullmatch(candidate):
        value = float(candidate)
        return value if math.isfinite(value) else None

    # Some v1 snapshots accidentally persisted Function#toString output.  Its
    # return expression is still useful for recognizing a constant expression.
    returns = re.findall(r"\breturn\s+(.+?);?(?:\n|$)", candidate)
    if returns:
        candidate = returns[-1].strip().rstrip(";")
    candidate = candidate.replace("Math.", "").replace("^", "**")
    try:
        tree = ast.parse(candidate, mode="eval")
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in {*_SAFE_FUNCTIONS, "pi"}:
            return None
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCTIONS:
                return None
        if isinstance(
            node,
            (
                ast.Attribute,
                ast.Await,
                ast.Dict,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.GeneratorExp,
                ast.NamedExpr,
                ast.Subscript,
            ),
        ):
            return None
    try:
        result = eval(  # noqa: S307 - AST and globals are deliberately restricted above.
            compile(tree, "<ATH expression>", "eval"),
            {"__builtins__": {}, **_SAFE_FUNCTIONS, "pi": math.pi},
            {},
        )
        number = float(result)
    except (ArithmeticError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class StrictModel(BaseModel):
    """Base class: reject accidental schema drift while allowing explicit extras."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Expr(StrictModel):
    """A numeric value and its exact v1 source spelling.

    V1 retains strings in ``src/config/index.js:319-333``.  A value is ``None``
    when the expression depends on the angular variable ``p`` (or is otherwise
    not a scalar); ``raw`` is always sufficient for lossless serialization.
    """

    value: float | None = None
    raw: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            return {"value": float(value), "raw": "1" if value else "0"}
        if isinstance(value, (int, float)):
            try:
                number = float(value)
            except OverflowError as exc:
                raise ValueError("numeric design values must be representable as floats") from exc
            if not math.isfinite(number):
                raise ValueError("numeric design values must be finite")
            return {"value": number, "raw": None}
        if isinstance(value, str):
            return {"value": _constant_expression_value(value), "raw": value}
        if isinstance(value, Mapping):
            result = dict(value)
            raw = result.get("raw")
            if result.get("value") is None and isinstance(raw, str):
                result["value"] = _constant_expression_value(raw)
            return result
        return value

    @field_validator("value", mode="before")
    @classmethod
    def _finite_value(cls, value: Any) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("numeric design values must be representable as finite floats") from exc
        if not math.isfinite(number):
            raise ValueError("numeric design values must be finite")
        return number

    def text(self) -> str:
        """Return the v1-readable spelling, preferring the preserved source."""

        if self.raw is not None:
            return self.raw
        if self.value is None:
            return ""
        return str(int(self.value)) if self.value.is_integer() else format(self.value, ".15g")


def _resolution_expression(value: Any) -> Any:
    """Accept ATH's scalar or exactly-four-value enclosure resolution syntax."""

    if isinstance(value, str) and "," in value:
        value = [part.strip() for part in value.split(",")]
    if isinstance(value, list):
        return tuple(value)
    return value


ResolutionExpr = Annotated[
    Expr | tuple[Expr, Expr, Expr, Expr],
    BeforeValidator(_resolution_expression),
]


class ConfigBlock(StrictModel):
    """An unrecognized v1 block retained as ordered rows and item strings."""

    items: dict[str, str] = Field(default_factory=dict)
    lines: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    # Exact block-body tokens preserve duplicate assignments and the relative
    # ordering of assignments, rows, and comments after an unrelated edit.
    entries: list[str] = Field(default_factory=list)


class MorphConfig(StrictModel):
    target_shape: Expr | None = None
    target_width: Expr | None = None
    target_height: Expr | None = None
    corner_radius: Expr | None = None
    rate: Expr | None = None
    fixed_part: Expr | None = None
    allow_shrinkage: Expr | None = None


class EnclosureConfig(StrictModel):
    depth: Expr | None = None
    edge_radius: Expr | None = None
    edge_type: Expr | None = None
    space_l: Expr | None = None
    space_t: Expr | None = None
    space_r: Expr | None = None
    space_b: Expr | None = None
    front_resolution: ResolutionExpr | None = None
    back_resolution: ResolutionExpr | None = None


class MeshConfig(StrictModel):
    angular_segments: Expr | None = None
    corner_segments: Expr | None = None
    throat_segments: Expr | None = None
    length_segments: Expr | None = None
    throat_resolution: Expr | None = None
    mouth_resolution: Expr | None = None
    throat_slice_density: Expr | None = None
    sampling_mode: str | None = None
    z_map_points: str | None = None
    vertical_offset: Expr | None = None
    quadrants: Expr | None = None
    wall_thickness: Expr | None = None
    rear_resolution: Expr | None = None
    aperture_resolution_scale: Expr | None = None
    max_triangles: Expr | None = None
    allow_large_mesh: Expr | None = None
    # Optional post-build safety guard in millimetres. Regional resolution
    # controls remain authoritative; this rejects a mesh whose realized
    # longest triangle edge exceeds the configured ceiling.
    max_edge: Expr | None = None


class SourceConfig(StrictModel):
    shape: Expr | None = None
    radius: Expr | None = None
    curvature: Expr | None = None
    velocity: Expr | None = None
    contours: str | None = None
    velocity_convention: Literal["normal", "axial", "legacy"] | None = None


class SimulationConfig(StrictModel):
    f1: Expr | None = None
    f2: Expr | None = None
    num_frequencies: Expr | None = None
    sim_type: Literal["freestanding", "infinite-baffle"] | None = None
    solver_mode: str | None = None

    @field_validator("sim_type", mode="before")
    @classmethod
    def _v1_sim_type(cls, value: Any) -> Any:
        """Map v1's 1/2 convention from ``mwgConfig.js:267-278`` to names."""

        normalized = str(value).strip().lower() if value is not None else None
        return {"1": "infinite-baffle", "2": "freestanding"}.get(normalized, normalized)


class OutputConfig(StrictModel):
    stl: Expr | None = None
    msh: Expr | None = None


class GuidingCurveConfig(StrictModel):
    curve_type: Expr | None = None
    distance: Expr | None = None
    width: Expr | None = None
    aspect_ratio: Expr | None = None
    superellipse_n: Expr | None = None
    superformula: Expr | None = None
    sf_a: Expr | None = None
    sf_b: Expr | None = None
    sf_m1: Expr | None = None
    sf_m2: Expr | None = None
    sf_n1: Expr | None = None
    sf_n2: Expr | None = None
    sf_n3: Expr | None = None
    rotation: Expr | None = None


class DesignCommon(StrictModel):
    """Fields shared by all formula families and all v1 export sections."""

    formula: str
    scale: Expr | None = None
    throat_ext_angle: Expr | None = None
    throat_ext_length: Expr | None = None
    slot_length: Expr | None = None
    length_mode: Literal["profile", "total"] | None = None
    coverage_mode: str | None = None
    morph: MorphConfig = Field(default_factory=MorphConfig)
    mesh: MeshConfig = Field(default_factory=MeshConfig)
    enclosure: EnclosureConfig | None = None
    source: SourceConfig = Field(default_factory=SourceConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    extra_keys: dict[str, str] = Field(default_factory=dict)
    extra_blocks: dict[str, ConfigBlock] = Field(default_factory=dict)


class OSSEConfig(DesignCommon):
    formula: Literal["OSSE"]
    L: Expr | None = None
    a: Expr | None = None
    a0: Expr | None = None
    r0: Expr | None = None
    k: Expr | None = None
    s: Expr | None = None
    n: Expr | None = None
    q: Expr | None = None
    h: Expr | None = None
    throat_profile: Expr | None = None
    rotation: Expr | None = None
    guiding_curve: GuidingCurveConfig = Field(default_factory=GuidingCurveConfig)
    circ_arc_radius: Expr | None = None
    circ_arc_term_angle: Expr | None = None


class ROSSEConfig(DesignCommon):
    formula: Literal["R-OSSE"]
    R: Expr | None = None
    a: Expr | None = None
    a0: Expr | None = None
    b: Expr | None = None
    k: Expr | None = None
    m: Expr | None = None
    q: Expr | None = None
    r: Expr | None = None
    r0: Expr | None = None
    tmax: Expr | None = None


class ICWConfig(DesignCommon):
    formula: Literal["ICW"]
    r0: Expr | None = None
    a: Expr | None = None
    a0: Expr | None = None
    k: Expr | None = None
    q: Expr | None = None
    L: Expr | None = None
    R: Expr | None = None
    coverage_angle: Expr | None = None
    hold_start: Expr | None = None
    hold_end: Expr | None = None
    n_coeff: Expr | None = None
    termination: Literal["flat_baffle", "rollback"] | None = None
    theta1_deg: Expr | None = None
    depth: Expr | None = None
    curl: Expr | None = None


class FreeformPoint(StrictModel):
    z: Expr
    r: Expr
    angle_deg: Expr | None = None


class FreeformProfile(StrictModel):
    points: list[FreeformPoint]
    throat_angle_deg: Expr | None = None
    mouth_angle_deg: Expr | None = None


class CornerGrid(StrictModel):
    """Optional per-ring corner samples used by advanced FREEFORM payloads."""

    t: Expr
    values: list[list[Expr]]


class CrossSectionStation(StrictModel):
    t: Expr
    shape: Literal["ellipse", "superellipse", "rounded_rectangle"]
    exponent: Expr | None = None
    corner_radius_mm: Expr | None = None
    corner_grid: list[list[Expr]] | None = None


class FreeformConfig(DesignCommon):
    formula: Literal["FREEFORM"]
    profile_h: FreeformProfile
    profile_v: FreeformProfile
    cross_sections: list[CrossSectionStation] = Field(min_length=2, max_length=32)
    inflection_policy: Literal["reject", "warn"] | None = None
    corner_grids: list[CornerGrid] = Field(default_factory=list)

    @field_validator("cross_sections")
    @classmethod
    def _valid_cross_section_domain(
        cls, stations: list[CrossSectionStation]
    ) -> list[CrossSectionStation]:
        values = [station.t.value for station in stations]
        if any(value is None for value in values):
            raise ValueError("cross-section station t values must be scalar")
        scalars = [float(value) for value in values if value is not None]
        if any(value < 0 or value > 1 for value in scalars):
            raise ValueError("cross-section station t values must be between 0 and 1")
        if any(right <= left for left, right in zip(scalars, scalars[1:])):
            raise ValueError("cross-section station t values must be strictly increasing")
        if scalars[0] != 0 or stations[0].shape != "ellipse":
            raise ValueError('the first cross-section station must be "0 ellipse"')
        if scalars[-1] != 1:
            raise ValueError("the last cross-section station must have t = 1")
        return stations


DesignVariant = Annotated[
    OSSEConfig | ROSSEConfig | ICWConfig | FreeformConfig,
    Field(discriminator="formula"),
]


class DesignConfig(RootModel[DesignVariant]):
    """Root discriminated design union, serialized as a flat API object."""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_payload(cls, value: Any) -> Any:
        if isinstance(value, cls) or not isinstance(value, Mapping):
            return value
        # Lazy import avoids a schema/migration import cycle while ensuring
        # every API path validates the post-migration strict Literal schema.
        from .migrate import apply_migrations

        migrated, _applications = apply_migrations(value)
        return migrated

    @property
    def formula(self) -> str:
        return self.root.formula

    @property
    def extra_keys(self) -> dict[str, str]:
        return self.root.extra_keys

    @property
    def extra_blocks(self) -> dict[str, ConfigBlock]:
        return self.root.extra_blocks


__all__ = [
    "ConfigBlock",
    "CornerGrid",
    "CrossSectionStation",
    "DesignConfig",
    "Expr",
    "FreeformConfig",
    "FreeformPoint",
    "FreeformProfile",
    "ICWConfig",
    "MeshConfig",
    "OSSEConfig",
    "ResolutionExpr",
    "ROSSEConfig",
]
