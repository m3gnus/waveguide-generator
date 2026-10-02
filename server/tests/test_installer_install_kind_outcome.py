"""Platform destinations and honest installer outcomes, without a live app."""

import json
import os
import plistlib

import pytest

from shared import release_assets as assets
from server.updates.install_kind import BUNDLE_ID, current_platform, probe_install
from server.updates.installer_outcome import read_outcome


def bundle(root, platform=assets.LINUX_PLATFORM):
    app = root / "app"
    app.mkdir(parents=True)
    runtime = root / "runtime"
    runtime.mkdir()
    (app / "APP-MANIFEST.json").write_text(json.dumps({"schemaVersion": 1, "version": "0.3.3", "runtimeId": "abcdef012345"}))
    (runtime / "RUNTIME-MANIFEST.json").write_text(json.dumps({"schemaVersion": 1, "runtimeId": "abcdef012345", "platform": platform}))
    return app


def probe(app, platform, **kwargs):
    return probe_install(app, "0.3.3", platform, environ={"WG2_BUNDLE": "1"}, **kwargs)


def test_windows_only_registered_exact_installation_is_supported(tmp_path):
    root = tmp_path / "installed"
    app = bundle(root, assets.WINDOWS_PLATFORM)
    assert probe(app, assets.WINDOWS_PLATFORM, registry_reader=lambda: str(root))["kind"] == "windows"
    for registered in (None, str(tmp_path / "other-install")):
        found = probe(app, assets.WINDOWS_PLATFORM, registry_reader=lambda: registered)
        assert found["kind"] == "portable" and found["updateSupported"] is False


def test_macos_identity_exact_destination_and_writable_parent(tmp_path):
    target = tmp_path / "Waveguide Generator.app"
    app = bundle(target / "Contents" / "Resources", assets.MACOS_PLATFORM)
    info = target / "Contents" / "Info.plist"
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": BUNDLE_ID}, handle)
    assert probe(app, assets.MACOS_PLATFORM, writable=lambda path: True)["installRoot"] == str(target)
    found = probe(app, assets.MACOS_PLATFORM, writable=lambda path: False)
    assert found["updateSupported"] is False and "read-only" in found["reason"]
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "foreign.app"}, handle)
    assert probe(app, assets.MACOS_PLATFORM)["updateSupported"] is False


def test_macos_translocation_is_notify_only(tmp_path):
    root = tmp_path / "AppTranslocation" / "x" / "Waveguide Generator.app" / "Contents" / "Resources"
    found = probe(bundle(root, assets.MACOS_PLATFORM), assets.MACOS_PLATFORM)
    assert found["updateSupported"] is False and "Translocation" in found["reason"]


def test_linux_exact_basename_and_manifest_identity(tmp_path):
    root = tmp_path / "waveguide-generator"
    app = bundle(root)
    found = probe(app, assets.LINUX_PLATFORM, writable=lambda path: True)
    assert found["kind"] == "linux" and found["installRoot"] == str(root)
    (root / "runtime" / "RUNTIME-MANIFEST.json").write_text(json.dumps({"schemaVersion": 1, "runtimeId": "012345abcdef"}))
    assert probe(app, assets.LINUX_PLATFORM)["updateSupported"] is False
    other = bundle(tmp_path / "custom-folder")
    assert probe(other, assets.LINUX_PLATFORM)["updateSupported"] is False


def test_source_unknown_architecture_and_linked_bundle_are_notify_only(tmp_path):
    app = bundle(tmp_path / "waveguide-generator")
    assert probe_install(app, "0.3.3", assets.LINUX_PLATFORM, environ={})["kind"] == "source"
    assert probe(app, None)["updateSupported"] is False
    linked = tmp_path / "linked" / "app"
    linked.parent.mkdir()
    linked.symlink_to(app)
    assert probe(linked, assets.LINUX_PLATFORM)["updateSupported"] is False
    assert current_platform("Linux", "aarch64") is None
    assert current_platform("Windows", "AMD64") == assets.WINDOWS_PLATFORM


@pytest.mark.parametrize("result,explicit_kept,expected", [("installed", True, False), ("failed", False, False), ("failed", True, True), ("rollback_incomplete", True, False)])
def test_outcomes_preserve_failure_truth_and_only_consume_valid_records(tmp_path, result, explicit_kept, expected):
    folder = tmp_path / "update-install"
    folder.mkdir()
    path = folder / "outcome.json"
    payload = {"from": "0.3.3", "to": "0.3.4", "result": result, "when": "2026-10-02T12:00:00Z", "log": "install.log", "previousKept": explicit_kept, "backupPath": "retained-backup", "journalPath": "separate-journal"}
    path.write_text(json.dumps(payload))
    outcome = read_outcome(tmp_path, consume=False)
    assert outcome["result"] == result and outcome["previousKept"] is expected
    assert outcome["backupPath"] == "retained-backup" and outcome["journalPath"] == "separate-journal" and path.exists()
    assert read_outcome(tmp_path) == outcome and not path.exists()


@pytest.mark.parametrize("payload", ["x", "{}", "[]", "x" * 20000, '{"result":"failed"}'])
def test_invalid_outcomes_are_preserved_for_diagnosis(tmp_path, payload):
    path = tmp_path / "update-install" / "outcome.json"
    path.parent.mkdir()
    path.write_text(payload)
    assert read_outcome(tmp_path) is None and path.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes")
def test_fifo_outcome_is_refused_without_opening_or_consuming_it(tmp_path):
    path = tmp_path / "update-install" / "outcome.json"
    path.parent.mkdir()
    os.mkfifo(path)
    assert read_outcome(tmp_path) is None and path.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes")
@pytest.mark.parametrize("part", ["app", "runtime", "plist"])
def test_fifo_installation_manifests_cannot_block_status(tmp_path, part):
    target = tmp_path / "Waveguide Generator.app"
    app = bundle(target / "Contents" / "Resources", assets.MACOS_PLATFORM)
    info = target / "Contents" / "Info.plist"
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": BUNDLE_ID}, handle)
    path = {"app": app / "APP-MANIFEST.json", "runtime": app.parent / "runtime" / "RUNTIME-MANIFEST.json", "plist": info}[part]
    path.unlink()
    os.mkfifo(path)
    assert probe(app, assets.MACOS_PLATFORM)["updateSupported"] is False


@pytest.mark.parametrize("value", [[], "text", True])
def test_nonobject_plist_is_notify_only_and_cannot_crash_status(tmp_path, value):
    target = tmp_path / "Waveguide Generator.app"
    app = bundle(target / "Contents" / "Resources", assets.MACOS_PLATFORM)
    with (target / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(value, handle)
    found = probe(app, assets.MACOS_PLATFORM)
    assert found["updateSupported"] is False and found["kind"] == "unsupported"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes")
def test_outcome_replaced_by_fifo_between_stat_and_open_cannot_block(tmp_path, monkeypatch):
    path = tmp_path / "update-install" / "outcome.json"
    path.parent.mkdir()
    path.write_text("{}")
    real_open = os.open
    def replace(candidate, flags, *args, **kwargs):
        if str(candidate) == str(path):
            path.unlink()
            os.mkfifo(path)
        return real_open(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", replace)
    assert read_outcome(tmp_path) is None and path.exists()
