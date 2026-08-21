"""Native-window contracts without importing the real GUI toolkit."""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
from types import ModuleType, SimpleNamespace

import pytest

from launchers import desktop
from launchers.statusapp.controller import LampStatus, ServiceState, StatusSnapshot
from launchers.statusapp.updater import UpdateHandoffError


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
    update_requests: list[str | None] = field(default_factory=list)
    launched: list[str] = field(default_factory=list)
    handoff_error: str | None = None

    @property
    def url(self) -> str:
        return self.poll_snapshot.url

    def start(self) -> StatusSnapshot:
        self.starts += 1
        return self.start_snapshot

    def poll(self) -> StatusSnapshot:
        self.polls += 1
        return self.poll_snapshot

    def close(self) -> StatusSnapshot:
        self.closes += 1
        return _snapshot(ServiceState.STOPPED)

    def take_update_request(self) -> str | None:
        return self.update_requests.pop(0) if self.update_requests else None

    def launch_update(self, tag: str) -> None:
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

    def start(*, func) -> None:
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

    result = desktop.DesktopWindow(controller, poll_interval=0).run()  # type: ignore[arg-type]

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
    window = desktop.DesktopWindow(controller, poll_interval=0)  # type: ignore[arg-type]
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

    assert desktop.DesktopWindow(controller, poll_interval=0).run() == 1  # type: ignore[arg-type]
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

    assert desktop.DesktopWindow(controller, poll_interval=0).run() == 1  # type: ignore[arg-type]
    message = reported[0][0]
    assert "pywebview" in message
    assert "pip" in message
    assert "server/requirements-runtime.txt" in message
    assert controller.closes == 1


def test_linux_window_request_reports_and_uses_status_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from launchers.statusapp import __main__ as status_entrypoint

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    seen: list[list[str]] = []
    monkeypatch.setattr(status_entrypoint, "main", lambda arguments: seen.append(arguments) or 7)

    assert desktop.main(["--port", "3199"]) == 7
    assert "unavailable on Linux" in reported[0]
    assert seen == [["--browser", "--port", "3199"]]


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

    assert desktop.DesktopWindow(controller, poll_interval=0).run() == 0  # type: ignore[arg-type]
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

    assert desktop.DesktopWindow(controller, poll_interval=0).run() == 0  # type: ignore[arg-type]
    assert created[0][0] == (desktop.WINDOW_TITLE, stale.url)


def test_a_server_that_never_answers_is_reported_instead_of_waited_on_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController(poll_snapshot=_snapshot(ServiceState.STARTING))
    webview, created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)

    window = desktop.DesktopWindow(controller, poll_interval=0, startup_timeout=0)  # type: ignore[arg-type]
    assert window.run() == 1
    assert created == []
    assert controller.closes == 1
    assert "within 0 seconds" in reported[0]


class LiveWindow:
    """A window whose closed event only fires after ``polls`` loop turns or destroy()."""

    def __init__(self, polls: int) -> None:
        self.remaining = polls
        self.destroyed = 0
        self.events = SimpleNamespace(closed=self)

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
    stub.start = lambda *, func: func()
    return stub, window


def test_an_in_app_update_request_hands_off_then_closes_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController(update_requests=[None, "v1.2.3"])
    webview, window = _live_webview(polls=10)
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)

    assert desktop.DesktopWindow(controller, poll_interval=0).run() == 0  # type: ignore[arg-type]

    assert controller.launched == ["v1.2.3"]
    assert window.destroyed == 1
    # The installer was started before the server went away, as in the status window.
    assert controller.closes == 1
    assert reported == []
    # One readiness poll before the window, then two loop turns.
    assert controller.polls == 3


def test_a_failed_update_handoff_is_reported_and_the_window_stays_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = StubController(
        update_requests=["v1.2.3"], handoff_error="The update installer is missing"
    )
    webview, window = _live_webview(polls=3)
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)

    assert desktop.DesktopWindow(controller, poll_interval=0).run() == 0  # type: ignore[arg-type]

    assert controller.launched == []
    assert window.destroyed == 0
    assert "v1.2.3" in reported[0] and "installer is missing" in reported[0]
    # Polling carried on after the failure until the user closed the window:
    # one readiness poll, then all three loop turns the stub window allowed.
    assert controller.polls == 4
    assert controller.closes == 1
