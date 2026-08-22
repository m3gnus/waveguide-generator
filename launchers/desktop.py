"""Native pywebview front end for the checkout-owned status controller."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from types import ModuleType
from urllib.parse import urljoin, urlsplit

from launchers.statusapp.__main__ import _report_startup_failure, _show_startup_failure_dialog
from launchers.statusapp.controller import ServiceState, StatusController, StatusSnapshot
from launchers.apply_update import (
    ApplyUpdateError,
    append_update_log,
    bundle_from_app_layer,
    cleanup_previous_layers,
    repair_bundle,
    resources_directory,
    rollback_previous_layers,
)
from launchers.statusapp.updater import BundleUpdateRequest, UpdateHandoffError


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
        update_ready_delay: float = 0.75,
    ) -> None:
        self.controller = controller
        self.poll_interval = poll_interval
        self.startup_timeout = startup_timeout
        self.update_ready_delay = update_ready_delay
        self.js_api = _WindowApi(self)
        self._webview: ModuleType | None = None
        self._window: object | None = None
        self._healthy_bundle_checked = False

    def _bundle_paths(self) -> tuple[Path, Path, Path] | None:
        environment = getattr(self.controller, "environ", os.environ)
        if environment.get("WG2_BUNDLE") != "1":
            return None
        try:
            app_layer = Path(getattr(self.controller, "repo_root")).resolve()
            bundle = bundle_from_app_layer(app_layer, sys.platform)
            data_dir = Path(getattr(self.controller, "data_dir")).resolve()
        except (ApplyUpdateError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None
        return bundle, resources_directory(bundle, sys.platform), data_dir

    def _finish_healthy_bundle_update(self, snapshot: StatusSnapshot) -> None:
        if self._healthy_bundle_checked or snapshot.frontend.state is not ServiceState.OK:
            return
        self._healthy_bundle_checked = True
        paths = self._bundle_paths()
        if paths is None:
            return
        bundle, resources, data_dir = paths

        def log(message: str) -> None:
            append_update_log(data_dir, message)

        had_previous = any(
            (resources / f"{name}.previous").exists() for name in ("app", "runtime")
        )
        try:
            cleanup_previous_layers(resources, log=log)
        except OSError as exc:
            log(f"Could not remove healthy-start rollback layers: {exc}")
        finally:
            if had_previous:
                repair_bundle(bundle, platform_name=sys.platform, log=log)
                # The staged layers moved into the bundle; what is left under
                # updates/ is the downloaded archives (the runtime zip alone is
                # well over 100 MB), which the healthy new version never needs.
                downloads = data_dir / "updates"
                try:
                    shutil.rmtree(downloads)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    log(f"Could not remove the update downloads {downloads}: {exc}")
                else:
                    log(f"Removed the update downloads: {downloads}")

    @staticmethod
    def _report_bundle_failure(message: str) -> None:
        """Log as usual, and always put a bundle's failure on screen.

        LaunchServices starts the bundle with stderr attached to nothing a
        user can read, yet not ``None``, so the console-only heuristic in
        ``_report_startup_failure`` would leave a failed start (or a completed
        rollback) invisible.
        """

        _report_startup_failure(message)
        if sys.stderr is not None:
            _show_startup_failure_dialog(message)

    def _report_bundle_startup_failure(
        self, snapshot: StatusSnapshot, cause: str
    ) -> None:
        message = self._failure_message(snapshot, cause)
        paths = self._bundle_paths()
        if paths is None:
            _report_startup_failure(message)
            return
        bundle, resources, data_dir = paths
        if not any((resources / f"{name}.previous").is_dir() for name in ("app", "runtime")):
            self._report_bundle_failure(message)
            return
        self.controller.close()

        def log(entry: str) -> None:
            append_update_log(data_dir, entry)

        rolled_back = rollback_previous_layers(resources, log=log)
        if rolled_back:
            repair_bundle(bundle, platform_name=sys.platform, log=log)
            result = "The previous version was restored. Reopen Waveguide Generator."
        else:
            result = (
                "Automatic rollback failed. Review update.log in the application "
                "data log directory before changing the bundle."
            )
        self._report_bundle_failure(f"{message}\n\n{result}")

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
                self._report_bundle_startup_failure(snapshot, "did not start")
                return None
            if time.monotonic() >= deadline:
                self._report_bundle_startup_failure(
                    snapshot,
                    f"did not answer within {self.startup_timeout:.0f} seconds",
                )
                return None
            time.sleep(self.poll_interval)
            snapshot = self.controller.poll()
        self._finish_healthy_bundle_update(snapshot)
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

        request = self.controller.take_update_request()
        if request is None:
            return False
        label = request.version if isinstance(request, BundleUpdateRequest) else request
        try:
            if isinstance(request, BundleUpdateRequest):
                # Leave one progress-poll window in which the SPA can render
                # "ready" after the server publishes the request file.
                time.sleep(self.update_ready_delay)
                self.controller.close()
            self.controller.launch_update(request)
        except UpdateHandoffError as exc:
            _report_startup_failure(
                f"Waveguide Generator could not start the {label} update: {exc}\n\n"
                "The current version stays open."
            )
            if isinstance(request, BundleUpdateRequest):
                self.controller.start()
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
