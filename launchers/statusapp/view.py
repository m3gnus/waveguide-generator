"""Compact tkinter view for :mod:`launchers.statusapp.controller`."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
import time
import webbrowser

from .controller import ServiceState, StatusController, StatusSnapshot


COLORS = {
    ServiceState.STARTING: "#d99b16",
    ServiceState.OK: "#2eae5e",
    ServiceState.ERROR: "#d94b4b",
    ServiceState.STOPPED: "#7b8490",
}


class StatusView:
    """Render controller snapshots and bind all close paths to controller.close."""

    def __init__(self, root: tk.Tk, controller: StatusController) -> None:
        self.root = root
        self.controller = controller
        self._closing = False
        self._starting = True
        self._poll_running = False
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
        ttk.Button(buttons, text="Quit", command=self.close).grid(row=0, column=1)

        self._render(self.controller.poll())
        threading.Thread(target=self._start, name="wg2-status-start", daemon=True).start()
        self.root.after(100, self._tick)

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
            else:
                self._poll_running = False
                self._next_poll_at = time.monotonic() + 0.55
            if not self._closing:
                self._render(snapshot)
        if not self._closing:
            requested_update = self.controller.take_update_request()
            if requested_update is not None:
                self._start_update(requested_update)
        if (
            not self._closing
            and not self._starting
            and not self._poll_running
            and time.monotonic() >= self._next_poll_at
        ):
            self._poll_running = True
            threading.Thread(target=self._poll, name="wg2-status-poll", daemon=True).start()
        self.root.after(100, self._tick)

    def _start_update(self, tag: str) -> None:
        self._closing = True
        self._backend_reason.set(f"Preparing {tag}…")
        self._frontend_reason.set("WG will close, install the update, and restart.")
        self._open_button.configure(state="disabled")
        threading.Thread(
            target=self._handoff_update,
            args=(tag,),
            name="wg2-status-update",
            daemon=True,
        ).start()

    def _handoff_update(self, tag: str) -> None:
        try:
            self.controller.launch_update(tag)
        except Exception as exc:  # noqa: BLE001 - keep the current healthy app usable
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
    root = tk.Tk()
    StatusView(root, controller)
    try:
        root.mainloop()
    finally:
        controller.close()
    return 0
