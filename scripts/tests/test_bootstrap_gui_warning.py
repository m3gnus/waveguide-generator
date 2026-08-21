"""A Python without a working Tk is a warning, never an invalid environment.

tkinter ships with the interpreter rather than with pip, so no reinstall of
this environment can supply it. Failing validation over a missing tkinter would
send every launch into a reinstall that cannot help, and would block --no-gui
mode, which needs no Tk at all. Saying nothing is the other failure: that is
what left a user with an install that reported success and a status window that
never appeared.

Saying the *wrong* thing is the third, and is what these tests were extended
for. The warning used to be a single canned remedy -- "tick tcl/tk and IDLE" --
behind an ``import tkinter`` probe. An import passes on a machine whose Tk is
present but unloadable, and the canned remedy sends a user whose box is already
ticked to tick it again. The probe now opens a real window and prints the
diagnosis it gets back.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap


def _captures(code: int, stderr: str = "", stdout: str = ""):
    return lambda *_args, **_kwargs: SimpleNamespace(
        returncode=code, stderr=stderr, stdout=stdout
    )


def test_validation_never_asks_for_tkinter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The load-bearing invariant: --no-gui has to keep working without Tk."""

    environment = tmp_path / ".venv"
    python = bootstrap._venv_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    bootstrap._install_cli_entrypoint(environment)
    fingerprint = bootstrap._fingerprint()
    (environment / bootstrap.STAMP_NAME).write_text(
        json.dumps({"fingerprint": fingerprint}), encoding="utf-8"
    )
    commands: list[list[str]] = []

    def run(command: list[str], *, quiet: bool = False):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap, "_run", run)

    assert bootstrap._validate(environment, fingerprint)[0] is True
    assert not any("tkinter" in " ".join(command) for command in commands)


def test_a_broken_tk_warns_without_raising(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bootstrap, "_capture", _captures(1, stderr="diagnosis goes here"))

    bootstrap._warn_when_gui_unavailable(Path("python"))

    warning = capsys.readouterr().out
    assert "WARNING" in warning
    assert "installed and works" in warning, "the install is still usable and should say so"


def test_the_diagnosis_is_printed_rather_than_summarised(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of the change: what reaches the transcript is the real cause.

    A fixed remedy composed here could only ever describe one of the causes, and
    described the wrong one for the user whose report prompted this.
    """

    report = "\n".join(
        [
            "tkinter is installed but its Tcl/Tk libraries could not be loaded.",
            "  Interpreter          C:/wg/.venv/Scripts/python.exe",
            "  Python installation  C:/Python313",
        ]
    )
    monkeypatch.setattr(bootstrap, "_capture", _captures(1, stderr=report))

    bootstrap._warn_when_gui_unavailable(Path("python"))

    warning = capsys.readouterr().out
    for line in report.splitlines():
        assert line.strip() in warning


def test_a_working_tk_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bootstrap, "_capture", _captures(0))

    bootstrap._warn_when_gui_unavailable(Path("python"))

    assert capsys.readouterr().out == ""


def test_a_silent_probe_failure_still_says_where_to_look(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A probe that dies without output must not degrade into a bare WARNING."""

    monkeypatch.setattr(bootstrap, "_capture", _captures(1))

    bootstrap._warn_when_gui_unavailable(Path("python"))

    warning = capsys.readouterr().out
    assert str(bootstrap.GUI_DIAGNOSTICS) in warning, "the command to run belongs in the message"


def test_the_probe_asks_the_environment_under_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not the interpreter running the bootstrap, which is a different Python."""

    seen: list[list[str]] = []

    def capture(command: list[str]):
        seen.append(command)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(bootstrap, "_capture", capture)
    interpreter = Path("/somewhere/.venv/bin/python")

    bootstrap._warn_when_gui_unavailable(interpreter)

    assert seen == [[str(interpreter), str(bootstrap.GUI_DIAGNOSTICS)]]


def test_the_probe_runs_by_path_not_as_a_package_module() -> None:
    """``-m launchers.statusapp.diagnostics`` would import the whole server.

    The package ``__init__`` pulls in the controller, and therefore FastAPI, so
    running the probe that way would fail on exactly the broken environments it
    exists to explain.
    """

    assert bootstrap.GUI_DIAGNOSTICS.is_file()
    assert bootstrap.GUI_DIAGNOSTICS.name == "diagnostics.py"


def test_an_unrunnable_interpreter_does_not_fail_the_install(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A warning must never be able to turn a working install into a failure.

    The probe is a real subprocess now, so the interpreter can be missing,
    truncated, or the wrong architecture -- none of which are the status
    window's problem to raise.
    """

    def refuse(_command: list[str]):
        raise OSError(8, "%1 is not a valid Win32 application")

    monkeypatch.setattr(bootstrap, "_capture", refuse)

    bootstrap._warn_when_gui_unavailable(Path("python"))

    assert "could not run" in capsys.readouterr().out

