"""Unit fixtures exercise the artifact gate; they are never native upgrade proof."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from scripts import qualify_installer_artifact as gate
from server.tests.test_updates_signing import PUBLIC_HEX, SEED, _sign
from server.updates import manifest


def test_verifier(data, sig, tag):
    return manifest.verify_manifest(data, sig, tag, public_key_hex=PUBLIC_HEX)


# This is a helper, not a pytest test.
test_verifier.__test__ = False


def payload(root: Path, platform: str, version: str):
    layer = root / gate.payload_relative(platform)
    (layer / "app").mkdir(parents=True)
    (layer / "runtime" / "packages").mkdir(parents=True)
    app = {"schemaVersion": 1, "version": version, "commit": "a" * 40,
           "runtimeId": "runtime-test", "treeSha256": "b" * 64}
    runtime = {"schemaVersion": 1, "platform": platform, "runtimeId": "runtime-test", "python": "3.13.1"}
    (layer / "app" / "APP-MANIFEST.json").write_text(json.dumps(app))
    (layer / "runtime" / "RUNTIME-MANIFEST.json").write_text(json.dumps(runtime))
    (layer / "app" / "kept.py").write_bytes(b"new release module")
    (layer / "runtime" / "python").write_bytes(b"interpreter fixture")
    (root / "launcher").write_bytes(b"native launcher fixture")
    return layer


def archive(artifact, candidate):
    with tarfile.open(artifact, "w:gz") as target:
        target.add(candidate, arcname="waveguide-generator")
        extra = tarfile.TarInfo("install.sh")
        extra.size = 7
        target.addfile(extra, io.BytesIO(b"fixture"))


def signed(artifact, manifest_path, signature):
    data = manifest.build_manifest("v0.3.4-rc.2", [artifact])
    manifest_path.write_bytes(data)
    signature.write_bytes(_sign(SEED, data))


@pytest.fixture
def case(tmp_path):
    def build(platform="linux-x86_64"):
        data = tmp_path / "data"
        data.mkdir()
        (data / "cadlink.db").write_bytes(b"private data fixture")
        control = tmp_path / "addins-control"
        control.mkdir()
        (control / "WGLink-owner").write_bytes(b"developer-owned")
        installed = tmp_path / "installed"
        old = payload(installed, platform, "0.3.4-rc.1")
        (old / "app" / "removed.py").write_bytes(b"old module")
        (old / "runtime" / "packages" / "removed_pkg").mkdir()
        (old / "runtime" / "packages" / "removed_pkg" / "__init__.py").write_bytes(b"old runtime package")
        saved = gate.snapshot(data, {"developer-addins": control}, [], installed, platform)
        shutil.rmtree(installed)
        candidate = tmp_path / "golden"
        payload(candidate, platform, "0.3.4-rc.2")
        shutil.copytree(candidate, installed)
        suffix = {"linux-x86_64": "linux-x86_64.tar.gz", "macos-arm64": "macos-arm64.dmg", "windows-x86_64": "windows-x86_64-setup.exe"}[platform]
        artifact = tmp_path / f"Waveguide.Generator-0.3.4-rc.2-{suffix}"
        if platform == "linux-x86_64":
            archive(artifact, candidate)
        else:
            artifact.write_bytes(b"opaque native installer unit fixture")
        manifest_path, signature = tmp_path / "SHA256SUMS", tmp_path / "SHA256SUMS.sig"
        signed(artifact, manifest_path, signature)
        return dict(saved=saved, artifact=artifact, manifest=manifest_path, signature=signature,
                    tag="v0.3.4-rc.2", platform=platform, candidate=candidate, installed=installed,
                    extraction_method="unit fixture; no native extraction performed", from_version="0.3.4-rc.1",
                    removed_app=["removed.py"], removed_runtime=["packages/removed_pkg"],
                    require_removals=True, verifier=test_verifier)
    return build


@pytest.mark.parametrize("platform", gate.PLATFORMS)
def test_full_tree_equality_records_signed_identity_without_native_claim(case, platform):
    result = gate.verify(**case(platform))
    assert result["artifactComparisonPassed"]
    assert result["nativeQualificationComplete"] is False
    assert result["nativeScenariosOwed"]
    assert result["payload"]["identity"]["commit"] == "a" * 40
    assert result["beforeIdentity"]["version"] == "0.3.4-rc.1"
    assert result["removalProofComplete"]
    assert result["preserved"]["data"]["entryCount"] == 1
    assert result["outcome"]["recorded"] is False
    assert result["payload"]["binding"]["kind"] == ("mechanical-tar-payload" if platform == "linux-x86_64" else "owner-attested")


@pytest.mark.parametrize("which", ("artifact", "manifest", "signature"))
def test_corrupt_signed_inputs_fail(case, which):
    args = case()
    path = args[which]
    data = path.read_bytes()
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(ValueError, match="signature|checksum"):
        gate.verify(**args)


def test_unit_key_is_not_accepted_by_default_compiled_trust(case):
    args = case()
    args.pop("verifier")
    with pytest.raises(ValueError, match="signature"):
        gate.verify(**args)


@pytest.mark.parametrize("tag", ("v0.03.4", "v0.3.4-rc.02", "v0.3.4+build", "v0.3.4-rc.2-updates"))
def test_bad_or_companion_tag_cannot_verify(case, tag):
    args = case()
    args["tag"] = tag
    with pytest.raises(ValueError):
        gate.verify(**args)


@pytest.mark.parametrize("mutation", ("extra", "bytes", "removed-module", "removed-package"))
def test_installed_stale_or_changed_payload_fails(case, mutation):
    args = case()
    root = args["installed"]
    paths = {"extra": "runtime/stale.dll", "bytes": "app/kept.py", "removed-module": "app/removed.py", "removed-package": "runtime/packages/removed_pkg/stale.py"}
    path = root / paths[mutation]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unexpected installed bytes")
    with pytest.raises(gate.EvidenceError, match="installed payload differs"):
        gate.verify(**args)


def test_tampered_golden_and_installed_together_cannot_pass_linux_binding(case):
    args = case()
    for root in (args["candidate"], args["installed"]):
        (root / "app" / "kept.py").write_bytes(b"same tampering in both trees")
    with pytest.raises(gate.EvidenceError, match="Linux tar payload versus candidate differs"):
        gate.verify(**args)


def test_removal_fixture_must_have_existed_before(case):
    args = case()
    args["removed_app"] = ["invented.py"]
    with pytest.raises(gate.EvidenceError, match="not present before"):
        gate.verify(**args)


def test_bridge_verification_can_record_no_removal_proof(case):
    args = case()
    args.update(require_removals=False, removed_app=[], removed_runtime=[])
    assert gate.verify(**args)["removalProofComplete"] is False
    args["require_removals"] = True
    with pytest.raises(gate.EvidenceError, match="both removed"):
        gate.verify(**args)


def test_only_top_level_update_install_is_excluded(case):
    args = case()
    data = Path(args["saved"]["roots"]["data"]["path"])
    (data / "update-install").mkdir()
    (data / "update-install" / "download.part").write_bytes(b"permitted journal/download churn")
    assert gate.verify(**args)["artifactComparisonPassed"]
    (data / "user" / "update-install").mkdir(parents=True)
    (data / "user" / "update-install" / "project.db").write_bytes(b"must not be excluded")
    with pytest.raises(gate.EvidenceError, match="preserved data differs"):
        gate.verify(**args)


@pytest.mark.parametrize("name", ("data", "developer-addins"))
def test_data_and_external_ownership_controls_must_be_unchanged(case, name):
    args = case()
    root = Path(args["saved"]["roots"][name]["path"])
    next(root.iterdir()).write_bytes(b"unintended modification")
    with pytest.raises(gate.EvidenceError, match=f"preserved {name} differs"):
        gate.verify(**args)


def test_developer_control_records_literal_external_symlink_without_following(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    outside = tmp_path / "developer"
    outside.mkdir()
    (outside / "file.py").write_bytes(b"developer data")
    (control / "WGLink").symlink_to(outside, target_is_directory=True)
    assert gate.tree_entries(control, control=True) == {"WGLink": {"kind": "symlink", "target": str(outside)}}
    with pytest.raises(gate.EvidenceError, match="absolute symlink"):
        gate.tree_entries(control)


@pytest.mark.parametrize("kind", ("escape", "root", "parent"))
def test_payload_symlink_escape_and_nominated_symlink_roots_fail(case, tmp_path, kind):
    args = case()
    if kind == "escape":
        (args["installed"] / "app" / "escape").symlink_to("../../outside")
    elif kind == "root":
        link = tmp_path / "link"
        link.symlink_to(args["installed"], target_is_directory=True)
        args["installed"] = link
    else:
        (args["installed"] / "app" / "dir").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(gate.EvidenceError, match="symlink"):
        gate.verify(**args)


def test_symlink_targets_participate_in_exact_comparison(case):
    args = case("macos-arm64")
    for root in (args["candidate"], args["installed"]):
        (root / "Contents" / "Resources" / "app" / "alias").symlink_to("kept.py")
    assert gate.verify(**args)["artifactComparisonPassed"]
    (args["installed"] / "Contents" / "Resources" / "app" / "alias").unlink()
    (args["installed"] / "Contents" / "Resources" / "app" / "alias").symlink_to("APP-MANIFEST.json")
    with pytest.raises(gate.EvidenceError, match="installed payload differs"):
        gate.verify(**args)


def test_installed_generated_files_are_outside_explicit_app_runtime_scope(case):
    args = case("windows-x86_64")
    (args["installed"] / "unins000.exe").write_bytes(b"native installer-generated uninstaller")
    result = gate.verify(**args)
    assert result["payload"]["scope"] == "app-and-runtime"
    assert result["nativeQualificationComplete"] is False


def test_hardlinked_golden_is_not_independent(case):
    args = case()
    installed_file = args["installed"] / "app" / "kept.py"
    installed_file.unlink()
    os.link(args["candidate"] / "app" / "kept.py", installed_file)
    with pytest.raises(gate.EvidenceError, match="hardlinked"):
        gate.verify(**args)


@pytest.mark.parametrize("mutation", ("root", "entry", "before", "pids", "exclusion"))
def test_malformed_snapshot_shapes_refused(case, mutation):
    args = case()
    saved = args["saved"]
    if mutation == "root":
        saved["roots"]["data"] = []
    elif mutation == "entry":
        saved["roots"]["data"]["entries"]["cadlink.db"] = None
    elif mutation == "before":
        saved["beforeInstalled"] = None
    elif mutation == "pids":
        saved["stoppedPids"] = [{}]
    else:
        saved["dataExclusions"] = ["update-install", "cadlink.db"]
    with pytest.raises(gate.EvidenceError):
        gate.verify(**args)


@pytest.mark.parametrize("value", ([], None, "text", 3))
def test_non_object_json_refused_before_access(tmp_path, value):
    path = tmp_path / "object.json"
    path.write_text(json.dumps(value))
    with pytest.raises(gate.EvidenceError, match="must be an object"):
        gate.read_object(path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO input")
@pytest.mark.parametrize("kind", ("manifest", "signature", "artifact", "outcome", "server-lock", "snapshot", "runtime-manifest"))
def test_fifo_inputs_fail_without_opening_or_blocking(case, tmp_path, kind):
    args = case()
    if kind in ("manifest", "signature", "artifact"):
        path = args[kind]
    elif kind == "outcome":
        path = tmp_path / "outcome.json"
        args["outcome"] = path
    elif kind == "server-lock":
        path = Path(args["saved"]["roots"]["data"]["path"]) / "locks" / "server.pid"
        path.parent.mkdir()
    elif kind == "runtime-manifest":
        path = args["candidate"] / "runtime" / "RUNTIME-MANIFEST.json"
    else:
        path = tmp_path / "snapshot.json"
    if path.exists():
        path.unlink()
    os.mkfifo(path)
    with pytest.raises(gate.EvidenceError, match="regular file|special file"):
        if kind == "snapshot":
            gate.read_object(path)
        else:
            gate.verify(**args)


def test_running_server_pid_refused_before_snapshot(tmp_path):
    data = tmp_path / "data"
    (data / "locks").mkdir(parents=True)
    (data / "locks" / "server.pid").write_text(json.dumps({"pid": os.getpid()}))
    with pytest.raises(gate.EvidenceError, match="still running"):
        gate.stopped(data, [])


@pytest.mark.parametrize("value", ([], {"pid": True}, {"pid": "123"}))
def test_malformed_server_lock_refused(tmp_path, value):
    data = tmp_path / "data"
    (data / "locks").mkdir(parents=True)
    (data / "locks" / "server.pid").write_text(json.dumps(value))
    with pytest.raises(gate.EvidenceError):
        gate.stopped(data, [])


@pytest.mark.parametrize("result", ("ok", "installed", "failed", "rollback_incomplete"))
def test_outcome_records_raw_result_and_never_infers_retained_previous(case, tmp_path, result):
    args = case()
    path = tmp_path / "outcome.json"
    value = {"from": args["from_version"], "to": args["tag"][1:], "result": result,
             "when": "2026-10-02T12:00:00Z", "log": "private helper log"}
    path.write_text(json.dumps(value))
    args["outcome"] = path
    recorded = gate.verify(**args)["outcome"]
    assert recorded["raw"] == value
    assert "previousKept" not in recorded["raw"]


@pytest.mark.parametrize("value", ([], {"from": "different"}, {"from": "0.3.4-rc.1", "to": "0.3.4-rc.2", "result": "invented", "when": "now", "log": "log"}))
def test_malformed_or_foreign_outcome_refused(case, tmp_path, value):
    args = case()
    path = tmp_path / "outcome.json"
    path.write_text(json.dumps(value))
    args["outcome"] = path
    with pytest.raises(gate.EvidenceError):
        gate.verify(**args)


@pytest.mark.parametrize("kind", ("traversal", "absolute", "duplicate", "hardlink", "device", "link-child", "escape-link"))
def test_unsafe_tar_never_extracted(case, kind):
    args = case()
    with tarfile.open(args["artifact"], "w:gz") as target:
        directory = tarfile.TarInfo("waveguide-generator")
        directory.type = tarfile.DIRTYPE
        target.addfile(directory)
        member = tarfile.TarInfo({"traversal": "waveguide-generator/../escape", "absolute": "/escape"}.get(kind, "waveguide-generator/link"))
        if kind in ("link-child", "escape-link"):
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside" if kind == "escape-link" else "app"
        elif kind == "hardlink":
            member.type = tarfile.LNKTYPE
            member.linkname = "outside"
        elif kind == "device":
            member.type = tarfile.CHRTYPE
        target.addfile(member)
        if kind == "duplicate":
            target.addfile(member)
        elif kind == "link-child":
            target.addfile(tarfile.TarInfo("waveguide-generator/link/child"))
    with pytest.raises(gate.EvidenceError):
        gate.linux_archive_entries(args["artifact"])


def test_tree_mutation_during_hashing_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a").write_bytes(b"a")
    (root / "b").write_bytes(b"b")
    original = gate.hash_file

    def mutate(path):
        if path.name == "b":
            (root / "a").write_bytes(b"changed after a was hashed")
        return original(path)

    monkeypatch.setattr(gate, "hash_file", mutate)
    with pytest.raises(gate.EvidenceError, match="tree changed"):
        gate.tree_entries(root)


def test_junction_reparse_points_rejected_without_recursion():
    class FakeStat:
        st_mode = 0o040755
        st_file_attributes = getattr(gate.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)

    assert gate.junction(FakeStat())


def test_evidence_cannot_be_written_inside_observed_root_or_overwrite(tmp_path):
    root = tmp_path / "protected"
    root.mkdir()
    with pytest.raises(gate.EvidenceError, match="outside"):
        gate.write_new(root / "evidence.json", {}, [root])
    out = tmp_path / "evidence.json"
    gate.write_new(out, {}, [root])
    with pytest.raises(FileExistsError):
        gate.write_new(out, {"replacement": True}, [root])


def test_bounded_json_reads_and_cli_runs_without_site_packages(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * 1025)
    with pytest.raises(gate.EvidenceError, match="too large"):
        gate.read_object(path, 1024)
    result = subprocess.run([sys.executable, "-S", str(Path(gate.__file__)), "verify", "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--require-removals" in result.stdout
    assert "public-key" not in result.stdout
