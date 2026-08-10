#!/usr/bin/env python3
"""Start the local Waveguide Generator v2 application server."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
import webbrowser


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import uvicorn  # noqa: E402 - the checkout root must be importable first

from server.app import VERSION, create_app  # noqa: E402
from server.platform.console import harden_console  # noqa: E402
from server.platform.instance import (  # noqa: E402
    InstanceAlreadyRunning,
    InstanceLock,
    InstanceLockError,
    pid_is_running,
    requested_port,
    reserve_port,
)
from server.platform.logging_setup import flush_logs, setup_logging  # noqa: E402
from server.platform.paths import ensure_data_layout  # noqa: E402
from server.platform.signal_rearm import (  # noqa: E402
    register_signal_rearm,
    unregister_signal_rearm,
)
from server.protocol.frame import DEFAULT_MAX_FRAME_BYTES  # noqa: E402


HOST = "127.0.0.1"


def _solver_warmup_enabled() -> bool:
    """Enable the native solver warmup only by explicit operator request.

    The warmup is a real, non-cancellable solve in a daemon thread. It is not
    serialized with user jobs and cannot be joined safely during a fast
    shutdown, so release launches must not start it implicitly.
    """

    return os.environ.get("WG2_SOLVER_WARMUP") == "1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="preferred local port (default: 3100)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--data-dir", type=Path, help="override the v2 application data directory")
    parser.add_argument("--status-control", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    return parser


def _open_browser_when_ready(port: int, stop: threading.Event) -> None:
    url = f"http://{HOST}:{port}/"
    deadline = time.monotonic() + 30.0
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.25):
                webbrowser.open(url)
                logging.getLogger("wg2.launch").info("Opened browser at %s", url)
                return
        except OSError:
            stop.wait(0.1)
    if not stop.is_set():
        logging.getLogger("wg2.launch").warning(
            "The server did not become ready within 30 seconds, so the browser was not "
            "opened. Check the errors above and %s.",
            url,
        )


def _watch_statusapp(
    server: uvicorn.Server,
    control_path: Path,
    parent_pid: int | None,
    stop: threading.Event,
) -> None:
    """Gracefully stop when the owning status window closes or disappears."""

    while not stop.wait(0.15):
        requested = control_path.is_file()
        parent_gone = parent_pid is not None and not pid_is_running(parent_pid)
        if requested or parent_gone:
            reason = "status window requested quit" if requested else "status window exited"
            logging.getLogger("wg2.launch").info("Stopping because the %s", reason)
            server.should_exit = True
            return


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
def _capture_shutdown_signals(server: uvicorn.Server):
    """Install WG2's handlers after Uvicorn has created its event loop.

    Uvicorn 0.49 enters ``Server.capture_signals`` inside the loop runner and
    re-raises every captured signal after restoring the prior handler. On
    uvloop, SIGTERM's prior handler is the platform default by then, so the
    re-raise terminates the process before WG2's outer ``finally`` can release
    its lock and stop logging. Owning the capture context keeps graceful exit
    semantics and lets ``main`` finish all application cleanup.
    """

    previous: dict[int, object] = {}

    def request_shutdown(signum: int, _frame: object) -> None:
        logging.getLogger("wg2.launch").info(
            "Received %s; finishing active requests and shutting down",
            signal.Signals(signum).name,
        )
        if server.should_exit and signum == signal.SIGINT:
            server.force_exit = True
        else:
            server.should_exit = True

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_dir is not None:
        os.environ["WG2_DATA_DIR"] = str(args.data_dir)

    lock: InstanceLock | None = None
    listener: socket.socket | None = None
    try:
        paths = ensure_data_layout()
        setup_logging(paths)
        preferred_port = requested_port(args.port)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Waveguide Generator v2 could not start: {exc}", file=sys.stderr)
        # setup_logging runs before requested_port so this path can already own
        # a QueueListener (for example, when WG2_PORT is invalid). Treat early
        # configuration failures like every later exit and drain it explicitly.
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
        flush_logs()
        return 2
    except InstanceLockError as exc:
        print(f"Waveguide Generator v2 could not start: {exc}", file=sys.stderr)
        flush_logs()
        return 1

    try:
        # Keep the selected socket bound through Uvicorn startup. Binding itself
        # is the retry loop, eliminating the probe-then-bind TOCTOU window.
        listener, port = reserve_port(preferred_port, host=HOST)
        lock.update_port(port)
    except (OSError, InstanceLockError) as exc:
        print(f"Waveguide Generator v2 could not start: {exc}", file=sys.stderr)
        lock.release()
        flush_logs()
        return 1

    stop_browser = threading.Event()
    stop_status_watch = threading.Event()
    shutdown_complete = threading.Event()
    try:
        app = create_app(
            data_dir=paths.root,
            solver_warmup=_solver_warmup_enabled(),
        )
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
        server.capture_signals = lambda: _capture_shutdown_signals(server)  # type: ignore[method-assign]
        # Windows-only, and a no-op anywhere else or without a console.
        harden_console(
            lambda: setattr(server, "should_exit", True),
            shutdown_complete,
        )

        if args.status_control is not None:
            threading.Thread(
                target=_watch_statusapp,
                args=(server, args.status_control, args.parent_pid, stop_status_watch),
                name="wg2-status-control",
                daemon=True,
            ).start()

        if open_browser:
            threading.Thread(
                target=_open_browser_when_ready,
                args=(port, stop_browser),
                name="wg2-browser",
                daemon=True,
            ).start()

        logging.getLogger("wg2.launch").info(
            "Starting Waveguide Generator v%s at http://%s:%d/ (pid %d)",
            VERSION,
            HOST,
            port,
            os.getpid(),
        )
        server.run(sockets=[listener])
        return 0
    except Exception:
        logging.getLogger("wg2.launch").exception(
            "The server stopped unexpectedly. Review the traceback and logs/server.log, "
            "then start again."
        )
        return 1
    finally:
        try:
            stop_browser.set()
            stop_status_watch.set()
            if listener is not None:
                listener.close()
            if lock is not None:
                lock.release()
            logging.getLogger("wg2.launch").info("Shutdown complete; instance lock released")
            flush_logs()
        finally:
            shutdown_complete.set()


if __name__ == "__main__":
    raise SystemExit(main())
