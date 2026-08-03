"""Waveguide binary frame v0 and viewport point-grid tessellation."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import Any

import numpy as np


MAGIC = b"WGF0"
_PREFIX = struct.Struct("<4sI")
_SUPPORTED_DTYPES = {
    "float32",
    "float64",
    "int8",
    "uint8",
    "int16",
    "uint16",
    "int32",
    "uint32",
    "int64",
    "uint64",
}


def _little_endian_contiguous(array: np.ndarray) -> np.ndarray:
    value = np.asarray(array)
    if value.dtype.name not in _SUPPORTED_DTYPES:
        raise TypeError(f"unsupported frame dtype: {value.dtype}")
    little_dtype = value.dtype.newbyteorder("<")
    return np.ascontiguousarray(value, dtype=little_dtype)


def encode(
    seq: int,
    eval_ms: float,
    arrays: Mapping[str, np.ndarray],
) -> bytes:
    """Encode named ndarrays as a WGF0 frame with little-endian payloads."""

    sections: list[dict[str, Any]] = []
    buffers: list[bytes] = []
    offset = 0
    for name, raw_array in arrays.items():
        if not isinstance(name, str) or not name:
            raise ValueError("frame section names must be non-empty strings")
        array = _little_endian_contiguous(np.asarray(raw_array))
        payload = array.tobytes(order="C")
        sections.append(
            {
                "name": name,
                "dtype": array.dtype.name,
                "shape": list(array.shape),
                "offset": offset,
                "byteLength": len(payload),
            }
        )
        buffers.append(payload)
        offset += len(payload)

    header = {
        "v": 0,
        "seq": int(seq),
        "evalMs": float(eval_ms),
        "sections": sections,
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return _PREFIX.pack(MAGIC, len(header_bytes)) + header_bytes + b"".join(buffers)


def decode(frame: bytes) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Decode and validate a WGF0 frame."""

    if len(frame) < _PREFIX.size:
        raise ValueError("frame is shorter than the WGF0 prefix")
    magic, header_length = _PREFIX.unpack_from(frame)
    if magic != MAGIC:
        raise ValueError(f"invalid frame magic: {magic!r}")
    header_end = _PREFIX.size + header_length
    if header_end > len(frame):
        raise ValueError("frame JSON header is truncated")
    try:
        header = json.loads(frame[_PREFIX.size:header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid frame JSON header") from error
    if header.get("v") != 0 or not isinstance(header.get("sections"), list):
        raise ValueError("unsupported or malformed frame header")

    payload_length = len(frame) - header_end
    arrays: dict[str, np.ndarray] = {}
    for section in header["sections"]:
        try:
            name = section["name"]
            dtype_name = section["dtype"]
            shape = tuple(int(size) for size in section["shape"])
            offset = int(section["offset"])
            byte_length = int(section["byteLength"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("malformed frame section") from error
        if not isinstance(name, str) or not name or name in arrays:
            raise ValueError(f"invalid or duplicate frame section name: {name!r}")
        if dtype_name not in _SUPPORTED_DTYPES:
            raise ValueError(f"unsupported frame dtype: {dtype_name!r}")
        if any(size < 0 for size in shape) or offset < 0 or byte_length < 0:
            raise ValueError(f"invalid bounds for frame section {name!r}")
        dtype = np.dtype(dtype_name).newbyteorder("<")
        expected_length = int(np.prod(shape, dtype=np.int64)) * dtype.itemsize
        if expected_length != byte_length or offset + byte_length > payload_length:
            raise ValueError(f"invalid byte length for frame section {name!r}")
        arrays[name] = np.frombuffer(
            frame,
            dtype=dtype,
            count=expected_length // dtype.itemsize,
            offset=header_end + offset,
        ).reshape(shape)
    return header, arrays


def _grid_indices(n_phi: int, n_columns: int, full_circle: bool) -> np.ndarray:
    if n_phi < 2 or n_columns < 2:
        raise ValueError("point grid needs at least 2 angular and 2 axial samples")
    angular_cells = n_phi if full_circle else n_phi - 1
    triangles = np.empty((angular_cells * (n_columns - 1) * 2, 3), dtype=np.uint32)
    cursor = 0
    for angular_index in range(angular_cells):
        next_angular = (angular_index + 1) % n_phi
        for axial_index in range(n_columns - 1):
            a = angular_index * n_columns + axial_index
            b = next_angular * n_columns + axial_index
            c = a + 1
            d = b + 1
            triangles[cursor] = (a, b, c)
            triangles[cursor + 1] = (b, d, c)
            cursor += 2
    return triangles.reshape(-1)


def _positions(raw: Any, n_phi: int, n_columns: int, name: str) -> np.ndarray:
    values = np.asarray(raw, dtype=np.float32)
    expected = n_phi * n_columns * 3
    if values.size != expected:
        raise ValueError(f"{name} has {values.size} values; expected {expected}")
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{name} contains non-finite coordinates")
    return np.ascontiguousarray(values.reshape(n_phi * n_columns, 3))


def grid_to_mesh(viewport_result: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """Tessellate inner and optional outer viewport point grids.

    The inner surface uses ``positions``/``indices``. If the viewport includes
    an outer wall, the frame gains ``outerPositions``/``outerIndices`` as a
    separately renderable section.
    """

    grid = viewport_result.get("grid") or {}
    n_phi = int(grid.get("grid_n_phi") or 0)
    n_length = int(grid.get("grid_n_length") or 0)
    n_columns = n_length + 1
    full_circle = bool(grid.get("full_circle", True))
    mesh = {
        "positions": _positions(grid.get("inner_points"), n_phi, n_columns, "inner_points"),
        "indices": _grid_indices(n_phi, n_columns, full_circle),
    }
    if grid.get("outer_points") is not None:
        mesh["outerPositions"] = _positions(
            grid["outer_points"], n_phi, n_columns, "outer_points"
        )
        mesh["outerIndices"] = _grid_indices(n_phi, n_columns, full_circle)
    return mesh
