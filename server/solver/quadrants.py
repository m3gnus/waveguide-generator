"""Compatibility aliases for the shared ATH geometry contract."""

from server.contracts.geometry import (
    FULL_DOMAIN_QUADRANTS as FULL_DOMAIN_QUADRANTS,
    PLANE_BY_QUADRANTS,
    native_symmetry_plane_for_quadrants as native_symmetry_plane_for_quadrants,
    normalise_quadrants as normalise_quadrants,
    quadrants_leading_int as quadrants_leading_int,
)

_PLANE_BY_QUADRANTS = PLANE_BY_QUADRANTS

__all__ = [
    "FULL_DOMAIN_QUADRANTS",
    "native_symmetry_plane_for_quadrants",
    "normalise_quadrants",
    "quadrants_leading_int",
]
