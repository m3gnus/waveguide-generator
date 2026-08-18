"""Run the WG status window, or the original terminal server with --no-gui."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import traceback

#: Checked before terminal mode hands over to the server. This is the same file
#: ``server.app.FRONTEND_DIST`` is built from, resolved from this module so the
#: check costs no application import.
FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"

#: Startup failures land here, beside server.log and install.log, so one
#: directory holds the whole story of a bad install.
LOG_FILENAME = "statusapp.log"


def _tkinter_repair_hint() -> str:
    """How to obtain tkinter on this platform.

    Worth spelling out: tkinter ships with the interpreter rather than with
    this application's packages, so the obvious remedy -- reinstalling
    Waveguide Generator -- cannot supply it, and a user who tries that learns
    nothing except that the installer succeeds again.
    """

    if sys.platform == "win32":
        return (
            "tkinter is part of the Python installation, not of Waveguide "
            "Generator, so reinstalling the application cannot add it. Re-run "
            "the Python 3.13 installer, choose Modify, and tick "
            '"tcl/tk and IDLE".'
        )
    if sys.platform == "darwin":
        return (
            "tkinter is part of the Python installation, not of Waveguide "
            "Generator, so reinstalling the application cannot add it. Use the "
            "python.org build of Python 3.13, or add Tk to the current one "
            "with: brew install python-tk@3.13"
        )
    return (
        "tkinter is part of the Python installation, not of Waveguide "
        "Generator, so reinstalling the application cannot add it. Install the "
        "distribution package instead -- on Debian and Ubuntu: "
        "sudo apt install python3-tk"
    )


def _log_startup_failure(message: str) -> None:
    """Append the failure to the application log directory. Best effort.

    Deliberately silent when it cannot write: this runs *because* something has
    already gone wrong, and an exception raised while reporting would replace a
    precise message with a worse one.
    """

    try:
        from server.platform.paths import data_paths

        directory = data_paths().logs
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


def _report_startup_failure(message: str, *, detail: str | None = None) -> None:
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
    """

    if sys.stderr is not None:
        print(message, file=sys.stderr)
    _log_startup_failure(message if detail is None else f"{message}\n{detail}")
    if sys.stderr is None:
        _show_startup_failure_dialog(message)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
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
            "The full traceback is in statusapp.log in the Waveguide Generator "
            "log folder. Run the launcher with --no-gui to see it in a terminal.",
            detail=traceback.format_exc(),
        )
        return 1

    try:
        from .view import run
    except ImportError as exc:
        _report_startup_failure(
            "Waveguide Generator could not open its status window because "
            f"tkinter is unavailable: {exc}\n\n"
            f"{_tkinter_repair_hint()}\n\n"
            "Until then the launcher still works with --no-gui for terminal mode."
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
        _report_startup_failure(
            "Waveguide Generator stopped with an unexpected error: "
            f"{type(exc).__name__}: {exc}\n\n"
            "The full traceback is in statusapp.log in the Waveguide Generator "
            "log folder.",
            detail=traceback.format_exc(),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
