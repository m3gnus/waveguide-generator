"""Canonical ATH ``Mesh.Quadrants`` semantics shared by mesh and solvers.

The exact leading-integer parsing and symmetry mapping port v1
``server/solver/quadrants.py:1-81``; using one implementation prevents a
quarter mesh from being accidentally solved as a full-domain open sheet.
"""

from __future__ import annotations

import re
from typing import Any


FULL_DOMAIN_QUADRANTS = 1234
_LEADING_INT_RE = re.compile(r"^[+-]?\d+")
_CANONICAL = (1234, 12, 14)
_PLANE_BY_QUADRANTS = {1: "yz+xz", 12: "xz", 14: "yz", 1234: None}


def quadrants_leading_int(value: Any) -> int:
    """Parse like ATH/C ``atoi`` (v1 ``quadrants.py:48-65``)."""

    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = _LEADING_INT_RE.match(str(value).strip())
    return int(match.group(0)) if match else 0


def normalise_quadrants(value: Any) -> int:
    """Return 1234, 12, 14, or ATH's quarter-domain fallback 1."""

    leading = quadrants_leading_int(value)
    return leading if leading in _CANONICAL else 1


def native_symmetry_plane_for_quadrants(value: Any) -> str | None:
    """Map reduced mesh coverage to Metal/BEMPP native image planes."""

    return _PLANE_BY_QUADRANTS[normalise_quadrants(value)]


__all__ = [
    "FULL_DOMAIN_QUADRANTS",
    "native_symmetry_plane_for_quadrants",
    "normalise_quadrants",
    "quadrants_leading_int",
]
