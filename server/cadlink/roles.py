"""Shared source-role canonicalization for CAD-return consumers."""

from __future__ import annotations


from .wglink_protocol import SOURCE_ROLES

_BAND_ROLES = frozenset(SOURCE_ROLES[:3])


def canonical_source_role(role: str) -> str:
    """Canonicalize driver-band roles without rewriting structural roles."""

    band_role = role.strip().upper()
    return band_role if band_role in _BAND_ROLES else role
