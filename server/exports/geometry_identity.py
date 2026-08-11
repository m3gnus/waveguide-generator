"""Stable geometry identity shared by export and CAD-return freshness checks."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from typing import Any, Mapping

from server.design.schema import DesignConfig
from server.preview.translate import design_to_mesher_config


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


def geometry_hash(geometry: object, mesher_version: str) -> str:
    """Hash resolved export geometry using the outbound artifact contract."""

    canonical = json.dumps(
        {
            "datum_schema": 1,
            "domain": "full",
            "geometry": normalize_json_value(geometry),
            "mesher_version": mesher_version,
            "open_throat": True,
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def mesher_version() -> str:
    try:
        return version("hornlab-waveguide-mesher")
    except PackageNotFoundError:
        return "unknown"


def geometry_hash_for_design(design: DesignConfig) -> str:
    """Run the exact outbound translation and geometry-resolution chain."""

    from hornlab_mesher.config_builder import resolve_geometry

    config = design_to_mesher_config(design)
    resolved = resolve_geometry(config)
    return geometry_hash(resolved.geometry, mesher_version())


__all__ = [
    "geometry_hash",
    "geometry_hash_for_design",
    "mesher_version",
    "normalize_json_value",
]
