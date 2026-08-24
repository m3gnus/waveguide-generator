"""Shared source-role canonicalization for CAD-return consumers."""

from __future__ import annotations


_BAND_ROLES = frozenset({"LF", "MF", "HF"})


def canonical_source_role(role: str) -> str:
    """Canonicalize driver-band roles without rewriting structural roles."""

    band_role = role.strip().upper()
    return band_role if band_role in _BAND_ROLES else role
