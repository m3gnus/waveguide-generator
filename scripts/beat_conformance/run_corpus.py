"""Run one frozen WG production case, with one clean process per engine/part."""

from __future__ import annotations

import argparse
from dataclasses import is_dataclass, make_dataclass
from importlib import metadata
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.jobs.models import SolveOptions, SolveRequest
from server.solver.beat_adapter.request import build_imported_request, build_parametric_request
from server.solver.beat_adapter.mesh import read_surface
from server.solver.context import SolverContext
from server.solver.directivity_index import calculate_di_from_spherical_grid

from .agreement import (
    NORMALIZED_IMPEDANCE_DB, NORMALIZED_IMPEDANCE_PHASE_DEG,
    PRODUCTION_COMPLEX_RELATIVE_L2, PRODUCTION_MASKED_SPL_DB, PRODUCTION_PHASE_DEG,
    ResultSet, _extrema, _masked_complex, compare_results,
)
from .corpus import CASES, ROOT, CorpusCase, FrozenCase, freeze_case, save_frozen
from .json_io import dumps, json_value, read_json, snapshot, write_json
from .run_agreement import hbb_pin
from .runners import output_directory, validate_frequency_axis
from .recorder import record_sha256
from .settings import observed_settings

THREAD_ENV = ("JULIA_NUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
              "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")
# Read the request schema's existing bound, rather than duplicating it.
FREQUENCY_LIMIT = next(item.le for item in SolveOptions.model_fields["num_frequencies"].metadata
                       if getattr(item, "le", None) is not None)
ISOLATED_ENV = ("JULIA_DEPOT_PATH", "JULIA_LOAD_PATH", "JULIA_PROJECT", "WG2_BEAT_RUNTIME_DIR",
                "WG2_BEAT_WORKER_DIR", "WG2_BEAT_JULIA", "HORNLAB_BEAT_JULIA", "WG2_BEAT_PROVIDER")


def relevant_environment(env: dict) -> dict:
    return {key: env.get(key) for key in sorted(set(ISOLATED_ENV) | set(THREAD_ENV)
                                              | {k for k in env if k.startswith("BLAB_")})}


def child_environment(*, official: bool, julia: str, hbb_depot: str) -> dict:
    if not hbb_depot or any(not part for part in hbb_depot.split(os.pathsep)):
        raise ValueError("--hbb-depot requires an explicit nonempty depot chain")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("BLAB_") and (official or k not in ISOLATED_ENV)}
    env.update({k: "1" for k in THREAD_ENV})
    if official:
        env.update(WG2_BEAT_PROVIDER="official", WG2_BEAT_JULIA=julia)
        env.pop("HORNLAB_BEAT_JULIA", None)
    else:
        env.update(JULIA_DEPOT_PATH=hbb_depot, HORNLAB_BEAT_JULIA=julia)
    return env


def wg_identity() -> dict:
    identity = {"wg_commit": command_evidence(["git", "rev-parse", "HEAD"]),
                "wg_worktree": command_evidence(["git", "status", "--porcelain"])}
    require_clean_wg(identity)
    return identity


def require_clean_wg(identity: dict) -> None:
    commit, status = identity.get("wg_commit", {}), identity.get("wg_worktree", {})
    if commit.get("returncode") != 0 or not commit.get("stdout") or status.get("returncode") != 0 or status.get("stdout"):
        raise ValueError("Corpus requires an observed clean WG tree and exact commit")


ARRAY_FIELDS = ("pressure_complex", "impedance", "sphere_pressure_complex",
                "surface_pressure_complex", "surface_neumann_complex",
                "radiated_power_surface_w", "radiated_power_sphere_w", "electrical_impedance_ohm")


def empty_output(path: Path) -> Path:
    path = output_directory(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Corpus requires an empty or absent output directory")
    path.mkdir(parents=True, exist_ok=True)
    return path


def command_evidence(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        return {"command": command, "returncode": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "unavailable": f"{type(exc).__name__}: {exc}"}


def distribution_identity(name: str) -> dict[str, Any]:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return {"name": name, "unavailable": "distribution is not installed"}
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    revision = direct.get("vcs_info", {}).get("commit_id")
    source = None
    if direct.get("dir_info", {}).get("editable"):
        from urllib.parse import unquote, urlparse
        source = Path(unquote(urlparse(direct["url"]).path))
        evidence = command_evidence(["git", "-C", str(source), "rev-parse", "HEAD"])
        revision = evidence.get("stdout") if evidence.get("returncode") == 0 else None
    return {"name": name, "version": dist.version, "direct_url": direct,
            "revision": revision, "revision_evidence": "VCS metadata attested",
            "source": str(source) if source else None,
            "source_worktree": command_evidence(["git", "-C", str(source), "status", "--porcelain"]) if source else None}


def capture_identity(julia: str) -> dict[str, Any]:
    """Capture in the engine child, including load and installed identities."""
    return {**wg_identity(),
            "distributions": {name: distribution_identity(name) for name in ("hornlab-beat-bem", "beat-engine")},
            "hbb_pin": hbb_pin(), "julia_path": str(Path(julia).expanduser().resolve()),
            "julia_version": command_evidence([julia, "--startup-file=no", "--version"]),
            "thread_env": {name: os.environ.get(name) for name in THREAD_ENV},
            "runtime_env": relevant_environment(os.environ),
            "load": command_evidence(["sysctl", "-n", "vm.loadavg"]),
            "battery": command_evidence(["pmset", "-g", "batt"]), "pid": os.getpid()}


def settings_for(frozen: FrozenCase, backend: str, precision: str) -> dict[str, Any]:
    request = SolveRequest.model_validate(frozen.request)
    if frozen.record is None:
        context = SolverContext.from_request(request, solver_mode="full_3d")
        compiled = build_parametric_request(frozen.mesh_bytes.decode(), context,
                    backend=backend, engine_id=f"beat-{backend}", precision=precision)
    else:
        from server.solver.imported import imported_domain_planes, imported_symmetry_from_cut_planes
        geometry = request.geometry
        symmetry = imported_symmetry_from_cut_planes(imported_domain_planes(frozen.record))
        context = SolverContext.from_imported_request(request, quadrants=symmetry.quadrants, source_motion="normal")
        compiled = build_imported_request(frozen.mesh_bytes.decode(), context, frozen.record, geometry.drive_channels,
                    backend=backend, engine_id=f"beat-{backend}", precision=precision)
    mesh = read_surface(frozen.mesh_bytes.decode())
    options = compiled.wire["solver_options"]
    normals = np.cross(mesh.points_m[mesh.faces[:, 1]] - mesh.points_m[mesh.faces[:, 0]],
                       mesh.points_m[mesh.faces[:, 2]] - mesh.points_m[mesh.faces[:, 0]])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    return {"tags": mesh.tags, "normals": normals, "axes": compiled.frame,
            "mesh": {"node_count": len(mesh.points_m), "triangle_count": len(mesh.faces),
                     "tag_areas_m2": {str(t): float(mesh.areas_m2[mesh.tags == t].sum()) for t in np.unique(mesh.tags)}},
            "connectivity": mesh.faces, "observation_points": compiled.layout.points_m,
            "observation_angles_deg": compiled.layout.angles_deg,
            "observation_planes": list(compiled.layout.planes),
            "quadrature": {k: v for k, v in options.items() if "quadrature" in k or "wavelength" in k or k == "singular_order"},
            "backend": backend, "precision": precision, "time_convention": SOLVER_TIME_CONVENTION,
            "density_kg_per_m3": 1.2041, "sound_speed_m_per_s": 343., "threads": 1,
            "symmetry_native": options["symmetry"], "trace_requested": frozen.case.traces}


def load_frozen(directory: Path, case: CorpusCase) -> FrozenCase:
    values = read_json(directory / "frozen.json")
    frozen = FrozenCase(case, values["request"], (directory / "surface.msh").read_bytes(),
                        values["record"], values["mesh_stats"])
    if values["case"] != case.name or frozen.sha256 != values["mesh_sha256"]:
        raise ValueError("Coarse case or frozen mesh hash differs")
    return frozen


def production_run(frozen: FrozenCase, frequencies: tuple[float, ...], *, official: bool,
                   backend: str, precision: str, julia: str,
                   routes: tuple[Callable, Callable] | None = None) -> dict[str, Any]:
    """Call the actual production function; capture unrounded fields before discard."""
    from server.solver.beat import solve_beat_from_msh_text
    from server.solver.beat_imported import solve_imported_beat_from_msh_text
    from server.solver.beat_runtime.manager import WorkerManager

    class SingleThreadManager(WorkerManager):
        def get_worker(self, backend: str = "cpu", **options: Any):
            return super().get_worker(backend, **dict(options, julia_threads=1))

    manager = SingleThreadManager(mode="child") if official and routes is None else None
    request_data = dict(frozen.request, options=dict(frozen.request["options"], frequencies_hz=list(frequencies)))
    request = SolveRequest.model_validate(request_data)
    natives = {}

    def capture(channel: str, native: Any) -> None:
        # Imported capture is AFTER apply_channel_driver; copy before display packaging.
        if channel in natives:
            raise ValueError(f"Native callback fired more than once for {channel}")
        natives[channel] = snapshot(native if is_dataclass(native) else vars(native))

    kwargs = {"backend": backend, "_official": official, "_precision": precision,
              "_julia_executable": julia, "_worker_manager": manager, "_native_result_callback": capture,
              "_hbb_options": {"solve_precision": "double" if precision == "float64" else "single",
                               "julia_threads": 1, "julia_executable": julia, "persistent_worker": False}}
    parametric, imported = routes or (solve_beat_from_msh_text, solve_imported_beat_from_msh_text)
    try:
        if frozen.record is None:
            response = parametric(frozen.mesh_bytes.decode(), SolverContext.from_request(request, solver_mode="full_3d"),
                                  mesh_stats=frozen.mesh_stats, **kwargs)
        else:
            response = imported(frozen.mesh_bytes.decode(), request, frozen.record, **kwargs)
            for channel, values in natives.items():
                channel_response = response.get("channels", {}).get(channel, {})
                electrical = channel_response.get("impedance")
                if channel_response.get("metadata", {}).get("impedance_quantity") == "electrical_input_impedance" and electrical:
                    if not np.array_equal(electrical["frequencies"], frequencies):
                        raise ValueError("Electrical impedance frequency axis differs from request")
                    values["electrical_impedance_ohm"] = np.asarray(electrical["real"]) + 1j * np.asarray(electrical["imaginary"])
        return {"response": response, "native": natives, "frequencies_hz": list(frequencies),
                "mesh_sha256": frozen.sha256, "official": official}
    finally:
        if manager is not None:
            manager.shutdown()


def engine_child(input_path: Path, output_path: Path, *, official: bool, backend: str,
                 precision: str, julia: str) -> int:
    def interrupted(signum, frame):
        raise TimeoutError(f"engine child interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    values = read_json(input_path)
    frozen = load_frozen(input_path.parent, CASES[values["case"]])
    identity = {}
    name = "beat-engine" if official else "hornlab-beat-bem"
    try:
        identity = capture_identity(julia)
        require_clean_wg(identity)
        dist = identity["distributions"][name]
        if not dist.get("revision"):
            raise ValueError(f"{name} has no exact revision in direct_url/source metadata")
        if not official and (dist["revision"] != identity["hbb_pin"] or dist["direct_url"].get("dir_info", {}).get("editable")):
            raise ValueError("HBB must be the non-editable current WG pin")
        result = production_run(frozen, tuple(values["frequencies_hz"]), official=official,
                                backend=backend, precision=precision, julia=julia)
        result["identity"] = identity
        # Serialization belongs to the child error boundary too.
        result = snapshot(result)
    except Exception as exc:
        if not identity:
            identity = {"wg_commit": command_evidence(["git", "rev-parse", "HEAD"]),
                        "wg_worktree": command_evidence(["git", "status", "--porcelain"]),
                        "runtime_env": relevant_environment(os.environ)}
        from server.solver.beat import BeatUnavailable
        result = {"identity": identity, "error": f"{type(exc).__name__}: {exc}",
                  "refusal": str(exc) if isinstance(exc, BeatUnavailable) else None}
    write_json(output_path, result)
    return 0 if "error" not in result else 1


def require_official_ready(backend: str, julia: str) -> None:
    """Use the production readiness identity, without launching an engine."""
    from server.solver.beat_runtime import paths, readiness

    env = dict(os.environ, WG2_BEAT_JULIA=julia)
    root = paths.runtime_dir(environ=env)
    verdict = readiness.backend_readiness(backend, root, environ=env)
    if not verdict.ready:
        raise ValueError(
            f"Official BEAT {backend} readiness is {verdict.state}: {verdict.reason} "
            f"Effective runtime directory: {root}. Provision this directory with the same "
            "Julia/depot/environment as the corpus; WG2_BEAT_RUNTIME_DIR is a base "
            "(wg-beat-engine is appended), while CLI --dir is exact. "
            "See scripts/beat_conformance/README.md. No corpus solve started."
        )


def isolated_pair(frozen: FrozenCase, frequencies: tuple[float, ...], directory: Path, *,
                  backend: str, precision: str, julia: str, hbb_depot: str,
                  timeout_seconds: float | None = None) -> tuple[dict, dict]:
    """Sequential subprocesses; official child manager cannot adopt a warm host."""
    wg_identity()
    require_official_ready(backend, julia)
    environments = [child_environment(official=value, julia=julia, hbb_depot=hbb_depot) for value in (False, True)]
    directory.mkdir(parents=True)
    save_frozen(frozen, directory)
    write_json(directory / "job.json", {"case": frozen.case.name, "frequencies_hz": frequencies})
    runs = []
    for official, name in ((False, "hbb"), (True, "official")):
        env = environments[int(official)]
        target = directory / f"{name}.json"
        command = [sys.executable, "-m", "scripts.beat_conformance.run_corpus", "--engine-child", str(directory / "job.json"),
                   "--engine-output", str(target), "--official-child", str(int(official)),
                   "--backend", backend, "--precision", precision, "--julia", julia, "--hbb-depot", hbb_depot]
        started = time.monotonic()
        with (directory / f"{name}.log").open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.wait(timeout=timeout_seconds if timeout_seconds is not None else frozen.case.coarse_minutes[1] * 60)
            except BaseException:
                # Only the recorded process group belongs to this invocation.
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
        if not target.exists():
            raise ValueError(f"{name} child produced no raw result; inspect {name}.log")
        elapsed = time.monotonic() - started
        run = read_json(target)
        run["environment"] = relevant_environment(env)
        run["timing"] = engine_timing(run, wall_seconds=elapsed, frequency_count=len(frequencies))
        write_json(target, run)
        runs.append(run)
    return tuple(runs)


def map_results(run: dict, frozen: FrozenCase, settings: dict, *, official: bool) -> tuple[dict[str, ResultSet], dict]:
    """Map raw production captures; never infer complex pressure from rounded SPL."""
    mapped, unavailable = {}, {}
    if run.get("mesh_sha256") != frozen.sha256:
        raise ValueError("Production result is not bound to this frozen mesh")
    revision = run.get("identity", {}).get("distributions", {}).get("beat-engine" if official else "hornlab-beat-bem", {}).get("revision", "")
    for channel, values in run.get("native", {}).items():
        frequencies = np.asarray(values["frequencies_hz"], dtype=float)
        if not np.array_equal(frequencies, run["frequencies_hz"]):
            raise ValueError("Production result frequency axis differs from the requested axis")
        native = SimpleNamespace(**values)
        missing = {}
        for field in ("pressure_complex", "impedance", "sphere_pressure_complex"):
            value = values.get(field)
            if value is None or not np.asarray(value).size or not np.isfinite(np.asarray(value)).all():
                missing[field] = "native production result absent, empty or nonfinite"
        if missing:
            if "sphere_pressure_complex" in missing:
                missing.update(di_db="full-sphere pressure unavailable", power_w="full-sphere pressure unavailable")
            unavailable[channel] = missing
            continue
        if np.asarray(values["pressure_complex"]).shape != (
                len(frequencies), len(settings["observation_planes"]), len(settings["observation_angles_deg"])):
            raise ValueError("Production pressure does not cover the frozen observation grid")
        # Use the same full-sphere estimator as run_agreement; never substitute boundary flux.
        theta = np.asarray(values["sphere_theta_deg"], dtype=float)
        phi = np.asarray(values["sphere_phi_deg"], dtype=float)
        theta_axis, phi_axis = np.unique(theta), np.unique(phi)
        polar = frozen.request["options"]["polar_config"]
        if (not np.array_equal(theta_axis, np.linspace(0., 180., polar["spherical_theta_count"]))
                or not np.array_equal(phi_axis, np.arange(polar["spherical_phi_count"]) * 360. / polar["spherical_phi_count"])):
            raise ValueError("Production sphere axes differ from the frozen grid")
        if not np.array_equal(theta, np.repeat(theta_axis, len(phi_axis))) or not np.array_equal(phi, np.tile(phi_axis, len(theta_axis))):
            raise ValueError("Sphere must retain theta-major production ordering")
        if theta_axis[0] != 0 or theta_axis[-1] != 180:
            raise ValueError("Comparable DI/power requires a complete sphere")
        sphere = np.asarray(values["sphere_pressure_complex"]).reshape(len(frequencies), len(theta_axis), len(phi_axis))
        levels = 20 * np.log10(np.maximum(np.abs(sphere), np.finfo(float).tiny))
        di = np.asarray(calculate_di_from_spherical_grid(theta_axis, phi_axis, levels), dtype=float)
        edges = np.deg2rad(np.r_[0., (theta_axis[1:] + theta_axis[:-1]) / 2, 180.])
        weights = (np.cos(edges[:-1]) - np.cos(edges[1:])) / 2
        power = 4 * np.pi * 2.**2 * np.sum(np.mean(np.abs(sphere)**2, axis=2) * weights, axis=1) / (2 * 1.2041 * 343.)
        facts = values.get("mesh_info")
        parsed = dict(facts) if isinstance(facts, dict) else None
        if parsed is not None and frozen.record is not None:
            # Production HBB merges the active CAD tags into 2 and every other
            # tag into 1. Its returned areas cannot observe original CAD tags.
            parsed.pop("physical_tag_areas_m2", None)
        native.mesh_info = (make_dataclass("CapturedMeshInfo", [(k, Any) for k in parsed])(**parsed)
                            if parsed is not None else None)
        observed, evidence = observed_settings(settings, native, official=official, native_symmetry=settings["symmetry_native"])
        if facts is not None and frozen.record is not None:
            evidence["hbb_mesh_info_sha256"] = {"status": "observed", "value": record_sha256(facts),
                "source": "HBB parsed MeshInfo after CAD tag merging; original tag areas remain declared"}
        mapped[channel] = ResultSet(frozen.mesh_bytes, frequencies, np.asarray(values["pressure_complex"]),
                     np.asarray(values["impedance"]), di, power, observed, revision, setting_evidence=evidence,
                     electrical_impedance_ohm=values.get("electrical_impedance_ohm"))
        for field in ("surface_pressure_complex", "surface_neumann_complex", "radiated_power_surface_w", "radiated_power_sphere_w"):
            if values.get(field) is None:
                unavailable.setdefault(channel, {})[field] = (
                    run.get("response", {}).get("_field_trace_unavailable_reason") or "not returned by this production route")
        if frozen.case.name == "driver-loading" and values.get("electrical_impedance_ohm") is None:
            unavailable.setdefault(channel, {})["electrical_impedance_ohm"] = "driver electrical impedance not returned"
    if not run.get("native"):
        unavailable["all"] = run.get("error", "production function returned no native fields")
    return mapped, unavailable


def engine_timing(run: dict, *, wall_seconds: float | None = None,
                  frequency_count: int | None = None) -> dict:
    """Measured acquisition context, never a numerical agreement gate.

    Legacy captures have route wall time but no parent process wall measurement.
    Preserve that distinction; do not substitute native kernel time for wall time.
    """
    if wall_seconds is None and "timing" in run:
        return run["timing"]
    source = "parent_process_wall" if wall_seconds is not None else "legacy_production_route_wall"
    if wall_seconds is None:
        wall_seconds = run.get("response", {}).get("metadata", {}).get("performance", {}).get("total_time_seconds")
    count = frequency_count if frequency_count is not None else len(run.get("frequencies_hz", ()))
    if not count or wall_seconds is None or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError("Measured wall time per engine is required for refinement planning")
    rows = []
    for channel, values in run.get("native", {}).items():
        for row in values.get("solver_log", ()):
            if "frequency_hz" in row:
                rows.append({"channel": channel, "frequency_hz": row["frequency_hz"],
                             "native_timings": row.get("timings", {})})
    return {"source": source, "wall_seconds": wall_seconds, "frequency_count": count,
            "wall_seconds_per_frequency": wall_seconds / count, "per_frequency": rows}


def timing_context(run: dict) -> list[dict]:
    if "acquisition_timing" in run:
        return run["acquisition_timing"]
    try:
        return [engine_timing(run)]
    except ValueError as exc:
        return [{"source": "unavailable", "reason": str(exc)}]


def local_bounds(frequencies: np.ndarray, index: int) -> tuple[float, float]:
    return (float(frequencies[max(0, index - 1)]),
            float(frequencies[min(len(frequencies) - 1, index + 1)]))


def quantity_columns(result: ResultSet) -> dict[str, np.ndarray]:
    f = result.frequencies_hz
    data = {"pressure_complex": np.asarray(result.pressure_complex).reshape(len(f), -1),
            "impedance_per_acceleration": np.asarray(result.impedance_per_acceleration).reshape(len(f), -1)
                * (-1j * 2 * np.pi * f[:, None]) / (1.2041 * 343.)}
    if result.electrical_impedance_ohm is not None:
        data["electrical_impedance_ohm"] = np.asarray(result.electrical_impedance_ohm).reshape(len(f), -1)
    return data


def detect_features(results: dict[str, ResultSet], case: CorpusCase, *, engine: str = "hbb") -> list[dict]:
    features = []
    for channel, result in results.items():
        f = result.frequencies_hz
        validate_frequency_axis(tuple(f))
        if len(f) < 3 or np.any(np.diff(f) <= 0):
            raise ValueError("Dense axis must be increasing and bracket interior features")
        for quantity, data in quantity_columns(result).items():
            for column in range(data.shape[1]):
                levels = 20 * np.log10(np.maximum(np.abs(data[:, column]), np.finfo(float).tiny))
                for kind, indices in _extrema(levels, case.prominence_db).items():
                    for index in indices:
                        start, end = local_bounds(f, index)
                        features.append({"engine": engine, "channel": channel, "quantity": quantity,
                                         "column": column, "kind": kind, "frequency_hz": float(f[index]),
                                         "start_hz": start, "end_hz": end})
    return features


def refine_windows(reference: dict[str, ResultSet], case: CorpusCase, *,
                   timing: dict[str, dict], max_part_minutes: float = 8.,
                   candidate: dict[str, ResultSet] | None = None) -> dict[str, Any]:
    """Dense union neighbour windows; acquisition parts capped by schema and time."""
    if not math.isfinite(max_part_minutes) or max_part_minutes <= 0:
        raise ValueError("--max-part-minutes must be positive and finite")
    # Wall/count already amortizes startup. Additionally reserve an entire coarse
    # pair's wall time per part for fresh startup/serialization, conservatively.
    costs = [timing[name]["wall_seconds_per_frequency"] for name in ("hbb", "official")]
    startups = [timing[name]["wall_seconds"] for name in ("hbb", "official")]
    if any(not math.isfinite(v) or v <= 0 for v in (*costs, *startups)):
        raise ValueError("Measured wall costs must be positive and finite")
    cost, startup = sum(costs), sum(startups)
    limit = min(FREQUENCY_LIMIT, math.floor((max_part_minutes * 60 - startup) / cost))
    windows = [{"start_hz": feature["start_hz"], "end_hz": feature["end_hz"], "features": [feature]}
               for engine, results in (("hbb", reference), ("official", candidate or {}))
               for feature in detect_features(results, case, engine=engine)]
    merged = []
    for window in sorted(windows, key=lambda w: (w["start_hz"], w["end_hz"])):
        if merged and window["start_hz"] < merged[-1]["end_hz"]:
            merged[-1]["end_hz"] = max(merged[-1]["end_hz"], window["end_hz"])
            merged[-1]["features"].extend(window["features"])
        else:
            merged.append(window)
    for window in merged:
        lowest = min(feat["frequency_hz"] for feat in window["features"])
        step = 2. ** math.floor(math.log2(lowest * 0.0025))
        start, end = window["start_hz"], window["end_hz"]
        axis = np.arange(math.ceil(start / step), math.floor(end / step) + 1) * step
        # Include the exact coarse neighbours even for off-grid bounds, never
        # round outward. End intervals are then no larger than the declared step.
        bounds = [feat[key] for feat in window["features"] for key in ("start_hz", "end_hz")]
        axis = np.unique(np.r_[axis, bounds])
        validate_frequency_axis(tuple(axis))
        window.update(frequencies_hz=axis.tolist(), step_hz=step)
    if merged and limit < 3:
        raise ValueError("Part wall budget cannot fit three frequencies plus startup and two-row overlap")
    # Split acquisition only; score complete windows after gathering parts.
    axis = sorted({f for w in merged for f in w["frequencies_hz"]})
    parts = []
    offset = 0
    while offset < len(axis):
        frequencies = axis[offset:offset + limit]
        parts.append({"part": len(parts) + 1, "frequencies_hz": frequencies,
                      "estimated_minutes": (len(frequencies) * cost + startup) / 60})
        if offset + limit >= len(axis):
            break
        offset += limit - 2
    return {"prominence_db": case.prominence_db, "windows": merged, "parts": parts,
            "count": len(axis), "acquired_frequency_count": sum(len(p["frequencies_hz"]) for p in parts),
            "part_count": len(parts), "per_part_limit": limit, "max_part_minutes": max_part_minutes,
            "estimated_minutes": sum(p["estimated_minutes"] for p in parts),
            "timing": timing, "pair_seconds_per_frequency": cost, "startup_seconds_per_part": startup,
            "startup_policy": "reserve one full coarse pair wall time in addition to amortized wall/frequency"}


def resolve_features(reference: ResultSet, features: list[dict], prominence_db: float) -> dict:
    """One-to-one reference matches: no coarse feature can silently disappear."""
    f = reference.frequencies_hz
    entries = []
    groups = {}
    for feature in features:
        groups.setdefault((feature["quantity"], feature["column"], feature["kind"]), []).append(feature)
    for (quantity, column, kind), expected in groups.items():
        data = quantity_columns(reference)[quantity][:, column]
        levels = 20 * np.log10(np.maximum(np.abs(data), np.finfo(float).tiny))
        indices, properties = find_peaks(levels if kind == "peaks" else -levels,
                                         prominence=(None, None))
        prominences = properties["prominences"]
        # Dummy columns make unresolved features explicit. Valid matches always
        # beat dummies; maximize their number before minimizing coarse shifts.
        penalty = (len(expected) + 1) * (float(f[-1] - f[0]) + 1)
        costs = np.full((len(expected), len(indices) + len(expected)), penalty)
        for row, feature in enumerate(expected):
            valid = ((f[indices] > feature["start_hz"]) & (f[indices] < feature["end_hz"])
                     & (prominences >= prominence_db))
            costs[row, :len(indices)] = np.where(valid, np.abs(f[indices] - feature["frequency_hz"]), 2 * penalty)
        rows, columns = linear_sum_assignment(costs)
        for row, col in zip(rows, columns):
            feature = expected[row]
            resolved = col < len(indices) and costs[row, col] < penalty
            local = (f[indices] > feature["start_hz"]) & (f[indices] < feature["end_hz"])
            entries.append(dict(feature, status="resolved" if resolved else "not_resolved",
                                refined_frequency_hz=float(f[indices[col]]) if resolved else None,
                                reference_prominence_db=float(prominences[col]) if resolved else
                                float(np.max(prominences[local], initial=0.)),
                                reason=None if resolved else "reference feature not resolved at the declared local prominence"))
    return {"passed": all(e["status"] == "resolved" for e in entries), "features": entries}


def solver_rows(value: dict) -> dict[float, dict]:
    rows = {}
    for row in value.get("solver_log", ()):
        if "frequency_hz" not in row:
            raise ValueError("solver_log row has no frequency_hz")
        f = float(row["frequency_hz"])
        if f in rows and solver_settings(rows[f]) != solver_settings(row):
            raise ValueError(f"Inconsistent duplicate solver_log row at {f} Hz")
        rows[f] = row
    if set(rows) != set(map(float, value["frequencies_hz"])):
        raise ValueError("solver_log frequencies do not cover the native axis")
    return rows


def solver_settings(row: dict) -> str:
    # Compare execution settings, not pressure samples or variable timing and
    # convergence outcomes. Nested cost-model times are measurements as well.
    def stable(value):
        if not isinstance(value, dict):
            return value
        return {k: stable(v) for k, v in value.items()
                if k != "timings" and not k.endswith(("_s", "_seconds")) and k not in {
                    "message", "convergence_info", "dense_solve_iterations", "dense_solve_relative_residuals",
                    "dense_solve_termination_reasons", "dense_solve_warm_start_used", "dense_solve_recent_fallback_hz"}}
    return dumps(stable({k: v for k, v in row.items() if k in {
        "frequency_hz", "native_diagnostics", "backend", "precision", "settings", "config",
        "quadrature", "observation_angles_deg", "observation_planes"}}))


def slice_run(run: dict, axis: list[float]) -> dict:
    from copy import deepcopy
    result = deepcopy(run)
    for value in result["native"].values():
        indices = [list(value["frequencies_hz"]).index(f) for f in axis]
        logs = solver_rows(value)
        value["frequencies_hz"] = np.asarray(axis)
        for field in ARRAY_FIELDS:
            if value.get(field) is not None:
                value[field] = np.asarray(value[field])[indices]
        value["solver_log"] = [logs[f] for f in axis]
    result["frequencies_hz"] = axis
    return result


def merge_runs(runs: list[dict]) -> dict:
    """Join acquired arrays and frequency-keyed logs, checking overlapping settings."""
    from copy import deepcopy
    for run in runs:
        require_clean_wg(run.get("identity", {}))
    for run in runs[1:]:
        if run.get("mesh_sha256") != runs[0].get("mesh_sha256"):
            raise ValueError("Refine run mesh differs from dense evidence")
        for key in ("distributions", "wg_commit", "wg_worktree", "julia_path", "julia_version", "thread_env", "hbb_pin", "runtime_env"):
            if run.get("identity", {}).get(key) != runs[0].get("identity", {}).get(key):
                raise ValueError(f"Refine run identity {key} differs from dense evidence")
        if run.get("environment") != runs[0].get("environment"):
            raise ValueError("Refine run environment differs from dense evidence")
        if set(run["native"]) != set(runs[0]["native"]):
            raise ValueError("Refine channels differ from dense evidence")
    merged = deepcopy(runs[0])
    for channel in merged["native"]:
        members = [r["native"][channel] for r in runs]
        logs = {}
        for member in members:
            for f, row in solver_rows(member).items():
                if f in logs and solver_settings(logs[f]) != solver_settings(row):
                    raise ValueError(f"Inconsistent overlapping solver_log settings at {f} Hz")
                logs[f] = row
        rows = {float(f): (m, i) for m in members for i, f in enumerate(m["frequencies_hz"])}
        axis = sorted(rows)
        target = merged["native"][channel]
        target["frequencies_hz"] = np.asarray(axis)
        for field in ARRAY_FIELDS:
            if all(m.get(field) is not None for m in members):
                target[field] = np.asarray([np.asarray(rows[f][0][field])[rows[f][1]] for f in axis])
            else:
                target[field] = None
        target["solver_log"] = [logs[f] for f in axis]
    merged["frequencies_hz"] = axis
    merged["acquisition_timing"] = [entry for r in runs for entry in timing_context(r)]
    merged.pop("timing", None)
    return merged


def narrowness_sentinel(results: dict[str, ResultSet], prominence_db: float,
                        refined_frequencies: set[float]) -> dict:
    features = []
    for channel, result in results.items():
        f = result.frequencies_hz
        for quantity, data in quantity_columns(result).items():
            for column in range(data.shape[1]):
                levels = 20 * np.log10(np.maximum(np.abs(data[:, column]), np.finfo(float).tiny))
                for index in _extrema(levels, prominence_db)["peaks"]:
                    # A dense-only sample cannot establish the refined Q claim.
                    if f[index] not in refined_frequencies:
                        continue
                    threshold = levels[index] - 3.
                    left, right = index, index
                    while left > 0 and levels[left] > threshold:
                        left -= 1
                    while right < len(f) - 1 and levels[right] > threshold:
                        right += 1
                    if levels[left] > threshold or levels[right] > threshold:
                        continue  # endpoints do not establish a width
                    if any(point not in refined_frequencies for point in f[left:right + 1]):
                        continue  # the width itself, not just its peak, requires refinement
                    lo = np.interp(threshold, levels[left:left + 2], f[left:left + 2])
                    hi = np.interp(threshold, levels[right - 1:right + 1][::-1], f[right - 1:right + 1][::-1])
                    width = float(hi - lo)
                    if width > 0:
                        features.append({"channel": channel, "quantity": quantity, "column": column,
                                         "frequency_hz": float(f[index]), "width_3db_hz": width,
                                         "crossings_hz": [float(lo), float(hi)], "q": float(f[index] / width)})
    passed = any(feature["q"] >= 10 for feature in features)
    return {"passed": passed, "status": "observed" if passed else "resonance_not_narrow",
            "minimum_q": 10, "features": features,
            "method": "refined peak and refined shoulders, linear absolute -3 dB crossings"}


def cut_sentinel(results: dict[str, ResultSet], planes: list[str]) -> dict:
    differences = {"horizontal": 0., "vertical": 0.}
    if not {"horizontal", "vertical", "diagonal"} <= set(planes):
        return {"passed": False, "status": "cut_not_discriminating", "reason": "missing control cuts"}
    for result in results.values():
        p = np.abs(result.pressure_complex)
        mask = (p > 0) & (p >= p.max(axis=(1, 2), keepdims=True) * 10**(-30 / 20))
        diagonal = planes.index("diagonal")
        for name in differences:
            control = planes.index(name)
            use = mask[:, diagonal] & mask[:, control]
            if np.any(use):
                delta = np.abs(20 * np.log10(p[:, diagonal][use] / p[:, control][use]))
                differences[name] = max(differences[name], float(delta.max()))
    passed = all(value > .5 for value in differences.values())
    return {"passed": passed, "status": "observed" if passed else "cut_not_discriminating",
            "max_difference_db": differences, "threshold_db": .5, "reference_mask_db": 30}


def score_pair(reference_run: dict, candidate_run: dict, frozen: FrozenCase, settings: dict, *,
               frequency_step: float, expected: bool = False,
               expected_by_channel: dict[str, dict[str, tuple[int, ...]]] | None = None,
               expected_features: list[dict] | None = None,
               feature_context: tuple[dict, dict] | None = None,
               refined_frequencies: set[float] | None = None) -> dict:
    for run in (reference_run, candidate_run):
        require_clean_wg(run.get("identity", {}))
    if reference_run["identity"]["wg_commit"] != candidate_run["identity"]["wg_commit"]:
        raise ValueError("Reference/candidate WG commits differ")
    reference, unavailable_ref = map_results(reference_run, frozen, settings, official=False)
    candidate, unavailable_got = map_results(candidate_run, frozen, settings, official=True)
    contexts = (reference, candidate) if feature_context is None else (
        map_results(feature_context[0], frozen, settings, official=False)[0],
        map_results(feature_context[1], frozen, settings, official=True)[0])
    reports = {}
    for channel in reference.keys() & candidate.keys():
        resolutions = [resolve_features(contexts[i][channel],
            [feat for feat in (expected_features or []) if feat["channel"] == channel
             and feat.get("engine", "hbb") == engine], frozen.case.prominence_db)
            for i, engine in enumerate(("hbb", "official"))]
        resolution = {"passed": all(r["passed"] for r in resolutions),
                      "features": [feature for r in resolutions for feature in r["features"]]}
        if not resolution["passed"]:
            reports[channel] = {"passed": False, "metrics": {}, "extra_fields": {},
                                "feature_resolution": resolution, "failures": [
                f"not_resolved: {feat['quantity']} column {feat['column']} {feat['kind']} at coarse {feat['frequency_hz']} Hz"
                for feat in resolution["features"] if feat["status"] == "not_resolved"]}
            continue
        reports[channel] = compare_results(reference[channel], candidate[channel], frequency_step_hz=frequency_step,
                    resonance_prominence_db=frozen.case.prominence_db,
                    expected_resonance_columns=(expected_by_channel or {}).get(channel,
                        {"pressure_complex": frozen.case.expected_pressure_columns} if expected else None),
                    resonance_context=tuple(context[channel] for context in contexts) if feature_context else None)
        if expected_features:
            reports[channel]["feature_resolution"] = resolution
        # Sphere is complex pressure too, and gets the identical 30 dB gate/budgets.
        a, b = reference_run["native"][channel], candidate_run["native"][channel]
        extras = {}
        resonance_passed = all(g["passed"] for g in reports[channel].get("resonances", {}).values())
        for field in ("sphere_pressure_complex", "surface_pressure_complex", "surface_neumann_complex", "electrical_impedance_ohm"):
            if not resonance_passed:
                continue
            if a.get(field) is not None and b.get(field) is not None:
                limits = (("masked_spl_db", NORMALIZED_IMPEDANCE_DB), ("phase_deg", NORMALIZED_IMPEDANCE_PHASE_DEG)) if field == "electrical_impedance_ohm" else (
                    ("relative_l2", PRODUCTION_COMPLEX_RELATIVE_L2),
                    ("masked_spl_db", PRODUCTION_MASKED_SPL_DB), ("phase_deg", PRODUCTION_PHASE_DEG))
                av, bv = np.asarray(a[field]).reshape(len(reference[channel].frequencies_hz), -1), np.asarray(b[field]).reshape(len(candidate[channel].frequencies_hz), -1)
                metrics = _masked_complex(av, bv)
                metrics["passed"] = all(metrics.get(k) is not None and metrics[k] <= limit for k, limit in limits)
                extras[field] = metrics
        reports[channel]["extra_fields"] = extras
        if not all(m["passed"] for m in extras.values()):
            reports[channel]["passed"] = False
            reports[channel]["failures"].append("Sphere/trace complex pressure budget exceeded")
    request = SolveRequest.model_validate(frozen.request)
    required = {c.id for c in request.geometry.drive_channels} if frozen.record else {"source"}
    for name, run in (("hbb", reference_run), ("official", candidate_run)):
        missing = required - set(run.get("native", {}))
        if missing:
            (unavailable_ref if name == "hbb" else unavailable_got)["missing_channels"] = sorted(missing)
    trace_missing = frozen.case.traces and any(
        field in missing for unavailable in (unavailable_ref, unavailable_got)
        for missing in unavailable.values() if isinstance(missing, dict)
        for field in ("surface_pressure_complex", "surface_neumann_complex"))
    driver_missing = frozen.case.name == "driver-loading" and any(
        values.get("electrical_impedance_ohm") is None for run in (reference_run, candidate_run)
        for values in run.get("native", {}).values())
    status, sentinels = "scored", {}
    reference_features = detect_features(contexts[0], frozen.case)
    if feature_context is None and not reference_features:
        if not (frozen.case.expect_no_features and frozen.case.no_features_reason):
            status = "no_features_observed"
    if feature_context is None and frozen.case.require_narrow:
        sentinels["narrowness"] = narrowness_sentinel(reference, frozen.case.prominence_db, refined_frequencies or set())
        if not sentinels["narrowness"]["passed"]:
            status = "resonance_not_narrow"
    if feature_context is None and frozen.case.require_cut_sensitivity:
        sentinels["cut_sensitivity"] = cut_sentinel(reference, settings["observation_planes"])
        if not sentinels["cut_sensitivity"]["passed"]:
            status = "cut_not_discriminating"
    if not reference_features and not (frozen.case.expect_no_features and frozen.case.no_features_reason):
        status = "no_features_observed"
    return {"status": status, "frequency_step": frequency_step, "sentinels": sentinels,
            "reference_feature_count": len(reference_features),
            "identities": {"hbb": reference_run["identity"], "official": candidate_run["identity"]},
            "passed": status == "scored" and bool(reports) and set(reports) == required and all(r["passed"] for r in reports.values()) and not trace_missing and not driver_missing,
            "timing": {name: timing_context(run)
                       for name, run in (("hbb", reference_run), ("official", candidate_run))},
            "channels": reports, "unavailable": {"hbb": unavailable_ref, "official": unavailable_got},
            "limitations": ["Identities from installed VCS metadata are attested, not source-byte verified",
                            "Production captures are API rows, not recorder-owned terminal counts",
                            "DI/power use the same sampled full-sphere far-field estimator; native power stays raw"]}


def run_case(case: CorpusCase, directory: Path, *, backend: str, precision: str, julia: str,
             phase: str = "both", coarse_dir: Path | None = None, refine_part: int = 1,
             refine_dirs: tuple[Path, ...] = (), pair_runner: Callable = isolated_pair,
             freezer: Callable = freeze_case, max_part_minutes: float = 8., hbb_depot: str | None = None) -> dict:
    if not math.isfinite(max_part_minutes) or max_part_minutes <= 0:
        raise ValueError("--max-part-minutes must be positive and finite")
    directory = empty_output(directory)
    if backend == "metal" and precision != "float32":
        raise ValueError("Metal production agreement requires float32")
    if case.unsupported:
        verdict = {"passed": False, "status": "unsupported", "refusal": case.unsupported}
        write_json(directory / "verdict.json", verdict)
        return verdict
    if phase == "refine" and coarse_dir is None:
        raise ValueError("--phase refine requires --coarse-dir containing frozen coarse evidence")
    if pair_runner is isolated_pair:
        wg_identity()
        child_environment(official=False, julia=julia, hbb_depot=hbb_depot)
        require_official_ready(backend, julia)
    if phase == "refine":
        frozen = load_frozen(coarse_dir, case)
        inputs = read_json(coarse_dir / "inputs.json")
        if (inputs["frequencies_hz"] != list(case.coarse_hz)
                or inputs["frequency_step_hz"] != case.dense_step_hz or inputs["case"] != json_value(case)):
            raise ValueError("Refinement catalogue/dense declaration differs from acquisition evidence")
        if inputs["backend"] != backend or inputs["precision"] != precision:
            raise ValueError("Refinement backend/precision must match coarse evidence")
        reference_run, candidate_run = (read_json(coarse_dir / "coarse" / f"{name}.json") for name in ("hbb", "official"))
    else:
        frozen = freezer(case, directory, backend=backend)
    save_frozen(frozen, directory)
    settings = settings_for(frozen, backend, precision)
    if phase == "refine" and json_value(inputs["settings"]) != json_value(settings):
        raise ValueError("Refinement frozen settings differ from coarse evidence")
    write_json(directory / "inputs.json", {"case": case, "settings": settings,
               "backend": backend, "precision": precision, "mesh_sha256": frozen.sha256,
               "frequencies_hz": case.coarse_hz, "frequency_step_hz": max(np.diff(case.coarse_hz)),
               "environment_thread_policy": {k: "1" for k in THREAD_ENV}})
    if phase != "refine":
        reference_run, candidate_run = pair_runner(frozen, case.coarse_hz, directory / "coarse",
                                backend=backend, precision=precision, julia=julia, hbb_depot=hbb_depot)
    refusals = [r.get("refusal") for r in (reference_run, candidate_run)]
    if all(refusals):
        verdict = {"passed": False, "status": "unsupported", "refusals": dict(zip(("hbb", "official"), refusals))}
    elif any("error" in r for r in (reference_run, candidate_run)):
        verdict = {"passed": False, "status": "failed", "errors": [r.get("error") for r in (reference_run, candidate_run)]}
    else:
        coarse_score = score_pair(reference_run, candidate_run, frozen, settings,
                                  frequency_step=case.dense_step_hz, expected=True)
        write_json(directory / "coarse-score.json", coarse_score)
        reference, _ = map_results(reference_run, frozen, settings, official=False)
        candidate, _ = map_results(candidate_run, frozen, settings, official=True)
        plan = refine_windows(reference, case, candidate=candidate, timing={"hbb": engine_timing(reference_run),
                              "official": engine_timing(candidate_run)}, max_part_minutes=max_part_minutes)
        write_json(directory / "refine-plan.json", plan)
        verdict = {"passed": False, "status": "coarse_complete", "coarse": coarse_score,
                   "required_refine_parts": len(plan["parts"]), "completed_refine_parts": []}
        if phase != "coarse" and coarse_score["status"] != "no_features_observed":
            refs, candidates = [reference_run], [candidate_run]
            completed = set()
            for source in refine_dirs:
                prior = read_json(source / "part.json")
                if prior["mesh_sha256"] != frozen.sha256 or prior["plan"] != plan or prior["backend"] != backend or prior["precision"] != precision:
                    raise ValueError("Refine evidence mesh/plan/backend/precision differs")
                refs.append(read_json(source / "refine" / "hbb.json"))
                candidates.append(read_json(source / "refine" / "official.json"))
                completed.add(prior["part"])
            if plan["parts"]:
                if not 1 <= refine_part <= len(plan["parts"]):
                    raise ValueError("--refine-part outside the reference plan")
                part = plan["parts"][refine_part - 1]
                a, b = pair_runner(frozen, tuple(part["frequencies_hz"]), directory / "refine",
                                  backend=backend, precision=precision, julia=julia,
                                  timeout_seconds=max_part_minutes * 60, hbb_depot=hbb_depot)
                if any("error" in r for r in (a, b)):
                    raise ValueError(f"Refine engine failure: {[r.get('error') for r in (a, b)]}")
                write_json(directory / "part.json", {"part": refine_part, "plan": plan,
                           "mesh_sha256": frozen.sha256, "backend": backend, "precision": precision,
                           "timing": {"hbb": engine_timing(a), "official": engine_timing(b)},
                           "identities": {"hbb": a["identity"], "official": b["identity"]}})
                refs.append(a)
                candidates.append(b)
                completed.add(refine_part)
            final_ref, final_got = merge_runs(refs), merge_runs(candidates)
            refined_axis = {f for index in completed for f in plan["parts"][index - 1]["frequencies_hz"]}
            report = score_pair(final_ref, final_got, frozen, settings,
                                frequency_step=case.dense_step_hz, expected=True, refined_frequencies=refined_axis)
            local_reports = []
            for window in plan["windows"]:
                axis = window["frequencies_hz"]
                if not set(axis) <= set(final_ref["frequencies_hz"]):
                    continue
                expected_columns = {}
                for feature in window["features"]:
                    name = "normalized_impedance" if feature["quantity"] == "impedance_per_acceleration" else feature["quantity"]
                    columns = expected_columns.setdefault(feature["channel"], {}).setdefault(name, set())
                    columns.add(feature["column"])
                expected_columns = {ch: {name: tuple(sorted(cols)) for name, cols in quantities.items()}
                                    for ch, quantities in expected_columns.items()}
                local_reports.append(score_pair(slice_run(final_ref, axis), slice_run(final_got, axis), frozen, settings,
                                                frequency_step=window["step_hz"], expected_by_channel=expected_columns,
                                                expected_features=window["features"], feature_context=(final_ref, final_got)))
            complete = len(completed) == len(plan["parts"]) and len(local_reports) == len(plan["windows"])
            verdict.update(passed=complete and report["passed"] and all(r["passed"] for r in local_reports),
                           status="complete" if complete else "refine_incomplete", agreement=report,
                           local_windows=local_reports, completed_refine_parts=sorted(completed))
    if "coarse" in verdict and verdict["coarse"]["status"] == "no_features_observed":
        verdict.update(passed=False, status="no_features_observed")
    if "agreement" in verdict and verdict["status"] == "complete" and verdict["agreement"]["status"] != "scored":
        verdict.update(passed=False, status=verdict["agreement"]["status"])
    verdict.update(case=case.name, phase=phase, qualified=False,
                   identities={name: run.get("identity", {}) for name, run in
                               (("hbb", reference_run), ("official", candidate_run))})
    # This corpus scores agreement; installed/device qualification still requires recorder evidence.
    write_json(directory / "verdict.json", verdict)
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(CASES))
    parser.add_argument("--backend", choices=("cpu", "metal"), default="cpu")
    parser.add_argument("--precision", choices=("float32", "float64"), default="float32")
    parser.add_argument("--julia", required=True)
    parser.add_argument("--hbb-depot", required=True, help="HBB-only Julia depot directory or explicit depot chain")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--phase", choices=("coarse", "refine", "both"), default="both")
    parser.add_argument("--coarse-dir", type=Path)
    parser.add_argument("--refine-part", type=int, default=1)
    parser.add_argument("--max-part-minutes", type=float, default=8.,
                        help="Hard estimated wall budget per part for both engines including startup (default: 8)")
    parser.add_argument("--refine-dir", type=Path, action="append", default=[])
    parser.add_argument("--engine-child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--engine-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--official-child", type=int, choices=(0, 1), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.engine_child:
        return engine_child(args.engine_child, args.engine_output, official=bool(args.official_child),
                            backend=args.backend, precision=args.precision, julia=args.julia)
    if not args.case or args.output_dir is None:
        parser.error("--case and --output-dir are required")
    try:
        verdict = run_case(CASES[args.case], args.output_dir, backend=args.backend, precision=args.precision,
                           julia=args.julia, phase=args.phase, coarse_dir=args.coarse_dir,
                           refine_part=args.refine_part, refine_dirs=tuple(args.refine_dir),
                           max_part_minutes=args.max_part_minutes, hbb_depot=args.hbb_depot)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")
    print(dumps(verdict))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
