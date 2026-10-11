"""Parser and v1-compatible serializer for ATH/MWG text configurations.

The lexer mirrors ``src/config/index.js:266-338``: semicolon comments, one
top-level block at a time, first-equals splitting, raw values, and passthrough
block rows.  Stable writer order mirrors ``src/export/mwgConfig.js:46-295``.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal, Mapping

from pydantic import ValidationError

from server.contracts.canonical import canonical_ascii_json

from .ath_import_defaults import apply_ath_import_defaults
from .migrate import MigrationApplication, apply_migrations
from .text_import import TEXT_IMPORT_VERSION, TEXT_IMPORT_VERSIONS, NATIVE_GEOMETRY_VERSION, GEOMETRY_STAMP_LABEL
from .schema import (
    ConfigBlock,
    DesignConfig,
    Expr,
    FreeformConfig,
    ICWConfig,
    OSSEConfig,
    ROSSEConfig,
)

if TYPE_CHECKING:
    from server.cadlink.identity import CadLink


_BLOCK_START = re.compile(r"^([\w.:-]+)\s*=\s*\{$")
_MWG_SNIFF = re.compile(r";\s*(?:Parameter|MWG) config", re.IGNORECASE)
_LINE_BREAK = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_DESIGN_FORMAT = 2
# ATH's report block, which carries the design's name in its ``Title``.
_REPORT_BLOCK = "Report"
_WG_SOLVE_BLOCK = "WG.Solve"
# `WG.Solve` keys that describe the machine rather than the design: what a host
# can run is not a property of a file that moves between hosts. They are kept
# verbatim on read and dropped only when WG rewrites the design, so the set is
# also what the open report names -- one source of truth, so the keys that are
# ignored and the keys that are reported cannot drift apart. Each entry carries
# why it is not portable and what to choose instead.
_MACHINE_SOLVE_ADVICE: tuple[tuple[str, str, str], ...] = (
    ("Engine", "Which backend runs a solve depends on the host", "the engine"),
    (
        "SolverMode",
        "Which formulations a host can run depends on the machine",
        "the solver path",
    ),
)
_MACHINE_SOLVE_KEYS = frozenset(key for key, _why, _choose in _MACHINE_SOLVE_ADVICE)
_DESIGN_FORMAT_STAMP = re.compile(r"\bdesign-format\s*:\s*(\d+)\b", re.IGNORECASE)
_GEOMETRY_STAMP = re.compile(r"^;\s*Waveguide Generator geometry-interpretation:\s*(\S+)\s*$", re.IGNORECASE)


class TextConfigError(ValueError):
    """A source-located syntax or design validation failure."""


@dataclass
class _RawBlock:
    items: dict[str, str] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)


def _semantic_fingerprint(design: DesignConfig) -> str:
    return canonical_ascii_json(design.model_dump(mode="json"))


@dataclass(frozen=True)
class IgnoredSetting:
    """A setting the file states, the solve does not read, and the report names.

    `key` is the fully qualified spelling as written, `value` is what the file
    said, and `note` is the sentence shown to the user. The value is reported,
    never checked: a key that is not honoured must not also be a parse error.
    """

    key: str
    value: str
    note: str


def _ignored_setting(key: str, value: str, why: str, choose: str) -> IgnoredSetting:
    stated = f"states {value!r}" if value else "is stated without a value"
    return IgnoredSetting(
        key=f"{_WG_SOLVE_BLOCK}.{key}",
        value=value,
        note=(
            f"{_WG_SOLVE_BLOCK}.{key} {stated}. {why}, so the solve ignores "
            f"it. Choose {choose} in Solve options."
        ),
    )


@dataclass
class ParsedDesign:
    """A validated design plus all text-only material needed for lossless I/O."""

    design: DesignConfig
    dialect: Literal["mwg", "ath"]
    migrations: list[MigrationApplication] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    raw_values: dict[str, str] = field(default_factory=dict)
    cadlink: CadLink | None = None
    source_text: str | None = None
    _initial_fingerprint: str = ""
    ignored_profile: list[IgnoredSetting] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self._initial_fingerprint:
            self._initial_fingerprint = _semantic_fingerprint(self.design)

    @property
    def extra_keys(self) -> dict[str, str]:
        return self.design.extra_keys

    @property
    def extra_blocks(self) -> dict[str, ConfigBlock]:
        return self.design.extra_blocks

    @property
    def migration_names(self) -> list[str]:
        return [item.name for item in self.migrations]

    @property
    def ignored_settings(self) -> list[IgnoredSetting]:
        """Stated `WG.Solve` keys the solve never reads, in a fixed order.

        Computed from the retained passthrough block on every read rather than
        recorded at parse, because parse must stay lossless: an untouched open
        and save still returns the author's own bytes, keys included, and they
        leave only when WG rewrites the design.
        """

        block = self.extra_blocks.get(_WG_SOLVE_BLOCK)
        if block is None:
            return list(self.ignored_profile)
        return [
            _ignored_setting(key, value, why, choose)
            for key, why, choose in _MACHINE_SOLVE_ADVICE
            if (value := block.items.get(key)) is not None
        ] + self.ignored_profile

    def semantic_data(self) -> dict[str, Any]:
        """Return the JSON/API meaning used by the corpus round-trip law."""

        return self.design.model_dump(mode="json")


def _without_comment(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _is_complete_legacy_function(value: str) -> bool:
    """Return whether ``_collect_function`` would consume the whole value."""

    lines = value.splitlines()
    if (
        len(lines) < 2
        or _LINE_BREAK.search(value[-1:]) is not None
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


def _has_balanced_braces(value: str) -> bool:
    depth = 0
    for character in value:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _is_block_syntax(line: str) -> bool:
    candidate = _without_comment(line)
    return candidate == "}" or _BLOCK_START.fullmatch(candidate) is not None


def _validate_serialized_key(key: str) -> None:
    if _LINE_BREAK.search(key) is not None or not _has_balanced_braces(key):
        raise TextConfigError(f"unsafe config key {key!r}")
    if _is_block_syntax(key):
        raise TextConfigError(f"config key contains block syntax: {key!r}")


def _validate_serialized_value(value: str, *, key: str) -> None:
    has_newline = _LINE_BREAK.search(value) is not None
    legacy_function = _is_complete_legacy_function(value) if has_newline else False
    if has_newline and not legacy_function:
        raise TextConfigError(f"value for {key!r} contains a structural newline")
    if not _has_balanced_braces(value):
        raise TextConfigError(f"value for {key!r} contains unbalanced braces")
    if value.strip().startswith("function anonymous(") and not legacy_function:
        raise TextConfigError(f"value for {key!r} is an incomplete legacy function")
    if not legacy_function and any(
        _is_block_syntax(line) for line in value.splitlines()
    ):
        raise TextConfigError(f"value for {key!r} contains config block syntax")


def _append_block_entry(lines: list[str], entry: str, *, block: str) -> None:
    if _LINE_BREAK.search(entry) is not None:
        raise TextConfigError(f"entry in block {block!r} contains a structural newline")
    if _is_block_syntax(entry):
        raise TextConfigError(f"entry in block {block!r} contains config block syntax")
    lines.append(entry)


def _collect_function(lines: list[str], index: int, first_value: str) -> tuple[str, int]:
    """Recover multiline Function#toString values found in 143 v1 snapshots."""

    raw = [first_value]
    depth = first_value.count("{") - first_value.count("}")
    opened = "{" in first_value
    index += 1
    while index < len(lines):
        part = lines[index].strip()
        raw.append(part)
        if "{" in part:
            opened = True
        depth += part.count("{") - part.count("}")
        index += 1
        if opened and depth <= 0:
            break
    if not opened or depth > 0:
        raise TextConfigError("unterminated function expression at end of file")
    return "\n".join(raw), index


def _lex(text: str, repeats: list[tuple[str, _RawBlock]] | None = None) -> tuple[dict[str, str], dict[str, _RawBlock], list[str], dict[str, str]]:
    flat: dict[str, str] = {}
    blocks: dict[str, _RawBlock] = {}
    comments: list[str] = []
    raw_values: dict[str, str] = {}
    lines = text.splitlines()
    current: str | None = None
    index = 0
    while index < len(lines):
        original = lines[index]
        stripped = original.strip()
        if ";" in original:
            comment = original[original.index(";") :].strip()
            if comment:
                if current is None:
                    comments.append(comment)
                else:
                    blocks[current].comments.append(comment)
        line = _without_comment(original)
        index += 1
        if not line:
            if current is not None and stripped:
                blocks[current].entries.append(stripped)
            continue
        match = _BLOCK_START.fullmatch(line)
        if match:
            if current is not None:
                raise TextConfigError(f"nested block {match.group(1)!r} inside {current!r}")
            current = match.group(1)
            if current in blocks and not current.startswith("_") and repeats is not None:
                repeats.append((current, blocks[current]))
            blocks[current] = _RawBlock()
            continue
        if line == "}":
            if current is None:
                raise TextConfigError("unexpected block terminator")
            current = None
            continue
        if current is not None:
            blocks[current].entries.append(stripped)
        if "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if value.startswith("function anonymous("):
                value, index = _collect_function(lines, index - 1, value)
                if current is not None:
                    blocks[current].entries.extend(value.splitlines()[1:])
            qualified = f"{current}.{key}" if current else key
            raw_values[qualified] = value
            if current is None:
                flat[key] = value
            else:
                blocks[current].items[key] = value
            continue
        if current is not None:
            blocks[current].lines.append(line)
    if current is not None:
        raise TextConfigError(f"unterminated block {current!r} at end of file")
    return flat, blocks, comments, raw_values


_COMMON_MAP: dict[str, tuple[str, ...]] = {
    "Scale": ("scale",),
    "Coverage.Mode": ("coverage_mode",),
    "Length.Mode": ("length_mode",),
    "Throat.Ext.Angle": ("throat_ext_angle",),
    "Throat.Ext.Length": ("throat_ext_length",),
    "Slot.Length": ("slot_length",),
    "Morph.TargetShape": ("morph", "target_shape"),
    "Morph.Exponent": ("morph", "target_exponent"),
    "Morph.TargetWidth": ("morph", "target_width"),
    "Morph.TargetHeight": ("morph", "target_height"),
    "Morph.CornerRadius": ("morph", "corner_radius"),
    "Morph.Rate": ("morph", "rate"),
    "Morph.FixedPart": ("morph", "fixed_part"),
    "Morph.AllowShrinkage": ("morph", "allow_shrinkage"),
    "Mesh.AngularSegments": ("mesh", "angular_segments"),
    "Mesh.CornerSegments": ("mesh", "corner_segments"),
    "Mesh.ThroatSegments": ("mesh", "throat_segments"),
    "Mesh.LengthSegments": ("mesh", "length_segments"),
    "Mesh.ThroatResolution": ("mesh", "throat_resolution"),
    "Mesh.MouthResolution": ("mesh", "mouth_resolution"),
    "Mesh.ThroatSliceDensity": ("mesh", "throat_slice_density"),
    "Mesh.SamplingMode": ("mesh", "sampling_mode"),
    "Mesh.ZMapPoints": ("mesh", "z_map_points"),
    "Mesh.ZMap": ("mesh", "z_map_points"),
    "Mesh.VerticalOffset": ("mesh", "vertical_offset"),
    "Mesh.Quadrants": ("mesh", "quadrants"),
    "Mesh.WallThickness": ("mesh", "wall_thickness"),
    "Mesh.RearResolution": ("mesh", "rear_resolution"),
    "Mesh.ApertureResolutionScale": ("mesh", "aperture_resolution_scale"),
    "Mesh.MaxTriangles": ("mesh", "max_triangles"),
    "Mesh.AllowLargeMesh": ("mesh", "allow_large_mesh"),
    "Mesh.MaxEdge": ("mesh", "max_edge"),
    "Output.STL": ("output", "stl"),
    "Output.MSH": ("output", "msh"),
    "Source.Shape": ("source", "shape"),
    "Source.Radius": ("source", "radius"),
    "Source.Curv": ("source", "curvature"),
    "Source.Velocity": ("source", "velocity"),
    "Source.Contours": ("source", "contours"),
    "Source.VelocityConvention": ("source", "velocity_convention"),
    "Simulation.F1": ("simulation", "f1"),
    "ABEC.f1": ("simulation", "f1"),
    "ABEC.F1": ("simulation", "f1"),
    "Simulation.F2": ("simulation", "f2"),
    "ABEC.f2": ("simulation", "f2"),
    "ABEC.F2": ("simulation", "f2"),
    "Simulation.NumFrequencies": ("simulation", "num_frequencies"),
    "ABEC.NumFrequencies": ("simulation", "num_frequencies"),
    "Simulation.SimType": ("simulation", "sim_type"),
    "ABEC.SimType": ("simulation", "sim_type"),
    "Simulation.SolverMode": ("simulation", "solver_mode"),
}

_OSSE_MAP: dict[str, tuple[str, ...]] = {
    "Coverage.Angle": ("a",),
    "Length": ("L",),
    "Term.n": ("n",),
    "Term.q": ("q",),
    "Term.s": ("s",),
    "Throat.Angle": ("a0",),
    "OS.k": ("k",),
    "OS.h": ("h",),
    "Throat.Profile": ("throat_profile",),
    "Rot": ("rotation",),
    "GCurve.Type": ("guiding_curve", "curve_type"),
    "GCurve.Dist": ("guiding_curve", "distance"),
    "GCurve.Width": ("guiding_curve", "width"),
    "GCurve.AspectRatio": ("guiding_curve", "aspect_ratio"),
    "GCurve.SE.n": ("guiding_curve", "superellipse_n"),
    "GCurve.SF": ("guiding_curve", "superformula"),
    "GCurve.SF.a": ("guiding_curve", "sf_a"),
    "GCurve.SF.b": ("guiding_curve", "sf_b"),
    "GCurve.SF.m1": ("guiding_curve", "sf_m1"),
    "GCurve.SF.m2": ("guiding_curve", "sf_m2"),
    "GCurve.SF.n1": ("guiding_curve", "sf_n1"),
    "GCurve.SF.n2": ("guiding_curve", "sf_n2"),
    "GCurve.SF.n3": ("guiding_curve", "sf_n3"),
    "GCurve.Rot": ("guiding_curve", "rotation"),
    "CircArc.Radius": ("circ_arc_radius",),
    "CircArc.TermAngle": ("circ_arc_term_angle",),
}


def _put(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = payload
    for name in path[:-1]:
        target = target.setdefault(name, {})
    target[path[-1]] = value


_GENERATED_DOUBLING = re.compile(r"\s*2\s*\*\s*\(")


def _unwrap_generated_doubling(value: str) -> str | None:
    """Undo the writer's ``2*(<raw>)`` spelling, matching parens properly.

    A greedy ``2\\s*\\*\\s*\\((.*)\\)`` stops at the *last* closing paren, so a
    hand-written ``Throat.Diameter = 2*(a)*(b)`` unwrapped to the unbalanced
    fragment ``a)*(b``.  Only the writer's own spelling -- where the paren that
    matches the opening one is the final character -- may be unwrapped; anything
    else has to fall through to the general ``(<value>) / 2`` path.
    """

    opening = _GENERATED_DOUBLING.match(value)
    if opening is None:
        return None
    start = opening.end()
    depth = 1
    for index in range(start, len(value)):
        character = value[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return value[start:index] if not value[index + 1 :].strip() else None
    return None


def _numeric_or_expression_divide_by_two(value: str) -> Any:
    generated = _unwrap_generated_doubling(value)
    if generated is not None:
        return generated
    try:
        number = float(value)
        return number / 2.0 if math.isfinite(number) else value
    except ValueError:
        return f"({value}) / 2"


def _formula(flat: Mapping[str, str], blocks: Mapping[str, _RawBlock]) -> str:
    if "FREEFORM" in blocks or "Freeform.Length" in flat or any(
        key.startswith("Freeform.") for key in blocks
    ):
        return "FREEFORM"
    # ROSSE was historically an unconsumed passthrough block. Only effective
    # stretch (both coefficients nonzero) opts it into the mesher's R-OSSE alias.
    from .throat_stretch import text_number

    alias = blocks.get("ROSSE")
    if "R-OSSE" in blocks or (alias is not None and all(
        text_number(alias.items.get(key, "0")) != 0 for key in ("s1", "s2")
    )):
        return "R-OSSE"
    if "ICW" in blocks:
        return "ICW"
    if "OSSE" in blocks or any(key in flat for key in ("Coverage.Angle", "Length", "Term.n")):
        return "OSSE"
    raise TextConfigError("could not find an OSSE, R-OSSE, ICW, or FREEFORM design")


def _profile_block_name(formula: str, blocks: Mapping[str, _RawBlock]) -> str | None:
    """Keep the source spelling separate from the canonical formula."""

    names = ("R-OSSE", "ROSSE") if formula == "R-OSSE" else (formula,)
    return next((name for name in names if name in blocks), None)


def _legacy_sections(flat: dict[str, str], blocks: Mapping[str, _RawBlock]) -> set[str]:
    """Flatten ATH section blocks used by the three v1 fixtures."""

    consumed: set[str] = set()
    prefixes = {"MORPH": "Morph", "Mesh": "Mesh", "Source": "Source", "Simulation": "Simulation"}
    for block_name, prefix in prefixes.items():
        block = blocks.get(block_name)
        if block is None:
            continue
        consumed.add(block_name)
        for key, value in block.items.items():
            flat.setdefault(f"{prefix}.{key}", value)
    return consumed


_ENCLOSURE_FLAT_PREFIX = "Mesh.Enclosure."


def _enclosure(
    blocks: Mapping[str, _RawBlock], flat: Mapping[str, str] | None = None, *, v2: bool = False
) -> dict[str, Any] | None:
    block = blocks.get("Mesh.Enclosure")
    # ATH also spells the members as top-level dotted keys, which the mesher
    # importer reads; a member in the block wins over the same dotted key.
    flat_items = {
        key[len(_ENCLOSURE_FLAT_PREFIX):]: value
        for key, value in (flat or {}).items()
        if key.startswith(_ENCLOSURE_FLAT_PREFIX)
    }
    if block is None and not flat_items:
        return None
    block = _RawBlock(items={**flat_items, **(block.items if block else {})})
    result: dict[str, Any] = {}
    mapping = {
        "Depth": "depth",
        "EdgeRadius": "edge_radius",
        "EdgeType": "edge_type",
        "FrontResolution": "front_resolution",
        "BackResolution": "back_resolution",
    }
    for source, target in mapping.items():
        if source in block.items:
            result[target] = block.items[source]
    if "Spacing" in block.items:
        parts = [part.strip() for part in block.items["Spacing"].split(",")]
        if v2 and len(parts) != 4:
            raise TextConfigError("Mesh.Enclosure.Spacing must list four margins (+x, +y, -x, -y)")
        order = ("space_r", "space_t", "space_l", "space_b") if v2 else ("space_l", "space_t", "space_r", "space_b")
        for target, value in zip(order, parts):
            result[target] = value
    return result


_FREEFORM_FLAT_ITEMS = frozenset(
    {
        "Freeform.Length",
        "Freeform.ThroatRadius",
        "Freeform.ThroatAngle",
        "Freeform.OvershootPolicy",
        "Freeform.InflectionPolicy",
    }
)
_FREEFORM_BLOCKS = frozenset(
    {
        "Freeform.H",
        "Freeform.V",
        "Freeform.H.Points",
        "Freeform.V.Points",
        "Freeform.CrossSections",
    }
)
_FREEFORM_PROFILE_ITEMS = frozenset(
    {
        "MouthRadius",
        "MouthAngle",
        "ThroatAngle",
        "ThroatTangentScale",
        "MouthTangentScale",
    }
)
_FREEFORM_STATION_SHAPES = frozenset(
    {"circle", "ellipse", "superellipse", "rounded_rectangle"}
)


def _finite(raw: str, location: str) -> float:
    try:
        value = float(raw)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TextConfigError(f"{location} must be a finite number") from exc
    if not math.isfinite(value):
        raise TextConfigError(f"{location} must be a finite number")
    return value


def _validate_block_items(block_name: str, block: _RawBlock, allowed: frozenset[str]) -> None:
    unknown = next((key for key in block.items if key not in allowed), None)
    if unknown is not None:
        raise TextConfigError(f"{block_name} item {unknown}: unknown item name")


def _point(raw: str, block_name: str, row: int, length: float) -> dict[str, Any]:
    parts = raw.split()
    if len(parts) not in (2, 3, 4):
        raise TextConfigError(f"{block_name} row {row}: expected 2, 3, or 4 numeric values")
    values = [_finite(part, f"{block_name} row {row} column {index + 1}") for index, part in enumerate(parts)]
    if not 0 < values[0] < length:
        raise TextConfigError(f"{block_name} row {row}: z must be strictly between 0 and {length:g}")
    if values[1] <= 0:
        raise TextConfigError(f"{block_name} row {row}: radius must be greater than 0")
    if len(values) >= 3 and not -90 < values[2] < 90:
        raise TextConfigError(f"{block_name} row {row}: angle must be between -90 and 90")
    if len(values) == 4 and not 0 < values[3] <= 3:
        raise TextConfigError(f"{block_name} row {row}: strength must be greater than 0 and at most 3")
    result = {"t": values[0] / length, "r": parts[1]}
    if len(parts) >= 3:
        result["angle_deg"] = parts[2]
    if len(parts) == 4:
        result["strength"] = parts[3]
    return result


def _freeform_payload(flat: Mapping[str, str], blocks: Mapping[str, _RawBlock]) -> dict[str, Any]:
    length = flat.get("Freeform.Length")
    throat_radius = flat.get("Freeform.ThroatRadius")
    if length is None or throat_radius is None:
        raise TextConfigError("FREEFORM requires Freeform.Length and Freeform.ThroatRadius")
    length_value = _finite(length, "Freeform.Length")
    throat_radius_value = _finite(throat_radius, "Freeform.ThroatRadius")
    if length_value <= 0:
        raise TextConfigError("Freeform.Length must be greater than 0")
    if throat_radius_value <= 0:
        raise TextConfigError("Freeform.ThroatRadius must be greater than 0")

    def profile(axis: str) -> dict[str, Any]:
        block_name = f"Freeform.{axis}"
        info = blocks.get(block_name)
        if info is None or "MouthRadius" not in info.items:
            raise TextConfigError(f"{block_name} requires MouthRadius")
        _validate_block_items(block_name, info, _FREEFORM_PROFILE_ITEMS)
        mouth_radius = _finite(info.items["MouthRadius"], f"{block_name}.MouthRadius")
        if mouth_radius <= 0:
            raise TextConfigError(f"{block_name}.MouthRadius must be greater than 0")
        points_block_name = f"{block_name}.Points"
        points_block = blocks.get(points_block_name, _RawBlock())
        _validate_block_items(points_block_name, points_block, frozenset())
        rows = points_block.lines
        if len(rows) > 62:
            raise TextConfigError(f"{points_block_name} contains more than 62 interior anchors")
        interior = [
            _point(row, points_block_name, index + 1, length_value)
            for index, row in enumerate(rows)
        ]
        z_values = [float(point["t"]) * length_value for point in interior]
        if any(right <= left for left, right in zip(z_values, z_values[1:])):
            raise TextConfigError(f"{points_block_name} z values must be strictly increasing")
        points = [{"t": 0, "r": throat_radius}]
        points.extend(interior)
        points.append({"t": 1, "r": info.items["MouthRadius"]})
        result = {
            "points": points,
            "throat_angle_deg": info.items.get(
                "ThroatAngle", flat.get("Freeform.ThroatAngle")
            ),
            "mouth_angle_deg": info.items.get("MouthAngle"),
        }
        if "ThroatTangentScale" in info.items:
            result["throat_tangent_scale"] = info.items["ThroatTangentScale"]
        if "MouthTangentScale" in info.items:
            result["mouth_tangent_scale"] = info.items["MouthTangentScale"]
        return result

    stations: list[dict[str, Any]] = []
    station_block = blocks.get("Freeform.CrossSections")
    if station_block is None:
        raise TextConfigError("FREEFORM requires a Freeform.CrossSections block")
    _validate_block_items("Freeform.CrossSections", station_block, frozenset())
    station_t: list[float] = []
    for index, row in enumerate(station_block.lines):
        parts = row.split()
        if len(parts) not in (2, 3):
            raise TextConfigError(f"Freeform.CrossSections row {index + 1}: expected 2 or 3 values")
        t_value = _finite(parts[0], f"Freeform.CrossSections row {index + 1} column 1")
        if not 0 <= t_value <= 1:
            raise TextConfigError(f"Freeform.CrossSections row {index + 1}: t must be between 0 and 1")
        if parts[1] not in _FREEFORM_STATION_SHAPES:
            raise TextConfigError(
                f"Freeform.CrossSections row {index + 1}: unknown shape {parts[1]!r}"
            )
        station: dict[str, Any] = {"t": parts[0], "shape": parts[1]}
        if len(parts) == 3:
            if parts[2].startswith("ratio:"):
                _finite(
                    parts[2].split(":", 1)[1],
                    f"Freeform.CrossSections row {index + 1} corner ratio",
                )
                station["corner_ratio"] = parts[2].split(":", 1)[1]
            elif parts[1] == "superellipse":
                _finite(parts[2], f"Freeform.CrossSections row {index + 1} exponent")
                station["exponent"] = parts[2]
            elif parts[1] == "rounded_rectangle":
                _finite(parts[2], f"Freeform.CrossSections row {index + 1} corner radius")
                station["corner_radius_mm"] = parts[2]
            else:
                raise TextConfigError(
                    f"Freeform.CrossSections row {index + 1}: {parts[1]} rows must contain exactly 2 values"
                )
        station_t.append(t_value)
        stations.append(station)
    if len(stations) < 2:
        raise TextConfigError("Freeform.CrossSections requires at least two station rows")
    if len(stations) > 32:
        raise TextConfigError("Freeform.CrossSections permits at most 32 station rows")
    if any(right <= left for left, right in zip(station_t, station_t[1:])):
        raise TextConfigError("Freeform.CrossSections t values must be strictly increasing")
    if station_t[0] != 0 or stations[0]["shape"] not in {"circle", "ellipse"}:
        raise TextConfigError('the first Freeform.CrossSections station must be "0 circle" or "0 ellipse"')
    if station_t[-1] != 1:
        raise TextConfigError("the last Freeform.CrossSections station must have t = 1")
    result = {
        "formula": "FREEFORM",
        "length": length,
        "profile_h": profile("H"),
        "profile_v": profile("V"),
        "cross_sections": stations,
        "inflection_policy": flat.get("Freeform.InflectionPolicy"),
    }
    if "Freeform.OvershootPolicy" in flat:
        result["overshoot_policy"] = flat["Freeform.OvershootPolicy"]
    return result


# ATH items that ATH applies and this importer has no model for. In an ATH file
# each one is refused by name: dropping it built a different, simpler horn with
# nothing on screen but a key count. The reasons follow the mesher importer's
# own table. Keys that ATH itself ignores (typos, solver and export settings)
# are not listed: they import and are shown by name as preserved.
_UNSUPPORTED_ATH_REASONS: dict[str, str] = {
    "Scale.Z": "a separate axial scale is not implemented",
    "Horn.Part": "the two-profile (Horn.Part) geometry is not implemented",
    "HornGeometry": "the two-profile (Horn.Part) geometry is not implemented",
    "Horn.Adapter": "the two-profile (Horn.Part) geometry is not implemented",
    "Throat.Ext.Ctrl": "the curved throat adapter is not implemented",
    "Rollback": "the rollback mouth termination is not implemented",
    "Mesh.Roundover": "scalar form is unsupported: ATH requires a Radius/Segments block",
    "Profile": "a profile read from a point file is not implemented",
    "FusionRotaryProfile": "the rotary profile with flange and joint is not implemented",
    "BEE": "the LF enclosure is not implemented",
    "Source.Array": "source arrays are not implemented",
    "LFSource": "secondary LF sources are not implemented",
    "Source.Contours": "contour-drawn (dome) sources are not implemented; WG would solve "
    "with its default throat source",
    "Mesh.ThroatSegments": "the throat-extension slice count is read but never built; use Mesh.ZMapPoints",
    "Mesh.ThroatExtSegments": "the throat-extension slice count is not implemented; use Mesh.ZMapPoints",
    "Mesh.ThroatSliceDensity": "the throat slice density is read but never built",
    "Coverage.Mode": "the coverage mode is read but never built",
    "Plan": "user-defined enclosure plans are not implemented; use Depth and Spacing",
    "Dim": "enclosure dimensions by Dim are not implemented; use Spacing",
}
# Applied by ATH only inside the R-OSSE block; a top-level copy is ignored by ATH.
_ATH_ROSSE_BLOCK_ONLY = {
    "arcterm": "the arc termination changes ATH's geometry and is not implemented",
    "trunc": "throat truncation changes ATH's geometry and is not implemented",
}
_ATH_ENCLOSURE_MEMBERS = frozenset(
    {"Depth", "EdgeRadius", "EdgeType", "FrontResolution", "BackResolution", "Spacing"}
)
# R-OSSE block members measured against ATH (the importer's profile aliases).
# `Rot`, `Length` and `Scale` are read from inside the block by neither ATH nor
# this importer (reported as ignored); every other member is unmeasured and
# refused.
_ATH_ROSSE_MEMBERS = frozenset(
    {"R", "r0", "a", "Coverage.Angle", "a0", "Throat.Angle", "k", "OS.k", "Term.k",
     "r", "m", "b", "q", "Term.q", "tmax", "s1", "s2",
     "Throat.Diameter", "Throat.Profile", "Rot", "Length", "Scale",
     "Throat.Ext.Length", "Throat.Ext.Angle", "Slot.Length"}
)


# Values at which an otherwise unsupported item changes nothing in ATH: no
# separate axial scale (1), single profile (1), no mouth roundover (0),
# rollback switched off (0).
_ATH_NOOP_VALUES = {"Scale.Z": 1.0, "HornGeometry": 1.0, "Mesh.Roundover": 0.0, "Rollback": 0.0}


def _ath_number(raw: str) -> float | None:
    """Evaluate a finite ATH value with the importer's profile evaluator."""
    from hornlab_mesher.profile_common import eval_param

    from .throat_stretch import text_number

    try:
        value = float(eval_param(text_number(raw)))
    except (TypeError, ValueError, ArithmeticError, SyntaxError):
        return None
    return value if math.isfinite(value) else None


def _ath_rollback_disabled(flat: Mapping[str, str]) -> bool:
    return "Rollback" in flat and _ath_number(flat["Rollback"]) == 0.0


def _ath_name(name: str) -> str:
    return name.split(":", 1)[0].strip()


def _ath_unsupported_reason(name: str) -> str | None:
    base = _ath_name(name)
    if base.startswith("_"):
        return None
    if base.startswith("LFSource"):
        return _UNSUPPORTED_ATH_REASONS["LFSource"]
    if base.startswith("Source.Velocity."):
        return "per-source velocity profiles are not implemented"
    if base == "Rollback" or base.startswith("Rollback."):
        return _UNSUPPORTED_ATH_REASONS["Rollback"]
    if base.startswith("Horn.Part"):
        return _UNSUPPORTED_ATH_REASONS["Horn.Part"]
    if base in {"Plan", "Dim"}:  # meaningful only inside Mesh.Enclosure
        return None
    return _UNSUPPORTED_ATH_REASONS.get(base)


def _refuse_unsupported_ath_items(
    flat: Mapping[str, str],
    blocks: Mapping[str, _RawBlock],
    *,
    formula: str,
    block_name: str | None,
) -> None:
    """Refuse ATH items that change ATH's model and that WG would drop.

    Runs for ATH-format files only, whether or not stretch is active. WG's own
    files (`; Parameter config` header) keep their passthrough semantics: what
    WG writes it reads back, extras included.
    """

    def noop(base: str, value: str) -> bool:
        # Defaults ATH treats as "off": the item is present and changes nothing.
        expected = _ATH_NOOP_VALUES.get(base)
        return expected is not None and _ath_number(value) == expected

    rollback_disabled = _ath_rollback_disabled(flat)

    def inactive_rollback(name: str) -> bool:
        base = _ath_name(name)
        return rollback_disabled and (base == "Rollback" or base.startswith("Rollback."))

    def live(block: _RawBlock) -> bool:
        # An empty block, or one whose members are all parked, applies nothing.
        return any(not key.startswith("_") for key in block.items) or any(
            entry.strip() and not entry.strip().startswith(("_", ";", "#"))
            for entry in (*block.entries, *block.lines)
        )

    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(written: str, reason: str) -> None:
        if written not in seen:
            seen.add(written)
            found.append((written, reason))

    # `Mesh = { ... }` and `Source = { ... }` blocks are ATH's section spelling
    # of the dotted keys.
    dotted = dict(flat)
    for section in ("Mesh", "Source"):
        if section in blocks:
            for key, value in blocks[section].items.items():
                dotted.setdefault(f"{section}.{key}", value)
    for key, value in dotted.items():
        if inactive_rollback(key):
            continue
        reason = _ath_unsupported_reason(key)
        if reason is not None:
            if not noop(_ath_name(key), value):
                add(key, reason)
        elif key.startswith(_ENCLOSURE_FLAT_PREFIX):
            member = key[len(_ENCLOSURE_FLAT_PREFIX):]
            if member not in _ATH_ENCLOSURE_MEMBERS and not member.startswith("_"):
                add(key, _UNSUPPORTED_ATH_REASONS.get(
                    member, "this enclosure member is not implemented; WG reads Depth, "
                    "EdgeRadius, EdgeType, FrontResolution, BackResolution and Spacing"))
        elif key == "Mesh.RearShape" and _ath_number(value) not in {1.0, 3.0}:
            add(key, f"Mesh.RearShape = {value} is not supported; only the full rear (1) and Opus rear (3) are implemented")
    for name, block in blocks.items():
        if name == "Mesh.Roundover":
            continue
        if inactive_rollback(name):
            continue
        reason = _ath_unsupported_reason(name)
        if reason is not None:
            if live(block):
                add(f"{_ath_name(name)} = {{...}}", reason)
            continue
        base = _ath_name(name)
        if base == "Mesh.Enclosure":
            for key in block.items:
                if key.startswith("_") or key in _ATH_ENCLOSURE_MEMBERS:
                    continue
                add(f"Mesh.Enclosure.{key}", _UNSUPPORTED_ATH_REASONS.get(
                    key, "this enclosure member is not implemented; WG reads Depth, "
                    "EdgeRadius, EdgeType, FrontResolution, BackResolution and Spacing"))
        elif base in ("OSSE", "R-OSSE", "ROSSE"):
            for key, value in block.items.items():
                inner = _ath_unsupported_reason(key)
                if inner is None and base != "OSSE":
                    inner = _ATH_ROSSE_BLOCK_ONLY.get(key)
                if inner is not None and not noop(_ath_name(key), value) and not inactive_rollback(key):
                    add(f"{base}.{key}", inner)
        # A named in-block child (a block opened inside this one) never reaches
        # `items`; the lexer already refuses nesting.
    # The selected profile block's Throat.Profile wins over the top-level one.
    selected_items = blocks[block_name].items if block_name is not None else {}
    profile = selected_items.get("Throat.Profile", flat.get("Throat.Profile"))
    if profile is not None and _ath_number(profile) != 1.0:
        add("Throat.Profile", f"Throat.Profile = {profile} is not measured against ATH; only the "
            "OS-SE profile (1) is")
    if formula == "R-OSSE" and block_name is not None:
        for key in blocks[block_name].items:
            if key.startswith("_") or key in _ATH_ROSSE_MEMBERS:
                continue
            if _ath_unsupported_reason(key) is None:
                add(f"{block_name}.{key}", "this member has not been measured against ATH's "
                    "R-OSSE output, so it cannot be imported without risking a different horn")
    if found:
        details = "; ".join(f"{written} ({reason})" for written, reason in found)
        raise TextConfigError(
            f"unsupported item(s) in ATH config: {details}. ATH applies these and this "
            "importer would drop them, building a different model than the file "
            "describes. Remove them, or prefix a name with '_' to disable it."
        )


# Unread ATH items WG states in the open report instead of refusing: ATH ignores
# them for geometry, or they only shape ABEC's subdomain interfaces, which a WG
# solve never builds. Everything else WG does not read is refused by name.
_ATH_IGNORED_WITH_NOTE = {
    "Mesh.SubdomainSlices": "it only places ABEC subdomain interfaces, which a WG solve does not build",
    "Mesh.InterfaceOffset": "it only places ABEC subdomain interfaces, which a WG solve does not build",
    "Mesh.InterfaceResolution": "it only sizes ABEC subdomain interfaces, which a WG solve does not build",
}
# ATH mesh-output switches. The mesher ignores them too; listed here so a WG
# paired with a mesher that predates that change still accepts them.
_ATH_MESH_OUTPUT_KEYS = frozenset({"Mesh.Algorithm", "Mesh.Binary", "Mesh.MshFileVersion", "Mesh.Wireframe"})
# Top-level members that ATH reads only inside the R-OSSE block.
_ATH_BLOCK_ONLY_AT_TOP = frozenset({"arcterm", "trunc"})
_ATH_NAMESPACES_IGNORING_UNKNOWN = ("Morph.", "MORPH.", "GCurve.", "GCURVE.")
_ATH_UNREAD_NOOP_VALUES = {"Rot": 0.0}
# Blocks WG reads (or ATH ignores) in some layout; a profile block beside the
# selected one, or a Mesh/Source section, is accounted for elsewhere.
_ATH_READ_BLOCKS = frozenset(
    {"OSSE", "R-OSSE", "ROSSE", "Mesh", "Source", "Mesh.Enclosure", "Mesh.Roundover", "Morph", "MORPH", "GCurve", "GCURVE"}
)


def _refuse_unread_ath_items(
    extra_keys: Mapping[str, str],
    extra_blocks: Mapping[str, Any],
    ignored: list[IgnoredSetting],
) -> None:
    """Refuse every ATH item that survives only as passthrough, by name.

    What the importer neither builds nor knows to be harmless used to import as
    a simpler horn: unknown keys, typos and items the mesher builds but WG does
    not translate. The harmless set is the mesher's own ignore list (output,
    project and solver items), so the two importers cannot drift; reasons and
    near-miss hints come from the mesher too.
    """

    from hornlab_mesher import config_parser as mesher

    def parked(name: str) -> bool:
        return any(part.startswith("_") for part in name.split(":", 1)[0].split("."))

    # Keys WG reads in some layout. Left over here they are keys ATH ignores in
    # this one (r0 beside an R-OSSE block, the unselected profile block...), so
    # they are not unknown. `Rot` is read only beside OSSE; leftover it is
    # judged below.
    read = ({*_COMMON_MAP, *_OSSE_MAP, *_ATH_ROSSE_MEMBERS, "Coverage.Angle", "Throat.Diameter",
             "Throat.Angle", "Length", "Term.n", "Term.s", "Term.k", "Term.q", "OS.k", "OS.h", "n", "s", "h"}
            - {"Rot"})
    candidates = sorted(read)
    found: list[tuple[str, str | None]] = []
    for key, value in extra_keys.items():
        if parked(key) or mesher._ath_item_ignored(key, block=False) or key in _ATH_MESH_OUTPUT_KEYS:
            continue
        # Judged by the named pass (`_refuse_unsupported_ath_items`), which has
        # already accepted these at their no-op values.
        if _ath_unsupported_reason(key) is not None or key in ("Mesh.RearShape", "Throat.Profile"):
            continue
        if key in read:
            continue
        if key.startswith(_ATH_NAMESPACES_IGNORING_UNKNOWN):
            ignored.append(IgnoredSetting(
                key=key, value=value,
                note=f"ATH has no {key} key and ignores it, so it has no effect here either.",
            ))
            continue
        if key in _ATH_IGNORED_WITH_NOTE:
            ignored.append(IgnoredSetting(
                key=key, value=value, note=f"{key} is not applied: {_ATH_IGNORED_WITH_NOTE[key]}.",
            ))
            continue
        if key in _ATH_BLOCK_ONLY_AT_TOP:
            ignored.append(IgnoredSetting(
                key=key, value=value,
                note=f"ATH reads {key} only inside the R-OSSE block, so it has no effect at top level.",
            ))
            continue
        if key in _ATH_UNREAD_NOOP_VALUES and _ath_number(value) == _ATH_UNREAD_NOOP_VALUES[key]:
            continue
        reason = (
            _UNSUPPORTED_ATH_REASONS.get(key)
            or mesher._UNSUPPORTED_ATH_REASONS.get(key)
            or mesher._ath_refusal_reason(key, candidates)
            or "WG does not read this key and ATH may apply it"
        )
        found.append((key, reason))
    for name in extra_blocks:
        base = name.split(":", 1)[0].strip()
        if parked(name) or mesher._ath_item_ignored(name, block=True):
            continue
        if (base.startswith(_ATH_NAMESPACES_IGNORING_UNKNOWN) or base in _ATH_READ_BLOCKS
                or _ath_unsupported_reason(base) is not None or base.startswith("WG.")):
            continue
        reason = (
            _UNSUPPORTED_ATH_REASONS.get(base)
            or mesher._UNSUPPORTED_ATH_REASONS.get(base)
            or "WG does not read this block and ATH may apply it"
        )
        found.append((f"{base} = {{...}}", reason))
    if found:
        details = "; ".join(f"{written} ({reason})" for written, reason in found)
        raise TextConfigError(
            f"unsupported item(s) in ATH config: {details}. ATH applies or would reject "
            "these and this importer does not read them, building a different model than "
            "the file describes. Remove them, or prefix a name with '_' to disable it."
        )


def _build_payload(
    flat_source: Mapping[str, str],
    blocks: Mapping[str, _RawBlock],
    *,
    dialect: Literal["mwg", "ath"],
    ignored_profile: list[IgnoredSetting],
    refuse_unsupported: bool = False,
    v2: bool = False,
) -> dict[str, Any]:
    from .throat_stretch import coefficient, text_number, validate_composition

    flat = dict(flat_source)
    formula = _formula(flat, blocks)
    # Validate every supplied occurrence, including sections ATH ignores.
    for items in (flat, *(block.items for block in blocks.values())):
        for key in ("s1", "s2"):
            if key in items:
                try:
                    coefficient(text_number(items[key]), key)
                except ValueError as exc:
                    raise TextConfigError(str(exc)) from exc
    block_name = _profile_block_name(formula, blocks)
    selected = blocks[block_name].items if block_name is not None else flat
    if dialect == "ath" and not selected:
        # An empty ATH selector leaves the formula controls at top level.
        selected = flat
    stretch = {key: text_number(selected[key]) for key in ("s1", "s2") if key in selected}
    active_stretch = stretch.get("s1", 0) != 0 and stretch.get("s2", 0) != 0
    if block_name is not None and formula in {"OSSE", "R-OSSE"} and (active_stretch or dialect == "ath"):
        in_block = [key for key in ("Throat.Ext.Length", "Throat.Ext.Angle", "Slot.Length") if key in blocks[block_name].items]
        if in_block:
            raise TextConfigError(
                f"{', '.join(in_block)} must be top-level keys — ATH ignores them inside "
                f"the {formula if dialect == 'ath' else block_name} block; move them out of the block"
            )
    if formula not in {"OSSE", "R-OSSE"} and any(text_number(items[key]) != 0 for items in (flat, *(block.items for block in blocks.values())) for key in ("s1", "s2") if key in items):
        message = ("OSSE/R-OSSE shape keys are not valid with formula ICW" if formula == "ICW"
                   else f"formula {formula} does not accept OSSE/R-OSSE profile coefficient keys")
        raise TextConfigError(message)
    if dialect == "ath" and formula == "OSSE" and active_stretch and not any(key in selected for key in ("L", "Length")):
        raise TextConfigError("ATH OSSE text configs must set Length")
    refusal_keys = {*flat, *selected, *blocks} if dialect == "ath" and active_stretch else set()
    multi_source = sorted(key for key in refusal_keys if key == "Source.Contours"
                          or key.startswith(("LFSource", "Source.Velocity.")))
    if multi_source:
        raise TextConfigError(
            "multi-source ATH configs are not supported by this mesher "
            f"(saw {', '.join(multi_source)}); only the single cap/disc throat source is implemented"
        )
    if active_stretch:
        throat_profile = selected.get("Throat.Profile", flat.get("Throat.Profile"))
        if throat_profile is not None and text_number(throat_profile) != 1:
            raise TextConfigError(f"Throat.Profile = {text_number(throat_profile)} is not supported; only the OS-SE profile (1) is implemented")
    rollback = sorted(key for key in refusal_keys if key == "Rollback" or key.startswith("Rollback."))
    if rollback and not _ath_rollback_disabled(flat):
        raise TextConfigError(f"Rollback is not supported by this mesher (saw {', '.join(rollback)})")
    if dialect == "ath" and refuse_unsupported:
        _refuse_unsupported_ath_items(flat, blocks, formula=formula, block_name=block_name)
    composition = {**stretch}
    for key in ("Throat.Ext.Length", "Throat.Ext.Angle", "Slot.Length"):
        if key in flat:
            composition[key] = text_number(flat[key])
    # V1/native retain block Rot precedence. V2 ignores it. R-OSSE top-level
    # Rot participates in refusal even though it is not a profile field.
    if "Rot" in selected and formula == "OSSE" and not v2:
        composition["Rot"] = text_number(selected["Rot"])
    elif "Rot" in flat:
        composition["Rot"] = text_number(flat["Rot"])
    guide_items: dict[str, str] = {}
    if active_stretch:
        # Match the mesher's namespace and block precedence exactly.
        for prefix in ("GCurve.", "GCURVE."):
            guide_items.update({key[len(prefix):]: value for key, value in flat.items() if key.startswith(prefix)})
        for name in ("GCurve", "GCURVE"):
            if name in blocks:
                guide_items.update(blocks[name].items)
        for key, value in guide_items.items():
            name = "GCurve." + key
            if name in _OSSE_MAP:
                composition[name] = text_number(value)
    try:
        validate_composition(composition, formula, length_supplied="Length" in flat)
    except ValueError as exc:
        raise TextConfigError(str(exc)) from exc
    if active_stretch and formula == "R-OSSE" and all(composition.get(k, 0) != 0 for k in ("GCurve.Type", "GCurve.Width")):
        raise TextConfigError("throat stretch geometry is invalid: guiding curves are only supported with formula OSSE")
    ath_radius = None
    if dialect == "ath" and formula in {"OSSE", "R-OSSE"} and "Throat.Diameter" in selected:
        # Reuse the mesher's bounded numeric-expression evaluator. Validate
        # the selected diameter even when an explicit r0 owns the radius.
        from hornlab_mesher.profile_common import eval_param

        diameter = text_number(selected["Throat.Diameter"])
        try:
            if isinstance(diameter, str):
                tree = ast.parse(diameter.replace("^", "**"), mode="eval")
                if any(isinstance(node, ast.Name) and node.id == "p" for node in ast.walk(tree)):
                    raise ValueError("azimuth-varying diameter is unsupported")
            ath_radius = eval_param(diameter) / 2.0
        except (SyntaxError, TypeError, ValueError, OverflowError) as exc:
            raise TextConfigError(
                f"Throat.Diameter must be a positive finite number; got {diameter!r}. "
                "Use a numeric value or constant numeric expression in millimetres; "
                "azimuth-varying diameter expressions are not supported."
            ) from exc
        if not math.isfinite(ath_radius) or ath_radius <= 0:
            raise TextConfigError(
                f"Throat.Diameter must be a positive finite number that yields a positive radius; got {diameter!r}"
            )
    # The mesher maps aliases in this order, regardless of their source order.
    profile_aliases = (
        ("r0", "r0"), ("a", "a"), ("Coverage.Angle", "a"),
        ("a0", "a0"), ("Throat.Angle", "a0"),
        ("k", "k"), ("OS.k", "k"), ("Term.k", "k"), ("q", "q"), ("Term.q", "q"),
    )
    if formula == "OSSE":
        profile_aliases += (
            ("L", "L"), ("Length", "L"), ("n", "n"), ("Term.n", "n"),
            ("s", "s"), ("Term.s", "s"), ("h", "h"), ("OS.h", "h"), ("Rot", "rotation"),
        )
    elif formula == "R-OSSE":
        profile_aliases += tuple((key, key) for key in ("R", "m", "b", "r", "tmax"))
    active_aliases = dict(profile_aliases) if active_stretch else {}
    consumed_blocks = _legacy_sections(flat, blocks)
    consumed_blocks.add("CadLink")
    consumed_keys: set[str] = set()
    if formula == "FREEFORM":
        payload = _freeform_payload(flat, blocks)
        consumed_keys.update(key for key in flat if key in _FREEFORM_FLAT_ITEMS)
        consumed_blocks.update(key for key in blocks if key in _FREEFORM_BLOCKS)
    else:
        payload = {"formula": formula}
        formula_block = blocks.get(block_name) if block_name is not None else None
        if formula_block:
            consumed_blocks.add(block_name)
            for key, value in formula_block.items.items():
                if v2 and formula == "OSSE" and key == "Rot":
                    ignored_profile.append(IgnoredSetting(
                        key="OSSE.Rot", value=value,
                        note="ATH ignores OSSE.Rot; use top-level Rot.",
                    ))
                    continue
                # Active ATH profiles consume only the mesher's profile keys.
                # Global Scale is top-level; R-OSSE Rot/Length are ignored in
                # the block, even though their top-level copies can refuse.
                if dialect == "ath" and active_stretch and (
                    key == "Scale" or (formula == "R-OSSE" and key not in {
                        *active_aliases, "s1", "s2", "Throat.Diameter", "Throat.Profile",
                    })
                ):
                    note = f"ATH ignores {key} inside the {block_name} profile block. "
                    note += ("Global Scale is read only at top level." if key == "Scale" else
                             f"ATH does not read it from the block, so it has no effect "
                             f"on the imported geometry; {key} is not applied.")
                    ignored_profile.append(IgnoredSetting(
                        key=f"{block_name}.{key}", value=value, note=note,
                    ))
                elif key == "Scale":
                    payload["scale"] = value
                elif key in ("s1", "s2"):
                    if formula in {"OSSE", "R-OSSE"}:
                        payload[key] = text_number(value)
                elif active_stretch and key in active_aliases:
                    payload[active_aliases[key]] = value
                elif formula in {"OSSE", "R-OSSE"} and key == "Throat.Diameter":
                    if dialect == "ath":
                        # Selected ATH radius precedence is handled below.
                        continue
                    elif "r0" not in formula_block.items:
                        payload["r0"] = _numeric_or_expression_divide_by_two(value)
                elif dialect == "ath" and active_stretch and formula == "R-OSSE" and key == "Throat.Profile":
                    # Already validated above; not an R-OSSE profile field.
                    continue
                elif formula == "OSSE" and key == "Term.k":
                    payload["k"] = value
                elif formula == "OSSE" and key in _OSSE_MAP:
                    _put(payload, _OSSE_MAP[key], value)
                else:
                    payload[key] = value

    if active_stretch or v2:
        for key, target in profile_aliases:
            if key in selected and not (v2 and key == "Rot"):
                payload[target] = selected[key]
                if v2 and selected is flat:
                    consumed_keys.add(key)
    if formula in {"OSSE", "R-OSSE"}:
        payload.update(stretch)
    consumed_keys.update(key for key in ("s1", "s2") if key in flat)

    for key, path in _COMMON_MAP.items():
        if key in flat:
            _put(payload, path, flat[key])
            consumed_keys.add(key)

    if formula == "OSSE":
        for key, path in _OSSE_MAP.items():
            if key in flat:
                # A populated ATH OSSE block owns the formula parameters;
                # Rot alone has the mesher's explicit top-level fallback.
                block = blocks.get("OSSE")
                if not block or not block.items or (not active_stretch and not v2) or key.startswith("GCurve.") or key.startswith("CircArc.") or (key == "Throat.Profile" and key not in block.items):
                    _put(payload, path, flat[key])
                elif key == "Rot" and (v2 or "Rot" not in block.items):
                    _put(payload, path, flat[key])
                consumed_keys.add(key)
        if dialect != "ath" and "Throat.Diameter" in flat and (not active_stretch or (block_name is None and "r0" not in selected)):
            payload["r0"] = _numeric_or_expression_divide_by_two(flat["Throat.Diameter"])
            consumed_keys.add("Throat.Diameter")
        if dialect == "ath":
            # ATH's own defaults for OSSE keys the file may omit (Ath 4.8.2 User
            # Guide 4.1.1), matching hornlab_mesher.config_parser. Leaving them
            # unset instead falls through to the mesher's native-config defaults
            # (a0 = 15.5, s = 0), which silently rebuilds the throat at a
            # different opening angle -- and with it the auto source cap, whose
            # radius is r0/sin(a0). ATH's Throat.Angle = 0 makes that radius
            # infinite, i.e. a flat source; 15.5 domes it by ~1.7 mm.
            # ATH's binary defaults an omitted Term.s to 0.5, not the guide's
            # 0.7: an explicit 0.5 gives byte-identical output (measured on
            # V2025-12 and V2026-09a), and 0.7 widens the mouth by a fifth.
            payload.setdefault("a0", 0)
            payload.setdefault("s", 0.5)
            if v2:
                payload.setdefault("a", 45)

    if dialect == "ath" and formula in {"OSSE", "R-OSSE"}:
        if v2 and formula == "OSSE" and block_name and "r0" in blocks[block_name].items:
            if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", blocks[block_name].items["r0"].strip()):
                raise TextConfigError("OSSE.r0 must be a literal number; ATH reads only a leading number from expressions")
        if formula == "R-OSSE":
            # Selected R-OSSE controls own the profile even without active
            # stretch, including flat controls beside an empty selector.
            for key, target in profile_aliases:
                if key in selected:
                    payload[target] = selected[key]
                    if selected is flat:
                        consumed_keys.add(key)
        if "r0" in selected:
            payload["r0"] = selected["r0"]
        elif ath_radius is not None:
            payload["r0"] = ath_radius
        elif active_stretch:
            # The raw ATH radius default participates in global Scale.
            payload["r0"] = 12.7
        if "Throat.Diameter" in flat:
            consumed_keys.add("Throat.Diameter")
        if v2 and formula == "R-OSSE":
            for key, value in {"R": 200, "a": 40, "a0": 0, "r": .25, "m": .9, "b": .4, "q": 3.5}.items():
                payload.setdefault(key, value)
            target = payload.get("morph", {}).get("target_shape")
            if target is not None and _ath_number(str(target)) != 0:
                raise TextConfigError("R-OSSE Morph.TargetShape is unsupported: ATH changes the radial profile")

    if dialect == "ath" and formula != "FREEFORM":
        # ATH's Length is the nominal axial depth of the whole device, so
        # Slot.Length is carved OUT of it; the application's own default treats
        # Length as the flare alone and adds the slot on top. Without this an
        # ATH import with a slot is built too long, and an azimuthally varying
        # Slot.Length -- ATH's own m2-clone uses 45 - 42*sin(2*p)^4 -- tilts the
        # mouth out of plane, which an infinite-baffle build rejects outright.
        # hornlab_mesher.config_parser stamps the same mode on its ATH path.
        payload.setdefault("length_mode", "total")

    if active_stretch and formula == "OSSE":
        for key, value in guide_items.items():
            name = "GCurve." + key
            if name in _OSSE_MAP:
                _put(payload, _OSSE_MAP[name], value)
        consumed_blocks.update(name for name in ("GCurve", "GCURVE") if name in blocks)
        consumed_keys.update(key for key in flat if key.startswith(("GCurve.", "GCURVE.")))

    enclosure = _enclosure(blocks, flat if dialect == "ath" and (refuse_unsupported or v2) else None, v2=v2)
    if enclosure is not None:
        payload["enclosure"] = enclosure
        consumed_blocks.add("Mesh.Enclosure")
        if dialect == "ath" and (refuse_unsupported or v2):
            consumed_keys.update(
                key for key in flat
                if key.startswith(_ENCLOSURE_FLAT_PREFIX)
                and key[len(_ENCLOSURE_FLAT_PREFIX):] in _ATH_ENCLOSURE_MEMBERS
            )

    if v2:
        mesh = payload.setdefault("mesh", {})
        for key, target in (("Mesh.RearShape", "ath_rear_shape"),
                            ("Mesh.RearShape.FrontOffset", "ath_rear_front_offset"),
                            ("Mesh.RearShape.Step", "ath_rear_step")):
            if key in flat:
                mesh[target] = _ath_number(flat[key])
                if mesh[target] is None:
                    raise TextConfigError(f"{key} must be a finite number")
                consumed_keys.add(key)
        if "Mesh.Roundover" in blocks:
            block = blocks["Mesh.Roundover"]
            unknown = [key for key in block.items if key not in {"Radius", "Segments"} and not key.startswith("_")]
            if unknown or block.lines:
                raise TextConfigError("unsupported Mesh.Roundover member(s): " + ", ".join(unknown or block.lines))
            mesh["ath_roundover"] = {"radius": block.items.get("Radius"), "segments": block.items.get("Segments", 5)}
            consumed_blocks.add("Mesh.Roundover")
        if "NAC" in flat:
            try:
                number = Decimal(flat["NAC"])
                if not number.is_finite() or number != number.to_integral_value():
                    raise ValueError("expected an integer")
                payload["ath_nac"] = int(number)
            except (ValueError, InvalidOperation) as exc:
                raise TextConfigError(f"NAC must be a finite integer, got {flat['NAC']!r}") from exc
            payload["ath_nac_present"] = True
            consumed_keys.add("NAC")
            ignored_profile.append(IgnoredSetting(key="NAC", value=flat["NAC"], note="NAC presence preserves Mesh.AngularSegments without the CornerSegments addition; its value only controls ATH ring-spline splitting and is ignored."))
        if mesh.get("ath_rear_shape") == 3:
            mesh.setdefault("ath_rear_front_offset", 40)
            mesh.setdefault("ath_rear_step", 25)

    for key, target in (("_athAxialSamplingRule", "ath_axial_sampling_rule"), ("_athNacPresent", "ath_nac_present")):
        if key in flat:
            payload[target] = flat[key]
            consumed_keys.add(key)

    payload["extra_keys"] = {key: value for key, value in flat.items() if key not in consumed_keys}
    payload["extra_blocks"] = {
        name: ConfigBlock(
            items=block.items,
            lines=block.lines,
            comments=block.comments,
            entries=block.entries,
        ).model_dump()
        for name, block in blocks.items()
        if name not in consumed_blocks
    }
    if dialect == "ath" and refuse_unsupported:
        _refuse_unread_ath_items(payload["extra_keys"], payload["extra_blocks"], ignored_profile)
    return payload


def _mesher_validation_text(
    flat: Mapping[str, str], blocks: Mapping[str, _RawBlock], comments: list[str],
    payload: Mapping[str, Any], ignored: list[IgnoredSetting],
) -> str:
    """Keep ATH profile precedence while parking WG's nongeometry extensions.

    WG accepts evaluated no-op switches, inactive enclosure preconfiguration,
    and solve metadata that the mesher's stricter text reader does not read.
    Normalize only those already checked cases before its geometry validation.
    """
    ignored_names = {item.key for item in ignored}
    rollback_off = _ath_rollback_disabled(flat)

    def items(source: Mapping[str, str], prefix: str = "") -> list[str]:
        rows = []
        for key, value in source.items():
            name = prefix + key
            if (name in ignored_names and name not in {"OSSE.Rot", "NAC"}) or name.startswith("WG."):
                continue
            if rollback_off and (name == "Rollback" or name.startswith("Rollback.")):
                continue
            if name in _ATH_NOOP_VALUES and _ath_number(value) == _ATH_NOOP_VALUES[name]:
                continue
            if name in {"Mesh.RearShape", "Throat.Profile", "OSSE.Throat.Profile"}:
                number = _ath_number(value)
                if number is not None:
                    value = str(number)
            if name == "Rot" and _ath_number(value) == 0:
                value = "0"
            rows.append(f"{key} = {value}")
        return rows

    rows = list(comments)
    rows.extend(items(flat))
    enclosure = payload.get("enclosure")
    inactive_enclosure = enclosure is not None and _ath_number(str(enclosure.get("depth", "0"))) == 0
    if inactive_enclosure:
        rows = [row for row in rows if not row.startswith(_ENCLOSURE_FLAT_PREFIX)]
        if payload.get("simulation", {}).get("sim_type") is None:
            rows.append("ABEC.SimType = 2")
    for name, block in blocks.items():
        if name.startswith("WG.") or (inactive_enclosure and name == "Mesh.Enclosure"):
            continue
        if rollback_off and (name == "Rollback" or name.startswith("Rollback.")):
            continue
        if _ath_unsupported_reason(name) is not None and not any(
            not key.startswith("_") for key in block.items
        ) and not block.lines:
            continue
        rows.extend([f"{name} = {{", *items(block.items, name + "."), *block.lines, "}"])
    return "\n".join(rows)


def parse(text: str, *, migrate: bool = True) -> ParsedDesign:
    """Parse and validate a v1 text design, preserving raw expressions/extras.

    Dialect sniffing exactly mirrors ``src/geometry/params.js:65-67`` as called
    by ``src/modules/design/useCases.js:33-35``.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    repeated: list[tuple[str, _RawBlock]] = []
    flat, blocks, comments, raw_values = _lex(text.removeprefix("\ufeff"), repeated)
    for comment in comments:
        for match in _DESIGN_FORMAT_STAMP.finditer(comment):
            version = int(match.group(1))
            if version > _DESIGN_FORMAT:
                raise TextConfigError(
                    f"unsupported design format {version}; "
                    f"this application supports up to {_DESIGN_FORMAT}"
                )
    from server.cadlink.identity import CadLink

    cadlink: CadLink | None = None
    if raw_cadlink := blocks.get("CadLink"):
        try:
            cadlink = CadLink.from_block(
                ConfigBlock(
                    items=raw_cadlink.items,
                    lines=raw_cadlink.lines,
                    comments=raw_cadlink.comments,
                    entries=raw_cadlink.entries,
                )
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise TextConfigError(f"invalid CadLink block: {exc}") from exc
    dialect: Literal["mwg", "ath"] = "mwg" if _MWG_SNIFF.search(text) else "ath"
    if dialect == "ath" and repeated and not any(_GEOMETRY_STAMP.match(c) for c in comments):
        from hornlab_mesher.config_parser import _ath_item_ignored

        # Retain earlier copies so an empty final Mesh cannot hide populated
        # members that ATH would merge. Only entirely empty Mesh copies are safe.
        populated_mesh = any(block.items or block.lines for name, block in repeated if name == "Mesh")
        repeated = [
            (name, block) for name, block in repeated
            if not _ath_item_ignored(name, block=True)
            and not (name == "Mesh" and not populated_mesh
                     and not blocks[name].items and not blocks[name].lines)
        ]
    if dialect == "ath" and repeated and not any(_GEOMETRY_STAMP.match(c) for c in comments):
        # ATH merges a repeated block member by member; this parser never did,
        # so the second copy silently replaced the first. WG's own files keep
        # passthrough blocks verbatim, repeats included.
        raise TextConfigError(
            f"block {repeated[0][0]!r} appears more than once; ATH merges repeated "
            "blocks member by member and this importer does not. Combine "
            "the copies into one block, or prefix a copy with '_' to park it"
        )
    ignored_profile: list[IgnoredSetting] = []
    versions = {match.group(1) for comment in comments if (match := _GEOMETRY_STAMP.match(comment))}
    if len(versions) > 1:
        raise TextConfigError("conflicting geometry interpretation stamps")
    version = next(iter(versions), None)
    if version is not None and version not in {*TEXT_IMPORT_VERSIONS, NATIVE_GEOMETRY_VERSION}:
        raise TextConfigError(f"unsupported geometry interpretation {version!r}")
    geometry_dialect = "mwg" if version == NATIVE_GEOMETRY_VERSION else "ath" if version in TEXT_IMPORT_VERSIONS else dialect
    import_version = version if version in TEXT_IMPORT_VERSIONS else TEXT_IMPORT_VERSION if geometry_dialect == "ath" else None
    # Refuse only files that are ATH by their own text and carry no WG stamp. A WG-saved file that
    # carries the text-import geometry stamp is read with ATH geometry but keeps
    # native passthrough semantics, so it never becomes unopenable.
    payload = _build_payload(
        flat, blocks, dialect=geometry_dialect, ignored_profile=ignored_profile,
        refuse_unsupported=dialect == "ath" and version is None,
        v2=import_version == TEXT_IMPORT_VERSION,
    )
    if import_version is not None and payload["formula"] != "FREEFORM":
        payload["text_import_version"] = import_version
        if version is None and dialect == "ath":
            ignored_profile.extend(IgnoredSetting(*entry) for entry in apply_ath_import_defaults(payload))
    if import_version == TEXT_IMPORT_VERSION:
        from hornlab_mesher.text_import import ath_axial_sampling_rule
        try:
            rule, _echo = ath_axial_sampling_rule(text)
        except ValueError as exc:
            raise TextConfigError(str(exc)) from exc
        payload.setdefault("ath_axial_sampling_rule", rule)
        payload.setdefault("ath_nac_present", False)
    applications: list[MigrationApplication] = []
    if migrate:
        try:
            payload, applications = apply_migrations(payload)
        except (OverflowError, ValueError) as exc:
            raise TextConfigError(str(exc)) from exc
    mesh_payload = payload.get("mesh", {})
    if (payload.get("text_import_version") == TEXT_IMPORT_VERSION and version is None and dialect == "ath"
            or mesh_payload.get("ath_roundover") is not None or mesh_payload.get("ath_rear_shape") == 3):
        # The mesher owns the bounded combinations and their named diagnostics.
        # This is config validation only; no surfaces or mesh jobs are built.
        from hornlab_mesher.config_parser import parse_text_config
        from hornlab_mesher.config_builder import build_geometry_params
        try:
            validation_text = _mesher_validation_text(flat, blocks, comments, payload, ignored_profile)
            build_geometry_params(parse_text_config(validation_text))
        except ValueError as exc:
            raise TextConfigError(str(exc)) from exc
    try:
        design = DesignConfig.model_validate(payload)
    except ValidationError as exc:
        raise TextConfigError(str(exc)) from exc
    block_name = _profile_block_name(design.formula, blocks)
    return ParsedDesign(
        design=design,
        dialect=dialect,
        migrations=applications,
        comments=comments,
        raw_values=raw_values,
        cadlink=cadlink,
        source_text=text,
        ignored_profile=ignored_profile + [
            IgnoredSetting(
                key=f"{name}.{key}" if name else key,
                value=value,
                note=f"ATH reads {key} from the {block_name or design.formula} profile section. This value is ignored; move it into that section to use it.",
            )
            for name, items in [("", flat), *((name, block.items) for name, block in blocks.items())]
            for key, value in items.items()
            if key in {"s1", "s2"} and (
                (name == "" and block_name is not None and (
                    geometry_dialect != "ath" or bool(blocks[block_name].items)
                )) or (name != "" and name != block_name)
            )
        ],
    )


def _text(value: Expr | tuple[Expr, Expr, Expr, Expr] | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, tuple):
        return ",".join(item.text() for item in value)
    return value.text() if isinstance(value, Expr) else str(value)


def _sim_type_text(value: str | None) -> str | None:
    return {"infinite-baffle": "1", "freestanding": "2"}.get(value, value)


def _line(
    lines: list[str],
    key: str,
    value: Expr | tuple[Expr, Expr, Expr, Expr] | str | None,
) -> None:
    if value is not None:
        text = _text(value)
        _validate_serialized_key(key)
        _validate_serialized_value(text, key=key)
        lines.append(f"{key} = {text}")


def _block(
    lines: list[str],
    name: str,
    items: list[
        tuple[str, Expr | tuple[Expr, Expr, Expr, Expr] | str | None]
    ],
    rows: list[str] | None = None,
    *,
    emit_empty: bool = False,
) -> None:
    if not emit_empty and not any(value is not None for _, value in items) and not rows:
        return
    if _BLOCK_START.fullmatch(f"{name} = {{") is None:
        raise TextConfigError(f"unsafe config block name {name!r}")
    lines.append(f"{name} = {{")
    for key, value in items:
        _line(lines, key, value)
    for row in rows or []:
        _append_block_entry(lines, row, block=name)
    lines.append("}")


def _serialize_freeform(lines: list[str], config: FreeformConfig) -> None:
    profile_h, profile_v = config.profile_h, config.profile_v
    length = config.length
    throat_radius = profile_h.points[0].r if profile_h.points else None
    lines.extend(
        [
            "; FREEFORM point rows in mm: z r [angleDeg]",
            "; FREEFORM station rows: t shape [exponent|cornerRadiusMm]",
        ]
    )
    _line(lines, "Freeform.Length", length)
    _line(lines, "Freeform.ThroatRadius", throat_radius)
    _line(lines, "Freeform.ThroatAngle", profile_h.throat_angle_deg)
    _line(lines, "Freeform.InflectionPolicy", config.inflection_policy)
    for axis, profile in (("H", profile_h), ("V", profile_v)):
        _block(
            lines,
            f"Freeform.{axis}",
            [
                ("MouthRadius", profile.points[-1].r if profile.points else None),
                ("ThroatAngle", profile.throat_angle_deg),
                ("MouthAngle", profile.mouth_angle_deg),
            ],
        )
        rows = []
        for point in profile.points[1:-1]:
            point_t = point.t.constant_value()
            length_value = length.constant_value()
            assert point_t is not None and length_value is not None
            z_mm = round(point_t * length_value, 12)
            values = [_text(Expr(value=z_mm)), _text(point.r)]
            if point.angle_deg is not None:
                values.append(_text(point.angle_deg))
            rows.append(" ".join(values))
        _block(lines, f"Freeform.{axis}.Points", [], rows)
    rows = []
    for station in config.cross_sections:
        values = [_text(station.t), station.shape]
        if station.shape == "superellipse" and station.exponent is not None:
            values.append(_text(station.exponent))
        elif station.shape == "rounded_rectangle" and station.corner_radius_mm is not None:
            values.append(_text(station.corner_radius_mm))
        rows.append(" ".join(values))
    _block(lines, "Freeform.CrossSections", [], rows)


def _serialize_enclosure(lines: list[str], config: Any, *, v2: bool = False) -> None:
    if config is None:
        return
    spacing = None
    spaces = [config.space_l, config.space_t, config.space_r, config.space_b]
    if v2:
        spaces[0], spaces[2] = spaces[2], spaces[0]
    if any(value is not None for value in spaces):
        spacing = ",".join(_text(value) for value in spaces)
    _block(
        lines,
        "Mesh.Enclosure",
        [
            ("Depth", config.depth),
            ("EdgeRadius", config.edge_radius),
            ("EdgeType", config.edge_type),
            ("Spacing", spacing),
            ("FrontResolution", config.front_resolution),
            ("BackResolution", config.back_resolution),
        ],
    )


def _block_without_machine_solve_keys(name: str, block: ConfigBlock) -> ConfigBlock:
    """Drop obsolete per-machine execution choices from canonical design files."""

    if name != _WG_SOLVE_BLOCK:
        return block

    def portable_entry(entry: str) -> bool:
        assignment = _without_comment(entry)
        if "=" not in assignment:
            return True
        return assignment.split("=", 1)[0].strip() not in _MACHINE_SOLVE_KEYS

    return block.model_copy(
        update={
            "items": {
                key: value
                for key, value in block.items.items()
                if key not in _MACHINE_SOLVE_KEYS
            },
            "entries": [entry for entry in block.entries if portable_entry(entry)],
        }
    )


def _emit_block(lines: list[str], name: str, block: ConfigBlock) -> None:
    """Write one passthrough block, preferring its verbatim imported rows."""

    if _BLOCK_START.fullmatch(f"{name} = {{") is None:
        raise TextConfigError(f"unsafe config block name {name!r}")
    block = _block_without_machine_solve_keys(name, block)
    lines.append(f"{name} = {{")
    if block.entries:
        for entry in block.entries:
            _append_block_entry(lines, entry, block=name)
    else:
        for comment in block.comments:
            if not comment.lstrip().startswith(";"):
                raise TextConfigError(f"unsafe comment in block {name!r}")
            _append_block_entry(lines, comment, block=name)
        for row in block.lines:
            _append_block_entry(lines, row, block=name)
        for key, value in block.items.items():
            _line(lines, key, value)
    lines.append("}")


def _serialize_canonical(
    design: DesignConfig,
    comments: list[str] | None = None,
    cadlink: CadLink | None = None,
) -> str:
    config = design.root
    lines = [
        "; Parameter config",
        f"; Waveguide Generator design-format: {_DESIGN_FORMAT}",
        f"; {GEOMETRY_STAMP_LABEL} {config.text_import_version or NATIVE_GEOMETRY_VERSION}",
    ]
    if config.ath_axial_sampling_rule == "ath-v2025-06":
        lines.append("; Ath version V2025-06")
    for comment in comments or []:
        if comment not in lines and "Generated:" not in comment and not _GEOMETRY_STAMP.match(comment):
            if _LINE_BREAK.search(comment) is not None or not comment.lstrip().startswith(";"):
                raise TextConfigError("unsafe preserved config comment")
            lines.append(comment)
    if cadlink is not None:
        lines.extend(cadlink.block_lines())
    # The design's name leads the file.
    #
    # ``Report`` is ATH's block and stays passthrough, but WG writes its
    # ``Title`` from the design name, and a name buried under two hundred lines
    # of geometry is a name nobody reads. It is emitted here rather than with
    # the other extra blocks at the end; the trailing loop skips it.
    report = config.extra_blocks.get(_REPORT_BLOCK)
    if report is not None:
        _emit_block(lines, _REPORT_BLOCK, report)
    _line(lines, "Scale", config.scale)
    _line(lines, "_athAxialSamplingRule", config.ath_axial_sampling_rule)
    if config.ath_nac_present is not None:
        _line(lines, "_athNacPresent", "1" if config.ath_nac_present else "0")
    if config.ath_nac is not None:
        _line(lines, "NAC", str(config.ath_nac))

    if isinstance(config, FreeformConfig):
        _serialize_freeform(lines, config)
    elif isinstance(config, ROSSEConfig):
        _block(
            lines,
            "R-OSSE",
            [(key, getattr(config, key)) for key in ("R", "a", "a0", "b", "k", "m", "q", "r", "r0", "tmax", "s1", "s2")],
            emit_empty=True,
        )
    elif isinstance(config, ICWConfig):
        _block(
            lines,
            "ICW",
            [
                (key, getattr(config, key))
                for key in (
                    "r0",
                    "a",
                    "a0",
                    "k",
                    "q",
                    "L",
                    "R",
                    "coverage_angle",
                    "hold_start",
                    "hold_end",
                    "n_coeff",
                    "termination",
                    "theta1_deg",
                    "depth",
                    "curl",
                )
            ],
            emit_empty=True,
        )
    elif isinstance(config, OSSEConfig):
        # Keep old files byte-identical when stretch is absent. For stretch,
        # ATH reads coefficients and formula scalars from the OSSE block.
        stretch_items = []
        if config.s1 is not None or config.s2 is not None:
            stretch_items = [(key, getattr(config, path)) for key, path in (
                ("L", "L"), ("a", "a"), ("a0", "a0"), ("r0", "r0"),
                ("k", "k"), ("s", "s"), ("n", "n"), ("q", "q"), ("h", "h"),
                ("Rot", "rotation"), ("s1", "s1"), ("s2", "s2"),
            )]
        _block(lines, "OSSE", stretch_items, emit_empty=True)
        _line(lines, "Throat.Profile", config.throat_profile)
        _line(lines, "Throat.Ext.Angle", config.throat_ext_angle)
        _line(lines, "Throat.Ext.Length", config.throat_ext_length)
        _line(lines, "Slot.Length", config.slot_length)
        _line(lines, "Coverage.Angle", config.a)
        _line(lines, "Length", config.L)
        _line(lines, "Term.n", config.n)
        _line(lines, "Term.q", config.q)
        _line(lines, "Term.s", config.s)
        _line(lines, "Throat.Angle", config.a0)
        if config.r0 is not None:
            if config.r0.raw is not None:
                _line(lines, "Throat.Diameter", f"2*({config.r0.raw})")
            elif config.r0.value is not None:
                _line(lines, "Throat.Diameter", Expr(value=config.r0.value * 2))
        if config.throat_profile is None:
            _line(lines, "Throat.Profile", Expr(value=1))
        _line(lines, "OS.k", config.k)
        _line(lines, "OS.h", config.h)
        _line(lines, "Rot", config.rotation)
        guide = config.guiding_curve
        for key, value in (
            ("GCurve.Type", guide.curve_type),
            ("GCurve.Dist", guide.distance),
            ("GCurve.Width", guide.width),
            ("GCurve.AspectRatio", guide.aspect_ratio),
            ("GCurve.SE.n", guide.superellipse_n),
            ("GCurve.SF", guide.superformula),
            ("GCurve.SF.a", guide.sf_a),
            ("GCurve.SF.b", guide.sf_b),
            ("GCurve.SF.m1", guide.sf_m1),
            ("GCurve.SF.m2", guide.sf_m2),
            ("GCurve.SF.n1", guide.sf_n1),
            ("GCurve.SF.n2", guide.sf_n2),
            ("GCurve.SF.n3", guide.sf_n3),
            ("GCurve.Rot", guide.rotation),
            ("CircArc.Radius", config.circ_arc_radius),
            ("CircArc.TermAngle", config.circ_arc_term_angle),
        ):
            _line(lines, key, value)

    if not isinstance(config, OSSEConfig):
        _line(lines, "Throat.Ext.Angle", config.throat_ext_angle)
        _line(lines, "Throat.Ext.Length", config.throat_ext_length)
        _line(lines, "Slot.Length", config.slot_length)
    _line(lines, "Coverage.Mode", config.coverage_mode)
    _line(lines, "Length.Mode", config.length_mode)

    morph = config.morph
    for key, value in (
        ("Morph.CornerRadius", morph.corner_radius),
        ("Morph.FixedPart", morph.fixed_part),
        ("Morph.Rate", morph.rate),
        ("Morph.TargetShape", morph.target_shape),
        ("Morph.Exponent", morph.target_exponent),
        ("Morph.TargetWidth", morph.target_width),
        ("Morph.TargetHeight", morph.target_height),
        ("Morph.AllowShrinkage", morph.allow_shrinkage),
    ):
        _line(lines, key, value)
    _serialize_enclosure(lines, config.enclosure, v2=config.text_import_version == TEXT_IMPORT_VERSION)

    mesh = config.mesh
    if mesh.ath_roundover is not None:
        _block(lines, "Mesh.Roundover", [("Radius", str(mesh.ath_roundover.radius)), ("Segments", str(mesh.ath_roundover.segments))])
    for key, value in (("Mesh.RearShape", mesh.ath_rear_shape),
                       ("Mesh.RearShape.FrontOffset", mesh.ath_rear_front_offset),
                       ("Mesh.RearShape.Step", mesh.ath_rear_step)):
        if value is not None:
            _line(lines, key, str(value))
    for key, value in (
        ("Mesh.AngularSegments", mesh.angular_segments),
        ("Mesh.CornerSegments", mesh.corner_segments),
        ("Mesh.ThroatSegments", mesh.throat_segments),
        ("Mesh.LengthSegments", mesh.length_segments),
        ("Mesh.ThroatResolution", mesh.throat_resolution),
        ("Mesh.MouthResolution", mesh.mouth_resolution),
        ("Mesh.ThroatSliceDensity", mesh.throat_slice_density),
        ("Mesh.SamplingMode", mesh.sampling_mode),
        ("Mesh.ZMapPoints", mesh.z_map_points),
        ("Mesh.VerticalOffset", mesh.vertical_offset),
        ("Mesh.Quadrants", mesh.quadrants),
        ("Mesh.WallThickness", mesh.wall_thickness),
        ("Mesh.RearResolution", mesh.rear_resolution),
        ("Mesh.ApertureResolutionScale", mesh.aperture_resolution_scale),
        ("Mesh.MaxTriangles", mesh.max_triangles),
        ("Mesh.AllowLargeMesh", mesh.allow_large_mesh),
        ("Mesh.MaxEdge", mesh.max_edge),
        ("Output.STL", config.output.stl),
        ("Output.MSH", config.output.msh),
        ("Source.Shape", config.source.shape),
        ("Source.Radius", config.source.radius),
        ("Source.Curv", config.source.curvature),
        ("Source.Velocity", config.source.velocity),
        ("Source.Contours", config.source.contours),
        ("Source.VelocityConvention", config.source.velocity_convention),
        ("Simulation.F1", config.simulation.f1),
        ("Simulation.F2", config.simulation.f2),
        ("Simulation.NumFrequencies", config.simulation.num_frequencies),
        ("Simulation.SimType", _sim_type_text(config.simulation.sim_type)),
    ):
        _line(lines, key, value)

    for key, value in config.extra_keys.items():
        _line(lines, key, value)
    for name, block in config.extra_blocks.items():
        if name == _REPORT_BLOCK:
            continue
        _emit_block(lines, name, block)
    return "\n".join(lines) + "\n"


def serialize(
    design: ParsedDesign | DesignConfig, *, cadlink: CadLink | None = None
) -> str:
    """Serialize in stable v1 writer order, returning exact bytes when pristine."""

    if isinstance(design, ParsedDesign):
        if (
            design.source_text is not None
            and not any(
                item.name == "004_freeform_solved_tangent_contract"
                for item in design.migrations
            )
            and (cadlink is None or cadlink == design.cadlink)
            and _semantic_fingerprint(design.design) == design._initial_fingerprint
        ):
            return design.source_text
        return _serialize_canonical(
            design.design,
            design.comments,
            design.cadlink if cadlink is None else cadlink,
        )
    if isinstance(design, DesignConfig):
        return _serialize_canonical(design, cadlink=cadlink)
    raise TypeError("serialize expects ParsedDesign or DesignConfig")


__all__ = [
    "IgnoredSetting",
    "ParsedDesign",
    "TextConfigError",
    "parse",
    "serialize",
]
