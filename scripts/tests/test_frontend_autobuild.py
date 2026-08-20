from __future__ import annotations

import os
from pathlib import Path
import subprocess

from scripts import build_frontend_if_stale as subject
from scripts.frontend_freshness import frontend_freshness


def _stale_checkout(root: Path) -> Path:
    source = root / "frontend" / "src" / "main.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const current = true;\n", encoding="utf-8")
    built = root / "frontend" / "dist" / "index.html"
    built.parent.mkdir(parents=True)
    built.write_text("old", encoding="utf-8")
    os.utime(built, (0, 0))
    vite = root / "frontend" / "node_modules" / ".bin" / (
        "vite.cmd" if os.name == "nt" else "vite"
    )
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")
    return built


def test_stale_checkout_builds_and_records_its_sources(tmp_path: Path, monkeypatch) -> None:
    _stale_checkout(tmp_path)
    npm = tmp_path / ("npm.cmd" if os.name == "nt" else "npm")
    npm.write_text("", encoding="utf-8")
    monkeypatch.setattr(subject, "_find_npm", lambda _environment: npm)
    commands: list[tuple[list[str], Path]] = []

    def run(command: list[str], *, cwd: Path, **_kwargs: object) -> subprocess.CompletedProcess:
        commands.append((command, cwd))
        (cwd / "dist" / "index.html").write_text("new", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subject.subprocess, "run", run)

    assert subject.build_frontend_if_stale(tmp_path, environ={"PATH": ""})
    assert commands[0][1] == tmp_path / "frontend"
    assert commands[0][0][-2:] == ["run", "build"]
    assert frontend_freshness(tmp_path)[0]


def test_missing_node_keeps_the_previous_build_and_never_spawns(
    tmp_path: Path,
    monkeypatch,
) -> None:
    built = _stale_checkout(tmp_path)
    monkeypatch.setattr(subject, "_find_npm", lambda _environment: None)
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    assert not subject.build_frontend_if_stale(tmp_path, environ={"PATH": ""})
    assert built.read_text(encoding="utf-8") == "old"


def test_skip_variable_bypasses_even_the_freshness_probe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        subject,
        "frontend_freshness",
        lambda _root: (_ for _ in ()).throw(AssertionError("freshness probed")),
    )

    assert subject.build_frontend_if_stale(
        tmp_path, environ={"WG2_SKIP_FRONTEND_BUILD": "1"}
    )


def test_windows_batch_npm_uses_cmd_without_losing_arguments(monkeypatch) -> None:
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    command = subject._npm_command(Path(r"C:\Program Files\nodejs\npm.cmd"), windows=True)
    assert command == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/c",
        r"C:\Program Files\nodejs\npm.cmd",
        "run",
        "build",
    ]
