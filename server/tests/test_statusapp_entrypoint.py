"""Terminal mode must refuse a missing interface as clearly as the GUI does.

``--no-gui`` hands straight to ``launch.serve``, bypassing the status window's
own guard. Without a check here the server raises a starlette RuntimeError from
inside ``create_app`` -- "Directory '.../frontend/dist' does not exist" -- and a
traceback several frames deep reads as a broken application rather than an
unbuilt one. This is platform-independent: the same hole existed on macOS,
Windows and Linux.
"""

from __future__ import annotations

from pathlib import Path

from launchers.statusapp import __main__ as entrypoint
from launchers.statusapp.controller import missing_frontend_reason


def test_terminal_mode_refuses_a_missing_interface(monkeypatch, capsys, tmp_path: Path) -> None:
    # Refusals are also appended to statusapp.log; keep the suite out of the
    # real application data directory.
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(entrypoint, "FRONTEND_INDEX", tmp_path / "frontend" / "dist" / "index.html")

    def _must_not_run(_arguments):  # pragma: no cover - the point is that it is not called
        raise AssertionError("the server must not be started without an interface")

    monkeypatch.setattr("launch.serve.main", _must_not_run)

    assert entrypoint.main(["--no-gui"]) == 1
    message = capsys.readouterr().err
    assert "frontend/dist missing" in message
    assert "Traceback" not in message


def test_terminal_mode_and_the_status_window_give_the_same_reason(monkeypatch, capsys, tmp_path: Path) -> None:
    """One condition, one explanation. Two wordings would be a drift bug."""

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(entrypoint, "FRONTEND_INDEX", tmp_path / "index.html")
    monkeypatch.setattr("launch.serve.main", lambda _arguments: 0)
    entrypoint.main(["--no-gui"])
    assert missing_frontend_reason() in capsys.readouterr().err


def test_terminal_mode_starts_the_server_when_the_interface_exists(monkeypatch, tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "FRONTEND_INDEX", index)

    seen: list[list[str]] = []
    monkeypatch.setattr("launch.serve.main", lambda arguments: seen.append(list(arguments)) or 0)

    assert entrypoint.main(["--no-gui", "--port", "3199"]) == 0
    assert seen == [["--port", "3199"]], "--no-gui is consumed, the rest is forwarded"


def test_the_guarded_path_points_at_the_file_the_server_serves() -> None:
    """A guard checking a different path from the server is worse than none."""

    from server.app import FRONTEND_DIST

    assert entrypoint.FRONTEND_INDEX == FRONTEND_DIST / "index.html"
