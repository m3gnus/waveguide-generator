"""Identify a parametric run's geometry and classify it when reopened."""

from __future__ import annotations

import hashlib
import re
from typing import Mapping

from server.contracts.canonical import canonical_ascii_strict_json
from server.design.schema import DesignConfig
from server.integration.mesher_identity import mesher_identity
from server.jobs.models import MeshFreshness
from server.mesh.cache import SolverMeshArtifactCache
from server.preview.geometry_identity import geometry_hash_for_design, mesher_version
from server.preview.translate import design_to_mesher_config


_GEOMETRY_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")

# Every parametric solve stamps this hash and every reopened run compares it,
# and each answer is a full mesher geometry resolve. The hash is a function of
# the translated mesher config and the mesher in this process, so it is kept
# by those. Small on purpose: it absorbs a design solved and reopened
# repeatedly, not a session's history.
_geometry_hashes = SolverMeshArtifactCache(max_entries=64, max_bytes=64 * 64 * 1024)


def _geometry_hash(design: DesignConfig, version: str) -> str:
    """``geometry_hash_for_design``, computed once per config and mesher."""

    try:
        config = canonical_ascii_strict_json(design_to_mesher_config(design))
    except Exception:  # noqa: BLE001 - an unhashable config just skips the cache
        return geometry_hash_for_design(design, mesher_version_value=version)
    key = f"{version}:{hashlib.sha256(config.encode('utf-8')).hexdigest()}"
    cached = _geometry_hashes.get(key)
    if cached is not None:
        return cached["geometry_hash"]
    value = geometry_hash_for_design(design, mesher_version_value=version)
    _geometry_hashes.put(key, {"geometry_hash": value})
    return value


def record_for_design(design: DesignConfig) -> dict[str, str] | None:
    """Capture the same resolved-geometry identity used by CAD export."""

    version = mesher_version()
    if not version or version == "unknown":
        return None
    record = {
        "mesher_version": version,
        "geometry_hash": _geometry_hash(design, version),
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
    current_hash = _geometry_hash(design, current_version)
    return "current" if recorded_hash == current_hash else "stale"
