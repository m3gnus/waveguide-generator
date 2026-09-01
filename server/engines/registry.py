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

from server.platform.warmup import BackgroundWarmup


@dataclass(frozen=True, slots=True)
class EngineInfo:
    name: str
    available: bool
    reason: str
    version: str | None
    fast_paths: tuple[str, ...] = ()
    formulations: tuple[str, ...] = ()
    mountings: tuple[str, ...] = ()
    geometry_sources: tuple[str, ...] = ("parametric",)
    symmetry_domains: tuple[str, ...] = ()
    field_traces: bool = False
    di_sphere: bool = True
    cancellation_granularity: str = "between-frequencies"


def _symmetry_domains(name: str) -> tuple[str, ...]:
    """Reduced domains an engine can actually solve.

    BEAT mirrors across x, or across x and y, with the mesh in the positive
    fundamental domain, which covers ATH quadrants 1234, 14 and 1. It has no
    y-only mirror, so quadrants 12 -- WG's ``xz`` half -- is refused by
    ``reject_unsupported_native_symmetry``; advertising a bare "half" here
    would promise it.
    """

    if name in {"metal", "bempp"}:
        return ("full", "half", "quarter")
    if name == "beat":
        return ("full", "half-yz", "quarter")
    return ("full",)


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
                formulations=("full-3d",),
                mountings=("free-standing", "infinite-baffle"),
                geometry_sources=("parametric",),
                symmetry_domains=("full",),
            )
        )

    from server.solver.beat import beat_status
    from server.solver.bempp import bempp_status
    from server.solver.circsym import circsym_status
    from server.solver.metal import metal_status

    try:
        meridian_status = circsym_status()
    except Exception as exc:
        meridian_status = {
            "available": False,
            "reason": f"axisymmetric-meridian detection failed: {exc}",
            "version": None,
        }

    engines.append(
        EngineInfo(
            name="axisym",
            available=bool(meridian_status.get("available")),
            reason=str(
                meridian_status.get("reason")
                or "axisymmetric capability probe returned no reason"
            ),
            version=(
                str(meridian_status["version"])
                if meridian_status.get("version") is not None
                else None
            ),
            fast_paths=("axisymmetric-meridian",),
            formulations=("axisymmetric",),
            mountings=("free-standing", "infinite-baffle"),
            geometry_sources=("parametric",),
            symmetry_domains=("continuous-axisymmetric",),
            field_traces=False,
            di_sphere=True,
            cancellation_granularity="intra-frequency",
        )
    )

    # "beat" is the GPU engine (hornlab-beat-bem). Its probe reports available
    # only when a functional CUDA/ROCm/Metal path exists (or the internal
    # force-CPU test switch is set), so on CPU-only hosts it shows up with an
    # honest unavailable reason and BEMPP stays the CPU engine.
    #
    # BEAT's symmetry and DI entries were stale rather than wrong: the package
    # has mapped WG's "yz" half onto its x mirror and "yz+xz" quarter onto its
    # xy mirror, and has emitted the theta-major DI grid, since the pin that
    # added diagonal cuts. Both are now declared, having been run rather than
    # reasoned about -- on an R-OSSE at 1 and 2 kHz the reduced domains mesh to
    # half (810) and a quarter (402) of the full 1,630 triangles, report the
    # expected native planes, return DI, and agree with the full-domain solve
    # to 0.052 dB on axis and 0.005-0.052 dB over |theta| <= 60.
    #
    # "half" is coarser than BEAT actually is: it has no y-only mirror, so a
    # half_xz request is refused at solve time with a capability message rather
    # than silently mis-solved. The vocabulary here cannot say "half, one
    # orientation", and an honest runtime refusal is better than under-declaring
    # the half that does work.
    for name, probe in (
        ("metal", metal_status),
        ("bempp", bempp_status),
        ("beat", beat_status),
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
                formulations=("full-3d",),
                mountings=(
                    ("free-standing", "infinite-baffle")
                    if name == "metal" or bool(status.get("coupled_infinite_baffle"))
                    else ("free-standing",)
                ),
                geometry_sources=(
                    ("parametric", "imported")
                    if name == "metal"
                    else ("parametric",)
                ),
                symmetry_domains=_symmetry_domains(name),
                field_traces=(
                    bool(status.get("surface_traces"))
                    if name == "beat"
                    else name in {"metal", "bempp"}
                ),
                di_sphere=True,
                cancellation_granularity=(
                    "intra-frequency"
                    if name in {"metal", "bempp"}
                    else "between-frequencies"
                ),
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
    if normalized == "beat":
        from server.solver.beat import BeatEngine

        return BeatEngine()
    if normalized == "circsym":
        from server.solver.circsym import AxisymmetricEngine

        return AxisymmetricEngine()
    if normalized == "axisym":
        from server.solver.circsym import AxisymmetricEngine

        return AxisymmetricEngine()
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
    mounting: str | None = None,
    environ: Mapping[str, str] | None = None,
    capabilities: Sequence[EngineInfo] | None = None,
) -> str | None:
    """Resolve AUTO to the best engine this host can actually run.

    Solver mode chooses a path inside a backend, not a backend. AUTO prefers
    Metal, then the GPU BEAT engine, then BEMPP; the gated dry-run engine is
    only a final development fallback when no physical solver is available.

    The order is safe because availability already encodes the platform, but
    not in the way it once did. "beat" advertises available only when a
    functional accelerator was probed and never for its internal CPU path --
    that part is unchanged. What changed is which accelerators count: the
    package gained an Apple Metal backend, so BEAT is now available on Apple
    Silicon too, and AUTO no longer reaches it "exactly on GPU-equipped non-Mac
    hosts". On a Mac both Metal and BEAT are available and Metal is preferred,
    which is a measured preference rather than a platform accident: on the ATH
    reference ladder hornlab-metal-bem wins the whole sweep at every size, by
    1.0x at ~2,000 dofs rising to 6.9x at ~20,000, and all of that margin is
    the solve stage. BEAT stays explicitly selectable there. BEMPP remains the
    universal CPU engine.

    ``mounting`` drops candidates that cannot solve the requested mounting at
    all. BEAT rejects every coupled infinite-baffle request, so without this
    the order above handed such a solve to BEAT ahead of a coupling-capable
    BEMPP on any GPU host -- persisting a job that could only ever fail.
    """

    detected = list(capabilities) if capabilities is not None else detect_engines(environ=environ)
    available = {item.name for item in detected if item.available}
    del solver_mode
    if mounting == "infinite-baffle":
        # Deliberately the only mounting tested. It is also the only value the
        # two vocabularies spell alike: EngineInfo.mountings says
        # "free-standing" while DesignConfig.sim_type says "freestanding", so a
        # general membership test would reject every free-standing solve.
        available &= {
            item.name for item in detected if "infinite-baffle" in item.mountings
        }
    for candidate in ("metal", "beat", "bempp", "dryrun"):
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
        # Probing imports the Metal and BEMPP stacks, which measured 500-950 ms
        # on the first request. Doing it during boot keeps it off the page load,
        # where it used to contend with the first symmetry resolution.
        self.warmup = BackgroundWarmup("engine-probe", self.capabilities)

    async def prewarm(self) -> None:
        """Fill the capability cache in the background.  Never blocks startup."""

        await self.warmup.start()

    async def shutdown_prewarm(self) -> None:
        """Finish a probe still running when the server stops."""

        await self.warmup.stop()

    async def capabilities(self) -> tuple[EngineInfo, ...]:
        if self._cache is None:
            async with self._lock:
                if self._cache is None:
                    self._cache = tuple(await asyncio.to_thread(self._detector))
        return self._cache

    async def resolve(
        self,
        requested: str,
        *,
        solver_mode: str | None,
        mounting: str | None = None,
    ) -> str | None:
        capabilities = await self.capabilities()
        if requested == "auto":
            return resolve_auto_engine(
                solver_mode=solver_mode, mounting=mounting, capabilities=capabilities
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
