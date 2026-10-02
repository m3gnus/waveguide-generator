"""POSIX native entries refuse an independent full installer owner."""
from __future__ import annotations

import subprocess
import sys

import pytest

from scripts.build_bundle import linux_launcher


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX native entry")
@pytest.mark.parametrize("leftover", ["lock", "journal", "linked-lock", "linked-recovery"])
def test_linux_native_entry_refuses_installer_or_recovery_record(tmp_path, leftover):
    root = tmp_path / "waveguide-generator"
    (root / "app").mkdir(parents=True)
    runtime = root / "runtime/bin"
    runtime.mkdir(parents=True)
    started = tmp_path / "started"
    python = runtime / "python3.13"
    python.write_text(f'#!/bin/sh\ntouch "{started}"\n')
    python.chmod(0o755)
    launcher = root / "waveguide-generator"
    launcher.write_text(linux_launcher())
    launcher.chmod(0o755)
    lock = root.parent / ".waveguide-generator.install.lock"
    if leftover == "lock":
        lock.mkdir()
    elif leftover == "journal":
        lock.with_name(lock.name + ".journal").write_text("recovery record")
    elif leftover == "linked-lock":
        lock.symlink_to(tmp_path / "missing")
    else:
        foreign = tmp_path / "foreign-recovery"
        foreign.mkdir()
        (foreign / "recover.sh").write_text(f'#!/bin/sh\ntouch "{tmp_path / "foreign-executed"}"\nexit 0\n')
        (root.parent / ".waveguide-generator.installer-recovery").symlink_to(foreign)
    result = subprocess.run(["/bin/sh", str(launcher)], capture_output=True, text=True)
    assert result.returncode == 4
    assert not started.exists()
    assert not (tmp_path / "foreign-executed").exists()
