"""Terminal mode must refuse a missing interface as clearly as the GUI does.

``--no-gui`` hands straight to ``launch.serve``, bypassing the status window's
own guard. Without a check here the server raises a starlette RuntimeError from
inside ``create_app`` -- "Directory '.../frontend/dist' does not exist" -- and a
traceback several frames deep reads as a broken application rather than an
unbuilt one. This is platform-independent: the same hole existed on macOS,
Windows and Linux.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

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


def test_window_mode_is_delegated_without_forwarding_the_display_flag(monkeypatch) -> None:
    seen: list[list[str]] = []
    module = ModuleType("launchers.desktop")
    module.main = lambda arguments: seen.append(arguments) or 7
    monkeypatch.setitem(sys.modules, "launchers.desktop", module)

    assert entrypoint.main(["--window", "--port", "3199"]) == 7
    assert seen == [["--port", "3199"]]


def test_browser_flag_keeps_the_status_window_default(monkeypatch) -> None:
    controllers: list[tuple[str, ...]] = []
    controller_module = ModuleType("launchers.statusapp.controller")

    class Controller:
        def __init__(self, *, server_args):
            controllers.append(tuple(server_args))

    controller_module.StatusController = Controller
    view_module = ModuleType("launchers.statusapp.view")
    view_module.run = lambda _controller: 0
    monkeypatch.setitem(sys.modules, "launchers.statusapp.controller", controller_module)
    monkeypatch.setitem(sys.modules, "launchers.statusapp.view", view_module)

    assert entrypoint.main(["--browser", "--port", "3199"]) == 0
    assert controllers == [("--port", "3199")]


def test_no_gui_consumes_desktop_display_flags(monkeypatch, tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "FRONTEND_INDEX", index)
    seen: list[list[str]] = []
    monkeypatch.setattr("launch.serve.main", lambda arguments: seen.append(arguments) or 0)

    assert entrypoint.main(["--window", "--no-gui", "--port", "3199"]) == 0
    assert seen == [["--port", "3199"]]


def test_help_prints_usage_and_starts_nothing(monkeypatch, capsys) -> None:
    """The one flag guaranteed never to open a window, or start a server."""

    def _must_not_run(_arguments):  # pragma: no cover - the point is that it is not called
        raise AssertionError("--help must not start the server")

    monkeypatch.setattr("launch.serve.main", _must_not_run)

    assert entrypoint.main(["--help"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("usage: waveguide-generator")
    assert "display mode" in printed


def test_usage_is_titled_by_the_command_users_type(capsys) -> None:
    """It said ``desktop.py``: a file in the bundle, not a command anyone ran.

    argparse derives ``prog`` from ``sys.argv[0]``, which under the installed
    launcher is whichever module the interpreter was handed.
    """

    assert entrypoint.main(["--help"]) == 0
    assert "desktop.py" not in capsys.readouterr().out


def test_an_unknown_flag_is_named_and_refused(monkeypatch, capsys) -> None:
    """``--no-brwoser`` used to be forwarded, and hung a window instead."""

    def _must_not_run(_arguments):  # pragma: no cover
        raise AssertionError("an unrecognised argument must not reach the server")

    monkeypatch.setattr("launch.serve.main", _must_not_run)

    assert entrypoint.main(["--no-brwoser"]) == 2
    reported = capsys.readouterr().err
    assert "--no-brwoser" in reported
    assert "usage: waveguide-generator" in reported


def test_an_abbreviated_display_flag_is_refused_rather_than_forwarded(capsys) -> None:
    """argparse would accept ``--wind``; the verbatim filter would not strip it.

    It matches the display flags by name, so an abbreviation argparse resolved
    would survive into the server's argument list as something the server has
    never heard of. ``allow_abbrev=False`` removes the whole class.
    """

    assert entrypoint.main(["--wind"]) == 2
    assert "--wind" in capsys.readouterr().err


def test_the_launcher_accepts_exactly_what_the_server_accepts() -> None:
    """One option surface, two parsers. Two definitions would drift silently.

    They already had: the launcher had no definition at all, so every server
    option and every typo were the same thing to it.
    """

    from launch.serve import build_parser as server_parser

    def options(parser) -> set[str]:
        return {
            option
            for action in parser._actions
            for option in action.option_strings
            if option != "-h" and option != "--help"
        }

    launcher = options(entrypoint.build_parser())
    server = options(server_parser())

    assert server <= launcher, "the launcher must not reject an argument the server takes"
    assert launcher - server == set(entrypoint.DISPLAY_FLAGS)


def test_the_display_flags_are_the_ones_the_launcher_consumes() -> None:
    """The filter that builds the server's argument list matches by name."""

    _options, forwarded = entrypoint.parse_arguments(
        ["--window", "--browser", "--no-gui", "--port", "3199"]
    )
    assert forwarded == ["--port", "3199"]


def test_a_bad_option_value_is_reported_rather_than_raised(capsys) -> None:
    """A non-integer port is the server's rule, enforced before a window opens."""

    assert entrypoint.main(["--port", "three thousand"]) == 2
    assert "--port" in capsys.readouterr().err


class _Stderr:
    """A stderr, with a say in whether anybody is reading it."""

    def __init__(self, *, tty: bool) -> None:
        self._tty = tty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self._tty

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class _WindowStation:
    """Every way this application can put a window on a screen.

    Patched at the *transport*, deliberately: a guard on
    ``_show_startup_failure_dialog`` proves only that one spelling was not
    called, and the defect this pins was a call that reached the real
    ``subprocess.run``. ``ctypes.windll`` is stubbed rather than skipped so the
    Windows branch is exercised on every host -- it does not exist off Windows,
    so without a stub that branch raises ``AttributeError``, is swallowed by
    the reporter's own ``except Exception``, and a broken assertion would pass.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def run(self, command, **_kwargs):
        self.calls.append(("run", command))
        return subprocess.CompletedProcess(command, 0, "", "")

    def popen(self, command, **_kwargs):
        self.calls.append(("popen", command))
        return None

    def message_box(self, _handle, text, _title, _flags):
        self.calls.append(("MessageBoxW", text))
        return 1


@pytest.fixture()
def window_station(monkeypatch: pytest.MonkeyPatch, real_startup_dialogs: None) -> _WindowStation:
    """Let the real dialog code run, with nothing behind it that can open."""

    station = _WindowStation()
    monkeypatch.setattr(subprocess, "run", station.run)
    monkeypatch.setattr(subprocess, "Popen", station.popen)
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(MessageBoxW=station.message_box)),
        raising=False,
    )
    # The Linux branch takes the first of zenity/kdialog/xmessage it finds.
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    return station


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
@pytest.mark.parametrize(
    "stderr", [None, _Stderr(tty=False)], ids=["no-stderr", "redirected-stderr"]
)
def test_no_gui_refuses_a_missing_interface_without_opening_anything(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    window_station: _WindowStation,
    platform: str,
    stderr: object,
) -> None:
    """``--no-gui`` says "opening no window of our own". Refusals included.

    The dialog test inside ``_report_startup_failure`` widened from "there is
    no ``sys.stderr``" to "nobody is reading ``sys.stderr``", which a
    redirected terminal run, a service and a CI step all satisfy -- so this
    refusal started opening a window in the one mode that had promised not to,
    and on Windows a modal one that waits for a person who may not be there.

    Both stderr shapes are covered because they are different promises: a
    redirected run must still get the message on its stream, and a run with no
    stream at all must still get it in the log. Neither may open anything.
    """

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(entrypoint, "FRONTEND_INDEX", tmp_path / "frontend" / "dist" / "index.html")

    def _must_not_run(_arguments):  # pragma: no cover - the point is that it is not called
        raise AssertionError("the server must not be started without an interface")

    monkeypatch.setattr("launch.serve.main", _must_not_run)

    assert entrypoint.main(["--no-gui"]) == 1
    assert window_station.calls == [], "terminal mode opened a window"

    # ...and the message is still delivered, on every channel that opens nothing.
    logged = (tmp_path / "logs" / entrypoint.LOG_FILENAME).read_text(encoding="utf-8")
    assert "frontend/dist missing" in logged
    if stderr is not None:
        assert "frontend/dist missing" in "".join(stderr.written)


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_a_contradictory_display_mode_with_no_gui_stays_in_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    window_station: _WindowStation,
    platform: str,
) -> None:
    """The refusal comes before the branch that honours ``--no-gui``.

    ``waveguide-generator --window --browser --no-gui`` is a contradiction, and
    answering it is not a reason to break the one promise the command line did
    make unambiguously.
    """

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "stderr", _Stderr(tty=False))

    assert entrypoint.main(["--window", "--browser", "--no-gui"]) == 2
    assert window_station.calls == []
    assert "only one display mode" in (
        tmp_path / "logs" / entrypoint.LOG_FILENAME
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("platform", "transport"), [("darwin", "popen"), ("win32", "MessageBoxW"), ("linux", "popen")]
)
def test_a_graphical_start_still_puts_its_failure_on_screen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    window_station: _WindowStation,
    platform: str,
    transport: str,
) -> None:
    """The positive half, so the guards above cannot pass by breaking delivery.

    Without this, a stubbed transport that never records anything would satisfy
    every assertion above while the application had silently stopped reporting
    to the only user who cannot see a terminal.
    """

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "stderr", _Stderr(tty=False))

    entrypoint._report_startup_failure("the interface is missing")

    assert [kind for kind, _payload in window_station.calls] == [transport]
