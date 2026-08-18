"""A startup failure must be visible, including when there is no console.

The Windows launcher spawns this application with ``start "" pythonw.exe``,
which leaves ``sys.stdout`` and ``sys.stderr`` as None. ``print`` silently
returns when its target is None, so every diagnostic the entry point wrote went
nowhere at all: ``launch-wg.bat`` had already exited 0, ``start`` reports only
its own spawn failures, and the user was left with two console windows flashing
and no explanation anywhere on the machine. These tests pin the three delivery
channels that replaced the lone stderr write.
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest

from launchers.statusapp import __main__ as entrypoint


def _import_of(name: str, error: str) -> ModuleType:
    """A stand-in module whose every ``from ... import`` raises ImportError.

    Module-level ``__getattr__`` is what ``from .view import run`` consults, so
    this reproduces a tkinter-less interpreter without needing one.
    """

    stub = ModuleType(name)

    def _raise(_attribute: str) -> object:
        raise ImportError(error)

    stub.__getattr__ = _raise  # type: ignore[attr-defined]
    return stub


def test_a_missing_tkinter_is_reported_rather_than_swallowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "launchers.statusapp.view",
        _import_of(
            "launchers.statusapp.view",
            "DLL load failed while importing _tkinter: The specified module could not be found.",
        ),
    )

    assert entrypoint.main([]) == 1

    message = capsys.readouterr().err
    assert "tkinter is unavailable" in message
    assert "_tkinter" in message, "the underlying import error is the actionable part"
    assert entrypoint._tkinter_repair_hint() in message
    assert "--no-gui" in message, "the mode that still works belongs in the message"


def test_a_broken_controller_import_is_reported_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The controller import reaches the server package and repository scripts.

    It fails for a broken checkout as readily as for a broken environment, and
    unguarded its traceback went to the same nowhere the tkinter message did.
    """

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "launchers.statusapp.controller",
        _import_of(
            "launchers.statusapp.controller", "No module named 'server.platform.instance'"
        ),
    )

    assert entrypoint.main([]) == 1

    message = capsys.readouterr().err
    assert "failed to load" in message
    assert "server.platform.instance" in message


def test_the_failure_and_its_traceback_reach_the_log_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))

    entrypoint._report_startup_failure("the interface is missing", detail="Traceback (most recent")

    log = tmp_path / "logs" / entrypoint.LOG_FILENAME
    contents = log.read_text(encoding="utf-8")
    assert "the interface is missing" in contents
    assert "Traceback (most recent" in contents, "the log is where a traceback belongs"


def test_without_a_console_the_failure_goes_to_a_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """This is the pythonw case, and the whole reason the bug was invisible."""

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stderr", None)
    shown: list[str] = []
    monkeypatch.setattr(entrypoint, "_show_startup_failure_dialog", shown.append)

    entrypoint._report_startup_failure("tkinter is unavailable")

    assert shown == ["tkinter is unavailable"]
    log = (tmp_path / "logs" / entrypoint.LOG_FILENAME).read_text(encoding="utf-8")
    assert "tkinter is unavailable" in log, "the dialog is dismissed; the log is not"


def test_with_a_console_no_dialog_is_raised(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    shown: list[str] = []
    monkeypatch.setattr(entrypoint, "_show_startup_failure_dialog", shown.append)

    entrypoint._report_startup_failure("terminal mode says this out loud")

    assert shown == [], "a dialog on top of a readable message is just a second click"
    assert "terminal mode says this out loud" in capsys.readouterr().err


def test_reporting_survives_a_log_directory_it_cannot_write(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Reporting runs because something already failed; it must not add to it."""

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path / "read-only"))

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _refuse)

    entrypoint._report_startup_failure("still reaches stderr")

    assert "still reaches stderr" in capsys.readouterr().err
