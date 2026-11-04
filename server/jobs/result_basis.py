"""Read-time excitation identity for archived results; stored fields stay untouched."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from pydantic import ValidationError

from server.jobs.models import ImportedGeometrySource


def with_result_basis(text: str, digest: str, row: Mapping[str, Any]) -> tuple[str, str]:
    payload = json.loads(text)
    config = row.get("config_json") or {}
    geometry = config.get("geometry") or {}
    normalization = None
    drivers: set[str] = set()
    if geometry.get("type") == "imported":
        try:
            source = ImportedGeometrySource.model_validate(geometry)
        except ValidationError:
            source = None
        if source is not None:
            channel_backends = [channel.get("metadata", {}).get("solver_backend")
                                for channel in payload.get("channels", {}).values()]
            engine = (payload.get("provenance", {}).get("resolved_engine")
                      or ("bempp" if "bempp" in channel_backends else None)
                      or config.get("options", {}).get("engine"))
            drivers = {channel.id for channel in source.drive_channels
                       if channel.driver is not None or channel.exterior_transducer is not None}
            normalization = source.normalization_for_engine(engine)
    elif geometry.get("type") == "parametric":
        normalization = "acceleration_legacy"
    changed = False
    if normalization is not None and "excitation_normalization" not in payload.setdefault("metadata", {}):
        payload["metadata"]["excitation_normalization"] = normalization
        changed = True
    for channel_id, channel in (payload.get("channels") or {"": payload}).items():
        metadata = channel.setdefault("metadata", {})
        # A combined response inherits its members' identities, never a guessed
        # uniform basis (voltage and prescribed members may coexist).
        if "combine" in metadata:
            continue
        # Driver evidence wins over a prescribed tag: LEM paths keep the solver's
        # basis tag after scaling the response to the drive voltage.
        if channel_id in drivers or metadata.get("driver") or metadata.get("impedance_drive") in ("rms_voltage", "voltage"):
            if "voltage" in str(metadata.get("source_normalization", "")):
                continue
            metadata["source_normalization"] = "voltage_driven_driver_lem"
        elif "source_normalization" in metadata:
            continue
        elif normalization is not None:
            metadata["source_normalization"] = (
                "native_rms_velocity" if normalization == "rms_velocity" else "unit_normal_acceleration"
            )
        else:
            continue
        changed = True
    if not changed:
        return text, digest
    content = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    return content, hashlib.sha256(content.encode()).hexdigest()
