#!/usr/bin/env python3
"""Start the local Waveguide Generator application server."""

from __future__ import annotations

import argparse
from collections.abc import Callable, MutableMapping
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import webbrowser


_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

import uvicorn  # noqa: E402 - the checkout root must be importable first

from launch.serve_options import (  # noqa: E402
    PROGRAM_NAME,
    UPDATE_RELEASED_FILENAME,
    UPDATE_STAGING_ROOT_ENV,
    add_server_arguments,
)
from server.app import BUILD, create_app  # noqa: E402
from server.platform.console import harden_console  # noqa: E402
from server.platform.instance import (  # noqa: E402
    DEFAULT_PID_POLL_INTERVAL,
    PID_EXITED,
    STOP_REQUESTED,
    InstanceAlreadyRunning,
    InstanceLock,
    InstanceLockError,
    StopSignal,
    pid_is_running,
    requested_port,
    reserve_port,
    wait_for_pid_exit,
    watch_directory_entries,
)
from server.platform.logging_setup import flush_logs, setup_logging  # noqa: E402
from server.platform.paths import app_root, default_runs_dir, ensure_data_layout  # noqa: E402
from server.platform.shutdown_backstop import ShutdownBackstop, lingering_threads  # noqa: E402
from server.platform.temp_session import (  # noqa: E402
    TemporarySession,
    sweep_stale_temporary_directories,
)
from server.platform.signal_rearm import (  # noqa: E402
    register_signal_rearm,
    unregister_signal_rearm,
)
from server.protocol.frame import DEFAULT_MAX_FRAME_BYTES  # noqa: E402
from scripts.migrate_v1 import MigrationError, auto_migrate_v1  # noqa: E402


HOST = "127.0.0.1"
#: After a stop's cleanup, how long the process's own threads get to finish
#: before it ends without them. Idle executor workers told to stop need
#: moments; one inside a native call would hold interpreter exit forever.
LINGERING_JOIN_SECONDS = 0.5
REPO_ROOT = app_root()
SPA_STAMP = REPO_ROOT / "frontend" / "dist" / ".wg2-spa.json"
VERSION_MANIFEST = REPO_ROOT / "shared" / "version.json"


def _installer_hint() -> str:
    if sys.platform == "darwin":
        return "installers/macos/install-wg.command"
    if os.name == "nt":
        return r"installers\windows\install-and-update.bat"
    return "installers/linux/install.sh"


def _release_interface_error() -> str | None:
    """Refuse a checksum-stamped release interface from a different tree version.

    Unstamped distributions are local developer builds and remain supported by
    ``--skip-spa``. Once a release stamp exists, however, its version is the
    authoritative record of which backend the prebuilt interface belongs to.
    """

    if not SPA_STAMP.is_file():
        return None

    try:
        backend_version = json.loads(VERSION_MANIFEST.read_text(encoding="utf-8"))[
            "version"
        ]
        interface_version = json.loads(SPA_STAMP.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return (
            "Waveguide Generator did not start because its release version metadata "
            f"could not be read: {exc}. Run the installer again "
            f"({_installer_hint()}) to install the matching interface."
        )

    if (
        not isinstance(backend_version, str)
        or not backend_version
        or not isinstance(interface_version, str)
        or not interface_version
    ):
        return (
            "Waveguide Generator did not start because its release version metadata "
            f"is invalid. Run the installer again ({_installer_hint()}) to install "
            "the matching interface."
        )

    if interface_version == backend_version:
        return None
    return (
        "Waveguide Generator did not start because the installed release interface "
        f"is v{interface_version}, but the backend tree is v{backend_version}. Run "
        f"the installer again ({_installer_hint()}) to install the matching interface."
    )


def _publish_status_ready(control_path: Path | None, port: int) -> None:
    """Atomically tell the status owner which socket was actually reserved."""

    if control_path is None:
        return
    ready_path = control_path.with_name("ready.json")
    temporary = ready_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"host": HOST, "port": port}) + "\n", encoding="utf-8")
    temporary.replace(ready_path)


def _start_beat_cpu_provisioning() -> None:
    """Give a GPU-less Windows or Linux install a BEAT CPU runtime, in the background.

    The source install provisions from ``scripts/bootstrap.py``, which the
    packaged application never runs: it ships a prebuilt runtime layer with
    ``hornlab-beat-bem`` already installed, so without this nothing would ever
    fetch the Julia that backend needs and the engine would be permanently
    unavailable on exactly the machines it exists for.

    Here rather than in ``create_app`` for the same reason ``solver_warmup`` is
    decided here: this is the production launcher, and building an app object --
    which hundreds of tests and any embedder does -- must not start downloading
    a runtime. Every gate, and the work itself, is in
    ``server/solver/beat_cpu_runtime.py``; this call only says "in a launched
    application, do it", and cannot fail the launch.
    """

    try:
        from server.solver.beat_cpu_runtime import start_cpu_provisioning

        start_cpu_provisioning()
    except Exception:  # noqa: BLE001 - an optional engine never blocks a launch
        logging.getLogger("wg.launch").exception(
            "Could not start BEAT CPU runtime provisioning"
        )


def _solver_warmup_enabled() -> bool:
    """Enable the native solver warmup only by explicit operator request.

    The warmup is a real, non-cancellable solve in a daemon thread. It is not
    serialized with user jobs and cannot be joined safely during a fast
    shutdown, so release launches must not start it implicitly.
    """

    return os.environ.get("WG2_SOLVER_WARMUP") == "1"


def _update_staging_root(
    args: argparse.Namespace, environ: MutableMapping[str, str]
) -> Path | None:
    """The staging folder beside the bundle that this server's launcher accepts, if any.

    The status window puts it in this process's environment
    (``UPDATE_STAGING_ROOT_ENV``); it is taken out again whatever it says, so
    no child of the server inherits it. It counts only with a launcher to hand
    off to (``--status-control``), and the update service still checks it
    against its own derivation (the updater review §2.7).
    """

    value = environ.pop(UPDATE_STAGING_ROOT_ENV, None)
    if args.status_control is None or not value:
        return None
    return Path(value)


def build_parser() -> argparse.ArgumentParser:
    """The server's own parser, over the shared option surface.

    ``prog`` is named rather than derived: this module is reached as
    ``launchers/desktop.py``, as ``launchers/statusapp/__main__.py`` and as an
    installed ``waveguide-generator``, and argparse would title its usage with
    whichever file happened to be ``sys.argv[0]`` -- a path the user did not
    type and cannot run.
    """

    return add_server_arguments(
        argparse.ArgumentParser(prog=PROGRAM_NAME, description=__doc__)
    )


def _open_browser_when_ready(port: int, stop: threading.Event) -> None:
    url = f"http://{HOST}:{port}/"
    deadline = time.monotonic() + 30.0
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.25):
                webbrowser.open(url)
                logging.getLogger("wg.launch").info("Opened browser at %s", url)
                return
        except OSError:
            stop.wait(0.1)
    if not stop.is_set():
        logging.getLogger("wg.launch").warning(
            "The server did not become ready within 30 seconds, so the browser was not "
            "opened. Check the errors above and %s.",
            url,
        )


def _take_update_release(control_path: Path, on_released: Callable[[str], object]) -> None:
    """Pass on the status window's release notice, if it wrote one (contract §4.2).

    The file existing is the message; the reason in it is best effort. It is
    removed before it is acted on, so one notice releases once.
    """

    notice = control_path.with_name(UPDATE_RELEASED_FILENAME)
    if not notice.is_file():
        return
    reason = "the status window discarded the update request"
    try:
        payload = json.loads(notice.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("reason"), str) and payload["reason"]:
            reason = payload["reason"]
    except (OSError, ValueError):
        pass
    try:
        notice.unlink()
    except OSError:
        pass
    try:
        on_released(reason)
    except Exception:  # noqa: BLE001 - the stop watch must outlive a bad callback
        logging.getLogger("wg.launch").exception("Could not release the update restart")


def _release_update_restart(app: object, reason: str) -> None:
    """Clear the server's restart latch: its status window will not hand off."""

    approval = getattr(getattr(app, "state", None), "update_restart", None)
    if approval is not None:
        approval.release(f"the status window did not hand off: {reason}")


def _watch_statusapp(
    server: uvicorn.Server,
    control_path: Path,
    parent_pid: int | None,
    stop: threading.Event,
    *,
    poll_interval: float = DEFAULT_PID_POLL_INTERVAL,
    on_stop: Callable[[str], object] | None = None,
    on_update_released: Callable[[str], object] | None = None,
) -> None:
    """Gracefully stop when the owning status window closes or disappears.

    ``on_update_released`` is told when the status window discarded an update
    request instead of handing off (``docs/reference/UPDATE-TRANSACTION-CONTRACT.md``
    §4.2): no restart is coming, so the server clears its restart latch. The
    notice is a file beside the control file, so it rides the same wakeups.

    ``on_stop`` is told why, after ``should_exit`` is set. ``main`` passes the
    shutdown backstop's ``begin``, so every stop from here runs against a
    deadline. That includes a parent that died without asking: on macOS and
    Linux the server outlives a crashed launcher, and nobody else will end it.

    This thread lives for the whole run of an attached server, so what it costs
    while nothing is happening is the point. It used to re-test both conditions
    every 150 ms: 6.7 wakeups a second forever, each one a ``stat`` plus, on
    Windows, an OpenProcess / WaitForSingleObject / CloseHandle round trip
    through ctypes. On a laptop it is the wakeup rate rather than the CPU time
    that matters -- a timer that never lets the package reach a deep C-state
    costs real watts for a thread that is, almost always, about to learn that
    nothing changed.

    Both conditions are waitable instead. A process handle is signalled when the
    parent exits, and a directory notification is signalled when the control
    file appears, so where the platform supports it this parks in a single
    ``WaitForMultipleObjects`` with no timeout at all: no wakeups, and both
    events observed the moment they happen rather than up to a poll late.
    Elsewhere -- and whenever a handle cannot be armed -- it degrades to exactly
    the poll it always did, at exactly the interval it always used, so shutdown
    latency never regresses.

    The waits are only ever wakeup sources. Every decision below still comes
    from re-testing the real conditions, which keeps one code path for both
    platforms and keeps the thread honest about a file that appeared before the
    watch was armed.
    """

    log = logging.getLogger("wg.launch")
    # The control file's directory is created by the status window before the
    # server is spawned, and it is where ready.json and update.json live too, so
    # a handful of unrelated wakes are possible and harmless: each one costs one
    # ``is_file()``.
    wakeup = watch_directory_entries(control_path.parent)
    try:
        while not stop.is_set():
            # Test before waiting, always. On the first pass this catches a
            # control file or a dead parent from before the thread existed, and
            # on later passes it is the arm-then-check ordering that stops a
            # change during the handover from being missed by both the wait and
            # the test.
            if on_update_released is not None:
                _take_update_release(control_path, on_update_released)
            requested = control_path.is_file()
            parent_gone = parent_pid is not None and not pid_is_running(parent_pid)
            if requested or parent_gone:
                reason = "status window requested quit" if requested else "status window exited"
                log.info("Stopping because the %s", reason)
                server.should_exit = True
                if on_stop is not None:
                    on_stop(reason)
                return

            outcome = wait_for_pid_exit(
                parent_pid,
                stop,
                # With a waitable control file there is nothing left for a timer
                # to discover; without one the timeout *is* the control-file
                # check, and it keeps the historical interval.
                timeout=None if wakeup is not None else poll_interval,
                wakeups=() if wakeup is None else (wakeup,),
                poll_interval=poll_interval,
            )
            if outcome == PID_EXITED:
                # The kernel signalled the process object. That is a stronger
                # answer than any probe can give, so do not re-test it.
                log.info("Stopping because the status window exited")
                server.should_exit = True
                if on_stop is not None:
                    on_stop("status window exited")
                return
            if outcome == STOP_REQUESTED:
                return
    finally:
        if wakeup is not None:
            wakeup.close()


#: How long ``--no-gui`` keeps asking its own server for a healthy start after
#: uvicorn reports that it started, how long it waits between attempts, and how
#: long one request may take.
HEALTHY_START_PROBE_SECONDS = 20.0
HEALTHY_START_PROBE_INTERVAL = 0.25
HEALTHY_START_PROBE_TIMEOUT = 2.0


def _probe_healthy_start(port: int, timeout: float) -> str | None:
    """Ask this process's own server for ``/health`` and the interface route.

    ``None`` when both answer the way a healthy start of this build must
    (``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.6): ``/health`` names
    this build, and the interface route serves HTML. Otherwise, what was wrong.
    uvicorn reports started only after the application's startup handlers have
    run, and the job store is opened there, so a server that answers at all has
    its stores and local services up.
    """

    import http.client

    for target in ("/health", "/"):
        connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
        try:
            connection.request(
                "GET", target, headers={"User-Agent": "WaveguideGenerator-HealthyStart"}
            )
            response = connection.getresponse()
            status, body = response.status, response.read(256 * 1024)
        except (OSError, http.client.HTTPException) as exc:
            return f"GET {target} failed: {exc or type(exc).__name__}"
        finally:
            connection.close()
        if status != 200:
            return f"GET {target} answered HTTP {status}"
        if target == "/health":
            try:
                build = json.loads(body).get("build")
            except (ValueError, AttributeError):
                return "GET /health did not answer with a JSON object"
            if build != BUILD:
                return f"GET /health named build {build!r}, not {BUILD!r}"
        elif b"<html" not in body.lower():
            return "GET / did not serve the interface"
    return None


#: How long a stopping --no-gui start waits for a settle already under way. A
#: stop that came from a signal or a closed console has also begun the shutdown
#: backstop (``server/platform/shutdown_backstop.py``), which ends the process
#: about 5 s later, so such a stop gives a settle about 5 s, not this.
HEALTHY_START_SETTLE_WAIT = 60.0


class _NoGuiHealthyStart:
    """Settle, or report, the update transaction of a start no controller owns.

    ``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.5, for ``--no-gui``.
    There is no status controller watching the interface, so this process asks
    itself. It settles -- ``commit_transaction`` and the scoped healthy-start
    cleanup, the same code and the same ``update.log`` lines as the window --
    only after uvicorn reports that it started and a self-probe of ``/health``
    and the interface route succeeds. Otherwise it writes the transaction id and
    the reason to ``update.log``, and settles nothing.

    It exists from the moment the data directory is known, so every way the
    start can end reports: a refused interface, a failed migration, no free
    port, ``create_app`` raising, the server stopping before it answered.
    Only :meth:`start` needs the server.

    It is never started from ``create_app``. The servers the window and browser
    modes start run that too, and their controllers settle on their own
    evidence; only this launcher knows no controller owns the server.
    """

    def __init__(self, paths: tuple[Path, Path, Path]) -> None:
        self._paths = paths
        self._server: uvicorn.Server | None = None
        self._port = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._decided = False
        self._settling = False
        self._last_problem: str | None = None
        self._thread: threading.Thread | None = None

    def start(self, server: uvicorn.Server, port: int) -> None:
        self._server = server
        self._port = port
        self._thread = threading.Thread(
            target=self._run, name="wg2-healthy-start", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not getattr(self._server, "started", False):
            if self._stop.wait(0.05):
                return
        deadline = time.monotonic() + HEALTHY_START_PROBE_SECONDS
        while True:
            problem = _probe_healthy_start(self._port, HEALTHY_START_PROBE_TIMEOUT)
            if problem is None:
                break
            self._last_problem = problem
            if time.monotonic() >= deadline:
                self._report(
                    "the --no-gui self-probe of /health and the interface kept failing: "
                    + problem
                )
                return
            if self._stop.wait(HEALTHY_START_PROBE_INTERVAL):
                return
        with self._lock:
            if self._decided:
                return
            self._decided = True
            self._settling = True
        log = logging.getLogger("wg.launch")
        try:
            from launchers.statusapp.healthy_start import HealthyStartSettlement

            HealthyStartSettlement(lambda: self._paths).settle(
                ready=True,
                evidence=f"this --no-gui server answered /health as {BUILD} and served the interface",
                # --no-gui opens no window of its own, not even to report.
                report=log.error,
            )
        except Exception:  # noqa: BLE001 - a running server outlives a failed cleanup
            log.exception("Could not settle the update transaction after a healthy start")
        finally:
            with self._lock:
                self._settling = False

    def _report(self, reason: str) -> None:
        with self._lock:
            if self._decided:
                return
            self._decided = True
        try:
            from launchers.statusapp.healthy_start import report_unconfirmed_start

            report_unconfirmed_start(self._paths, reason)
        except Exception:  # noqa: BLE001 - reporting must not replace the exit path
            logging.getLogger("wg.launch").exception("Could not report the update transaction")

    def finish(self, reason: str) -> None:
        """This start is over: report the transaction unless something decided it.

        A settle already under way is waited for, up to
        ``HEALTHY_START_SETTLE_WAIT``, rather than abandoned: on macOS it moves
        ``.previous`` out of the bundle and re-seals it, and a process that
        exits between the two leaves the bundle unsealed and the rollback
        material in the holding directory. A stop that came from a signal has
        begun the shutdown backstop, which ends the process about 5 s later, so
        that is what such a stop gives a settle; a second Ctrl+C ends it at once
        (contract §4.5).
        """

        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=HEALTHY_START_PROBE_TIMEOUT + 3.0)
            deadline = time.monotonic() + HEALTHY_START_SETTLE_WAIT
            while thread.is_alive() and self._settling and time.monotonic() < deadline:
                thread.join(timeout=0.5)
        if self._last_problem is not None:
            reason = f"{reason} (the last self-probe said: {self._last_problem})"
        self._report(reason)


def _no_gui_healthy_start(data_dir: Path) -> _NoGuiHealthyStart | None:
    """The healthy-start check of a bundle that no status controller owns, or None."""

    if os.environ.get("WG2_BUNDLE") != "1":
        return None
    try:
        from launchers.statusapp.healthy_start import resolve_bundle_paths

        paths = resolve_bundle_paths(os.environ, app_root(), data_dir)
    except Exception:  # noqa: BLE001 - never refuse a start over this
        logging.getLogger("wg.launch").exception("Could not prepare the healthy-start check")
        return None
    if paths is None:
        return None
    return _NoGuiHealthyStart(paths)


def _not_confirmed(check: _NoGuiHealthyStart | None, reason: str) -> None:
    """A --no-gui start is ending: report an open update transaction, and why (§4.5)."""

    if check is not None:
        check.finish(reason)


def _shutdown_signals() -> tuple[int, ...]:
    """Signals that mean "stop the server", including Windows' Ctrl+Break.

    Windows cannot deliver SIGTERM from another process -- os.kill maps to
    TerminateProcess -- so SIGINT from Ctrl+C is otherwise the only way in.
    SIGBREAK is what Ctrl+Break raises, and it is the only stop signal that can
    be sent to a specific process group, which is what makes the graceful path
    testable there at all.
    """

    signals = [signal.SIGINT, signal.SIGTERM]
    windows_break = getattr(signal, "SIGBREAK", None)
    if windows_break is not None:
        signals.append(windows_break)
    return tuple(signals)


@contextmanager
def _capture_shutdown_signals(
    server: uvicorn.Server, backstop: ShutdownBackstop | None = None
):
    """Install WG's handlers after Uvicorn has created its event loop.

    Uvicorn 0.49 enters ``Server.capture_signals`` inside the loop runner and
    re-raises every captured signal after restoring the prior handler. On
    uvloop, SIGTERM's prior handler is the platform default by then, so the
    re-raise terminates the process before WG's outer ``finally`` can release
    its lock and stop logging. Owning the capture context keeps graceful exit
    semantics and lets ``main`` finish all application cleanup.
    """

    previous: dict[int, object] = {}

    def request_shutdown(signum: int, _frame: object) -> None:
        logging.getLogger("wg.launch").info(
            "Received %s; finishing active requests and shutting down",
            signal.Signals(signum).name,
        )
        if server.should_exit and signum == signal.SIGINT:
            server.force_exit = True
            if backstop is not None:
                # A second Ctrl+C: the user has stopped waiting for the
                # graceful path, so the process stops waiting too.
                backstop.exit_now("a second Ctrl+C")
        else:
            server.should_exit = True
            if backstop is not None:
                backstop.begin(f"{signal.Signals(signum).name} received")

    def install() -> None:
        for signum in _shutdown_signals():
            signal.signal(signum, request_shutdown)

    for signum in _shutdown_signals():
        previous[signum] = signal.getsignal(signum)
    token = register_signal_rearm(install)
    install()
    try:
        yield
    finally:
        unregister_signal_rearm(token)
        for signum, handler in previous.items():
            signal.signal(signum, handler)  # type: ignore[arg-type]


def _start_temporary_session() -> TemporarySession | None:
    """Give this process a temporary directory of its own, and sweep dead ones.

    A stop during a build ends without cleanup, so whatever its
    ``TemporaryDirectory`` held stays behind (``server/platform/temp_session.py``).
    This start removes what dead processes left, and nothing a live one owns.
    A failure here never stops the start: the process then writes where it
    always did.
    """

    log = logging.getLogger("wg.launch")
    base = Path(tempfile.gettempdir())
    session: TemporarySession | None
    try:
        session = TemporarySession.create(base)
    except OSError as exc:
        log.warning(
            "Could not create this process's own temporary directory in %s (%s); "
            "writing there directly",
            base,
            exc,
        )
        session = None
    else:
        session.activate()
    removed = sweep_stale_temporary_directories(
        base, keep=session.path if session is not None else None
    )
    if removed:
        log.info(
            "Removed %d temporary director%s left by processes that did not exit cleanly",
            len(removed),
            "y" if len(removed) == 1 else "ies",
        )
    return session


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_dir is not None:
        os.environ["WG2_DATA_DIR"] = str(args.data_dir)

    lock: InstanceLock | None = None
    listener: socket.socket | None = None
    # Only a start that no status controller owns settles its own update
    # transaction (contract §4.5). It is set up as soon as the data directory
    # is known, so every exit below can report a transaction it left open.
    no_gui_start: _NoGuiHealthyStart | None = None
    try:
        paths = ensure_data_layout()
        setup_logging(paths)
        if args.status_control is None:
            no_gui_start = _no_gui_healthy_start(paths.root)
        preferred_port = requested_port(args.port)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Waveguide Generator could not start: {exc}", file=sys.stderr)
        # setup_logging runs before requested_port so this path can already own
        # a QueueListener (for example, when WG2_PORT is invalid). Treat early
        # configuration failures like every later exit and drain it explicitly.
        _not_confirmed(
            no_gui_start, f"the --no-gui start stopped before its server started: {exc}"
        )
        flush_logs()
        return 1

    open_browser = not args.no_browser and os.environ.get("WG2_NO_BROWSER") != "1"

    lock = InstanceLock(paths.locks)
    try:
        # The process lock is authoritative and must be checked before any port
        # scan so an existing instance always produces the documented exit 2.
        lock.acquire(preferred_port)
    except InstanceAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        # Double-clicking the launcher a second time is a request to use the
        # application, not a request for an error message. The lock metadata
        # already names the live port, so honour the intent and show the
        # running instance. The exit code stays 2: that is the documented
        # contract, and the launcher distinguishes it from a real failure.
        if open_browser and exc.info.port > 0:
            url = f"http://{HOST}:{exc.info.port}/"
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - a missing browser is not an error here
                pass
            else:
                print(f"Opened the running instance at {url}", file=sys.stderr)
        _not_confirmed(
            no_gui_start,
            "another Waveguide Generator server holds the instance lock, so the build "
            "this --no-gui start launched never served",
        )
        flush_logs()
        return 2
    except InstanceLockError as exc:
        print(f"Waveguide Generator could not start: {exc}", file=sys.stderr)
        _not_confirmed(no_gui_start, f"the --no-gui start could not take the instance lock: {exc}")
        flush_logs()
        return 1

    interface_error = _release_interface_error()
    if interface_error is not None:
        print(interface_error, file=sys.stderr)
        logging.getLogger("wg.launch").error(interface_error)
        _not_confirmed(no_gui_start, f"the --no-gui start refused its interface: {interface_error}")
        lock.release()
        flush_logs()
        return 1

    try:
        auto_migrate_v1(REPO_ROOT, paths.root, lock)
    except (MigrationError, OSError, sqlite3.Error) as exc:
        print(f"Waveguide Generator could not migrate v1 runs: {exc}", file=sys.stderr)
        logging.getLogger("wg.launch").exception("Automatic v1 run migration failed")
        _not_confirmed(no_gui_start, f"the --no-gui start could not migrate v1 runs: {exc}")
        lock.release()
        flush_logs()
        return 1

    try:
        # Keep the selected socket bound through Uvicorn startup. Binding itself
        # is the retry loop, eliminating the probe-then-bind TOCTOU window.
        listener, port = reserve_port(preferred_port, host=HOST)
        lock.update_port(port)
        _publish_status_ready(args.status_control, port)
    except (OSError, InstanceLockError) as exc:
        print(f"Waveguide Generator could not start: {exc}", file=sys.stderr)
        _not_confirmed(no_gui_start, f"the --no-gui start could not reserve a port: {exc}")
        lock.release()
        flush_logs()
        return 1

    stop_browser = threading.Event()
    # The status watchdog blocks in a kernel wait that cannot see a plain
    # threading.Event, so give it one the kernel can signal; without it that
    # thread would have to keep ticking just to notice shutdown. It is
    # deliberately never closed: the watchdog is a daemon thread that may still
    # be inside a wait on this handle when main returns, and closing a handle
    # out from under a live wait is undefined behaviour on Win32.
    stop_status_watch: threading.Event = (
        StopSignal() if args.status_control is not None else threading.Event()
    )
    shutdown_complete = threading.Event()
    # Every stop path below begins this budget, and the budget ends the process
    # if cleanup outlives it (server/platform/shutdown_backstop.py). Only a stop
    # request arms it; building and serving are untouched.
    backstop = ShutdownBackstop()
    # Threads that exist before this start serves are not its own. Only an
    # in-process caller (a test) has any; see ``lingering_threads``.
    preexisting_threads = {thread.ident for thread in threading.enumerate()}
    # Before anything writes a temporary file, so all this process leaves
    # behind is in one directory a later start can prove is dead.
    temporary = _start_temporary_session()
    # Before the app, so the capability probe this start runs can already report
    # "provisioning" rather than "not provisioned". It only starts a thread.
    _start_beat_cpu_provisioning()
    stop_reason = "the --no-gui server stopped before it confirmed a healthy start"
    # What ``main`` returns, for an exit that has to end the process itself.
    exit_status = 1
    try:
        backstop.activate()
        app = create_app(
            data_dir=paths.root,
            workspace_dir=default_runs_dir(),
            solver_warmup=_solver_warmup_enabled(),
            update_request_path=(
                args.status_control.with_name("update.json")
                if args.status_control is not None
                else None
            ),
            # Only with a launcher to hand off to: the staging root it accepts.
            update_staging_root=_update_staging_root(args, os.environ),
        )
        # ``getattr`` twice: an embedder's or a test's stand-in app need not
        # carry Starlette's ``state`` at all.
        jobs_runtime = getattr(getattr(app, "state", None), "jobs_runtime", None)
        if jobs_runtime is not None:
            # The first thing a stop does, on the backstop's thread: whatever
            # ends this process during the budget, the next start reads its
            # running jobs as interrupted by Quit, not as a crash.
            backstop.on_begin(jobs_runtime.mark_running_interrupted_by_quit)
        config = uvicorn.Config(
            app,
            host=HOST,
            port=port,
            log_config=None,
            # Every websocket message is deflated on the event loop unless this
            # is off. The preview socket carries 0.23-0.64 MB coarse geometry
            # frames at up to 30 Hz while a control is being dragged, plus
            # 1.06-3.05 MB fine frames after input goes idle. On an M1 Max,
            # websockets 17 took a 23 ms median to deflate the largest seed
            # coarse frame -- most of the 33 ms drag cadence, on the one thread
            # that also answers every request. Loopback bandwidth was not the
            # constraint.
            ws_per_message_deflate=False,
            # Keep transport framing aligned with the limit advertised and
            # enforced by the application protocol. The protocol receive loop
            # continuously drains into its own one-slot latest-wins queue, so a
            # single transport-level pending message provides backpressure
            # without changing preview coalescing semantics.
            ws_max_size=DEFAULT_MAX_FRAME_BYTES,
            ws_max_queue=1,
            # The application logs every request itself, with timings, in
            # server/app.py. Uvicorn's access log duplicates that line through
            # the same handlers, so leaving both on formatted and wrote every
            # request twice.
            access_log=False,
            # Without a timeout this waits forever for connections to drain,
            # and the SPA holds two long-lived websockets: a browser that has
            # been suspended by the OS never answers the close frame, and quit
            # hangs until the user kills the window.
            timeout_graceful_shutdown=3,
        )
        server = uvicorn.Server(config)
        # Uvicorn enters this context only after its loop (uvloop when
        # available) exists, which is late enough that the loop cannot replace
        # the handlers we need for the outer cleanup path.
        server.capture_signals = lambda: _capture_shutdown_signals(server, backstop)  # type: ignore[method-assign]

        def request_stop(reason: str) -> None:
            server.should_exit = True
            backstop.begin(reason)

        # Windows-only, and a no-op anywhere else or without a console.
        harden_console(
            lambda: request_stop("console window was closed"),
            shutdown_complete,
        )

        if args.status_control is not None:
            threading.Thread(
                target=_watch_statusapp,
                args=(server, args.status_control, args.parent_pid, stop_status_watch),
                kwargs={
                    "on_stop": backstop.begin,
                    "on_update_released": lambda reason: _release_update_restart(app, reason),
                },
                name="wg2-status-control",
                daemon=True,
            ).start()
        elif no_gui_start is not None:
            # No status controller owns this server, so nothing else will settle
            # an update transaction for it (contract §4.5).
            no_gui_start.start(server, port)

        if open_browser:
            threading.Thread(
                target=_open_browser_when_ready,
                args=(port, stop_browser),
                name="wg2-browser",
                daemon=True,
            ).start()

        # The build label, not the bare version: a user quoting this line back
        # to us must be quoting a specific commit (``shared/build_identity.py``).
        logging.getLogger("wg.launch").info(
            "Starting Waveguide Generator %s at http://%s:%d/ (pid %d)",
            BUILD,
            HOST,
            port,
            os.getpid(),
        )
        server.run(sockets=[listener])
        exit_status = 0
        return 0
    except Exception as exc:
        logging.getLogger("wg.launch").exception(
            "The server stopped unexpectedly. Review the traceback and logs/server.log, "
            "then start again."
        )
        stop_reason = (
            "the --no-gui server stopped with an error before it confirmed a healthy "
            f"start: {type(exc).__name__}: {exc}"
        )
        return 1
    finally:
        try:
            stop_browser.set()
            stop_status_watch.set()
            _not_confirmed(no_gui_start, stop_reason)
            if listener is not None:
                listener.close()
            if lock is not None:
                lock.release()
            logging.getLogger("wg.launch").info("Shutdown complete; instance lock released")
            flush_logs()
        finally:
            shutdown_complete.set()
            backstop.deactivate()
            lingering = (
                lingering_threads(LINGERING_JOIN_SECONDS, ignore=preexisting_threads)
                if backstop.begun
                else []
            )
            if lingering:
                # Cleanup is done. What remains is interpreter exit, which would
                # join these threads -- the gmsh worker inside an OCC call, a
                # preview worker inside the mesher -- and so wait out the budget
                # for nothing. Everything it would still run is crash-safe, and
                # the temporary directory they may be writing in is left for the
                # next start's sweep.
                names = ", ".join(sorted(thread.name for thread in lingering))
                backstop.exit_now(
                    f"cleanup finished with {names} still running", code=exit_status
                )
            elif temporary is not None:
                temporary.close(remove=True)


if __name__ == "__main__":
    raise SystemExit(main())
