from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_linux_launcher_refreshes_before_starting_and_names_a_missing_build() -> None:
    source = (ROOT / "launchers" / "linux" / "launch-wg.sh").read_text(encoding="utf-8")
    refresh = source.index('"$PYTHON" -m scripts.build_frontend_if_stale')
    guard = source.index('frontend/dist/index.html')
    start = source.index('exec "$PYTHON" -m launchers.statusapp')
    assert refresh < guard < start
    assert "npm ci && npm run build" in source


def test_windows_launcher_refreshes_before_gui_or_terminal_start() -> None:
    source = (ROOT / "launchers" / "windows" / "launch-wg.bat").read_text(encoding="utf-8")
    refresh = source.index(r'"%PYTHON%" -m scripts.build_frontend_if_stale')
    guard = source.index(r'if not exist "frontend\dist\index.html" goto :missing_frontend')
    mode = source.index("call :gui_mode_requested %*")
    assert refresh < guard < mode


def test_platform_launchers_forward_display_and_server_flags_unchanged() -> None:
    macos = (ROOT / "launchers" / "macos" / "launch-wg.command").read_text(encoding="utf-8")
    windows = (ROOT / "launchers" / "windows" / "launch-wg.bat").read_text(encoding="utf-8")

    assert 'exec "$PYTHON" -m launchers.statusapp "$@"' in macos
    assert 'start "" "%PYTHONW%" -m launchers.statusapp %*' in windows
    assert '"%PYTHON%" -m launchers.statusapp %*' in windows
    assert "--no-gui" in macos
    assert "--no-gui" in windows
