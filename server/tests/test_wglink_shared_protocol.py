"""Vendored protocol provenance and the add-in development pair."""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from server.cadlink import fusion_status, ingest, operations, step_evidence, wglink_protocol
from server.cadlink.fusion_delivery import capabilities, ipc_folder
from server.cadlink.store import CadLinkStore
from server.cadlink import solve_command
from server.cadlink.wgreturn import read_wgreturn, validate_manifest
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.tests.test_wglink_packaged_pair import CORPUS, _from_package, _verdict

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "server/tests/fixtures/wglink-shared-protocol"
VENDORED = ROOT / "server/cadlink/wglink_protocol.py"
SOURCE = json.loads((ROOT / "server/cadlink/wglink_protocol.source.json").read_text())


def _fetch_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    remote = os.environ.get("WGLINK_SOURCE_GIT") or SOURCE["repository"]
    result = subprocess.run(
        ["git", "fetch", "--quiet", "--no-tags", "--depth", "1", remote, SOURCE["commit"]],
        cwd=source, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Shared WGLink source {SOURCE['commit']} is unavailable from {remote}: {result.stderr}"
    subprocess.run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=source, check=True)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() == SOURCE["commit"]
    return source


@pytest.fixture(scope="module")
def addin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _fetch_source(tmp_path_factory.mktemp("wglink-shared")) / "fusion-addins/WGLink"


def test_vendor_bytes_and_shared_fixtures(addin: Path):
    expected = VENDORED.resolve()
    assert Path(wglink_protocol.__file__).resolve() == expected
    data = VENDORED.read_bytes()
    assert hashlib.sha256(data).hexdigest() == SOURCE["sha256"]
    assert data == (addin / "wglink_protocol.py").read_bytes()
    manifest = {}
    for row in (SHARED / "MANIFEST.sha256").read_text().splitlines():
        digest, name = row.split("  ", 1)
        manifest[name] = digest
    assert set(manifest) == {p.name for p in SHARED.iterdir() if p.name != "MANIFEST.sha256"}
    for name, digest in manifest.items():
        assert hashlib.sha256((SHARED / name).read_bytes()).hexdigest() == digest
        assert (SHARED / name).read_bytes() == (addin.parents[1] / "tests/fixtures/wgreturn-endpoint-oracle" / name).read_bytes()


def test_shared_ingress_oracle():
    for case in json.loads((SHARED / "wg_ingress_cases.json").read_text()):
        assert _verdict(lambda: validate_manifest(case["manifest"])) == case["expected"], case["name"]


def test_production_hash_profiles_against_addin_goldens():
    for row in json.loads((SHARED / "goldens.json").read_text()):
        name = row["name"]
        value = {"nan": {"x": float("nan")}, "positive_infinity": {"x": float("inf")}, "negative_infinity": {"x": -float("inf")}}.get(name)
        if value is None:
            value = json.loads(row["core_json"]["value"])
        expected = row["core_json"]
        if "error_type" in expected:
            with pytest.raises(ValueError, match="Out of range float values"):
                step_evidence._canonical(value)
            with pytest.raises(ValueError, match="Out of range float values"):
                ingest._canonical(value)
        else:
            assert step_evidence._canonical(value).hex() == expected["canonical_bytes_hex"]
            assert ingest._canonical(value).hex() == expected["canonical_bytes_hex"]
            assert hashlib.sha256(step_evidence._canonical(value)).hexdigest() == expected["sha256"]
        if name in ("non_ascii", "negative_zero", "large_integer", "nested"):
            assert operations.canonical_json(value) == wglink_protocol.canonical_json(value, wglink_protocol.UTF8_STRICT)


def test_observation_fingerprint_equality(addin: Path):
    source = ast.parse((addin / "WGLink.py").read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "_fingerprint_hash")
    namespace = {"hashlib": hashlib, "json": json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(addin / "WGLink.py"), "exec"), namespace)
    addin_hash = namespace["_fingerprint_hash"]
    api_tree = ast.parse((ROOT / "server/cadlink/api.py").read_text())
    observed_dump = next(
        node for node in ast.walk(api_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps" and node.args
        and isinstance(node.args[0], ast.Name) and node.args[0].id == "observed"
    )
    api_json = compile(ast.Expression(observed_dump), str(ROOT / "server/cadlink/api.py"), "eval")
    for value in ({"é": "雪", "x": -0.0}, {"x": float("nan")}, {"n": 2**100}):
        assert fusion_status._fingerprint_hash(value) == addin_hash(value)
        if isinstance(value.get("x"), float) and value["x"] != value["x"]:
            with pytest.raises(ValueError):
                eval(api_json, {"json": json, "observed": value})
        else:
            actual = "sha256:" + hashlib.sha256(eval(api_json, {"json": json, "observed": value}).encode("utf-8")).hexdigest()
            assert actual == addin_hash(value)


def test_development_writer_reader_claim_and_ingest(addin: Path, tmp_path: Path):
    with _from_package(addin, "wglink_return.py") as writer, _from_package(addin, "wglink_watch.py") as watcher:
        data = tmp_path / "data"
        data.mkdir()
        ipc = ipc_folder(data, create=True)
        (ipc / "wg-capabilities.json").write_text(json.dumps(capabilities()))
        store = CadLinkStore.for_data_dir(data)
        try:
            for name in ("automatic-full", "automatic-half-as-shown", "automatic-quarter"):
                fixture = CORPUS / f"{name}.wgreturn"
                fields = deepcopy(json.loads((fixture / "wgreturn.json").read_text()))
                fields["return_record"] = fields.pop("return")
                fields.pop("acoustics")
                for body in fields["scope"]["included"]:
                    body.update(component="Body1", external_reference="none")
                manifest = writer.build_return_manifest(**fields)
                bundle = tmp_path / "wgreturn" / f"{name}.wgreturn"
                bundle.mkdir(parents=True)
                shutil.copy2(fixture / "assembly.step", bundle / "assembly.step")
                (bundle / "wgreturn.json").write_text(writer.dumps_return_manifest(manifest))
                assert _verdict(lambda: read_wgreturn(bundle)) == json.loads((CORPUS / "ORACLE.json").read_text())[name]["read_wgreturn"]
                assert [item["id"] for item in read_wgreturn(bundle).manifest["sources"]] == [item["id"] for item in fields["sources"]]
                for kind in ("receive_snapshot", "prepare_and_solve"):
                    command_id = f"dev-{name}-{kind}"
                    request = watcher.write_wg_request(
                        ipc, kind=kind, command_id=command_id,
                        return_id=manifest["return"]["id"] if kind == "prepare_and_solve" else None,
                        bundle_relative=f"wgreturn/{name}.wgreturn",
                        manifest_sha256="sha256:" + hashlib.sha256((bundle / "wgreturn.json").read_bytes()).hexdigest(),
                        requested_at="2026-09-28T00:00:00Z",
                    )
                    payload = json.loads(request.read_text())
                    assert payload["schemaVersion"] == 4 and payload["kind"] == kind
                    refusals = []
                    solve_command.collect_solve_deliveries(data, store, refuse=refusals.append)
                    assert refusals == []
                    assert store.get_operation(command_id)["kind"] == kind
                if name == "automatic-full":
                    record = _run_in_gmsh_session(
                        ingest.ingest_bundle, bundle,
                        {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {"throat": 8}},
                        [], store, data, prep_options={"symmetry_mode": "auto"},
                    )
                    assert record["source_tags"] == {"throat": 101}
                    assert record["tag_map"]["101"]["role"] == "HF"
        finally:
            store.close()
