"""Metal-BEM's compatible worker, mapped onto WG's existing result contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import inspect
from typing import Any

import numpy as np

from .beat_adapter.results import acceleration_scale, map_sweep, real_source_copy_count
from .result_mapping import build_provisional_frequency_response


def worker_available() -> bool:
    """A pre-switch installation still runs its existing native path."""
    try:
        from hornlab_metal_bem import MetalEngineWorker, validate_compiled

        arguments = inspect.signature(MetalEngineWorker).parameters
        return callable(validate_compiled) and {"native_formulation", "complex_k_shift", "native_progress_cb"} <= set(arguments)
    except (ImportError, OSError, TypeError, ValueError):
        return False


def validate_worker_request(compiled: Any) -> None:
    """Reuse the provider's request and mesh validation before routing."""
    from hornlab_metal_bem import validate_compiled

    validate_compiled(compiled.wire)


def transducer_worker_available() -> bool:
    """Require the complete exterior contract, without starting a worker."""
    if not worker_available():
        return False
    try:
        from hornlab_metal_bem import compiled_capabilities

        capabilities = compiled_capabilities()
        if not isinstance(capabilities, Mapping):
            return False
        required = {
            "compiled_system_versions": {3},
            "precisions": {"float32"},
            "components": {"ideal_velocity_source", "electrodynamic_transducer"},
            "motion_profiles": {"uniform_normal", "rigid_translation"},
            "outputs": {"exterior_pressure", "radiation_impedance_matrix", "diaphragm_velocity", "voice_coil_current"},
            "symmetry": {"off", "x", "xy"},
            "phasor_conventions": {"exp(-i omega t)", "exp(+i omega t)"},
        }
        return (capabilities.get("backend") == "metal"
                and capabilities.get("request_schema_version") == 1
                and all(values <= set(capabilities.get(key, ())) for key, values in required.items()))
    except (ImportError, OSError, TypeError, ValueError):
        return False


def validate_transducer_worker_request(compiled: Any) -> None:
    from .beat_adapter.transducers import validate_transducer_request

    if not transducer_worker_available():
        raise ValueError("Installed Metal-BEM lacks the complete exterior network contract; select BEAT CPU or BEAT Metal")
    validate_transducer_request(compiled)
    try:
        validate_worker_request(compiled)
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Installed Metal-BEM cannot solve this exterior network: {exc}. Select BEAT CPU or BEAT Metal") from exc


def solve_transducer_worker(
    compiled: Any, *, cancellation_callback=None, status_callback=None, on_frequency_result=None,
):
    """Submit native RMS voltage bases and decode with the existing network owner."""
    from beat_engine.beat_contract.worker import negotiate_submission
    from hornlab_metal_bem import MetalEngineWorker

    from .beat_adapter.transducers import map_transducer_sweep
    from .formulation import bem_formulation

    validate_transducer_worker_request(compiled)
    if cancellation_callback:
        cancellation_callback()
    bem = bem_formulation(coupled_infinite_baffle=False)
    callback_error: list[BaseException] = []

    def native_progress(_index, _frequency, _entry):
        try:
            if cancellation_callback:
                cancellation_callback()
        except BaseException as exc:
            callback_error.append(exc)
            raise InterruptedError("Metal exterior network solve cancelled") from exc

    def acquired(sweep):
        if cancellation_callback:
            cancellation_callback()
        if on_frequency_result:
            on_frequency_result(sweep)

    worker = MetalEngineWorker(native_formulation=bem.formulation,
                               complex_k_shift=bem.complex_k_shift, native_progress_cb=native_progress)
    try:
        worker.ensure_started()
        negotiate_submission(worker.worker_info, compiled.wire, "solve")
        result = map_transducer_sweep(worker.submit(compiled.wire, status_callback=status_callback),
                                      compiled, on_frequency_result=acquired)
        if callback_error:
            raise callback_error[0]
        if result.cancelled and not result.rows:
            raise RuntimeError("Metal exterior network solve cancelled before any results; retry the solve")
        return result
    except BaseException as exc:
        if callback_error and exc is not callback_error[0]:
            raise callback_error[0] from exc
        raise
    finally:
        worker.terminate()


def select_worker_request(
    builder: Callable[[], Any], *, msh_text: str, merge_tol: float,
) -> tuple[Any | None, str | None]:
    """Select only requests the installed worker can validate before solving."""
    if not worker_available():
        return None, "The installed Metal-BEM package does not expose the compatible interface"
    try:
        from hornlab_metal_bem.mesh import _compact_surface_vertices, _merge_duplicate_vertices

        from .beat_adapter.mesh import read_surface

        if not np.isfinite(merge_tol):
            return None, "The native interface is required to preserve mesh loading"
        mesh = read_surface(msh_text)
        points, faces, unused = _compact_surface_vertices(mesh.points_m, mesh.faces)
        _, _, merged = _merge_duplicate_vertices(points, faces, merge_tol)
        if unused or merged:
            return None, "The native interface is required to preserve mesh loading"
        candidate = builder()
        validate_worker_request(candidate)
    except (ImportError, ValueError, RuntimeError):
        return None, "The compatible interface does not accept this mesh or request"
    return candidate, None


def solve_worker(
    compiled: Any, *, channel_id: str, context: Any, config: Any,
    progress: Callable[[int, int, float], None],
    cancel: Callable[[], None] | None, publish: Callable[[int, dict], None] | None,
):
    """Use the worker's event decoder and preserve native Metal result fields.

    This bridge is for a single normal-motion source. Its pressure loading is
    the area-weighted P1 mean, so the existing native power integrator can use
    that mean and the source's physical area without approximating a field.
    """
    from beat_engine.beat_contract.worker import negotiate_submission
    from hornlab_metal_bem import MetalEngineWorker
    from hornlab_metal_bem.bie import integrate_driven_surface_power
    from hornlab_metal_bem.observation import integrate_sphere_radiated_power, sphere_grid_solid_angle_weights
    from hornlab_metal_bem.sweep import _directivity_from_pressure

    frequencies = np.asarray(compiled.wire["frequencies_hz"], dtype=float)
    layout = compiled.layout
    loading = compiled.channel_loading[channel_id]
    options = compiled.wire["solver_options"]
    callback_error: list[BaseException] = []
    native_logs: list[dict[str, Any]] = []
    sound_speed = float(compiled.wire["compiled_system"]["regions"][0]["sound_speed_m_per_s"])
    density = float(compiled.wire["compiled_system"]["regions"][0]["density_kg_per_m3"])

    def native_progress(index: int, frequency: float, entry: dict) -> None:
        try:
            if cancel is not None:
                cancel()
            source_entries = entry.get("source_results")
            source_entry = source_entries[0] if isinstance(source_entries, list) else entry
            if index != len(native_logs) or not isinstance(source_entry, dict):
                raise ValueError("Metal worker returned out-of-order native diagnostics")
            native_logs.append({
                key: value for key, value in source_entry.items()
                if key not in {"impedance", "observation_pressure_complex", "observation_directivity_db",
                               "observation_angles_deg", "observation_planes", "observation_sphere_pressure_complex"}
            })
            progress(index, len(frequencies), frequency)
            if publish is None:
                return
            pressure = np.asarray(source_entry["observation_pressure_complex"], dtype=np.complex128).reshape(-1)
            n_planes, n_angles = len(layout.planes), len(layout.angles_deg)
            pressure = pressure[:n_planes * n_angles].reshape(n_planes, n_angles) * acceleration_scale(frequency)
            row = {
                "observation_angles_deg": layout.angles_deg,
                "observation_planes": list(layout.planes),
                "observation_pressure_complex": pressure,
                "observation_directivity_db": _directivity_from_pressure(
                    pressure, int(np.argmin(np.abs(layout.angles_deg))),
                ),
                "impedance": complex(source_entry["impedance"]) * acceleration_scale(frequency),
            }
            publish(index, build_provisional_frequency_response(
                index=index, frequency_hz=frequency, entry=row, config=config,
                context=context, backend="metal", sound_speed_m_per_s=sound_speed,
            ))
        except BaseException as exc:
            callback_error.append(exc)
            raise InterruptedError("Native Metal progress callback stopped the solve") from exc

    worker = MetalEngineWorker(
        native_formulation="complex_k", complex_k_shift=float(config.complex_k_shift),
        native_progress_cb=native_progress,
    )
    try:
        worker.ensure_started()
        negotiate_submission(worker.worker_info, compiled.wire, "solve")
        result = map_sweep(
            worker.submit(compiled.wire), frequencies, layout=layout,
            source_area_m2=loading.area_m2, excitation_port_id=compiled.channel_ports[channel_id][0],
            symmetry=options["symmetry"], precision=options["precision"], backend="metal",
            trace_counts=(len(compiled.mesh.points_m), len(compiled.mesh.faces)) if compiled.surface_traces else None,
            boundary_loading=loading, compiled_request=compiled, channel_id=channel_id,
        )
        if callback_error:
            raise callback_error[0]
        if result.cancelled or not len(result.frequencies_hz):
            if cancel is not None:
                cancel()
            raise RuntimeError("Metal worker cancelled before the requested sweep completed")
        if len(native_logs) != len(result.solver_log):
            raise RuntimeError("Metal worker did not report the native diagnostics for every frequency")
    finally:
        worker.terminate()

    velocity = np.asarray([acceleration_scale(f) for f in result.frequencies_hz])
    result.radiated_power_surface_w = integrate_driven_surface_power(
        result.impedance[:, None], velocity[:, None], np.asarray([loading.area_m2]),
        symmetry_multiplier=real_source_copy_count(options["symmetry"]),
    )
    if result.sphere_pressure_complex is not None:
        theta = int(context.polar_config.get("spherical_theta_count") or 37)
        phi = int(context.polar_config.get("spherical_phi_count") or 72)
        weights = sphere_grid_solid_angle_weights(theta, phi)
        result.radiated_power_sphere_w = integrate_sphere_radiated_power(
            result.sphere_pressure_complex, distance_m=float(config.observation.distance_m),
            solid_angle_weights_sr=weights, air_density=density, speed_of_sound=sound_speed,
        )
        result.radiated_power_sphere_coverage_sr = float(np.sum(weights))
    else:
        result.radiated_power_sphere_w = None
        result.radiated_power_sphere_coverage_sr = None
    for entry, native in zip(result.solver_log, native_logs, strict=True):
        worker_diagnostics = entry.get("native_diagnostics") or {}
        native_diagnostics = native.pop("native_diagnostics", None)
        entry.update(native)
        entry["native_diagnostics"] = {
            **(native_diagnostics if isinstance(native_diagnostics, dict) else {}),
            "engine_provenance": worker_diagnostics.get("engine_provenance"),
        }
    result.native_diagnostics = [entry.get("native_diagnostics", {}) for entry in result.solver_log]
    run = next((entry.get("native_diagnostics", {}).get("engine_provenance")
                for entry in result.solver_log
                if isinstance(entry.get("native_diagnostics"), dict)
                and isinstance(entry["native_diagnostics"].get("engine_provenance"), dict)), {})
    runtime = run.get("runtime") if isinstance(run, dict) else None
    result.native_runtime = dict(runtime) if isinstance(runtime, dict) else {}
    result.native_runtime["source"] = run.get("engine", {}) if isinstance(run, dict) else {}
    return result
