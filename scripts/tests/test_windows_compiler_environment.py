"""Native compiler discovery keeps cmd's quoted batch path intact."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from scripts import build_bundle


@pytest.mark.parametrize("initialization_exit", [0, 1])
def test_native_compiler_initializes_a_spaced_batch_path_and_preserves_refusal(tmp_path, monkeypatch, initialization_exit):
    program_files = tmp_path / "Program Files (x86)"
    installer = program_files / "Microsoft Visual Studio/Installer/vswhere.exe"
    installer.parent.mkdir(parents=True)
    installer.touch()
    installation = tmp_path / "Visual Studio 2026"
    vcvars = installation / "VC/Auxiliary/Build/vcvars64.bat"
    vcvars.parent.mkdir(parents=True)
    vcvars.write_text('@echo off\nset "PATH=initialized-compiler-path"\n'
                      + ("echo fixture initialization failure 1>&2\n" if initialization_exit else "")
                      + f"exit /b {initialization_exit}\n", encoding="ascii")
    environment = {key: os.environ[key] for key in ["SystemRoot", "WINDIR"] if key in os.environ}
    environment.update({"PATH": "original-path", "COMSPEC": os.environ.get("COMSPEC", "cmd.exe"),
                        "ProgramFiles(x86)": str(program_files)})
    target = tmp_path / "helper.exe"
    compiler = str(tmp_path / "cl.exe")
    monkeypatch.setattr(build_bundle.shutil, "which", lambda name, *, path: compiler if path == "initialized-compiler-path" else None)
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        if isinstance(command, list) and command[0] == str(installer):
            return subprocess.CompletedProcess(command, 0, str(installation).encode(), b"")
        if isinstance(command, str):
            # /s removes only these outer quotes; inner path quotes must reach
            # cmd unchanged, without CRT backslash escaping.
            tail = command.split(" /c ", 1)[1]
            assert tail[0] == tail[-1] == '"'
            assert tail[1:-1] == f'call "{vcvars}" >nul && set'
            if os.name == "nt":
                return subprocess.run(command, **kwargs)
            return subprocess.CompletedProcess(command, initialization_exit,
                                               b"PATH=initialized-compiler-path\n", b"fixture initialization failure")
        assert command[0] == compiler
        assert kwargs["env"]["PATH"] == "initialized-compiler-path"
        assert all(flag in command for flag in ["/MT", "/W4", "/WX", "/MACHINE:X64"])
        target.write_bytes(b"compiled fixture")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    if initialization_exit:
        with pytest.raises(build_bundle.BundleError, match="compiler environment: fixture initialization failure"):
            build_bundle.write_windows_launcher(target, repo_root=Path(__file__).resolve().parents[2],
                                                runner=runner, environment=environment)
        assert len(commands) == 2 and not target.exists()
    else:
        build_bundle.write_windows_launcher(target, repo_root=Path(__file__).resolve().parents[2],
                                            runner=runner, environment=environment)
        assert target.read_bytes() == b"compiled fixture" and len(commands) == 3
