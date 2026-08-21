"""One canned remedy cannot describe three different causes.

The status window's failure message was "re-run the Python installer and tick
tcl/tk and IDLE", printed for every ImportError the view could raise. A user
reported that their box was already ticked, that the log said the same thing as
the dialog, and that they had given up and would pass --no-gui from then on.
That is what a message which guesses costs.

These tests pin the separation that replaced the guess: an interpreter with no
Tk at all, an interpreter whose Tk is present but will not load, and a Tk that
loads and then cannot open a window -- three causes with three remedies, each
carrying the evidence that distinguishes it.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from launchers.statusapp import diagnostics


def _how_this_platform_installs_tk() -> str:
    """The phrase that has to reach the user on the platform under test."""

    if os.name == "nt":
        return "tcl/tk and IDLE"
    if sys.platform == "darwin":
        return "python-tk@3.13"
    return "python3-tk"


def test_a_truly_absent_tkinter_is_the_install_it_case() -> None:
    failure = diagnostics.diagnose_import_error(
        ModuleNotFoundError("No module named 'tkinter'", name="tkinter")
    )

    assert failure.kind == "missing"
    assert _how_this_platform_installs_tk() in failure.summary()


def test_an_unloadable_tkinter_is_not_the_install_it_case() -> None:
    """The regression this module exists for.

    "DLL load failed while importing _tkinter" means the files are already
    there. Repeating the install-it remedy sends the user back to a checkbox
    they have already ticked, which is precisely the dead end that was reported.
    """

    failure = diagnostics.diagnose_import_error(
        ImportError("DLL load failed while importing _tkinter: The specified module could not be found.")
    )

    assert failure.kind == "unloadable"
    assert "already installed" in failure.summary() or "missing from" in failure.summary()


def test_the_two_import_causes_do_not_give_the_same_advice() -> None:
    absent = diagnostics.diagnose_import_error(
        ModuleNotFoundError("No module named 'tkinter'", name="tkinter")
    )
    unloadable = diagnostics.diagnose_import_error(ImportError("DLL load failed"))

    assert absent.remedy != unloadable.remedy


def test_every_report_names_the_interpreter_that_actually_failed() -> None:
    """The "but I ticked the box" case is a *different Python* nine times in ten.

    Nothing on screen used to say which interpreter the launcher had chosen, so
    ticking the option in one installation and launching from another looked
    exactly like a broken application.
    """

    report = diagnostics.diagnose_import_error(ImportError("DLL load failed")).report()

    assert "Interpreter" in report
    assert "Python installation" in report
    assert diagnostics._module_origin.__name__  # the evidence is gathered, not guessed


def test_the_evidence_stays_out_of_the_dialog_and_in_the_report() -> None:
    """A modal dialog cannot be scrolled or copied out of; a log file can."""

    failure = diagnostics.diagnose_import_error(ImportError("DLL load failed"))

    assert "What was found:" not in failure.summary()
    assert "What was found:" in failure.report()
    assert failure.summary() in failure.report(), "the report is the summary plus evidence"


def test_every_message_says_the_application_still_works() -> None:
    for failure in (
        diagnostics.diagnose_missing_module("x"),
        diagnostics.diagnose_unloadable_module("x"),
        diagnostics.diagnose_display_failure("x"),
    ):
        assert "--no-gui" in failure.summary()


def test_a_foreign_tcl_is_named_when_it_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAD and simulation tools set TCL_LIBRARY, and Tk then loads their Tcl.

    Nothing in the old message could express this, so the resulting TclError
    surfaced as "an unexpected error" with no mention of Tk.
    """

    monkeypatch.setenv("TCL_LIBRARY", "C:/SomeCAD/tcl8.5")

    failure = diagnostics.diagnose_display_failure("TclError: Can't find a usable init.tcl")

    assert failure.kind == "display"
    assert "TCL_LIBRARY" in failure.summary()
    assert ("TCL_LIBRARY", "C:/SomeCAD/tcl8.5") in failure.evidence


def test_a_submodule_import_failure_is_read_from_disk_not_from_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``No module named 'tkinter.ttk'`` on a Python that *has* tkinter.

    The package is present and incomplete, which is an install that will not
    load rather than an absent one; classifying by the exception's name alone
    would get it backwards.
    """

    error = ModuleNotFoundError("No module named 'tkinter.ttk'", name="tkinter.ttk")

    monkeypatch.setattr(diagnostics, "_module_origin", lambda _name: Path("tkinter/__init__.py"))
    assert diagnostics.diagnose_import_error(error).kind == "unloadable"

    monkeypatch.setattr(diagnostics, "_module_origin", lambda _name: None)
    assert diagnostics.diagnose_import_error(error).kind == "missing"


def test_a_machine_with_no_screen_is_not_reported_as_broken(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A headless server has no window to open and never will.

    Warning about it on every install is how warnings stop being read.
    """

    monkeypatch.setattr(
        diagnostics, "probe", lambda: diagnostics.diagnose_display_failure("no $DISPLAY")
    )
    monkeypatch.setattr(diagnostics, "is_headless", lambda: True)

    assert diagnostics.main([]) == 0
    assert "no graphical session" in capsys.readouterr().out


def test_a_desktop_that_cannot_open_a_window_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        diagnostics, "probe", lambda: diagnostics.diagnose_display_failure("TclError: init.tcl")
    )
    monkeypatch.setattr(diagnostics, "is_headless", lambda: False)

    assert diagnostics.main([]) == 1
    assert "What was found:" in capsys.readouterr().err


def test_windows_is_never_treated_as_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics.os, "name", "nt")
    monkeypatch.delenv("DISPLAY", raising=False)

    assert diagnostics.is_headless() is False


@pytest.mark.skipif(
    os.name == "nt" or sys.platform == "darwin",
    reason="X11 and Wayland are where DISPLAY decides; Aqua needs neither",
)
def test_x11_without_a_display_is_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    assert diagnostics.is_headless() is True


def test_macos_is_never_treated_as_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aqua has no DISPLAY, so the X11 rule would call every Mac headless."""

    monkeypatch.setattr(diagnostics.os, "name", "posix")
    monkeypatch.setattr(diagnostics.sys, "platform", "darwin")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    assert diagnostics.is_headless() is False


def test_the_probe_opens_a_window_rather_than_importing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An import proves the files exist, which is not the question being asked.

    The install-time check used to stop at ``import tkinter`` and reported
    success on machines where the window never appeared.
    """

    tkinter = pytest.importorskip("tkinter")
    if diagnostics.is_headless():
        pytest.skip("no window server to fail against")
    calls: list[str] = []

    def refuse():
        calls.append("Tk")
        raise tkinter.TclError("Can't find a usable init.tcl")

    monkeypatch.setattr(tkinter, "Tk", refuse)

    failure = diagnostics.probe()

    assert calls == ["Tk"], "the probe has to construct a window, not just import"
    assert failure is not None and failure.kind == "display"


def test_the_view_marks_a_window_that_never_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """So the entry point can tell it from a TclError raised hours later."""

    pytest.importorskip("tkinter")
    from launchers.statusapp import view

    def refuse():
        raise view.tk.TclError("Can't find a usable init.tcl")

    monkeypatch.setattr(view.tk, "Tk", refuse)

    with pytest.raises(diagnostics.WindowUnavailable):
        view.run(object())


def test_an_import_error_about_something_else_is_not_a_tk_problem() -> None:
    """The view imports its siblings too.

    Answering a broken checkout with a page about tcl/tk would repeat, in a new
    direction, the mistake of answering every failure with one canned cause.
    """

    assert diagnostics.concerns_tk(ImportError("No module named 'server.platform'")) is False
    assert diagnostics.concerns_tk(ModuleNotFoundError("nope", name="launchers.statusapp.controller")) is False


def test_tk_import_errors_are_recognised_by_name_or_by_message() -> None:
    """A failed extension load reports itself in one or the other.

    Which one depends on where in the import machinery it gave up, so both are
    consulted rather than whichever happened to be true on the test machine.
    """

    assert diagnostics.concerns_tk(ModuleNotFoundError("x", name="tkinter")) is True
    assert diagnostics.concerns_tk(ModuleNotFoundError("x", name="tkinter.ttk")) is True
    assert diagnostics.concerns_tk(ImportError("DLL load failed while importing _tkinter")) is True
    assert diagnostics.concerns_tk(ImportError("Can't find a usable init.tcl")) is True

