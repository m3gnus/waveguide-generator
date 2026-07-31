"""Canonical Mesh.Quadrants normalisation shared by the mesher and solvers.

The mesh that gets built and the symmetry the solver is told about must come
from the same reading of ``Mesh.Quadrants``. They used not to: the mesher
adapter followed Ath's rule (anything unrecognised is a quarter model) while
the solver adapters looked the raw value up in a dict and fell through to
``None`` -- full domain -- for the same input. For ``quadrants`` values such as
``0``, ``""``, ``2``, ``13``, ``21``, ``"1,2"`` or ``"x14"`` the mesher built a
quarter shell and the solver then treated it as a complete free-space model,
solving a quarter of a horn as an open sheet.

Ath is the authority here, and its semantics are reproduced in
``hornlab_mesher.profile_common``. That package is an optional dependency, so
the rules are restated rather than imported; ``tests/test_quadrants.py`` asserts
the two agree whenever the mesher is installed, which is what stops them
drifting apart again.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = [
    "FULL_DOMAIN_QUADRANTS",
    "native_symmetry_plane_for_quadrants",
    "normalise_quadrants",
    "quadrants_leading_int",
]

FULL_DOMAIN_QUADRANTS = 1234

_LEADING_INT_RE = re.compile(r"^[+-]?\d+")

# Ath recognises exactly these; everything else is a quarter model.
_CANONICAL = (1234, 12, 14)

# HornLab/WG quadrants are in the transverse X/Y plane after mesher export.
# Quadrant 1 is bounded by both planes; 14 keeps X >= 0 (mirror across YZ);
# 12 keeps Y >= 0 (mirror across XZ).
_PLANE_BY_QUADRANTS = {
    1: "yz+xz",
    12: "xz",
    14: "yz",
    1234: None,
}


def quadrants_leading_int(value: Any) -> int:
    """Read ``Mesh.Quadrants`` as a leading integer, the way Ath does.

    Ath parses with C ``atoi`` / Pascal ``Val`` semantics: skip leading
    whitespace, take an optional sign and the run of digits after it, stop at
    the first non-digit. Trailing junk is ignored, so ``"1234x"`` reads as
    ``1234`` and ``"1,2"`` as ``1``, while ``"x1234"``, ``""`` and ``None`` all
    read as ``0``. Digits are never reordered -- ``"21"`` is ``21`` (a quarter
    model), not the set ``{1, 2}``.
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = _LEADING_INT_RE.match(str(value).strip())
    return int(match.group(0)) if match else 0


def normalise_quadrants(value: Any) -> int:
    """Normalise ``Mesh.Quadrants`` to Ath's canonical coverage.

    Returns one of ``1234`` (full), ``12`` (top half), ``14`` (right half) or
    ``1`` (quarter). Never raises: an unrecognised, empty or malformed value is
    a quarter model, exactly as Ath treats it.
    """
    leading = quadrants_leading_int(value)
    return leading if leading in _CANONICAL else 1


def native_symmetry_plane_for_quadrants(value: Any) -> str | None:
    """Mirror plane implied by ``Mesh.Quadrants``; ``None`` for a full model."""
    return _PLANE_BY_QUADRANTS[normalise_quadrants(value)]
