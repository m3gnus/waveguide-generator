"""Native pywebview front end for the checkout-owned status controller."""

from __future__ import annotations

import importlib
import sys
import time
import traceback
from types import ModuleType
from urllib.parse import urljoin, urlsplit

from launchers.statusapp.__main__ import _report_startup_failure
from launchers.statusapp.controller import ServiceState, StatusController, StatusSnapshot
from launchers.statusapp.updater import UpdateHandoffError


WINDOW_TITLE = "Waveguide Generator"
PYWEBVIEW_REPAIR = (
    "Install the desktop dependency with pip from server/requirements-runtime.txt "
    "(for example: python -m pip install -r server/requirements-runtime.txt)."
)


def _origin(url: str) -> tuple[str, str, int] | None:
    """Return an HTTP origin with its effective port, or ``None``."""

    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return scheme, parsed.hostname.casefold(), port or (443 if scheme == "https" else 80)


class _WindowApi:
    """The complete JavaScript surface exposed to the frontend."""

    def __init__(self, desktop: DesktopWindow) -> None:
        self._desktop = desktop

    def open_window(self, url: str) -> None:
        """Open a secondary native window when ``url`` has the app's origin."""

        self._desktop.open_window(url)


class DesktopWindow:
    """Own one controller and its primary native application window."""

    def __init__(
        self,
        controller: StatusController,
        *,
        poll_interval: float = 0.25,
        startup_timeout: float = 120.0,
    ) -> None:
        self.controller = controller
        self.poll_interval = poll_interval
        self.startup_timeout = startup_timeout
        self.js_api = _WindowApi(self)
        self._webview: ModuleType | None = None
        self._window: object | None = None

    @staticmethod
    def _failure_message(snapshot: StatusSnapshot, cause: str) -> str:
        return (
            "Waveguide Generator could not open its desktop window because the "
            f"local interface {cause}.\n\n"
            f"Backend: {snapshot.backend.reason}\n"
            f"Frontend: {snapshot.frontend.reason}"
        )

    @staticmethod
    def _frontend_ready(snapshot: StatusSnapshot) -> bool:
        # WARNING is an interface that is being served from a stale build. The
        # status window shows that as a yellow lamp beside its "Open in browser"
        # button; the desktop window has no lamps, so it shows the interface.
        return snapshot.frontend.state in {ServiceState.OK, ServiceState.WARNING}

    @staticmethod
    def _startup_failed(snapshot: StatusSnapshot) -> bool:
        # The controller answers one probe timeout with ERROR lamps while the
        # server is still importing its solvers, and the status window simply
        # polls again. Only an ERROR without a live process -- a preflight
        # refusal or an exited server -- is final.
        return snapshot.backend.state is ServiceState.ERROR and not snapshot.running

    def _wait_for_frontend(self) -> StatusSnapshot | None:
        snapshot = self.controller.start()
        deadline = time.monotonic() + self.startup_timeout
        while not self._frontend_ready(snapshot):
            if self._startup_failed(snapshot):
                _report_startup_failure(self._failure_message(snapshot, "did not start"))
                return None
            if time.monotonic() >= deadline:
                _report_startup_failure(
                    self._failure_message(
                        snapshot,
                        f"did not answer within {self.startup_timeout:.0f} seconds",
                    )
                )
                return None
            time.sleep(self.poll_interval)
            snapshot = self.controller.poll()
        return snapshot

    def _load_webview(self) -> ModuleType:
        try:
            return importlib.import_module("webview")
        except ImportError as exc:
            _report_startup_failure(
                "Waveguide Generator could not open a desktop window because "
                f"pywebview is unavailable: {exc}\n\n{PYWEBVIEW_REPAIR}",
                detail=traceback.format_exc(),
            )
            raise

    def open_window(self, url: str) -> None:
        """Open one same-origin secondary window for the JavaScript bridge."""

        base_url = self.controller.url
        target = urljoin(base_url, url)
        if _origin(target) is None or _origin(target) != _origin(base_url):
            raise ValueError("Desktop windows may open only same-origin URLs")
        if self._webview is None:
            raise RuntimeError("The desktop window has not started")
        self._webview.create_window(WINDOW_TITLE, target)

    def _poll_loop(self) -> None:
        window = self._window
        closed = getattr(getattr(window, "events", None), "closed", None)
        wait = getattr(closed, "wait", None)
        if not callable(wait):
            self.controller.poll()
            return
        while not wait(self.poll_interval):
            self.controller.poll()
            if self._hand_off_update(window):
                return

    def _hand_off_update(self, window: object) -> bool:
        """Run a consumed in-app update request; True once the window is going.

        Same order as the status window: the installer is started first and
        this process leaves afterwards, so a handoff that cannot start leaves
        the current healthy application open. The server writes the request
        file on "Install update" and nobody but this loop reads it in window
        mode, so without this the button would be a silent no-op.
        """

        tag = self.controller.take_update_request()
        if tag is None:
            return False
        try:
            self.controller.launch_update(tag)
        except UpdateHandoffError as exc:
            _report_startup_failure(
                f"Waveguide Generator could not start the {tag} update: {exc}\n\n"
                "The current version stays open."
            )
            return False
        # Closing the window ends webview.start(), and run()'s finally stops
        # the server the way a user-initiated close does.
        destroy = getattr(window, "destroy", None)
        if callable(destroy):
            destroy()
        return True

    def run(self) -> int:
        """Start the controller, show its frontend, and own shutdown."""

        try:
            snapshot = self._wait_for_frontend()
            if snapshot is None:
                return 1
            try:
                webview = self._load_webview()
            except ImportError:
                return 1
            self._webview = webview
            webview.settings["ALLOW_DOWNLOADS"] = True
            self._window = webview.create_window(
                WINDOW_TITLE,
                snapshot.url,
                js_api=self.js_api,
                width=1440,
                height=900,
                min_size=(1100, 700),
            )
            webview.start(func=self._poll_loop)
            return 0
        except Exception as exc:  # noqa: BLE001 - native startup must remain visible
            _report_startup_failure(
                "Waveguide Generator could not open its desktop window: "
                f"{type(exc).__name__}: {exc}",
                detail=traceback.format_exc(),
            )
            return 1
        finally:
            self.controller.close()


def main(argv: list[str] | None = None) -> int:
    """Run the native window, with the status-window fallback on Linux."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if sys.platform.startswith("linux"):
        _report_startup_failure(
            "The native Waveguide Generator desktop window is unavailable on Linux "
            "in this release; opening the existing browser/status window instead."
        )
        from launchers.statusapp.__main__ import main as status_main

        return status_main(["--browser", *arguments])
    return DesktopWindow(StatusController(server_args=arguments)).run()


if __name__ == "__main__":
    raise SystemExit(main())
