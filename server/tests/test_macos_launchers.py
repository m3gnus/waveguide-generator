from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MACOS = ROOT / "launchers" / "macos"
COMMAND = MACOS / "launch-wg.command"
DEV_COMMAND = MACOS / "launch-wg-dev.command"
APP_EXECUTABLE = (
    MACOS / "Waveguide Generator.app" / "Contents" / "MacOS" / "Waveguide Generator"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_finder_app_delegates_to_the_validated_command_launcher() -> None:
    source = _read(APP_EXECUTABLE)
    assert "launchers/macos/launch-wg.command" in source
    assert "exec \"$LAUNCHER\"" in source
    assert "-m launchers.statusapp" not in source
    assert "scripts/bootstrap.py" not in source
    assert "WG2_FINDER_APP=1" in source


def test_review_launcher_builds_local_sources_without_changing_git() -> None:
    source = _read(DEV_COMMAND)
    assert "scripts/frontend_freshness.py" in source
    assert '"$NPM" run build' in source
    assert "launchers/macos/launch-wg.command" in source
    assert "git pull" not in source
    assert "git fetch" not in source


def test_macos_launchers_are_executable() -> None:
    for path in (COMMAND, DEV_COMMAND, APP_EXECUTABLE):
        assert path.stat().st_mode & 0o111, f"{path.relative_to(ROOT)} is not executable"
