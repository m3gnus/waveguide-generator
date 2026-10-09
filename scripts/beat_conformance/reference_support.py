"""Raw analytic qualification using WG's decoder, managed sessions and recorder.

This module adds no production solve capabilities or worker lifecycle. Raw
velocity/force/transducer outputs deliberately bypass WG's acceleration mapper.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import tempfile
import traceback
from pathlib import Path

import numpy as np

from server.solver.beat_adapter.results import decode_complex_values
from server.solver.beat_runtime.manager import WorkerManager
from server.solver.beat_runtime.session import SolveSession
from .recorder import write_record
from .verification import verify_runtime

POSITIVE_TIME = "exp(+i omega t)"
NEGATIVE_TIME = "exp(-i omega t)"


def expected_engine_revision():
    return json.loads((Path(__file__).resolve().parents[2] / "pins.json").read_text())["modules"][
        "beat-engine"
    ]["sha"]


@dataclass(frozen=True)
class Quantity:
    id: str
    quantity: str
    unit: str
    axes: tuple[str, ...]
    values: np.ndarray
    target_id: str | None
    metadata: dict


@dataclass(frozen=True)
class FrequencyResult:
    freq_hz: float
    excitation_port_ids: tuple[str, ...]
    quantities: tuple[Quantity, ...]
    diagnostics: dict


@dataclass
class SolveOutcome:
    status: str
    results: list[FrequencyResult] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    worker_info: dict = field(default_factory=dict)
    engine_runs: list[dict] = field(default_factory=list)


class ReferenceSolveError(ValueError):
    def __init__(self, message, outcome):
        super().__init__(message)
        self.outcome = outcome


def decode_result_v2(raw, *, expected_phasor, expected_ports):
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 2
    ):
        raise ValueError("Expected result schema v2")
    diagnostics = raw.get("diagnostics")
    if not isinstance(diagnostics, dict) or diagnostics.get("phasor_convention") != expected_phasor:
        raise ValueError("Returned phasor differs from request")
    if raw.get("excitation_port_ids") != expected_ports:
        raise ValueError("Returned excitation order differs from request")
    frequency = raw.get("freq_hz")
    if type(frequency) not in (int, float) or not math.isfinite(frequency) or frequency <= 0:
        raise ValueError("Returned frequency must be positive and finite")
    items = raw.get("quantities")
    if not isinstance(items, list) or not items:
        raise ValueError("Result quantities must be a nonempty list")
    quantities = []
    identifiers = set()
    for item in items:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) or not item[key]
            for key in ("id", "quantity", "unit")
        ):
            raise ValueError("Malformed result quantity")
        if item["id"] in identifiers:
            raise ValueError("Duplicate quantity ID")
        identifiers.add(item["id"])
        descriptor = item.get("values")
        axes = item.get("axes")
        if not isinstance(descriptor, dict) or descriptor.get("dtype") != "complex128":
            raise ValueError("CPU analytic references require returned Float64 arrays")
        shape = descriptor.get("shape")
        if not isinstance(shape, list) or any(type(n) is not int or n < 0 for n in shape):
            raise ValueError("Invalid result shape")
        if (
            not isinstance(axes, list)
            or any(not isinstance(a, str) or not a for a in axes)
            or len(axes) != len(shape)
            or len(set(axes)) != len(axes)
        ):
            raise ValueError("Invalid quantity axes")
        values = decode_complex_values(descriptor, tuple(shape))
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Invalid quantity metadata")
        quantities.append(
            Quantity(
                item["id"],
                item["quantity"],
                item["unit"],
                tuple(axes),
                values,
                item.get("target_id"),
                deepcopy(metadata),
            )
        )
    return FrequencyResult(
        float(frequency), tuple(expected_ports), tuple(quantities), deepcopy(diagnostics)
    )


def _verify_outputs(result, request):
    precision = {"complex128": "float64"}.get(
        result.diagnostics.get("precision"), result.diagnostics.get("precision")
    )
    if request["solver_options"].get("precision") != "float64" or precision != "float64":
        raise ValueError("Returned precision differs from request")
    actual = {q.id: q for q in result.quantities}
    if len(actual) != len(result.quantities) or set(actual) != {
        o["id"] for o in request["outputs"]
    }:
        raise ValueError("Returned output IDs differ from request")
    count = len(request["excitation_port_ids"])
    components = request["compiled_system"]["components"]
    for output in request["outputs"]:
        q = actual[output["id"]]
        if output.get("target_ids") != [] or q.target_id is not None:
            raise ValueError("Untargeted analytic output has an unexpected target identity")
        if (
            q.quantity != output["quantity"]
            or q.values.dtype != np.dtype("complex128")
            or not np.isfinite(q.values).all()
        ):
            raise ValueError("Returned quantity or precision differs from request")
        if q.quantity == "exterior_pressure":
            expected = (
                "Pa",
                ("excitation", "observation"),
                (count, len(output["options"]["points_m"])),
            )
        elif q.quantity == "radiation_impedance":
            expected = ("N*s/m", ("radiator",), (len(components),))
        elif q.quantity in {"diaphragm_velocity", "voice_coil_current"}:
            ids = [c["id"] for c in components if c["kind"] == "electrodynamic_transducer"]
            expected = (
                "m/s" if q.quantity == "diaphragm_velocity" else "A",
                ("excitation", "transducer"),
                (count, len(ids)),
            )
            if (
                q.metadata.get("component_ids") != ids
                or (q.unit, q.axes, q.values.shape) != expected
            ):
                raise ValueError("Transducer units, axes, shape or identities differ from request")
        else:
            raise ValueError("Unsupported analytic reference output")
        if (q.unit, q.axes, q.values.shape) != expected:
            raise ValueError("Returned output units, axes or shape differ from request")


def validate_scoring_outcome(outcome, frequencies, convention, ports, specifications):
    """Scoring cannot accept a synthetic partial, reordered or malformed result."""
    if len(outcome.results) != len(frequencies):
        raise ValueError("Reference scoring requires every declared frequency")
    for row, frequency in zip(outcome.results, frequencies):
        if (
            type(row.freq_hz) not in (int, float)
            or not math.isfinite(row.freq_hz)
            or row.freq_hz != frequency
        ):
            raise ValueError("Reference scoring frequency order differs")
        if row.excitation_port_ids != tuple(ports):
            raise ValueError("Reference scoring excitation identities differ")
        if any(
            row.diagnostics.get(key) != value
            for key, value in {
                "phasor_convention": convention,
                "precision": "float64",
                "bem_backend": "cpu",
                "symmetry": "off",
            }.items()
        ):
            raise ValueError("Reference scoring diagnostics differ")
        quantities = {q.id: q for q in row.quantities}
        if len(quantities) != len(row.quantities) or set(quantities) != set(specifications):
            raise ValueError("Reference scoring quantity identities differ")
        for name, (kind, unit, axes, shape, component_ids) in specifications.items():
            q = quantities[name]
            if q.target_id is not None:
                raise ValueError("Untargeted analytic scoring has an unexpected target identity")
            if (
                (q.quantity, q.unit, q.axes, q.values.shape) != (kind, unit, axes, shape)
                or q.values.dtype != np.dtype("complex128")
                or not np.isfinite(q.values).all()
            ):
                raise ValueError("Reference scoring quantity units, axes, shape or values differ")
            if component_ids is not None and q.metadata.get("component_ids") != component_ids:
                raise ValueError("Reference scoring transducer identities differ")


def decode_reference_result(raw, request, index):
    options = request["solver_options"]
    result = decode_result_v2(
        raw,
        expected_phasor=options["phasor_convention"],
        expected_ports=request["excitation_port_ids"],
    )
    _verify_outputs(result, request)
    if (
        index >= len(request["frequencies_hz"])
        or result.freq_hz != request["frequencies_hz"][index]
    ):
        raise ValueError("Returned frequency order differs from request")
    if (
        result.diagnostics.get("bem_backend") != "cpu"
        or result.diagnostics.get("symmetry") != "off"
    ):
        raise ValueError("Returned backend/symmetry differs from request")
    if result.diagnostics.get("compiled_worker", {}).get("driver_mode") != "source":
        raise ValueError("Analytic qualification requires the selected source driver")
    provenance = result.diagnostics.get("engine_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("engine", {}).get("repository_revision") != expected_engine_revision()
        or provenance.get("engine", {}).get("repository_dirty") is not False
    ):
        raise ValueError("Returned provenance is not the clean WG fork pin")
    return result


def build_sphere_request(
    mesh_path, frequencies, points, *, phasor_convention, sound_speed_m_per_s, density_kg_per_m3
):
    from server.solver.beat_adapter.request import build_request, SourceBasis
    from server.solver.beat_adapter.observations import ObservationLayout
    from server.solver.beat_adapter.capabilities import FRAME

    layout = ObservationLayout(
        np.array([]), tuple(points), {name: np.asarray(rows) for name, rows in points.items()}
    )
    compiled = build_request(
        Path(mesh_path).read_text(),
        sources=[SourceBasis("sphere", 1, port_id="excitation:sphere")],
        channel_ports={"source": ["excitation:sphere"]},
        layout=layout,
        frame=FRAME,
        frequencies_hz=frequencies,
        precision="float64",
        density_kg_per_m3=density_kg_per_m3,
        sound_speed_m_per_s=sound_speed_m_per_s,
    )
    wire = deepcopy(compiled.wire)
    wire["solver_options"]["phasor_convention"] = phasor_convention
    wire["outputs"] = [o for o in wire["outputs"] if o["quantity"] == "exterior_pressure"] + [
        {
            "id": "radiation_impedance",
            "quantity": "radiation_impedance",
            "target_ids": [],
            "options": {},
        }
    ]
    return wire


class ReferenceSession:
    """Qualification adapter around WG-owned admission/staging, never a runtime."""

    def __init__(self, *, julia, backend="cpu", julia_threads=2, record_dir):
        if backend != "cpu":
            raise ValueError("Analytic references qualify CPU Float64 only")
        self.julia = str(julia)
        self.threads = julia_threads
        self.record_dir = Path(record_dir)
        self.manager = None
        self.index = 0

    def __enter__(self):
        source_text = os.environ.get("WG_BEAT_ENGINE_SRC", "").strip()
        if not source_text:
            raise ValueError("Analytic references require WG_BEAT_ENGINE_SRC at the clean fork pin")
        source = Path(source_text).resolve(strict=True)
        self.facts = verify_runtime(self.julia, "cpu", engine_source=source)
        if (
            self.facts["engine_revision"] != expected_engine_revision()
            or self.facts["engine_revision_status"] != "observed"
            or self.facts.get("engine_revision_source") != "installed_source_byte_match"
            or self.facts.get("artifact_kind") != "installed"
        ):
            raise ValueError("Analytic qualification requires observed clean WG fork pin")
        installed_script = Path(self.facts["solver_script"])
        source_script = source / "src/beat_engine/julia_local/coupled_solver.jl"
        source_bytes = source_script.read_bytes()
        if source_bytes != installed_script.read_bytes():
            raise ValueError("Selected source entry differs from verified installed engine")
        self.facts.update(
            installed_solver_script=str(installed_script),
            solver_script=str(source_script),
            selected_source_revision=self.facts["engine_revision"],
            selected_source_solver_sha256=hashlib.sha256(source_bytes).hexdigest(),
            numerical_qualification_kind="source; independently matched installed distribution",
            selected_driver_mode="source",
            selected_bundle_policy="disabled for source numerical qualification",
        )
        self.manager = WorkerManager(mode="child")
        return self

    def __exit__(self, *exc):
        if self.manager is not None:
            self.manager.shutdown()

    def solve(self, request):
        from beat_engine.beat_contract import worker as contract

        contract.validate_solve_request(request)
        options = request["solver_options"]
        if (
            options.get("bem_backend") != "cpu"
            or options.get("precision") != "float64"
            or options.get("symmetry") != "off"
        ):
            raise ValueError("Reference request must declare CPU Float64 full geometry")
        if options.get("phasor_convention") not in {POSITIVE_TIME, NEGATIVE_TIME}:
            raise ValueError("Reference request requires explicit phasor")
        outcome = SolveOutcome("interrupted")
        self.index += 1
        record = {
            "schema_version": 1,
            "kind": "analytic_reference",
            "pass_scope": "transport, result contract and native identity only; analytic report owns numerical verdict",
            "transport_passed": False,
            "expected_engine_revision": expected_engine_revision(),
            "request": deepcopy(request),
            "observations": self.facts,
            "terminal_events": [],
            "passed": False,
        }
        try:
            client = self.manager.get_worker(
                "cpu",
                julia_executable=self.facts["julia_executable"],
                julia_project=Path(self.facts["project"]),
                solver_script=Path(self.facts["solver_script"]),
                julia_threads=self.threads,
                compiled_request_policy=Path(__file__),
                environment={**os.environ, "BLAB_BEAT_ENGINE_BUNDLE": "0"},
            )
            with SolveSession() as session:
                session.submit(client, request, negotiate=contract.negotiate_submission)
                outcome.worker_info = deepcopy(client.worker_info or {})
                first_error = None
                for event in session.events():
                    kind = event.get("type")
                    if kind == "result" and first_error is None:
                        try:
                            result = decode_reference_result(
                                event.get("result"), request, len(outcome.results)
                            )
                            provenance = result.diagnostics["engine_provenance"]
                            outcome.results.append(result)
                            if provenance not in outcome.engine_runs:
                                outcome.engine_runs.append(deepcopy(provenance))
                        except Exception as exc:
                            first_error = exc
                            # Private local triage retains the exact offending wire and
                            # exception. Portable numerical reports retain only its type.
                            try:
                                self.record_dir.mkdir(parents=True, exist_ok=True)
                                fd, name = tempfile.mkstemp(
                                    prefix=f"failed-result-{self.index:02d}-",
                                    suffix=".json",
                                    dir=self.record_dir,
                                )
                                with os.fdopen(fd, "w") as stream:
                                    json.dump(
                                        {
                                            "raw_result": event.get("result"),
                                            "failure_type": type(exc).__name__,
                                            "failure_message": str(exc),
                                        },
                                        stream,
                                        allow_nan=True,
                                    )
                                record["private_failure_wire"] = Path(name).name
                            except Exception as capture_error:
                                record["private_capture_failure_type"] = type(
                                    capture_error
                                ).__name__
                            traceback.print_exc()
                            session.request_cancel()
                    elif kind != "result":
                        outcome.events.append(deepcopy(event))
                    if kind in {"completed", "failed", "cancelled"}:
                        outcome.status = kind
                        record["terminal_events"].append(deepcopy(event))
                if first_error is not None:
                    raise first_error
            terminal = record["terminal_events"]
            if (
                len(terminal) != 1
                or outcome.status != "completed"
                or terminal[0].get("solved_count") != len(request["frequencies_hz"])
                or len(outcome.results) != len(request["frequencies_hz"])
            ):
                raise ValueError("Reference solve lacks complete terminal/frequency evidence")
            record["results"] = outcome.results
            record["engine_runs"] = outcome.engine_runs
            record["transport_passed"] = True
            record["passed"] = True
            return outcome
        except Exception as exc:
            record["failure_type"] = type(exc).__name__
            raise ReferenceSolveError(str(exc), outcome) from exc
        finally:
            # Existing WG recorder owns canonical evidence hashing/publication.
            record["results"] = [
                {
                    "freq_hz": row.freq_hz,
                    "quantities": [{"id": q.id, "values": q.values} for q in row.quantities],
                    "diagnostics": row.diagnostics,
                }
                for row in outcome.results
            ]
            write_record(self.record_dir / f"attempt-{self.index:02d}.json", record)
