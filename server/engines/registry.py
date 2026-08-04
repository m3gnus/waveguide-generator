"""Truthful runtime capability detection and adapter construction.

The helper/package probes replace placeholders using the same real layers as
v1 ``server/solver/metal_solver.py:79-179``,
``server/solver/bempp_solver.py:153-188``, and
``server/services/runtime_preflight.py:323-485``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class EngineInfo:
    name: str
    available: bool
    reason: str
    version: str | None


def detect_engines(*, environ: Mapping[str, str] | None = None) -> list[EngineInfo]:
    """Return stable, honest reasons without treating optional absence as an error."""

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

    from server.solver.bempp import bempp_status
    from server.solver.circsym import circsym_status
    from server.solver.metal import metal_status

    for name, probe in (
        ("metal", metal_status),
        ("bempp", bempp_status),
        ("circsym", circsym_status),
    ):
        try:
            status = probe()
        except Exception as exc:  # a broken optional stack is unavailable, not fatal
            status = {
                "available": False,
                "reason": f"{name} detection failed: {exc}",
                "version": None,
            }
        engines.append(
            EngineInfo(
                name=name,
                available=bool(status.get("available")),
                reason=str(status.get("reason") or f"{name} capability probe returned no reason"),
                version=(str(status["version"]) if status.get("version") is not None else None),
            )
        )
    return engines


def create_engine(name: str) -> Any | None:
    """Construct a known adapter without performing a capability probe."""
    normalized = str(name).strip().lower()
    if normalized == "dryrun":
        from .dryrun import DryRunEngine

        return DryRunEngine()
    if normalized == "metal":
        from server.solver.metal import MetalEngine

        return MetalEngine()
    if normalized == "bempp":
        from server.solver.bempp import BemppEngine

        return BemppEngine()
    if normalized == "circsym":
        from server.solver.circsym import CircSymEngine

        return CircSymEngine()
    return None


def get_engine(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    capabilities: Sequence[EngineInfo] | None = None,
) -> Any | None:
    """Return an enabled adapter, optionally reusing an existing probe result."""

    detected = list(capabilities) if capabilities is not None else detect_engines(environ=environ)
    available = {item.name: item.available for item in detected}
    if not available.get(str(name).strip().lower(), False):
        return None
    return create_engine(name)


def resolve_auto_engine(
    *,
    solver_mode: str | None = None,
    environ: Mapping[str, str] | None = None,
    capabilities: Sequence[EngineInfo] | None = None,
) -> str | None:
    """Resolve AUTO to the best engine this host can actually run.

    Explicit CircSym designs require that specialized adapter. Full-3D and
    automatic solver modes prefer Metal, then BEMPP. The gated dry-run engine
    is only a final development fallback when no physical solver is available.
    """

    detected = list(capabilities) if capabilities is not None else detect_engines(environ=environ)
    available = {item.name for item in detected if item.available}
    normalized_mode = str(solver_mode or "auto").strip().lower().replace("-", "_")
    if normalized_mode in {"circsym", "circ_sym", "axisymmetric", "axisym"}:
        return "circsym" if "circsym" in available else None
    for candidate in ("metal", "bempp", "dryrun"):
        if candidate in available:
            return candidate
    return None


class EngineRegistry:
    """One off-thread capability snapshot shared by HTTP and job submission."""

    def __init__(
        self,
        *,
        detector: Callable[[], list[EngineInfo]] = detect_engines,
        factory: Callable[[str], Any | None] = create_engine,
    ) -> None:
        self._detector = detector
        self._factory = factory
        self._cache: tuple[EngineInfo, ...] | None = None
        self._lock = asyncio.Lock()

    async def capabilities(self) -> tuple[EngineInfo, ...]:
        if self._cache is None:
            async with self._lock:
                if self._cache is None:
                    self._cache = tuple(await asyncio.to_thread(self._detector))
        return self._cache

    async def resolve(self, requested: str, *, solver_mode: str | None) -> str | None:
        capabilities = await self.capabilities()
        if requested == "auto":
            return resolve_auto_engine(
                solver_mode=solver_mode, capabilities=capabilities
            )
        return requested if any(
            item.name == requested and item.available for item in capabilities
        ) else None

    async def get_engine(self, name: str) -> Any | None:
        capabilities = await self.capabilities()
        if not any(item.name == name and item.available for item in capabilities):
            return None
        return self._factory(name)

    async def unavailable_reason(self, name: str) -> str | None:
        capabilities = await self.capabilities()
        item = next((item for item in capabilities if item.name == name), None)
        return item.reason if item is not None else None
