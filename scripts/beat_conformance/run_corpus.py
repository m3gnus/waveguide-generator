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
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
from scipy.signal import peak_prominences

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.jobs.models import SolveRequest
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
    return {"wg_commit": command_evidence(["git", "rev-parse", "HEAD"]),
            "wg_worktree": command_evidence(["git", "status", "--porcelain"]),
            "distributions": {name: distribution_identity(name) for name in ("hornlab-beat-bem", "beat-engine")},
            "hbb_pin": hbb_pin(), "julia_path": str(Path(julia).expanduser().resolve()),
            "julia_version": command_evidence([julia, "--startup-file=no", "--version"]),
            "thread_env": {name: os.environ.get(name) for name in THREAD_ENV},
            "runtime_env": {name: os.environ.get(name) for name in
                            ("WG2_BEAT_PROVIDER", "WG2_BEAT_RUNTIME_DIR", "WG2_BEAT_WORKER_DIR", "HORNLAB_BEAT_JULIA")},
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
        # Copy immediately: imported driver scaling and later packaging may mutate arrays.
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
                driver = response.get("channels", {}).get(channel, {}).get("metadata", {}).get("driver", {})
                electrical = driver.get("electrical_impedance_ohm")
                if electrical:
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
    identity = capture_identity(julia)
    name = "beat-engine" if official else "hornlab-beat-bem"
    try:
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
                  backend: str, precision: str, julia: str) -> tuple[dict, dict]:
    """Sequential subprocesses; official child manager cannot adopt a warm host."""
    require_official_ready(backend, julia)
    directory.mkdir(parents=True)
    save_frozen(frozen, directory)
    write_json(directory / "job.json", {"case": frozen.case.name, "frequencies_hz": frequencies})
    runs = []
    for official, name in ((False, "hbb"), (True, "official")):
        env = dict(os.environ, **{k: "1" for k in THREAD_ENV})
        env.update(WG2_BEAT_PROVIDER="official" if official else "", WG2_BEAT_JULIA=julia, HORNLAB_BEAT_JULIA=julia)
        # Inherit WG2_BEAT_RUNTIME_DIR/WG2_BEAT_WORKER_DIR verbatim; HBB uses its own directories.
        target = directory / f"{name}.json"
        command = [sys.executable, "-m", "scripts.beat_conformance.run_corpus", "--engine-child", str(directory / "job.json"),
                   "--engine-output", str(target), "--official-child", str(int(official)),
                   "--backend", backend, "--precision", precision, "--julia", julia]
        with (directory / f"{name}.log").open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.wait(timeout=240 if directory.name == "coarse" else 320)
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
        runs.append(read_json(target))
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
                     np.asarray(values["impedance"]), di, power, observed, revision, setting_evidence=evidence)
        for field in ("surface_pressure_complex", "surface_neumann_complex", "radiated_power_surface_w", "radiated_power_sphere_w"):
            if values.get(field) is None:
                unavailable.setdefault(channel, {})[field] = (
                    run.get("response", {}).get("_field_trace_unavailable_reason") or "not returned by this production route")
        if frozen.case.name == "driver-loading" and values.get("electrical_impedance_ohm") is None:
            unavailable.setdefault(channel, {})["electrical_impedance_ohm"] = "driver electrical impedance not returned"
    if not run.get("native"):
        unavailable["all"] = run.get("error", "production function returned no native fields")
    return mapped, unavailable


def refine_windows(reference: dict[str, ResultSet], case: CorpusCase) -> dict[str, Any]:
    """Reference-only topographic prominence; dyadic steps strictly below 0.5%."""
    windows = {}
    for channel, result in reference.items():
        f = result.frequencies_hz
        for quantity in ("pressure_complex", "impedance_per_acceleration"):
            data = np.asarray(getattr(result, quantity)).reshape(len(f), -1)
            if quantity == "impedance_per_acceleration":
                data = data * (-1j * 2 * np.pi * f[:, None]) / (1.2041 * 343.)
            for column in range(data.shape[1]):
                levels = 20 * np.log10(np.maximum(np.abs(data[:, column]), np.finfo(float).tiny))
                for kind, indices in _extrema(levels, case.prominence_db).items():
                    signed = levels if kind == "peaks" else -levels
                    _, left_bases, right_bases = peak_prominences(signed, indices)
                    for index, left, right in zip(indices, left_bases, right_bases):
                        # Full prominence bases bracket broad physical features too;
                        # neighboring samples alone can erase their prominence.
                        key = (float(f[left]), float(f[right]), float(f[index]))
                        step = 2. ** math.floor(math.log2(float(f[index]) * 0.0025))
                        start, end = key[:2]
                        axis = np.arange(math.floor(start / step), math.ceil(end / step) + 1) * step
                        axis = axis[axis > 0]
                        validate_frequency_axis(tuple(axis))
                        entry = windows.setdefault(key, {"frequencies_hz": axis.tolist(), "step_hz": step, "features": []})
                        entry["features"].append({"channel": channel, "quantity": quantity,
                                                 "column": column, "kind": kind, "frequency_hz": float(f[index])})
    parts, current = [], set()
    # Split acquisition only, with two-row overlaps. Score complete windows after
    # gathering parts; a peak on a part boundary cannot disappear from the gate.
    for window in windows.values():
        for frequency in window["frequencies_hz"]:
            if frequency in current:
                continue
            if len(current) == case.max_refine_count:
                parts.append(sorted(current))
                current = set(sorted(current)[-2:])
            current.add(frequency)
    if current:
        parts.append(sorted(current))
    return {"prominence_db": case.prominence_db, "windows": list(windows.values()),
            "parts": [{"part": i + 1, "frequencies_hz": axis} for i, axis in enumerate(parts)],
            "count": len(set(f for p in parts for f in p)), "per_part_limit": case.max_refine_count}


def merge_runs(runs: list[dict]) -> dict:
    """Join acquired rows without interpolation; duplicate frequency uses latest row."""
    from copy import deepcopy
    for run in runs[1:]:
        if run.get("mesh_sha256") != runs[0].get("mesh_sha256"):
            raise ValueError("Refine run mesh differs from coarse evidence")
        for key in ("distributions", "wg_commit", "julia_path", "julia_version", "thread_env", "hbb_pin", "runtime_env"):
            if run.get("identity", {}).get(key) != runs[0].get("identity", {}).get(key):
                raise ValueError(f"Refine run identity {key} differs from coarse evidence")
    merged = deepcopy(runs[0])
    for channel in merged["native"]:
        members = [r["native"][channel] for r in runs]
        rows = {float(f): (m, i) for m in members for i, f in enumerate(m["frequencies_hz"])}
        axis = sorted(rows)
        target = merged["native"][channel]
        target["frequencies_hz"] = np.asarray(axis)
        for field in ARRAY_FIELDS:
            if all(m.get(field) is not None for m in members):
                target[field] = np.asarray([np.asarray(rows[f][0][field])[rows[f][1]] for f in axis])
            else:
                target[field] = None
        target["solver_log"] = [rows[f][0]["solver_log"][rows[f][1]] for f in axis]
    merged["frequencies_hz"] = axis
    return merged


def score_pair(reference_run: dict, candidate_run: dict, frozen: FrozenCase, settings: dict, *,
               frequency_step: float, expected: bool = False,
               expected_by_channel: dict[str, dict[str, tuple[int, ...]]] | None = None) -> dict:
    reference, unavailable_ref = map_results(reference_run, frozen, settings, official=False)
    candidate, unavailable_got = map_results(candidate_run, frozen, settings, official=True)
    reports = {}
    for channel in reference.keys() & candidate.keys():
        reports[channel] = compare_results(reference[channel], candidate[channel], frequency_step_hz=frequency_step,
                    resonance_prominence_db=frozen.case.prominence_db,
                    expected_resonance_columns=(expected_by_channel or {}).get(channel,
                        {"pressure_complex": frozen.case.expected_pressure_columns} if expected else None))
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
    return {"passed": bool(reports) and set(reports) == required and all(r["passed"] for r in reports.values()) and not trace_missing and not driver_missing,
            "channels": reports, "unavailable": {"hbb": unavailable_ref, "official": unavailable_got},
            "limitations": ["Identities from installed VCS metadata are attested, not source-byte verified",
                            "Production captures are API rows, not recorder-owned terminal counts",
                            "DI/power use the same sampled full-sphere far-field estimator; native power stays raw"]}


def run_case(case: CorpusCase, directory: Path, *, backend: str, precision: str, julia: str,
             phase: str = "both", coarse_dir: Path | None = None, refine_part: int = 1,
             refine_dirs: tuple[Path, ...] = (), pair_runner: Callable = isolated_pair,
             freezer: Callable = freeze_case) -> dict:
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
        require_official_ready(backend, julia)
    if phase == "refine":
        frozen = load_frozen(coarse_dir, case)
        inputs = read_json(coarse_dir / "inputs.json")
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
                                backend=backend, precision=precision, julia=julia)
    refusals = [r.get("refusal") for r in (reference_run, candidate_run)]
    if all(refusals):
        verdict = {"passed": False, "status": "unsupported", "refusals": dict(zip(("hbb", "official"), refusals))}
    elif any("error" in r for r in (reference_run, candidate_run)):
        verdict = {"passed": False, "status": "failed", "errors": [r.get("error") for r in (reference_run, candidate_run)]}
    else:
        coarse_score = score_pair(reference_run, candidate_run, frozen, settings,
                                  frequency_step=max(np.diff(case.coarse_hz)), expected=True)
        write_json(directory / "coarse-score.json", coarse_score)
        reference, _ = map_results(reference_run, frozen, settings, official=False)
        plan = refine_windows(reference, case)
        write_json(directory / "refine-plan.json", plan)
        verdict = {"passed": False, "status": "coarse_complete", "coarse": coarse_score,
                   "required_refine_parts": len(plan["parts"]), "completed_refine_parts": []}
        if phase != "coarse":
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
                                  backend=backend, precision=precision, julia=julia)
                if any("error" in r for r in (a, b)):
                    raise ValueError(f"Refine engine failure: {[r.get('error') for r in (a, b)]}")
                write_json(directory / "part.json", {"part": refine_part, "plan": plan,
                           "mesh_sha256": frozen.sha256, "backend": backend, "precision": precision})
                refs.append(a)
                candidates.append(b)
                completed.add(refine_part)
            final_ref, final_got = merge_runs(refs), merge_runs(candidates)
            report = score_pair(final_ref, final_got, frozen, settings,
                                frequency_step=max(np.diff(case.coarse_hz)), expected=True)
            local_reports = []
            for window in plan["windows"]:
                axis = window["frequencies_hz"]
                if not set(axis) <= set(final_ref["frequencies_hz"]):
                    continue
                def slice_run(run):
                    from copy import deepcopy
                    result = deepcopy(run)
                    for value in result["native"].values():
                        indices = [list(value["frequencies_hz"]).index(f) for f in axis]
                        value["frequencies_hz"] = np.asarray(axis)
                        for field in ARRAY_FIELDS:
                            if value.get(field) is not None:
                                value[field] = np.asarray(value[field])[indices]
                        value["solver_log"] = [value["solver_log"][i] for i in indices]
                    result["frequencies_hz"] = axis
                    return result
                expected_columns = {}
                for feature in window["features"]:
                    name = "normalized_impedance" if feature["quantity"] == "impedance_per_acceleration" else feature["quantity"]
                    columns = expected_columns.setdefault(feature["channel"], {}).setdefault(name, set())
                    columns.add(feature["column"])
                expected_columns = {ch: {name: tuple(sorted(cols)) for name, cols in quantities.items()}
                                    for ch, quantities in expected_columns.items()}
                local_reports.append(score_pair(slice_run(final_ref), slice_run(final_got), frozen, settings,
                                                frequency_step=window["step_hz"], expected_by_channel=expected_columns))
            complete = len(completed) == len(plan["parts"]) and len(local_reports) == len(plan["windows"])
            verdict.update(passed=complete and report["passed"] and all(r["passed"] for r in local_reports),
                           status="complete" if complete else "refine_incomplete", agreement=report,
                           local_windows=local_reports, completed_refine_parts=sorted(completed))
    verdict.update(case=case.name, phase=phase, qualified=False)
    # This corpus scores agreement; installed/device qualification still requires recorder evidence.
    write_json(directory / "verdict.json", verdict)
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(CASES))
    parser.add_argument("--backend", choices=("cpu", "metal"), default="cpu")
    parser.add_argument("--precision", choices=("float32", "float64"), default="float32")
    parser.add_argument("--julia", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--phase", choices=("coarse", "refine", "both"), default="both")
    parser.add_argument("--coarse-dir", type=Path)
    parser.add_argument("--refine-part", type=int, default=1)
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
                           refine_part=args.refine_part, refine_dirs=tuple(args.refine_dir))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")
    print(dumps(verdict))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
