"""Parser and v1-compatible serializer for ATH/MWG text configurations.

The lexer mirrors ``src/config/index.js:266-338``: semicolon comments, one
top-level block at a time, first-equals splitting, raw values, and passthrough
block rows.  Stable writer order mirrors ``src/export/mwgConfig.js:46-295``.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from pydantic import ValidationError

from .migrate import MigrationApplication, apply_migrations
from .schema import (
    ConfigBlock,
    DesignConfig,
    Expr,
    FreeformConfig,
    ICWConfig,
    OSSEConfig,
    ROSSEConfig,
)


_BLOCK_START = re.compile(r"^([\w.:-]+)\s*=\s*\{$")
_MWG_SNIFF = re.compile(r";\s*(?:Parameter|MWG) config", re.IGNORECASE)


class TextConfigError(ValueError):
    """A source-located syntax or design validation failure."""


@dataclass
class _RawBlock:
    items: dict[str, str] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)


def _semantic_fingerprint(design: DesignConfig) -> str:
    return json.dumps(design.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


@dataclass
class ParsedDesign:
    """A validated design plus all text-only material needed for lossless I/O."""

    design: DesignConfig
    dialect: Literal["mwg", "ath"]
    migrations: list[MigrationApplication] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    raw_values: dict[str, str] = field(default_factory=dict)
    source_text: str | None = None
    _initial_fingerprint: str = ""

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

    def semantic_data(self) -> dict[str, Any]:
        """Return the JSON/API meaning used by the corpus round-trip law."""

        return self.design.model_dump(mode="json")


def _without_comment(line: str) -> str:
    return line.split(";", 1)[0].strip()


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


def _lex(text: str) -> tuple[dict[str, str], dict[str, _RawBlock], list[str], dict[str, str]]:
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
    if "R-OSSE" in blocks:
        return "R-OSSE"
    if "ICW" in blocks:
        return "ICW"
    if "OSSE" in blocks or any(key in flat for key in ("Coverage.Angle", "Length", "Term.n")):
        return "OSSE"
    raise TextConfigError("could not find an OSSE, R-OSSE, ICW, or FREEFORM design")


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


def _enclosure(blocks: Mapping[str, _RawBlock]) -> dict[str, Any] | None:
    block = blocks.get("Mesh.Enclosure")
    if block is None:
        return None
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
        for target, value in zip(("space_l", "space_t", "space_r", "space_b"), parts):
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
    {"MouthRadius", "MouthAngle", "ThroatTangentScale", "MouthTangentScale"}
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
            "throat_angle_deg": flat.get("Freeform.ThroatAngle"),
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


def _build_payload(flat_source: Mapping[str, str], blocks: Mapping[str, _RawBlock]) -> dict[str, Any]:
    flat = dict(flat_source)
    formula = _formula(flat, blocks)
    consumed_blocks = _legacy_sections(flat, blocks)
    consumed_keys: set[str] = set()
    if formula == "FREEFORM":
        payload = _freeform_payload(flat, blocks)
        consumed_keys.update(key for key in flat if key in _FREEFORM_FLAT_ITEMS)
        consumed_blocks.update(key for key in blocks if key in _FREEFORM_BLOCKS)
    else:
        payload = {"formula": formula}
        formula_block = blocks.get(formula)
        if formula_block:
            consumed_blocks.add(formula)
            for key, value in formula_block.items.items():
                if key == "Scale":
                    payload["scale"] = value
                else:
                    payload[key] = value

    for key, path in _COMMON_MAP.items():
        if key in flat:
            _put(payload, path, flat[key])
            consumed_keys.add(key)

    if formula == "OSSE":
        for key, path in _OSSE_MAP.items():
            if key in flat:
                _put(payload, path, flat[key])
                consumed_keys.add(key)
        if "Throat.Diameter" in flat:
            payload["r0"] = _numeric_or_expression_divide_by_two(flat["Throat.Diameter"])
            consumed_keys.add("Throat.Diameter")

    enclosure = _enclosure(blocks)
    if enclosure is not None:
        payload["enclosure"] = enclosure
        consumed_blocks.add("Mesh.Enclosure")

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
    return payload


def parse(text: str, *, migrate: bool = True) -> ParsedDesign:
    """Parse and validate a v1 text design, preserving raw expressions/extras.

    Dialect sniffing exactly mirrors ``src/geometry/params.js:65-67`` as called
    by ``src/modules/design/useCases.js:33-35``.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    flat, blocks, comments, raw_values = _lex(text)
    payload = _build_payload(flat, blocks)
    applications: list[MigrationApplication] = []
    if migrate:
        try:
            payload, applications = apply_migrations(payload)
        except (OverflowError, ValueError) as exc:
            raise TextConfigError(str(exc)) from exc
    try:
        design = DesignConfig.model_validate(payload)
    except ValidationError as exc:
        raise TextConfigError(str(exc)) from exc
    return ParsedDesign(
        design=design,
        dialect="mwg" if _MWG_SNIFF.search(text) else "ath",
        migrations=applications,
        comments=comments,
        raw_values=raw_values,
        source_text=text,
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
        lines.append(f"{key} = {_text(value)}")


def _block(
    lines: list[str],
    name: str,
    items: list[
        tuple[str, Expr | tuple[Expr, Expr, Expr, Expr] | str | None]
    ],
    rows: list[str] | None = None,
) -> None:
    if not any(value is not None for _, value in items) and not rows:
        return
    lines.append(f"{name} = {{")
    for key, value in items:
        _line(lines, key, value)
    lines.extend(rows or [])
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


def _serialize_enclosure(lines: list[str], config: Any) -> None:
    if config is None:
        return
    spacing = None
    spaces = [config.space_l, config.space_t, config.space_r, config.space_b]
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


def _serialize_canonical(design: DesignConfig, comments: list[str] | None = None) -> str:
    config = design.root
    lines = ["; Parameter config", "; Waveguide Generator v2 design-format: 2"]
    for comment in comments or []:
        if comment not in lines and "Generated:" not in comment:
            lines.append(comment)
    _line(lines, "Scale", config.scale)

    if isinstance(config, FreeformConfig):
        _serialize_freeform(lines, config)
    elif isinstance(config, ROSSEConfig):
        _block(
            lines,
            "R-OSSE",
            [(key, getattr(config, key)) for key in ("R", "a", "a0", "b", "k", "m", "q", "r", "r0", "tmax")],
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
        )
    elif isinstance(config, OSSEConfig):
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
        ("Morph.TargetWidth", morph.target_width),
        ("Morph.TargetHeight", morph.target_height),
        ("Morph.AllowShrinkage", morph.allow_shrinkage),
    ):
        _line(lines, key, value)
    _serialize_enclosure(lines, config.enclosure)

    mesh = config.mesh
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
        ("Simulation.SolverMode", config.simulation.solver_mode),
    ):
        _line(lines, key, value)

    for key, value in config.extra_keys.items():
        _line(lines, key, value)
    for name, block in config.extra_blocks.items():
        lines.append(f"{name} = {{")
        if block.entries:
            lines.extend(block.entries)
        else:
            lines.extend(block.comments)
            lines.extend(block.lines)
            for key, value in block.items.items():
                lines.append(f"{key} = {value}")
        lines.append("}")
    return "\n".join(lines) + "\n"


def serialize(design: ParsedDesign | DesignConfig) -> str:
    """Serialize in stable v1 writer order, returning exact bytes when pristine."""

    if isinstance(design, ParsedDesign):
        if (
            design.source_text is not None
            and not any(
                item.name == "004_freeform_solved_tangent_contract"
                for item in design.migrations
            )
            and _semantic_fingerprint(design.design) == design._initial_fingerprint
        ):
            return design.source_text
        return _serialize_canonical(design.design, design.comments)
    if isinstance(design, DesignConfig):
        return _serialize_canonical(design)
    raise TypeError("serialize expects ParsedDesign or DesignConfig")


__all__ = ["ParsedDesign", "TextConfigError", "parse", "serialize"]
