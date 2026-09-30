"""Engine-independent retained-field result passed across the worker boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class FieldPlaneEvaluation:
    frequency_hz: float
    pressure: NDArray[np.complex64]
    geometry_sha256: str
    synthesis_revision: str
    symmetry_plane: str | None
    sampling: dict[str, Any] | None = None
