"""Truthful runtime capability detection and adapter construction.

The helper/package probes replace placeholders using the same real layers as
v1 ``server/solver/metal_solver.py:79-179``,
``server/solver/bempp_solver.py:153-188``, and
``server/services/runtime_preflight.py:323-485``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
import logging
import os
import threading
from typing import Any, Callable, Mapping, Sequence

from server.platform.warmup import BackgroundWarmup


log = logging.getLogger("wg.engines.registry")


#: The full-3D backends AUTO walks, best first, on a host that provisions no
#: BEAT CPU runtime of its own. One list, because it was two:
#: ``resolve_auto_engine`` and the ``/api/capabilities`` payload each kept their
#: own copy, and a copy that drifted would have made the interface advertise an
#: order the planner does not follow.
#:
#: Metal leads on the measured ATH ladder (1.0x at ~2,000 dofs to 6.9x at
#: ~20,000, all of it in the solve stage). The BEAT accelerators follow; at most
#: one of them is ever available on a given host, so their relative order only
#: settles a two-GPU-family box. BEMPP is ahead of BEAT's CPU path here because
#: it is the CPU engine this project has measured and shipped -- and BEAT-CPU is
#: ahead of dryrun, because a slow real solve beats a synthetic one.
_BASE_FULL3D_ENGINE_ORDER: tuple[str, ...] = (
    "metal",
    "beat-cuda",
    "beat-rocm",
    "beat-metal",
    "bempp",
    "beat-cpu",
    "dryrun",
)

#: Windows and Linux are the platforms Waveguide Generator provisions a BEAT CPU
#: runtime for (``server/solver/beat_cpu_runtime.py``), and there ``beat-cpu``
#: leads BEMPP. What makes that safe is what "available" now means for that row:
#: since the readiness rewrite it is set only when ``hornlab_beat_bem`` has
#: instantiated the CPU project and solved a 1 kHz probe through the precompiled
#: engine bundle on this machine, so AUTO can only reach it on a host where a
#: CPU solve has demonstrably run. On every host where it has not, this order is
#: the base order.
_CPU_FIRST_FULL3D_ENGINE_ORDER: tuple[str, ...] = (
    "metal",
    "beat-cuda",
    "beat-rocm",
    "beat-metal",
    "beat-cpu",
    "bempp",
    "dryrun",
)


def full3d_engine_order(system: str | None = None) -> tuple[str, ...]:
    """AUTO's full-3D preference order on this platform.

    macOS is deliberately not in the swap. Metal leads there on measured
    evidence and BEAT-CPU is not provisioned there at all, so moving it ahead of
    BEMPP could only change the answer on a Mac whose Metal path is broken --
    and would move it ahead of the CPU engine this project has measured, on the
    one platform where nothing proved the swap.

    Ordering is a *default*, never an override: an explicitly selected engine is
    resolved by name in ``EngineRegistry.resolve`` and never passes through
    here.
    """

    import platform as _platform

    host = _platform.system() if system is None else system
    if host in {"Windows", "Linux"}:
        return _CPU_FIRST_FULL3D_ENGINE_ORDER
    return _BASE_FULL3D_ENGINE_ORDER


#: This host's order. Kept as a module constant because it cannot change while
#: the process runs, and because the capability payload and the planner must
#: publish and follow the same one.
FULL3D_ENGINE_ORDER: tuple[str, ...] = full3d_engine_order()

#: Every engine name a solve request may name. Derived from the order above so
#: adding a backend there is enough to make it requestable, plus the formulation
#: and family names that are not full-3D backends: ``auto``, the axisymmetric
#: meridian runner, and the legacy bare ``beat``.
#:
#: The names are spelled out rather than imported from ``server.solver.beat``
#: on purpose -- this module is imported at boot and that one pulls the optional
#: Julia package with it. ``test_engines_registry`` pins the two together.
SELECTABLE_ENGINE_NAMES: frozenset[str] = frozenset(
    {"auto", "axisym", "beat"} | set(FULL3D_ENGINE_ORDER)
)


@dataclass(frozen=True, slots=True)
class EngineInfo:
    name: str
    available: bool
    reason: str
    version: str | None
    fast_paths: tuple[str, ...] = ()
    formulations: tuple[str, ...] = ()
    mountings: tuple[str, ...] = ()
    # Which single axes an engine can bound with a rigid half space, e.g.
    # ("y",) for a floor only. Empty means no ground plane at all, and the
    # "ground-plane" mounting is advertised only when this is non-empty.
    #
    # A list rather than a boolean because the engines genuinely differ:
    # hornlab-bempp-bem mirrors across any one of the three coordinate planes,
    # while hornlab-beat-bem's :ground transform is y = 0 only. A flat boolean
    # would make BEAT look capable of a side wall it cannot solve.
    ground_plane_axes: tuple[str, ...] = ()
    # Whether a ground plane may be combined with a reduced-domain mesh.
    #
    # bempp joins the symmetry spec and the ground plane into one reflection
    # group, so a left-right-symmetric horn on a floor keeps its half mesh.
    # metal and BEAT each carry a single image-transform set and cannot, so the
    # same model solves the full domain there -- about four times the work. The
    # mounting is therefore not performance-neutral across engines, and the UI
    # must not present them as interchangeable once a ground plane is on.
    ground_plane_composes_with_symmetry: bool = False
    geometry_sources: tuple[str, ...] = ("parametric",)
    symmetry_domains: tuple[str, ...] = ()
    field_traces: bool = False
    di_sphere: bool = True
    cancellation_granularity: str = "between-frequencies"
    #: What the selector calls this engine. ``name`` is a wire identifier and
    #: reads like one -- "beat-rocm" tells a user nothing about what hardware it
    #: wants. Defaults to the name so an engine that has nothing better to say
    #: displays exactly what it did before labels existed.
    label: str = ""

    def display_label(self) -> str:
        return self.label or self.name


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
    if name == "beat" or name.startswith("beat-"):
        return ("full", "half-yz", "quarter")
    return ("full",)


def _ground_plane_axes(name: str, status: Mapping[str, Any]) -> tuple[str, ...]:
    """Which axes this engine can bound with a rigid half space, per its probe.

    Probed, never assumed. ``server/solver/bempp.py`` reports the axes only
    after constructing a ``SolveConfig`` that actually accepts ``ground_plane``,
    so a wg2 running against a pre-merge pin of hornlab-bempp-bem advertises
    nothing here and the mounting simply does not appear -- rather than being
    offered and then failing with a TypeError deep in the adapter.

    BEAT is deliberately absent even though the package has a ``:ground``
    symmetry mode. It is not reachable through the package's own config today,
    so there is nothing for wg2 to call; when it is, BEAT reports ("y",) alone,
    because its transform mirrors across y = 0 only and advertising a side wall
    it cannot solve is the failure this per-axis list exists to prevent.
    """

    if name != "bempp":
        return ()
    axes = status.get("ground_plane_axes") or ()
    return tuple(str(axis) for axis in axes)


def _mountings(
    *, infinite_baffle: bool, ground_plane_axes: Sequence[str] = ()
) -> tuple[str, ...]:
    """Assemble the advertised mounting vocabulary from probed capabilities.

    "free-standing" is unconditional: every engine here can solve a body in
    free air. The other two are separate boundary conditions rather than two
    spellings of one, and are advertised only when the installed package can
    actually run them:

    * "infinite-baffle" -- the mouth is let into an unbounded rigid wall,
      coplanar with the mouth, removing every cabinet edge.
    * "ground-plane" -- the whole body, edges and all, stands above an infinite
      rigid half space and radiates into 2*pi.

    Choosing the wrong one of those returns a plausible wrong answer rather
    than an error, which is exactly why the vocabulary names them apart.
    """

    names = ["free-standing"]
    if infinite_baffle:
        names.append("infinite-baffle")
    if ground_plane_axes:
        names.append("ground-plane")
    return tuple(names)


def detect_engines(*, environ: Mapping[str, str] | None = None) -> list[EngineInfo]:
    """Return stable, honest reasons without treating optional absence as an error."""

    env = os.environ if environ is None else environ
    engines: list[EngineInfo] = []
    if env.get("WG2_ENABLE_DRYRUN") == "1":
        engines.append(
            EngineInfo(
                name="dryrun",
                label="Dry run \u2014 synthetic",
                available=True,
                reason="Enabled explicitly by WG2_ENABLE_DRYRUN=1",
                version="builtin",
                formulations=("full-3d",),
                mountings=("free-standing", "infinite-baffle"),
                geometry_sources=("parametric",),
                symmetry_domains=("full",),
            )
        )

    from server.solver.beat import (
        BEAT_BACKENDS,
        BEAT_BACKEND_LABELS,
        beat_backend_statuses,
        beat_engine_name,
    )
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
            label="Axisymmetric meridian",
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
    for name, label, probe in (
        ("metal", "Metal \u2014 Apple GPU", metal_status),
        ("bempp", "BEMPP \u2014 CPU", bempp_status),
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
                label=label,
                available=bool(status.get("available")),
                reason=str(status.get("reason") or f"{name} capability probe returned no reason"),
                version=(str(status["version"]) if status.get("version") is not None else None),
                formulations=("full-3d",),
                mountings=_mountings(
                    infinite_baffle=(
                        name == "metal" or bool(status.get("coupled_infinite_baffle"))
                    ),
                    ground_plane_axes=_ground_plane_axes(name, status),
                ),
                ground_plane_axes=_ground_plane_axes(name, status),
                ground_plane_composes_with_symmetry=(
                    name == "bempp"
                    and bool(status.get("ground_plane_composes_with_symmetry"))
                ),
                geometry_sources=(
                    ("parametric", "imported")
                    if name == "metal"
                    else ("parametric",)
                ),
                symmetry_domains=_symmetry_domains(name),
                field_traces=True,
                di_sphere=True,
                cancellation_granularity="intra-frequency",
            )
        )

    # BEAT is one solver with four interchangeable execution backends, and it
    # is advertised as four engines rather than one so a host that has both a
    # GPU and the portable CPU path can choose between them. A single "beat"
    # entry could only ever offer whichever backend the package's probe named
    # first, which on this project's own Macs means a user who wants to compare
    # BEAT-CPU against BEAT-Metal has no way to ask for it.
    #
    # The four share every capability below: the backend is an execution
    # choice, not a formulation, and the same Julia solver runs on each.
    try:
        backend_statuses = beat_backend_statuses()
    except Exception as exc:  # a broken optional stack is unavailable, not fatal
        backend_statuses = {
            backend: {
                "available": False,
                "reason": f"beat detection failed: {exc}",
                "version": None,
            }
            for backend in BEAT_BACKENDS
        }
    for backend in BEAT_BACKENDS:
        status = backend_statuses.get(backend, {})
        name = beat_engine_name(backend)
        engines.append(
            EngineInfo(
                name=name,
                label=BEAT_BACKEND_LABELS.get(backend, name),
                available=bool(status.get("available")),
                reason=str(
                    status.get("reason") or f"{name} capability probe returned no reason"
                ),
                version=(str(status["version"]) if status.get("version") is not None else None),
                formulations=("full-3d",),
                # No "ground-plane": see _ground_plane_axes. The gap is in this
                # application, not in hornlab-beat-bem.
                mountings=("free-standing",),
                geometry_sources=("parametric",),
                symmetry_domains=_symmetry_domains(name),
                field_traces=bool(status.get("surface_traces")),
                di_sphere=True,
                cancellation_granularity="between-frequencies",
            )
        )
    return engines


def _beat_engine_backend(name: str) -> str | None:
    """``beat_engine_backend`` without importing the optional stack eagerly."""

    from server.solver.beat import beat_engine_backend

    return beat_engine_backend(name)


def resolve_legacy_beat_engine(
    capabilities: Sequence[EngineInfo],
) -> str | None:
    """The BEAT variant the bare legacy ``beat`` name means on this host.

    Design files and stored solve options written before the backends became
    separately selectable still say ``beat``, and they must keep working. The
    answer is AUTO's own preference restricted to BEAT, so a saved "run this on
    BEAT" keeps meaning "run this on the best BEAT this machine has" -- which is
    what it meant when the probe was doing the choosing.

    ``None`` when no BEAT variant is available, which the caller reports as an
    unavailable engine rather than silently substituting another solver.
    """

    available = {item.name for item in capabilities if item.available}
    for candidate in full3d_engine_order():
        if candidate.startswith("beat-") and candidate in available:
            return candidate
    return None


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
    beat_backend = _beat_engine_backend(normalized)
    if beat_backend is not None:
        from server.solver.beat import BeatEngine

        return BeatEngine(beat_backend)
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


#: Mountings AUTO must filter candidates by, because an engine can genuinely
#: refuse them. Both are spelled the same in EngineInfo.mountings and in the
#: caller's resolved mounting; "free-standing" is deliberately absent, since
#: every engine can solve it and its two spellings differ.
_GATED_MOUNTINGS = frozenset({"infinite-baffle", "ground-plane"})


def resolve_auto_engine(
    *,
    solver_mode: str | None = None,
    mounting: str | None = None,
    resolved_quadrants: int | None = None,
    environ: Mapping[str, str] | None = None,
    capabilities: Sequence[EngineInfo] | None = None,
) -> str | None:
    """Resolve AUTO to the best engine this host can actually run.

    Solver mode chooses a path inside a backend, not a backend. The order is
    ``full3d_engine_order()``: Metal, then BEAT's accelerators, then -- on the
    platforms this application provisions a BEAT CPU runtime for -- BEAT-CPU
    ahead of BEMPP, and only then the gated dry-run engine.

    The order is safe because availability already encodes the platform. On a
    Mac, Metal, BEAT-Metal, BEAT-CPU and BEMPP are all available and Metal is
    preferred, which is a measured preference rather than a platform accident:
    on the ATH reference ladder hornlab-metal-bem wins the whole sweep at every
    size, by 1.0x at ~2,000 dofs rising to 6.9x at ~20,000, and all of that
    margin is the solve stage. Every BEAT variant stays explicitly selectable
    there.

    Where BEAT-CPU sits relative to BEMPP is the one platform-dependent part,
    and ``full3d_engine_order`` documents why. Either way it stays ahead of
    dryrun, because a slow real solve beats a synthetic one.

    ``mounting`` drops candidates that cannot solve the requested mounting at
    all. BEAT rejects every coupled infinite-baffle request, so without this
    the order above handed such a solve to BEAT ahead of a coupling-capable
    BEMPP on any GPU host -- persisting a job that could only ever fail. The
    rigid ground plane is filtered the same way and is currently BEMPP-only.
    """

    detected = list(capabilities) if capabilities is not None else detect_engines(environ=environ)
    available = {item.name for item in detected if item.available}
    del solver_mode
    if mounting in _GATED_MOUNTINGS:
        # Only the mountings an engine can refuse are tested, and only these
        # two are spelled identically in both vocabularies: EngineInfo.mountings
        # says "free-standing" while DesignConfig.sim_type says "freestanding",
        # so a general membership test would reject every free-standing solve.
        #
        # "ground-plane" is not a sim_type at all -- it comes from
        # SolveOptions.ground_plane, because it describes the room rather than
        # the horn -- so the caller resolves the effective mounting and passes
        # it here. Without this, AUTO would hand a ground-plane solve to an
        # engine that cannot express it and persist a job that could only ever
        # fail, which is the same trap coupled infinite baffle fell into.
        available &= {item.name for item in detected if mounting in item.mountings}
    if resolved_quadrants is not None:
        available &= {
            item.name
            for item in detected
            if engine_supports_symmetry(item, resolved_quadrants)
        }
    for candidate in full3d_engine_order():
        if candidate in available:
            return candidate
    return None


def engine_supports_symmetry(info: EngineInfo, resolved_quadrants: int) -> bool:
    """Whether an engine can solve the already validated fundamental domain."""

    # Dry-run is synthetic: its capability says "full" because it has no native
    # mirror implementation, but it accepts every validated mesh domain so the
    # gated development fallback remains useful on reduced designs.
    if info.name == "dryrun":
        return True
    # Every built-in detector publishes this field. Keep hand-built/legacy
    # EngineInfo objects compatible; they predate capability-level symmetry
    # filtering and are used by embedders and focused tests.
    if not info.symmetry_domains:
        return True
    required = {
        1234: ("full",),
        12: ("half", "half-xz"),
        14: ("half", "half-yz"),
        1: ("quarter",),
    }.get(resolved_quadrants, ())
    return bool(set(required) & set(info.symmetry_domains))


class EngineRegistry:
    """One off-thread capability snapshot shared by HTTP and job submission."""

    def __init__(
        self,
        *,
        detector: Callable[[], list[EngineInfo]] = detect_engines,
        factory: Callable[[str], Any | None] = create_engine,
        cpu_refresh: bool | None = None,
    ) -> None:
        self._detector = detector
        self._factory = factory
        self._cache: tuple[EngineInfo, ...] | None = None
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_state_lock = threading.Lock()
        self._refresh_revision = 0
        self._refresh_applied_revision = 0
        self._listener_removed = False
        self._cpu_listener: Callable[[], None] | None = None
        self._cpu_refresh_enabled = detector is detect_engines if cpu_refresh is None else cpu_refresh
        if self._cpu_refresh_enabled:
            try:
                from server.solver.beat_cpu_runtime import add_readiness_listener

                self._cpu_listener = self._cpu_readiness_changed
                add_readiness_listener(self._cpu_listener)
            except ImportError:
                self._cpu_refresh_enabled = False
        # Probing imports the Metal and BEMPP stacks, which measured 500-950 ms
        # on the first request. Doing it during boot keeps it off the page load,
        # where it used to contend with the first symmetry resolution.
        self.warmup = BackgroundWarmup("engine-probe", self.capabilities)

    async def prewarm(self) -> None:
        """Fill the capability cache in the background.  Never blocks startup."""

        await self.warmup.start()

    async def shutdown_prewarm(self) -> None:
        """Finish a probe still running when the server stops."""

        if not self._listener_removed:
            self._listener_removed = True
            if self._cpu_listener is not None:
                from server.solver.beat_cpu_runtime import remove_readiness_listener

                remove_readiness_listener(self._cpu_listener)
                self._cpu_listener = None
        await self.warmup.stop()
        refresh_task = self._refresh_task
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task

    def cpu_preparation_in_flight(self) -> bool:
        """Whether the owned runtime preparation lifecycle is active."""

        if not self._cpu_refresh_enabled or self._listener_removed:
            return False
        from server.solver.beat_cpu_runtime import cpu_preparation_in_flight

        with self._refresh_state_lock:
            refresh_pending = self._refresh_revision > self._refresh_applied_revision
        return cpu_preparation_in_flight() or refresh_pending

    async def capabilities(self) -> tuple[EngineInfo, ...]:
        self._loop = asyncio.get_running_loop()
        if self._cache is None:
            async with self._lock:
                if self._cache is None:
                    self._cache = tuple(await asyncio.to_thread(self._detector))
                    self._schedule_cpu_refresh()
        return self._cache

    def _cpu_readiness_changed(self) -> None:
        if self._listener_removed:
            return
        with self._refresh_state_lock:
            self._refresh_revision += 1
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._schedule_cpu_refresh)

    def _schedule_cpu_refresh(self) -> None:
        if self._listener_removed or self._cache is None:
            return
        with self._refresh_state_lock:
            dirty = self._refresh_revision > self._refresh_applied_revision
        if dirty and (self._refresh_task is None or self._refresh_task.done()):
            self._refresh_task = asyncio.create_task(self._refresh_cpu_backend())
            self._refresh_task.add_done_callback(self._cpu_refresh_finished)

    def _cpu_refresh_finished(self, task: asyncio.Task[None]) -> None:
        """Close the completion window in which a new event saw a live task."""

        if self._refresh_task is task:
            self._refresh_task = None
        self._schedule_cpu_refresh()

    async def _refresh_cpu_backend(self) -> None:
        from server.solver.beat import _cpu_backend_status, _load_api

        while not self._listener_removed:
            with self._refresh_state_lock:
                target_revision = self._refresh_revision
                applied_revision = self._refresh_applied_revision
            if target_revision <= applied_revision:
                return
            try:
                package = _load_api()
                if package is not None:
                    available, reason = await asyncio.to_thread(
                        _cpu_backend_status, package
                    )
                    async with self._lock:
                        if self._cache is not None and not self._listener_removed:
                            self._cache = tuple(
                                replace(item, available=available, reason=reason)
                                if item.name == "beat-cpu"
                                else item
                                for item in self._cache
                            )
            except Exception:  # noqa: BLE001 - refresh cannot break capabilities
                log.warning("BEAT CPU capability refresh failed", exc_info=True)
            with self._refresh_state_lock:
                self._refresh_applied_revision = max(
                    self._refresh_applied_revision, target_revision
                )
                if self._refresh_revision == self._refresh_applied_revision:
                    return

    async def resolve(
        self,
        requested: str,
        *,
        solver_mode: str | None,
        mounting: str | None = None,
        resolved_quadrants: int | None = None,
    ) -> str | None:
        capabilities = await self.capabilities()
        if requested == "auto":
            return resolve_auto_engine(
                solver_mode=solver_mode,
                mounting=mounting,
                resolved_quadrants=resolved_quadrants,
                capabilities=capabilities,
            )
        if requested == "beat":
            return resolve_legacy_beat_engine(capabilities)
        return requested if any(
            item.name == requested and item.available for item in capabilities
        ) else None

    async def supports_symmetry(self, name: str, resolved_quadrants: int) -> bool:
        capabilities = await self.capabilities()
        item = next((item for item in capabilities if item.name == name), None)
        return item is not None and engine_supports_symmetry(item, resolved_quadrants)

    async def get_engine(self, name: str) -> Any | None:
        capabilities = await self.capabilities()
        if not any(item.name == name and item.available for item in capabilities):
            return None
        return self._factory(name)

    async def unavailable_reason(self, name: str) -> str | None:
        capabilities = await self.capabilities()
        item = next((item for item in capabilities if item.name == name), None)
        if item is not None:
            return item.reason
        if name == "beat":
            # The legacy family name has no entry of its own. The CPU variant
            # carries the reason worth reporting: it is the one backend every
            # host could run, so whatever stops it -- no Julia, no package --
            # is why none of the four is available.
            fallback = next(
                (item for item in capabilities if item.name == "beat-cpu"), None
            )
            if fallback is not None:
                return f"No BEAT backend is available here. {fallback.reason}"
        return None
