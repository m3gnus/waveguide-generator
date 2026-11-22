"""Read-time excitation identity for archived results; stored fields stay untouched."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from pydantic import ValidationError

from server.jobs.models import ImportedGeometrySource
from server.jobs.read_time_cache import ReadTimeCache


# Verdicts for the few records most recently opened, so polling or reopening
# a multi-megabyte run serves its stored or presented bytes without parsing.
_SERVED = ReadTimeCache(4)


def with_result_basis(text: str, digest: str, row: Mapping[str, Any]) -> tuple[str, str]:
    """Serve ``text`` with its read-time excitation identity, and the digest of what is served."""

    payload = json.loads(text)
    if not _apply_result_basis(payload, row):
        return text, digest
    content = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    return content, hashlib.sha256(content.encode()).hexdigest()


def served_result_basis(text: str, digest: str, row: Mapping[str, Any]) -> tuple[str, str]:
    """``with_result_basis`` for a stored record whose ``digest`` names ``text``.

    The verdict is memoised by that digest and the saved request's inputs to
    the identity, so only the first read of a record parses it.
    """

    key = _basis_inputs_key(row)
    if key is None:
        return with_result_basis(text, digest, row)

    def present() -> str | None:
        served, _ = with_result_basis(text, digest, row)
        return None if served is text else served

    return _SERVED.serve(f"{digest}\0{key}", text, digest, present)


def with_result_basis_payload(payload: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """The decoded form of ``with_result_basis`` for results already in memory.

    Equal, as JSON, to decoding ``with_result_basis(json.dumps(payload), ...)``;
    ``payload`` itself is never mutated. Only the dictionaries the identity can
    touch are copied; every array and other branch is shared with the input.
    """

    presented = _copy_basis_path(payload)
    _apply_result_basis(presented, row)
    return presented


def _copy_basis_path(payload: Mapping[str, Any]) -> dict[str, Any]:
    def node(value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        copied = dict(value)
        if isinstance(copied.get("metadata"), Mapping):
            copied["metadata"] = dict(copied["metadata"])
        return copied

    presented: dict[str, Any] = node(payload)
    channels = presented.get("channels")
    if isinstance(channels, Mapping):
        presented["channels"] = {channel_id: node(channel) for channel_id, channel in channels.items()}
    return presented


def _basis_inputs_key(row: Mapping[str, Any]) -> str | None:
    """Name the saved-request inputs of the identity, or None when it cannot."""

    try:
        config = row.get("config_json") or {}
        geometry = config.get("geometry") or {}
        kind = geometry.get("type")
        if kind != "imported":
            return json.dumps(kind)
        inputs = json.dumps([geometry, config.get("options")], sort_keys=True, allow_nan=False)
    except (AttributeError, TypeError, ValueError):
        return None
    return hashlib.sha256(inputs.encode()).hexdigest()


def _apply_result_basis(payload: dict[str, Any], row: Mapping[str, Any]) -> bool:
    """Add the read-time identity to decoded ``payload`` in place; True if it changed."""

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
        drive = metadata.get("impedance_drive")
        # Explicit drive evidence first; a saved driver whose coupling was deferred
        # (passive cardioid) leaves the channel prescribed.
        voltage = drive in ("rms_voltage", "voltage") or (
            drive is None and not metadata.get("driver_coupling_deferred_to")
            and (channel_id in drivers or bool(metadata.get("driver"))))
        if voltage:
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
    return changed
