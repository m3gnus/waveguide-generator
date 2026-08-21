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


class _TclError(Exception):
    """Stands in for tkinter.TclError, which CI may not be able to import."""


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
    assert "_tkinter" in message, "the underlying import error is the actionable part"
    # This particular error means the files are present and will not load, so
    # the message must not be the install-it remedy. See
    # test_statusapp_diagnostics for the separation of the causes.
    assert "could not be loaded" in message
    assert "Interpreter" in message, "which Python failed is half the answer"
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


def test_the_dialog_is_short_and_the_log_carries_the_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A modal box cannot be scrolled, copied out of, or read beside the log.

    So the evidence table goes to the log and the terminal, and the dialog gets
    the cause, the remedy, and the path to the rest.
    """

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stderr", None)
    shown: list[str] = []
    monkeypatch.setattr(entrypoint, "_show_startup_failure_dialog", shown.append)
    monkeypatch.setitem(
        sys.modules,
        "launchers.statusapp.view",
        _import_of("launchers.statusapp.view", "DLL load failed while importing _tkinter"),
    )

    assert entrypoint.main([]) == 1

    dialog = shown[0]
    logged = (tmp_path / "logs" / entrypoint.LOG_FILENAME).read_text(encoding="utf-8")
    assert "What was found:" not in dialog, "an evidence table is unreadable in a dialog"
    assert "What was found:" in logged
    assert entrypoint.LOG_FILENAME in dialog, "the dialog has to say where the rest is"


def test_the_log_is_named_by_path_rather_than_described(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """"The Waveguide Generator log folder" is a phrase, not an address."""

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))

    location = entrypoint._log_location()

    assert str(tmp_path / "logs" / entrypoint.LOG_FILENAME) in location


def test_a_non_tk_import_failure_does_not_get_the_tk_page(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The view imports its siblings, so this branch catches more than tkinter."""

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "launchers.statusapp.view",
        _import_of("launchers.statusapp.view", "No module named 'server.platform.instance'"),
    )

    assert entrypoint.main([]) == 1

    message = capsys.readouterr().err
    assert "server.platform.instance" in message
    assert "tcl/tk" not in message, "a broken checkout is not a Tk problem"


def test_a_window_that_never_opened_reports_the_tcl_error_beneath_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """tkinter is imported at view module scope, so this arrives from run().

    It used to land in the generic branch and read as "an unexpected error"
    over a TclError about init.tcl, naming neither Tk nor anything actionable.
    """

    from launchers.statusapp.diagnostics import WindowUnavailable

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    stub = ModuleType("launchers.statusapp.view")

    def run(_controller: object) -> int:
        raise WindowUnavailable("Can't find a usable init.tcl") from _TclError(
            "Can't find a usable init.tcl"
        )

    stub.run = run
    monkeypatch.setitem(sys.modules, "launchers.statusapp.view", stub)

    assert entrypoint.main([]) == 1

    message = capsys.readouterr().err
    assert "failed to create a window" in message
    assert "_TclError: Can" in message, "the wrapper is a marker; the cause carries the text"
