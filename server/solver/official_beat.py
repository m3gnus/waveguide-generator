"""Opt-in official BEAT worker bridge; deliberately not registered for routing yet.

Only a parametric, free-air, normal-velocity exterior source is translated.
The installed ``beat_engine`` owns its versioned Julia protocol; this module
owns HornLab's geometry, units, cancellation and normalized result contract.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np

from server.jobs.models import ImportedGeometrySource, SolveRequest
from server.mesh.builder import build_solver_mesh

from .base import (
    ArtifactCallback,
    CancelCallback,
    EngineRunResult,
    FULL3D_SOLVER_PORT_MARKER,
    ResultCallback,
    StageCallback,
)
from .context import SolverContext
from .frequency_sweep import live_execution_frequencies
from .quadrants import FULL_DOMAIN_QUADRANTS
from .result_mapping import (
    REFERENCE_AIR_DENSITY_KG_PER_M3,
    _custom_observation_points,
    build_provisional_frequency_response,
    build_solver_response,
    native_observation_frame,
)


PHASOR = "exp(-i omega t)"
SOURCE_ID = "excitation:source"
PRESSURE_ID = "pressure"
IMPEDANCE_ID = "impedance"
SOUND_SPEED_M_PER_S = 343.0


class OfficialBeatUnavailable(RuntimeError):
    """The requested official worker capability or physics is unavailable."""


class OfficialBeatProtocolError(RuntimeError):
    """A worker result did not match the negotiated compiled-system contract."""


def preflight(request: SolveRequest, imported_record: Mapping[str, Any] | None = None) -> SolverContext:
    """Reject unsupported physics before meshing or publishing any artifact."""

    if request.options.ground_plane.enabled:
        raise OfficialBeatUnavailable("Official BEAT bridge does not support a rigid ground plane")
    if imported_record is not None or isinstance(request.geometry, ImportedGeometrySource):
        raise OfficialBeatUnavailable("Official BEAT bridge does not support imported or multi-source geometry")
    if (request.options.solver_mode or "").strip().lower() == "circsym":
        raise OfficialBeatUnavailable("Official BEAT bridge requires a full-3D solve")
    context = SolverContext.from_request(request, solver_mode="full_3d")
    if context.sim_type != 2:
        raise OfficialBeatUnavailable("Official BEAT bridge does not support a coupled infinite baffle")
    if context.source_motion != "normal":
        raise OfficialBeatUnavailable("Official BEAT compiled normal velocity cannot represent axial piston motion")
    if context.quadrants != FULL_DOMAIN_QUADRANTS:
        raise OfficialBeatUnavailable("Official BEAT bridge has not qualified reduced-domain symmetry")
    if context.polar_config.get("spherical_sampling"):
        raise OfficialBeatUnavailable("Official BEAT bridge does not translate spherical observation sampling")
    return context


def decode_complex_values(values: Any, shape: tuple[int, ...]) -> np.ndarray:
    """Decode a BEAT binary complex descriptor without lossy coercion."""

    if not isinstance(values, dict):
        raise OfficialBeatProtocolError("quantity values must be a binary descriptor")
    dtype = values.get("dtype")
    if (
        values.get("encoding") != "base64"
        or values.get("order") != "C"
        or values.get("byte_order") != "little"
        or dtype not in {"complex64", "complex128"}
        or values.get("shape") != list(shape)
    ):
        raise OfficialBeatProtocolError("quantity binary descriptor does not match expected encoding or shape")
    payload = values.get("content_base64")
    if not isinstance(payload, str):
        raise OfficialBeatProtocolError("quantity binary payload is missing")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OfficialBeatProtocolError("quantity binary payload is invalid base64") from exc
    item = np.dtype("<c8" if dtype == "complex64" else "<c16")
    if len(raw) != math.prod(shape) * item.itemsize:
        raise OfficialBeatProtocolError("quantity binary payload has the wrong byte count")
    decoded = np.frombuffer(raw, dtype=item).reshape(shape).astype(np.complex128)
    if not np.isfinite(decoded).all():
        raise OfficialBeatProtocolError("quantity binary payload contains non-finite complex values")
    return decoded


def _source_area_m2(msh_text: str) -> float:
    """Area of physical-tag-2 triangles in the authoritative Gmsh 2.2 mesh."""

    lines = iter(msh_text.splitlines())
    nodes: dict[int, np.ndarray] = {}
    area = 0.0
    for line in lines:
        if line == "$Nodes":
            for _ in range(int(next(lines))):
                fields = next(lines).split()
                nodes[int(fields[0])] = np.asarray([float(v) for v in fields[1:4]])
        elif line == "$Elements":
            for _ in range(int(next(lines))):
                fields = [int(v) for v in next(lines).split()]
                tag_count = fields[2]
                if fields[1] != 2 or tag_count == 0 or fields[3] != 2:
                    continue
                a, b, c = (nodes[index] for index in fields[3 + tag_count : 6 + tag_count])
                area += float(np.linalg.norm(np.cross(b - a, c - a))) / 2.0
    if not math.isfinite(area) or area <= 0:
        raise OfficialBeatUnavailable("Source physical tag 2 has no positive-area triangles")
    return area


def _scaled_frame_mesh(msh_text: str, scale_to_m: float) -> str:
    """Scale only the in-memory observation frame; the worker scales its mesh."""

    if scale_to_m == 1.0:
        return msh_text
    lines = msh_text.splitlines()
    try:
        start = lines.index("$Nodes") + 1
        count = int(lines[start])
    except (ValueError, IndexError) as exc:
        raise OfficialBeatUnavailable("Observation mesh is not Gmsh 2.2 ASCII") from exc
    for index in range(start + 1, start + 1 + count):
        parts = lines[index].split()
        if len(parts) != 4:
            raise OfficialBeatUnavailable("Observation mesh has an invalid node row")
        lines[index] = " ".join([parts[0]] + [format(float(v) * scale_to_m, ".17g") for v in parts[1:]])
    return "\n".join(lines) + "\n"


def _observation(context: SolverContext, msh_text: str,
                 scale_to_m: float) -> tuple[list[str], np.ndarray, dict[str, np.ndarray]]:
    planes = list(context.polar_config["enabled_axes"])
    start, end, count = context.polar_config["angle_range"]
    angles = np.linspace(float(start), float(end), int(count))
    points = _custom_observation_points(context, _scaled_frame_mesh(msh_text, scale_to_m))
    if any(points[plane].shape != (len(angles), 3) for plane in planes):
        raise OfficialBeatUnavailable("Source-tag observation frame is invalid")
    return planes, angles, points


def build_compiled_request(
    mesh_path: Path, cancel_path: Path, context: SolverContext, msh_text: str, *, backend: str,
    precision: str, mesh_scale_to_m: float = 1.0,
) -> tuple[dict[str, Any], list[str], np.ndarray, float]:
    """Translate one HornLab normal source to the official exterior contract."""

    if backend not in {"cpu", "metal"}:
        raise OfficialBeatUnavailable(f"Official BEAT bridge backend {backend!r} is not qualified")
    if precision not in {"float32", "float64"}:
        raise OfficialBeatUnavailable(f"Official BEAT precision {precision!r} is unsupported")
    if backend == "metal" and precision != "float32":
        raise OfficialBeatUnavailable("Official BEAT Metal bridge is qualified only for float32")
    if not math.isfinite(mesh_scale_to_m) or mesh_scale_to_m <= 0:
        raise OfficialBeatUnavailable("Mesh scale to metres must be finite and positive")
    planes, angles, points = _observation(context, msh_text, mesh_scale_to_m)
    outputs = [
        {"id": f"{PRESSURE_ID}:{plane}", "quantity": "exterior_pressure", "target_ids": [],
         "options": {"points_m": points[plane].tolist()}}
        for plane in planes
    ]
    outputs.append({"id": IMPEDANCE_ID, "quantity": "radiation_impedance", "target_ids": [], "options": {}})
    request = {
        "schema_version": 1,
        "compiled_system": {
            "id": "system:hornlab", "name": "HornLab normal-velocity exterior", "contract_version": 1,
            "meshes": [{"id": "mesh:surface", "name": "Surface", "file": str(mesh_path.resolve()),
                        "purpose": "bem_surface", "scale_to_m": mesh_scale_to_m,
                        "translation_m": [0.0, 0.0, 0.0]}],
            "regions": [{"id": "region:air", "name": "Air", "kind": "unbounded_air",
                         "mesh_ids": ["mesh:surface"], "volume_groups": [],
                         "sound_speed_m_per_s": SOUND_SPEED_M_PER_S,
                         "density_kg_per_m3": REFERENCE_AIR_DENSITY_KG_PER_M3, "loss_model": {}}],
            "boundaries": [{"id": "boundary:source", "name": "Source", "kind": "moving",
                            "region_id": "region:air", "group": {"mesh_id": "mesh:surface", "dimension": 2,
                                                               "tag": 2, "name": None}, "parameters": {}}],
            "interfaces": [],
            "components": [{"id": "component:source", "name": "Source", "kind": "ideal_velocity_source",
                            "boundary_ids": ["boundary:source"], "parameters": {}}],
            "excitation_ports": [{"id": SOURCE_ID, "name": "Source", "component_id": "component:source",
                                  "kind": "normal_velocity"}],
        },
        "frequencies_hz": live_execution_frequencies(context).tolist(),
        "excitation_port_ids": [SOURCE_ID],
        "outputs": outputs,
        "solver_options": {"precision": precision, "bem_backend": backend, "symmetry": "off",
                           "phasor_convention": PHASOR, "regular_quadrature_mode": "fixed",
                           "quadrature_order": 4, "singular_order": 4},
        "cancel_path": str(cancel_path.resolve()),
    }
    return request, planes, angles, _source_area_m2(msh_text) * mesh_scale_to_m**2


def _quantity(result: dict[str, Any], output_id: str, quantity: str, unit: str,
              axes: list[str], shape: tuple[int, ...]) -> np.ndarray:
    matches = [item for item in result.get("quantities", []) if item.get("id") == output_id]
    if len(matches) != 1:
        raise OfficialBeatProtocolError(f"Expected exactly one {output_id} quantity")
    item = matches[0]
    if (item.get("quantity"), item.get("unit"), item.get("axes")) != (quantity, unit, axes):
        raise OfficialBeatProtocolError(f"{output_id} quantity, unit or axes changed")
    return decode_complex_values(item.get("values"), shape)


def parse_result(result: Any, *, frequency_hz: float, planes: list[str],
                 angles: np.ndarray, source_area_m2: float,
                 backend: str | None = None, precision: str | None = None) -> tuple[np.ndarray, complex]:
    if not isinstance(result, dict) or type(result.get("schema_version")) is not int or result["schema_version"] != 2:
        raise OfficialBeatProtocolError("Expected system_result schema version 2")
    observed_frequency = result.get("freq_hz")
    if (type(observed_frequency) not in {int, float} or not math.isfinite(observed_frequency)
            or observed_frequency != frequency_hz or result.get("excitation_port_ids") != [SOURCE_ID]):
        raise OfficialBeatProtocolError("Result frequency or excitation identity changed")
    expected_ids = [f"{PRESSURE_ID}:{plane}" for plane in planes] + [IMPEDANCE_ID]
    items = result.get("quantities")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items) or [item.get("id") for item in items] != expected_ids:
        raise OfficialBeatProtocolError("Result outputs changed order, identity or count")
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, dict) or diagnostics.get("phasor_convention") != PHASOR:
        raise OfficialBeatProtocolError("Result phasor convention is unqualified")
    if backend is not None and diagnostics.get("bem_backend") != backend:
        raise OfficialBeatProtocolError("Result backend differs from selected worker")
    if precision is not None and diagnostics.get("precision") != precision:
        raise OfficialBeatProtocolError("Result precision differs from selected worker")
    if diagnostics.get("symmetry") != "off":
        raise OfficialBeatProtocolError("Result symmetry differs from requested full domain")
    pressure = np.stack([
        _quantity(result, f"{PRESSURE_ID}:{plane}", "exterior_pressure", "Pa",
                  ["excitation", "observation"], (1, len(angles)))[0]
        for plane in planes
    ])
    force = _quantity(result, IMPEDANCE_ID, "radiation_impedance", "N*s/m", ["radiator"], (1,))[0]
    # Official impedance is total force on the physical radiator for 1 m/s.
    # HornLab's result mapper expects mean pressure for unit acceleration.
    acceleration_scale = 1.0 / (-1j * 2.0 * math.pi * frequency_hz)
    return pressure * acceleration_scale, complex(force / source_area_m2 * acceleration_scale)


def _native_result(frequencies: np.ndarray, angles: np.ndarray, planes: list[str],
                   pressure: np.ndarray, impedance: np.ndarray) -> SimpleNamespace:
    magnitudes = np.abs(pressure)
    levels = 20.0 * np.log10(np.maximum(magnitudes, np.finfo(float).tiny) / 20e-6)
    return SimpleNamespace(
        frequencies_hz=frequencies, observation_angles_deg=angles, observation_planes=planes,
        pressure_complex=pressure, directivity_db=levels, impedance=impedance,
    )


def resolve_julia_executable(explicit: str | None = None) -> str | None:
    """Use the app's existing provisioned runtime while the fork remains pinned.

    The final package replacement must move runtime provisioning into an
    application-owned service; the worker protocol itself has no Julia locator.
    """

    if explicit is not None:
        path = Path(explicit).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    try:
        legacy_runtime = importlib.import_module("hornlab_beat_bem.runtime")
        found = legacy_runtime.discover_julia()
        if found:
            return str(found)
    except ImportError:
        pass
    return shutil.which("julia")


def solve_official_beat_from_msh_text(
    msh_text: str, context: SolverContext, *, backend: str = "cpu", precision: str = "float32",
    stage_callback: StageCallback | None = None, cancellation_callback: CancelCallback | None = None,
    result_callback: ResultCallback | None = None, worker_factory: Any | None = None,
    julia_executable: str | None = None, mesh_scale_to_m: float = 1.0,
) -> dict[str, Any]:
    """Run one installed official worker, closing the stream before file cleanup."""

    if context.source_motion != "normal" or context.quadrants != FULL_DOMAIN_QUADRANTS or context.sim_type != 2 or context.ground_plane:
        raise OfficialBeatUnavailable("Official BEAT bridge accepts only full-domain free-air normal motion")
    if cancellation_callback:
        cancellation_callback()
    beat = importlib.import_module("beat_engine")
    contract = importlib.import_module("beat_engine.beat_contract.worker")
    julia = resolve_julia_executable(julia_executable)
    if not julia and worker_factory is None:
        raise OfficialBeatUnavailable("Julia executable is unavailable")
    started = time.time()
    worker = None
    stream = None
    monitor_stop = threading.Event()
    monitor_error: list[BaseException] = []
    watcher: threading.Thread | None = None
    with tempfile.TemporaryDirectory(prefix="hornlab-official-beat-") as directory:
        job_dir = Path(directory)
        mesh_path = job_dir / "surface.msh"
        cancel_path = job_dir / "cancel.marker"
        request_path = job_dir / "request.json"
        mesh_path.write_text(msh_text, encoding="utf-8")
        compiled, planes, angles, source_area = build_compiled_request(
            mesh_path, cancel_path, context, msh_text, backend=backend, precision=precision,
            mesh_scale_to_m=mesh_scale_to_m,
        )
        frame = native_observation_frame(
            context, _scaled_frame_mesh(msh_text, mesh_scale_to_m), SimpleNamespace,
        )
        if frame is None:
            raise OfficialBeatUnavailable("Source-tag observation frame is unavailable")
        config = SimpleNamespace(
            observation=SimpleNamespace(
                distance_m=context.polar_config["distance"],
                origin=context.polar_config["observation_origin"],
            ),
            frame_override=frame,
        )
        contract.validate_solve_request(compiled)
        request_path.write_text(json.dumps(compiled), encoding="utf-8")
        paths = beat.engine_paths(backend)
        worker = (worker_factory or beat.EngineWorker)(
            julia_executable=julia or "julia", solver_script=paths.system_solver,
            julia_threads=max(1, min(os.cpu_count() or 1, 8)), julia_project=paths.project,
            environment=os.environ.copy(), backend_label=f"BEAT {backend}",
        )
        frequencies = np.asarray(compiled["frequencies_hz"], dtype=float)
        pressure_rows: list[np.ndarray] = []
        impedance_rows: list[complex] = []
        try:
            def watch_cancel() -> None:
                if cancellation_callback is None:
                    return
                while not monitor_stop.wait(0.05):
                    try:
                        cancellation_callback()
                    except BaseException as exc:  # callback raises on cancellation
                        if monitor_stop.is_set():
                            return
                        monitor_error.append(exc)
                        try:
                            cancel_path.touch()
                        except OSError:
                            pass  # process termination must still wake a blocked read
                        try:
                            if stream is not None:
                                stream.close()
                        except Exception:
                            pass  # termination is the final cancellation backstop
                        finally:
                            worker.terminate()
                        return

            watcher = threading.Thread(target=watch_cancel, daemon=True)
            watcher.start()
            if stage_callback:
                stage_callback("setup", 0.0, f"Starting official BEAT {backend} worker")
            worker.ensure_started()
            if monitor_error:
                raise monitor_error[0]
            ready = worker.worker_info
            contract.negotiate_submission(ready, compiled, "solve")
            if cancellation_callback:
                cancellation_callback()
            if monitor_error:
                raise monitor_error[0]
            stream = worker.submit(request_path)
            terminal = None
            for event in stream:
                if monitor_error:
                    raise monitor_error[0]
                if not isinstance(event, dict):
                    raise OfficialBeatProtocolError("Worker event is not an object")
                kind = event.get("type")
                if kind == "result":
                    index = len(pressure_rows)
                    if index >= len(frequencies):
                        raise OfficialBeatProtocolError("Worker returned surplus frequencies")
                    row, impedance = parse_result(
                        event.get("result"), frequency_hz=float(frequencies[index]),
                        planes=planes, angles=angles, source_area_m2=source_area,
                        backend=backend, precision=precision,
                    )
                    pressure_rows.append(row)
                    impedance_rows.append(impedance)
                    if result_callback:
                        provisional = build_provisional_frequency_response(
                            index=index, frequency_hz=float(frequencies[index]),
                            entry={"observation_angles_deg": angles.tolist(), "observation_planes": planes,
                                   "observation_pressure_complex": row,
                                   "observation_spl_db": _native_result(
                                       frequencies[index:index + 1], angles, planes,
                                       row[None], np.asarray([impedance])
                                   ).directivity_db[0], "impedance": impedance},
                            config=config,
                            context=context, backend="beat", sound_speed_m_per_s=SOUND_SPEED_M_PER_S,
                        )
                        result_callback(index, provisional)
                    if stage_callback:
                        stage_callback("frequency_solve", len(pressure_rows) / len(frequencies),
                                       f"Solved frequency {len(pressure_rows)}/{len(frequencies)}")
                elif kind == "failed":
                    raise OfficialBeatProtocolError(f"Official BEAT solve failed: {event.get('error', event)}")
                elif kind == "cancelled":
                    raise OfficialBeatUnavailable("Official BEAT solve was cancelled")
                elif kind == "completed":
                    terminal = event
                    break
                elif kind not in {"status", "progress"}:
                    raise OfficialBeatProtocolError(f"Unknown worker event {kind!r}")
            if monitor_error:
                raise monitor_error[0]
            if (terminal is None or type(terminal.get("solved_count")) is not int
                    or terminal["solved_count"] != len(frequencies)
                    or len(pressure_rows) != len(frequencies)):
                raise OfficialBeatProtocolError("Worker completion count does not match requested frequencies")
        finally:
            monitor_stop.set()
            try:
                if stream is not None:
                    stream.close()
            finally:
                try:
                    if worker is not None:
                        worker.terminate()
                finally:
                    if watcher is not None:
                        watcher.join(timeout=5.0)
                        if watcher.is_alive():
                            raise RuntimeError("Official BEAT cancellation monitor did not stop")
        pressure = np.stack(pressure_rows)
        impedance = np.asarray(impedance_rows, dtype=np.complex128)
        order = np.argsort(frequencies, kind="stable")
        native = _native_result(frequencies[order], angles, planes, pressure[order], impedance[order])
        response = build_solver_response(
            result=native, config=config, context=context, start_time=started,
            metadata={"solver_backend": "beat", "solver_mode": "full_3d", "engine": "official-beat-engine",
                      "phase_time_convention": "exp(+ikr)", "phasor_convention": PHASOR,
                      "beat_backend": backend,
                      "performance": {"total_time_seconds": time.time() - started}},
            sound_speed_m_per_s=SOUND_SPEED_M_PER_S,
        )
        response["_field_trace_unavailable_reason"] = (
            "Official BEAT compiled bridge does not yet translate boundary field traces"
        )
        response["metadata"]["directivity_index_unavailable_reason"] = (
            "Official BEAT compiled bridge has not yet translated spherical pressure sampling"
        )
        return response


class OfficialBeatEngine:
    """Standalone port; deliberately absent from the production registry."""

    solver_port_marker = FULL3D_SOLVER_PORT_MARKER

    def __init__(self, backend: str = "cpu", precision: str = "float32",
                 julia_executable: str | None = None) -> None:
        if backend not in {"cpu", "metal"}:
            raise ValueError("Official BEAT bridge is qualified only for CPU and Metal")
        if precision not in {"float32", "float64"} or (backend == "metal" and precision != "float32"):
            raise ValueError("Official BEAT bridge precision is unsupported for the selected backend")
        self.backend = backend
        self.precision = precision
        self.julia_executable = julia_executable
        self.name = f"official-beat-{backend}"

    async def run(
        self, request: SolveRequest, *, cancel_cb: CancelCallback, stage_cb: StageCallback,
        artifact_cb: ArtifactCallback | None = None, result_cb: ResultCallback | None = None,
        imported_record: Mapping[str, Any] | None = None,
    ) -> EngineRunResult:
        context = preflight(request, imported_record)
        mesh = await build_solver_mesh(
            request.design, request.options, cancel_cb,
            lambda stage, progress, message: stage_cb(stage, progress, message),
        )
        if artifact_cb:
            await artifact_cb(mesh["msh_text"], mesh["stats"])
        cancel_cb()
        results = await asyncio.to_thread(
            solve_official_beat_from_msh_text, mesh["msh_text"], context,
            backend=self.backend, precision=self.precision, stage_callback=stage_cb,
            cancellation_callback=cancel_cb, result_callback=result_cb,
            julia_executable=self.julia_executable,
        )
        results.setdefault("metadata", {})["mesh_stats"] = mesh["stats"]
        results["metadata"]["solve_path"] = "full-3d"
        reason = results.pop("_field_trace_unavailable_reason", None)
        return EngineRunResult(
            results=results, msh_text=mesh["msh_text"], mesh_stats=mesh["stats"],
            field_trace_unavailable_reason=reason,
        )


__all__ = [
    "OfficialBeatEngine", "OfficialBeatProtocolError", "OfficialBeatUnavailable",
    "build_compiled_request", "decode_complex_values", "parse_result", "preflight",
    "resolve_julia_executable", "solve_official_beat_from_msh_text",
]
