"""Shared official BEAT production bridge through WG-managed solve sessions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import copy
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
import importlib
import logging
from types import SimpleNamespace
from typing import Any

import numpy as np

from .beat_adapter.request import CompiledRequest
from .beat_adapter.preflight import validate_exterior_physics
from .beat_adapter.results import ResultContractError, SweepResult, map_sweep
from .beat_runtime import assets, discovery, paths, readiness, registry, warm_cache
from .beat_runtime.client import HostError
from .beat_runtime.manager import UnsupportedBackend, WorkerManager, get_manager
from .beat_runtime.session import SolveSession
from .beat_runtime.ownership import OwnershipClosed
from .beat_runtime.negotiation import validated_negotiator
from .beat_runtime.profile import start_profile, SweepProfile
from .context import SolverContext
from .frequency_sweep import sort_native_result_frequencies
from .beat import BeatUnavailable
from .result_mapping import native_symmetry_plane


class OfficialBeatUnavailable(BeatUnavailable):
    """The selected official runtime cannot execute the request."""


OfficialBeatProtocolError = ResultContractError


def engine_version() -> str | None:
    """Read installed distribution metadata without importing either engine."""
    try:
        return version("beat-engine")
    except PackageNotFoundError:
        return None


def batch_request(request: CompiledRequest, frequencies: Sequence[float]) -> CompiledRequest:
    """Reuse compiled topology and its frame for each acquired batch."""
    return replace(request, wire=dict(request.wire, frequencies_hz=list(frequencies)))


def production_statuses(*, force_refresh: bool = False, backend: str | None = None) -> dict[str, dict[str, Any]]:
    """Read matching compiled proof; static engine capabilities are insufficient."""
    refresh = {"force_refresh": True} if force_refresh else {}
    statuses = ({backend: readiness.backend_status(backend, **refresh)} if backend in readiness.BACKENDS
                else readiness.beat_backend_statuses(**refresh))
    installed_version = engine_version()
    for name in readiness.BACKENDS:
        if name in statuses:
            statuses[name] = dict(statuses[name], surface_traces=True, version=installed_version)
    return statuses


def response_config(request: CompiledRequest, context: SolverContext) -> SimpleNamespace:
    """Supply the frame and observation metadata WG's result mapper consumes."""
    return SimpleNamespace(
        observation=SimpleNamespace(
            distance_m=float(context.polar_config.get("distance", 2.0)),
            origin=str(context.polar_config.get("observation_origin") or "mouth"),
            sphere_grid=(int(context.polar_config.get("spherical_theta_count") or 37),
                         int(context.polar_config.get("spherical_phi_count") or 72)),
        ),
        frame_override=SimpleNamespace(**request.frame),
        native_symmetry_plane=native_symmetry_plane(context),
    )


def sort_official_result(result: SweepResult) -> None:
    """Sort auxiliary force rows and retain absolute SPL beside derived directivity."""
    order = np.argsort(result.frequencies_hz, kind="stable")
    force = getattr(result, "radiation_impedance", None)
    if force is not None:
        result.radiation_impedance = force[order] if len(force) == len(order) else None
    sort_native_result_frequencies(result)
    with np.errstate(divide="ignore"):
        result.spl_db = 20.0 * np.log10(np.abs(result.pressure_complex) / 20e-6)


def common_artifact_results(results: Mapping[str, Any]) -> dict[str, Any]:
    """Keep acquired channel responses; export bases/traces on their shared axis."""
    shared = set.intersection(*(set(result.frequencies_hz) for result in results.values()))
    frequencies = np.asarray(sorted(shared), dtype=float)
    aligned = {}
    for name, result in results.items():
        indices = np.searchsorted(result.frequencies_hz, frequencies)
        member = copy(result)
        member.frequencies_hz = frequencies.copy()
        for field in ("pressure_complex", "impedance", "sphere_pressure_complex",
                      "surface_pressure_complex", "surface_neumann_complex", "frequency_status"):
            values = getattr(result, field, None)
            if values is not None:
                setattr(member, field, np.asarray(values)[indices])
        sort_official_result(member)
        aligned[name] = member
    return aligned


@warm_cache.signature_scope
def solve_compiled(
    request: CompiledRequest, *, channel_id: str | None = None,
    channel_callbacks: Mapping[str, tuple[Callable | None, Callable | None]] | None = None,
    worker_manager: WorkerManager | None = None, julia_executable: str | None = None,
    cancellation_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int, float], None] | None = None,
    on_frequency_result: Callable[[int, float, dict[str, Any]], bool | None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    _profile: SweepProfile | None = None,
) -> SweepResult:
    """Negotiate and map one acquired batch, retaining the manager for reuse."""
    options = request.wire["solver_options"]
    if options.get("bem_backend") not in {"cpu", "metal", "cuda", "rocm"}:
        raise UnsupportedBackend("BEAT runtime supports CPU, Metal, CUDA and ROCm")
    validate_exterior_physics(request.wire)
    profile = _profile or start_profile(logging.getLogger(__name__).info, "wg")
    if profile is not None and _profile is None:
        # Direct compiled callers have already selected their runtime.
        profile.mark("statuses done")
        profile.mark("request built")
    try:
        contract = importlib.import_module("beat_engine.beat_contract.worker")
    except ImportError as exc:
        raise OfficialBeatUnavailable("beat-engine is not installed.") from exc
    with SolveSession(cancellation_callback=cancellation_callback) as session:
        wire = dict(request.wire, cancel_path=str(session.cancel_path.resolve()))
        negotiate = validated_negotiator(contract, wire)
        try:
            try:
                client = (worker_manager or get_manager()).get_worker(
                    options["bem_backend"], julia_executable=julia_executable,
                )
            except (OwnershipClosed, UnsupportedBackend):
                raise
            except (ValueError, RuntimeError, OSError) as exc:
                # This phase has no request or compatibility validation: even
                # plain ValueError from discovery/host launch revokes the proof.
                readiness.probe_cache_clear()
                raise OfficialBeatUnavailable(str(exc)) from exc
            if profile is not None:
                profile.mark("worker acquired")
            session.submit(client, wire, negotiate=negotiate,
                           status_callback=status_callback)
            if profile is not None:
                profile.mark("submitted")
        except (discovery.JuliaDiscoveryError, assets.AssetsUnavailable, paths.RootConflict,
                registry.RecordRefused) as exc:
            readiness.probe_cache_clear()
            raise OfficialBeatUnavailable(str(exc)) from exc
        except (HostError, OSError) as exc:
            # Session admission/transport failures already revoked the proof.
            raise OfficialBeatUnavailable(str(exc)) from exc
        events = session.events()

        def reported_events():
            try:
                for event in events:
                    if profile is not None:
                        profile.event(event)
                    if isinstance(event, dict) and event.get("type") == "status" and status_callback:
                        status_callback(str(event.get("message") or ""))
                    yield event
                    if (isinstance(event, dict) and event.get("type") == "result"
                            and cancellation_callback is not None):
                        try:
                            cancellation_callback()
                        except BaseException:
                            session.request_cancel()
                            raise
            finally:
                events.close()

        selected = channel_id if channel_callbacks is None else next(iter(channel_callbacks))
        loading = request.channel_loading[selected]
        native = map_sweep(
            reported_events(), wire["frequencies_hz"], layout=request.layout,
            source_area_m2=loading.area_m2,
            excitation_port_id=request.channel_ports[selected][0],
            symmetry=options["symmetry"], precision=options["precision"],
            backend=options["bem_backend"], boundary_loading=loading,
            trace_counts=(len(request.mesh.points_m), len(request.mesh.faces))
            if request.surface_traces else None,
            compiled_request=request, channel_id=channel_id, _channel_callbacks=channel_callbacks,
            progress_callback=progress_callback, on_frequency_result=on_frequency_result,
            request_cancel=session.request_cancel,
        )
        if profile is not None:
            profile.mark("mapped")
        session.close()
        if profile is not None:
            profile.mark("closed")
        session.raise_callback_error()
        first = next(iter(native.values())) if channel_callbacks is not None else native
        if first.cancelled and not len(first.frequencies_hz):
            raise OfficialBeatUnavailable("BEAT solve cancelled before any results")
        return native


def solve_official_beat_from_msh_text(
    msh_text: str, context: SolverContext, *, backend: str = "cpu", precision: str = "float32",
    worker_manager: WorkerManager | None = None, julia_executable: str | None = None,
    mesh_scale_to_m: float = 1.0, **callbacks: Any,
) -> dict[str, Any]:
    """Explicit official entry point using the production parametric path."""
    from .beat import BeatUnavailable, solve_beat_from_msh_text

    try:
        return solve_beat_from_msh_text(
            msh_text, context, backend=backend, _official=True,
            _worker_manager=worker_manager, _julia_executable=julia_executable,
            _precision=precision, _mesh_scale_to_m=mesh_scale_to_m, **callbacks,
        )
    except BeatUnavailable as exc:
        raise OfficialBeatUnavailable(str(exc)) from exc


# The explicit entry above replaces the former standalone prototype port.


@warm_cache.signature_scope
def solve_transducer_compiled(
    request, *, worker_manager: WorkerManager | None = None,
    julia_executable: str | None = None,
    cancellation_callback: Callable[[], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    on_frequency_result: Callable | None = None,
):
    """Explicit opt-in v3 solve; return RMS voltage bases, never W4 results.

    This uses the same worker admission, capability negotiation, cancellation
    and ownership as the production bridge. Existing job/provider routing keeps
    its ideal-source path. CPU Float64 and Metal Float32 complete solids are
    qualified; reduced solids and other backends need consumer qualification.
    """
    from .beat_adapter.transducers import (
        TransducerRequest, map_transducer_sweep, validate_transducer_request,
    )
    if not isinstance(request, TransducerRequest):
        raise TypeError("Expected an opt-in TransducerRequest")
    validate_transducer_request(request)
    try:
        contract = importlib.import_module("beat_engine.beat_contract.worker")
    except ImportError as exc:
        raise OfficialBeatUnavailable("beat-engine is not installed.") from exc
    with SolveSession(cancellation_callback=cancellation_callback) as session:
        wire = dict(request.wire, cancel_path=str(session.cancel_path.resolve()))
        negotiate = validated_negotiator(contract, wire)
        try:
            try:
                client = (worker_manager or get_manager()).get_worker(
                    request.wire["solver_options"]["bem_backend"], julia_executable=julia_executable,
                )
            except (OwnershipClosed, UnsupportedBackend):
                raise
            except (ValueError, RuntimeError, OSError) as exc:
                readiness.probe_cache_clear()
                raise OfficialBeatUnavailable(str(exc)) from exc
            session.submit(client, wire, negotiate=negotiate, status_callback=status_callback)
        except (discovery.JuliaDiscoveryError, assets.AssetsUnavailable, paths.RootConflict,
                registry.RecordRefused, HostError, OSError) as exc:
            readiness.probe_cache_clear()
            raise OfficialBeatUnavailable(str(exc)) from exc
        events = session.events()

        def reported_events():
            try:
                for event in events:
                    if isinstance(event, dict) and event.get("type") == "status" and status_callback:
                        status_callback(str(event.get("message") or ""))
                    yield event
                    if isinstance(event, dict) and event.get("type") == "result" and cancellation_callback:
                        try:
                            cancellation_callback()
                        except BaseException:
                            session.request_cancel()
                            raise
            finally:
                events.close()

        result = map_transducer_sweep(reported_events(), request, on_frequency_result=on_frequency_result)
        session.close()
        session.raise_callback_error()
        if result.cancelled and not result.rows:
            raise OfficialBeatUnavailable("BEAT solve cancelled before any results")
        return result
