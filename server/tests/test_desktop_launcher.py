"""Native-window contracts without importing the real GUI toolkit."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from launchers import desktop
from launchers.full_installer import UpdateHandoffError
from launchers.statusapp.controller import (
    LampStatus,
    ServiceState,
    StatusSnapshot,
)


def _request(version: str = "1.2.3") -> SimpleNamespace:
    """What ``take_update_request`` returns: the window reads only its version."""

    return SimpleNamespace(version=version)


#: Windows resolves the desktop window through pythonnet and a WebView2 registry
#: probe, and ``_prepare_windows_webview`` reads the real ``sys.platform``, so on
#: a Windows runner both of those are the genuine article. A machine without
#: either -- which includes any CI runner that ships no Edge WebView2 -- sends
#: every happy-path test here down the browser fallback, and that fallback ends
#: in a modal MessageBoxW with nobody to select OK. These say "assume the window
#: is available", which is what a test about window contracts means; the three
#: tests that exercise the fallback pass their own values instead.
WINDOWS_WEBVIEW_READY = {
    "pythonnet_loader": lambda: object(),
    "webview2_probe": lambda: True,
}


@pytest.fixture(autouse=True)
def _isolate_qt_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the launcher's ``QT_QPA_PLATFORM`` pin inside the test that caused it.

    ``main`` pins the platform plugin by writing to the real ``os.environ`` --
    it has to, because the in-process Qt reads it through ``getenv`` and the
    probe's child inherits it. Left alone that write escapes into the rest of
    the session, and the next test to assert the variable is *unset* would pass
    or fail depending on which tests ran before it.
    """

    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)


def _snapshot(state: ServiceState, url: str = "http://127.0.0.1:3199/") -> StatusSnapshot:
    return StatusSnapshot(
        backend=LampStatus(state, "backend ready"),
        frontend=LampStatus(state, "frontend ready"),
        url=url,
        pid=123 if state is not ServiceState.ERROR else None,
        exit_code=None,
    )


@dataclass
class StubController:
    start_snapshot: StatusSnapshot = _snapshot(ServiceState.STARTING)
    poll_snapshot: StatusSnapshot = _snapshot(ServiceState.OK)
    starts: int = 0
    polls: int = 0
    closes: int = 0
    update_requests: list[object | None] = field(default_factory=list)
    launched: list[object] = field(default_factory=list)
    handoff_error: str | None = None
    #: Every callback the window has registered for backend loss. The real
    #: controller answers this with a thread parked in ``Popen.wait()``; a test
    #: calls the callback directly to stand in for the child dying.
    watchers: list[Callable[[StatusSnapshot], None]] = field(default_factory=list)
    #: Every reason the window gave the server for calling off its restart.
    released: list[str] = field(default_factory=list)

    def release_update_restart(self, reason: str) -> bool:
        self.released.append(reason)
        return True

    @property
    def url(self) -> str:
        return self.poll_snapshot.url

    def watch_backend(
        self, on_lost: Callable[[StatusSnapshot], None]
    ) -> Callable[[StatusSnapshot], None]:
        if on_lost not in self.watchers:
            self.watchers.append(on_lost)
        return on_lost

    def start(self) -> StatusSnapshot:
        self.starts += 1
        return self.start_snapshot

    def poll(self) -> StatusSnapshot:
        self.polls += 1
        return self.poll_snapshot

    def close(self) -> StatusSnapshot:
        self.closes += 1
        return _snapshot(ServiceState.STOPPED)

    def take_update_request(self) -> object | None:
        return self.update_requests.pop(0) if self.update_requests else None

    def launch_update(self, tag: object) -> None:
        if self.handoff_error is not None:
            raise UpdateHandoffError(self.handoff_error)
        self.launched.append(tag)


class ClosedEvent:
    def wait(self, _timeout: float) -> bool:
        return True


def _stub_webview() -> tuple[ModuleType, list[tuple[tuple[object, ...], dict[str, object]]]]:
    created: list[tuple[tuple[object, ...], dict[str, object]]] = []
    stub = ModuleType("webview")
    stub.settings = {"ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True}

    def create_window(*args: object, **kwargs: object) -> object:
        created.append((args, kwargs))
        return SimpleNamespace(events=SimpleNamespace(closed=ClosedEvent()))

    #: Every keyword ``webview.start`` was given, so a test can see which
    #: backend was named without a second stub. pywebview's own signature takes
    #: ``gui`` beside ``func``.
    stub.start_calls = []

    def start(*, func, **options: object) -> None:
        stub.start_calls.append(options)
        func()

    stub.create_window = create_window
    stub.start = start
    return stub, created


def test_primary_window_uses_controller_url_and_closing_stops_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController()
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)

    result = desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run()  # type: ignore[arg-type]

    assert result == 0
    assert controller.starts == 1
    assert controller.closes == 1
    assert webview.settings["ALLOW_DOWNLOADS"] is True
    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert created[0][0] == (desktop.WINDOW_TITLE, controller.url)
    assert created[0][1]["width"] == 1440
    assert created[0][1]["height"] == 900
    assert created[0][1]["min_size"] == (1100, 700)
    assert created[0][1]["js_api"] is not None


def test_javascript_api_rejects_foreign_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = StubController()
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    window = desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY)  # type: ignore[arg-type]
    assert window.run() == 0
    api = created[0][1]["js_api"]

    with pytest.raises(ValueError, match="same-origin"):
        api.open_window("https://example.com/foreign")  # type: ignore[attr-defined]
    assert len(created) == 1

    api.open_window("/api/jobs/one/log")  # type: ignore[attr-defined]
    assert created[1][0] == (
        desktop.WINDOW_TITLE,
        "http://127.0.0.1:3199/api/jobs/one/log",
    )


def test_controller_startup_error_is_reported_before_a_window_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController(start_snapshot=_snapshot(ServiceState.ERROR))
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 1  # type: ignore[arg-type]
    assert created == []
    assert controller.closes == 1
    assert "Backend:" in reported[0]
    assert "Frontend:" in reported[0]


def test_missing_pywebview_reports_the_pip_requirements_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController()
    monkeypatch.delitem(sys.modules, "webview", raising=False)

    def missing(_name: str) -> ModuleType:
        raise ModuleNotFoundError("No module named 'webview'", name="webview")

    monkeypatch.setattr(desktop.importlib, "import_module", missing)
    reported: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        desktop,
        "_report_startup_failure",
        lambda message, *, detail=None: reported.append((message, detail)),
    )

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 1  # type: ignore[arg-type]
    message = reported[0][0]
    assert "pywebview" in message
    assert "pip" in message
    assert "server/requirements-runtime.txt" in message
    assert controller.closes == 1


def test_missing_windows_webview2_reports_evergreen_and_opens_browser_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController()
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    reported: list[tuple[str, str | None]] = []
    opened: list[str] = []
    monkeypatch.setattr(
        desktop,
        "_report_startup_failure",
        lambda message, *, detail=None: reported.append((message, detail)),
    )

    result = desktop.DesktopWindow(
        controller,  # type: ignore[arg-type]
        poll_interval=0,
        pythonnet_loader=lambda: object(),
        webview2_probe=lambda: False,
        browser_fallback=opened.append,
    ).run()

    assert result == 0
    assert created == []
    assert opened == [controller.url]
    assert "Microsoft Edge WebView2 Evergreen Runtime" in reported[0][0]
    assert "default browser" in reported[0][0]
    assert controller.closes == 1


def test_windows_pythonnet_initialization_error_uses_the_same_visible_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController()
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    reported: list[str] = []
    opened: list[str] = []
    monkeypatch.setattr(
        desktop,
        "_report_startup_failure",
        lambda message, *, detail=None: reported.append(message),
    )

    def broken_pythonnet() -> object:
        raise ImportError("No module named 'clr_loader'")

    result = desktop.DesktopWindow(
        controller,  # type: ignore[arg-type]
        poll_interval=0,
        pythonnet_loader=broken_pythonnet,
        webview2_probe=lambda: True,
        browser_fallback=opened.append,
    ).run()

    assert result == 0
    assert created == []
    assert opened == [controller.url]
    assert "pythonnet could not load" in reported[0]
    assert "Evergreen Runtime" in reported[0]


def _completed(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["python", "-c", "..."], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_linux_opens_the_native_window_when_qt_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The window is offered on Linux, not refused before it is attempted.

    0.3.1 short-circuited every Linux launch into the status window with a
    notice saying the native window was "unavailable on Linux in this
    release". The window code was already platform-generic; only pywebview's
    Linux backend was missing from the requirements.
    """

    controller = StubController()
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop, "_linux_window_blocker", lambda: None)
    monkeypatch.setattr(
        desktop.DesktopWindow, "_name_linux_application", lambda self: None
    )
    windows: list[StubController] = []
    monkeypatch.setattr(
        desktop, "StatusController", lambda *, server_args, **_kwargs: windows.append(server_args) or controller
    )

    assert desktop.main(["--port", "3199"]) == 0
    assert windows == [["--port", "3199"]]
    assert created[0][0] == (desktop.WINDOW_TITLE, controller.url)
    # Named rather than left to pywebview's own GTK-first order: Qt is the
    # backend this application ships and tests against.
    assert webview.start_calls == [{"gui": "qt"}]


def test_linux_falls_back_to_the_status_window_and_says_which_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine whose Qt cannot start must reach the interface anyway.

    The status window rather than a bare browser: it owns the server for as
    long as it is open, where ``_open_browser_fallback`` returns at once on
    Linux and would let ``run``'s ``finally`` stop the backend under the
    interface it had just opened.
    """

    from launchers.statusapp import __main__ as status_entrypoint

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(
        desktop,
        "_linux_window_blocker",
        lambda: 'a Qt check exited with -6:\nlibxcb-cursor.so.0: cannot open shared object file',
    )
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    seen: list[list[str]] = []
    monkeypatch.setattr(status_entrypoint, "main", lambda arguments: seen.append(arguments) or 7)

    assert desktop.main(["--port", "3199"]) == 7
    assert seen == [["--browser", "--port", "3199"]]
    assert "libxcb-cursor.so.0" in reported[0], "the cause has to survive to the user"
    assert "sudo dnf install" in reported[0] and "sudo pacman" in reported[0]


def test_a_failure_from_the_applications_menu_is_put_on_screen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, startup_dialogs: list[str],
) -> None:
    """A desktop entry's stderr is real and goes to the journal.

    ``_report_startup_failure`` used to show a dialog only when there was no
    stderr at all, which is Windows' ``pythonw`` case and not this one -- so a
    Linux user who launched from the menu would have got a different
    application from the one macOS gives them, with the explanation filed
    somewhere they will never look.

    **Exactly once.** The console test moved *into* ``_report_startup_failure``
    and the fallback kept its own copy of it, so both fired on precisely the
    hosts this feature exists for. Nothing here stubs the reporter, because
    the reporter is the thing under test: a stub would have hidden the second
    dialog, which is how it survived review.
    """

    from launchers.statusapp import __main__ as status_entrypoint

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop, "_linux_window_blocker", lambda: "no Qt here")
    monkeypatch.setattr(status_entrypoint, "main", lambda _arguments: 0)

    monkeypatch.setattr(status_entrypoint, "_console_is_readable", lambda: True)
    assert desktop.main([]) == 0
    assert startup_dialogs == [], "a terminal already showed it"

    monkeypatch.setattr(status_entrypoint, "_console_is_readable", lambda: False)
    assert desktop.main([]) == 0
    assert len(startup_dialogs) == 1, "one failure, one dialog"
    assert "no Qt here" in startup_dialogs[0]


def test_the_linux_check_is_skipped_when_the_user_has_chosen_a_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PYWEBVIEW_GUI`` is pywebview's override and this must not overrule it."""

    controller = StubController()
    webview, _created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setenv("PYWEBVIEW_GUI", "gtk")
    monkeypatch.setattr(
        desktop.DesktopWindow, "_name_linux_application", lambda self: None
    )

    def _must_not_probe() -> str | None:  # pragma: no cover - the point is the absence
        raise AssertionError("a chosen backend must not be probed for Qt")

    monkeypatch.setattr(desktop, "_linux_window_blocker", _must_not_probe)
    monkeypatch.setattr(desktop, "StatusController", lambda *, server_args, **_kwargs: controller)

    assert desktop.main([]) == 0
    # ...and pywebview is left to honour the variable itself.
    assert webview.start_calls == [{}]


def test_a_machine_with_no_display_is_answered_without_starting_an_interpreter() -> None:
    """No display means nothing for Qt to connect to, and nothing to pay for."""

    def _must_not_run(*_args: object, **_kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("the display check comes first")

    reason = desktop._linux_window_blocker(environ={}, runner=_must_not_run)

    assert reason is not None
    assert "DISPLAY" in reason and "WAYLAND_DISPLAY" in reason


def test_the_qt_check_runs_out_of_process_because_qt_aborts_in_it() -> None:
    """Constructing a QApplication is the call that can call ``qFatal``.

    ``qFatal`` is ``abort()``: no ``try`` in this process could catch it, and
    the application would die with no window and no message rather than fall
    back. So the check has to be a child interpreter, and the program it runs
    has to build a real QApplication -- importing the module proves nothing
    about the platform plugin.
    """

    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        commands.append(command)
        return _completed(0)

    assert (
        desktop._linux_window_blocker(
            environ={"WAYLAND_DISPLAY": "wayland-0"},
            executable="/bundle/runtime/bin/python3.13",
            runner=runner,
        )
        is None
    )
    assert commands[0][:2] == ["/bundle/runtime/bin/python3.13", "-c"]
    assert "QApplication(" in commands[0][2]
    # QtWebEngine is where the interface is actually drawn, and its shared
    # libraries are a separate way for this to fail.
    assert "QtWebEngineWidgets" in commands[0][2]


def test_the_qt_check_renders_a_page_rather_than_stopping_at_an_application() -> None:
    """QtWebEngine is Chromium: the interface is drawn by a second process.

    A machine can construct a perfectly good ``QApplication`` and then produce
    a blank window, because the render process needs an unprivileged user
    namespace and seccomp-bpf that some kernels and container images do not
    give it (Qt WebEngine Platform Notes). Only loading a page starts that
    process, so only loading a page answers the question the fallback is
    asking.
    """

    program = desktop._linux_qt_probe(deadline_ms=1234)

    # It is built by ``str.format``, so a brace added to it in good faith
    # would produce a program that is not Python at all -- and the failure
    # would read exactly like a machine with no Qt.
    ast.parse(program)
    assert "setHtml(" in program
    assert "loadFinished" in program
    # Bounded: a render process that never answers must end the check rather
    # than hold the launch on an event loop.
    assert "QTimer.singleShot(1234" in program
    assert "exec_()" in program
    # The view goes before the application does. Collected the other way round
    # -- at interpreter shutdown, after QApplication -- QtWebEngine is a known
    # crash at exit, which would come back as a failed probe from a machine
    # where the window works.
    assert program.index("del view") < program.index("sys.exit(verdict)")


def test_the_qt_check_never_buys_a_pass_by_disabling_the_sandbox() -> None:
    """A probe that switched off what was failing would be worse than none.

    It would report success and send the application into exactly the blank
    window the check exists to predict.
    """

    program = desktop._linux_qt_probe()

    for escape in ("--no-sandbox", "QTWEBENGINE_DISABLE_SANDBOX", "--disable-gpu-sandbox"):
        assert escape not in program
    # Nor may the probe decide the display protocol on its own: a QT_QPA_PLATFORM
    # set *inside* the program would answer a question about a session nobody is
    # running. The launcher does choose the plugin, but it chooses it in the
    # parent, before this child inherits the environment -- so the probe still
    # reports on the session the window will open in. That ordering is the
    # contract, and it is tested directly in
    # ``test_linux_pins_the_qt_platform_before_the_probe_reads_it``.
    assert "QT_QPA_PLATFORM" not in program
    assert "environ" not in program, "the probe reads its environment, it does not write it"


def test_an_explicitly_chosen_qt_backend_is_still_checked() -> None:
    """``PYWEBVIEW_GUI=qt`` is the same renderer, and the same exposure.

    Skipping the probe for it would withhold the diagnosis from the one user
    who was explicit about what they wanted.
    """

    assert desktop._linux_backend_is_qt({"PYWEBVIEW_GUI": "qt"}) is True
    assert desktop._linux_backend_is_qt({}) is True
    assert desktop._linux_backend_is_qt({"PYWEBVIEW_GUI": "gtk"}) is False


def test_the_qt_check_quotes_the_end_of_the_failure_not_the_beginning() -> None:
    """Qt puts the library it could not load in its last lines."""

    output = "\n".join(
        [
            "qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed.",
            "",
            'qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.',
            "This application failed to start because no Qt platform plugin could be initialized.",
        ]
    )
    reason = desktop._linux_window_blocker(
        environ={"DISPLAY": ":0"},
        runner=lambda *_a, **_k: _completed(-6, stderr=output),
    )

    assert reason is not None
    assert "xcb-cursor0" in reason
    assert "no Qt platform plugin could be initialized" in reason
    assert "-6" in reason, "the signal is the difference between a crash and an exit"


def test_a_qt_check_that_never_returns_is_a_blocker_rather_than_a_hang() -> None:
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd="python", timeout=desktop.LINUX_QT_PROBE_TIMEOUT)

    reason = desktop._linux_window_blocker(environ={"DISPLAY": ":0"}, runner=runner)

    assert reason is not None and "did not finish" in reason


def test_the_linux_application_is_named_before_any_window_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qt reads both of these statically, so they must be set pre-construction.

    Left unset, Qt derives the window's identity from ``argv[0]`` --
    ``python3.13`` for this application -- and the installed launcher matches
    nothing at all.
    """

    named: dict[str, str] = {}
    core = ModuleType("qtpy.QtCore")
    core.QCoreApplication = SimpleNamespace(
        setApplicationName=lambda value: named.update(application=value)
    )
    gui = ModuleType("qtpy.QtGui")
    gui.QGuiApplication = SimpleNamespace(
        setDesktopFileName=lambda value: named.update(desktop_file=value)
    )
    monkeypatch.setitem(sys.modules, "qtpy.QtCore", core)
    monkeypatch.setitem(sys.modules, "qtpy.QtGui", gui)
    monkeypatch.setattr(desktop.sys, "platform", "linux")

    desktop.DesktopWindow(StubController(), poll_interval=0)._name_linux_application()  # type: ignore[arg-type]

    assert named == {
        "application": desktop.LINUX_APPLICATION_NAME,
        "desktop_file": desktop.LINUX_DESKTOP_FILE_NAME,
    }


def test_a_qt_that_cannot_be_named_still_gets_its_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong taskbar icon is a far better outcome than no window."""

    monkeypatch.setattr(desktop.sys, "platform", "linux")

    def missing(_name: str) -> ModuleType:
        raise ImportError("No module named 'qtpy'")

    monkeypatch.setattr(desktop.importlib, "import_module", missing)
    logged: list[str] = []
    monkeypatch.setattr(desktop, "_log_startup_failure", logged.append)

    desktop.DesktopWindow(StubController(), poll_interval=0)._name_linux_application()  # type: ignore[arg-type]

    assert logged and "qtpy" in logged[0]


def test_help_never_opens_a_window_and_is_titled_by_the_installed_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``waveguide-generator --help`` opened a window and never exited.

    The launcher forwarded everything it did not recognise as a display flag
    to the server, so ``--help`` reached the *server's* argparse, which
    printed usage into a pipe nobody reads and exited -- leaving the GUI event
    loop running for ever. Reported on Fedora 44, 2026-09-05.
    """

    assert desktop.main(["--help"]) == 0
    printed = capsys.readouterr().out

    assert printed.startswith("usage: waveguide-generator")
    assert "--no-gui" in printed and "--window" in printed and "--port" in printed


def test_an_unrecognised_flag_is_refused_instead_of_forwarded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--no-brwoser`` was accepted silently and hung a window."""

    assert desktop.main(["--no-brwoser"]) == 2
    assert "--no-brwoser" in capsys.readouterr().err


def test_the_terminal_and_browser_modes_reach_the_status_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundle's launcher execs this module, so it owns every display mode.

    Without this, ``--no-gui`` from an installed copy became a server argument
    and produced the same hang ``--help`` did.
    """

    from launchers.statusapp import __main__ as status_entrypoint

    seen: list[list[str]] = []
    monkeypatch.setattr(status_entrypoint, "main", lambda arguments: seen.append(arguments) or 0)

    assert desktop.main(["--no-gui", "--port", "3199"]) == 0
    assert desktop.main(["--browser"]) == 0
    assert seen == [["--no-gui", "--port", "3199"], ["--browser"]]


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_the_installed_command_keeps_the_no_gui_promise_when_it_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str,
) -> None:
    """``launchers.desktop`` is what the bundle launcher execs, not the status app.

    So ``waveguide-generator --window --browser --no-gui`` reaches *this*
    module's contradiction check, which sits before the branch that hands
    ``--no-gui`` on. The same promise applies: no window of our own.

    Guarded at the transport, and on all three platforms, for the reason given
    in ``test_statusapp_entrypoint._WindowStation``.
    """

    import ctypes
    import shutil
    import subprocess

    calls: list[str] = []
    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", platform)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "stderr", _Stderr(tty=False))
    monkeypatch.setattr(subprocess, "run", lambda command, **_k: calls.append("run"))
    monkeypatch.setattr(subprocess, "Popen", lambda command, **_k: calls.append("popen"))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(MessageBoxW=lambda *_a: calls.append("box"))),
        raising=False,
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    assert desktop.main(["--window", "--browser", "--no-gui"]) == 2
    assert calls == [], "the installed command opened a window in terminal mode"
    assert "only one display mode" in (
        tmp_path / "logs" / "statusapp.log"
    ).read_text(encoding="utf-8")


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


@pytest.mark.parametrize(
    ("stderr", "reporter_shows", "bundle_shows"),
    [
        (None, True, False),
        (_Stderr(tty=False), True, False),
        (_Stderr(tty=True), False, True),
    ],
    ids=["pythonw-has-no-console", "launchservices-nobody-reads-it", "a-real-terminal"],
)
def test_a_bundle_failure_reaches_the_screen_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    startup_dialogs: list[str],
    stderr: object,
    reporter_shows: bool,
    bundle_shows: bool,
) -> None:
    """Two reporters, one dialog, in all three console states.

    ``_report_bundle_failure`` exists because LaunchServices gives a bundle a
    stderr that is real and that nobody reads, so a failed start or a completed
    rollback must be put on screen anyway. It delivers that through two calls
    and they have to be complementary: ``_report_startup_failure``'s own dialog
    when the console is unreadable, the bundle's own when a terminal has
    already printed the message.

    They stopped being complementary when the inner test changed from
    ``sys.stderr is None`` to :func:`_console_is_readable` and the outer guard
    stayed ``sys.stderr is not None``. Both then fired on every bundle launch,
    and on macOS both were modal, so the user dismissed the same sentence twice
    before the application would continue.

    Driven through ``sys.stderr`` rather than by replacing the predicate,
    because the predicate is imported into both modules: patching one name
    leaves the other answering about the real stream, which is how the two
    halves came to disagree in the first place.
    """

    monkeypatch.setenv("WG2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stderr", stderr)
    bundle_dialogs: list[str] = []
    monkeypatch.setattr(desktop, "_show_bundle_failure_dialog", bundle_dialogs.append)

    desktop.DesktopWindow._report_bundle_failure("layers restored")

    assert startup_dialogs == (["layers restored"] if reporter_shows else [])
    assert bundle_dialogs == (["layers restored"] if bundle_shows else [])


def test_bundle_failure_dialog_uses_valid_macos_applescript(
    monkeypatch: pytest.MonkeyPatch, real_startup_dialogs: None,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    commands: list[list[str]] = []
    monkeypatch.setattr(
        desktop.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    desktop._show_bundle_failure_dialog('Update failed at "app"')

    assert commands[0][0] == "/usr/bin/osascript"
    script = " ".join(commands[0])
    assert 'buttons {"OK"} default button "OK" with icon stop' in script
    assert commands[0][-1] == 'Update failed at "app"'


class SequencedController(StubController):
    """A controller whose polls replay a scripted sequence of snapshots."""

    def __init__(self, *polls: StatusSnapshot) -> None:
        super().__init__()
        self._polls = list(polls)

    def poll(self) -> StatusSnapshot:
        self.polls += 1
        if len(self._polls) > 1:
            return self._polls.pop(0)
        return self._polls[0]


def _probe_timeout_while_importing() -> StatusSnapshot:
    # What StatusController.poll() reports when one 0.35 s probe times out while
    # the server process is alive and still importing: ERROR lamps, live pid.
    return StatusSnapshot(
        backend=LampStatus(ServiceState.ERROR, "/health failed: timed out"),
        frontend=LampStatus(ServiceState.ERROR, "SPA route failed: timed out"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )


def test_a_probe_timeout_during_server_import_is_not_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = SequencedController(
        _probe_timeout_while_importing(),
        _probe_timeout_while_importing(),
        _snapshot(ServiceState.OK),
    )
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]
    assert reported == []
    assert len(created) == 1
    assert controller.polls >= 3


def test_a_stale_frontend_build_still_opens_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "Healthy"),
        frontend=LampStatus(ServiceState.WARNING, "SPA is being served, but stale"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )
    controller = StubController(poll_snapshot=stale)
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]
    assert created[0][0] == (desktop.WINDOW_TITLE, stale.url)


def test_a_server_that_never_answers_is_reported_instead_of_waited_on_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController(poll_snapshot=_snapshot(ServiceState.STARTING))
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)

    window = desktop.DesktopWindow(controller, poll_interval=0, startup_timeout=0, **WINDOWS_WEBVIEW_READY)  # type: ignore[arg-type]
    assert window.run() == 1
    assert created == []
    assert controller.closes == 1
    assert "within 0 seconds" in reported[0]


class LiveWindow:
    """A window whose closed event only fires after ``polls`` loop turns or destroy()."""

    def __init__(self, polls: int) -> None:
        self.remaining = polls
        self.destroyed = 0
        self.titles: list[str] = []
        self.events = SimpleNamespace(closed=self)

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def wait(self, _timeout: float) -> bool:
        if self.destroyed or self.remaining <= 0:
            return True
        self.remaining -= 1
        return False

    def destroy(self) -> None:
        self.destroyed += 1


def _live_webview(polls: int) -> tuple[ModuleType, LiveWindow]:
    stub = ModuleType("webview")
    stub.settings = {"ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True}
    window = LiveWindow(polls)
    stub.create_window = lambda *_args, **_kwargs: window

    # pywebview receives the selected Linux backend as ``gui="qt"``. Keep
    # this stub's signature aligned with pywebview so the test remains about
    # the desktop loop rather than a mock-only TypeError.
    def start(*, func, **_options: object) -> None:
        func()

    stub.start = start
    return stub, window


def test_an_installer_handoff_stops_the_server_before_starting_the_installer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    events: list[str] = []

    class OrderedController(StubController):
        def close(self) -> StatusSnapshot:
            events.append("close")
            return super().close()

        def launch_update(self, update: object) -> None:
            assert update is request
            events.append("launch")

    controller = OrderedController(update_requests=[request])
    webview, window = _live_webview(polls=3)
    monkeypatch.setitem(sys.modules, "webview", webview)

    assert desktop.DesktopWindow(controller, poll_interval=0, update_ready_delay=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]

    assert events[:2] == ["close", "launch"]
    assert window.destroyed == 1


class BundleController(StubController):
    def __init__(
        self,
        app_layer: Path,
        data_dir: Path,
        *,
        start_snapshot: StatusSnapshot = _snapshot(ServiceState.STARTING),
        poll_snapshot: StatusSnapshot = _snapshot(ServiceState.OK),
    ) -> None:
        super().__init__(start_snapshot=start_snapshot, poll_snapshot=poll_snapshot)
        self.environ = {"WG2_BUNDLE": "1"}
        self.repo_root = app_layer
        self.data_dir = data_dir


def test_failed_installer_handoff_restarts_before_claiming_current_version_is_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    app.mkdir(parents=True)
    (resources / "runtime").mkdir()
    controller = BundleController(app, tmp_path / "data")
    controller.update_requests = [_request()]
    controller.handoff_error = "updater spawn failed"
    webview, window = _live_webview(polls=3)
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_bundle_failure_dialog", shown.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, update_ready_delay=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]
    assert controller.starts == 2
    assert window.destroyed == 0
    assert "was restarted and remains open" in reported[0]
    # One dialog, and it is _report_startup_failure's: its own console test
    # now covers a bundle's unreadable stderr, so this second call must not
    # fire as well. See test_a_bundle_failure_reaches_the_screen_exactly_once.
    assert shown == []


def test_failed_installer_handoff_and_restart_close_the_dead_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    app.mkdir(parents=True)
    (resources / "runtime").mkdir()

    class RestartFailureController(BundleController):
        def start(self) -> StatusSnapshot:
            if self.starts:
                self.starts += 1
                return _snapshot(ServiceState.ERROR)
            return super().start()

    controller = RestartFailureController(app, tmp_path / "data")
    controller.update_requests = [_request()]
    controller.handoff_error = "updater spawn failed"
    webview, window = _live_webview(polls=3)
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_bundle_failure_dialog", shown.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, update_ready_delay=0, **WINDOWS_WEBVIEW_READY).run() == 1  # type: ignore[arg-type]
    assert controller.starts == 2
    assert window.destroyed == 1
    assert "also could not restart" in reported[0]
    assert "unusable window was closed" in reported[0]
    # One dialog, and it is _report_startup_failure's: its own console test
    # now covers a bundle's unreadable stderr, so this second call must not
    # fire as well. See test_a_bundle_failure_reaches_the_screen_exactly_once.
    assert shown == []


def _unreachable(reason: str) -> StatusSnapshot:
    """What the controller reports once the server behind the window is gone."""

    return StatusSnapshot(
        backend=LampStatus(ServiceState.ERROR, reason),
        frontend=LampStatus(ServiceState.ERROR, "Frontend unavailable: " + reason),
        url="http://127.0.0.1:3199/",
        pid=None,
        exit_code=1,
    )


def test_an_open_window_stops_asking_the_backend_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measured bug: 8.21 HTTP requests a second from a completely idle app.

    ``StatusController.poll()`` issues two unpooled requests -- ``/health`` and
    a full re-download of index.html -- and this loop ran it four times a second
    for as long as the window was open. Nothing it learned could change: the SPA
    route cannot stop working while the process lives, and the process exiting
    is something a blocking wait reports sooner and for free.
    """

    controller = StubController()
    webview, window = _live_webview(polls=25)
    monkeypatch.setitem(sys.modules, "webview", webview)

    assert desktop.DesktopWindow(controller, poll_interval=0, idle_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]

    # Twenty-five turns of the steady-state loop; the only poll in the whole run
    # is the readiness one that decided the window could open at all.
    assert controller.polls == 1
    # And exactly one watcher, however many turns re-register it.
    assert len(controller.watchers) == 1
    assert window.titles == []


def test_a_backend_that_goes_is_reported_once_and_leaves_the_window_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported symptom: Solve goes grey and nothing anywhere says why.

    This loop used to poll the controller and discard the answer, so a backend
    that died under an open window produced no lamp, no log line and no dialog
    -- the interface it had already served kept rendering, and the only trace
    was the Solve button greying out for good. The report survives the removal
    of the polling; it now arrives from the controller's process watcher, which
    fires the instant the child exits rather than up to 250 ms later.
    """

    controller = StubController()
    webview, window = _live_webview(polls=2)
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    # `_report_desktop_failure` forwards a `detail` keyword the other tests
    # here never reach, so this stub has to accept one.
    monkeypatch.setattr(
        desktop, "_report_startup_failure", lambda message, **_kwargs: reported.append(message)
    )
    logged: list[str] = []
    monkeypatch.setattr(desktop, "_log_startup_failure", logged.append)

    desktop_window = desktop.DesktopWindow(controller, poll_interval=0, idle_interval=0, **WINDOWS_WEBVIEW_READY)  # type: ignore[arg-type]
    assert desktop_window.run() == 0

    (on_lost,) = controller.watchers
    gone = _unreachable("Server exited with code 3: solver worker died")
    on_lost(gone)
    # A second report of the same loss changes nothing, whoever sends it.
    on_lost(gone)
    if desktop_window._loss_report is not None:
        desktop_window._loss_report.join(timeout=5.0)

    assert len(reported) == 1
    assert "solver worker died" in reported[0]
    assert "Close and reopen" in reported[0]
    # Said once, however many times it is noticed.
    assert logged.count(reported[0]) == 1
    # The window is left alone: unsaved design work is still in it.
    assert window.destroyed == 0
    assert window.titles == [desktop.UNREACHABLE_TITLE, desktop.UNREACHABLE_TITLE]


def test_a_reported_loss_is_never_re_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-arming would rediscover the same dead child on every loop turn."""

    controller = StubController()
    window = desktop.DesktopWindow(controller, **WINDOWS_WEBVIEW_READY)  # type: ignore[arg-type]
    monkeypatch.setattr(desktop, "_log_startup_failure", lambda _message: None)
    monkeypatch.setattr(desktop.DesktopWindow, "_report_desktop_failure", lambda *_a, **_k: None)

    window._watch_backend()
    assert len(controller.watchers) == 1

    window._backend_unreachable(_unreachable("Server exited with code 3"))
    controller.watchers.clear()
    window._watch_backend()

    assert controller.watchers == []
    if window._loss_report is not None:
        window._loss_report.join(timeout=5.0)


def test_linux_pins_the_qt_platform_before_the_probe_reads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default Linux launch chooses xcb, and the probe sees that choice.

    Order is the whole point. The probe runs a child with a copy of this
    environment; if the pin landed after it, the probe would be answering about
    Wayland and the window would open under xcb -- a check about a different
    machine from the one that runs.
    """

    controller = StubController()
    webview, _created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(
        desktop.DesktopWindow, "_name_linux_application", lambda self: None
    )
    monkeypatch.setattr(desktop, "StatusController", lambda *, server_args, **_kwargs: controller)

    seen: list[str | None] = []

    def _probe() -> str | None:
        seen.append(os.environ.get("QT_QPA_PLATFORM"))
        return None

    monkeypatch.setattr(desktop, "_linux_window_blocker", _probe)

    assert desktop.main([]) == 0
    assert seen == ["xcb"], "the probe must run under the plugin the window will use"
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_an_explicit_qt_platform_wins_over_the_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user whose Wayland session does render can ask for it back.

    The same precedence ``PYWEBVIEW_GUI`` already has over ``gui='qt'``: this
    launcher picks a default, it does not overrule a choice.
    """

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")

    assert desktop._pin_linux_qt_platform(os.environ) is None
    assert os.environ["QT_QPA_PLATFORM"] == "wayland"


def test_the_pin_is_linux_only_and_qt_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing else is drawn by the Qt platform plugin, so nothing else is told.

    A ``PYWEBVIEW_GUI=gtk`` launch is WebKitGTK; naming a Qt plugin for it would
    be a claim about a renderer that launch is not using. macOS and Windows have
    no platform plugin to choose at all.
    """

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    environment: dict[str, str] = {"PYWEBVIEW_GUI": "gtk"}
    assert desktop._pin_linux_qt_platform(environment) is None
    assert environment == {"PYWEBVIEW_GUI": "gtk"}

    for platform in ("darwin", "win32"):
        monkeypatch.setattr(desktop.sys, "platform", platform)
        elsewhere: dict[str, str] = {}
        assert desktop._pin_linux_qt_platform(elsewhere) is None
        assert elsewhere == {}


def test_an_empty_qt_platform_is_not_a_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """``QT_QPA_PLATFORM=`` is what a shell leaves behind, not a decision.

    Qt treats an empty value as unset and auto-detects; so must this, or the
    launcher would hand the blank window back to the one user whose environment
    mentions the variable without setting it.
    """

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    environment = {"QT_QPA_PLATFORM": "  "}
    assert desktop._pin_linux_qt_platform(environment) == "xcb"
    assert environment["QT_QPA_PLATFORM"] == "xcb"


def test_a_browser_launch_does_not_pin_a_platform_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--browser`` and ``--no-gui`` open no Qt window, so they choose no plugin."""

    from launchers.statusapp import __main__ as status_entrypoint

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(status_entrypoint, "main", lambda _arguments: 0)

    assert desktop.main(["--browser"]) == 0
    assert "QT_QPA_PLATFORM" not in os.environ

def test_the_help_names_the_mode_the_installed_command_actually_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``(the default)`` has to mean the branch ``main`` falls through to.

    It stopped being true silently. The help said ``--browser`` was the default
    from before the native window existed, and when the Linux window landed the
    sentence became wrong on the one platform whose users had just had their
    launch mode changed underneath them -- with nothing failing, because no test
    tied the words to the fall-through.

    So tie them: read which option the help marks, and prove ``main`` with no
    display flags takes that mode.
    """

    from launchers.statusapp.__main__ import build_parser

    # Wide, so argparse puts each option and its help on one line. At a normal
    # terminal width the old wording wrapped and ``(the default)`` landed on a
    # continuation line -- which would make this test pass or fail on where the
    # text happened to break rather than on what it said.
    monkeypatch.setenv("COLUMNS", "200")
    marked = [
        line.strip()
        for line in build_parser().format_help().splitlines()
        if "(the default)" in line
    ]
    assert len(marked) == 1, f"exactly one display mode is the default, got {marked}"
    assert marked[0].startswith("--window"), marked[0]

    # ...and that is the mode a bare command line reaches.
    controller = StubController()
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop, "_linux_window_blocker", lambda: None)
    monkeypatch.setattr(
        desktop.DesktopWindow, "_name_linux_application", lambda self: None
    )
    monkeypatch.setattr(desktop, "StatusController", lambda *, server_args, **_kwargs: controller)

    assert desktop.main([]) == 0
    assert created, "no display flag must open the window the help calls the default"


def test_a_successful_browser_fallback_is_a_successful_start(monkeypatch: pytest.MonkeyPatch) -> None:
    window = desktop.DesktopWindow.__new__(desktop.DesktopWindow)
    opened: list[str] = []
    window.browser_fallback = opened.append
    monkeypatch.setattr(desktop.DesktopWindow, "_report_desktop_failure", lambda *_a, **_k: None)

    assert window._fallback_from_windows_webview(_snapshot(ServiceState.OK), RuntimeError("no WebView2")) == 0
    assert opened == ["http://127.0.0.1:3199/"]


def test_a_failed_browser_fallback_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    window = desktop.DesktopWindow.__new__(desktop.DesktopWindow)

    def refuse(_url: str) -> None:
        raise RuntimeError("the default browser refused the local interface URL")

    window.browser_fallback = refuse
    reported: list[str] = []
    monkeypatch.setattr(
        desktop.DesktopWindow, "_report_desktop_failure", lambda _self, message, **_k: reported.append(message)
    )

    assert window._fallback_from_windows_webview(_snapshot(ServiceState.OK), RuntimeError("no WebView2")) == 1
    assert "also could not open the browser fallback" in reported[-1]
