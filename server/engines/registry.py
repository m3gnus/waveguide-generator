"""Report solver engines without importing optional solver stacks."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


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
