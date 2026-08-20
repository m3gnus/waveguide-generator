"""A Python without tkinter is a warning, never an invalid environment.

tkinter ships with the interpreter rather than with pip, so no reinstall of
this environment can supply it. Failing validation over a missing tkinter would
send every launch into a reinstall that cannot help, and would block --no-gui
mode, which needs no Tk at all. Saying nothing is the other failure: that is
what left a user with an install that reported success and a status window that
never appeared.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap


def _returns(code: int):
    return lambda *_args, **_kwargs: SimpleNamespace(returncode=code)


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


def test_a_missing_tkinter_warns_without_raising(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bootstrap, "_run", _returns(1))

    bootstrap._warn_when_gui_unavailable(Path("python"))

    warning = capsys.readouterr().out
    assert "WARNING" in warning
    assert "--no-gui" in warning, "the install is still usable and should say so"
    assert "not to Waveguide" in warning, "reinstalling the app cannot add tkinter"


def test_a_working_tkinter_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bootstrap, "_run", _returns(0))

    bootstrap._warn_when_gui_unavailable(Path("python"))

    assert capsys.readouterr().out == ""


def test_the_probe_asks_the_environment_under_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not the interpreter running the bootstrap, which is a different Python."""

    seen: list[list[str]] = []

    def run(command: list[str], *, quiet: bool = False):
        seen.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap, "_run", run)
    interpreter = Path("/somewhere/.venv/bin/python")

    bootstrap._warn_when_gui_unavailable(interpreter)

    assert seen == [[str(interpreter), "-c", "import tkinter"]]
