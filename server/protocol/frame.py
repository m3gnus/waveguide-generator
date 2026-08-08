"""FRAME-SPEC v1.1 encoder and zero-copy decoder."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any, Mapping, TypeAlias

import numpy as np

Header: TypeAlias = dict[str, Any]

MAGIC = b"WGF1"
SUPPORTED_VERSION = 1
MAX_HEADER_BYTES = 64 * 1024
DEFAULT_MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_SECTION_ELEMENTS = 1 << 28
MAX_SECTION_RANK = 64
MAX_SAFE_INTEGER = (1 << 53) - 1
NORMAL_LENGTH_TOLERANCE = 1e-3

_DTYPES: dict[str, np.dtype[Any]] = {
    "f32": np.dtype("<f4"),
    "f64": np.dtype("<f8"),
    "u32": np.dtype("<u4"),
    "u16": np.dtype("<u2"),
    "u8": np.dtype("u1"),
    "i32": np.dtype("<i4"),
}
_DTYPE_NAMES = {(dtype.kind, dtype.itemsize): name for name, dtype in _DTYPES.items()}
_RULES_PATH = Path(__file__).resolve().parents[2] / "shared" / "frame-rules.json"
RULE_IDS = frozenset(json.loads(_RULES_PATH.read_text(encoding="utf-8")))


class FrameError(ValueError):
    """A cleanly classified FRAME-SPEC validation failure."""

    def __init__(self, rule: str, detail: str = "") -> None:
        if rule not in RULE_IDS:
            raise RuntimeError(f"unknown frame rule id: {rule}")
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}" if detail else rule)


def _fail(rule: str, detail: str = "") -> None:
    raise FrameError(rule, detail)


def _align8(value: int) -> int:
    return (value + 7) & ~7


def _safe_integer(value: object) -> int | None:
    """Return the normalized JSON safe integer shared with the JS decoder."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    if abs(value) > MAX_SAFE_INTEGER:
        return None
    return int(value)


def _is_int(value: object) -> bool:
    return _safe_integer(value) is not None


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _json_object(raw: bytes) -> Header:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        _fail("header-json", str(exc))
    if not isinstance(value, dict):
        _fail("header-json", "header must be a JSON object")
    return value


def _section_descriptors(
    header: Header, payload_length: int
) -> list[tuple[str, np.dtype[Any], tuple[int, ...], int, int]]:
    raw_sections = header.get("sections")
    if not isinstance(raw_sections, list):
        _fail("sections-invalid", "sections must be a list")

    result: list[tuple[str, np.dtype[Any], tuple[int, ...], int, int]] = []
    names: set[str] = set()
    ranges: list[tuple[int, int, str]] = []
    expected_offset = 0

    for index, raw in enumerate(raw_sections):
        if not isinstance(raw, dict):
            _fail("sections-invalid", f"section {index} must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            _fail("section-name", f"section {index} has no valid name")
        if name in names:
            _fail("section-duplicate", name)
        names.add(name)

        dtype_name = raw.get("dtype")
        if not isinstance(dtype_name, str) or dtype_name not in _DTYPES:
            _fail("section-dtype", f"{name}: {dtype_name!r}")
        dtype = _DTYPES[dtype_name]

        raw_shape = raw.get("shape")
        if not isinstance(raw_shape, list) or len(raw_shape) > MAX_SECTION_RANK:
            _fail("section-shape", name)
        shape_values = [_safe_integer(dim) for dim in raw_shape]
        if any(dim is None or dim < 0 for dim in shape_values):
            _fail("section-shape", name)
        element_count = 1
        for dimension in shape_values:
            assert dimension is not None
            if dimension > MAX_SECTION_ELEMENTS:
                _fail("section-elements", name)
            dim = dimension
            element_count *= dim
            if element_count > MAX_SECTION_ELEMENTS:
                _fail("section-elements", name)
        shape = tuple(dimension for dimension in shape_values if dimension is not None)
        raw["shape"] = list(shape)

        byte_offset = _safe_integer(raw.get("byteOffset"))
        byte_length = _safe_integer(raw.get("byteLength"))
        if byte_offset is None or byte_length is None or byte_offset < 0 or byte_length < 0:
            _fail("section-range", name)
        raw["byteOffset"] = byte_offset
        raw["byteLength"] = byte_length
        if byte_length != element_count * dtype.itemsize:
            _fail("section-length", name)
        if byte_offset > payload_length or byte_length > payload_length - byte_offset:
            _fail("section-range", name)
        if byte_offset % 8 != 0:
            _fail("section-alignment", name)

        if byte_offset != expected_offset:
            if byte_offset < expected_offset:
                _fail("section-overlap", name)
            _fail("section-order", name)
        end = byte_offset + byte_length
        for other_start, other_end, other_name in ranges:
            if byte_offset < other_end and other_start < end:
                _fail("section-overlap", f"{name} overlaps {other_name}")
        ranges.append((byte_offset, end, name))
        expected_offset = _align8(end)
        result.append((name, dtype, shape, byte_offset, byte_length))
    if payload_length != expected_offset:
        _fail("section-range", "payload has missing or trailing bytes")
    return result


def _require_section(
    section_meta: Mapping[str, tuple[np.dtype[Any], tuple[int, ...]]],
    surface: Mapping[str, Any],
    field: str,
    missing_rule: str,
) -> tuple[str, np.dtype[Any], tuple[int, ...]]:
    name = surface.get(field)
    if not isinstance(name, str) or not name:
        _fail(missing_rule, f"surface {field} reference missing")
    try:
        dtype, shape = section_meta[name]
    except KeyError:
        _fail(missing_rule, name)
    return name, dtype, shape


def _validate_fidelity(header: Header) -> None:
    fidelity = header.get("fidelity")
    if not isinstance(fidelity, dict):
        _fail("fidelity-invalid", "preview fidelity must be an object")

    float_fields = (
        "maxChordErrorMmRequested",
        "maxNormalStepDegRequested",
        "maxNormalStepDegAchieved",
    )
    int_fields = ("minSilhouetteSegmentsRequested", "minSilhouetteSegmentsAchieved")
    for field in float_fields:
        value = fidelity.get(field)
        if not _is_finite_number(value) or value < 0:
            _fail("fidelity-invalid", field)
    achieved_chord = fidelity.get("maxChordErrorMmAchieved")
    complete = fidelity.get("chordMeasurementComplete")
    unmeasured = _safe_integer(fidelity.get("unmeasuredChordIntervals"))
    if not isinstance(complete, bool) or unmeasured is None or unmeasured < 0:
        _fail("fidelity-invalid", "chord measurement completeness")
    if complete:
        if not _is_finite_number(achieved_chord) or achieved_chord < 0 or unmeasured != 0:
            _fail("fidelity-invalid", "maxChordErrorMmAchieved")
    elif (
        "maxChordErrorMmAchieved" not in fidelity
        or achieved_chord is not None
        or unmeasured < 1
    ):
        _fail("fidelity-invalid", "maxChordErrorMmAchieved")
    for field in int_fields:
        value = fidelity.get(field)
        if not _is_int(value) or value < 0:
            _fail("fidelity-invalid", field)

    missed = (
        not complete
        or (
            achieved_chord is not None
            and achieved_chord > fidelity["maxChordErrorMmRequested"]
        )
        or fidelity["maxNormalStepDegAchieved"] > fidelity["maxNormalStepDegRequested"]
        or fidelity["minSilhouetteSegmentsAchieved"] < fidelity["minSilhouetteSegmentsRequested"]
    )
    cap_flag = fidelity.get("vertexCapLimited")
    if cap_flag is not None and not isinstance(cap_flag, bool):
        _fail("fidelity-invalid", "vertexCapLimited")
    if missed and cap_flag is not True:
        _fail("fidelity-cap-unreported")


def _validate_preview(header: Header, arrays: Mapping[str, np.ndarray]) -> None:
    surfaces = header.get("surfaces")
    if not isinstance(surfaces, list):
        _fail("preview-surfaces", "surfaces must be a list")
    _validate_fidelity(header)

    section_meta = {name: (array.dtype, array.shape) for name, array in arrays.items()}
    for surface_index, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            _fail("surface-invalid", str(surface_index))
        role = surface.get("role")
        if not isinstance(role, str) or not role:
            _fail("surface-role", str(surface_index))
        shading = surface.get("shading")
        if not isinstance(shading, str) or shading not in {"smooth", "flat"}:
            _fail("surface-shading", role)
        normal_method = surface.get("normalMethod")
        if not isinstance(normal_method, str) or normal_method not in {
            "analytic-parametric",
            "analytic-spline",
            "exact-planar",
        }:
            _fail("surface-normal-method", role)
        if "closedPhi" in surface and not isinstance(surface["closedPhi"], bool):
            _fail("surface-closed-phi", role)

        position_name, position_dtype, position_shape = _require_section(
            section_meta, surface, "positions", "surface-section-missing"
        )
        index_name, index_dtype, index_shape = _require_section(
            section_meta, surface, "indices", "surface-section-missing"
        )
        normal_name, normal_dtype, normal_shape = _require_section(
            section_meta, surface, "normals", "normals-missing"
        )
        if position_dtype != _DTYPES["f32"] or len(position_shape) != 2 or position_shape[1] != 3:
            _fail("surface-position-format", position_name)
        if index_dtype != _DTYPES["u32"] or len(index_shape) != 1:
            _fail("surface-index-format", index_name)
        if normal_dtype != _DTYPES["f32"] or len(normal_shape) != 2 or normal_shape[1] != 3:
            _fail("normals-format", normal_name)
        if normal_shape[0] != position_shape[0]:
            _fail("normals-row-mismatch", role)

        positions = arrays[position_name]
        normals = arrays[normal_name]
        if not np.isfinite(positions).all():
            _fail("positions-nonfinite", position_name)
        if not np.isfinite(normals).all():
            _fail("normals-nonfinite", normal_name)
        lengths = np.linalg.norm(normals.astype(np.float64, copy=False), axis=1)
        if np.any(np.abs(lengths - 1.0) > NORMAL_LENGTH_TOLERANCE):
            _fail("normals-nonunit", normal_name)

        indices = arrays[index_name]
        if indices.size:
            sample = indices if indices.size <= 2048 else np.concatenate((indices[:1024], indices[-1024:]))
            if np.any(sample >= position_shape[0]):
                _fail("indices-out-of-range", index_name)


def decode(
    data: bytes | bytearray | memoryview, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
) -> tuple[Header, dict[str, np.ndarray]]:
    """Decode a frame, returning NumPy views backed by *data*."""

    try:
        view = memoryview(data).cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("data must be a contiguous bytes-like object") from exc
    if len(view) > max_frame_bytes:
        _fail("frame-too-large", f"{len(view)} > {max_frame_bytes}")
    if len(view) < 4 or bytes(view[:4]) != MAGIC:
        _fail("bad-magic")
    if len(view) < 8:
        _fail("header-truncated")
    header_length = struct.unpack_from("<I", view, 4)[0]
    if header_length > MAX_HEADER_BYTES:
        _fail("header-too-large", str(header_length))
    header_end = 8 + header_length
    if header_end > len(view):
        _fail("header-truncated")
    payload_base = _align8(header_end)
    if payload_base > len(view):
        _fail("header-truncated", "payload alignment padding missing")

    header = _json_object(bytes(view[8:header_end]))
    version = _safe_integer(header.get("v"))
    if version != SUPPORTED_VERSION:
        _fail("unsupported-version", repr(version))
    header["v"] = version
    descriptors = _section_descriptors(header, len(view) - payload_base)

    arrays: dict[str, np.ndarray] = {}
    for name, dtype, shape, byte_offset, _byte_length in descriptors:
        try:
            arrays[name] = np.ndarray(
                shape, dtype=dtype, buffer=view, offset=payload_base + byte_offset
            )
        except (TypeError, ValueError, OverflowError) as exc:
            _fail("section-shape", f"{name}: {exc}")
    if header.get("kind") == "preview":
        _validate_preview(header, arrays)
    return header, arrays


def encode(header_fields: Mapping[str, Any], arrays: Mapping[str, np.ndarray]) -> bytes:
    """Encode a deterministic FRAME-SPEC v1.1 frame."""

    if not isinstance(header_fields, Mapping) or not isinstance(arrays, Mapping):
        raise TypeError("header_fields and arrays must be mappings")
    header: Header = dict(header_fields)
    if "sections" in header:
        _fail("sections-invalid", "encoder owns the sections field")
    header.setdefault("v", SUPPORTED_VERSION)

    payload = bytearray()
    sections: list[dict[str, Any]] = []
    for name, value in arrays.items():
        if not isinstance(name, str) or not name:
            _fail("section-name")
        array = np.asarray(value)
        dtype_name = _DTYPE_NAMES.get((array.dtype.kind, array.dtype.itemsize))
        if dtype_name is None:
            _fail("section-dtype", f"{name}: {array.dtype}")
        dtype = _DTYPES[dtype_name]
        element_count = int(array.size)
        if element_count > MAX_SECTION_ELEMENTS:
            _fail("section-elements", name)
        semantic_name = name.lower()
        if (semantic_name.endswith("positions") or semantic_name.endswith("normals")) and not np.isfinite(array).all():
            _fail("normals-nonfinite" if semantic_name.endswith("normals") else "positions-nonfinite", name)
        while len(payload) % 8:
            payload.append(0)
        raw = array.astype(dtype, copy=False).tobytes(order="C")
        sections.append(
            {
                "name": name,
                "dtype": dtype_name,
                "shape": list(array.shape),
                "byteOffset": len(payload),
                "byteLength": len(raw),
            }
        )
        payload.extend(raw)
    while len(payload) % 8:
        payload.append(0)
    header["sections"] = sections
    try:
        header_bytes = json.dumps(
            header, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        _fail("header-json", str(exc))
    if len(header_bytes) > MAX_HEADER_BYTES:
        _fail("header-too-large", str(len(header_bytes)))
    prefix = MAGIC + struct.pack("<I", len(header_bytes)) + header_bytes
    frame = prefix + bytes(_align8(len(prefix)) - len(prefix)) + payload
    if len(frame) > DEFAULT_MAX_FRAME_BYTES:
        _fail("frame-too-large", str(len(frame)))
    decode(frame)
    return frame
