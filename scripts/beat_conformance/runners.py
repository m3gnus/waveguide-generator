"""Broker-job runners with one frozen exterior input for both engines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
from importlib import import_module, metadata
from importlib.util import find_spec
import json
import os
from pathlib import Path
import tempfile
import sys
from typing import Any

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.solver.beat_adapter import request, results
from server.solver.beat_adapter.capabilities import FRAME
from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.mesh import read_surface
from server.solver.beat_runtime.discovery import JULIA_ENV_VAR
from server.solver.beat_runtime.manager import WorkerManager
from server.solver.beat_runtime.paths import checked_root
from server.solver.beat_runtime.session import SolveSession
from server.solver.beat_runtime.threads import resolve_julia_threads
from server.solver.directivity_index import calculate_di_from_spherical_grid

from .agreement import ResultSet
from .recorder import EngineRun, write_record
from .settings import observed_settings


def output_directory(directory: Path, *, engine_source: Path | None = None) -> Path:
    """Refuse runtime, source-checkout and installed-distribution trees before writes."""
    directory = checked_root(directory).expanduser().resolve()
    roots = [engine_source] if engine_source else []
    for module, distribution in (("hornlab_beat_bem", "hornlab-beat-bem"), ("beat_engine", "beat-engine")):
        loaded = sys.modules.get(module)
        try:
            origin = getattr(loaded, "__file__", None) if loaded else getattr(find_spec(module), "origin", None)
            if origin:
                roots.append(Path(origin).resolve().parent)
            dist = metadata.distribution(distribution)
            roots.append(Path(dist.locate_file("")))
        except (ImportError, ValueError, metadata.PackageNotFoundError):
            pass
    # Recognize source checkouts even when their packages are not imported/installed.
    for parent in (directory, *directory.parents):
        if any((parent / relative).is_file() for relative in (
                "hornlab_beat_bem/__init__.py", "src/beat_engine/__init__.py")):
            roots.append(parent)
    for root in roots:
        root = Path(root).expanduser().resolve()
        if directory == root or root in directory.parents or directory in root.parents:
            raise ValueError(f"Output directory overlaps engine source/package directory: {root}")
    return directory


@dataclass(frozen=True)
class FrozenExterior:
    mesh_bytes: bytes
    frequencies_hz: tuple[float, ...]
    precision: str = "float64"
    symmetry: str = "full"
    source_tag: int = 2
    threads: int = 1
    distance_m: float = 2.
    angle_range: tuple[float, float, int] = (0., 180., 19)
    sphere_grid: tuple[int, int] = (19, 24)
    quadrature_order: int = 4
    singular_order: int = 4
    density: float = 1.2041
    sound_speed: float = 343.
    backend: str = "cpu"

    def compiled(self) -> request.CompiledRequest:
        """Freeze metre geometry and the HBB-representable observation frame."""
        if type(self.threads) is not int or self.threads < 1:
            raise ValueError("Qualification requires an explicit positive thread count")
        if self.backend not in {"cpu", "metal"}:
            raise ValueError("Agreement backend must be cpu or metal")
        if self.backend == "metal" and self.precision != "float32":
            raise ValueError("Metal agreement requires float32 precision")
        validate_frequency_axis(self.frequencies_hz)
        if not self.angle_range[0] <= 0 <= self.angle_range[1] or self.sphere_grid is None:
            raise ValueError("HBB comparison requires on-axis cuts and a complete sphere")
        if self.symmetry not in {"full", "yz", "yz+xz"}:
            raise ValueError("HBB qualification supports full/yz/yz+xz symmetry only")
        layout = build_observations(distance_m=self.distance_m, angle_range=self.angle_range,
                                    sphere_grid=self.sphere_grid, precision=self.precision)
        return request.build_request(
            self.mesh_bytes.decode("utf-8"), sources=[request.SourceBasis("source", self.source_tag, port_id="source")],
            channel_ports={"source": ["source"]}, frame=FRAME, layout=layout,
            frequencies_hz=self.frequencies_hz, precision=self.precision, engine_id=f"beat-{self.backend}",
            symmetry=self.symmetry, quadrature_order=self.quadrature_order,
            singular_order=self.singular_order, density_kg_per_m3=self.density,
            sound_speed_m_per_s=self.sound_speed)

    def settings(self) -> dict[str, Any]:
        compiled = self.compiled()
        mesh = read_surface(self.mesh_bytes.decode())
        triangles = np.asarray(mesh.points_m[mesh.faces], dtype=float)
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                       triangles[:, 2] - triangles[:, 0]), axis=1) / 2
        return {"tags": mesh.tags, "normals": normals, "axes": FRAME,
                "mesh": {"node_count": len(mesh.points_m), "triangle_count": len(mesh.faces),
                         "tag_areas_m2": {int(tag): float(areas[mesh.tags == tag].sum()) for tag in np.unique(mesh.tags)}},
                "observation_points": {k: v.tolist() for k, v in compiled.layout.points_m.items()},
                "quadrature": {k: v for k, v in compiled.wire["solver_options"].items()
                               if "quadrature" in k or "wavelength" in k or k == "singular_order"},
                "backend": self.backend, "precision": self.precision, "threads": self.threads,
                "time_convention": SOLVER_TIME_CONVENTION, "density_kg_per_m3": self.density,
                "sound_speed_m_per_s": self.sound_speed, "symmetry": self.symmetry,
                "source_tag": self.source_tag, "source_motion": "normal"}


def validate_frequency_axis(frequencies: tuple[float, ...]) -> None:
    axis = np.asarray(frequencies, dtype=np.float64)
    if (not np.isfinite(axis).all() or np.any(axis <= 0)
            or not np.array_equal(axis, axis.astype(np.float32).astype(np.float64))):
        raise ValueError("Agreement frequencies must be exactly representable in Float32")


def official_runner(julia_executable: str, *, threads: int,
                    engine_source: Path | None = None) -> Callable[[request.CompiledRequest], EngineRun]:
    """Return launch selection; recorder observes and owns the managed child solve."""
    def solve(compiled: request.CompiledRequest) -> EngineRun:
        if compiled.channel_ports != {"source": ("source",)}:
            raise ValueError("Qualification runner requires one source channel/port")
        return EngineRun(julia_executable, threads, runtime_mode="child", engine_source=engine_source)
    return solve


def solve(compiled: request.CompiledRequest) -> EngineRun:
    """Conformance CLI callback; selection only, with explicit broker-job environment."""
    executable = os.environ.get(JULIA_ENV_VAR, "").strip()
    if not executable:
        raise ValueError(f"Conformance runner requires {JULIA_ENV_VAR}")
    threads = resolve_julia_threads(compiled.wire["solver_options"]["bem_backend"],
                                    os.environ.get("JULIA_NUM_THREADS", "1"))
    source = os.environ.get("WG_BEAT_ENGINE_SRC", "").strip()
    return official_runner(executable, threads=threads,
                           engine_source=Path(source) if source else None)(compiled)


def managed_solve(compiled: request.CompiledRequest, selection: EngineRun,
                  facts: dict[str, Any], terminal_events: list[dict]) -> results.SweepResult:
    """Use WG admission/staging and public engine negotiation, always releasing workers."""
    contract = import_module("beat_engine.beat_contract.worker")

    contract.validate_solve_request(compiled.wire)
    manager = WorkerManager(mode="child")
    options = compiled.wire["solver_options"]
    loading = compiled.channel_loading["source"]
    launch_options = {}
    if options["bem_backend"] == "metal":
        # HBB Metal sets BLAS to Julia threads. Official Metal inherits BLAS
        # then reserves one thread when Julia is multithreaded; match its sweep.
        environment = dict(os.environ)
        count = resolve_julia_threads("metal", selection.julia_threads)
        environment["OPENBLAS_NUM_THREADS"] = str(count + (count > 1))
        launch_options["environment"] = environment
    try:
        client = manager.get_worker(options["bem_backend"], julia_executable=facts["julia_executable"],
                                    julia_project=Path(facts["project"]),
                                    solver_script=Path(facts["solver_script"]),
                                    julia_threads=selection.julia_threads,
                                    compiled_request_policy=Path(request.__file__), **launch_options)
        with SolveSession() as session:
            session.submit(client, compiled.wire, negotiate=contract.negotiate_submission)
            def observed():
                for event in session.events():
                    if event.get("type") in {"completed", "failed", "cancelled"}:
                        terminal_events.append(event)
                    yield event
            return results.map_sweep(
                observed(), compiled.wire["frequencies_hz"], layout=compiled.layout,
                source_area_m2=loading.area_m2, excitation_port_id="source",
                boundary_loading=loading, symmetry=options["symmetry"],
                precision=options["precision"], backend=options["bem_backend"],
                source_motion=compiled.bases[0].motion,
                trace_counts=(len(compiled.mesh.points_m), len(compiled.mesh.faces))
                if compiled.surface_traces else None,
                request_cancel=session.request_cancel)
    finally:
        manager.shutdown()


def result_set(inputs: FrozenExterior, native: Any, *, revision: str,
               recorder_record: dict | None = None, official: bool = False) -> ResultSet:
    """Score the same full-sphere quadrature for DI and far-field power estimates."""
    compiled = inputs.compiled()
    if (native.cancelled or native.is_partial
            or not np.array_equal(native.frequencies_hz, inputs.frequencies_hz)
            or not np.array_equal(native.observation_angles_deg, compiled.layout.angles_deg)
            or list(native.observation_planes) != list(compiled.layout.planes)):
        raise ValueError("Runner returned incomplete or mismatched frequency/observation axes")
    count = len(inputs.frequencies_hz)
    n_theta, n_phi = inputs.sphere_grid
    sphere = np.asarray(native.sphere_pressure_complex, dtype=complex)
    if sphere.shape != (count, n_theta * n_phi) or not np.isfinite(sphere).all():
        raise ValueError("Agreement requires the complete frozen spherical field")
    grid = sphere.reshape(count, n_theta, n_phi)
    theta = np.linspace(0., 180., n_theta)
    phi = np.arange(n_phi) * 360. / n_phi
    levels = 20 * np.log10(np.maximum(np.abs(grid), np.finfo(float).tiny))
    di = np.asarray(calculate_di_from_spherical_grid(theta, phi, levels), dtype=float)
    edges = np.deg2rad(np.r_[0., (theta[1:] + theta[:-1]) / 2, 180.])
    weights = (np.cos(edges[:-1]) - np.cos(edges[1:])) / 2
    mean_square = np.sum(np.mean(np.abs(grid)**2, axis=2) * weights, axis=1)
    power = 4 * np.pi * inputs.distance_m**2 * mean_square / (2 * inputs.density * inputs.sound_speed)
    settings = inputs.settings()
    evidence = None
    if recorder_record is not None:
        settings, evidence = observed_settings(settings, native, official=official,
                                               native_symmetry=compiled.wire["solver_options"]["symmetry"])
    return ResultSet(inputs.mesh_bytes, np.asarray(native.frequencies_hz), np.asarray(native.pressure_complex),
                     np.asarray(native.impedance), di, power, settings, revision,
                     recorder_record, recorder_record["record_sha256"] if recorder_record else "", evidence)


def hbb_runner(inputs: FrozenExterior, *, julia_executable: str,
               expected_revision: str, directory: Path) -> ResultSet:
    """Use HBB's public API in one-shot mode; never adopt or signal a registry PID."""
    inputs.compiled()  # Refuse quantized frequencies before imports or launch.
    directory = output_directory(directory)
    import hornlab_beat_bem as hbb

    distribution = metadata.distribution("hornlab-beat-bem")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    revision = direct.get("vcs_info", {}).get("commit_id", "")
    if (not expected_revision or revision != expected_revision
            or direct.get("dir_info", {}).get("editable", False)
            or Path(hbb.__file__).resolve() != Path(distribution.locate_file("hornlab_beat_bem/__init__.py")).resolve()):
        raise ValueError("HBB must be the non-editable current WG pin")
    compiled = inputs.compiled()  # Validate before admitting any solve.
    observation = hbb.ObservationConfig(
        planes=list(compiled.layout.planes), distance_m=inputs.distance_m,
        angle_min_deg=inputs.angle_range[0], angle_max_deg=inputs.angle_range[1],
        angle_count=inputs.angle_range[2], sphere_grid=inputs.sphere_grid)
    config = hbb.SolveConfig(
        freq_min_hz=min(inputs.frequencies_hz), freq_max_hz=max(inputs.frequencies_hz),
        freq_count=len(inputs.frequencies_hz), velocity_sources={inputs.source_tag: 1.},
        observation=observation, native_symmetry_plane=None if inputs.symmetry == "full" else inputs.symmetry,
        mesh_scale=1., air_density=inputs.density, sound_speed=inputs.sound_speed,
        quadrature_order=inputs.quadrature_order, singular_order=inputs.singular_order,
        regular_quadrature_mode="wavelength" if inputs.backend == "cpu" else "fixed",
        solve_precision="double" if inputs.precision == "float64" else "single",
        beat_backend=inputs.backend, julia_executable=julia_executable, julia_threads=inputs.threads,
        persistent_worker=False)
    directory.mkdir(parents=True, exist_ok=True)
    record = {"evidence_mode": "real", "engine_distribution": "hornlab-beat-bem",
              "engine_revision": revision, "revision_source": "installed_vcs_metadata_attested",
              "engine_status": "failed", "requested_frequencies_hz": inputs.frequencies_hz,
              "declared_settings": inputs.settings(), "real_solved_count": 0,
              "case": {"backend": inputs.backend, "precision": inputs.precision},
              "runtime": {"engine_revision": revision},
              "mesh_sha256": hashlib.sha256(json.dumps(compiled.mesh.packed(), sort_keys=True).encode()).hexdigest(),
              "count_source": "HBB public API-validated rows; not terminal events or WG-observed transport"}
    try:
        with tempfile.TemporaryDirectory(prefix="hbb-reference-", dir=directory) as temporary:
            mesh_path = Path(temporary) / "surface.msh"
            mesh_path.write_bytes(inputs.mesh_bytes)
            native = hbb.solve_frequencies(mesh_path, inputs.frequencies_hz, config)
        result_set(inputs, native, revision=revision)  # Validate decoded axes before recording success.
        record.update(engine_status="passed", real_solved_count=len(native.frequencies_hz),
                      solver_log=getattr(native, "solver_log", []),
                      pressure_complex=native.pressure_complex, impedance=native.impedance,
                      sphere_pressure_complex=native.sphere_pressure_complex)
        record["result"] = {"solver_log": getattr(native, "solver_log", [])}
        # Validate observed settings before declaring an engine-pass record.
        _, record["setting_evidence"] = observed_settings(inputs.settings(), native, official=False,
                                                          native_symmetry=compiled.wire["solver_options"]["symmetry"])
        if getattr(native, "mesh_info", None) is not None:
            record["hbb_mesh_info"] = asdict(native.mesh_info)
        write_record(directory / "hbb-run.json", record)
        result = result_set(inputs, native, revision=revision, recorder_record=record)
        return result
    except Exception as exc:
        record["engine_status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        write_record(directory / "hbb-run.json", record)
