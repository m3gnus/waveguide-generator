"""Record failures; real mode owns independent probes and engine submissions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

import numpy as np

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.solver.beat_adapter.request import CompiledRequest
from server.solver.beat_adapter.results import SweepResult, map_sweep
from server.solver.beat_runtime.paths import PROVIDER_ID
from server.solver.beat_runtime.threads import resolve_julia_threads

from .cases import ConformanceCase, all_cases
from .verification import verify_runtime


@dataclass(frozen=True)
class RuntimeEvidence:
    """Runner attestations; these alone can never qualify a real solve."""
    backend: str
    precision: str
    julia_executable: str
    julia_version: str
    engine_path: str
    engine_revision: str
    engine_distribution: str = "beat-engine"
    device_class: str = "cpu"
    device_name: str = ""
    device_kernel_verified: bool = False
    real_solves: bool = False
    artifact_kind: str = "source"
    engine_fingerprint: str = ""
    worker_state: str = "unknown"


@dataclass(frozen=True)
class SolveEvidence:
    result: SweepResult
    runtime: RuntimeEvidence


@dataclass(frozen=True)
class EngineRun:
    """Launch selection only; the recorder owns probes, worker and event decoding."""
    julia_executable: str
    julia_threads: int | str = "auto"


Solve = Callable[[CompiledRequest], SolveEvidence | EngineRun]


def _engine_worker(**options: Any) -> Any:
    from beat_engine import EngineWorker
    return EngineWorker(**options)


def _observed_solve(request: CompiledRequest, selection: EngineRun, record: dict[str, Any]) -> SolveEvidence:
    backend = request.wire["solver_options"]["bem_backend"]
    precision = request.wire["solver_options"]["precision"]
    facts = verify_runtime(selection.julia_executable, backend)
    revision_status = facts.pop("engine_revision_status", "observed")
    record["observations"] = {name: {"status": "observed", "value": value}
                              for name, value in facts.items()}
    record["observations"]["engine_revision"]["status"] = revision_status
    worker = _engine_worker(
        julia_executable=facts["julia_executable"], solver_script=Path(facts["solver_script"]),
        julia_project=Path(facts["project"]),
        julia_threads=resolve_julia_threads(backend, selection.julia_threads))
    terminal_events = []
    try:
        stream = worker.submit(request.wire)
        def observe():
            try:
                for event in stream:
                    if event.get("type") in {"completed", "cancelled", "failed"}:
                        terminal_events.append(event)
                    yield event
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
        result = map_sweep(observe(), request.wire["frequencies_hz"], layout=request.layout,
                           source_area_m2=request.channel_loading["source"].area_m2,
                           excitation_port_id="source", boundary_loading=request.channel_loading["source"],
                           precision=precision, backend=backend)
    finally:
        record["terminal_events"] = terminal_events
        worker.terminate()
    # map_sweep checks terminal count against decoded rows and the full request.
    count = terminal_events[-1]["solved_count"] if terminal_events[-1]["type"] == "completed" else 0
    record["observations"]["solve_count"] = {"status": "observed", "value": count}
    record["observations"]["backend"] = {"status": "observed", "value": backend}
    record["observations"]["precision"] = {"status": "observed", "value": precision}
    runtime = RuntimeEvidence(backend, precision, facts["julia_executable"], facts["julia_version"],
                              facts["engine_path"], facts["engine_revision"],
                              device_class=facts["device_class"], device_name=facts["device_name"],
                              device_kernel_verified=facts["device_kernel_verified"],
                              artifact_kind=facts["artifact_kind"], engine_fingerprint=facts["engine_fingerprint"])
    return SolveEvidence(result, runtime)


Comparator = Callable[[SweepResult, dict[str, Any]], None]


def _json_value(value: Any) -> Any:
    """Strict JSON including complex fields and explicit nonfinite samples."""
    if isinstance(value, np.ndarray):
        return _json_value(value.tolist())
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, complex):
        return [_json_value(value.real), _json_value(value.imag)]
    if isinstance(value, float) and not np.isfinite(value):
        return {"nonfinite": str(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def record_sha256(record: dict[str, Any]) -> str:
    content = {key: value for key, value in record.items() if key != "record_sha256"}
    return hashlib.sha256(json.dumps(_json_value(content), sort_keys=True, allow_nan=False).encode()).hexdigest()


def write_record(path: Path, record: dict[str, Any]) -> None:
    record["record_sha256"] = record_sha256(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_value(record), indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _validate_solve(case: ConformanceCase, evidence: SolveEvidence, record: dict[str, Any],
                    *, evidence_mode: str) -> None:
    result, runtime = evidence.result, evidence.runtime
    count = len(result.frequencies_hz)
    observed = record.get("observations", {})
    solve_count = observed.get("solve_count", {})
    real_count = solve_count.get("value", 0) if solve_count.get("status") == "observed" and evidence_mode == "real" else 0
    record["solved_count"] = count
    record["real_solved_count"] = real_count
    failures = []
    if (result.cancelled or result.is_partial or result.requested_frequency_count != len(case.frequencies_hz)
            or not np.array_equal(result.frequencies_hz, case.frequencies_hz)):
        failures.append("required solve did not complete the declared frequency list in order")
    if count < case.min_solved_count:
        failures.append(f"solve count {count} is below floor {case.min_solved_count}")
    if evidence_mode == "real":
        if real_count < case.min_solved_count or real_count != count:
            failures.append("observed terminal real solve count is below the required floor or differs from rows")
        for name in ("backend", "precision", "device_class", "device_name", "julia_version"):
            if observed.get(name, {}).get("status") != "observed":
                failures.append(f"{name} evidence is attested; real qualification requires observed")
    if (runtime.backend, runtime.precision) != (case.backend, case.precision):
        failures.append("recorded backend/precision differs from case")
    if (not runtime.julia_executable or not runtime.julia_version or not runtime.engine_path
            or not runtime.engine_revision or runtime.engine_distribution != "beat-engine"):
        failures.append("required Julia/engine identity is missing")
    expected_device = "cpu" if case.backend == "cpu" else "gpu"
    if runtime.device_class != expected_device or not runtime.device_name:
        failures.append("recorded device differs from required backend")
    if case.backend == "metal" and runtime.device_kernel_verified is not True:
        failures.append("Metal requires a verified dispatched device kernel")
    if runtime.artifact_kind not in {"source", "installed"}:
        failures.append("unknown artifact kind")
    if len(result.solver_log) != count:
        failures.append("missing per-frequency solve diagnostics")
    for index, row in enumerate(result.solver_log):
        if (row.get("converged") is not True or index >= count
                or row.get("frequency_hz") != result.frequencies_hz[index]):
            failures.append("result log lacks converged frequency evidence")
            break
        diagnostic = row.get("native_diagnostics", {})
        if any(diagnostic.get(key) != value for key, value in
               {"bem_backend": case.backend, "precision": case.precision,
                "phasor_convention": SOLVER_TIME_CONVENTION}.items()):
            failures.append("result diagnostics disagree with required runtime/convention")
            break
    record["qualification_failures"] = failures
    if failures:
        raise ValueError("; ".join(failures))


def provenance() -> dict[str, Any]:
    """Fingerprint WG policy and record timing context without scanning data roots."""
    adapter = Path(__file__).resolve().parents[2] / "server/solver/beat_adapter"
    digest = hashlib.sha256()
    for namespace, root in (("adapter", adapter), ("conformance", Path(__file__).parent)):
        for path in sorted(path for path in root.iterdir() if path.suffix in {".py", ".jl"}):
            digest.update(f"{namespace}/{path.name}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    try:
        load = list(os.getloadavg())
    except (AttributeError, OSError):
        load = None
    return {"python": {"executable_sha256": hashlib.sha256(sys.executable.encode()).hexdigest(),
                       "version": sys.version},
            "platform": platform.platform(), "wg_policy_sha256": digest.hexdigest(),
            "captured_at_utc": datetime.now(timezone.utc).isoformat(), "load_average": load,
            "environment_overrides": {key: {"value_sha256": hashlib.sha256(value.encode()).hexdigest()}
                                      for key, value in sorted(os.environ.items())
                                      if key.startswith(("WG2_BEAT_", "BLAB_", "JULIA_"))}}


def run_case(case: ConformanceCase, *, output_dir: Path, solve: Solve | None = None,
             evidence_mode: str = "synthetic", comparator: Comparator | None = None,
             comparison_limitation: str = "No counterpart result was supplied") -> dict[str, Any]:
    """Persist a per-case record before propagating any failure, including skips."""
    if evidence_mode not in {"synthetic", "real"}:
        raise ValueError("Evidence mode must be synthetic or real")
    if not case.name or Path(case.name).name != case.name or case.name in {".", ".."}:
        raise ValueError("Case name must be a safe filename")
    record: dict[str, Any] = {
        "schema_version": 1, "provider": PROVIDER_ID, "evidence_mode": evidence_mode,
        "case": {"name": case.name, "description": case.description, "backend": case.backend,
                 "precision": case.precision, "frequencies_hz": case.frequencies_hz,
                 "min_solved_count": case.min_solved_count,
                 "kind": "solve" if case.make_request else "static"},
        "provenance": provenance(),
        "comparison": {"ran": False, "limitation": comparison_limitation},
        "solved_count": 0, "real_solved_count": 0, "qualified": False,
    }
    started = time.monotonic()
    try:
        if case.static_check:
            record["metrics"] = case.static_check()
            if record["metrics"].get("passed") is not True:
                raise AssertionError("Static conformance check failed")
        else:
            request = case.make_request()
            if (request.wire["frequencies_hz"] != list(case.frequencies_hz)
                    or request.wire["solver_options"]["bem_backend"] != case.backend
                    or request.wire["solver_options"]["precision"] != case.precision):
                raise ValueError("Built request differs from declared case")
            encoded = json.dumps(request.wire, sort_keys=True, allow_nan=False).encode()
            record["request"] = request.wire
            record["request_sha256"] = hashlib.sha256(encoded).hexdigest()
            record["mesh_sha256"] = hashlib.sha256(json.dumps(
                request.mesh.packed(), sort_keys=True).encode()).hexdigest()
            if solve is None:
                raise RuntimeError("Required Julia/engine solve function is unavailable")
            evidence = solve(request)
            if isinstance(evidence, EngineRun):
                if evidence_mode != "real":
                    raise ValueError("EngineRun requires real evidence mode")
                evidence = _observed_solve(request, evidence, record)
            else:
                record["observations"] = {name: {"status": "attested", "value": value}
                                          for name, value in asdict(evidence.runtime).items()}
            record["runtime"] = asdict(evidence.runtime)
            for name, value in record["runtime"].items():
                record["observations"].setdefault(name, {"status": "attested", "value": value})
            result = evidence.result
            record["result"] = asdict(result)
            _validate_solve(case, evidence, record, evidence_mode=evidence_mode)
            # Store scored values before evaluating their verdict.
            metrics = case.accept(result)
            record["metrics"] = metrics
            if not metrics["passed"]:
                raise AssertionError("; ".join(metrics["failures"]))
            if comparator:
                record["comparison"] = {"ran": True, "limitation": None}
                comparator(result, record["comparison"])
            record["qualified"] = evidence_mode == "real" and record["real_solved_count"] >= case.min_solved_count
        record["status"] = "passed"
        return record
    except BaseException as exc:
        record["status"] = "failed"
        record["qualified"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        if not isinstance(exc, (Exception, KeyboardInterrupt, SystemExit, GeneratorExit)):
            # pytest/unittest skip outcomes must become failures for these
            # required cases. No pytest dependency or global skip rule needed.
            raise RuntimeError(record["error"]) from exc
        raise
    finally:
        record["wall_seconds"] = time.monotonic() - started
        write_record(output_dir / f"{case.name}.json", record)


def run_cases(cases: Sequence[ConformanceCase], *, output_dir: Path, solve: Solve | None = None,
              evidence_mode: str = "synthetic", backend: str = "cpu") -> dict[str, Any]:
    """Run exactly the declared list and retain failed cases instead of skipping."""
    if backend not in {"cpu", "metal"}:
        raise ValueError("Qualification backend must be cpu or metal")
    catalog = {case.name: case for case in all_cases()}
    required = {case.name for case in catalog.values() if case.backend == "cpu" or backend == "metal"}
    if any(case.backend == "metal" for case in cases):
        required.update(case.name for case in all_cases())
    names = [case.name for case in cases]
    missing = sorted(required - set(names))
    if not names or len(set(names)) != len(names):
        raise ValueError("Case list must be nonempty with unique names")
    records = []
    for case in cases:
        try:
            record = run_case(case, output_dir=output_dir, solve=solve, evidence_mode=evidence_mode)
        except Exception:
            record = json.loads((output_dir / f"{case.name}.json").read_text(encoding="utf-8"))
        records.append(record)
    solve_records = [record for record in records if record["case"]["kind"] == "solve"]
    declaration_failures = []
    for record in records:
        canonical = catalog.get(record["case"]["name"])
        if canonical is None:
            continue
        spec = record["case"]
        if (tuple(spec["frequencies_hz"]), spec["backend"], spec["precision"], spec["min_solved_count"], spec["kind"]) != (
                canonical.frequencies_hz, canonical.backend, canonical.precision, canonical.min_solved_count,
                "solve" if canonical.make_request else "static"):
            declaration_failures.append(f"{canonical.name}: required declaration differs")
        if canonical.make_request:
            wire = canonical.make_request().wire
            digest = hashlib.sha256(json.dumps(wire, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if record.get("request_sha256") != digest:
                declaration_failures.append(f"{canonical.name}: required request differs")
        elif record.get("metrics", {}).get("expected_refusals") != canonical.static_check()["expected_refusals"]:
            declaration_failures.append(f"{canonical.name}: required static criterion differs")
    count = sum(record["real_solved_count"] for record in solve_records)
    passed = (not missing and not declaration_failures and bool(solve_records) and count > 0 and all(record["status"] == "passed" for record in records)
              and all(record["qualified"] for record in solve_records))
    report = {"schema_version": 1, "provider": PROVIDER_ID, "passed": passed,
              "evidence_mode": evidence_mode, "backend": backend, "qualified": passed,
              "missing_required_cases": missing, "qualification_failures": declaration_failures,
              "real_solved_count": count, "records": records,
              "installed_qualified": passed and all(record["runtime"]["artifact_kind"] == "installed"
                                                    for record in solve_records)}
    write_record(output_dir / "summary.json", report)
    return report
