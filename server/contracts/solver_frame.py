"""The solver frame contract's axes and matrices (cad-solver-frame-v1 and -v2).

Pure frame arithmetic, shared by CAD interpretation, which decides the frame a
return is solved in (``server/cadlink/interpretation/solver_frame.py``), and the
mesher, which applies it. ``spec_matrix`` is the one producer of
``solver_from_assembly``; docs/architecture/CAD-OPERATIONS.md describes the
contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from server.contracts.wglink_protocol import _wg_DOCUMENT_UP_AXES as DOCUMENT_UP_AXES


CONTRACT_V1 = "cad-solver-frame-v1"
CONTRACT_V2 = "cad-solver-frame-v2"
AS_MODELLED = "+z"
AXES: tuple[str, ...] = ("+z", "-z", "+x", "-x", "+y", "-y")
UP_FROM_DOCUMENT = "document"
UP_DEFAULT = "default"
UP_FORWARD_PARALLEL = "forward-parallel-to-document-up"

_VECTORS: dict[str, tuple[float, float, float]] = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}

# Contract v1. Row-major rotations, solver_from_assembly. Each takes its axis
# to +Z by the minimal rotation.
ROTATIONS: dict[str, tuple[tuple[float, float, float], ...]] = {
    "+z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    # 180 degrees about X.
    "-z": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
    # -90 degrees about Y: +X -> +Z, +Z -> -X.
    "+x": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
    # +90 degrees about Y: -X -> +Z, +Z -> +X.
    "-x": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
    # +90 degrees about X: +Y -> +Z, +Z -> -Y.
    "+y": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    # -90 degrees about X: -Y -> +Z, +Z -> +Y.
    "-y": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
}


def require_axis(axis: object) -> str:
    if not isinstance(axis, str) or axis not in ROTATIONS:
        raise ValueError(f"solver frame axis must be one of {', '.join(AXES)}, got {axis!r}")
    return axis


def frame_matrix(axis: str) -> np.ndarray:
    """Contract v1's ``solver_from_assembly`` for one axis (historical records)."""

    rotation = ROTATIONS[require_axis(axis)]
    matrix = np.eye(4)
    matrix[:3, :3] = np.asarray(rotation, dtype=float)
    return matrix


def resolve_up(forward: str, stated_up: str | None) -> tuple[str, str]:
    """The model's up for a forward axis, and where it came from."""

    require_axis(forward)
    if stated_up is None:
        return ("+y" if forward[1] == "z" else "+z"), UP_DEFAULT
    if stated_up not in DOCUMENT_UP_AXES:
        raise ValueError(f"document up must be one of {', '.join(DOCUMENT_UP_AXES)}, got {stated_up!r}")
    if forward[1] == stated_up[1]:
        # Radiating along the document's own up (or down): no roll to take
        # from it, so the other of the two up axes a CAD document can have.
        return ("+z" if stated_up == "+y" else "+y"), UP_FORWARD_PARALLEL
    return stated_up, UP_FROM_DOCUMENT


def _v2_matrix(axis: str, up: str) -> np.ndarray:
    forward = np.asarray(_VECTORS[require_axis(axis)])
    upward = np.asarray(_VECTORS[require_axis(up)])
    if abs(float(forward @ upward)) > 0.5:
        raise ValueError(f"up {up} is parallel to the forward axis {axis}")
    matrix = np.eye(4)
    matrix[0, :3] = np.cross(upward, forward)
    matrix[1, :3] = upward
    matrix[2, :3] = forward
    return matrix


def spec_matrix(spec: str | Mapping[str, Any]) -> np.ndarray:
    """``solver_from_assembly`` for a frame: a v1 axis, or a v1/v2 frame spec.

    A bare axis string is contract v1, which is how preparations before v2
    named their frame.
    """

    if isinstance(spec, str):
        return frame_matrix(spec)
    if not isinstance(spec, Mapping):
        raise ValueError(f"solver frame must be an axis or a frame, got {spec!r}")
    contract = spec.get("contract")
    if contract == CONTRACT_V1:
        return frame_matrix(spec.get("axis"))  # type: ignore[arg-type]
    if contract == CONTRACT_V2:
        axis = require_axis(spec.get("axis"))
        up, source = resolve_up(axis, spec.get("document_up"))
        if spec.get("up") != up or spec.get("up_source") != source:
            raise ValueError(
                f"solver frame up {spec.get('up')!r} ({spec.get('up_source')!r}) is not the "
                f"contract's {up!r} ({source!r}) for {axis} with document up "
                f"{spec.get('document_up')!r}"
            )
        return _v2_matrix(axis, up)
    raise ValueError(f"unknown solver frame contract {contract!r}")


def spec_axis(spec: str | Mapping[str, Any]) -> str:
    """The forward axis a frame names."""

    axis = spec if isinstance(spec, str) else spec.get("axis") if isinstance(spec, Mapping) else None
    return require_axis(axis)
