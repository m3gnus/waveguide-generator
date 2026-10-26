"""Identify a parametric run's geometry and classify it when reopened."""

from __future__ import annotations

import re
from typing import Mapping

from server.design.schema import DesignConfig
from server.integration.mesher_identity import mesher_identity
from server.jobs.models import MeshFreshness
from server.preview.geometry_identity import geometry_hash_for_design, mesher_version


_GEOMETRY_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def record_for_design(design: DesignConfig) -> dict[str, str] | None:
    """Capture the same resolved-geometry identity used by CAD export."""

    version = mesher_version()
    if not version or version == "unknown":
        return None
    record = {
        "mesher_version": version,
        "geometry_hash": geometry_hash_for_design(design, mesher_version_value=version),
    }
    source = mesher_identity()
    if not source.startswith("unmeasured:"):
        record["mesher_source"] = source
    return record


def classify_open(provenance: Mapping[str, object] | None, design: DesignConfig) -> MeshFreshness:
    """Compare a run with the current mesher; missing evidence proves nothing."""

    if not provenance:
        return "unknown"
    recorded_version = provenance.get("mesher_version")
    recorded_hash = provenance.get("geometry_hash")
    if not isinstance(recorded_version, str) or not recorded_version or recorded_version == "unknown":
        return "unknown"
    if not isinstance(recorded_hash, str) or not _GEOMETRY_HASH.fullmatch(recorded_hash):
        return "unknown"
    current_version = mesher_version()
    if not current_version or current_version == "unknown":
        return "unknown"
    if recorded_version != current_version:
        return "stale"
    recorded_source = provenance.get("mesher_source")
    if isinstance(recorded_source, str) and recorded_source:
        if recorded_source.startswith("unmeasured:"):
            return "unknown"
        current_source = mesher_identity()
        if current_source.startswith("unmeasured:"):
            return "unknown"
        if current_source != recorded_source:
            return "stale"
    current_hash = geometry_hash_for_design(design, mesher_version_value=current_version)
    return "current" if recorded_hash == current_hash else "stale"
