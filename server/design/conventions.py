"""Machine-readable coordinate, unit, and phasor conventions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Final


# Keep aligned with docs/reference/EXPORT-CONTRACTS.md (the STL row documents
# the axis remap + winding rule) and docs/reference/RESULT-CONTRACTS.md (phase
# and phasor semantics). FRAME-SPEC.md is the binary wire format, not this
# contract.
ARTIFACT_CONVENTIONS: Final[dict[str, Any]] = {
    "frame": {
        "axes": {
            "x": "horizontal",
            "y": "vertical",
            "z": "axial (throat to mouth)",
        },
        "axis_remap_matrix": [
            [1, 0, 0],
            [0, -1, 0],
            [0, 0, 1],
        ],
        "winding": "reversed-on-remap",
    },
    "units": {
        "solver_length": "m",
        "cad_length": "mm",
        "frequency": "Hz",
        "phase": "degrees",
    },
    "phasor": "exp(-i omega t)",
}


def artifact_conventions() -> dict[str, Any]:
    """Return a JSON-safe copy so artifact assembly cannot mutate the contract."""

    return deepcopy(ARTIFACT_CONVENTIONS)


__all__ = ["ARTIFACT_CONVENTIONS", "artifact_conventions"]
