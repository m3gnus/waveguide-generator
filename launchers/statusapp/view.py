"""Compact tkinter view for :mod:`launchers.statusapp.controller`."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
import time
import webbrowser

from .controller import ServiceState, StatusController, StatusSnapshot
from .diagnostics import WindowUnavailable
from .updater import BundleUpdateRequest, UpdateRequest


COLORS = {
    ServiceState.STARTING: "#d99b16",
    ServiceState.OK: "#2eae5e",
    ServiceState.WARNING: "#d99b16",
    ServiceState.ERROR: "#d94b4b",
    ServiceState.STOPPED: "#7b8490",
}

#: How often the Tk loop drains its cross-thread queue. Nothing remote is asked
#: on this tick: the queue carries snapshots produced elsewhere, and the one
#: thing it touches is the in-app update request file the server writes on
#: demand. Ten times a second was inherited from when this same tick also drove
#: HTTP probing, and a window showing two lamps and a URL has never needed it.
TICK_MS = 250
#: Between startup polls, and only until the backend answers once. After that
#: the view stops asking altogether -- see :meth:`StatusView._settle`.
STARTUP_POLL_INTERVAL = 0.55


class StatusView:
    """Render controller snapshots and bind all close paths to controller.close."""

    def __init__(
        self,
        root: tk.Tk,
        controller: StatusController,
        *,
        tick_ms: int = TICK_MS,
        startup_poll_interval: float = STARTUP_POLL_INTERVAL,
    ) -> None:
        self.root = root
        self.controller = controller
        self._tick_ms = tick_ms
        self._startup_poll_interval = startup_poll_interval
        self._closing = False
        self._starting = True
        self._poll_running = False
        self._settled = False
        self._next_poll_at = 0.0
        self._updates: queue.SimpleQueue[tuple[str, StatusSnapshot]] = queue.SimpleQueue()
        self._update_errors: queue.SimpleQueue[str] = queue.SimpleQueue()

        root.title("Waveguide Generator")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(root, padding=18)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Waveguide Generator", font=("TkDefaultFont", 15, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )

        self._backend_lamp, self._backend_reason = self._lamp_row(frame, 1, "Backend")
        self._frontend_lamp, self._frontend_reason = self._lamp_row(frame, 2, "Frontend")

        ttk.Separator(frame).grid(row=3, column=0, columnspan=3, sticky="ew", pady=12)
        self._url_text = tk.StringVar(value="Starting…")
        url = ttk.Label(frame, textvariable=self._url_text, foreground="#2167b1", cursor="hand2")
        url.grid(row=4, column=0, columnspan=3, sticky="w")
        url.bind("<Button-1>", lambda _event: self.open_browser())

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(16, 0))
        self._open_button = ttk.Button(buttons, text="Open in browser", command=self.open_browser)
        self._open_button.grid(row=0, column=0, padx=(0, 8))
        # Never disabled, unlike "Open in browser". This is the button for the
        # case where the backend did not start, so the state that greys the
        # others out is exactly the state that makes this one the only route to
        # a log somebody can attach to a report.
        ttk.Button(buttons, text="Open logs folder", command=self.open_logs).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(buttons, text="Quit", command=self.close).grid(row=0, column=2)

        self._render(self.controller.poll())
        threading.Thread(target=self._start, name="wg2-status-start", daemon=True).start()
        self.root.after(self._tick_ms, self._tick)

    def _lamp_row(
        self, parent: ttk.Frame, row: int, name: str
    ) -> tuple[tk.Canvas, tk.StringVar]:
        lamp = tk.Canvas(parent, width=16, height=16, highlightthickness=0)
        lamp.grid(row=row, column=0, sticky="n", pady=4)
        lamp.create_oval(2, 2, 14, 14, fill=COLORS[ServiceState.STOPPED], outline="")
        ttk.Label(parent, text=name, width=10).grid(row=row, column=1, sticky="nw", padx=(8, 4))
        reason = tk.StringVar(value="Not started")
        ttk.Label(parent, textvariable=reason, width=43, wraplength=310).grid(
            row=row, column=2, sticky="nw", pady=2
        )
        return lamp, reason

    def _start(self) -> None:
        self._updates.put(("started", self.controller.start()))

    def _tick(self) -> None:
        while not self._update_errors.empty():
            self._closing = False
            # A bundle handoff that failed stopped and restarted the server, so
            # the lamps describe a process that no longer exists and the watcher
            # that would have said so was ended by the stop. Go back to startup
            # polling until the replacement answers and settles this again.
            self._settled = False
            self._next_poll_at = 0.0
            error = self._update_errors.get()
            self._backend_reason.set("Update could not start")
            self._frontend_reason.set(error)
            self._open_button.configure(state="normal" if self.controller.url else "disabled")
        while not self._updates.empty():
            kind, snapshot = self._updates.get()
            if kind == "closed":
                self.root.destroy()
                return
            if kind == "started":
                self._starting = False
            elif kind == "snapshot":
                self._poll_running = False
                self._next_poll_at = time.monotonic() + self._startup_poll_interval
            # "lost" arrives from the controller's watcher, not from a poll of
            # ours, so it re-arms nothing -- there is nothing left to poll for.
            if not self._closing:
                self._render(snapshot)
                if not self._settled and snapshot.backend.state is ServiceState.OK:
                    self._settle()
        if not self._closing:
            requested_update = self.controller.take_update_request()
            if requested_update is not None:
                self._start_update(requested_update)
        if (
            not self._closing
            and not self._starting
            and not self._settled
            and not self._poll_running
            and time.monotonic() >= self._next_poll_at
        ):
            self._poll_running = True
            threading.Thread(target=self._poll, name="wg2-status-poll", daemon=True).start()
        self.root.after(self._tick_ms, self._tick)

    def _settle(self) -> None:
        """Stop asking the server questions, and arrange to be told instead.

        The backend has answered, so nothing a further poll could discover is
        still unknown. The SPA route it also fetched cannot stop working while
        the process lives, and the process dying is precisely what the
        controller's watcher reports -- from a blocking wait on the child's
        handle, at no cost, and sooner than any interval could.

        Polling on from here is what an idle installation was measured doing:
        two fresh loopback connections every 0.55 s, for ever, one of them
        re-downloading six kilobytes of index.html to check it still began with
        "<html". Startup is the only part of that which was ever load-bearing.
        """

        self._settled = True
        self.controller.watch_backend(self._on_backend_lost)

    def _on_backend_lost(self, snapshot: StatusSnapshot) -> None:
        # Runs on the controller's watcher thread. Tk belongs to _tick, so the
        # snapshot travels the same queue every other worker here uses.
        self._updates.put(("lost", snapshot))

    def _start_update(self, request: UpdateRequest) -> None:
        label = request.version if isinstance(request, BundleUpdateRequest) else request
        self._closing = True
        self._backend_reason.set(f"Preparing {label}…")
        self._frontend_reason.set("WG will close, install the update, and restart.")
        self._open_button.configure(state="disabled")
        threading.Thread(
            target=self._handoff_update,
            args=(request,),
            name="wg2-status-update",
            daemon=True,
        ).start()

    def _handoff_update(self, request: UpdateRequest) -> None:
        try:
            if isinstance(request, BundleUpdateRequest):
                self.controller.close()
            self.controller.launch_update(request)
        except Exception as exc:  # noqa: BLE001 - keep the current healthy app usable
            if isinstance(request, BundleUpdateRequest):
                self.controller.start()
            self._update_errors.put(str(exc) or type(exc).__name__)
            return
        self._updates.put(("closed", self.controller.close()))

    def _poll(self) -> None:
        self._updates.put(("snapshot", self.controller.poll()))

    @staticmethod
    def _set_lamp(canvas: tk.Canvas, state: ServiceState) -> None:
        canvas.itemconfigure(1, fill=COLORS[state])

    def _render(self, snapshot: StatusSnapshot) -> None:
        self._set_lamp(self._backend_lamp, snapshot.backend.state)
        self._set_lamp(self._frontend_lamp, snapshot.frontend.state)
        self._backend_reason.set(snapshot.backend.reason)
        self._frontend_reason.set(snapshot.frontend.reason)
        self._url_text.set(snapshot.url or "Local URL will appear when startup begins")
        self._open_button.configure(state="normal" if snapshot.url else "disabled")

    def open_browser(self) -> None:
        if self.controller.url:
            webbrowser.open(self.controller.url)

    def open_logs(self) -> None:
        try:
            self.controller.open_logs_folder()
        except Exception as exc:  # noqa: BLE001 - reported in the window, never raised
            # Reported where the user is already looking. A traceback out of a
            # Tk callback would go to a console this application does not have.
            self._frontend_reason.set(f"Could not open the logs folder: {exc}")

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._backend_reason.set("Shutting down cleanly…")
        self._frontend_reason.set("Shutting down cleanly…")
        self._open_button.configure(state="disabled")
        threading.Thread(target=self._stop_and_destroy, name="wg2-status-stop", daemon=True).start()

    def _stop_and_destroy(self) -> None:
        self._updates.put(("closed", self.controller.close()))


def run(controller: StatusController) -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        # Importing tkinter proves the files are present; only Tk() proves Tcl
        # can initialise. Narrowed to this one call so that a TclError from a
        # window that did open is not mistaken for one that never could.
        raise WindowUnavailable(str(exc)) from exc
    StatusView(root, controller)
    try:
        root.mainloop()
    finally:
        controller.close()
    return 0
