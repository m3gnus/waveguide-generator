"""Host protocol v2: send unchanged per-sweep provenance only once.

EngineWorker yields parsed dictionaries, so raw stdout forwarding is not part
of its public API. Shallow copies here preserve engine ownership; the client
reattaches one shared provenance object without per-frequency deep copies.
Changed provenance is transmitted again, preserving device/error diagnostics.
"""

from typing import Any


_MISSING = object()


class EventRelay:
    def __init__(self) -> None:
        self.provenance: Any = _MISSING

    def envelope(self, event: dict) -> dict:
        result = event.get("result")
        diagnostics = result.get("diagnostics") if isinstance(result, dict) else None
        if not isinstance(diagnostics, dict) or "engine_provenance" not in diagnostics:
            return {"type": "event", "event": event}
        provenance = diagnostics["engine_provenance"]
        frame = {"type": "event", "event": dict(event, result=dict(result, diagnostics={
            key: value for key, value in diagnostics.items() if key != "engine_provenance"}))}
        if provenance != self.provenance or self.provenance is _MISSING:
            self.provenance = provenance
            frame["engine_provenance"] = provenance
        frame["provenance_ref"] = True
        return frame

    def accept(self, frame: dict) -> dict:
        event = frame.get("event")
        if not isinstance(event, dict):
            raise ValueError("Invalid BEAT event envelope")
        if "engine_provenance" in frame:
            self.provenance = frame["engine_provenance"]
        if frame.get("provenance_ref"):
            result = event.get("result")
            diagnostics = result.get("diagnostics") if isinstance(result, dict) else None
            if self.provenance is _MISSING or not isinstance(diagnostics, dict):
                raise ValueError("Invalid BEAT provenance reference")
            diagnostics["engine_provenance"] = self.provenance
        return event
