"""BEM formulation choice for the Metal and BEMPP adapters.

This module is the one place that decides which wavenumber treatment a solve
runs with. The adapters execute it, record it in the result metadata (which the
power-qualification provenance reads), and the job runtime folds it into a
submission's identity, so what runs, what is recorded and what a replay is
compared against cannot drift apart.
"""

from __future__ import annotations

from typing import NamedTuple

DEFAULT_BEM_FORMULATION = "complex_k"
DEFAULT_COMPLEX_K_SHIFT = 0.005

#: Real wavenumber, no stabilisation shift.
STANDARD_BEM_FORMULATION = "standard"
STANDARD_COMPLEX_K_SHIFT = 0.0


class BemFormulation(NamedTuple):
    """The formulation name the native solver is given and its shift."""

    formulation: str
    complex_k_shift: float


def bem_formulation(*, coupled_infinite_baffle: bool) -> BemFormulation:
    """The formulation a Fast Metal or BEMPP solve uses.

    Free-standing and ground-plane solves are exterior problems. A plain
    boundary-integral equation on a closed exterior surface has fictitious
    resonances (of the space the mesh encloses), so they use ``complex_k``: a
    small imaginary shift of the wavenumber that suppresses them, at the price
    of slight numerical damping.

    A coupled infinite baffle is different. The horn is an interior problem
    closed by the radiating aperture, so there is no enclosed exterior space and
    no fictitious frequency for the shift to suppress. CHIEF, the other remedy,
    is refused on this path. The cavity resonances it does have are real and are
    damped by radiation through the aperture, not by the shift, so ``complex_k``
    only made them look milder than they are. Real ``standard`` is also the
    native packages' own default. It is validated against the landed
    pipe-reference gates in hornlab-metal-bem
    (``tests/test_native_coupled_ib_validation.py``) and hornlab-bempp-bem
    (``tests/test_infinite_baffle.py``).
    """

    if coupled_infinite_baffle:
        return BemFormulation(STANDARD_BEM_FORMULATION, STANDARD_COMPLEX_K_SHIFT)
    return BemFormulation(DEFAULT_BEM_FORMULATION, DEFAULT_COMPLEX_K_SHIFT)


__all__ = [
    "BemFormulation",
    "DEFAULT_BEM_FORMULATION",
    "DEFAULT_COMPLEX_K_SHIFT",
    "STANDARD_BEM_FORMULATION",
    "STANDARD_COMPLEX_K_SHIFT",
    "bem_formulation",
]
