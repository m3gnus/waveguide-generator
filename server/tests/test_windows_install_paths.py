"""Windows launcher spellings must preserve released installation identities."""

from dataclasses import replace
import ast
import hashlib
import json
import ntpath
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from launchers import apply_update, full_installer, update_lock
from launchers.statusapp.updater import (
    BundleUpdateRequest, UpdateHandoffError, consume_update_request, launch_bundle_update_handoff,
)
from server.tests.test_installer_install_kind_outcome import bundle, probe
from shared.release_assets import WINDOWS_PLATFORM, windows_setup_name


def startup_hook_ordinary_path():
    # Importing the embedded hook would execute native admission. Compile only
    # its string helper to ensure the pre-import copy stays in agreement.
    hook = Path(__file__).resolve().parents[2] / "launchers/windows/startup_hook.py"
    function = next(node for node in ast.parse(hook.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_ordinary_path")
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<startup hook helper>", "exec"), scope)
    return scope["_ordinary_path"]


@pytest.mark.parametrize("value,expected", [
    (r"\\?\c:\x", r"c:\x"),
    (r"\\?\unc\s\sh\x", r"\\s\sh\x"),
    (r"\\?\Volume{12345678-1234-1234-1234-123456789abc}\x", r"\\?\Volume{12345678-1234-1234-1234-123456789abc}\x"),
    ("\\\\?\\", "\\\\?\\"),
    (r"\\?\C:", r"\\?\C:"),
    (r"\\?\GLOBALROOT\Device\HarddiskVolume1\x", r"\\?\GLOBALROOT\Device\HarddiskVolume1\x"),
    ("\\\\?\\UNC\\", "\\\\?\\UNC\\"),
    (r"\\?\UNC\server", r"\\?\UNC\server"),
])
def test_windows_ordinary_path_strips_only_drive_and_unc_forms(monkeypatch, value, expected):
    monkeypatch.setattr(update_lock, "os", SimpleNamespace(name="nt"))
    assert update_lock.ordinary_path(value) == expected
    assert startup_hook_ordinary_path()(value) == expected


@pytest.mark.parametrize("plain,extended", [
    (r"C:\Program Files\Waveguide Generator", r"\\?\C:\Program Files\Waveguide Generator"),
    (r"\\server\share\Waveguide Generator", r"\\?\UNC\server\share\Waveguide Generator"),
    (r"\\server\share\Waveguide Generator", r"\\?\unc\server\share\Waveguide Generator"),
])
def test_windows_path_strings_and_keys_agree_everywhere(monkeypatch, plain, extended):
    # Model only string normalization, without changing pathlib's host flavour.
    windows_os = SimpleNamespace(name="nt", path=ntpath, fspath=os.fspath)
    monkeypatch.setattr(update_lock, "os", windows_os)
    monkeypatch.setattr(apply_update, "os", windows_os)
    assert update_lock.ordinary_path(extended) == plain
    assert update_lock.ordinary_path(plain) == plain
    assert startup_hook_ordinary_path()(extended) == plain
    assert apply_update.installation_key(Path(extended)) == apply_update.installation_key(Path(plain))
    assert update_lock.installation_key(extended) == update_lock.installation_key(plain)
    assert apply_update.journal_describes({"resources": plain}, Path(extended))
    assert apply_update.journal_describes({"resources": extended}, Path(plain))
    assert not apply_update.journal_describes({"resources": plain + "-other"}, Path(extended))


def test_released_plain_windows_key_is_unchanged(monkeypatch):
    # v0.3.2/v0.3.3 hashed normcase(normpath(root)), UTF-8/surrogatepass,
    # taking the first 16 SHA-256 hex digits. This synthetic path is fixed.
    monkeypatch.setattr(update_lock, "os", SimpleNamespace(name="nt", path=ntpath, fspath=os.fspath))
    monkeypatch.setattr(apply_update, "os", SimpleNamespace(name="nt", path=ntpath, fspath=os.fspath))
    plain = r"C:\Program Files\Waveguide Generator"
    expected = hashlib.sha256(ntpath.normcase(ntpath.normpath(plain)).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    assert expected == "bcdda84f6c67b81f"
    for root in (plain, "\\\\?\\" + plain):
        assert apply_update.installation_key(Path(root)) == expected


@pytest.mark.parametrize("value", ["/opt/waveguide-generator", r"\\?\C:\literal", r"\\?\UNC\literal"])
def test_posix_path_and_key_bytes_are_unchanged(monkeypatch, value):
    import posixpath

    monkeypatch.setattr(update_lock, "os", SimpleNamespace(name="posix", path=posixpath, fspath=os.fspath))
    monkeypatch.setattr(apply_update, "os", SimpleNamespace(name="posix", path=posixpath, fspath=os.fspath))
    assert update_lock.ordinary_path(value) == value
    expected = hashlib.sha256(posixpath.normcase(posixpath.normpath(str(Path(value)))).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    assert apply_update.installation_key(Path(value)) == expected
    physical = posixpath.realpath(value)
    expected_lock = hashlib.sha256(posixpath.normcase(posixpath.normpath(physical)).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    assert update_lock.installation_key(value) == expected_lock


windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows filesystem path semantics")


def extended(path):
    text = str(path)
    return Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)


@windows_only
@pytest.mark.parametrize("app_prefix,registry_prefix", [(True, False), (False, True), (True, True)])
def test_probe_registered_prefix_alias_reports_plain_root(tmp_path, app_prefix, registry_prefix):
    root = tmp_path / "installed"
    app = bundle(root, WINDOWS_PLATFORM)
    found = probe(extended(app) if app_prefix else app, WINDOWS_PLATFORM,
                  registry_reader=lambda: str(extended(root) if registry_prefix else root) + "\\")
    assert found == {"kind": "windows", "installRoot": str(root), "updateSupported": True, "reason": None}
    for registered in (None, str(tmp_path / "other-install"), str(extended(tmp_path / "other-install"))):
        found = probe(extended(app), WINDOWS_PLATFORM, registry_reader=lambda: registered)
        assert found["kind"] == "portable" and not found["updateSupported"]
        assert found["installRoot"] == str(root)


@windows_only
def test_prefixed_probe_still_refuses_symlinked_app(tmp_path):
    app = bundle(tmp_path / "installed", WINDOWS_PLATFORM)
    linked = tmp_path / "linked" / "app"
    linked.parent.mkdir()
    try:
        linked.symlink_to(app, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows privileges")
    found = probe(extended(linked), WINDOWS_PLATFORM, registry_reader=lambda: str(linked.parent))
    assert found["kind"] == "unsupported" and not found["updateSupported"]


@windows_only
def test_plain_bridge_marker_and_journal_accept_prefixed_installation(tmp_path):
    root, data = tmp_path / "installed", tmp_path / "data"
    root.mkdir()
    data.mkdir()
    apply_update.write_transaction_open_marker(data, root, "existing-bridge")
    marker = apply_update.read_transaction_open_marker(extended(root))
    assert marker["installation"] == apply_update.installation_key(root)
    assert marker["transaction"] == "existing-bridge" and "state" not in marker
    assert apply_update._open_marker_mismatch(marker, extended(data), extended(root)) is None
    marker["dataDir"] = ntpath.normcase(str(extended(data)))
    assert apply_update._open_marker_mismatch(marker, data, root) is None
    assert apply_update.journal_path(data, root) == apply_update.journal_path(data, extended(root))
    assert update_lock.installation_key(root) == update_lock.installation_key(extended(root))


@windows_only
@pytest.mark.parametrize("root_prefix,app_prefix", [(False, True), (True, False), (True, True)])
def test_full_installer_prefix_aliases_compare_and_pass_plain_dir(tmp_path, monkeypatch, root_prefix, app_prefix):
    app = tmp_path / "installed" / "app"
    app.mkdir(parents=True)
    data = tmp_path / "data"
    folder = data / "update-install" / "0.3.4"
    folder.mkdir(parents=True)
    asset = folder / windows_setup_name("0.3.4")
    asset.write_bytes(b"verified installer")
    request_path = tmp_path / "request.json"
    payload = {"kind": "full_installer", "schemaVersion": 2, "version": "0.3.4",
               "fromVersion": "0.3.3", "tag": "v0.3.4", "platform": WINDOWS_PLATFORM,
               "installer": str(extended(asset)), "installRoot": str(extended(app.parent) if root_prefix else app.parent),
               "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(), "size": asset.stat().st_size,
               "readyAtEpoch": 0, "expiresAtEpoch": 999999999999, "approval": 1}
    request_path.write_text(json.dumps(payload))
    own_app = extended(app) if app_prefix else app
    handled, request = full_installer.consume_full_installer_request(
        request_path, repo_root=own_app, data_dir=extended(data), now=1)
    assert handled and request.install_root == app.parent and request.installer == asset
    assert not request_path.exists()
    calls = []
    monkeypatch.setattr(full_installer.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)))
    # Also exercise detachment when a caller directly supplies the prefix.
    full_installer.launch_full_installer(own_app, replace(request, install_root=extended(app.parent)), 123,
                                        data_dir=extended(data))
    command, options = calls.pop()
    assert f"/DIR={app.parent}" in command
    assert not any("\\\\?\\" in arg for arg in command)
    assert options["cwd"] == data / "update-install"
    other = tmp_path / "other-install"
    other.mkdir()
    with pytest.raises(UpdateHandoffError, match="destination changed"):
        full_installer.launch_full_installer(own_app, replace(request, install_root=extended(other)), 123, data_dir=data)
    payload["installRoot"] = str(extended(other))
    request_path.write_text(json.dumps(payload))
    with pytest.raises(UpdateHandoffError, match="different destination"):
        full_installer.consume_full_installer_request(request_path, repo_root=own_app, data_dir=data, now=1)
    assert not calls


@windows_only
def test_bundle_handoff_accepts_mixed_prefixes_without_allowing_escape(tmp_path, monkeypatch):
    data = tmp_path / "data"
    app = data / "updates" / "0.3.4" / "staged" / "app"
    app.mkdir(parents=True)
    request = tmp_path / "request.json"
    payload = {"schemaVersion": 1, "kind": "apply_bundle", "version": "0.3.4",
               "stagedAppDir": str(extended(app)), "stagedRuntimeDir": None}
    request.write_text(json.dumps(payload))
    assert consume_update_request(request, data_dir=data).staged_app_dir == app
    payload["stagedAppDir"] = str(app)
    request.write_text(json.dumps(payload))
    assert consume_update_request(request, data_dir=extended(data)).staged_app_dir == app
    own_app = tmp_path / "installed" / "app"
    own_app.mkdir(parents=True)
    (app / "launchers").mkdir()
    (app / "launchers" / "apply_update.py").write_text("# staged helper")
    calls = []
    monkeypatch.setattr(full_installer.subprocess, "Popen", lambda command, **kwargs: calls.append(command))
    launch_bundle_update_handoff(extended(own_app), BundleUpdateRequest("0.3.4", extended(app), None), 123,
                                 environ={"WG2_DATA_DIR": str(extended(data))}, platform_name="win32")
    assert not any("\\\\?\\" in argument for argument in calls.pop())
    outside = tmp_path / "outside"
    outside.mkdir()
    payload["stagedAppDir"] = str(extended(outside))
    request.write_text(json.dumps(payload))
    with pytest.raises(UpdateHandoffError):
        consume_update_request(request, data_dir=data)


@windows_only
def test_staging_reclamation_compares_plain_and_prefixed_roots(tmp_path):
    root, data = tmp_path / "installed", tmp_path / "data"
    root.mkdir()
    staged = data / "updates" / "0.3.4"
    staged.mkdir(parents=True)
    journal = {"transaction": "bridge", "operation": "update",
               "layers": [{"staged": str(staged / "staged" / "app")}]}
    assert apply_update.write_completion_record(data, root, journal, outcome="installed", detail="test")
    assert apply_update.reclaim_committed_staging(extended(data), extended(root), bundle=extended(root)) == [staged]
    assert not staged.exists()
