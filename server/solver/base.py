"""Shared adapter outcome and callbacks for the retained in-process pipeline.

The artifact/result split follows v1
``server/services/simulation_runner.py:451-489,529-567``.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any


CancelCallback = Callable[[], None]
StageCallback = Callable[[str, float, str], None]
ArtifactCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class EngineRunResult:
    results: dict[str, Any]
    msh_text: str | None = None
    mesh_stats: dict[str, Any] | None = None


__all__ = ["ArtifactCallback", "CancelCallback", "EngineRunResult", "StageCallback"]
