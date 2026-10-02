"""The bridge's retained native entries reach the B app's earliest guard."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX retained entries", allow_module_level=True)

from scripts.tests.test_installer_journal import sandbox, stage_recovery
from scripts.tests.test_installer_helper import ROOT, set_bridge_app_prefix


@pytest.mark.skipif(sys.platform != "darwin", reason="retained Mach-O entry")
@pytest.mark.parametrize("live", [False, True])
def test_retained_v032_mac_executable_guards_B_app_before_controller(tmp_path, live):
    env = sandbox(tmp_path)
    root = tmp_path / "Waveguide Generator.app"
    app = root / "Contents/Resources/app"
    app.mkdir(parents=True)
    runtime = app.parent / "runtime/bin"
    runtime.mkdir(parents=True)
    python = runtime / "python3.13"
    python.touch()
    python.chmod(0o755)
    executable = root / "Contents/MacOS/Waveguide Generator"
    executable.parent.mkdir()
    compiled = subprocess.run(["clang", str(ROOT / "scripts/tests/fixtures/full-installer-bridge-entry/macos-v032.c"),
        "-o", str(executable)], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    started = tmp_path / "controller-admission"
    set_bridge_app_prefix(app, started, platform="darwin")
    recovery, work = stage_recovery(root, env, tmp_path)
    config = recovery / "config"
    config.write_text(config.read_text().replace("linux-x86_64\n", "macos-arm64\n"))
    lock = root.parent / ("." + root.name + ".install.lock")
    lock.mkdir()
    owner = subprocess.Popen(["/bin/sh", "-c", "exec sleep 120"], start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (lock / "pid").write_text(str(owner.pid) + "\n")
    original = (root.stat().st_dev, root.stat().st_ino)
    try:
        if not live:
            os.kill(owner.pid, signal.SIGKILL)
            owner.wait(timeout=5)
        result = subprocess.run([str(executable)], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == (4 if live else 0), result.stdout + result.stderr
        assert started.exists() is (not live)
        assert (root.stat().st_dev, root.stat().st_ino) == original
        if live:
            assert lock.exists() and recovery.exists() and not (work / "outcome.json").exists()
        else:
            outcome = json.loads((work / "outcome.json").read_text())
            assert outcome["result"] == "failed" and outcome["previousKept"] is True
            assert not lock.exists() and not recovery.exists()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "darwin", reason="new Mach-O entry")
def test_new_mac_native_entry_does_not_execute_linked_recovery_directory(tmp_path):
    root = tmp_path / "Waveguide Generator.app"
    app = root / "Contents/Resources/app"
    app.mkdir(parents=True)
    runtime = app.parent / "runtime/bin"
    runtime.mkdir(parents=True)
    started = tmp_path / "runtime-started"
    python = runtime / "python3.13"
    python.write_text(f'#!/bin/sh\ntouch "{started}"\n')
    python.chmod(0o755)
    executable = root / "Contents/MacOS/Waveguide Generator"
    executable.parent.mkdir()
    compiled = subprocess.run(["clang", str(ROOT / "launchers/macos/launcher.c"), "-o", str(executable)],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    foreign = tmp_path / "foreign-recovery"
    foreign.mkdir()
    executed = tmp_path / "foreign-executed"
    (foreign / "recover.sh").write_text(f'#!/bin/sh\ntouch "{executed}"\nexit 0\n')
    (root.parent / ("." + root.name + ".installer-recovery")).symlink_to(foreign)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 4
    assert not executed.exists() and not started.exists()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_retained_v032_layer_receiver_delivers_B_logger_before_next_handoff(tmp_path, monkeypatch, platform):
    import importlib.util
    import shutil
    import time
    from launchers.full_installer import FullInstallerRequest, launch_full_installer
    from scripts.build_bundle import write_installer_logger, write_runtime_manifest, MACOS_PLATFORM, LINUX_PLATFORM, PYTHON_BUILD
    import hashlib
    root = tmp_path / ("Waveguide Generator.app" if platform == "darwin" else "waveguide-generator")
    resources = root / "Contents/Resources" if platform == "darwin" else root
    app, runtime, outer = (resources / name for name in ("app", "runtime", "recovery"))
    for directory in (app, runtime, outer):
        directory.mkdir(parents=True)
    (app / "old-app").write_text("0.3.2")
    (runtime / "old-runtime").write_text("0.3.2")
    (outer / "historical-recovery").write_text("must remain exactly unchanged")
    staged = tmp_path / "staged"
    new_app, new_runtime = staged / "app", staged / "runtime"
    (new_app / "launchers").mkdir(parents=True)
    (new_runtime / "bin").mkdir(parents=True)
    for name in ("installer-helper.sh", "installer-recovery.sh"):
        shutil.copyfile(ROOT / "launchers" / name, new_app / "launchers" / name)
    native = new_runtime / "bin/wg-installer-log"
    selected_platform = MACOS_PLATFORM if platform == "darwin" else LINUX_PLATFORM
    write_installer_logger(native, repo_root=ROOT, platform_name=selected_platform)
    runtime_manifest = write_runtime_manifest(new_runtime, python_version="3.13.12", runtime_id="new-logger-runtime",
        requirements=b"runtime", pins=b"pins", lock=b"lock", python_build=PYTHON_BUILD,
        runtime_recipe="source-bound-logger", platform_name=selected_platform)
    assert runtime_manifest["installerLoggerSha256"] == hashlib.sha256(native.read_bytes()).hexdigest()
    expected_binary = native.read_bytes()
    # This is the actual retained v0.3.2 receiver, not a test copy operation.
    retained = ROOT / "scripts/tests/fixtures/windows_v032/apply_update.py"
    spec = importlib.util.spec_from_file_location("wg_retained_posix_bridge_receiver", retained)
    legacy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, legacy)
    spec.loader.exec_module(legacy)
    legacy.swap_staged_layers(resources, new_app, new_runtime)
    assert (outer / "historical-recovery").read_text() == "must remain exactly unchanged"
    assert not (outer / "installer-log").exists()
    assert (resources / "runtime.previous/old-runtime").read_text() == "0.3.2"
    assert (resources / "runtime/bin/wg-installer-log").read_bytes() == expected_binary
    data = tmp_path / "data"
    work = data / "update-install"
    work.mkdir(parents=True)
    asset = work / "next-installer"
    asset.write_bytes(b"verified next full installer")
    request = FullInstallerRequest("0.3.5", "0.3.4", asset, root, selected_platform,
        hashlib.sha256(asset.read_bytes()).hexdigest(), asset.stat().st_size, time.time() + 120, 1)
    import launchers.full_installer as module
    monkeypatch.setattr(module, "_extract_linux", lambda *args: None)
    copied = []
    def detach(command, **_kwargs):
        external = Path(command[-1]) / "log"
        delivered = resources / "runtime/bin/wg-installer-log"
        assert external.read_bytes() == delivered.read_bytes()
        assert external.stat().st_mode & 0o111
        copied.append(external)
    monkeypatch.setattr(module.subprocess, "Popen", detach)
    launch_full_installer(app, request, 999999999, data_dir=data)
    assert len(copied) == 1
