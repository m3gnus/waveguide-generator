"""Shared source-role canonicalization for CAD-return consumers."""

from __future__ import annotations


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
