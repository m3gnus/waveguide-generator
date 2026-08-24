"""Native-window contracts without importing the real GUI toolkit."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from launchers import desktop
from launchers.statusapp.controller import LampStatus, ServiceState, StatusSnapshot
from launchers.statusapp.updater import (
    BundleUpdateRequest,
    UpdateHandoffError,
    UpdateRequest,
)


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
    update_requests: list[UpdateRequest | None] = field(default_factory=list)
    launched: list[UpdateRequest] = field(default_factory=list)
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

    def take_update_request(self) -> UpdateRequest | None:
        return self.update_requests.pop(0) if self.update_requests else None

    def launch_update(self, tag: UpdateRequest) -> None:
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


def test_bundle_failure_dialog_uses_valid_macos_applescript(
    monkeypatch: pytest.MonkeyPatch,
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


def test_a_stale_frontend_still_counts_as_a_healthy_start_for_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshness caveat is not evidence that the update failed.

    WARNING means "the SPA is being served, but it looks stale against its
    sources". Requiring OK meant a perfectly good update never reclaimed its
    .previous layers or its downloaded archives -- measured at over a gigabyte
    on a bundle whose only fault was a dist that looked older than its source.
    """

    bundle = tmp_path / "Waveguide Generator"
    data = tmp_path / "data"
    for name in ("app", "runtime", "app.previous", "runtime.previous"):
        (bundle / name).mkdir(parents=True)
    (data / "updates" / "0.2.5").mkdir(parents=True)
    (data / "logs").mkdir(parents=True)

    stale_start = StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "Healthy"),
        frontend=LampStatus(ServiceState.WARNING, "SPA is being served, but stale"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )
    window = desktop.DesktopWindow(StubController(poll_snapshot=stale_start), **WINDOWS_WEBVIEW_READY)  # type: ignore[arg-type]
    monkeypatch.setattr(window, "_bundle_paths", lambda: (bundle, bundle, data))
    monkeypatch.setattr(desktop, "repair_bundle", lambda *a, **k: None)

    window._finish_healthy_bundle_update(stale_start)

    assert not (bundle / "app.previous").exists()
    assert not (bundle / "runtime.previous").exists()
    assert not (data / "updates").exists()


def test_declining_to_clean_says_so_in_the_update_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent refusal is what made this cost two rebuilds to find."""

    bundle = tmp_path / "Waveguide Generator"
    data = tmp_path / "data"
    (bundle / "app.previous").mkdir(parents=True)
    (data / "logs").mkdir(parents=True)

    not_serving = StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "Healthy"),
        frontend=LampStatus(ServiceState.STARTING, "Waiting for the SPA route"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )
    window = desktop.DesktopWindow(StubController(poll_snapshot=not_serving), **WINDOWS_WEBVIEW_READY)  # type: ignore[arg-type]
    monkeypatch.setattr(window, "_bundle_paths", lambda: (bundle, bundle, data))

    window._finish_healthy_bundle_update(not_serving)

    assert (bundle / "app.previous").exists()
    written = (data / "logs" / "update.log").read_text(encoding="utf-8")
    assert "Not reclaiming the previous layers" in written
    assert "STARTING" in written


def test_the_cleanup_guard_is_never_stricter_than_the_loop_that_calls_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_wait_for_frontend calls this on the snapshot that ended its loop.

    Any condition here that the loop does not also enforce is one the caller
    never promised, and skipping is silent: no log line, no dialog, just a
    gigabyte that never comes back.
    """

    bundle = tmp_path / "Waveguide Generator"
    data = tmp_path / "data"
    for name in ("app", "runtime", "app.previous"):
        (bundle / name).mkdir(parents=True)
    (data / "logs").mkdir(parents=True)

    # Backend still reporting ERROR while the SPA is already being served is a
    # real ordering during start-up, and it must not block the sweep.
    serving = StatusSnapshot(
        backend=LampStatus(ServiceState.ERROR, "/health failed: timed out"),
        frontend=LampStatus(ServiceState.WARNING, "SPA is being served, but stale"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )
    window = desktop.DesktopWindow(StubController(poll_snapshot=serving), **WINDOWS_WEBVIEW_READY)  # type: ignore[arg-type]
    assert window._frontend_ready(serving) is True
    monkeypatch.setattr(window, "_bundle_paths", lambda: (bundle, bundle, data))
    monkeypatch.setattr(desktop, "repair_bundle", lambda *a, **k: None)

    window._finish_healthy_bundle_update(serving)

    assert not (bundle / "app.previous").exists()


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

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]

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

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]

    assert controller.launched == []
    assert window.destroyed == 0
    assert "v1.2.3" in reported[0] and "installer is missing" in reported[0]
    # Polling carried on after the failure until the user closed the window:
    # one readiness poll, then all three loop turns the stub window allowed.
    assert controller.polls == 4
    assert controller.closes == 1


def test_bundle_handoff_stops_the_server_before_spawning_the_staged_updater(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged_app = tmp_path / "data" / "updates" / "1.2.3" / "staged" / "app"
    staged_app.mkdir(parents=True)
    request = BundleUpdateRequest("1.2.3", staged_app, None)
    events: list[str] = []

    class OrderedController(StubController):
        def close(self) -> StatusSnapshot:
            events.append("close")
            return super().close()

        def launch_update(self, update: UpdateRequest) -> None:
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


def test_first_healthy_bundle_start_removes_previous_layers_and_resigns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    previous = resources / "app.previous"
    app.mkdir(parents=True)
    previous.mkdir()
    (resources / "runtime").mkdir()
    downloads = tmp_path / "data" / "updates" / "1.2.3" / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "waveguide-generator-app-1.2.3.zip").write_bytes(b"zip")
    controller = BundleController(app, tmp_path / "data")
    webview, _created = _stub_webview()
    event_loop_started: list[bool] = []

    def start(*, func) -> None:
        # HTTP is already healthy, but rollback must remain until pywebview
        # actually enters its event-loop callback.
        assert previous.exists()
        event_loop_started.append(True)
        func()

    webview.start = start
    monkeypatch.setitem(sys.modules, "webview", webview)
    repaired: list[Path] = []
    monkeypatch.setattr(
        desktop,
        "repair_bundle",
        lambda bundle, **_kwargs: repaired.append(bundle),
    )

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]

    assert not previous.exists()
    assert event_loop_started == [True]
    assert not (tmp_path / "data" / "updates").exists()
    assert repaired == [tmp_path / "Waveguide Generator.app"]
    log_text = (tmp_path / "data" / "logs" / "update.log").read_text(encoding="utf-8")
    assert "Removed healthy-start rollback layer" in log_text
    assert "Removed the update downloads" in log_text


def test_failed_new_bundle_start_rolls_back_and_reports_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    previous = resources / "app.previous"
    app.mkdir(parents=True)
    previous.mkdir()
    (resources / "runtime").mkdir()
    (app / "marker").write_text("new", encoding="utf-8")
    (previous / "marker").write_text("old", encoding="utf-8")
    controller = BundleController(
        app,
        tmp_path / "data",
        start_snapshot=_snapshot(ServiceState.ERROR),
    )
    reported: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_bundle_failure_dialog", shown.append)
    monkeypatch.setattr(desktop, "repair_bundle", lambda *_args, **_kwargs: None)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 1  # type: ignore[arg-type]

    assert (app / "marker").read_text(encoding="utf-8") == "old"
    assert not previous.exists()
    assert "previous version was restored" in reported[0]
    # A bundle started by LaunchServices has a stderr nobody reads, so the
    # rollback result must reach the screen regardless of the console heuristic.
    assert shown == reported
    assert "Restored the previous bundle layers" in (
        tmp_path / "data" / "logs" / "update.log"
    ).read_text(encoding="utf-8")


def test_missing_pywebview_rolls_back_bundle_after_http_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    previous = resources / "app.previous"
    runtime = resources / "runtime"
    app.mkdir(parents=True)
    previous.mkdir()
    runtime.mkdir()
    (app / "marker").write_text("new", encoding="utf-8")
    (previous / "marker").write_text("old", encoding="utf-8")
    controller = BundleController(app, tmp_path / "data")

    def missing(_name: str) -> ModuleType:
        raise ModuleNotFoundError("No module named 'webview'", name="webview")

    monkeypatch.setattr(desktop.importlib, "import_module", missing)
    monkeypatch.setattr(desktop, "repair_bundle", lambda *_args, **_kwargs: None)
    reported: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_bundle_failure_dialog", shown.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 1  # type: ignore[arg-type]
    assert (app / "marker").read_text(encoding="utf-8") == "old"
    assert not previous.exists()
    assert "pywebview is unavailable" in reported[0]
    assert "previous version was restored" in reported[0]
    assert shown == reported


def test_macos_cleanup_sign_failure_restores_rollback_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    previous = resources / "app.previous"
    app.mkdir(parents=True)
    previous.mkdir()
    (resources / "runtime").mkdir()
    downloads = tmp_path / "data" / "updates" / "1.2.3" / "downloads"
    downloads.mkdir(parents=True)
    controller = BundleController(app, tmp_path / "data")
    webview, _created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    repair_calls = 0

    def repair(_bundle: Path, **_kwargs: object) -> None:
        nonlocal repair_calls
        repair_calls += 1
        if repair_calls == 1:
            raise desktop.ApplyUpdateError("injected verification failure")

    monkeypatch.setattr(desktop, "repair_bundle", repair)
    reported: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_bundle_failure_dialog", shown.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]
    assert previous.is_dir()
    assert downloads.is_dir()
    assert repair_calls == 2
    assert "rollback material was restored" in reported[0].casefold()
    assert shown == reported


def test_startup_recovers_a_missing_live_layer_before_starting_the_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    runtime_previous = resources / "runtime.previous"
    app.mkdir(parents=True)
    runtime_previous.mkdir()
    (runtime_previous / "marker").write_text("old runtime", encoding="utf-8")

    class RecoveryController(BundleController):
        def start(self) -> StatusSnapshot:
            assert (resources / "runtime" / "marker").read_text() == "old runtime"
            return super().start()

    controller = RecoveryController(app, tmp_path / "data")
    webview, _created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    repaired: list[Path] = []
    monkeypatch.setattr(
        desktop,
        "repair_bundle",
        lambda bundle, **_kwargs: repaired.append(bundle),
    )

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]
    assert (resources / "runtime" / "marker").read_text() == "old runtime"
    assert not runtime_previous.exists()
    assert repaired == [tmp_path / "Waveguide Generator.app"]


def test_second_bundle_update_with_pending_previous_stays_visible_and_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    app.mkdir(parents=True)
    (resources / "runtime").mkdir()
    (resources / "app.previous").mkdir()
    staged_app = tmp_path / "data" / "updates" / "1.2.3" / "staged" / "app"
    staged_app.mkdir(parents=True)
    warning = StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "Healthy"),
        frontend=LampStatus(ServiceState.WARNING, "SPA stale"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )
    controller = BundleController(app, tmp_path / "data", poll_snapshot=warning)
    controller.update_requests = [BundleUpdateRequest("1.2.3", staged_app, None)]
    # This test is about refusing a second update while rollback material is
    # pending. The healthy-start cleanup would now reclaim that material first
    # (it treats a stale interface as healthy, as the wait loop does), which is
    # its own behaviour with its own tests; hold it still so the refusal is what
    # is being exercised here.
    monkeypatch.setattr(
        desktop.DesktopWindow, "_finish_healthy_bundle_update", lambda *_a: None
    )
    webview, window = _live_webview(polls=2)
    monkeypatch.setitem(sys.modules, "webview", webview)
    reported: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_bundle_failure_dialog", shown.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, update_ready_delay=0, **WINDOWS_WEBVIEW_READY).run() == 0  # type: ignore[arg-type]
    assert controller.launched == []
    assert controller.closes == 1
    assert window.destroyed == 0
    assert "rollback material from an earlier update" in reported[0]
    assert "current version remains open" in reported[0]
    assert shown == reported


def test_failed_bundle_handoff_restarts_before_claiming_current_version_is_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    app.mkdir(parents=True)
    (resources / "runtime").mkdir()
    staged_app = tmp_path / "data" / "updates" / "1.2.3" / "staged" / "app"
    staged_app.mkdir(parents=True)
    controller = BundleController(app, tmp_path / "data")
    controller.update_requests = [BundleUpdateRequest("1.2.3", staged_app, None)]
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
    assert shown == reported


def test_failed_bundle_handoff_and_restart_close_the_dead_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    app = resources / "app"
    app.mkdir(parents=True)
    (resources / "runtime").mkdir()
    staged_app = tmp_path / "data" / "updates" / "1.2.3" / "staged" / "app"
    staged_app.mkdir(parents=True)

    class RestartFailureController(BundleController):
        def start(self) -> StatusSnapshot:
            if self.starts:
                self.starts += 1
                return _snapshot(ServiceState.ERROR)
            return super().start()

    controller = RestartFailureController(app, tmp_path / "data")
    controller.update_requests = [BundleUpdateRequest("1.2.3", staged_app, None)]
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
    assert shown == reported


def _failed_windows_bundle(tmp_path: Path) -> tuple[Path, Path]:
    """A Windows folder whose newly installed layers are about to fail to start."""

    bundle = tmp_path / "Waveguide Generator"
    for name, marker in (
        ("app", "new"),
        ("app.previous", "old"),
        ("runtime", "new"),
        ("runtime.previous", "old"),
    ):
        layer = bundle / name
        layer.mkdir(parents=True)
        (layer / "marker").write_text(marker, encoding="utf-8")
    (bundle / "Waveguide Generator.exe").write_bytes(b"pythonw")
    (bundle / "vcruntime140.dll").write_text("new", encoding="utf-8")
    (bundle / "vcruntime140.dll.previous").write_text("old", encoding="utf-8")
    return bundle, tmp_path / "data"


class WindowsBundleController(BundleController):
    def __init__(self, app_layer: Path, data_dir: Path) -> None:
        super().__init__(app_layer, data_dir, start_snapshot=_snapshot(ServiceState.ERROR))
        self.server_args = ("--port", "3110")


def test_a_failed_windows_start_hands_the_rollback_to_a_detached_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This process is the one holding the DLLs, so it must not do the restore.

    It has ``runtime`` and ``app`` mapped into it, and Windows will not let
    anything delete a mapped file until the process exits. Rolling back from
    here is what left the 0.2.6 ``vcruntime140.dll`` sitting on top of a
    restored 0.2.5 runtime: the deletion raised, and the launcher files were
    never reached.
    """

    monkeypatch.setattr(desktop.sys, "platform", "win32")
    bundle, data_dir = _failed_windows_bundle(tmp_path)
    controller = WindowsBundleController(bundle / "app", data_dir)
    handoffs: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        desktop,
        "launch_rollback_handoff",
        lambda *args, **kwargs: (
            handoffs.append((args, kwargs)) or [str(bundle / "Waveguide Generator.exe")]
        ),
    )
    monkeypatch.setattr(
        desktop,
        "rollback_previous_layers",
        lambda *_args, **_kwargs: pytest.fail("the failed process must not roll back in place"),
    )
    reported: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_startup_failure_dialog", shown.append)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 1  # type: ignore[arg-type]

    # Nothing moved in this process; the helper does it after this one exits.
    assert (bundle / "app" / "marker").read_text(encoding="utf-8") == "new"
    assert (bundle / "vcruntime140.dll.previous").is_file()
    (positional, keywords) = handoffs[0]
    assert positional[0] == bundle
    assert positional[1] == data_dir
    assert positional[2] == os.getpid()
    assert keywords["server_args"] == ("--port", "3110")
    assert "reopen by itself" in reported[0]
    assert shown == reported
    assert "Started the detached rollback helper" in (data_dir / "logs" / "update.log").read_text(
        encoding="utf-8"
    )


def test_a_rollback_handoff_that_cannot_start_falls_back_to_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-process rollback is imperfect on Windows; no rollback is worse."""

    monkeypatch.setattr(desktop.sys, "platform", "win32")
    bundle, data_dir = _failed_windows_bundle(tmp_path)
    controller = WindowsBundleController(bundle / "app", data_dir)

    def refuse(*_args: object, **_kwargs: object) -> list[str]:
        raise UpdateHandoffError("The rollback helper interpreter is missing")

    monkeypatch.setattr(desktop, "launch_rollback_handoff", refuse)
    monkeypatch.setattr(desktop, "repair_bundle", lambda *_args, **_kwargs: None)
    reported: list[str] = []
    monkeypatch.setattr(desktop, "_report_startup_failure", reported.append)
    monkeypatch.setattr(desktop, "_show_startup_failure_dialog", lambda _message: None)

    assert desktop.DesktopWindow(controller, poll_interval=0, **WINDOWS_WEBVIEW_READY).run() == 1  # type: ignore[arg-type]

    assert (bundle / "app" / "marker").read_text(encoding="utf-8") == "old"
    assert (bundle / "runtime" / "marker").read_text(encoding="utf-8") == "old"
    assert (bundle / "vcruntime140.dll").read_text(encoding="utf-8") == "old"
    assert "previous version was restored" in reported[0]
    log_text = (data_dir / "logs" / "update.log").read_text(encoding="utf-8")
    assert "Could not start the detached rollback helper" in log_text
    assert "Rolling back in this process instead" in log_text


def test_a_healthy_start_after_a_rollback_clears_what_windows_refused_to_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deferred deletion has to actually happen somewhere."""

    monkeypatch.setattr(desktop.sys, "platform", "win32")
    bundle = tmp_path / "Waveguide Generator"
    (bundle / "app").mkdir(parents=True)
    (bundle / "runtime").mkdir()
    (bundle / "app.failed").mkdir()
    (bundle / "runtime.failed").mkdir()
    (bundle / "vcruntime140.dll.failed").write_text("new", encoding="utf-8")
    downloads = tmp_path / "data" / "updates" / "0.2.6" / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "runtime.zip").write_bytes(b"zip")
    controller = BundleController(bundle / "app", tmp_path / "data")
    webview, _created = _stub_webview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(desktop, "repair_bundle", lambda *_args, **_kwargs: None)
    window = desktop.DesktopWindow(
        controller,
        poll_interval=0,
        pythonnet_loader=lambda: object(),
        webview2_probe=lambda: True,
    )  # type: ignore[arg-type]

    assert window.run() == 0

    assert not list(bundle.glob("*.failed*"))
    # The download that produced them is equally spent.
    assert not (tmp_path / "data" / "updates").exists()
    assert "rolled-back failed update copy" in (
        tmp_path / "data" / "logs" / "update.log"
    ).read_text(encoding="utf-8")


def _bundle_layers(tmp_path: Path, *, app_runtime_id: str, runtime_id: str) -> Path:
    """A bundle whose two layers declare the runtime ids given."""

    resources = tmp_path / "Waveguide Generator.app" / "Contents" / "Resources"
    (resources / "app").mkdir(parents=True)
    (resources / "runtime").mkdir(parents=True)
    (resources / "app" / "APP-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "version": "0.2.5", "runtimeId": app_runtime_id}),
        encoding="utf-8",
    )
    (resources / "runtime" / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": runtime_id}), encoding="utf-8"
    )
    (tmp_path / "data" / "logs").mkdir(parents=True)
    return resources


def test_an_update_interrupted_between_its_two_renames_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both layers present is not the same as both from one generation.

    Process death between the runtime and app renames leaves a complete new app
    on the complete old runtime. Nothing is missing, so a recovery that only
    looks for a missing directory starts the app against a runtime it never
    declared.
    """

    resources = _bundle_layers(tmp_path, app_runtime_id="new1", runtime_id="old0")
    (resources / "app.previous").mkdir()
    data_dir = tmp_path / "data"
    window = desktop.DesktopWindow.__new__(desktop.DesktopWindow)
    monkeypatch.setattr(
        desktop.DesktopWindow,
        "_bundle_paths",
        lambda _self: (resources.parents[1], resources, data_dir),
    )
    rolled: list[Path] = []
    monkeypatch.setattr(
        desktop, "rollback_previous_layers", lambda res, log=None: rolled.append(res) or True
    )

    assert window._recover_interrupted_bundle_update() is True
    assert rolled == [resources], "a mixed generation must be rolled back, not started"
    assert "different updates" in (data_dir / "logs" / "update.log").read_text(encoding="utf-8")


def test_matching_layers_and_unreadable_manifests_both_start_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a real disagreement blocks a start; a missing field is not one."""

    resources = _bundle_layers(tmp_path, app_runtime_id="same", runtime_id="same")
    data_dir = tmp_path / "data"
    window = desktop.DesktopWindow.__new__(desktop.DesktopWindow)
    monkeypatch.setattr(
        desktop.DesktopWindow,
        "_bundle_paths",
        lambda _self: (resources.parents[1], resources, data_dir),
    )
    monkeypatch.setattr(
        desktop, "rollback_previous_layers", lambda *_a, **_k: pytest.fail("must not roll back")
    )

    assert window._recover_interrupted_bundle_update() is True

    # An older bundle predates these fields; refusing to start over a missing
    # file would be worse than the mismatch this is looking for.
    (resources / "runtime" / "RUNTIME-MANIFEST.json").write_text("not json", encoding="utf-8")
    assert window._recover_interrupted_bundle_update() is True


def test_a_successful_browser_fallback_still_commits_the_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser start is as successful as a native one, and must finalize.

    A machine without WebView2 takes this path on every launch, so an update
    applied there would otherwise keep its previous layers and downloads for
    ever -- and eventually be refused for having rollback material pending.
    """

    _bundle_layers(tmp_path, app_runtime_id="same", runtime_id="same")
    window = desktop.DesktopWindow.__new__(desktop.DesktopWindow)
    window.browser_fallback = lambda _url: None
    finished: list[StatusSnapshot] = []
    monkeypatch.setattr(
        desktop.DesktopWindow, "_finish_healthy_bundle_update", lambda _s, snap: finished.append(snap)
    )
    monkeypatch.setattr(desktop.DesktopWindow, "_report_desktop_failure", lambda *_a, **_k: None)
    snapshot = StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "ready"),
        frontend=LampStatus(ServiceState.OK, "ready"),
        url="http://127.0.0.1:3100/",
        pid=1,
        exit_code=None,
    )

    assert window._fallback_from_windows_webview(snapshot, RuntimeError("no WebView2")) == 0
    assert finished == [snapshot]


def test_a_failed_browser_fallback_commits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _bundle_layers(tmp_path, app_runtime_id="same", runtime_id="same")
    window = desktop.DesktopWindow.__new__(desktop.DesktopWindow)

    def refuse(_url: str) -> None:
        raise RuntimeError("the default browser refused the local interface URL")

    window.browser_fallback = refuse
    monkeypatch.setattr(
        desktop.DesktopWindow,
        "_bundle_paths",
        lambda _self: (resources.parents[1], resources, tmp_path / "data"),
    )
    monkeypatch.setattr(
        desktop.DesktopWindow,
        "_finish_healthy_bundle_update",
        lambda *_a: pytest.fail("a failed fallback must not commit the update"),
    )
    monkeypatch.setattr(desktop.DesktopWindow, "_report_desktop_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(desktop.DesktopWindow, "_report_bundle_window_failure", lambda *_a: None)
    snapshot = StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "ready"),
        frontend=LampStatus(ServiceState.OK, "ready"),
        url="http://127.0.0.1:3100/",
        pid=1,
        exit_code=None,
    )

    assert window._fallback_from_windows_webview(snapshot, RuntimeError("no WebView2")) == 1
