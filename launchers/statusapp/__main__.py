"""Run the WG status window, or the original terminal server with --no-gui."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import traceback
from typing import TYPE_CHECKING

from server.platform.paths import app_root

if TYPE_CHECKING:  # pragma: no cover - import cost is why it is deferred at runtime
    from .diagnostics import TkFailure

#: Checked before terminal mode hands over to the server. This is the same file
#: ``server.app.FRONTEND_DIST`` is built from, resolved from this module so the
#: check costs no application import.
FRONTEND_INDEX = app_root() / "frontend" / "dist" / "index.html"

#: Startup failures land here, beside server.log and install.log, so one
#: directory holds the whole story of a bad install.
LOG_FILENAME = "statusapp.log"


def _log_directory() -> Path | None:
    """The application log directory, or None when it cannot be determined.

    Resolved through the server package because that is what owns the platform
    rules; a broken checkout that cannot answer simply gets no path in the
    message, which is better than a wrong one.
    """

    try:
        from server.platform.paths import data_paths

        return data_paths().logs
    except Exception:  # noqa: BLE001 - a missing path must not replace the real failure
        return None


def _log_location() -> str:
    """Where to find the log, named in full rather than described.

    "the Waveguide Generator log folder" is a phrase, not an address, and a
    user who has just been told their application will not start should not
    have to work out where that folder lives on their platform.
    """

    directory = _log_directory()
    if directory is None:
        return f"The full details are in {LOG_FILENAME} in the Waveguide Generator log folder."
    return f"The full details are in: {directory / LOG_FILENAME}"


def _log_startup_failure(message: str) -> None:
    """Append the failure to the application log directory. Best effort.

    Deliberately silent when it cannot write: this runs *because* something has
    already gone wrong, and an exception raised while reporting would replace a
    precise message with a worse one.
    """

    try:
        directory = _log_directory()
        if directory is None:
            return
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with (directory / LOG_FILENAME).open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message.rstrip()}\n")
    except Exception:  # noqa: BLE001 - see docstring
        pass


def _show_startup_failure_dialog(message: str) -> None:
    """Put the failure on screen for a process that has no console.

    Only reached when there is no stderr, which on Windows means the launcher's
    ``start "" pythonw.exe``. Best effort in the same sense as
    :func:`_log_startup_failure`; a platform without a usable dialog still has
    the log file.
    """

    try:
        if sys.platform == "win32":
            import ctypes

            # MB_ICONERROR | MB_SETFOREGROUND. Foreground matters because the
            # launcher's console has already exited, so nothing else in this
            # process tree is around to raise the dialog.
            ctypes.windll.user32.MessageBoxW(0, message, "Waveguide Generator", 0x10 | 0x10000)
        elif sys.platform == "darwin":
            import subprocess

            # The message travels as an argv item rather than interpolated into
            # the AppleScript source, where a quote in a path or an exception
            # string would rewrite the program.
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    "on run argv",
                    "-e",
                    'display dialog (item 1 of argv) with title "Waveguide Generator"'
                    ' with icon stop buttons {"OK"} default button "OK"',
                    "-e",
                    "end run",
                    "--",
                    message,
                ],
                check=False,
            )
    except Exception:  # noqa: BLE001 - see docstring
        pass


def _report_startup_failure(
    message: str, *, detail: str | None = None, dialog: str | None = None
) -> None:
    """Deliver a failure that happens before the status window exists.

    The Windows launcher starts this module through ``start "" pythonw.exe``,
    which hands the process no console: ``sys.stdout`` and ``sys.stderr`` are
    both None, and ``print`` silently returns when its target is None. Every
    diagnostic here used to be written to stderr alone, so the default GUI path
    could only ever fail *invisibly* -- launch-wg.bat has already exited 0 by
    the time this runs, and ``start`` reports nothing but its own spawn
    failures. The user was left with two console windows flashing and no
    explanation anywhere on the machine.

    So write to each channel that can survive that: stderr when there is one,
    the log directory always, and a dialog when there is no console to read.
    ``detail`` carries a traceback to the log without putting it in a dialog.
    ``dialog`` shortens what the dialog shows when ``message`` is a full
    diagnostic report: a modal box cannot be scrolled, copied out of, or read
    beside the thing it describes, so the evidence goes to the log and the
    terminal while only the cause and the remedy go on screen.
    """

    if sys.stderr is not None:
        print(message, file=sys.stderr)
    _log_startup_failure(message if detail is None else f"{message}\n{detail}")
    if sys.stderr is None:
        _show_startup_failure_dialog(message if dialog is None else dialog)


def _report_failure_with_evidence(failure: "TkFailure", *, detail: str | None = None) -> None:
    """Deliver a diagnosed Tk failure to each channel at the right length.

    The evidence table is what makes a report from an unreachable machine
    actionable, so it goes to the terminal and to the log. It is also unreadable
    in a modal dialog that cannot be scrolled or copied out of, so the dialog
    gets the cause, the remedy, and the path to the log instead.
    """

    _report_startup_failure(
        failure.report(),
        detail=detail,
        dialog=f"{failure.summary()}\n\n{_log_location()}",
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    window_requested = "--window" in arguments
    browser_requested = "--browser" in arguments
    arguments = [argument for argument in arguments if argument not in {"--window", "--browser"}]
    if window_requested and browser_requested:
        _report_startup_failure("Choose only one display mode: --window or --browser.")
        return 2
    if "--no-gui" in arguments:
        arguments.remove("--no-gui")
        # The status window refuses to start the backend when the interface is
        # missing (controller.poll). Terminal mode bypasses that guard by going
        # straight to the server, which then raises a starlette RuntimeError
        # from deep inside create_app -- "Directory '.../frontend/dist' does not
        # exist" -- and a traceback reads as a broken application rather than an
        # unbuilt one. Refuse in the same words the status window uses, so the
        # two modes cannot disagree about what is wrong.
        if not FRONTEND_INDEX.is_file():
            from .controller import missing_frontend_reason

            _report_startup_failure(
                "Waveguide Generator did not start because the interface is "
                f"missing: {missing_frontend_reason()}"
            )
            return 1

        from launch.serve import main as serve

        return serve(arguments)

    if window_requested:
        from launchers.desktop import main as desktop_main

        return desktop_main(arguments)

    # Importing the controller pulls in the server package and the repository
    # scripts, so it fails for a broken checkout as readily as for a broken
    # environment. Unguarded, that traceback goes to the same nowhere the
    # tkinter message used to.
    try:
        from .controller import StatusController

        controller = StatusController(server_args=arguments)
    except Exception as exc:  # noqa: BLE001 - reported through every channel below
        _report_startup_failure(
            "Waveguide Generator could not start because one of its own modules "
            f"failed to load: {type(exc).__name__}: {exc}\n\n"
            f"{_log_location()}\n\n"
            "Run the launcher with --no-gui to see this in a terminal.",
            detail=traceback.format_exc(),
        )
        return 1

    try:
        from .view import run
    except ImportError as exc:
        # One canned remedy used to be printed for every ImportError here: "tick
        # tcl/tk and IDLE". It is right for exactly one cause, and a user whose
        # box was already ticked was sent to re-do the thing they had done.
        # diagnostics separates the causes and names the interpreter that failed.
        from .diagnostics import concerns_tk, diagnose_import_error

        # The view imports its siblings too, so not every ImportError here is a
        # Tk problem. Answering a broken checkout with a page about tcl/tk would
        # repeat the same mistake in a new direction.
        if concerns_tk(exc):
            _report_failure_with_evidence(diagnose_import_error(exc))
        else:
            _report_startup_failure(
                "Waveguide Generator could not open its status window because one "
                f"of its own modules failed to load: {type(exc).__name__}: {exc}\n\n"
                f"{_log_location()}\n\n"
                "Run the launcher with --no-gui to see this in a terminal.",
                detail=traceback.format_exc(),
            )
        return 1

    try:
        return run(controller)
    except Exception as exc:  # noqa: BLE001 - reported through every channel below
        # The view starts the backend from a thread in its constructor, so an
        # exception thrown after that point would otherwise leave a headless
        # server holding the port with no window to stop it.
        try:
            controller.close()
        except Exception:  # noqa: BLE001 - the original failure is the one to report
            pass
        # tkinter is imported at view module scope, so a Tk that loaded and then
        # refused to open a window arrives here rather than above. Left to the
        # generic branch it reads as "an unexpected error" over a TclError about
        # init.tcl, which names neither Tk nor anything the user can act on.
        from .diagnostics import WindowUnavailable, diagnose_display_failure

        if isinstance(exc, WindowUnavailable):
            # The TclError underneath is what carries the useful text;
            # the wrapper exists only to mark where it was raised.
            cause = exc.__cause__ or exc
            _report_failure_with_evidence(
                diagnose_display_failure(f"{type(cause).__name__}: {cause}"),
                detail=traceback.format_exc(),
            )
            return 1
        _report_startup_failure(
            "Waveguide Generator stopped with an unexpected error: "
            f"{type(exc).__name__}: {exc}\n\n{_log_location()}",
            detail=traceback.format_exc(),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
