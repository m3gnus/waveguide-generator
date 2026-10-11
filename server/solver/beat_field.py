"""Exterior fields from persisted BEAT P1 pressure and DP0 derivative traces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import importlib
import math
from pathlib import Path
from typing import Any

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION

from .beat_adapter.mesh import SurfaceMesh, read_surface
from .beat_adapter.request import _image_frame
from .beat_runtime import readiness
from .beat_runtime.manager import get_manager
from .beat_runtime.session import SolveSession
from .field_traces_store import ArtifactCorrupt


def field_status() -> Mapping[str, Any]:
    """Use any proved official runtime; field traces do not depend on a device."""
    cpu = readiness.backend_status("cpu")
    if cpu["available"]:
        return cpu
    for backend in ("metal", "cuda", "rocm"):
        status = readiness.backend_status(backend)
        if status["available"]:
            return status
    return cpu


def load_mesh(path: Path, *, native_symmetry_plane: str | None = None) -> SurfaceMesh:
    """Preserve the original metre-frame node and triangle row ordering."""
    try:
        return read_surface(Path(path).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ArtifactCorrupt(f"Invalid retained BEAT surface mesh: {exc}") from exc


def _stage_array(directory: Path, name: str, values: Any, dtype: str) -> dict[str, Any]:
    array = np.ascontiguousarray(values, dtype=dtype)
    filename = f"{name}.bin"
    (directory / filename).write_bytes(array.tobytes(order="C"))
    return dict(file=filename, offset=0, nbytes=array.nbytes, shape=list(array.shape),
                dtype=array.dtype.name, order="C", byte_order="little")


def _read_result(directory: Path, event: Mapping[str, Any], count: int) -> np.ndarray:
    descriptor = event.get("values_binary")
    expected = dict(file="field.bin", offset=0, nbytes=count * 8, shape=[count],
                    dtype="complex64", order="C", byte_order="little")
    if (event.get("phasor_convention") != SOLVER_TIME_CONVENTION
            or descriptor != expected):
        raise RuntimeError("BEAT field result has an invalid binary array or phase convention")
    path = directory / "field.bin"
    if not path.is_file() or path.stat().st_size != expected["nbytes"]:
        raise RuntimeError("BEAT field result is missing or truncated")
    pressure = np.frombuffer(path.read_bytes(), dtype="<c8").copy()
    if not np.isfinite(pressure).all():
        raise RuntimeError("BEAT field result contains nonfinite pressure")
    return pressure


def evaluate_exterior_from_traces(
    mesh: SurfaceMesh, frequency_hz: float, k_real: float,
    pressure_p1: Any, neumann_dp0: Any, points_m: Any, *,
    symmetry_plane: str | None = None,
    cancellation_callback: Callable[[], None] | None = None,
) -> np.ndarray:
    """Run BEAT's own potential evaluator, without solving the boundary again.

    WG stores solver exp(-i omega t) Pa / Pa/m traces after acceleration and
    channel scaling. These are the physical traces, including Burton-Miller
    solutions, so the field operation takes them unchanged. CAD traces belong
    to the ingested mesh: rotate mesh and points together only for a Y image.
    """
    from .field_plane import FieldPlaneUnsupported

    status = field_status()
    if not status["available"]:
        raise FieldPlaneUnsupported(f"BEAT field runtime is unavailable: {status['reason']}")
    if not math.isfinite(k_real) or k_real <= 0:
        raise ArtifactCorrupt("Retained BEAT field wavenumber must be positive and finite")
    pressure = np.asarray(pressure_p1)
    neumann = np.asarray(neumann_dp0)
    if pressure.shape != (len(mesh.points_m),) or neumann.shape != (len(mesh.faces),):
        raise ArtifactCorrupt("Retained BEAT traces do not align with mesh nodes and triangles")
    try:
        symmetry, rotation, translation = _image_frame(symmetry_plane or "full", None)
    except ValueError as exc:
        raise FieldPlaneUnsupported(str(exc)) from exc
    vertices = np.asarray((mesh.points_m + translation) @ rotation.T, dtype="<f4")
    points = np.asarray((np.asarray(points_m) + translation) @ rotation.T, dtype="<f4")
    # Repeat the solve adapter's snapping in the image frame. Scalar traces
    # retain their row order under this proper rigid rotation.
    if symmetry in {"x", "xy"}:
        tolerance = max(1e-9, float(np.linalg.norm(np.ptp(vertices, axis=0))) * 1e-6)
        for axis in ((0,) if symmetry == "x" else (0, 1)):
            vertices[np.abs(vertices[:, axis]) <= tolerance, axis] = 0.
    key = hashlib.sha256(vertices.tobytes() + np.asarray(mesh.faces, dtype="<i8").tobytes()).hexdigest()
    try:
        contract = importlib.import_module("beat_engine.beat_contract.worker")
        with SolveSession(cancellation_callback=cancellation_callback) as session:
            directory = session.directory
            assert directory is not None
            arrays = {
                "boundary_points_m": _stage_array(directory, "vertices", vertices, "<f4"),
                "boundary_triangles": _stage_array(directory, "triangles", mesh.faces, "<i8"),
                "boundary_pressure": _stage_array(directory, "pressure", pressure, "<c8"),
                "boundary_normal_derivative": _stage_array(directory, "neumann", neumann, "<c8"),
                "points_m": _stage_array(directory, "points", points, "<f4"),
            }
            request = dict(
                binary_array_schema_version=1, binary_arrays=arrays,
                binary_result_file="field.bin", domain_key=key,
                frequency_hz=float(frequency_hz),
                sound_speed_m_per_s=2 * math.pi * frequency_hz / k_real,
                precision="float32", bem_backend=status["backend"], symmetry=symmetry,
                phasor_convention=SOLVER_TIME_CONVENTION, quadrature_order=4,
            )
            session.submit(get_manager().get_worker(status["backend"]), request,
                           operation="bem_field", negotiate=contract.negotiate_submission)
            result = None
            completed = False
            for event in session.events():
                kind = event.get("type")
                if kind == "field_result":
                    if result is not None:
                        raise RuntimeError("BEAT returned more than one field result")
                    # events() closes staging when it reaches the terminal.
                    result = _read_result(directory, event, len(points))
                elif kind == "completed":
                    completed = True
                elif kind in {"failed", "cancelled"}:
                    raise RuntimeError(str(event.get("error") or f"BEAT field {kind}"))
            if result is None or not completed:
                raise RuntimeError("BEAT field evaluation ended without a completed pressure result")
            session.raise_callback_error()
            return result
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        raise FieldPlaneUnsupported(f"BEAT field evaluation unavailable: {exc}") from exc
