"""Shared source-role canonicalization for CAD-return consumers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any, Literal

from server.contracts.canonical import canonical_utf8_strict_json

from .wglink_protocol import (
    LEGACY_SOURCE_ROLE_ALIASES,
    SOURCE_ROLES,
    canonical_source_role as _protocol_source_role,
)

# Named rather than sliced from the shared vocabulary, so a reordered or
# extended SOURCE_ROLES cannot silently change which roles are driver bands.
_BAND_ROLES = frozenset({"LF", "MF", "HF"})
if not _BAND_ROLES <= frozenset(SOURCE_ROLES):
    raise ImportError(
        "WGLink shared protocol no longer defines the driver-band roles "
        f"{sorted(_BAND_ROLES - frozenset(SOURCE_ROLES))}"
    )


def normalize_source_role(
    role: object, *, mode: Literal["source", "band", "protocol"] = "source",
) -> str:
    """Normalize a source role while preserving each legacy caller's contract.

    The default composes the shared protocol alias with case-insensitive band
    and passive-role names. Historical inventories and CAD summaries use
    ``band`` (structural spelling affects their identity); file preparation
    uses ``protocol`` (its accepted input vocabulary is case-sensitive).
    """
    raw_role = str(role)
    if mode == "protocol":
        return _protocol_source_role(raw_role)
    normalized = raw_role.strip().upper()
    if normalized in _BAND_ROLES:
        return normalized
    if mode == "band":
        return raw_role
    if normalized in SOURCE_ROLES or normalized in LEGACY_SOURCE_ROLE_ALIASES:
        return _protocol_source_role(normalized)
    return raw_role


def inventory_sha256(sources: Sequence[Mapping[str, Any]]) -> str:
    """A source inventory's identity: which sources, in which roles, required or not.

    Band roles are normalized, as they are in the returns listing and the
    frontend, whatever spelling the manifest used.

    The source ``id`` is part of the key. When a return requires
    ``source-identity-v1`` that id is the CAD-authored identity of the logical
    source, the same across exports, so a project's setup follows its sources
    from one export to the next and a reassigned identity does not inherit it.
    The byte shape is unchanged, so an inventory from a return without the
    feature keeps its exact digest.
    """

    entries = sorted(
        [
            str(item.get("id") or ""),
            normalize_source_role(str(item.get("role") or ""), mode="band"),
            bool(item.get("required")),
        ]
        for item in sources
        if isinstance(item, Mapping)
    )
    return "sha256:" + hashlib.sha256(
        canonical_utf8_strict_json(entries).encode("utf-8")
    ).hexdigest()


def prepared_inventory_sha256(record: Mapping[str, Any]) -> str:
    """Keep legacy file setups under their historical inventory key.

    File requests using source_tags now record the canonical HF role, but
    their saved setups predate roles and hash SOURCE. Explicit role assignments
    use the ordinary role inventory; CAD records are unchanged.
    """
    sources = record.get("sources") or []
    if record.get("producer") == "mesh-file" and "roles" not in (record.get("source_file") or {}):
        sources = [{**source, "role": "SOURCE" if source.get("role") == "HF" else source.get("role")}
                   for source in sources]
    return inventory_sha256(sources)
