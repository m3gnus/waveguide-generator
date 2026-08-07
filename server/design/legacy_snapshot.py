"""Convert v1 job parameter snapshots into validated v2 designs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .textcfg import ParsedDesign, TextConfigError, parse


class LegacySnapshotError(ValueError):
    """A legacy snapshot cannot be converted into an ATH design."""


_UNDEFINED = object()


def _defined(params: Mapping[str, Any], key: str) -> bool:
    """Treat JSON null like the undefined property checks in this port."""

    return key in params and params[key] is not None


# ECMA-262 StringNumericLiteral trims WhiteSpace plus LineTerminator, a set that
# neither contains nor is contained by the one ``str.strip()`` uses: JavaScript
# also trims U+FEFF, and Python also trims U+001C-U+001F.  Spelling it out keeps
# the two from disagreeing about which characters are padding.
_JS_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def _js_number(value: Any) -> float | None:
    """Return the useful part of JavaScript's Number coercion."""

    if value is _UNDEFINED:
        return math.nan
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        joined = ",".join(_js_string(item) if item is not None else "" for item in value)
        return _js_number(joined)
    if isinstance(value, str):
        stripped = value.strip(_JS_WHITESPACE)
        if not stripped:
            return 0.0
        # ``float`` is more permissive than ``Number``: it takes "inf", "nan",
        # digit separators such as "1_000", and non-ASCII decimal digits, all of
        # which JavaScript answers with NaN.  Only the exact "Infinity" spelling
        # is a JavaScript numeric literal.
        if not stripped.isascii() or "_" in stripped:
            return math.nan
        body = stripped[1:] if stripped[:1] in ("+", "-") else stripped
        if body.lower() in ("inf", "infinity", "nan"):
            if body != "Infinity":
                return math.nan
            return -math.inf if stripped[:1] == "-" else math.inf
        if stripped.lower().startswith(("0x", "0b", "0o")):
            try:
                return float(int(stripped, 0))
            except ValueError:
                return math.nan
        try:
            return float(stripped)
        except ValueError:
            return math.nan
    return math.nan


def _js_string(value: Any) -> str:
    """Spell a value as a JavaScript template interpolation would."""

    if value is _UNDEFINED:
        return "undefined"
    if value is None:
        # ``${null}`` is "null"; only a missing property spells "undefined".
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join("" if item is None else _js_string(item) for item in value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value.is_integer():
            return str(int(value))
    return str(value)


def _format_value(value: Any) -> str:
    """Port v1's formatValue helper, including its boolean spelling."""

    if value is None or value is _UNDEFINED:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join("" if item is None else _js_string(item) for item in value)
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _js_truthy(value: Any) -> bool:
    if value is _UNDEFINED or value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def _is_nonzero(value: Any) -> bool:
    """Port v1's numeric-first, string-fallback non-zero predicate."""

    if value is _UNDEFINED or value is None:
        return False
    if isinstance(value, bool):
        return value
    number = _js_number(value)
    if number is not None and math.isfinite(number):
        return number != 0
    stripped = str(value).strip()
    return stripped not in ("", "0")


def _strict_equal(left: Any, right: Any) -> bool:
    """Model the strict equality cases used by the v1 conditionals."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _js_greater_than(left: Any, right: float) -> bool:
    number = _js_number(left)
    return number is not None and number > right


def _js_or(value: Any, fallback: Any) -> Any:
    return value if _js_truthy(value) else fallback


def _is_throat_formula(r0: Any) -> bool:
    """Match v1's isThroatFormula: single-line expression text, not a junk token."""

    return (
        isinstance(r0, str)
        and "\n" not in r0
        and r0.strip() not in ("", "NaN", "undefined")
    )


def _throat_diameter(r0: Any) -> Any:
    """Double a throat radius, keeping formula text as an expression.

    ``r0`` is formula-capable in v1, so a snapshot can carry raw expression text
    such as ``"6.35*2"`` or ``"10 + 2*p"``. v1's ``${params.r0 * 2}`` flattened
    that to ``NaN``. Both writers now emit the ``2*(<raw>)`` spelling that
    ``textcfg`` already uses, and plain numbers keep the historical numeric path.
    Persisted ``function anonymous(`` sources and the ``NaN``/``undefined`` tokens
    stay on the NaN path so migration 003 keeps dropping them.
    """

    number = _js_number(r0)
    if number is not None and not math.isnan(number):
        return number * 2
    if _is_throat_formula(r0):
        return f"2*({r0})"
    return math.nan


def _line(lines: list[str], key: str, value: Any) -> None:
    lines.append(f"{key} = {_js_string(value)}")


def _formatted_line(lines: list[str], key: str, value: Any) -> None:
    lines.append(f"{key} = {_format_value(value)}")


def _enclosure(lines: list[str], params: Mapping[str, Any]) -> None:
    if not _js_greater_than(params.get("encDepth", _UNDEFINED), 0):
        return
    lines.extend(
        [
            "Mesh.Enclosure = {",
            f"Depth = {_js_string(params.get('encDepth', _UNDEFINED))}",
            f"EdgeRadius = {_js_string(params.get('encEdge', _UNDEFINED))}",
            f"EdgeType = {_js_string(params.get('encEdgeType', _UNDEFINED))}",
            "Spacing = "
            + ",".join(
                _js_string(_js_or(params.get(key, _UNDEFINED), 25))
                for key in ("encSpaceL", "encSpaceT", "encSpaceR", "encSpaceB")
            ),
        ]
    )
    if _is_nonzero(params.get("encFrontResolution", _UNDEFINED)):
        _formatted_line(lines, "FrontResolution", params["encFrontResolution"])
    if _is_nonzero(params.get("encBackResolution", _UNDEFINED)):
        _formatted_line(lines, "BackResolution", params["encBackResolution"])
    lines.append("}")


def _write_osse(lines: list[str], params: Mapping[str, Any]) -> None:
    if params.get("type") == "R-OSSE":
        lines.extend(
            [
                "R-OSSE = {",
                f"R = {_js_string(params.get('R', _UNDEFINED))}",
                f"a = {_js_string(params.get('a', _UNDEFINED))}",
                f"a0 = {_js_string(params.get('a0', _UNDEFINED))}",
                f"b = {_js_string(params.get('b', _UNDEFINED))}",
                f"k = {_js_string(params.get('k', _UNDEFINED))}",
                f"m = {_js_string(params.get('m', _UNDEFINED))}",
                f"q = {_js_string(params.get('q', _UNDEFINED))}",
                f"r = {_js_string(params.get('r', _UNDEFINED))}",
                f"r0 = {_js_string(params.get('r0', _UNDEFINED))}",
            ]
        )
        if not _strict_equal(params.get("tmax", _UNDEFINED), 1.0):
            lines.append(f"tmax = {_js_string(params.get('tmax', _UNDEFINED))}")
        lines.append("}")
        return

    if _defined(params, "throatProfile"):
        _formatted_line(lines, "Throat.Profile", params["throatProfile"])
    if _is_nonzero(params.get("throatExtAngle", _UNDEFINED)):
        _formatted_line(lines, "Throat.Ext.Angle", params["throatExtAngle"])
    if _is_nonzero(params.get("throatExtLength", _UNDEFINED)):
        _formatted_line(lines, "Throat.Ext.Length", params["throatExtLength"])
    if _is_nonzero(params.get("slotLength", _UNDEFINED)):
        _formatted_line(lines, "Slot.Length", params["slotLength"])
    _line(lines, "Coverage.Angle", params.get("a", _UNDEFINED))
    _line(lines, "Length", params.get("L", _UNDEFINED))
    _line(lines, "Term.n", params.get("n", _UNDEFINED))
    _line(lines, "Term.q", params.get("q", _UNDEFINED))
    _line(lines, "Term.s", params.get("s", _UNDEFINED))
    _line(lines, "Throat.Angle", params.get("a0", _UNDEFINED))
    _line(lines, "Throat.Diameter", _throat_diameter(params.get("r0", _UNDEFINED)))
    if not _defined(params, "throatProfile"):
        lines.append("Throat.Profile = 1")
    _line(lines, "OS.k", params.get("k", _UNDEFINED))
    if _defined(params, "h") and not _strict_equal(params["h"], 0):
        _line(lines, "OS.h", params["h"])
    if _is_nonzero(params.get("rot", _UNDEFINED)):
        _formatted_line(lines, "Rot", params["rot"])

    if _js_truthy(params.get("gcurveType", _UNDEFINED)) and not _strict_equal(
        _js_number(params["gcurveType"]), 0
    ):
        _formatted_line(lines, "GCurve.Type", params["gcurveType"])
        for param_key, ath_key in (
            ("gcurveDist", "GCurve.Dist"),
            ("gcurveWidth", "GCurve.Width"),
            ("gcurveAspectRatio", "GCurve.AspectRatio"),
            ("gcurveSeN", "GCurve.SE.n"),
            ("gcurveSf", "GCurve.SF"),
            ("gcurveSfA", "GCurve.SF.a"),
            ("gcurveSfB", "GCurve.SF.b"),
            ("gcurveSfM1", "GCurve.SF.m1"),
            ("gcurveSfM2", "GCurve.SF.m2"),
            ("gcurveSfN1", "GCurve.SF.n1"),
            ("gcurveSfN2", "GCurve.SF.n2"),
            ("gcurveSfN3", "GCurve.SF.n3"),
            ("gcurveRot", "GCurve.Rot"),
        ):
            value = params.get(param_key, _UNDEFINED)
            if _is_nonzero(value):
                _formatted_line(lines, ath_key, value)

    for param_key, ath_key in (
        ("circArcRadius", "CircArc.Radius"),
        ("circArcTermAngle", "CircArc.TermAngle"),
    ):
        value = params.get(param_key, _UNDEFINED)
        if _is_nonzero(value):
            _formatted_line(lines, ath_key, value)

    morph_allow = _defined(params, "morphAllowShrinkage")
    morph_target = params.get("morphTarget", _UNDEFINED)
    if (_defined(params, "morphTarget") and not _strict_equal(morph_target, 0)) or morph_allow:
        morph_corner = params.get("morphCorner", _UNDEFINED)
        if _defined(params, "morphCorner") and _js_greater_than(morph_corner, 0):
            _line(lines, "Morph.CornerRadius", morph_corner)
        if _defined(params, "morphFixed"):
            _line(lines, "Morph.FixedPart", params["morphFixed"])
        if _defined(params, "morphRate"):
            _line(lines, "Morph.Rate", params["morphRate"])
        if _defined(params, "morphTarget"):
            _line(lines, "Morph.TargetShape", morph_target)
        for param_key, ath_key in (("morphWidth", "Morph.TargetWidth"), ("morphHeight", "Morph.TargetHeight")):
            value = params.get(param_key, _UNDEFINED)
            if _defined(params, param_key) and _js_greater_than(value, 0):
                _line(lines, ath_key, value)
        if morph_allow:
            _formatted_line(lines, "Morph.AllowShrinkage", params["morphAllowShrinkage"])

    _enclosure(lines, params)


def _write_mesh(lines: list[str], params: Mapping[str, Any]) -> None:
    _line(lines, "Mesh.AngularSegments", params.get("angularSegments", _UNDEFINED))
    corner_segments = params.get("cornerSegments", _UNDEFINED)
    if _defined(params, "cornerSegments") and (
        params.get("type") == "FREEFORM" or _strict_equal(params.get("morphTarget", _UNDEFINED), 1)
    ):
        number = _js_number(corner_segments)
        rounded = math.nan if number is None or math.isnan(number) else math.floor(number + 0.5)
        value = max(0, rounded) if not isinstance(rounded, float) or not math.isnan(rounded) else rounded
        _line(lines, "Mesh.CornerSegments", value)
    if _is_nonzero(params.get("throatSegments", _UNDEFINED)):
        _formatted_line(lines, "Mesh.ThroatSegments", params["throatSegments"])
    _line(lines, "Mesh.LengthSegments", params.get("lengthSegments", _UNDEFINED))
    for param_key, ath_key in (
        ("throatResolution", "Mesh.ThroatResolution"),
        ("mouthResolution", "Mesh.MouthResolution"),
    ):
        value = params.get(param_key, _UNDEFINED)
        if _is_nonzero(value):
            _formatted_line(lines, ath_key, value)
    if _defined(params, "throatSliceDensity"):
        _formatted_line(lines, "Mesh.ThroatSliceDensity", params["throatSliceDensity"])

    sampling_mode = _format_value(params.get("samplingMode", _UNDEFINED)).strip()
    z_map_points = _format_value(params.get("zMapPoints", _UNDEFINED)).strip()
    derived_sampling_mode = "zmap" if z_map_points else "ath-default-zmap"
    if sampling_mode and sampling_mode != derived_sampling_mode:
        lines.append(f"Mesh.SamplingMode = {sampling_mode}")
    if z_map_points:
        lines.append(f"Mesh.ZMapPoints = {z_map_points}")
    if _is_nonzero(params.get("verticalOffset", _UNDEFINED)):
        _formatted_line(lines, "Mesh.VerticalOffset", params["verticalOffset"])
    if _defined(params, "quadrants"):
        _formatted_line(lines, "Mesh.Quadrants", params["quadrants"])
    if _js_greater_than(params.get("wallThickness", _UNDEFINED), 0):
        _line(lines, "Mesh.WallThickness", params["wallThickness"])
    for param_key, ath_key in (
        ("rearResolution", "Mesh.RearResolution"),
        ("apertureResolutionScale", "Mesh.ApertureResolutionScale"),
    ):
        value = params.get(param_key, _UNDEFINED)
        if _is_nonzero(value):
            _formatted_line(lines, ath_key, value)
    if _defined(params, "maxTriangles"):
        _formatted_line(lines, "Mesh.MaxTriangles", params["maxTriangles"])
    if _defined(params, "allowLargeMesh"):
        _formatted_line(lines, "Mesh.AllowLargeMesh", params["allowLargeMesh"])


def _write_shared(lines: list[str], params: Mapping[str, Any]) -> None:
    _write_mesh(lines, params)
    for param_key, ath_key in (("outputSTL", "Output.STL"), ("outputMSH", "Output.MSH")):
        if _defined(params, param_key):
            _formatted_line(lines, ath_key, params[param_key])

    if _defined(params, "sourceShape"):
        _line(lines, "Source.Shape", params["sourceShape"])
    if _defined(params, "sourceRadius") and not _strict_equal(params["sourceRadius"], -1):
        _line(lines, "Source.Radius", params["sourceRadius"])
    if _defined(params, "sourceCurv"):
        _formatted_line(lines, "Source.Curv", params["sourceCurv"])
    if _defined(params, "sourceVelocity"):
        _line(lines, "Source.Velocity", params["sourceVelocity"])
    if _js_truthy(params.get("sourceContours", _UNDEFINED)):
        _formatted_line(lines, "Source.Contours", params["sourceContours"])

    sim_type = _format_value(params.get("simType", _UNDEFINED)).strip()
    derived_sim_type = "2" if params.get("type") != "R-OSSE" and _js_greater_than(
        params.get("encDepth", _UNDEFINED), 0
    ) else "1"
    if sim_type and sim_type != derived_sim_type:
        lines.append(f"ABEC.SimType = {sim_type}")
    for param_key, ath_key in (
        ("freqStart", "Simulation.F1"),
        ("freqEnd", "Simulation.F2"),
        ("numFreqs", "Simulation.NumFrequencies"),
    ):
        if _defined(params, param_key):
            _line(lines, ath_key, params[param_key])
    if _defined(params, "simType"):
        _formatted_line(lines, "Simulation.SimType", params["simType"])
    if _defined(params, "solverMode"):
        _formatted_line(lines, "Simulation.SolverMode", params["solverMode"])


def _write_passthrough_blocks(lines: list[str], params: Mapping[str, Any]) -> None:
    blocks = params.get("_blocks") or {}
    if not isinstance(blocks, Mapping):
        raise LegacySnapshotError("legacy snapshot _blocks must be a mapping")
    for block_name, block in blocks.items():
        if block_name == "Mesh.Enclosure" or str(block_name).startswith("Freeform."):
            continue
        if not _js_truthy(block):
            continue
        if not isinstance(block, Mapping):
            raise LegacySnapshotError(f"legacy snapshot block {block_name!r} must be a mapping")
        lines.append(f"{block_name} = {{")
        block_lines = block.get("_lines", _UNDEFINED)
        if _js_truthy(block_lines) and len(block_lines) > 0:
            # ``Array.prototype.join`` spells null and undefined as "", unlike the
            # template interpolation used for ``_items`` values just below.
            lines.append(
                "\n".join("" if value is None else _js_string(value) for value in block_lines)
            )
        block_items = block.get("_items", _UNDEFINED)
        if _js_truthy(block_items):
            if not isinstance(block_items, Mapping):
                raise LegacySnapshotError(f"legacy snapshot block {block_name!r} _items must be a mapping")
            for key, value in block_items.items():
                lines.append(f"{key} = {_js_string(value)}")
        lines.append("}")


def snapshot_to_ath_text(params: Mapping[str, Any]) -> str:
    """Port of v1 ``generateMWGConfigContent`` without its timestamp line."""

    if not isinstance(params, Mapping):
        raise LegacySnapshotError("legacy snapshot params must be a mapping")
    if params.get("type") == "FREEFORM":
        raise LegacySnapshotError(
            "FREEFORM legacy snapshots are not supported; the design must be re-entered."
        )

    lines = ["; Parameter config"]
    has_scale = _defined(params, "Scale") or _defined(params, "scale")
    if has_scale:
        scale_value = params.get("scale") if _defined(params, "scale") else params.get("Scale")
        scale_number = _js_number(scale_value)
        if (
            scale_number is None
            or not math.isfinite(scale_number)
            or scale_number != 1
            or _defined(params, "Scale")
        ):
            _formatted_line(lines, "Scale", scale_value)

    _write_osse(lines, params)
    _write_shared(lines, params)
    _write_passthrough_blocks(lines, params)
    return "\n".join(lines) + "\n"


def _snapshot_params(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise LegacySnapshotError("legacy snapshot must be a mapping")
    if "params" not in snapshot:
        return snapshot
    params = snapshot["params"]
    if not isinstance(params, Mapping):
        raise LegacySnapshotError("legacy snapshot params must be a mapping")
    return params


def snapshot_to_design(snapshot: Mapping[str, Any]) -> ParsedDesign:
    """Convert a whole v1 snapshot or params bag into a validated design."""

    params = _snapshot_params(snapshot)
    try:
        return parse(snapshot_to_ath_text(params))
    except LegacySnapshotError:
        raise
    except (TextConfigError, TypeError, ValueError) as exc:
        raise LegacySnapshotError(f"could not parse legacy snapshot: {exc}") from exc


__all__ = ["LegacySnapshotError", "snapshot_to_ath_text", "snapshot_to_design"]
