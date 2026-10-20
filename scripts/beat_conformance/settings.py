"""Validate native backend evidence for the conformance recorder."""

from __future__ import annotations

from typing import Any


def validate_native_backend(native: Any, backend: str, *, official: bool,
                            device_name: str | None = None) -> None:
    """Refuse fallback or absent native evidence, including conflicting aliases."""
    rows = getattr(native, "solver_log", [])
    if len(rows) != len(native.frequencies_hz) or not rows:
        raise ValueError("Agreement requires per-frequency native backend diagnostics")
    for row in rows:
        diagnostic = row.get("native_diagnostics") or {}
        labels = [diagnostic[key] for key in ("bem_backend", "backend") if key in diagnostic]
        if not labels or any(label != backend for label in labels):
            raise ValueError(f"Observed native backend differs from requested {backend} or is missing")
        execution = diagnostic.get("engine_provenance", {}).get("execution", {})
        if execution and execution.get("backend") != backend:
            raise ValueError("Native device backend differs from requested backend")
        if backend == "metal":
            device = execution.get("device", diagnostic.get("device"))
            if (not isinstance(device, str) or not device.strip()
                    or device.lower() in {"cpu", "host"} or execution.get("device_query_error")
                    or (device_name is not None and device != device_name)):
                raise ValueError("Native Metal device is missing or differs from independently probed device")
