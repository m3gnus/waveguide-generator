"""Raising Fusion after an export is a courtesy that must never cost the export."""

from __future__ import annotations

import subprocess

import pytest

from server.platform import cad_launch


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


def test_macos_opens_the_archived_file_with_fusion(monkeypatch, tmp_path) -> None:
    calls = _capture(monkeypatch)
    monkeypatch.delenv("WG2_CAD_LAUNCH", raising=False)
    document = tmp_path / "68_cad-import.f3d"
    cad_launch.open_cad_document(document, system="Darwin")
    assert calls == [["open", "-a", "Autodesk Fusion", str(document)]]


def test_macos_uses_the_file_handler_if_the_app_name_is_not_registered(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.delenv("WG2_CAD_LAUNCH", raising=False)

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, int("-a" in command))

    monkeypatch.setattr(cad_launch.subprocess, "run", run)
    document = tmp_path / "model.f3d"
    cad_launch.open_cad_document(document, system="Darwin")
    assert calls == [["open", "-a", "Autodesk Fusion", str(document)], ["open", str(document)]]


def test_macos_missing_fusion_and_handler_is_a_clear_error(monkeypatch, tmp_path) -> None:
    _capture(monkeypatch, returncode=1)
    monkeypatch.delenv("WG2_CAD_LAUNCH", raising=False)
    with pytest.raises(RuntimeError, match="Install Fusion 360"):
        cad_launch.open_cad_document(tmp_path / "model.f3d", system="Darwin")


def test_open_timeout_is_a_clear_error(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WG2_CAD_LAUNCH", raising=False)

    def timeout(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 15)

    monkeypatch.setattr(cad_launch.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="Check that Fusion 360 is installed"):
        cad_launch.open_cad_document(tmp_path / "model.f3d", system="Darwin")


def test_windows_opens_the_exact_file_with_startfile(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WG2_CAD_LAUNCH", raising=False)
    calls = []
    monkeypatch.setattr(cad_launch.os, "startfile", calls.append, raising=False)
    document = tmp_path / "model.f3d"
    cad_launch.open_cad_document(document, system="Windows")
    assert calls == [str(document)]


def test_windows_missing_handler_is_a_clear_error(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WG2_CAD_LAUNCH", raising=False)

    def fail(_path):
        raise OSError("No association")

    monkeypatch.setattr(cad_launch.os, "startfile", fail, raising=False)
    with pytest.raises(RuntimeError, match="Check that Fusion 360 is installed"):
        cad_launch.open_cad_document(tmp_path / "model.f3d", system="Windows")


def test_disabled_launch_never_opens_a_document(monkeypatch, tmp_path) -> None:
    calls = _capture(monkeypatch)
    monkeypatch.setenv("WG2_CAD_LAUNCH", "0")
    with pytest.raises(RuntimeError, match="disabled"):
        cad_launch.open_cad_document(tmp_path / "model.f3d", system="Darwin")
    assert calls == []
