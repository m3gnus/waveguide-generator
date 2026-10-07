"""Opt-in sweep timings; disabled sweeps never read the performance clock."""

from collections.abc import Callable
import os
from statistics import median
from time import monotonic, perf_counter


class SweepProfile:
    def __init__(self, emit: Callable[[str], None], side: str) -> None:
        self.emit, self.side = emit, side
        self.start = perf_counter()
        self.seen_event = False
        self.seen_result = False
        self.last_result: float | None = None
        self.mark("solve start", self.start)

    def mark(self, name: str, at: float | None = None) -> None:
        stamp = perf_counter() if at is None else at
        self.emit(f"BEAT profile {self.side}: {name} elapsed_s={stamp - self.start:.6f}")

    def relay_frame(self, frame: dict) -> None:
        frame["host_relay_monotonic"] = monotonic()

    def event(self, event: dict) -> None:
        stamp = perf_counter()
        if not self.seen_event:
            self.seen_event = True
            self.mark("first event", stamp)
        kind = event.get("type")
        if kind == "result":
            if not self.seen_result:
                self.seen_result = True
                self.mark("first result", stamp)
            self.last_result = stamp
        elif kind in {"completed", "cancelled", "failed"}:
            if self.last_result is not None:
                self.mark("last result", self.last_result)
            self.mark(kind, stamp)


def start_profile(emit: Callable[[str], None], side: str) -> SweepProfile | None:
    return SweepProfile(emit, side) if os.environ.get("WG2_BEAT_PROFILE") == "1" else None


class RelayLatency:
    """Measure framed events in the shared host/WG monotonic clock domain."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.latencies: list[float] = []

    def observe(self, frame: dict) -> None:
        sent = frame.get("host_relay_monotonic")
        if type(sent) not in {float, int}:
            return
        latency = monotonic() - sent
        self.latencies.append(latency)
        event = frame.get("event", frame)
        kind = event.get("type") if isinstance(event, dict) else None
        self.emit(f"BEAT relay wg: event={kind} count={len(self.latencies)} latency_s={latency:.6f}")

    def finish(self) -> None:
        if not self.latencies:
            return
        ordered = sorted(self.latencies)
        position = (len(ordered) - 1) * .9
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        p90 = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
        self.emit(f"BEAT relay wg: summary count={len(ordered)} median_s={median(ordered):.6f} "
                  f"p90_s={p90:.6f} max_s={ordered[-1]:.6f}")
