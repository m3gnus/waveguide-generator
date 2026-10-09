"""Refuse misleading installed provenance and preserve production requests."""

from copy import deepcopy
import hashlib
import sys
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest

from scripts.beat_conformance import legacy_compiled_inputs as qualification


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    package = "qualification_fake_solver"
    files = {f"{package}/__init__.py": b"# facade\n", f"{package}/compiled.py": b"# compiled\n",
             f"{package}/native/helper": b"binary"}
    wheel = tmp_path / "candidate.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    module = SimpleNamespace(__file__=str(root / package / "__init__.py"))
    monkeypatch.setitem(sys.modules, package, module)
    return package, wheel, root, hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_installed_wheel_provenance_covers_native_assets(installation):
    facts = qualification.verify_installation(*installation)
    package = installation[0]
    assert len(facts["package_files_sha256"]) == 3
    assert facts["package_files_sha256"][f"{package}/native/helper"] == qualification.sha256(b"binary")


def test_wrong_candidate_hash_fails_before_import(installation, monkeypatch):
    package, wheel, root, digest = installation
    monkeypatch.delitem(sys.modules, package)
    with pytest.raises(ValueError, match="SHA256"):
        qualification.verify_installation(package, wheel, root, "0" * 64)


def test_source_tree_import_is_refused(installation, monkeypatch, tmp_path):
    package, wheel, root, digest = installation
    monkeypatch.setitem(sys.modules, package, SimpleNamespace(__file__=str(tmp_path / "source/__init__.py")))
    with pytest.raises(ValueError, match="imported"):
        qualification.verify_installation(package, wheel, root, digest)


@pytest.mark.parametrize("action", ["modify", "delete", "symlink"])
def test_changed_or_missing_installed_native_asset_is_refused(installation, tmp_path, action):
    package, wheel, root, digest = installation
    asset = root / package / "native/helper"
    if action == "modify":
        asset.write_bytes(b"wrong binary")
    else:
        asset.unlink()
        if action == "symlink":
            foreign = tmp_path / "foreign"
            foreign.write_bytes(b"binary")
            asset.symlink_to(foreign)
    with pytest.raises(ValueError, match="member"):
        qualification.verify_installation(package, wheel, root, digest)


def test_foreign_preimported_submodule_is_refused(installation, monkeypatch, tmp_path):
    package, wheel, root, digest = installation
    monkeypatch.setitem(sys.modules, package + ".native", SimpleNamespace(__file__=str(tmp_path / "foreign.py")))
    with pytest.raises(ValueError, match="submodule"):
        qualification.verify_installation(package, wheel, root, digest)


def test_stale_in_root_submodule_is_refused(installation, monkeypatch):
    package, wheel, root, digest = installation
    stale = root / package / "compiled/__init__.py"
    stale.parent.mkdir()
    stale.write_text("# shadows the verified compiled.py\n")
    monkeypatch.setitem(sys.modules, package + ".compiled", SimpleNamespace(__file__=str(stale)))
    with pytest.raises(ValueError, match="not a verified wheel member"):
        qualification.verify_installation(package, wheel, root, digest)


@pytest.fixture
def contract(tmp_path, monkeypatch):
    root = tmp_path / "beat_contract"
    root.mkdir()
    files = {"__init__.py": b"# validator", "mesh.py": b"# mesh", "system-v1.schema.json": b"{}"}
    for name, content in files.items():
        (root / name).write_bytes(content)
    monkeypatch.setitem(sys.modules, "beat_engine.beat_contract", SimpleNamespace(__file__=str(root / "__init__.py")))
    import json
    hashes = {name: qualification.sha256(content) for name, content in files.items()}
    digest = qualification.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())
    return root, digest


def test_expected_contract_fingerprint_passes(contract):
    assert qualification.verify_contract(*contract)["fingerprint_sha256"] == contract[1]


@pytest.mark.parametrize("file", ["__init__.py", "mesh.py", "system-v1.schema.json"])
def test_changed_validator_or_schema_is_refused_before_solves(contract, file):
    root, digest = contract
    (root / file).write_text("changed")
    with pytest.raises(ValueError, match="fingerprint"):
        qualification.verify_contract(root, digest)


def test_foreign_contract_import_is_refused(contract, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "beat_engine.beat_contract", SimpleNamespace(__file__=str(tmp_path / "foreign.py")))
    with pytest.raises(ValueError, match="contract root"):
        qualification.verify_contract(*contract)


@pytest.mark.parametrize("name", ["../foreign.py", "/absolute.py", "bad\\name"])
def test_unsafe_wheel_member_is_refused(installation, name):
    package, wheel, root, digest = installation
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(name, b"foreign")
    with pytest.raises(ValueError, match="Unsafe"):
        qualification.verify_installation(package, wheel, root, qualification.sha256(wheel.read_bytes()))


def test_old_wheel_without_adapter_is_refused(installation):
    package, wheel, root, digest = installation
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{package}/__init__.py", (root / package / "__init__.py").read_bytes())
    with pytest.raises(ValueError, match="compiled-input"):
        qualification.verify_installation(package, wheel, root, qualification.sha256(wheel.read_bytes()))


@pytest.mark.parametrize("engine", ["metal", "bempp"])
@pytest.mark.parametrize("motion", ["normal", "axial"])
def test_wg_projection_preserves_topology_ports_observations_and_production_request(tmp_path, engine, motion):
    built = qualification.build_case(engine, motion, tmp_path)
    before = deepcopy(built.wire)
    wire, omissions = qualification.pressure_projection(built, engine)
    assert built.wire == before
    assert wire["compiled_system"] == before["compiled_system"]
    assert wire["excitation_port_ids"] == ["p:rear", "p:front"]
    assert wire["frequencies_hz"] == [250., 100., 180.]
    assert [o["id"] for o in wire["outputs"]] == ["pressure:vertical", "pressure:horizontal", "pressure:sphere"]
    assert [len(o["options"]["points_m"]) for o in wire["outputs"]] == [3, 3, 12]
    assert omissions["omitted_quantities"] == ["radiation_impedance", "bem_boundary_pressure"]
    assert omissions["omitted_beat_orders"] == ({"quadrature_order": 4, "singular_order": 4} if engine == "metal" else {})
    expected = ((qualification.POINTS + qualification.TRANSLATION * 1000.) * .001).astype(
        np.float32 if engine == "metal" else np.float64)
    np.testing.assert_array_equal(built.mesh.points_m, expected)
    if motion == "axial":
        assert [c["parameters"]["motion_axis"] for c in wire["compiled_system"]["components"]] == [[0., 0., -1.], [0., 0., 1.]]
        # Every axial test must actually exercise motion: no tangential source.
        triangles = built.mesh.points_m[built.mesh.faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        for source in built.bases:
            assert np.any(np.abs(normals[built.mesh.tags == source.tag] @ source.axis) > 0)
    assert before["solver_options"]["quadrature_order"] == 4


def test_cli_removes_stale_success_before_failure(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    report.write_text('{"passed": true}')
    monkeypatch.setattr(sys, "argv", ["qualify", "--engine", "metal", "--wheel", "missing.whl",
                                     "--wheel-sha256", "0" * 64, "--installed-root", "missing",
                                     "--contract-root", "missing", "--contract-sha256", "0" * 64,
                                     "--report", str(report)])
    with pytest.raises(FileNotFoundError):
        qualification.main()
    assert not report.exists()
