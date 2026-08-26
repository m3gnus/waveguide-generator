"""Typed, lossless design models for Waveguide Generator configurations.

The field inventory mirrors the v1 writer in ``src/export/mwgConfig.js:46-280``,
the parser aliases in ``src/config/index.js:25-76,358-486``, and the solver
adapter payload in ``server/solver/mesher_adapter.py:190-424``.
"""

from __future__ import annotations

import ast
import math
import operator
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
_CONFIG_BLOCK_START = re.compile(r"^[\w.:-]+\s*=\s*\{$")
_LINE_BREAK = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_SAFE_FUNCTIONS = {
    "abs": abs,
    "acos": math.acos,
    "asin": math.asin,
    "atan": math.atan,
    "atan2": math.atan2,
    "cos": math.cos,
    "max": max,
    "min": min,
    "pow": pow,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}
# These functions were accepted by the v1 UI but are not supported by the
# pinned geometry engine. If this allowlist is ever widened, beware that v1
# maps ``log`` to base-10 Math.log10 and ``ln`` to natural-log Math.log. Python's
# ``math.log`` is natural log, so mapping v1's ``log`` to it would silently
# compute the wrong geometry, which is worse than rejecting the expression.
_V1_ONLY_FUNCTIONS = frozenset(
    {
        "acosh",
        "asinh",
        "atanh",
        "cbrt",
        "ceil",
        "copysign",
        "cosh",
        "deg",
        "exp",
        "exp2",
        "expm1",
        "fabs",
        "fdim",
        "floor",
        "fma",
        "fmax",
        "fmin",
        "fmod",
        "hypot",
        "ln",
        "log",
        "log1p",
        "log2",
        "log10",
        "rad",
        "remainder",
        "round",
        "sign",
        "sinh",
        "tanh",
        "trunc",
    }
)
_SAFE_CONSTANTS = {"pi": math.pi, "e": math.e}
_MAX_CONSTANT_EXPRESSION_CHARS = 16 * 1024
_MAX_CONSTANT_EXPRESSION_NODES = 256
_MAX_EXPRESSION_NESTING = 128
# ATH formulas use small shape exponents (commonly 2--16). This leaves ample
# compatibility headroom without inviting pathological power expressions.
_MAX_POWER_EXPONENT = 1_024.0
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_BOOLEAN_OPERATORS = (ast.And, ast.Or)
_COMPARE_OPERATORS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


class _NonConstantExpression(ValueError):
    """The ATH source is not a bounded scalar expression."""


def _has_balanced_braces(source: str) -> bool:
    """Match the deliberately simple brace accounting used by textcfg."""

    depth = 0
    for character in source:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _is_complete_legacy_function(source: str) -> bool:
    """Return whether textcfg's multiline function collector consumes all of it."""

    lines = source.splitlines()
    if (
        len(lines) < 2
        or _LINE_BREAK.search(source[-1:]) is not None
        or not lines[0].strip().startswith("function anonymous(")
    ):
        return False
    depth = 0
    opened = False
    for index, line in enumerate(lines):
        if "{" in line:
            opened = True
        depth += line.count("{") - line.count("}")
        if depth < 0:
            return False
        if opened and depth == 0:
            return index == len(lines) - 1
    return False


def _line_is_config_block_syntax(line: str) -> bool:
    candidate = line.split(";", 1)[0].strip()
    return candidate == "}" or _CONFIG_BLOCK_START.fullmatch(candidate) is not None


def _expression_candidate(raw: str) -> str:
    candidate = raw.strip()
    # Some v1 snapshots accidentally persisted Function#toString output. Its
    # return expression is still useful for validation and scalar recovery.
    returns = re.findall(r"\breturn\s+(.+?);?(?:\n|$)", candidate)
    if returns:
        candidate = returns[-1].strip().rstrip(";")
    if candidate.lower() == "true":
        return "1"
    if candidate.lower() == "false":
        return "0"
    return (
        candidate.replace("Math.PI", "pi")
        .replace("Math.E", "e")
        .replace("Math.", "")
        .replace("^", "**")
    )


def _validate_execution_node(node: ast.AST) -> None:
    """Mirror hornlab_mesher.profile_common's public expression grammar."""

    if isinstance(node, ast.Expression):
        _validate_execution_node(node.body)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("expression literals must be numeric")
    elif isinstance(node, ast.Name):
        # Lossless imports intentionally retain hand-written symbolic names
        # (legacy Throat.Diameter round-trip tests use a/b/k). They cannot be
        # scalar-derived here, and an execution path reports them if the pinned
        # mesher cannot resolve them. Calls are stricter because accepting a
        # known-incompatible function such as log()/ceil() creates a misleading
        # locally-derived value for an expression guaranteed to fail downstream.
        return
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINARY_OPERATORS:
            raise ValueError(f"unsupported expression operator {type(node.op).__name__}")
        _validate_execution_node(node.left)
        _validate_execution_node(node.right)
    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY_OPERATORS:
            raise ValueError(
                f"unsupported expression unary operator {type(node.op).__name__}"
            )
        _validate_execution_node(node.operand)
    elif isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _BOOLEAN_OPERATORS):
            raise ValueError(
                f"unsupported expression boolean operator {type(node.op).__name__}"
            )
        for value in node.values:
            _validate_execution_node(value)
    elif isinstance(node, ast.IfExp):
        _validate_execution_node(node.test)
        _validate_execution_node(node.body)
        _validate_execution_node(node.orelse)
    elif isinstance(node, ast.Compare):
        _validate_execution_node(node.left)
        for comparison, comparator in zip(node.ops, node.comparators):
            if not isinstance(comparison, _COMPARE_OPERATORS):
                raise ValueError(
                    f"unsupported expression comparison {type(comparison).__name__}"
                )
            _validate_execution_node(comparator)
    elif isinstance(node, ast.Call):
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id not in _SAFE_FUNCTIONS
            or node.keywords
        ):
            name = node.func.id if isinstance(node.func, ast.Name) else "indirect call"
            supported = ", ".join(sorted(_SAFE_FUNCTIONS))
            message = (
                f"unknown expression function {name!r}; the geometry engine supports "
                f"a fixed set of functions: {supported}"
            )
            if name in _V1_ONLY_FUNCTIONS:
                message += (
                    f"; {name!r} was accepted by the v1 UI but is not supported by "
                    "the current geometry engine; this is an original-to-current "
                    "compatibility limit"
                )
            raise ValueError(message)
        for argument in node.args:
            _validate_execution_node(argument)
    else:
        raise ValueError(f"unsupported expression syntax {type(node).__name__}")


def _bounded_constant_node(node: ast.AST) -> float:
    """Evaluate only numeric scalar AST nodes, converting at every operation."""

    if isinstance(node, ast.Expression):
        return _bounded_constant_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _NonConstantExpression
        try:
            result = float(node.value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise _NonConstantExpression from exc
    elif isinstance(node, ast.Name):
        if node.id not in _SAFE_CONSTANTS:
            raise _NonConstantExpression
        result = _SAFE_CONSTANTS[node.id]
    elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        result = _UNARY_OPERATORS[type(node.op)](_bounded_constant_node(node.operand))
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _bounded_constant_node(node.left)
        right = _bounded_constant_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER_EXPONENT:
            raise _NonConstantExpression
        result = _BINARY_OPERATORS[type(node.op)](left, right)
    elif isinstance(node, ast.Call):
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id not in _SAFE_FUNCTIONS
            or node.keywords
        ):
            raise _NonConstantExpression
        arguments = [_bounded_constant_node(argument) for argument in node.args]
        if node.func.id == "pow" and len(arguments) >= 2:
            if abs(arguments[1]) > _MAX_POWER_EXPONENT:
                raise _NonConstantExpression
        result = _SAFE_FUNCTIONS[node.func.id](*arguments)
    else:
        raise _NonConstantExpression

    try:
        number = float(result)
    except (OverflowError, TypeError, ValueError) as exc:
        raise _NonConstantExpression from exc
    if not math.isfinite(number):
        raise _NonConstantExpression
    return number


def _validate_expression_source(raw: str) -> None:
    """Reject expressions that are unsafe to forward to mesher evaluators."""

    candidate = raw.strip()
    if len(candidate) > _MAX_CONSTANT_EXPRESSION_CHARS:
        raise ValueError(
            f"expression source must not exceed {_MAX_CONSTANT_EXPRESSION_CHARS} characters"
        )
    has_newline = _LINE_BREAK.search(raw) is not None
    legacy_function = _is_complete_legacy_function(raw) if has_newline else False
    if has_newline and not legacy_function:
        raise ValueError("expression source must not contain structural newlines")
    if not _has_balanced_braces(raw):
        raise ValueError("expression source must contain balanced braces")
    if raw.strip().startswith("function anonymous(") and not legacy_function:
        raise ValueError("legacy function expression must be a complete multiline value")
    if not legacy_function and any(
        _line_is_config_block_syntax(line) for line in raw.splitlines()
    ):
        raise ValueError("expression source must not contain config block syntax")

    if not candidate:
        return
    if _NUMBER.fullmatch(candidate):
        try:
            number = float(candidate)
        except (OverflowError, ValueError) as exc:
            raise ValueError("numeric expression must be a finite float") from exc
        if not math.isfinite(number):
            raise ValueError("numeric expression must be finite")
        return
    nesting = 0
    for character in candidate:
        if character in "([{":
            nesting += 1
            if nesting > _MAX_EXPRESSION_NESTING:
                raise ValueError(
                    f"expression nesting must not exceed {_MAX_EXPRESSION_NESTING} levels"
                )
        elif character in ")]}":
            nesting = max(0, nesting - 1)
    candidate = _expression_candidate(candidate)
    if candidate.strip().lower() in {
        "undefined",
        "null",
        "nan",
        "infinity",
        "+infinity",
        "-infinity",
    }:
        # Historical missing-value tokens remain lossless for import/export.
        # Structural execution paths already reject them where a scalar is
        # required, while optional fields can preserve old files unchanged.
        return
    try:
        tree = ast.parse(candidate, mode="eval")
    except RecursionError as exc:
        raise ValueError("expression nesting is too deep") from exc
    except (SyntaxError, ValueError):
        # Preserve unparseable legacy spellings losslessly. They are not
        # evaluated by this module; execution paths will still report that the
        # particular design field is unsupported.
        return
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_CONSTANT_EXPRESSION_NODES:
        raise ValueError(
            f"expression must not exceed {_MAX_CONSTANT_EXPRESSION_NODES} syntax nodes"
        )
    # ATH's SuperFormula field is historically encoded as one comma-separated
    # numeric tuple (for example ``1,1,8,0.6,5,2``), despite sharing Expr's wire
    # shape. Preserve that bounded root form without allowing tuple arithmetic.
    root_tuple = tree.body if isinstance(tree.body, ast.Tuple) else None
    if root_tuple is not None:
        if len(root_tuple.elts) > 32:
            raise ValueError("expression tuples must not exceed 32 values")
        try:
            for element in root_tuple.elts:
                _bounded_constant_node(element)
        except (ArithmeticError, _NonConstantExpression, TypeError, ValueError) as exc:
            raise ValueError("expression tuple values must be numeric constants") from exc
        return
    _validate_execution_node(tree)
    for node in nodes:
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, (int, float))
        ):
            raise ValueError("expression literals must be numeric")
        if isinstance(node, (ast.Dict, ast.List, ast.Set)) or (
            isinstance(node, ast.Tuple) and node is not root_tuple
        ):
            raise ValueError("expression containers are not supported")
        exponent: ast.AST | None = None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = node.right
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "pow"
            and len(node.args) >= 2
        ):
            exponent = node.args[1]
        if exponent is not None:
            try:
                power = _bounded_constant_node(exponent)
            except (ArithmeticError, _NonConstantExpression, TypeError, ValueError) as exc:
                raise ValueError("expression exponents must be bounded constants") from exc
            if abs(power) > _MAX_POWER_EXPONENT:
                raise ValueError(
                    f"expression exponent magnitude must not exceed {_MAX_POWER_EXPONENT:g}"
                )


def _constant_expression_value(raw: str) -> float | None:
    """Evaluate scalar-only ATH syntax without accepting arbitrary Python."""

    candidate = raw.strip()
    if len(candidate) > _MAX_CONSTANT_EXPRESSION_CHARS:
        return None
    if _NUMBER.fullmatch(candidate):
        try:
            value = float(candidate)
        except (OverflowError, ValueError):
            return None
        return value if math.isfinite(value) else None

    candidate = _expression_candidate(candidate)
    try:
        tree = ast.parse(candidate, mode="eval")
    except (RecursionError, SyntaxError, ValueError):
        return None
    if sum(1 for _node in ast.walk(tree)) > _MAX_CONSTANT_EXPRESSION_NODES:
        return None
    try:
        return _bounded_constant_node(tree)
    except (ArithmeticError, _NonConstantExpression, TypeError, ValueError):
        return None


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
            _validate_expression_source(value)
            return {"value": _constant_expression_value(value), "raw": value}
        if isinstance(value, Mapping):
            result = dict(value)
            raw = result.get("raw")
            if isinstance(raw, str):
                _validate_expression_source(raw)
                if result.get("value") is None:
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

    @model_validator(mode="after")
    def _source_matches_value(self) -> "Expr":
        """Reject dual representations that would execute different geometry."""

        if self.raw is None:
            return self
        derived = _constant_expression_value(self.raw)
        if derived is None:
            # Parameterized ATH formulas intentionally carry the editor's
            # current numeric sample as a sidecar. The raw source remains the
            # executable geometry; only constant raw can be cross-checked.
            return self
        if self.value is None or not math.isclose(
            self.value, derived, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("expression value does not match constant raw source")
        return self

    def text(self) -> str:
        """Return the v1-readable spelling, preferring the preserved source."""

        if self.raw is not None:
            return self.raw
        if self.value is None:
            return ""
        return str(int(self.value)) if self.value.is_integer() else format(self.value, ".15g")

    def execution_text(self) -> str | float | None:
        """Return mesher-compatible syntax without changing lossless ``raw``."""

        if self.raw is not None:
            return _expression_candidate(self.raw)
        return self.value

    def constant_value(self) -> float | None:
        """Return a scalar only when the executable source is actually constant.

        Parameterized expressions may carry a cached editor sample in ``value``.
        That sidecar is useful to the UI but must not drive discrete controls such
        as a frequency count, quadrant selection, or FREEFORM station position.
        """

        if self.raw is None:
            return self.value
        return _constant_expression_value(self.raw)


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
    target_exponent: Expr | None = None
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
    # A compatibility slot, never a portable setting. Legacy files carry
    # ``Simulation.SolverMode``, but the solver path depends on what the host
    # machine can run, so it belongs to solve options; migration
    # ``006_machine_solver_mode_not_portable`` clears any stated value before
    # validation and reports the drop, and design export never writes it.
    solver_mode: Literal["auto", "full_3d", "circsym"] | None = None

    @field_validator("sim_type", mode="before")
    @classmethod
    def _v1_sim_type(cls, value: Any) -> Any:
        """Map v1's 1/2 convention from ``mwgConfig.js:267-278`` to names."""

        normalized = str(value).strip().lower() if value is not None else None
        return {"1": "infinite-baffle", "2": "freestanding"}.get(normalized, normalized)

    @field_validator("solver_mode", mode="before")
    @classmethod
    def _solver_mode(cls, value: Any) -> Any:
        return str(value).strip().lower() if value is not None else None


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
    t: Expr
    r: Expr
    angle_deg: Expr | None = None


class FreeformProfile(StrictModel):
    points: list[FreeformPoint]
    throat_angle_deg: Expr | None = None
    mouth_angle_deg: Expr | None = None

    @field_validator("points")
    @classmethod
    def _valid_point_domain(cls, points: list[FreeformPoint]) -> list[FreeformPoint]:
        if len(points) < 2:
            raise ValueError("FREEFORM profiles require at least 2 points")
        values = [point.t.constant_value() for point in points]
        if any(value is None for value in values):
            raise ValueError("FREEFORM profile point t values must be scalar")
        scalars = [float(value) for value in values if value is not None]
        if any(value < 0 or value > 1 for value in scalars):
            raise ValueError("FREEFORM profile point t values must be between 0 and 1")
        if any(right <= left for left, right in zip(scalars, scalars[1:])):
            raise ValueError("FREEFORM profile point t values must be strictly increasing")
        if scalars[0] != 0:
            raise ValueError("the first FREEFORM profile point must have t = 0")
        if scalars[-1] != 1:
            raise ValueError("the last FREEFORM profile point must have t = 1")
        return points


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

    @field_validator("corner_grid", mode="before")
    @classmethod
    def _reject_corner_grid(cls, corner_grid: Any) -> Any:
        if corner_grid is not None:
            raise ValueError(
                "FREEFORM cross-section corner_grid is not supported by design format 2"
            )
        return corner_grid


class FreeformConfig(DesignCommon):
    formula: Literal["FREEFORM"]
    length: Expr
    profile_h: FreeformProfile
    profile_v: FreeformProfile
    cross_sections: list[CrossSectionStation] = Field(min_length=2, max_length=32)
    inflection_policy: Literal["reject", "warn"] | None = None
    corner_grids: list[CornerGrid] = Field(default_factory=list)

    @field_validator("length")
    @classmethod
    def _valid_length(cls, length: Expr) -> Expr:
        value = length.constant_value()
        if value is None:
            raise ValueError("FREEFORM length must be scalar")
        if value <= 0:
            raise ValueError("FREEFORM length must be greater than 0")
        return length

    @field_validator("cross_sections")
    @classmethod
    def _valid_cross_section_domain(
        cls, stations: list[CrossSectionStation]
    ) -> list[CrossSectionStation]:
        values = [station.t.constant_value() for station in stations]
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

    @field_validator("corner_grids", mode="before")
    @classmethod
    def _reject_corner_grids(cls, corner_grids: Any) -> Any:
        # Mirrors the mesher translation, which refuses corner-grid data
        # outright: accepting it here only to drop it at save time is the
        # silent-loss this validator exists to prevent.
        if corner_grids:
            raise ValueError("FREEFORM corner_grids are not supported by design format 2")
        return corner_grids

    @model_validator(mode="after")
    def _shared_throat_radius(self) -> "FreeformConfig":
        if self.profile_v.points[0].r != self.profile_h.points[0].r:
            raise ValueError(
                "FREEFORM profile throat radii must match because design format 2 "
                "stores one shared throat radius"
            )
        return self


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
