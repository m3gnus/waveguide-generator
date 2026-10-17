"""Shared source-role canonicalization for CAD-return consumers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any

from server.contracts.canonical import canonical_utf8_strict_json

from .wglink_protocol import SOURCE_ROLES

# Named rather than sliced from the shared vocabulary, so a reordered or
# extended SOURCE_ROLES cannot silently change which roles are driver bands.
_BAND_ROLES = frozenset({"LF", "MF", "HF"})
if not _BAND_ROLES <= frozenset(SOURCE_ROLES):
    raise ImportError(
        "WGLink shared protocol no longer defines the driver-band roles "
        f"{sorted(_BAND_ROLES - frozenset(SOURCE_ROLES))}"
    )


def canonical_source_role(role: str) -> str:
    """Canonicalize driver-band roles without rewriting structural roles."""

    band_role = role.strip().upper()
    return band_role if band_role in _BAND_ROLES else role


def inventory_sha256(sources: Sequence[Mapping[str, Any]]) -> str:
    """A source inventory's identity: which sources, in which roles, required or not.

    Roles are canonical (``canonical_source_role``), as the returns listing and
    so the frontend state them, whatever spelling the manifest used.

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
            canonical_source_role(str(item.get("role") or "")),
            bool(item.get("required")),
        ]
        for item in sources
        if isinstance(item, Mapping)
    )
    return "sha256:" + hashlib.sha256(
        canonical_utf8_strict_json(entries).encode("utf-8")
    ).hexdigest()
