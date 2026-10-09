"""Installed legacy native solves using WG-generated BEAT pressure requests.

Run through the compute broker. This is an explicit qualification projection,
not production routing or a promise of full WG result compatibility.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import platform
import sys
import tempfile
import zipfile

import meshio
import numpy as np

from server.solver.beat_adapter.observations import build_observations
from server.solver.beat_adapter.request import SourceBasis, build_request


PACKAGES = {"metal": "hornlab_metal_bem", "bempp": "hornlab_bempp_bem"}
FREQUENCIES = [250.0, 100.0, 180.0]
POINTS = np.array([[0., 0., 0.], [100., 0., 0.], [0., 100., 0.], [0., 0., 100.]])
FACES = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
TAGS = np.array([11, 12, 13, 14])
TRANSLATION = np.array([.2, -.3, .4])
FRAME = {"origin": [0., 0., 0.], "axis": [0., 0., 1.],
         "u": [1., 0., 0.], "v": [0., 1., 0.]}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_installation(package: str, wheel: Path, root: Path, expected_sha256: str) -> dict:
    """Fail before any solve if imports or installed wheel bytes do not match."""
    root = root.resolve()
    digest = sha256(wheel.read_bytes())
    if digest != expected_sha256:
        raise ValueError("Wheel SHA256 differs from the expected candidate")
    module = importlib.import_module(package)
    if Path(module.__file__).resolve() != root / package / "__init__.py":
        raise ValueError("Solver was not imported from the expected installed root")
    matched = {}
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Unsafe wheel member")
            if name.endswith("/") or path.parts[0] != package:
                continue
            installed = (root / name).resolve()
            if not installed.is_relative_to(root) or not installed.is_file():
                raise ValueError(f"Installed wheel member is missing: {name}")
            data = archive.read(name)
            if installed.read_bytes() != data:
                raise ValueError(f"Installed wheel member differs: {name}")
            matched[name] = sha256(data)
    if f"{package}/compiled.py" not in matched:
        raise ValueError("Wheel does not contain the compiled-input adapter")
    # Reject a previously imported source-tree submodule, too.
    for name, member in tuple(sys.modules.items()):
        if name.startswith(package + ".") and getattr(member, "__file__", None):
            file = Path(member.__file__).resolve()
            if not file.is_relative_to(root / package):
                raise ValueError(f"Solver submodule came from another installation: {name}")
            relative = file.relative_to(root).as_posix()
            if matched.get(relative) != sha256(file.read_bytes()):
                raise ValueError(f"Solver submodule is not a verified wheel member: {name}")
    return {"wheel_sha256": digest, "package_files_sha256": matched,
            "module_file": str(Path(module.__file__).resolve())}


def verify_contract(root: Path, expected_sha256: str) -> dict:
    """Verify the expected pinned contract directory before native submission.

    The fingerprint is SHA256 of sorted compact JSON mapping every .py/.json
    resource name relative to beat_contract to its content SHA256.
    """
    root = root.resolve()
    contract = importlib.import_module("beat_engine.beat_contract")
    if Path(contract.__file__).resolve() != root / "__init__.py":
        raise ValueError("BEAT validator was not imported from the expected contract root")
    files = {}
    for file in sorted(root.rglob("*")):
        if file.is_file() and file.suffix in {".py", ".json"}:
            if not file.resolve().is_relative_to(root):
                raise ValueError("BEAT contract resource is outside the expected root")
            files[file.relative_to(root).as_posix()] = sha256(file.read_bytes())
    if not {"__init__.py", "mesh.py", "system-v1.schema.json"} <= files.keys():
        raise ValueError("BEAT contract resources are incomplete")
    digest = sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    if digest != expected_sha256:
        raise ValueError("BEAT contract fingerprint differs from the expected pin")
    for name, member in tuple(sys.modules.items()):
        if name.startswith("beat_engine.beat_contract.") and getattr(member, "__file__", None):
            file = Path(member.__file__).resolve()
            if (not file.is_relative_to(root)
                    or files.get(file.relative_to(root).as_posix()) != sha256(file.read_bytes())):
                raise ValueError("BEAT contract submodule is outside the verified contract")
    return {"root": str(root), "fingerprint_sha256": digest, "files_sha256": files}


def build_case(engine: str, motion: str, directory: Path):
    """Keep topology, SI scaling, WG observation order and signed source axes."""
    precision = "float32" if engine == "metal" else "float64"
    file = directory / "input-mm.msh"
    meshio.write(file, meshio.Mesh(POINTS + TRANSLATION * 1000., [("triangle", FACES)],
                                 cell_data={"gmsh:physical": [TAGS], "gmsh:geometrical": [TAGS]}),
                 file_format="gmsh22", binary=False)
    layout = build_observations(angle_range=(-90., 90., 3),
                                planes=("vertical", "horizontal"), sphere_grid=(3, 4),
                                origin_m=TRANSLATION, distance_m=2., precision=precision)
    sources = [SourceBasis("rear", 14, motion, [0., 0., -8.] if motion == "axial" else None, "p:rear"),
               SourceBasis("front", 11, motion, [0., 0., 9.] if motion == "axial" else None, "p:front")]
    built = build_request(file.read_text(), sources=sources,
                          channel_ports={"rear": ["p:rear"], "front": ["p:front"],
                                         "together": ["p:front", "p:rear"]},
                          layout=layout, frame=FRAME, frequencies_hz=FREQUENCIES,
                          engine_id="beat-metal" if engine == "metal" else "beat-cpu",
                          precision=precision, mesh_scale_to_m=.001, density_kg_per_m3=1.21)
    return built


def pressure_projection(built, engine: str) -> tuple[dict, dict]:
    """Explicitly request only the subset the legacy facade can honor."""
    wire = deepcopy(built.wire)
    omitted = [item["quantity"] for item in wire["outputs"] if item["quantity"] != "exterior_pressure"]
    wire["outputs"] = [item for item in wire["outputs"] if item["quantity"] == "exterior_pressure"]
    orders = {}
    if engine == "metal":
        # Old Metal uses its own fixed native rule. Do not claim it honored
        # the BEAT orders, or silently do this on a production request.
        for key in ("quadrature_order", "singular_order"):
            orders[key] = wire["solver_options"].pop(key)
    return wire, {"omitted_quantities": omitted, "omitted_beat_orders": orders}


def qualify_case(api, engine: str, motion: str, directory: Path) -> dict:
    built = build_case(engine, motion, directory)
    before = json.dumps(built.wire, sort_keys=True)
    # A complete WG production request must fail, rather than return a partial
    # result whose missing impedance/traces look like a successful WG solve.
    try:
        api.solve_compiled(built.wire)
    except ValueError:
        pass
    else:
        raise AssertionError("Legacy facade accepted an unsupported full WG request")
    wire, omissions = pressure_projection(built, engine)
    result = api.solve_compiled(wire)
    if json.dumps(built.wire, sort_keys=True) != before:
        raise AssertionError("Qualification mutated the production request")
    np.testing.assert_array_equal(result.frequencies_hz, FREQUENCIES)
    assert result.excitation_port_ids == ("p:rear", "p:front")
    assert result.time_convention == "exp(-i omega t)"
    assert list(result.pressure) == [output["id"] for output in wire["outputs"]]
    # Independent native configuration and SI mesh; do not use the compiled
    # facade's planner, writer or LoadedMesh to produce the control.
    scalar = np.float32 if engine == "metal" else np.float64
    points = ((POINTS + TRANSLATION * 1000.) * .001).astype(scalar)
    native_file = directory / "direct-si.msh"
    meshio.write(native_file, meshio.Mesh(points, [("triangle", FACES)],
                                         cell_data={"gmsh:physical": [TAGS], "gmsh:geometrical": [TAGS]}),
                 file_format="gmsh22", binary=False)
    observation_points = np.concatenate(list(built.layout.points_m.values()))
    config = dict(velocity_mode=api.VelocityMode.VELOCITY, source_motion=motion,
                  source_axes={11: (0., 0., 1.), 14: (0., 0., -1.)} if motion == "axial" else None,
                  air_density=1.21,
                  observation=api.ObservationConfig(planes=["comparison"],
                                                    custom_points={"comparison": observation_points},
                                                    angle_count=len(observation_points)),
                  frame_override=api.ObservationFrame(**{k: np.asarray(v) for k, v in FRAME.items()},
                                                       mouth_center=np.zeros(3), source_center=np.zeros(3)))
    if engine == "metal":
        config.update(speed_of_sound=343., dense_solve_dtype="float32",
                      metal_native_assembly_mode="corrected", mesh_merge_tol=0.)
        tolerance = 1e-6
    else:
        config.update(precision="double", assembly_backend="numba", require_closed_mesh=True,
                      slp_dlp_quadrature=4, slp_dlp_singular_quadrature=4, hyp_adlp_quadrature=4)
        tolerance = 1e-12
    max_error = 0.
    for column, tag in enumerate([14, 11]):
        direct = api.solve_frequencies(native_file, FREQUENCIES,
                                       api.SolveConfig(**config, velocity_sources={14: float(tag == 14),
                                                                                 11: float(tag == 11)}))
        actual = np.concatenate([result.pressure[output["id"]][:, column, :]
                                 for output in wire["outputs"]], axis=1)
        expected = direct.pressure_complex[:, 0, :]
        assert np.isfinite(actual).all() and np.any(np.abs(actual) > 0)
        np.testing.assert_allclose(actual, expected, rtol=tolerance, atol=1e-12)
        np.testing.assert_array_equal(direct.frequencies_hz, FREQUENCIES)
        max_error = max(max_error, float(np.max(np.abs(actual - expected))))
    return {"motion": motion, "passed": True, "frequencies_hz": FREQUENCIES,
            "excitation_port_ids": list(result.excitation_port_ids),
            "outputs": {name: list(array.shape) for name, array in result.pressure.items()},
            "wire_sha256": sha256(json.dumps(wire, sort_keys=True, allow_nan=False).encode()),
            "comparison_rtol": tolerance, "comparison_atol": 1e-12,
            "max_absolute_pressure_error": max_error, "projection": omissions,
            "full_wg_request_refused": True}


def qualify(engine: str, wheel: Path, installed_root: Path, expected_sha256: str,
            contract_root: Path, contract_sha256: str) -> dict:
    package = PACKAGES[engine]
    provenance = verify_installation(package, wheel, installed_root, expected_sha256)
    contract_provenance = verify_contract(contract_root, contract_sha256)
    api = importlib.import_module(package)
    helper = None
    if engine == "metal":
        runtime = importlib.import_module(package + ".metal").discover_native_runtime(run_smoke_test=True)
        if not runtime.available:
            raise RuntimeError("Installed Metal helper unavailable: " + "; ".join(runtime.unavailable_reasons))
        path = runtime.helper_executable_path.resolve()
        if not path.is_relative_to(installed_root.resolve() / package):
            raise ValueError("Native helper is outside the verified installed wheel")
        relative = path.relative_to(installed_root.resolve()).as_posix()
        digest = sha256(path.read_bytes())
        if provenance["package_files_sha256"].get(relative) != digest:
            raise ValueError("Native helper differs from verified wheel")
        helper = {"file": str(path), "sha256": digest, "source": runtime.helper_source}
    with tempfile.TemporaryDirectory(prefix="wg-legacy-beat-qualification-") as temporary:
        cases = [qualify_case(api, engine, motion, Path(temporary)) for motion in ("normal", "axial")]
    # Submodule provenance also covers imports performed lazily by native solves.
    verify_installation(package, wheel, installed_root, expected_sha256)
    verify_contract(contract_root, contract_sha256)
    return {"schema_version": 1, "passed": True, "engine": engine,
            "scope": "installed legacy wheels; WG-generated prescribed-source exterior pressure subset",
            "platform": platform.platform(), "python": platform.python_version(),
            "provenance": provenance, "native_helper": helper,
            "beat_contract": contract_provenance, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=PACKAGES, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--contract-root", type=Path, required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # Remove a stale success report before executing, including on validation failure.
    args.report.unlink(missing_ok=True)
    report = qualify(args.engine, args.wheel, args.installed_root, args.wheel_sha256,
                     args.contract_root, args.contract_sha256)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"{args.engine}: {len(report['cases'])} installed native cases passed")


if __name__ == "__main__":
    main()
