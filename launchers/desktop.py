"""Native pywebview front end for the checkout-owned status controller."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
from types import ModuleType
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit
import webbrowser

from launchers.statusapp.__main__ import (
    _log_startup_failure,
    _report_startup_failure,
    _show_startup_failure_dialog,
)
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
from launchers.statusapp.updater import (
    BundleUpdateRequest,
    UpdateHandoffError,
    launch_rollback_handoff,
)


WINDOW_TITLE = "Waveguide Generator"
#: Shown while the backend is not answering. The desktop window has no lamps --
#: that is the status window's job -- so the title bar is the only surface it
#: owns that is visible without the user going looking for it.
UNREACHABLE_TITLE = f"{WINDOW_TITLE} — backend not responding"
#: How long the steady-state loop sleeps between turns. It no longer asks the
#: backend anything -- the controller's watcher does that from a blocking wait
#: on the child process -- so all this governs is how soon the window notices an
#: "Install update" the server has written to disk. The 0.25 s it inherited from
#: the old health poll bought nothing there and cost 8.2 HTTP requests a second
#: on a completely idle application.
IDLE_INTERVAL = 1.0
BACKEND_LOST = (
    "Waveguide Generator's backend has stopped, so this window can no longer "
    "reach it. Solving, the job list, and saving to the workspace are all "
    "unavailable, and the interface cannot tell you so itself.\n\n"
    "{reason}\n\n"
    "The window is left open so you can copy out anything you still need. "
    "Close and reopen Waveguide Generator to carry on working."
)
ROLLBACK_HANDOFF_RESULT = (
    "The previous version is being restored by a separate helper and will "
    "reopen by itself. If it does not, review update.log in the application "
    "data log directory before changing the installation."
)
ROLLBACK_FAILED_RESULT = (
    "Automatic rollback failed. Review update.log in the application "
    "data log directory before changing the bundle."
)
PYWEBVIEW_REPAIR = (
    "Install the desktop dependency with pip from server/requirements-runtime.txt "
    "(for example: python -m pip install -r server/requirements-runtime.txt)."
)
WEBVIEW2_REPAIR = (
    "Install or repair the Microsoft Edge WebView2 Evergreen Runtime (x64), then "
    "reopen Waveguide Generator: https://developer.microsoft.com/microsoft-edge/webview2/\n"
    "If WebView2 is already installed and the error names pythonnet, reinstall the "
    "dependencies from server/requirements-runtime.txt."
)


class WindowsWebViewUnavailable(RuntimeError):
    """The Windows native-window prerequisites could not initialize."""


def _show_bundle_failure_dialog(message: str) -> None:
    """Show a bundle failure even when LaunchServices supplied a dead stderr."""

    if sys.platform != "darwin":
        _show_startup_failure_dialog(message)
        return
    try:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                "on run argv",
                "-e",
                'display dialog (item 1 of argv) with title "Waveguide Generator" '
                'buttons {"OK"} default button "OK" with icon stop',
                "-e",
                "end run",
                "--",
                message,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:  # noqa: BLE001 - statusapp.log remains the fallback
        pass


def _load_pythonnet() -> object:
    return importlib.import_module("clr")


def _windows_webview2_installed() -> bool:
    """Detect the Evergreen runtime registrations used by pywebview."""

    if sys.platform != "win32":
        return True
    configured = os.environ.get("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER")
    if configured and Path(configured).is_dir():
        return True
    try:
        import winreg
    except ImportError:
        return False

    client = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    views = {
        0,
        getattr(winreg, "KEY_WOW64_32KEY", 0),
        getattr(winreg, "KEY_WOW64_64KEY", 0),
    }
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(hive, client, 0, winreg.KEY_READ | view) as key:
                    version, _kind = winreg.QueryValueEx(key, "pv")
            except OSError:
                continue
            if isinstance(version, str) and version.strip(" .0"):
                return True
    return False


def _open_browser_fallback(url: str) -> None:
    """Open the interface and retain a visible owner for the local server."""

    if not webbrowser.open(url):
        raise RuntimeError("the default browser refused the local interface URL")
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "Waveguide Generator is running in your browser.\n\n"
            "Select OK when you want to stop it.",
            WINDOW_TITLE,
            0x40 | 0x10000,
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
        idle_interval: float = IDLE_INTERVAL,
        startup_timeout: float = 120.0,
        update_ready_delay: float = 0.75,
        pythonnet_loader: Callable[[], object] = _load_pythonnet,
        webview2_probe: Callable[[], bool] = _windows_webview2_installed,
        browser_fallback: Callable[[str], None] = _open_browser_fallback,
    ) -> None:
        self.controller = controller
        #: Between readiness polls while the server is still coming up. HTTP is
        #: the only way to learn that, so this one stays quick.
        self.poll_interval = poll_interval
        self.idle_interval = idle_interval
        self.startup_timeout = startup_timeout
        self.update_ready_delay = update_ready_delay
        self.pythonnet_loader = pythonnet_loader
        self.webview2_probe = webview2_probe
        self.browser_fallback = browser_fallback
        self.js_api = _WindowApi(self)
        self._webview: ModuleType | None = None
        self._window: object | None = None
        self._healthy_bundle_checked = False
        self._startup_snapshot: StatusSnapshot | None = None
        self._exit_code = 0
        self._backend_loss_reported = False
        #: Retained so a caller -- in practice a test -- can join the one-shot
        #: reporting thread instead of racing it.
        self._loss_report: threading.Thread | None = None

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

    @staticmethod
    def _previous_generation_paths(resources: Path) -> list[Path]:
        """Return layer and launcher backups that belong to one pending update."""

        paths = [
            path
            for name in ("app.previous", "runtime.previous")
            if ((path := resources / name).exists() or path.is_symlink())
        ]
        paths.extend(
            path
            for path in sorted(resources.glob("*.previous"))
            if path not in paths and (path.is_file() or path.is_symlink())
        )
        return paths

    def _finish_healthy_bundle_update(self, snapshot: StatusSnapshot) -> None:
        # Exactly the predicate _wait_for_frontend loops on, and deliberately
        # not a stricter one. This runs on the single snapshot that ended that
        # loop, so any extra condition here is a condition the loop never
        # promised to satisfy -- and the failure is silent, because a skipped
        # cleanup logs nothing at all. Requiring the frontend lamp to be OK
        # rather than OK-or-WARNING left a perfectly good update holding both
        # .previous layers and its downloaded archives for good: 1.08 GB
        # against 0.56 GB swept, on a bundle whose only fault was a dist whose
        # timestamps looked older than its sources. Requiring the backend lamp
        # to be OK as well, which is the shape this had while that was being
        # fixed, reintroduced the same silence from the other side.
        if self._healthy_bundle_checked:
            return
        paths = self._bundle_paths()
        if paths is None:
            return
        bundle, resources, data_dir = paths

        def log(message: str) -> None:
            append_update_log(data_dir, message)

        # Say so when it declines. Both times this broke, the whole symptom was
        # a gigabyte that never came back and an update.log that stopped after
        # "Relaunched" -- there was nothing to search for. A refusal that
        # names the two lamps turns the third occurrence into one grep.
        if not self._frontend_ready(snapshot):
            log(
                "Not reclaiming the previous layers yet: backend "
                f"{snapshot.backend.state.name}, frontend {snapshot.frontend.state.name}."
            )
            return
        self._healthy_bundle_checked = True

        previous = self._previous_generation_paths(resources)
        if sys.platform == "darwin":
            if not previous:
                return
            self._finish_healthy_macos_update(bundle, resources, data_dir, previous)
            return

        # ``.failed`` is the trail a rollback leaves: Windows would not let the
        # helper delete a directory whose DLLs were still mapped, so the
        # deletion was deferred to exactly here, where nothing holds them and
        # the download that produced them is equally spent.
        had_previous = any(
            (resources / f"{name}{suffix}").exists()
            for name in ("app", "runtime")
            for suffix in (".previous", ".failed")
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
    def _cleanup_holding_directory(bundle: Path) -> Path:
        return bundle.with_name(f".{bundle.name}.update-rollback")

    @staticmethod
    def _restore_held_previous(
        holding: Path,
        moved: list[tuple[Path, Path]],
    ) -> list[str]:
        errors: list[str] = []
        for original, saved in reversed(moved):
            try:
                if (saved.exists() or saved.is_symlink()) and not (
                    original.exists() or original.is_symlink()
                ):
                    os.replace(saved, original)
            except OSError as exc:
                errors.append(f"{original}: {exc}")
        try:
            holding.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if holding.exists():
                errors.append(f"{holding}: {exc}")
        return errors

    def _finish_healthy_macos_update(
        self,
        bundle: Path,
        resources: Path,
        data_dir: Path,
        previous: list[Path],
    ) -> None:
        """Remove sealed rollback content only around a required sign/verify."""

        def log(message: str) -> None:
            append_update_log(data_dir, message)

        holding = self._cleanup_holding_directory(bundle)
        if holding.exists() or holding.is_symlink():
            message = (
                f"Waveguide Generator could not finish update cleanup because recovery material "
                f"already exists at {holding}. The current version remains open and rollback "
                "material was retained."
            )
            log(message)
            self._report_bundle_failure(message)
            return

        moved: list[tuple[Path, Path]] = []
        try:
            holding.mkdir()
            for original in previous:
                saved = holding / original.name
                os.replace(original, saved)
                moved.append((original, saved))
        except OSError as exc:
            restore_errors = self._restore_held_previous(holding, moved)
            if moved and not restore_errors:
                try:
                    repair_bundle(bundle, platform_name="darwin", log=log)
                except ApplyUpdateError as repair_exc:
                    restore_errors.append(str(repair_exc))
            detail = "; ".join(restore_errors) if restore_errors else "rollback material restored"
            message = (
                f"Waveguide Generator could not stage healthy-update cleanup: {exc}. "
                f"Recovery result: {detail}."
            )
            log(message)
            self._report_bundle_failure(message)
            return
        try:
            repair_bundle(bundle, platform_name="darwin", log=log)
        except ApplyUpdateError as exc:
            restore_errors = self._restore_held_previous(holding, moved)
            repair_error: ApplyUpdateError | None = None
            if not restore_errors:
                try:
                    repair_bundle(bundle, platform_name="darwin", log=log)
                except ApplyUpdateError as restored_exc:
                    repair_error = restored_exc
            if restore_errors:
                outcome = "Rollback material could not be fully restored: " + "; ".join(
                    restore_errors
                )
            elif repair_error is not None:
                outcome = (
                    "Rollback material was restored, but the restored bundle also failed "
                    f"signature verification: {repair_error}"
                )
            else:
                outcome = "Rollback material was restored and the current version remains open."
            message = f"Waveguide Generator could not verify healthy-update cleanup: {exc}. {outcome}"
            log(message)
            self._report_bundle_failure(message)
            return

        try:
            shutil.rmtree(holding)
        except OSError as exc:
            # The holding directory is outside the signed bundle. Failure to
            # remove it wastes space but cannot invalidate the verified app.
            log(f"Could not remove obsolete update rollback material {holding}: {exc}")
        for original in previous:
            kind = "layer" if original.name in {"app.previous", "runtime.previous"} else "launcher file"
            log(f"Removed healthy-start rollback {kind}: {original}")
        self._remove_update_downloads(data_dir, log)

    @staticmethod
    def _remove_update_downloads(data_dir: Path, log: Callable[[str], None]) -> None:
        # The staged layers moved into the bundle; only downloaded archives are
        # left, including a runtime zip that can exceed 100 MB.
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
    def _report_bundle_failure(message: str, *, detail: str | None = None) -> None:
        """Log as usual, and always put a bundle's failure on screen.

        LaunchServices starts the bundle with stderr attached to nothing a
        user can read, yet not ``None``, so the console-only heuristic in
        ``_report_startup_failure`` would leave a failed start (or a completed
        rollback) invisible.
        """

        if detail is None:
            _report_startup_failure(message)
        else:
            _report_startup_failure(message, detail=detail)
        if sys.stderr is not None:
            _show_bundle_failure_dialog(message)

    def _report_desktop_failure(self, message: str, *, detail: str | None = None) -> None:
        if self._bundle_paths() is None:
            _report_startup_failure(message, detail=detail)
        else:
            self._report_bundle_failure(message, detail=detail)

    @staticmethod
    def _layer_runtime_ids(resources: Path) -> tuple[str | None, str | None]:
        """Return (the runtime the app requires, the runtime installed)."""

        def read(path: Path, key: str) -> str | None:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            value = payload.get(key) if isinstance(payload, dict) else None
            return value if isinstance(value, str) and value else None

        return (
            read(resources / "app" / "APP-MANIFEST.json", "runtimeId"),
            read(resources / "runtime" / "RUNTIME-MANIFEST.json", "runtimeId"),
        )

    @classmethod
    def _layers_disagree(cls, resources: Path) -> bool:
        """Report a live app and runtime that came from different generations.

        Unreadable or absent manifests are not treated as disagreement: an older
        bundle predates these fields, and refusing to start over a missing file
        would be worse than the mismatch this is looking for.
        """

        required, installed = cls._layer_runtime_ids(resources)
        return required is not None and installed is not None and required != installed

    def _roll_back_mixed_generation(self, resources: Path, data_dir: Path) -> bool:
        """Undo an update that was interrupted between its two layer renames."""

        def log(message: str) -> None:
            append_update_log(data_dir, message)

        log("The installed app and runtime are from different updates; rolling back.")
        if not self._previous_generation_paths(resources):
            # Nothing to restore: report rather than start a combination the app
            # never shipped, because the failure it produces would arrive later
            # and look like something else entirely.
            self._report_bundle_window_failure(
                "Waveguide Generator cannot start: an interrupted update left the "
                "application and its runtime from different versions, and there is "
                "no previous version to restore. Reinstall to repair it."
            )
            return False
        if rollback_previous_layers(resources, log=log):
            return True
        self._report_bundle_window_failure(
            "Waveguide Generator cannot start: an interrupted update left the "
            "application and its runtime from different versions, and restoring "
            "the previous version failed. Reinstall to repair it."
        )
        return False

    def _recover_interrupted_bundle_update(self) -> bool:
        """Restore a missing live layer from pending rollback before server start."""

        paths = self._bundle_paths()
        if paths is None:
            return True
        bundle, resources, data_dir = paths
        missing = [
            resources / name
            for name in ("runtime", "app")
            if not (resources / name).is_dir()
        ]
        if not missing:
            # Both layers exist, which is not the same as both belonging to one
            # generation. An update interrupted between its two renames leaves a
            # complete new app on the complete old runtime; nothing is missing,
            # and the app would then start against a runtime it never declared.
            # The manifests say which generation each layer is, so ask them.
            if self._layers_disagree(resources):
                return self._roll_back_mixed_generation(resources, data_dir)
            return True
        unavailable = [
            live
            for live in missing
            if not live.with_name(live.name + ".previous").is_dir()
        ]
        if unavailable:
            self._report_bundle_failure(
                "Waveguide Generator detected an interrupted update, but no previous layer is "
                "available for: " + ", ".join(str(path) for path in unavailable)
            )
            return False
        if not rollback_previous_layers(resources):
            self._report_bundle_failure(
                "Waveguide Generator detected an interrupted update, but could not restore every "
                "required layer and launcher file. Review update.log before changing the bundle."
            )
            return False
        try:
            repair_bundle(bundle, platform_name=sys.platform)
        except ApplyUpdateError as exc:
            message = (
                "Waveguide Generator restored files from an interrupted update, but could not "
                f"sign and verify the recovered bundle: {exc}"
            )
            append_update_log(data_dir, message)
            self._report_bundle_failure(message)
            return False
        append_update_log(
            data_dir,
            "Recovered missing live bundle layers from an interrupted update before startup.",
        )
        return True

    def _report_bundle_window_failure(self, message: str) -> None:
        """Rollback a failed bundle window start, then report the exact result."""

        paths = self._bundle_paths()
        if paths is None:
            _report_startup_failure(message)
            return
        bundle, resources, data_dir = paths
        if not self._previous_generation_paths(resources):
            self._report_bundle_failure(message)
            return
        self.controller.close()

        def log(entry: str) -> None:
            append_update_log(data_dir, entry)

        if self._hand_off_rollback(bundle, data_dir, log):
            # The helper waits for this process to exit before it touches
            # anything, so the dialog below can keep the window open for as
            # long as the user leaves it there.
            self._report_bundle_failure(f"{message}\n\n{ROLLBACK_HANDOFF_RESULT}")
            return

        rolled_back = rollback_previous_layers(resources, log=log)
        if rolled_back:
            try:
                repair_bundle(bundle, platform_name=sys.platform, log=log)
            except ApplyUpdateError as exc:
                result = (
                    "The previous files were restored, but the bundle could not be signed and "
                    f"verified: {exc}. Automatic recovery is incomplete."
                )
            else:
                result = "The previous version was restored. Reopen Waveguide Generator."
        else:
            result = ROLLBACK_FAILED_RESULT
        self._report_bundle_failure(f"{message}\n\n{result}")

    def _hand_off_rollback(
        self,
        bundle: Path,
        data_dir: Path,
        log: Callable[[str], None],
    ) -> bool:
        """Start the detached helper that restores the previous version.

        Windows will happily rename a directory whose DLLs are mapped, but it
        refuses to delete one, and this process has the failed ``runtime`` and
        ``app`` mapped into it. Rolling back from here left the launcher files
        beside the runtime unrestored -- a 0.2.5 runtime under a 0.2.6
        ``vcruntime140.dll`` -- so the work moves to a process that outlives
        this one and starts only once these mappings are gone.

        macOS has no such rule and its in-process rollback is the tested path,
        so the handoff is Windows-only; a handoff that cannot start falls back
        to it as well, because a rollback that happens here is still far better
        than none.
        """

        if sys.platform != "win32":
            return False
        try:
            command = launch_rollback_handoff(
                bundle,
                data_dir,
                os.getpid(),
                environ=dict(getattr(self.controller, "environ", os.environ)),
                server_args=tuple(getattr(self.controller, "server_args", ())),
            )
        except (UpdateHandoffError, OSError) as exc:
            log(
                f"Could not start the detached rollback helper: {exc} "
                "Rolling back in this process instead."
            )
            return False
        log(f"Started the detached rollback helper: {' '.join(command)}")
        return True

    def _report_bundle_startup_failure(self, snapshot: StatusSnapshot, cause: str) -> None:
        self._report_bundle_window_failure(self._failure_message(snapshot, cause))

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
        if not self._recover_interrupted_bundle_update():
            return None
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
        return snapshot

    def _load_webview(self) -> ModuleType:
        return importlib.import_module("webview")

    def _prepare_windows_webview(self) -> None:
        if sys.platform != "win32":
            return
        try:
            self.pythonnet_loader()
        except Exception as exc:  # noqa: BLE001 - translated to an actionable repair
            raise WindowsWebViewUnavailable(
                f"pythonnet could not load: {type(exc).__name__}: {exc}"
            ) from exc
        if not self.webview2_probe():
            raise WindowsWebViewUnavailable("the Microsoft Edge WebView2 runtime was not found")

    def _fallback_from_windows_webview(self, snapshot: StatusSnapshot, exc: Exception) -> int:
        self._report_desktop_failure(
            "Waveguide Generator could not initialize its Windows desktop window: "
            f"{type(exc).__name__}: {exc}\n\n{WEBVIEW2_REPAIR}\n\n"
            "The interface will open in your default browser instead.",
            detail=traceback.format_exc(),
        )
        try:
            self.browser_fallback(snapshot.url)
        except Exception as fallback_exc:  # noqa: BLE001 - must be visible under pythonw
            message = (
                "Waveguide Generator also could not open the browser fallback: "
                f"{type(fallback_exc).__name__}: {fallback_exc}"
            )
            if self._bundle_paths() is None:
                _report_startup_failure(message, detail=traceback.format_exc())
            else:
                self._report_bundle_window_failure(message)
            return 1
        # The browser is now showing the interface, so this launch succeeded as
        # surely as one with a native window, and the update it may have just
        # applied has to be committed here too. Without this a machine that
        # always takes the fallback -- no WebView2 -- would keep every previous
        # layer and every downloaded archive for ever, and eventually refuse the
        # next update for having rollback material pending.
        self._finish_healthy_bundle_update(snapshot)
        return 0

    def open_window(self, url: str) -> None:
        """Open one same-origin secondary window for the JavaScript bridge."""

        base_url = self.controller.url
        target = urljoin(base_url, url)
        if _origin(target) is None or _origin(target) != _origin(base_url):
            raise ValueError("Desktop windows may open only same-origin URLs")
        if self._webview is None:
            raise RuntimeError("The desktop window has not started")
        self._webview.create_window(WINDOW_TITLE, target)

    def _set_window_title(self, title: str) -> None:
        """Retitle the live window, if this toolkit build lets us.

        Defensive in the same way as the ``destroy`` calls below: the window is
        whatever pywebview handed back, and a title we cannot set must not take
        down the poll loop that is reporting a problem.
        """

        setter = getattr(self._window, "set_title", None)
        if not callable(setter):
            return
        try:
            setter(title)
        except Exception as exc:  # noqa: BLE001 - cosmetic, and never worth raising
            _log_startup_failure(f"Could not retitle the desktop window: {exc}")

    def _report_backend_loss(self, message: str) -> None:
        """Put the loss on screen without stalling the loop that found it.

        On Windows this ends in a modal ``MessageBoxW``, which blocks until
        somebody selects OK. Blocking *here* would stall the caller: the
        controller's watcher thread, which is also what would notice a
        replacement server, and before that the window loop itself. Either way
        quitting the application would appear to hang behind a dialog
        explaining that it is already broken.
        """

        _log_startup_failure(message)
        self._loss_report = threading.Thread(
            target=self._report_desktop_failure,
            args=(message,),
            name="wg2-backend-loss",
            daemon=True,
        )
        self._loss_report.start()

    def _backend_unreachable(self, snapshot: StatusSnapshot) -> None:
        """React to a backend that goes away while the window is open.

        The window has no lamps, the status bar in the interface reports only
        the preview socket, and the interface itself is served by the very
        backend that has gone -- so a mid-session loss had no symptom at all
        except the Solve button going grey and staying grey. That is the report
        this exists to answer, and it is answered once.

        There is no grace period here any more, and no separate "unreachable,
        but possibly only for a moment" level. Both existed to hedge against an
        ambiguous reading: a single 0.35 s HTTP timeout is entirely normal while
        the server is still importing its solvers, so a failure had to outlive
        five seconds of polling before it could be believed. Nothing takes those
        readings now. This is called by the controller's watcher when it is
        already certain -- the child's process handle returned from ``wait()``,
        or an adopted instance failed two probes in a row -- and waiting a
        further five seconds to believe a process that has already exited would
        only delay the message.
        """

        # Called on the controller's watcher thread, like the reporting thread
        # below and like the old poll loop before it; ``_set_window_title``
        # swallows a toolkit that dislikes that.
        self._set_window_title(UNREACHABLE_TITLE)
        if self._backend_loss_reported:
            return
        self._backend_loss_reported = True
        self._report_backend_loss(BACKEND_LOST.format(reason=snapshot.backend.reason))

    def _watch_backend(self) -> None:
        """Ask the controller to tell us if the backend goes, and stop asking.

        Idempotent and self-healing, which is why the loop calls it on every
        turn rather than once: a controller restarted after a failed update
        handoff needs a watcher for its new child, and a controller that has
        already reported a loss must not be handed another one.
        """

        if self._backend_loss_reported:
            return
        self.controller.watch_backend(self._backend_unreachable)

    def _window_loop(self) -> None:
        """Own the window until it closes. Deliberately not a poll of anything.

        What is left in here is one file check for an in-app update request the
        server writes on demand; backend liveness is the controller's watcher,
        which blocks rather than asks. The measured cost of the version that did
        poll was 1,220 HTTP requests in 181 idle seconds, 610 of them re-reading
        an index.html that had not changed.
        """

        if self._startup_snapshot is not None:
            # pywebview invokes this callback only after its native event loop
            # has started. HTTP readiness alone is not enough to discard rollback.
            self._finish_healthy_bundle_update(self._startup_snapshot)
        window = self._window
        closed = getattr(getattr(window, "events", None), "closed", None)
        wait = getattr(closed, "wait", None)
        # Before the first sleep, not after it: the backend can die in the
        # second this loop would otherwise spend not yet watching it.
        self._watch_backend()
        if not callable(wait):
            return
        while not wait(self.idle_interval):
            self._watch_backend()
            if self._hand_off_update(window):
                return

    def _pending_bundle_update_paths(self) -> list[Path]:
        paths = self._bundle_paths()
        if paths is None:
            return []
        bundle, resources, _data_dir = paths
        pending = self._previous_generation_paths(resources)
        pending.extend(
            path
            for path in sorted(resources.glob("*.failed"))
            if path.exists() or path.is_symlink()
        )
        holding = self._cleanup_holding_directory(bundle)
        if holding.exists() or holding.is_symlink():
            pending.append(holding)
        return pending

    def _restart_after_failed_handoff(self) -> str | None:
        """Restart the server and wait until the existing window is usable again."""

        try:
            snapshot = self.controller.start()
        except Exception as exc:  # noqa: BLE001 - include controller startup faults
            return f"restart raised {type(exc).__name__}: {exc}"
        deadline = time.monotonic() + self.startup_timeout
        while not self._frontend_ready(snapshot):
            if self._startup_failed(snapshot):
                return self._failure_message(snapshot, "did not restart")
            if time.monotonic() >= deadline:
                return self._failure_message(
                    snapshot,
                    f"did not restart within {self.startup_timeout:.0f} seconds",
                )
            time.sleep(self.poll_interval)
            snapshot = self.controller.poll()
        return None

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
        if isinstance(request, BundleUpdateRequest):
            pending = self._pending_bundle_update_paths()
            if pending:
                self._report_bundle_failure(
                    f"Waveguide Generator cannot start the {label} update because rollback "
                    "material from an earlier update is still present:\n"
                    + "\n".join(str(path) for path in pending)
                    + "\n\nThe current version remains open. Review update.log before trying again."
                )
                return False
        try:
            if isinstance(request, BundleUpdateRequest):
                # Leave one progress-poll window in which the SPA can render
                # "ready" after the server publishes the request file.
                time.sleep(self.update_ready_delay)
                self.controller.close()
            self.controller.launch_update(request)
        except UpdateHandoffError as exc:
            if isinstance(request, BundleUpdateRequest):
                restart_error = self._restart_after_failed_handoff()
                if restart_error is None:
                    self._report_bundle_failure(
                        f"Waveguide Generator could not start the {label} update: {exc}\n\n"
                        "The current version was restarted and remains open."
                    )
                    return False
                destroy = getattr(window, "destroy", None)
                if callable(destroy):
                    destroy()
                self._exit_code = 1
                self._report_bundle_failure(
                    f"Waveguide Generator could not start the {label} update: {exc}\n\n"
                    f"The current version also could not restart: {restart_error}. "
                    "The unusable window was closed."
                )
                return True
            _report_startup_failure(
                f"Waveguide Generator could not start the {label} update: {exc}\n\n"
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

        snapshot: StatusSnapshot | None = None
        try:
            snapshot = self._wait_for_frontend()
            if snapshot is None:
                return 1
            try:
                webview = self._load_webview()
            except ImportError as exc:
                message = (
                    "Waveguide Generator could not open a desktop window because "
                    f"pywebview is unavailable: {exc}\n\n{PYWEBVIEW_REPAIR}"
                )
                if self._bundle_paths() is None:
                    _report_startup_failure(message, detail=traceback.format_exc())
                else:
                    self._report_bundle_window_failure(message)
                return 1
            try:
                self._prepare_windows_webview()
            except WindowsWebViewUnavailable as exc:
                return self._fallback_from_windows_webview(snapshot, exc)
            self._webview = webview
            webview.settings["ALLOW_DOWNLOADS"] = True
            self._startup_snapshot = snapshot
            self._window = webview.create_window(
                WINDOW_TITLE,
                snapshot.url,
                js_api=self.js_api,
                width=1440,
                height=900,
                min_size=(1100, 700),
            )
            try:
                webview.start(func=self._window_loop)
            except Exception as exc:  # noqa: BLE001 - native initialization boundary
                if sys.platform == "win32" and (
                    "pythonnet" in str(exc).casefold() or "webview2" in str(exc).casefold()
                ):
                    return self._fallback_from_windows_webview(snapshot, exc)
                raise
            return self._exit_code
        except Exception as exc:  # noqa: BLE001 - native startup must remain visible
            message = (
                "Waveguide Generator could not open its desktop window: "
                f"{type(exc).__name__}: {exc}"
            )
            if snapshot is not None and self._bundle_paths() is not None:
                self._report_bundle_window_failure(message)
            else:
                self._report_desktop_failure(message, detail=traceback.format_exc())
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
