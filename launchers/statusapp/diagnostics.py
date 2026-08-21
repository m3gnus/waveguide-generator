"""Say which Tk is broken and how, instead of guessing that a box is unticked.

The status window's only failure message used to be a single canned remedy --
"re-run the Python installer and tick tcl/tk and IDLE" -- printed for every
ImportError the view could raise. That remedy is correct for exactly one cause.
A user whose box was already ticked is told to do the thing they have already
done, learns nothing from doing it again, and reasonably concludes that the
application is broken; the report that prompted this module said precisely
that, and ended with the user giving up and passing --no-gui forever.

So replace the guess with evidence. This module names the interpreter that
actually failed -- which is not always the one the user believes they
installed -- separates "this Python has no Tk at all" from "it has Tk that will
not load" from "Tk loaded but could not open a window", and lists the files it
looked for, so a report from a machine nobody can reach is still actionable.

Deliberately standard-library-only and free of intra-package imports: running
``python launchers/statusapp/diagnostics.py`` has to work in an environment too
broken to import the application, because that is the environment that needs
it.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


#: Closes every message. The single most useful fact for someone staring at a
#: failure is that the application is not the thing that is broken.
STILL_WORKS = (
    "Waveguide Generator itself is installed and working. Until the status "
    "window can open, start it with the launcher's --no-gui option."
)


class WindowUnavailable(RuntimeError):
    """Tk loaded, and then refused to open a window.

    Raised by :mod:`launchers.statusapp.view` around the ``Tk()`` call alone,
    so that the entry point can tell this apart from a TclError raised later by
    a running application. Defined here rather than there because this module
    is the one an environment without Tk can still import.
    """


@dataclass(frozen=True)
class TkFailure:
    """One diagnosed cause: what happened, what to do, and what was observed.

    ``evidence`` is carried separately from ``remedy`` because the two have
    different audiences. The remedy is for the user in front of the machine;
    the evidence is for whoever they forward the log to, and belongs in the log
    and the terminal but not in a modal dialog nobody can copy out of.
    """

    #: Which of the three causes this is. Callers act on it: an install
    #: script has no reason to alarm anyone about "display" on a machine
    #: that has no screen, while the other two are faults anywhere.
    kind: str
    headline: str
    remedy: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]

    def summary(self) -> str:
        """The short form: cause and remedy, for a dialog or a first glance."""

        steps = "\n".join(f"  {index}. {step}" for index, step in enumerate(self.remedy, 1))
        return f"{self.headline}\n\nWhat to do:\n{steps}\n\n{STILL_WORKS}"

    def report(self) -> str:
        """The full form: the summary plus everything that was observed."""

        width = max((len(label) for label, _ in self.evidence), default=0)
        rows = "\n".join(f"  {label.ljust(width)}  {value}" for label, value in self.evidence)
        return f"{self.summary()}\n\nWhat was found:\n{rows}"


def _module_origin(name: str) -> Path | None:
    """Where ``name`` would be loaded from, without loading it.

    ``find_spec`` reads the file system rather than executing the module, which
    is the whole point here: ``_tkinter`` failing to *load* is one of the causes
    being diagnosed, so asking where it lives must not re-trigger that failure.
    """

    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError):
        return None
    if spec is None or spec.origin is None or spec.origin in {"built-in", "frozen"}:
        return None
    return Path(spec.origin)


def _tk_shared_libraries(extension: Path | None) -> list[Path]:
    """The Tcl and Tk shared libraries shipped beside ``_tkinter``.

    Matched by glob rather than by name. CPython 3.13 for Windows ships
    ``tcl86t.dll`` and ``tk86t.dll``, but the version rides in the filename and
    moves with the interpreter, so a hard-coded name would report a healthy
    future Python as broken. On Linux these are system libraries that do not
    live here, and finding none is not evidence of anything.
    """

    if extension is None or not extension.parent.is_dir():
        return []
    found: list[Path] = []
    for pattern in ("tcl*.dll", "tk*.dll", "libtcl*", "libtk*"):
        found.extend(sorted(extension.parent.glob(pattern)))
    return found


def _tcl_script_library() -> Path | None:
    """The ``init.tcl`` Tk needs at startup, if it is where Python puts it."""

    for candidate in sorted((Path(sys.base_prefix) / "tcl").glob("tcl*/init.tcl")):
        return candidate
    return None


def _installed_pythons() -> str:
    """What ``py -0p`` reports, for the "but I ticked the box" case.

    On a machine with two Python 3.13 installations -- a python.org one and a
    Microsoft Store one, say -- ticking "tcl/tk and IDLE" in one installer does
    nothing for an environment built from the other, and nothing on screen has
    ever said which one that is. Best effort with a short timeout: the launcher
    already treats a missing ``py`` as normal, so a wedged one cannot be allowed
    to hold up a diagnosis either.
    """

    if os.name != "nt":
        return "(Windows only)"
    try:
        completed = subprocess.run(
            ["py", "-0p"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "(the py launcher is not available)"
    listed = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return "; ".join(listed) if listed else "(the py launcher reported none)"


def _environment_variable(name: str) -> str:
    return os.environ.get(name) or "(not set)"


def is_headless() -> bool:
    """True when this machine has no window server for Tk to draw on.

    A server install has no screen and never will. Reporting that as a
    broken Tk would put a warning in front of every headless install for a
    window nobody there wants, which is how warnings stop being read.
    """

    if os.name == "nt" or sys.platform == "darwin":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _evidence(detail: str) -> tuple[tuple[str, str], ...]:
    """Everything worth knowing about this interpreter's Tk, in report order."""

    package = _module_origin("tkinter")
    extension = _module_origin("_tkinter")
    libraries = _tk_shared_libraries(extension)
    script_library = _tcl_script_library()
    rows = [
        ("Error", detail),
        ("Interpreter", sys.executable or "(unknown)"),
        ("Python", sys.version.split()[0]),
        ("Python installation", sys.base_prefix),
        ("tkinter package", str(package) if package else "NOT FOUND"),
        ("_tkinter extension", str(extension) if extension else "NOT FOUND"),
        (
            "Tcl/Tk libraries",
            ", ".join(path.name for path in libraries) if libraries else "none beside _tkinter",
        ),
        ("Tcl script library", str(script_library) if script_library else "NOT FOUND"),
        ("TCL_LIBRARY", _environment_variable("TCL_LIBRARY")),
        ("TK_LIBRARY", _environment_variable("TK_LIBRARY")),
    ]
    if os.name == "nt":
        rows.append(("Pythons on this machine", _installed_pythons()))
    else:
        rows.append(("DISPLAY", _environment_variable("DISPLAY")))
    return tuple(rows)


def _install_tk_instruction() -> str:
    if os.name == "nt":
        return (
            "Open Settings > Apps > Installed apps, find the Python entry installed at "
            f'"{sys.base_prefix}", choose Modify > Change, and tick "tcl/tk and IDLE".'
        )
    if sys.platform == "darwin":
        return (
            "Install Python 3.13 from python.org, which bundles Tk, or add Tk to the "
            "current one with: brew install python-tk@3.13"
        )
    return "Install the distribution package: on Debian and Ubuntu, sudo apt install python3-tk"


def _repair_instruction() -> str:
    if os.name == "nt":
        return (
            "Repair the Python installation: Settings > Apps > Installed apps > the "
            "Python entry > Modify > Repair."
        )
    return "Reinstall the Tk package belonging to this Python installation."


def _wrong_python_instruction() -> str:
    """The step the old single-remedy message had no way to express."""

    return (
        "If you already ticked that box on a different Python installation, that "
        "is not the one Waveguide Generator uses. It uses the one at "
        f'"{sys.base_prefix}". Delete the .venv folder in the Waveguide '
        "Generator directory and start the launcher again so it rebuilds against "
        "a Python that has Tk."
    )


def diagnose_missing_module(detail: str) -> TkFailure:
    """This interpreter genuinely has no Tk: the old message's one true case."""

    return TkFailure(
        kind="missing",
        headline=(
            "Waveguide Generator could not open its status window because this "
            "Python installation does not include tkinter.\n\n"
            "tkinter is part of Python, not part of Waveguide Generator, so "
            "reinstalling Waveguide Generator cannot add it."
        ),
        remedy=(_install_tk_instruction(), _wrong_python_instruction()),
        evidence=_evidence(detail),
    )


def diagnose_unloadable_module(detail: str) -> TkFailure:
    """Tk is installed but will not load. Re-ticking the box changes nothing.

    Reported as its own cause because the remedy is the opposite of the one
    above: the files are already present, and telling someone to install what
    they have installed is exactly the dead end this module exists to remove.
    """

    libraries = _tk_shared_libraries(_module_origin("_tkinter"))
    steps: list[str] = []
    if os.name == "nt" and not libraries:
        steps.append(
            "The Tcl and Tk libraries are missing from the Python installation at "
            f'"{sys.base_prefix}", so that installation is incomplete or files '
            "were removed after it was installed."
        )
    else:
        steps.append(
            'The "tcl/tk and IDLE" option is already installed here, so ticking it '
            "again will not change anything. Something is stopping the Tcl and Tk "
            "libraries that are already present from loading."
        )
    steps.append(_repair_instruction())
    steps.append(
        "Check whether antivirus or Controlled Folder Access has quarantined or "
        f'blocked files under "{sys.base_prefix}".'
        if os.name == "nt"
        else "Check that the system Tcl and Tk shared libraries are installed and readable."
    )
    if os.environ.get("TCL_LIBRARY") or os.environ.get("TK_LIBRARY"):
        steps.append(
            "TCL_LIBRARY or TK_LIBRARY is set in this environment. Some CAD and "
            "simulation tools set these to their own bundled Tcl, which this "
            "Python cannot use. Clear them and start the launcher again."
        )
    steps.append(_wrong_python_instruction())
    return TkFailure(
        kind="unloadable",
        headline=(
            "Waveguide Generator could not open its status window because tkinter "
            "is installed in this Python but its Tcl/Tk libraries could not be "
            "loaded."
        ),
        remedy=tuple(steps),
        evidence=_evidence(detail),
    )


def diagnose_display_failure(detail: str) -> TkFailure:
    """Tk imported and then refused to open a window. Previously unreported.

    ``view`` imports tkinter at module scope, so this failure arrives from
    ``Tk()`` rather than from an import, and used to land in the entry point's
    generic "stopped with an unexpected error" branch: a TclError about
    init.tcl, carrying no hint that it concerns Tk at all.
    """

    steps: list[str] = []
    if os.environ.get("TCL_LIBRARY") or os.environ.get("TK_LIBRARY"):
        steps.append(
            "TCL_LIBRARY or TK_LIBRARY is set in this environment, and is the "
            "usual cause: it points Tk at a different Tcl, often one bundled with "
            "CAD or simulation software. Clear both and start the launcher again."
        )
    else:
        steps.append(
            "Tk found its libraries but could not initialise them. " + _repair_instruction()
        )
    if os.name == "nt":
        steps.append(
            "If this machine was reached over Remote Desktop, or the launcher runs "
            "as a service account, sign in at the console instead: Tk needs an "
            "interactive desktop session."
        )
    else:
        steps.append(
            "If DISPLAY is unset there is no graphical session here, so the status "
            "window cannot open on this machine at all -- which is expected on a "
            "headless server, and no fault of the installation."
        )
    return TkFailure(
        kind="display",
        headline=(
            "Waveguide Generator could not open its status window because Tk "
            "loaded but failed to create a window."
        ),
        remedy=tuple(steps),
        evidence=_evidence(detail),
    )


def concerns_tk(error: BaseException) -> bool:
    """Whether an ImportError from the view is about Tk at all.

    ``view`` imports its own siblings as well as tkinter, so an ImportError
    caught around it is not automatically a Tk problem, and answering a broken
    checkout with a page about tcl/tk would repeat -- in a new direction -- the
    mistake this module was written to fix. Both the module name and the message
    are consulted: a failed extension load reports itself in one or the other
    depending on where in the import machinery it gave up.
    """

    name = getattr(error, "name", None) or ""
    if name == "tkinter" or name.startswith("tkinter.") or name == "_tkinter":
        return True
    text = str(error).lower()
    return "tkinter" in text or "tcl" in text


def diagnose_import_error(error: BaseException) -> TkFailure:
    """Route an ImportError raised by importing the view to its actual cause."""

    detail = f"{type(error).__name__}: {error}"
    if isinstance(error, ModuleNotFoundError):
        # A ModuleNotFoundError naming a submodule -- tkinter.ttk, say -- means
        # the package is on disk but incomplete, which is an install that will
        # not load rather than an absent one. Ask the file system, not the name.
        if getattr(error, "name", None) in {"tkinter", "_tkinter"} or _module_origin("tkinter") is None:
            return diagnose_missing_module(detail)
    return diagnose_unloadable_module(detail)


def probe() -> TkFailure | None:
    """Try what the status window will try. ``None`` when the window can open.

    Constructing a real :class:`~tkinter.Tk` is the point: importing tkinter
    proves only that the files are present, and the install-time check that did
    exactly that reported success on machines where the window never appeared.
    """

    try:
        import tkinter
    except ImportError as exc:
        return diagnose_import_error(exc)
    except Exception as exc:  # noqa: BLE001 - however it failed, Tk is unusable
        return diagnose_unloadable_module(f"{type(exc).__name__}: {exc}")
    try:
        root = tkinter.Tk()
    except Exception as exc:  # noqa: BLE001 - TclError, but not only
        return diagnose_display_failure(f"{type(exc).__name__}: {exc}")
    try:
        root.destroy()
    except Exception:  # noqa: BLE001 - a window that opened has answered the question
        pass
    return None


def main(argv: list[str] | None = None) -> int:
    """Report this interpreter's Tk, for a user who has been asked to check.

    Runnable by path so that it works in an environment that cannot import the
    application: ``python launchers/statusapp/diagnostics.py``.
    """

    del argv
    failure = probe()
    if failure is None:
        print(f"The status window can open with this Python: {sys.executable}")
        return 0
    if failure.kind == "display" and is_headless():
        # Not a fault: there is no screen here. Say so and succeed, so that
        # a headless install is not warned about a window it cannot use.
        print(
            "Tk is installed and working, but this machine has no graphical "
            "session, so the status window cannot open here. Use the "
            "launcher's --no-gui option."
        )
        return 0
    print(failure.report(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
