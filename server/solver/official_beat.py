"""Shared official BEAT production bridge through WG-managed solve sessions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import copy
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
import importlib
from types import SimpleNamespace
from typing import Any

import numpy as np

from .beat_adapter.request import CompiledRequest
from .beat_adapter.results import ResultContractError, SweepResult, map_sweep
from .beat_runtime import assets, discovery
from .beat_runtime.manager import WorkerManager, get_manager
from .beat_runtime.session import SolveSession
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


def production_statuses() -> dict[str, dict[str, Any]]:
    """Read matching compiled proof; static engine capabilities are insufficient."""
    from .beat_runtime import readiness

    statuses = readiness.beat_backend_statuses()
    installed_version = engine_version()
    for backend in ("cpu", "metal"):
        statuses[backend] = dict(statuses[backend], surface_traces=True, version=installed_version)
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


def solve_compiled(
    request: CompiledRequest, *, channel_id: str,
    worker_manager: WorkerManager | None = None, julia_executable: str | None = None,
    cancellation_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int, float], None] | None = None,
    on_frequency_result: Callable[[int, float, dict[str, Any]], bool | None] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> SweepResult:
    """Negotiate and map one acquired batch, retaining the manager for reuse."""
    try:
        contract = importlib.import_module("beat_engine.beat_contract.worker")
    except ImportError as exc:
        raise OfficialBeatUnavailable("beat-engine is not installed.") from exc
    options = request.wire["solver_options"]
    with SolveSession(cancellation_callback=cancellation_callback) as session:
        wire = dict(request.wire, cancel_path=str(session.cancel_path.resolve()))
        contract.validate_solve_request(wire)
        try:
            client = (worker_manager or get_manager()).get_worker(
                options["bem_backend"], julia_executable=julia_executable,
            )
            session.submit(client, wire, negotiate=contract.negotiate_submission,
                           status_callback=status_callback)
        except (discovery.JuliaDiscoveryError, assets.AssetsUnavailable) as exc:
            raise OfficialBeatUnavailable(str(exc)) from exc
        events = session.events()

        def reported_events():
            try:
                for event in events:
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

        loading = request.channel_loading[channel_id]
        native = map_sweep(
            reported_events(), wire["frequencies_hz"], layout=request.layout,
            source_area_m2=loading.area_m2,
            excitation_port_id=request.channel_ports[channel_id][0],
            symmetry=options["symmetry"], precision=options["precision"],
            backend=options["bem_backend"], boundary_loading=loading,
            trace_counts=(len(request.mesh.points_m), len(request.mesh.faces))
            if request.surface_traces else None,
            compiled_request=request, channel_id=channel_id,
            progress_callback=progress_callback, on_frequency_result=on_frequency_result,
            request_cancel=session.request_cancel,
        )
        session.close()
        session.raise_callback_error()
        if native.cancelled and not len(native.frequencies_hz):
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
