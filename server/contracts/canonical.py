"""Stable JSON encodings for persisted identities.

All variants sort keys, use compact comma/colon separators, retain the standard
library's float spelling, and add no newline. Byte variants encode as UTF-8.
These profiles preserve existing identifiers; changing one requires a versioned
identity migration. The pinned WGLink protocol keeps its independent copy.
"""

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from typing import Any


def normalize_json_value(value: Any) -> Any:
    """Recursively coerce dataclasses, enums, and numpy-like values to JSON."""
    if is_dataclass(value):
        return normalize_json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return normalize_json_value(value.tolist())
    return value


def canonical_ascii_json(value: Any) -> str:
    """ASCII escapes; historical NaN and infinity tokens permitted."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=True)


def canonical_ascii_strict_json(value: Any) -> str:
    """ASCII escapes; non-finite numbers rejected."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def canonical_utf8_strict_json(value: Any) -> str:
    """Unescaped Unicode; non-finite numbers rejected."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def canonical_ascii_string_json(value: Any) -> str:
    """ASCII escapes and non-finite tokens; unsupported values use str()."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=True, default=str)


def canonical_ascii_strict_bytes(value: Any) -> bytes:
    return canonical_ascii_strict_json(value).encode("utf-8")


def canonical_utf8_strict_bytes(value: Any) -> bytes:
    return canonical_utf8_strict_json(value).encode("utf-8")


def canonical_normalized_bytes(value: Any) -> bytes:
    """Normalize geometry-like values before strict ASCII serialization."""
    return canonical_ascii_strict_bytes(normalize_json_value(value))
