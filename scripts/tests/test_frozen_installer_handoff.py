"""Released 0.3.4/0.3.5 handoff code still drives this build's native installers.

An installed client starts the new installer with its own code: on Windows the
command line in ``launchers/full_installer.py``, on macOS and Linux its own
``launchers/installer-helper.sh``. Both are read from the release tags.
"""
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys

import pytest

from server.tests._frozen_installers import ROOT, extract_release

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX helper")


def _windows_command(source: str) -> list[str]:
    """The literal switches of the released Windows installer command line."""
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.List)
                and any(isinstance(target, ast.Name) and target.id == "command" for target in node.targets)):
            elements = node.value.elts
            if any(isinstance(item, ast.Constant) and item.value == "/VERYSILENT" for item in elements):
                switches = []
                for item in elements[1:]:
                    if isinstance(item, ast.Constant):
                        switches.append(item.value)
                    else:
                        assert isinstance(item, ast.JoinedStr), ast.dump(item)
                        switches.append(item.values[0].value)
                return switches
    raise AssertionError("no Windows installer command line found")


@pytest.mark.parametrize("tag", ["v0.3.4", "v0.3.5"])
def test_released_windows_command_line_is_understood_by_current_setup(tag):
    released = subprocess.run(["git", "show", f"{tag}:launchers/full_installer.py"], cwd=ROOT,
                              capture_output=True, text=True, check=False)
    if released.returncode:
        from server.tests._release_tags import skip_or_fail_missing_tag
        skip_or_fail_missing_tag(f"Cannot read {tag}: {released.stderr}")
    switches = _windows_command(released.stdout)
    assert switches == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=", "/WAITPID=",
                        "/OUTCOME=", "/WGLOG=", "/RELAUNCH"]
    setup = (ROOT / "installers/windows/bundle-setup.iss").read_text(encoding="utf-8")
    # /VERYSILENT, /SUPPRESSMSGBOXES, /NORESTART and /DIR= are Inno's own; the
    # rest are this script's, and each must still be read.
    for name in ("WAITPID", "OUTCOME", "WGLOG"):
        assert re.search(r"\{param:" + name + r"\|", setup), name
    assert "CompareText(ParamStr(I), '/RELAUNCH') = 0" in setup
    assert "Check: RelaunchRequested" in setup


@posix_only
def test_released_helper_updates_current_linux_installer(tmp_path):
    from scripts.tests.test_installer_helper import helper, native_layout, set_native_entry, wait_for
    from scripts.tests.test_installer_journal import sandbox
    from scripts.tests.test_linux_bundle_install_update import installed_dir, run

    frozen = extract_release("v0.3.5", tmp_path / "released")
    env = sandbox(tmp_path)
    started = tmp_path / "started"
    old = native_layout(tmp_path, env, "old")
    assert run(old, env, "--no-launch").returncode == 0
    payload = native_layout(tmp_path, env, "new")
    set_native_entry(payload, started)
    proc, family, _, work = helper(installed_dir(env), payload, env, tmp_path,
                                   script_path=frozen / "launchers/installer-helper.sh")
    try:
        assert proc.wait(timeout=30) == 0
        wait_for(started.exists)
        assert (installed_dir(env) / "version.txt").read_text() == "new"
        assert json.loads((work / "outcome.json").read_text())["result"] == "installed"
    finally:
        family.stop()


@posix_only
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS installer tools")
def test_released_helper_updates_current_macos_installer(tmp_path):
    """The 0.3.5 helper mounts a real disk image and runs this build's installer."""
    from scripts.tests.test_dmg_install_update import APP, SCRIPT, installer_process_signals, make_app, version_of
    from scripts.tests.test_installer_journal import sandbox, stage_recovery

    frozen = extract_release("v0.3.5", tmp_path / "released")
    env = sandbox(tmp_path)
    target = make_app(tmp_path / "Apps" / APP, version="old")
    image_root = tmp_path / "image"
    image_root.mkdir()
    shutil.copy2(SCRIPT, image_root / "Install Waveguide Generator.command")
    make_app(image_root / APP, version="new")
    asset = tmp_path / "installer.dmg"
    # The helper resets PATH to the system tools, so the disk image is real.
    subprocess.run(["hdiutil", "create", "-quiet", "-fs", "HFS+", "-format", "UDZO", "-volname", "WG test",
                    "-srcfolder", str(image_root), str(asset)],
                   check=True, capture_output=True, timeout=60, preexec_fn=installer_process_signals)
    payload = tmp_path / "payload"
    payload.mkdir()
    recovery, work = stage_recovery(target, env, tmp_path)
    config = recovery / "config"
    config.write_text(config.read_text().replace("linux-x86_64\n", "macos-arm64\n"))
    journal = target.parent / ("." + target.name + ".install.lock.journal")
    metadata = json.dumps({"from": "0.3.5", "to": "0.3.6", "log": str(work / "install.log")})[:-1]
    result = subprocess.run(["/bin/sh", str(frozen / "launchers/installer-helper.sh"), "macos-arm64",
                             str(asset), str(payload), str(target), "999999999", str(work), metadata,
                             hashlib.sha256(asset.read_bytes()).hexdigest(), json.dumps(str(journal)), str(recovery)],
                            env=env, capture_output=True, text=True, timeout=120, preexec_fn=installer_process_signals)
    outcome = json.loads((work / "outcome.json").read_text())
    assert outcome["result"] == "installed", (outcome, result.stdout + result.stderr)
    assert version_of(target) == "new"
    assert not (payload / "mount" / APP).exists(), "the helper left the disk image attached"
