"""Time conventions and their existing serialized phase tags.

Solvers use exp(-i omega t), with outgoing waves exp(+ikr). WG filters and
driver models use engineering exp(+j omega t). Conjugation translates the
same physical signal between these conventions; it changes no magnitudes.
The phase tags below preserve the established result and artifact formats.
"""

from typing import Any

import numpy as np


SOLVER_TIME_CONVENTION = "exp(-i omega t)"
ENGINEERING_TIME_CONVENTION = "exp(+j omega t)"
# Despite its historical key, this result metadata value names the outgoing
# spatial phase, not the time exponential.
PHASE_TIME_CONVENTION = "exp(+ikr)"
SOLVER_PHASE_CONVENTION = "solver_exp_plus_ikr"
ENGINEERING_PHASE_CONVENTION = "engineering_exp_plus_jwt"


def solver_to_engineering(z: Any) -> Any:
    """Conjugate solver exp(-i omega t) phasors for WG exp(+j omega t).

    Opposite time signs require conjugation to describe the same real signal.
    Delegate directly to NumPy to preserve scalar/array types and exact bits.
    """

    return np.conj(z)


def engineering_to_solver(z: Any) -> Any:
    """Conjugate WG exp(+j omega t) phasors for solver exp(-i omega t).

    Engineering filters, delays and acceleration scales must cross this
    boundary before multiplying raw exp(+ikr) pressure or Neumann fields.
    """

    return np.conj(z)
