"""Metal-BEM's compatible worker, used only for exterior-transducer networks.

That route has no native equivalent. Parametric and imported solves always run
on the native interface; the worker route for them was retired after it
measured slower with the same results.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import inspect
from typing import Any


@contextmanager
def _worker_session(
    compiled: Any, *, native_formulation: str, complex_k_shift: float,
    cancellation_callback=None, status_callback=None,
):
    """Own one native submission and restore errors wrapped by its event stream."""
    from beat_engine.beat_contract.worker import negotiate_submission
    from hornlab_metal_bem import MetalEngineWorker

    callback_error: list[BaseException] = []

    def native_progress(index, frequency, entry):
        try:
            if cancellation_callback is not None:
                cancellation_callback()
        except BaseException as exc:
            callback_error.append(exc)
            raise InterruptedError("Native Metal progress callback stopped the solve") from exc

    worker = MetalEngineWorker(
        native_formulation=native_formulation, complex_k_shift=complex_k_shift,
        native_progress_cb=native_progress,
    )
    try:
        worker.ensure_started()
        negotiate_submission(worker.worker_info, compiled.wire, "solve")
        yield worker.submit(compiled.wire, status_callback=status_callback)
        if callback_error:
            raise callback_error[0]
    except BaseException as exc:
        if callback_error and exc is not callback_error[0]:
            raise callback_error[0] from exc
        raise
    finally:
        worker.terminate()


def worker_available() -> bool:
    """Whether the installed Metal-BEM exposes the compatible worker interface.

    Only ``transducer_worker_available`` asks: exterior-transducer networks need
    the worker, and are not offered on Metal without it.
    """
    try:
        from hornlab_metal_bem import MetalEngineWorker, validate_compiled

        arguments = inspect.signature(MetalEngineWorker).parameters
        return callable(validate_compiled) and {"native_formulation", "complex_k_shift", "native_progress_cb"} <= set(arguments)
    except (ImportError, OSError, TypeError, ValueError):
        return False


def validate_worker_request(compiled: Any) -> None:
    """Apply the provider's own request and mesh validation to a transducer request."""
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
    """Decode native RMS voltage bases as an experimental final-only sweep.

    The pinned provider emits records only after computing the whole sweep and
    emits none on cancellation. Keep the shared solver callback signature, but
    do not publish provisional frequencies or promise a recoverable prefix.
    """
    from .beat_adapter.transducers import map_transducer_sweep
    from .formulation import bem_formulation

    validate_transducer_worker_request(compiled)
    if cancellation_callback:
        cancellation_callback()
    bem = bem_formulation(coupled_infinite_baffle=False)

    def acquired(_sweep):
        if cancellation_callback:
            cancellation_callback()

    with _worker_session(
        compiled, native_formulation=bem.formulation, complex_k_shift=bem.complex_k_shift,
        cancellation_callback=cancellation_callback, status_callback=status_callback,
    ) as events:
        result = map_transducer_sweep(events, compiled, on_frequency_result=acquired)
        if result.cancelled:
            raise RuntimeError(
                "Metal exterior network solve cancelled; partial results are unavailable. "
                "Retry the full sweep or select BEAT CPU or BEAT Metal."
            )
        return result
