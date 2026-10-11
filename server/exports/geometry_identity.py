"""Stable geometry identity shared by export and CAD-return freshness checks."""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version

from server.contracts.canonical import canonical_ascii_strict_json
from server.contracts.canonical import normalize_json_value as normalize_json_value
from server.design.schema import DesignConfig
from server.preview.translate import design_to_mesher_config


def geometry_hash(geometry: object, mesher_version: str) -> str:
    """Hash resolved export geometry using the outbound artifact contract."""

    canonical = canonical_ascii_strict_json(
        {
            "datum_schema": 1,
            "domain": "full",
            "geometry": normalize_json_value(geometry),
            "mesher_version": mesher_version,
            "open_throat": True,
        }
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
