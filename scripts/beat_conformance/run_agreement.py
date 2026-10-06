"""Run one meaningful CPU exterior comparison inside a compute-broker job."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np

from server.solver.beat_adapter.mesh import read_surface
from server.solver.beat_adapter.results import SweepResult

from .agreement import compare_results
from .cases import ConformanceCase
from .recorder import run_case, write_record
from .runners import FrozenExterior, hbb_runner, official_runner, output_directory, result_set, validate_frequency_axis


def hbb_pin() -> str:
    return json.loads((Path(__file__).resolve().parents[2] / "pins.json").read_text())["modules"]["hornlab-beat-bem"]["sha"]


def bind_engine_record(record: dict, path: Path) -> dict:
    """Freeze engine-only evidence separately from the final agreement verdict."""
    snapshot = deepcopy(record)
    snapshot["engine_status"] = snapshot.pop("status", "failed")
    snapshot["engine_qualified"] = snapshot.pop("qualified", False)
    write_record(path, snapshot)
    return snapshot


def metre_mesh(original: bytes, scale: float) -> bytes:
    """Create one shared metre artifact, preserving row IDs, tags and winding."""
    mesh = read_surface(original.decode(), scale_to_m=scale)
    nodes = [f"{i} {x:.17e} {y:.17e} {z:.17e}" for i, (x, y, z) in zip(mesh.node_ids, mesh.points_m)]
    faces = [f"{i} 2 2 {tag} {tag} " + " ".join(str(mesh.node_ids[n]) for n in face)
             for i, tag, face in zip(mesh.element_ids, mesh.tags, mesh.faces)]
    return ("\n".join(["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(nodes)), *nodes,
                        "$EndNodes", "$Elements", str(len(faces)), *faces, "$EndElements", ""])).encode()


def accept_pressure(native) -> dict:
    passed = bool(np.isfinite(native.pressure_complex).all() and np.any(np.abs(native.pressure_complex)))
    return {"passed": passed, "failures": [] if passed else ["Nonfinite or zero exterior pressure"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--mesh-scale", type=float, default=1.)
    parser.add_argument("--source-tag", type=int, default=2)
    parser.add_argument("--symmetry", choices=("full", "yz", "yz+xz"), default="full")
    parser.add_argument("--frequencies", required=True, help="Comma-separated explicit Hz axis")
    parser.add_argument("--frequency-step", type=float, required=True)
    parser.add_argument("--prominence-db", type=float, required=True)
    parser.add_argument("--expect-pressure-column", type=int, action="append", default=[])
    parser.add_argument("--precision", choices=("float64", "float32"), action="append", required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--julia", required=True)
    parser.add_argument("--engine-source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frequencies = tuple(float(f) for f in args.frequencies.split(","))
    validate_frequency_axis(frequencies)
    args.output_dir = output_directory(args.output_dir, engine_source=args.engine_source)
    pin = hbb_pin()
    original = args.mesh.read_bytes()
    mesh_bytes = metre_mesh(original, args.mesh_scale)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "surface.msh").write_bytes(mesh_bytes)
    all_passed = True
    for precision in args.precision:
        name = f"{args.symmetry}-{precision}"
        directory = args.output_dir / name
        inputs = FrozenExterior(mesh_bytes, frequencies,
                                precision=precision, symmetry=args.symmetry,
                                source_tag=args.source_tag, threads=args.threads)
        write_record(directory / "inputs.json", {"settings": inputs.settings(),
                     "original_mesh_sha256": hashlib.sha256(original).hexdigest(),
                     "mesh_sha256": hashlib.sha256(mesh_bytes).hexdigest(),
                     "frequencies_hz": inputs.frequencies_hz,
                     "frequency_step_hz": args.frequency_step, "prominence_db": args.prominence_db,
                     "expected_pressure_columns": args.expect_pressure_column})
        case = ConformanceCase(name, "Frozen same-mesh CPU exterior agreement", inputs.frequencies_hz,
                               precision=precision, min_solved_count=len(inputs.frequencies_hz),
                               make_request=inputs.compiled,
                               accept=accept_pressure)
        report = {"passed": False}
        record = None
        try:
            record = run_case(case, output_dir=directory, evidence_mode="real",
                              solve=official_runner(args.julia, threads=inputs.threads, engine_source=args.engine_source))
            engine_record = bind_engine_record(record, directory / "official-run.json")
            candidate = result_set(inputs, SweepResult(**record["result"]),
                                   revision=record["runtime"]["engine_revision"],
                                   recorder_record=engine_record, official=True)
            reference = hbb_runner(inputs, julia_executable=args.julia,
                                   expected_revision=pin, directory=directory)
            write_record(directory / "hbb.json", {"evidence_mode": "real", "engine_status": "passed",
                         "revision_source": "installed_vcs_metadata_attested", "result": asdict(reference) | {"mesh_bytes": None},
                         "mesh_sha256": hashlib.sha256(mesh_bytes).hexdigest()})
            write_record(directory / "official-result.json", asdict(candidate) | {"mesh_bytes": None})
            report = compare_results(reference, candidate, frequency_step_hz=args.frequency_step,
                                     resonance_prominence_db=args.prominence_db,
                                     expected_resonance_columns={"pressure_complex": tuple(args.expect_pressure_column)})
            report["limitations"].extend(["HBB revision correspondence remains installed VCS metadata attested",
                                          "HBB solve count is API-validated rows, not terminal events",
                                          "Power is the same sampled-sphere far-field estimate, not boundary flux",
                                          "One-shot workers; no startup/performance qualification"])
        except Exception as exc:
            report.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            write_record(directory / "agreement.json", report)
            if record is not None:
                record["engine_status"] = record.get("status", "failed")
                record["engine_qualified"] = record.get("qualified", False)
                record["status"] = "passed" if report["passed"] else "failed"
                record["qualified"] = bool(report["passed"] and record["engine_qualified"])
                record["comparison"] = {"ran": "resonances" in report, "passed": report["passed"],
                                        "record": "agreement.json", "record_sha256": report["record_sha256"]}
                write_record(directory / f"{name}.json", record)
        print(name, json.dumps(report, default=str), flush=True)
        all_passed &= report["passed"]
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
