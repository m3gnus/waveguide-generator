"""Raising Fusion after an export is a courtesy that must never cost the export."""

from __future__ import annotations

import subprocess

import pytest

from server.exports import cad_launch


def _capture(monkeypatch, *, returncode: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, returncode, b"", b"")

    monkeypatch.setattr(cad_launch.subprocess, "run", fake_run)
    monkeypatch.setattr(cad_launch.shutil, "which", lambda _name: "/usr/bin/open")
    return calls


def test_macos_raises_fusion_by_bundle_id(monkeypatch) -> None:
    calls = _capture(monkeypatch)
    assert cad_launch.focus_cad(system="Darwin", environ={}) == "launched"
    # By bundle id, so a per-user install under ~/Applications still resolves.
    assert calls == [["open", "-b", "com.autodesk.fusion360"]]


def test_macos_falls_back_to_the_application_name(monkeypatch) -> None:
    calls = _capture(monkeypatch, returncode=1)
    assert cad_launch.focus_cad(system="Darwin", environ={}) == "failed"
    assert calls[0] == ["open", "-b", "com.autodesk.fusion360"]
    assert calls[1] == ["open", "-a", "Autodesk Fusion"]


def test_a_launcher_that_explodes_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(cad_launch.shutil, "which", lambda _name: "/usr/bin/open")

    def explode(_command, **_kwargs):
        raise OSError("no such process")

    monkeypatch.setattr(cad_launch.subprocess, "run", explode)
    assert cad_launch.focus_cad(system="Darwin", environ={}) == "failed"


def test_a_timeout_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(cad_launch.shutil, "which", lambda _name: "/usr/bin/open")

    def timeout(_command, **_kwargs):
        raise subprocess.TimeoutExpired(_command, 15)

    monkeypatch.setattr(cad_launch.subprocess, "run", timeout)
    assert cad_launch.focus_cad(system="Darwin", environ={}) == "failed"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_the_environment_can_switch_it_off(monkeypatch, value: str) -> None:
    calls = _capture(monkeypatch)
    assert cad_launch.focus_cad(system="Darwin", environ={"WG2_CAD_LAUNCH": value}) == "disabled"
    assert calls == []


def test_an_unsupported_platform_says_so_without_running_anything(monkeypatch) -> None:
    calls = _capture(monkeypatch)
    assert cad_launch.focus_cad(system="Linux", environ={}) == "unsupported:linux"
    assert calls == []


def test_windows_picks_the_newest_webdeploy_build(monkeypatch, tmp_path) -> None:
    production = tmp_path / "Autodesk" / "webdeploy" / "production"
    older = production / "aaa"
    newer = production / "bbb"
    for folder, mtime in ((older, 1_000), (newer, 2_000)):
        folder.mkdir(parents=True)
        exe = folder / "Fusion360.exe"
        exe.write_bytes(b"")
        import os

        os.utime(exe, (mtime, mtime))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = _capture(monkeypatch)
    assert cad_launch.focus_cad(system="Windows", environ={}) == "launched"
    assert calls == [[str(newer / "Fusion360.exe")]]


def test_windows_without_an_install_is_unsupported_not_a_crash(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = _capture(monkeypatch)
    assert cad_launch.focus_cad(system="Windows", environ={}) == "unsupported:windows"
    assert calls == []
