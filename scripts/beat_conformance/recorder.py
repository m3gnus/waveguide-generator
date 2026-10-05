"""Record failures and gate declared solves; no engine or Julia is launched here."""

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
from server.solver.beat_adapter.results import SweepResult
from server.solver.beat_runtime.paths import PROVIDER_ID

from .cases import ConformanceCase


@dataclass(frozen=True)
class RuntimeEvidence:
    """Facts observed by the injected runner, not inferred from requested options."""
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


Solve = Callable[[CompiledRequest], SolveEvidence]
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


def write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_value(record), indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _validate_solve(case: ConformanceCase, evidence: SolveEvidence, record: dict[str, Any],
                    *, evidence_mode: str) -> None:
    result, runtime = evidence.result, evidence.runtime
    count = len(result.frequencies_hz)
    # Count received completed frequency results, never calls to solve(), tests
    # collected, or a worker handshake. Diagnostics are independently checked.
    real_count = count if runtime.real_solves is True and evidence_mode == "real" else 0
    record["solved_count"] = count
    record["real_solved_count"] = real_count
    failures = []
    if (result.cancelled or result.is_partial or result.requested_frequency_count != len(case.frequencies_hz)
            or not np.array_equal(result.frequencies_hz, case.frequencies_hz)):
        failures.append("required solve did not complete the declared frequency list in order")
    if count < case.min_solved_count:
        failures.append(f"solve count {count} is below floor {case.min_solved_count}")
    if evidence_mode == "real" and real_count < case.min_solved_count:
        failures.append("real solve count is below the required floor")
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
        for path in sorted(root.glob("*.py")):
            digest.update(f"{namespace}/{path.name}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    try:
        load = list(os.getloadavg())
    except (AttributeError, OSError):
        load = None
    return {"python": sys.executable, "version": sys.version, "prefix": sys.prefix,
            "platform": platform.platform(), "wg_policy_sha256": digest.hexdigest(),
            "captured_at_utc": datetime.now(timezone.utc).isoformat(), "load_average": load,
            "environment_overrides": {key: value for key, value in sorted(os.environ.items())
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
            if record["metrics"].get("passed") is False:
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
            record["runtime"] = asdict(evidence.runtime)
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
            record["qualified"] = evidence_mode == "real" and evidence.runtime.real_solves is True
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
              evidence_mode: str = "synthetic") -> dict[str, Any]:
    """Run exactly the declared list and retain failed cases instead of skipping."""
    names = [case.name for case in cases]
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
    count = sum(record["real_solved_count"] for record in solve_records)
    passed = (bool(solve_records) and count > 0 and all(record["status"] == "passed" for record in records)
              and all(record["qualified"] for record in solve_records))
    report = {"schema_version": 1, "provider": PROVIDER_ID, "passed": passed,
              "evidence_mode": evidence_mode, "real_solved_count": count, "records": records,
              "installed_qualified": passed and all(record["runtime"]["artifact_kind"] == "installed"
                                                    for record in solve_records)}
    write_record(output_dir / "summary.json", report)
    return report
