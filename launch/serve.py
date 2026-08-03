#!/usr/bin/env python3
"""Start the local Waveguide Generator v2 application server."""

from __future__ import annotations

import argparse
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

import uvicorn

from server.app import create_app
from server.platform.instance import InstanceAlreadyRunning, InstanceLock, acquire_port
from server.platform.logging_setup import flush_logs, setup_logging
from server.platform.paths import ensure_data_layout


HOST = "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="preferred local port (default: 3100)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--data-dir", type=Path, help="override the v2 application data directory")
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


def _install_shutdown_handlers(server: uvicorn.Server) -> dict[int, object]:
    previous: dict[int, object] = {}

    def request_shutdown(signum: int, _frame: object) -> None:
        logging.getLogger("wg2.launch").info(
            "Received %s; finishing active requests and shutting down",
            signal.Signals(signum).name,
        )
        server.should_exit = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_shutdown)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    return previous


def _restore_signal_handlers(previous: dict[int, object]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_dir is not None:
        os.environ["WG2_DATA_DIR"] = str(args.data_dir)

    try:
        paths = ensure_data_layout()
        setup_logging(paths)
        port = acquire_port(args.port, host=HOST)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Waveguide Generator v2 could not start: {exc}", file=sys.stderr)
        return 1

    lock = InstanceLock(paths.locks)
    try:
        lock.acquire(port)
    except InstanceAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        flush_logs()
        return 2
    except RuntimeError as exc:
        print(f"Waveguide Generator v2 could not start: {exc}", file=sys.stderr)
        flush_logs()
        return 1

    stop_browser = threading.Event()
    previous_handlers: dict[int, object] = {}
    try:
        app = create_app(data_dir=paths.root)
        config = uvicorn.Config(app, host=HOST, port=port, log_config=None)
        server = uvicorn.Server(config)
        previous_handlers = _install_shutdown_handlers(server)

        if not args.no_browser and os.environ.get("WG2_NO_BROWSER") != "1":
            threading.Thread(
                target=_open_browser_when_ready,
                args=(port, stop_browser),
                name="wg2-browser",
                daemon=True,
            ).start()

        logging.getLogger("wg2.launch").info(
            "Starting Waveguide Generator v2 at http://%s:%d/ (pid %d)",
            HOST,
            port,
            os.getpid(),
        )
        server.run()
        return 0
    except Exception:
        logging.getLogger("wg2.launch").exception(
            "The server stopped unexpectedly. Review the traceback and logs/server.log, "
            "then start again."
        )
        return 1
    finally:
        stop_browser.set()
        if previous_handlers:
            _restore_signal_handlers(previous_handlers)
        lock.release()
        logging.getLogger("wg2.launch").info("Shutdown complete; instance lock released")
        flush_logs()


if __name__ == "__main__":
    raise SystemExit(main())
