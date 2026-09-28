"""The WGLink shipped at source.json is a live writer for this WG reader."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts import build_wglink_package
from server.cadlink import ingest, solve_command
from server.cadlink.ingest import IngestRefusal
from server.cadlink.fusion_delivery import capabilities, ipc_folder
from server.cadlink.store import CadLinkStore
from server.cadlink.wgreturn import read_wgreturn, source_physical_name, validate_manifest
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.tests.tools.oracle_geometry import geometry_result


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "server/tests/fixtures/cadlink-endpoint-oracle"
PIN = json.loads((ROOT / "integrations/wglink/source.json").read_text())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verdict(call) -> dict[str, object]:
    try:
        call()
    except Exception as exc:
        return {"accepted": False, "message": str(exc)}
    return {"accepted": True, "message": None}


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def packaged(tmp_path_factory: pytest.TempPathFactory):
    """Fetch an exact commit object, never a sibling working tree."""

    root = tmp_path_factory.mktemp("wglink-pinned")
    source = root / "source"
    source.mkdir()
    init = _run("git", "init", "-q", cwd=source)
    assert init.returncode == 0, init.stderr
    remote = os.environ.get("WGLINK_SOURCE_GIT") or PIN["repository"]
    fetched = _run(
        "git", "fetch", "--quiet", "--no-tags", "--depth", "1", remote, PIN["commit"], cwd=source
    )
    assert fetched.returncode == 0, (
        f"Pinned WGLink source {PIN['commit']} is unavailable from {remote}: {fetched.stderr}"
    )
    checkout = _run("git", "checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=source)
    assert checkout.returncode == 0, checkout.stderr
    assert _run("git", "rev-parse", "HEAD", cwd=source).stdout.strip() == PIN["commit"]
    archive = build_wglink_package.build_package(source, root / "wglink.zip")
    with zipfile.ZipFile(archive) as bundle:
        provenance = json.loads(bundle.read("wglink/provenance.json"))
        assert provenance["sourceCommit"] == PIN["commit"]
        assert set(bundle.namelist()) == set(provenance["files"]) | {"wglink/provenance.json"}
        for name, digest in provenance["files"].items():
            assert _sha(bundle.read(name)) == digest, name
        bundle.extractall(root / "extracted")
    folder = root / "extracted/wglink/fusion-addins/WGLink"
    return folder


@contextmanager
def _from_package(folder: Path, filename: str):
    name = "_wglink_pinned_" + filename.removesuffix(".py")
    path = folder / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    assert name not in sys.modules
    sys.modules[name] = module  # dataclasses needs the module while it executes
    try:
        spec.loader.exec_module(module)
        assert Path(module.__file__).resolve() == path.resolve()
        assert Path(module.__file__).resolve().is_relative_to(folder.resolve())
        yield module
    finally:
        assert sys.modules.pop(name) is module
        assert name not in sys.modules


def test_endpoint_oracle_bytes_and_messages(tmp_path: Path):
    provenance = json.loads((CORPUS / "PROVENANCE.json").read_text())
    assert provenance["wg_commit"] == "30b8104704a0f556391901804344facd73a31932"
    assert provenance["addin_commit"] == PIN["commit"]
    assert provenance["generator"] == "server/tests/tools/generate_cadlink_endpoint_oracle.py"
    actual = {
        p.relative_to(CORPUS).as_posix(): _sha(p.read_bytes())
        for p in CORPUS.rglob("*")
        if p.is_file() and p.name != "PROVENANCE.json"
    }
    assert actual == provenance["files"]
    oracle = json.loads((CORPUS / "ORACLE.json").read_text())
    bare = json.loads((CORPUS / "BARE.json").read_text())
    disk_setup = json.loads((CORPUS / "DISK_SETUP.json").read_text())
    assert len(oracle) == 40
    for name, expected in oracle.items():
        folder = CORPUS / f"{name}.wgreturn"
        manifest = (
            bare[name] if name in bare else json.loads((folder / "wgreturn.json").read_bytes())
        )
        assert _verdict(lambda: validate_manifest(manifest)) == expected["validate_manifest"], name
        disk_folder = folder
        if name in disk_setup:
            disk_folder = tmp_path / f"{name}.wgreturn"
            shutil.copytree(folder, disk_folder)
            (disk_folder / disk_setup[name]["link"]).symlink_to(disk_setup[name]["target"])
        assert _verdict(lambda: read_wgreturn(disk_folder)) == expected["read_wgreturn"], name


def test_oracle_rule_mutation_exits_nonzero(tmp_path: Path):
    """A rule removed in a fresh process must make the committed oracle red."""

    plugin = tmp_path / "oracle_mutant.py"
    plugin.write_text(
        "from server.cadlink import wgreturn\n"
        "def pytest_collection_modifyitems(items):\n"
        "    original = wgreturn._fail\n"
        "    def without_chirality(path, message):\n"
        "        if path.endswith('.chirality') and message == \"Phase 2 accepts only 'original'\":\n"
        "            return None\n"
        "        return original(path, message)\n"
        "    wgreturn._fail = without_chirality\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "server/tests/test_wglink_packaged_pair.py::test_endpoint_oracle_bytes_and_messages",
            "-q",
            "-p",
            "oracle_mutant",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "first-error-chirality" in result.stdout or "mirrored-chirality" in result.stdout


@pytest.mark.parametrize("name", sorted(json.loads((CORPUS / "GEOMETRY.json").read_text())))
def test_geometry_oracle_without_solver(name: str, tmp_path: Path):
    expected = json.loads((CORPUS / "GEOMETRY.json").read_text())[name]
    folder = CORPUS / f"{name}.wgreturn"
    manifest = json.loads((folder / "wgreturn.json").read_text())
    sizes = {
        "rigid_size_mm": 20,
        "transition_mm": 30,
        "source_size_mm": {
            source["id"]: 4 if name == "onshape-linked" else 8 for source in manifest["sources"]
        },
    }
    data = tmp_path / "data"
    data.mkdir()
    store = CadLinkStore(data / "cadlink.db")
    try:
        if not expected["accepted"]:
            with pytest.raises(IngestRefusal) as caught:
                _run_in_gmsh_session(
                    ingest.ingest_bundle,
                    folder,
                    sizes,
                    [],
                    store,
                    data,
                    prep_options={"symmetry_mode": "auto"},
                )
            assert str(caught.value) == expected["message"]
            return
        record = _run_in_gmsh_session(
            ingest.ingest_bundle,
            folder,
            sizes,
            [],
            store,
            data,
            prep_options={"symmetry_mode": "auto"},
        )
        assert geometry_result(record) == expected
    finally:
        store.close()


def test_pinned_writer_bundle_request_claim_and_ingest(packaged: Path, tmp_path: Path):
    with (
        _from_package(packaged, "wglink_return.py") as writer,
        _from_package(packaged, "wglink_watch.py") as watcher,
    ):
        fixture = CORPUS / "automatic-full.wgreturn"
        step = (fixture / "assembly.step").read_bytes()
        base = json.loads((fixture / "wgreturn.json").read_text())
        fields = deepcopy(base)
        fields["return_record"] = fields.pop("return")
        fields.pop("acoustics")
        fields["wgreturn_version"] = "1.1"
        fields["required_features"].remove("domain-automatic-v1")
        fields["assembly"].pop("domain")
        fields["assembly"]["signature_hash"] = "sha256:" + "4" * 64
        fields["scope"]["included"][0].update(component="Body1", external_reference="none")
        manifest = writer.build_return_manifest(**fields)
        workspace = tmp_path / "workspace"
        bundle = workspace / "wgreturn/pinned.wgreturn"
        bundle.mkdir(parents=True)
        (bundle / "assembly.step").write_bytes(step)
        (bundle / "wgreturn.json").write_text(
            writer.dumps_return_manifest(manifest), encoding="utf-8"
        )
        read = read_wgreturn(bundle)
        assert read.manifest["sources"][0]["id"] == "throat"
        assert read.degradations == ()
        assert source_physical_name(101, "throat", None, "HF") == (
            "wg-import-v1|tag=101|source_id=throat|instance_id=null|role=HF"
        )

        data = tmp_path / "data"
        data.mkdir()
        ipc = ipc_folder(data, create=True)
        (ipc / watcher.CAPABILITIES_FILENAME).write_text(
            json.dumps(capabilities()), encoding="utf-8"
        )
        request = watcher.write_solve_request(
            ipc,
            command_id="pinned-solve",
            return_id=manifest["return"]["id"],
            bundle_path=bundle,
            workspace_root=workspace,
            requested_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        )
        payload = json.loads(request.read_text())
        assert payload["schemaVersion"] == 3 and "kind" not in payload
        store = CadLinkStore.for_data_dir(data)
        try:
            refusals = []
            solve_command.collect_solve_deliveries(data, store, refuse=refusals.append)
            assert refusals == []
            operation = store.get_operation("pinned-solve")
            assert operation is not None and operation["kind"] == "prepare_and_solve"

            record = _run_in_gmsh_session(
                ingest.ingest_bundle,
                bundle,
                {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {"throat": 8}},
                [],
                store,
                data,
                prep_options={"symmetry_mode": "auto"},
            )
            assert [source["id"] for source in record["sources"]] == ["throat"]
            assert record["source_tags"] == {"throat": 101}
            assert record["tag_map"]["101"] == {
                "source_id": "throat",
                "instance_id": None,
                "role": "HF",
            }
            fixture_record = _run_in_gmsh_session(
                ingest.ingest_bundle,
                fixture,
                {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {"throat": 8}},
                [],
                store,
                data,
                prep_options={"symmetry_mode": "auto"},
            )
            assert record["mesh_content_sha256"] == fixture_record["mesh_content_sha256"]
        finally:
            store.close()

        oracle = json.loads((CORPUS / "ORACLE.json").read_text())
        tampered = step[:-1] + bytes([step[-1] ^ 1])
        (bundle / "assembly.step").write_bytes(tampered)
        assert _verdict(lambda: read_wgreturn(bundle)) == {
            "accepted": False,
            "message": "bundle member 'assembly.step' checksum mismatch: declared "
            f"sha256:{_sha(step)}, actual sha256:{_sha(tampered)}",
        }
        (bundle / "assembly.step").write_bytes(step)
        (bundle / "extra.txt").write_bytes(b"extra")
        assert (
            _verdict(lambda: read_wgreturn(bundle)) == oracle["undeclared-member"]["read_wgreturn"]
        )
        (bundle / "extra.txt").unlink()
        for name in ("major-2", "unknown-feature"):
            altered = deepcopy(manifest)
            if name == "major-2":
                altered["wgreturn_version"] = "2.0"
            else:
                altered["required_features"].append("future-physics-v1")
            (bundle / "wgreturn.json").write_text(json.dumps(altered), encoding="utf-8")
            assert _verdict(lambda: read_wgreturn(bundle)) == oracle[name]["read_wgreturn"]


def test_pinned_writer_negative_manifest_messages(packaged: Path):
    with _from_package(packaged, "wglink_return.py") as writer:
        base = json.loads((CORPUS / "base-1-1.wgreturn/wgreturn.json").read_text())
        for name in ("major-2", "unknown-feature"):
            altered = deepcopy(base)
            if name == "major-2":
                altered["wgreturn_version"] = "2.0"
            else:
                altered["required_features"].append("future-physics-v1")
            # WG's exact refusal remains independent of the writer's own
            # validation and is asserted by the committed endpoint oracle.
            assert (
                _verdict(lambda m=altered: validate_manifest(m))
                == json.loads((CORPUS / "ORACLE.json").read_text())[name]["validate_manifest"]
            )
        assert writer.__file__.endswith("wglink_return.py")


def test_oversized_step_refusal_is_exact(tmp_path: Path):
    bundle = tmp_path / "oversized.wgreturn"
    shutil.copytree(CORPUS / "base-1-1.wgreturn", bundle)
    size = 64 * 1024 * 1024 + 1
    with (bundle / "assembly.step").open("r+b") as member:
        member.truncate(size)
    manifest = json.loads((bundle / "wgreturn.json").read_text())
    manifest["files"]["assembly.step"].update(size_bytes=size, sha256="sha256:" + "0" * 64)
    (bundle / "wgreturn.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert _verdict(lambda: read_wgreturn(bundle)) == {
        "accepted": False,
        "message": "bundle member 'assembly.step' is 67,108,865 bytes, over the "
        "67,108,864 byte limit for one STEP input",
    }


def test_request_versions_and_exact_refusals(tmp_path: Path):
    assert capabilities()["solveCommandDelivery"] == 4
    assert capabilities()["fusionRequestDelivery"] == 3
    data = tmp_path / "data"
    data.mkdir()
    store = CadLinkStore.for_data_dir(data)
    folder = ipc_folder(data, create=True) / solve_command.SOLVE_REQUESTS_DIRECTORY
    folder.mkdir()
    base = {
        "target": "waveguide-generator",
        "commandId": "case",
        "operationId": "case",
        "returnId": "wgr_1",
        "bundlePath": "wgreturn/case.wgreturn",
        "manifestSha256": "sha256:" + "1" * 64,
        "requestedAt": "2026-09-28T00:00:00Z",
    }
    try:
        for schema in (1, 2, 3, 4):
            payload = {
                **base,
                "schemaVersion": schema,
                "commandId": f"case-{schema}",
                "operationId": f"case-{schema}",
            }
            if schema == 4:
                payload["kind"] = "prepare_and_solve"
            target = (
                ipc_folder(data) / solve_command.SOLVE_REQUEST_FILENAME
                if schema == 1
                else folder / f"case-{schema}.json"
            )
            target.write_text(json.dumps(payload))
        refusals = []
        for _ in range(4):
            solve_command.collect_solve_deliveries(data, store, refuse=refusals.append)
        assert store.get_operation("case-3")["kind"] == "prepare_and_solve"
        assert store.get_operation("case-4")["kind"] == "prepare_and_solve"
        assert refusals == []
        for schema in (1, 2):
            row = store.get_operation(f"case-{schema}")
            assert row["state"] == "rejected"
            assert json.loads(row["outcome_json"])["message"] == (
                "This solve request came from a WGLink add-in older than this Waveguide "
                "Generator, which it no longer accepts. Restart Fusion so it loads the WGLink "
                "that WG installed, then use Solve in WG again."
            )
        sent = {
            **base,
            "schemaVersion": 4,
            "kind": "receive_snapshot",
            "commandId": "case-send",
            "operationId": "case-send",
        }
        sent.pop("returnId")
        (folder / "case-send.json").write_text(json.dumps(sent))
        solve_command.collect_solve_deliveries(data, store, refuse=refusals.append)
        assert store.get_operation("case-send")["kind"] == "receive_snapshot"
        assert refusals == []
        bad = {
            **base,
            "schemaVersion": 3,
            "kind": "receive_snapshot",
            "commandId": "bad-kind",
            "operationId": "bad-kind",
        }
        (folder / "bad-kind.json").write_text(json.dumps(bad))
        refusals.clear()
        solve_command.collect_solve_deliveries(data, store, refuse=refusals.append)
        assert [r["reason"] for r in refusals] == [
            "A schema-3 request is a solve, and this one names another kind."
        ]
    finally:
        store.close()
