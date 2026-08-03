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
            continue
        match = _BLOCK_START.fullmatch(line)
        if match:
            current = match.group(1)
            blocks[current] = _RawBlock()
            continue
        if line == "}":
            current = None
            continue
        if "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if value.startswith("function anonymous("):
                value, index = _collect_function(lines, index - 1, value)
            qualified = f"{current}.{key}" if current else key
            raw_values[qualified] = value
            if current is None:
                flat[key] = value
            else:
                blocks[current].items[key] = value
            continue
        if current is not None:
            blocks[current].lines.append(line)
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


def _numeric_or_expression_divide_by_two(value: str) -> Any:
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


def _point(raw: str, block_name: str, row: int) -> dict[str, Any]:
    parts = raw.split()
    if len(parts) not in (2, 3, 4):
        raise TextConfigError(f"{block_name} row {row}: expected 2, 3, or 4 numeric values")
    result = {"z": parts[0], "r": parts[1]}
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

    def profile(axis: str) -> dict[str, Any]:
        info = blocks.get(f"Freeform.{axis}", _RawBlock())
        rows = blocks.get(f"Freeform.{axis}.Points", _RawBlock()).lines
        points = [{"z": "0", "r": throat_radius}]
        points.extend(_point(row, f"Freeform.{axis}.Points", index + 1) for index, row in enumerate(rows))
        points.append({"z": length, "r": info.items.get("MouthRadius", throat_radius)})
        return {
            "points": points,
            "throat_angle_deg": flat.get("Freeform.ThroatAngle"),
            "mouth_angle_deg": info.items.get("MouthAngle"),
            "throat_tangent_scale": info.items.get("ThroatTangentScale"),
            "mouth_tangent_scale": info.items.get("MouthTangentScale"),
        }

    stations: list[dict[str, Any]] = []
    for index, row in enumerate(blocks.get("Freeform.CrossSections", _RawBlock()).lines):
        parts = row.split()
        if len(parts) not in (2, 3):
            raise TextConfigError(f"Freeform.CrossSections row {index + 1}: expected 2 or 3 values")
        station: dict[str, Any] = {"t": parts[0], "shape": parts[1]}
        if len(parts) == 3:
            if parts[2].startswith("ratio:"):
                station["corner_ratio"] = parts[2].split(":", 1)[1]
            elif parts[1] == "superellipse":
                station["exponent"] = parts[2]
            elif parts[1] == "rounded_rectangle":
                station["corner_radius_mm"] = parts[2]
        stations.append(station)
    return {
        "formula": "FREEFORM",
        "profile_h": profile("H"),
        "profile_v": profile("V"),
        "cross_sections": stations,
        "overshoot_policy": flat.get("Freeform.OvershootPolicy"),
        "inflection_policy": flat.get("Freeform.InflectionPolicy"),
    }


def _build_payload(flat_source: Mapping[str, str], blocks: Mapping[str, _RawBlock]) -> dict[str, Any]:
    flat = dict(flat_source)
    formula = _formula(flat, blocks)
    consumed_blocks = _legacy_sections(flat, blocks)
    consumed_keys: set[str] = set()
    if formula == "FREEFORM":
        payload = _freeform_payload(flat, blocks)
        consumed_keys.update(key for key in flat if key.startswith("Freeform."))
        consumed_blocks.update(key for key in blocks if key.startswith("Freeform."))
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
        name: ConfigBlock(items=block.items, lines=block.lines, comments=block.comments).model_dump()
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
        payload, applications = apply_migrations(payload)
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


def _text(value: Expr | str | None) -> str:
    if value is None:
        return ""
    return value.text() if isinstance(value, Expr) else str(value)


def _sim_type_text(value: str | None) -> str | None:
    return {"infinite-baffle": "1", "freestanding": "2"}.get(value, value)


def _line(lines: list[str], key: str, value: Expr | str | None) -> None:
    if value is not None:
        lines.append(f"{key} = {_text(value)}")


def _block(lines: list[str], name: str, items: list[tuple[str, Expr | str | None]], rows: list[str] | None = None) -> None:
    if not any(value is not None for _, value in items) and not rows:
        return
    lines.append(f"{name} = {{")
    for key, value in items:
        _line(lines, key, value)
    lines.extend(rows or [])
    lines.append("}")


def _serialize_freeform(lines: list[str], config: FreeformConfig) -> None:
    profile_h, profile_v = config.profile_h, config.profile_v
    length = profile_h.points[-1].z if profile_h.points else None
    throat_radius = profile_h.points[0].r if profile_h.points else None
    lines.extend(
        [
            "; FREEFORM point rows: z r [angleDeg [strength]]",
            "; FREEFORM station rows: t shape [exponent|cornerRadiusMm]",
        ]
    )
    _line(lines, "Freeform.Length", length)
    _line(lines, "Freeform.ThroatRadius", throat_radius)
    _line(lines, "Freeform.ThroatAngle", profile_h.throat_angle_deg)
    _line(lines, "Freeform.OvershootPolicy", config.overshoot_policy)
    _line(lines, "Freeform.InflectionPolicy", config.inflection_policy)
    for axis, profile in (("H", profile_h), ("V", profile_v)):
        _block(
            lines,
            f"Freeform.{axis}",
            [
                ("MouthRadius", profile.points[-1].r if profile.points else None),
                ("MouthAngle", profile.mouth_angle_deg),
                ("ThroatTangentScale", profile.throat_tangent_scale),
                ("MouthTangentScale", profile.mouth_tangent_scale),
            ],
        )
        rows = []
        for point in profile.points[1:-1]:
            values = [_text(point.z), _text(point.r)]
            if point.angle_deg is not None:
                values.append(_text(point.angle_deg))
            if point.strength is not None:
                values.append(_text(point.strength))
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
            diameter = None if config.r0.value is None else Expr(value=config.r0.value * 2)
            _line(lines, "Throat.Diameter", diameter or f"2*({config.r0.text()})")
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
        lines.extend(block.comments)
        lines.extend(block.lines)
        for key, value in block.items.items():
            lines.append(f"{key} = {value}")
        lines.append("}")
    return "\n".join(lines) + "\n"


def serialize(design: ParsedDesign | DesignConfig) -> str:
    """Serialize in stable v1 writer order, returning exact bytes when pristine."""

    if isinstance(design, ParsedDesign):
        if design.source_text is not None and _semantic_fingerprint(design.design) == design._initial_fingerprint:
            return design.source_text
        return _serialize_canonical(design.design, design.comments)
    if isinstance(design, DesignConfig):
        return _serialize_canonical(design)
    raise TypeError("serialize expects ParsedDesign or DesignConfig")


__all__ = ["ParsedDesign", "TextConfigError", "parse", "serialize"]
