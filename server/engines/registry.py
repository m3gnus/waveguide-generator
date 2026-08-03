"""Report solver engines without importing optional solver stacks."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class EngineInfo:
    name: str
    available: bool
    reason: str
    version: str | None


def detect_engines(*, environ: Mapping[str, str] | None = None) -> list[EngineInfo]:
    """Return a stable capability report for the current process."""

    env = os.environ if environ is None else environ
    engines: list[EngineInfo] = []
    if env.get("WG2_ENABLE_DRYRUN") == "1":
        engines.append(
            EngineInfo(
                name="dryrun",
                available=True,
                reason="Enabled explicitly by WG2_ENABLE_DRYRUN=1",
                version="builtin",
            )
        )

    for name in ("metal", "bempp", "circsym"):
        engines.append(
            EngineInfo(
                name=name,
                available=False,
                reason="not detected: real engine detection is deferred to a later batch",
                version=None,
            )
        )
    return engines


def get_engine(name: str, *, environ: Mapping[str, str] | None = None) -> Any | None:
    """Return an enabled engine implementation without probing optional stacks."""

    normalized = str(name).strip().lower()
    available = {item.name: item.available for item in detect_engines(environ=environ)}
    if not available.get(normalized, False):
        return None
    if normalized == "dryrun":
        from .dryrun import DryRunEngine

        return DryRunEngine()
    return None
