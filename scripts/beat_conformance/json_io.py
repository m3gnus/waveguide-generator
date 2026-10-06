"""Strict corpus evidence serialization, including native result snapshots."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import json
import math
from pathlib import Path, PurePath
from typing import Any

import numpy as np

CALLABLE_REASON = "callable omitted: execution callback is not serializable evidence"


def json_value(value: Any, *, _path: str = "$") -> Any:
    """Preserve typed arrays/complex/bytes; mark omitted callbacks and nonfinite data.

    Dictionary/dataclass callbacks are dropped with reasons in __omitted_fields__.
    Sequence positions cannot be dropped without changing array shapes, so they
    retain an explicit unavailable marker. Unknown objects fail with their path;
    neither values nor arbitrary mapping keys are silently stringified.
    Paths (including mapping keys) are evidence strings in as_posix() form:
    only Windows separators change; drives, roots, UNC/extended prefixes and
    POSIX literal backslashes survive. Reparse Windows evidence with
    PureWindowsPath to retain its meaning on any host; do not resolve/rebase it.
    Ordinary strings are never treated as paths or rewritten.
    """
    if callable(value):
        return {"__unavailable__": CALLABLE_REASON}
    if is_dataclass(value) and not isinstance(value, type):
        # asdict deep-copies callbacks (potentially bound to locks/worker state).
        return json_value({f.name: getattr(value, f.name) for f in fields(value)}, _path=_path)
    if isinstance(value, np.ndarray):
        return {"__array__": value.dtype.str, "shape": list(value.shape),
                "data": json_value(value.tolist(), _path=f"{_path}.data")}
    if isinstance(value, (complex, np.complexfloating)):
        return {"__complex__": [json_value(float(value.real)), json_value(float(value.imag))]}
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        result, omitted = {}, {}
        for key, item in value.items():
            if isinstance(key, np.generic):
                key = key.item()
            if not isinstance(key, (str, int, float, bool, PurePath)) or isinstance(key, float) and not math.isfinite(key):
                raise TypeError(f"Unsupported mapping key {type(key).__name__} at {_path}")
            key = key.as_posix() if isinstance(key, PurePath) else str(key)
            if key in result or key in omitted:
                raise ValueError(f"Colliding JSON mapping key at {_path}.{key}")
            if callable(item):
                omitted[key] = CALLABLE_REASON
            else:
                result[key] = json_value(item, _path=f"{_path}.{key}")
        if omitted:
            if "__omitted_fields__" in result:
                raise ValueError(f"Reserved omission metadata at {_path}")
            result["__omitted_fields__"] = omitted
        # Wrap mappings that could be mistaken for tagged values. The wrapper
        # itself is reserved too, so arbitrary user dictionaries round-trip.
        if any(key in result for key in ("__array__", "__complex__", "__bytes__", "__float__", "__mapping__")):
            return {"__mapping__": list(result.items())}
        return result
    if isinstance(value, (list, tuple)):
        return [json_value(item, _path=f"{_path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, np.generic):
        return json_value(value.item(), _path=_path)
    if isinstance(value, float) and not math.isfinite(value):
        return {"__float__": "nan" if math.isnan(value) else "+inf" if value > 0 else "-inf"}
    if isinstance(value, PurePath):
        return value.as_posix()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported evidence type {type(value).__name__} at {_path}")


def _decode(value: dict) -> Any:
    if set(value) == {"__mapping__"}:
        return dict(value["__mapping__"])
    if set(value) == {"__float__"}:
        return float(value["__float__"])
    if "__complex__" in value:
        return complex(*(float("nan") if v is None else v for v in value["__complex__"]))
    if "__array__" in value:
        return np.asarray(value["data"], dtype=value["__array__"]).reshape(value["shape"])
    if "__bytes__" in value:
        return base64.b64decode(value["__bytes__"])
    return value


def dumps(value: Any) -> str:
    return json.dumps(json_value(value), sort_keys=True, allow_nan=False, indent=2)


def snapshot(value: Any) -> Any:
    """Detach mutable native arrays without copying execution callbacks."""
    return json.loads(dumps(value), object_hook=_decode)


def write_json(path: Path, value: Any) -> None:
    path.write_text(dumps(value) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_hook=_decode)
