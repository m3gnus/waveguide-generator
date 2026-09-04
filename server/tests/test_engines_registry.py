"""Capability detection and real-dispatch persistence tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pytest

from server.engines import registry
from server.engines.dryrun import DryRunEngine
from server.jobs.api import create_jobs_router
from server.jobs.models import SolveRequest
from server.jobs.runtime import (
    EngineUnavailableError,
    JobRuntime,
    SymmetryValidationError,
    resolve_submission,
)
from server.jobs.store import JobStore
from server.solver.base import EngineRunResult
from server.solver.field_traces_store import (
    FieldTraceArtifact,
    FieldTraceChannel,
    METAL_FIELD_TRACE_BACKEND,
)


def _stub_beat_backends(monkeypatch, **available: bool) -> None:
    """Pin every BEAT backend's availability, so detection is not host-dependent.

    ``beat_backend_statuses`` asks the package probe about the accelerators and
    the filesystem about the CPU path, both of which vary by machine. Tests that
    are about the registry's fan-out, not about this laptop, state the four
    answers outright.
    """

    from server.solver import beat

    def statuses() -> dict[str, dict[str, object]]:
        return {
            backend: {
                "available": bool(available.get(backend, False)),
                "reason": f"{backend} stub",
                "version": "1",
                "backend": backend,
                "surface_traces": False,
            }
            for backend in beat.BEAT_BACKENDS
        }

    monkeypatch.setattr(beat, "beat_backend_statuses", statuses)


def test_detection_uses_honest_probe_reasons_and_dryrun_gate(monkeypatch) -> None:
    from server.solver import bempp, circsym, metal

    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "helper loadable", "version": "1"})
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": False, "reason": "package absent", "version": None})
    _stub_beat_backends(monkeypatch, cpu=True)
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": True, "reason": "meridian ready", "version": "2"})
    detected = registry.detect_engines(environ={"WG2_ENABLE_DRYRUN": "1"})
    assert [(item.name, item.available, item.reason) for item in detected] == [
        ("dryrun", True, "Enabled explicitly by WG2_ENABLE_DRYRUN=1"),
        ("axisym", True, "meridian ready"),
        ("metal", True, "helper loadable"),
        ("bempp", False, "package absent"),
        ("beat-cuda", False, "cuda stub"),
        ("beat-rocm", False, "rocm stub"),
        ("beat-metal", False, "metal stub"),
        ("beat-cpu", True, "cpu stub"),
    ]
    assert detected[1].formulations == ("axisymmetric",)
    assert detected[1].mountings == ("free-standing", "infinite-baffle")
    assert detected[1].cancellation_granularity == "intra-frequency"
    assert detected[2].fast_paths == ()
    assert all(item.name != "circsym" for item in detected)
    # The wire name is an identifier; the label is what the picker shows.
    labels = {item.name: item.display_label() for item in detected}
    assert labels["beat-cuda"] == "BEAT \u00b7 CUDA \u2014 NVIDIA GPU"
    assert labels["beat-cpu"] == "BEAT \u00b7 CPU \u2014 no GPU needed"
    assert labels["metal"] == "Metal \u2014 Apple GPU"


def test_every_beat_backend_is_selectable_and_ordered(monkeypatch) -> None:
    """The registry's engine names and BEAT's backend list must not drift apart.

    ``FULL3D_ENGINE_ORDER`` spells the four ``beat-*`` names out rather than
    importing them, because the registry is imported at boot and the BEAT
    adapter pulls the optional Julia package with it. That is the price of a
    fast boot; this is what stops it becoming a silently missing engine when a
    fifth backend lands.
    """

    from server.solver import beat

    expected = {beat.beat_engine_name(backend) for backend in beat.BEAT_BACKENDS}
    assert expected <= set(registry.FULL3D_ENGINE_ORDER)
    assert expected <= registry.SELECTABLE_ENGINE_NAMES
    assert beat.LEGACY_BEAT_ENGINE in registry.SELECTABLE_ENGINE_NAMES
    # AUTO must not reach the portable CPU path ahead of the CPU engine this
    # project ships and has measured.
    order = list(registry.FULL3D_ENGINE_ORDER)
    assert order.index("bempp") < order.index("beat-cpu") < order.index("dryrun")
    assert order.index("metal") == 0

    _stub_beat_backends(monkeypatch, metal=True, cpu=True)
    engine = registry.create_engine("beat-metal")
    assert engine is not None and engine.name == "beat-metal" and engine.backend == "metal"
    legacy = registry.create_engine("beat")
    assert legacy is not None and legacy.name == "beat" and legacy.backend is None
    assert registry.create_engine("beat-vulkan") is None


def test_beat_advertises_the_reduced_domains_and_di_sphere_it_really_has(
    monkeypatch,
) -> None:
    """The registry must not under-report BEAT, nor promise the xz half.

    BEAT gained theta-major sphere grids, diagonal cuts and axial motion, but
    the registry still answered ``di_sphere=False`` and offered only the full
    domain. It mirrors across x, or x and y, so ATH quadrants 1234, 14 and 1
    are solvable; quadrants 12 is refused by the package itself, and a bare
    "half" here would promise it.
    """

    from server.solver import beat, bempp, circsym, metal

    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False, "reason": "no helper", "version": None})
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": False, "reason": "package absent", "version": None})
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": False, "reason": "absent", "version": None})
    _stub_beat_backends(monkeypatch, cuda=True)
    detected = {item.name: item for item in registry.detect_engines(environ={})}
    # The backend is an execution choice, not a formulation: every variant
    # advertises the same reduced domains, because the same solver runs on each.
    for backend in beat.BEAT_BACKENDS:
        variant = detected[beat.beat_engine_name(backend)]
        assert variant.di_sphere is True
        assert variant.symmetry_domains == ("full", "half-yz", "quarter")
        assert "half" not in variant.symmetry_domains
        assert variant.mountings == ("free-standing",)
    assert detected["bempp"].symmetry_domains == ("full", "half", "quarter")


def test_beat_field_trace_capability_follows_the_installed_package(
    monkeypatch,
) -> None:
    """Surface-trace retention landed after the first pinned build.

    Advertising it unconditionally would make the app request traces a pinned
    solver cannot return; hard-coding it False would keep them unreachable
    after a pin bump. The probe reports what the installed package can do.
    """

    from server.solver import beat, bempp, circsym, metal

    monkeypatch.setattr(metal, "metal_status", lambda: {"available": False, "reason": "no helper", "version": None})
    monkeypatch.setattr(bempp, "bempp_status", lambda: {"available": False, "reason": "absent", "version": None})
    monkeypatch.setattr(circsym, "circsym_status", lambda: {"available": False, "reason": "absent", "version": None})
    for supported in (False, True):
        monkeypatch.setattr(
            beat,
            "beat_backend_statuses",
            lambda supported=supported: {
                backend: {
                    "available": backend == "cuda",
                    "reason": "cuda",
                    "version": "1",
                    "backend": backend,
                    "surface_traces": supported,
                }
                for backend in beat.BEAT_BACKENDS
            },
        )
        detected = {item.name: item for item in registry.detect_engines(environ={})}
        # A property of the installed package, so it is the same on every
        # backend -- and reported on every backend, not just the selected one.
        for backend in beat.BEAT_BACKENDS:
            assert detected[beat.beat_engine_name(backend)].field_traces is supported


def test_auto_resolution_prefers_metal_then_beat_gpus_then_bempp() -> None:
    """AUTO: metal > BEAT's accelerators > bempp > BEAT-CPU > dryrun.

    The CPU path's position is the deliberate part. It is user-facing now, so
    AUTO can reach it at all, but it sits behind BEMPP: it is the portable last
    resort, not a peer of the CPU engine this project has measured and shipped.
    Every host with a Julia advertises it, so putting it any earlier would take
    solves away from BEMPP on every CPU-only machine.
    """

    def info(name: str, available: bool) -> registry.EngineInfo:
        return registry.EngineInfo(name, available, "test", "1")

    mac = [info("metal", True), info("beat-metal", True), info("beat-cpu", True), info("bempp", True)]
    assert registry.resolve_auto_engine(capabilities=mac) == "metal"
    gpu_windows = [info("metal", False), info("beat-cuda", True), info("beat-cpu", True), info("bempp", True)]
    assert registry.resolve_auto_engine(capabilities=gpu_windows) == "beat-cuda"
    cpu_windows = [info("metal", False), info("beat-cuda", False), info("beat-cpu", True), info("bempp", True)]
    assert registry.resolve_auto_engine(capabilities=cpu_windows) == "bempp"
    no_bempp = [info("bempp", False), info("beat-cpu", True), info("dryrun", True)]
    assert registry.resolve_auto_engine(capabilities=no_bempp) == "beat-cpu"
    assert registry.get_engine("beat-cuda", capabilities=gpu_windows) is not None


def test_auto_keeps_synthetic_dryrun_available_for_reduced_geometry() -> None:
    dryrun = registry.EngineInfo(
        "dryrun", True, "enabled", "builtin", symmetry_domains=("full",)
    )

    assert (
        registry.resolve_auto_engine(capabilities=[dryrun], resolved_quadrants=1)
        == "dryrun"
    )


def test_legacy_beat_name_resolves_to_the_best_available_variant() -> None:
    """A design file or stored preference written before the split still works.

    ``beat`` named the family, and the package probe chose the backend. It now
    means the same thing -- the best BEAT this host has -- resolved against the
    same order AUTO uses, so an old file does not quietly become a different
    solver or an unavailable engine.
    """

    def info(name: str, available: bool) -> registry.EngineInfo:
        return registry.EngineInfo(name, available, "test", "1")

    gpu = [info("beat-cuda", True), info("beat-cpu", True), info("metal", True)]
    assert registry.resolve_legacy_beat_engine(gpu) == "beat-cuda"
    cpu_only = [info("beat-cuda", False), info("beat-cpu", True), info("metal", True)]
    assert registry.resolve_legacy_beat_engine(cpu_only) == "beat-cpu"
    # No BEAT at all is an unavailable engine, not a silent swap to Metal.
    none_at_all = [info("beat-cuda", False), info("beat-cpu", False), info("metal", True)]
    assert registry.resolve_legacy_beat_engine(none_at_all) is None


def _planner_request(
    *,
    engine: str = "auto",
    solver_mode: str = "auto",
    sim_type: str = "freestanding",
    symmetry: str = "auto",
) -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "simulation": {
                    "f1": 500,
                    "f2": 8000,
                    "num_frequencies": 3,
                    "sim_type": sim_type,
                },
            },
            "options": {
                "engine": engine,
                "solver_mode": solver_mode,
                "symmetry": symmetry,
            },
        }
    )


@pytest.mark.parametrize("solver_mode", ["auto", "circsym"])
def test_formulation_planner_uses_portable_axisym_without_revolved_symmetry(
    monkeypatch,
    solver_mode: str,
) -> None:
    from server.solver import circsym

    monkeypatch.setattr(
        "server.jobs.runtime.resolve_symmetry",
        lambda _design: pytest.fail(
            "axisymmetric submissions must not build a revolved surface"
        ),
    )
    monkeypatch.setattr(circsym, "axisymmetric_eligibility_reasons", lambda _request: [])
    monkeypatch.setattr(
        circsym,
        "axisymmetric_plan_cost",
        lambda _request, *, full_3d_quadrants: {
            "model": "test",
            "full_3d_quadrants": full_3d_quadrants,
        },
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("axisym", True, "portable CPU", "1"),
            registry.EngineInfo("bempp", True, "Windows CPU fallback", "1"),
        ],
        factory=lambda name: object() if name in {"axisym", "bempp"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(_planner_request(solver_mode=solver_mode), engine_registry)
    )

    assert resolution.engine_name == "axisym"
    assert resolution.request.options.engine == "axisym"
    assert resolution.symmetry_metadata["solver_plan"] == {
        "formulation": "axisymmetric",
        "engine": "axisym",
        "reason": (
            "forced by solver_mode='circsym'"
            if solver_mode == "circsym"
            else "AUTO selected the eligible platform-neutral axisymmetric runner"
        ),
        "eligibility_reasons": [],
        "cost_evidence": {"model": "test", "full_3d_quadrants": 1},
    }
    assert resolution.symmetry_metadata["domain"] == "continuous-axisymmetric"


def test_axisymmetric_cost_evidence_uses_refined_meridian_and_requested_domain() -> None:
    from server.solver.circsym import axisymmetric_plan_cost

    cost = axisymmetric_plan_cost(_planner_request(), full_3d_quadrants=1)

    assert cost["model"] == "deterministic-reduced-vs-revolved-dense-v1"
    assert cost["frequency_count"] == 3
    assert cost["frequency_max_hz"] == 8000.0
    assert cost["meridian_segments"] > 0
    assert cost["azimuth_quadrature"]["maximum"] >= cost["azimuth_quadrature"][
        "minimum"
    ]
    assert cost["axisymmetric"]["ring_quadrature_terms"] > 0
    assert cost["full_3d_equivalent"]["requested_quadrants"] == 1
    assert cost["full_3d_equivalent"]["domain_fraction"] == 0.25
    assert cost["full_3d_equivalent"]["estimated_triangles"] >= cost[
        "meridian_segments"
    ]
    assert cost["relative_dense_unknowns"] >= 1.0
    assert cost["relative_dense_matrix_memory"] >= 1.0


def test_formulation_planner_falls_back_to_selected_full_3d_backend(
    monkeypatch,
) -> None:
    from server.solver import circsym

    monkeypatch.setattr(
        circsym,
        "axisymmetric_eligibility_reasons",
        lambda _request: ["mouth is not circular"],
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("axisym", True, "portable CPU", "1"),
            registry.EngineInfo("bempp", True, "CPU", "1"),
        ],
        factory=lambda name: object() if name in {"axisym", "bempp"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(_planner_request(engine="bempp"), engine_registry)
    )

    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["solver_plan"] == {
        "formulation": "full-3d",
        "engine": "bempp",
        "reason": "axisymmetric formulation was not eligible",
        "eligibility_reasons": ["mouth is not circular"],
    }


def test_submission_plan_endpoint_uses_the_submitted_design(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from server.solver import circsym

    eligibility_reasons: list[str] = []
    monkeypatch.setattr(
        circsym,
        "axisymmetric_eligibility_reasons",
        lambda _request: eligibility_reasons,
    )
    monkeypatch.setattr(
        circsym,
        "axisymmetric_plan_cost",
        lambda _request, *, full_3d_quadrants: {
            "model": "test",
            "full_3d_quadrants": full_3d_quadrants,
        },
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("axisym", True, "portable CPU", "1"),
            registry.EngineInfo("beat", False, "GPU backend is offline", None),
        ],
        factory=lambda name: object() if name == "axisym" else None,
    )
    runtime = JobRuntime(
        JobStore(tmp_path / "jobs.db"),
        engine_registry=engine_registry,
    )
    endpoint = next(
        route.endpoint
        for route in create_jobs_router(runtime).routes
        if getattr(route, "path", None) == "/api/solve/plan"
    )
    request = _planner_request(engine="beat", solver_mode="auto")

    eligible = asyncio.run(endpoint(request))
    assert eligible.engine == "axisym"
    assert eligible.formulation == "axisymmetric"
    assert eligible.eligibility_reasons == []

    eligibility_reasons.append("mouth is not circular")
    ineligible = asyncio.run(endpoint(request))
    assert ineligible.status_code == 503
    refusal = json.loads(ineligible.body)
    assert refusal["error"]["code"] == "engine_unavailable"
    # Still a refusal, and it must stay one: the axisymmetric runner is the
    # only registered engine and this design is not eligible for it, so there
    # is genuinely nothing to fall back to. The message now says that rather
    # than naming BEAT alone, because "install BEAT" is not the only remedy.
    assert refusal["error"]["message"] == (
        "Solve engine 'beat' is unavailable, and no other engine on this "
        "host can take its place. GPU backend is offline Install/enable "
        "Axisymmetric, Metal, BEAT, or BEMPP; explicitly enable dry-run "
        "with WG2_ENABLE_DRYRUN=1 for synthetic development solves."
    )


def test_a_stored_legacy_beat_request_still_submits(monkeypatch) -> None:
    """A design file written before the backends were separately selectable.

    It says ``engine: beat``, which no longer matches an advertised engine. The
    submission must resolve it to a variant this host can run -- and it must do
    so *after* the axisymmetric planner, which resolves an explicit ``beat``
    request to the meridian runner for eligible circular geometry without ever
    consulting BEAT's availability.
    """

    from server.solver import circsym

    monkeypatch.setattr(
        circsym,
        "axisymmetric_eligibility_reasons",
        lambda _request: ["mouth is not circular"],
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("axisym", True, "portable CPU", "1"),
            registry.EngineInfo("beat-cuda", False, "no NVIDIA GPU", None),
            registry.EngineInfo("beat-cpu", True, "Julia found", "1"),
        ],
        factory=lambda name: object() if name in {"axisym", "beat-cpu"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(_planner_request(engine="beat"), engine_registry)
    )

    assert resolution.engine_name == "beat-cpu"
    # The request itself is rewritten, so the job records the engine that ran.
    assert resolution.request.options.engine == "beat-cpu"
    assert resolution.symmetry_metadata["solver_plan"]["engine"] == "beat-cpu"


def test_a_legacy_beat_request_with_no_beat_backend_substitutes_and_says_why(
    monkeypatch,
) -> None:
    """The two mechanisms compose: resolve the name first, substitute second.

    With no BEAT backend on the host there is nothing to resolve ``beat`` to,
    so it falls through to the ordinary unavailable-engine substitution and the
    user is told which engine ran instead and why -- rather than being refused,
    which is what this did before the substitution fallback existed.
    """

    from server.solver import circsym

    monkeypatch.setattr(
        circsym,
        "axisymmetric_eligibility_reasons",
        lambda _request: ["mouth is not circular"],
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("bempp", True, "CPU", "1"),
            registry.EngineInfo("beat-cpu", False, "No Julia executable was found.", None),
        ],
        factory=lambda name: object() if name == "bempp" else None,
    )

    resolution = asyncio.run(
        resolve_submission(_planner_request(engine="beat"), engine_registry)
    )

    assert resolution.engine_name == "bempp"
    substitution = resolution.symmetry_metadata["solver_plan"]["engine_substitution"]
    assert substitution["requested"] == "beat"
    assert substitution["resolved"] == "bempp"
    # The CPU variant carries the reason worth reporting: it is the one backend
    # every host could run, so whatever stops it is why none of the four works.
    assert "No Julia executable was found." in substitution["reason"]


def test_a_beat_variant_this_host_lacks_substitutes_like_any_other_engine(
    monkeypatch,
) -> None:
    """A preference made on a CUDA box, opened on a Mac.

    Backend-level selection has to compose with the substitution fallback, or
    the new engine names would be the one class of stored selection that
    refuses instead of falling back.
    """

    from server.solver import circsym

    monkeypatch.setattr(
        circsym,
        "axisymmetric_eligibility_reasons",
        lambda _request: ["mouth is not circular"],
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("metal", True, "helper loadable", "1"),
            registry.EngineInfo("beat-cuda", False, "Needs an NVIDIA GPU.", None),
            registry.EngineInfo("beat-cpu", True, "Julia found", "1"),
        ],
        factory=lambda name: object() if name in {"metal", "beat-cpu"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(_planner_request(engine="beat-cuda"), engine_registry)
    )

    assert resolution.engine_name == "metal"
    substitution = resolution.symmetry_metadata["solver_plan"]["engine_substitution"]
    assert substitution == {
        "requested": "beat-cuda",
        "resolved": "metal",
        "reason": "Needs an NVIDIA GPU.",
    }


@pytest.mark.parametrize("symmetry", ["half_xz", "auto"])
def test_auto_skips_a_backend_that_cannot_solve_the_resolved_symmetry(
    monkeypatch: pytest.MonkeyPatch, symmetry: str
) -> None:
    """Planning must not hand BEAT the xz half it rejects after meshing."""

    from server.solver.symmetry import SymmetryResolution

    if symmetry == "auto":
        monkeypatch.setattr(
            "server.jobs.runtime.resolve_symmetry",
            lambda _design: SymmetryResolution(
                quadrants=12,
                xz=True,
                yz=False,
                reasons={"xz": [], "yz": ["asymmetric"]},
                tolerance_mm=0.01,
            ),
        )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo(
                "beat-cuda", True, "CUDA", "1", symmetry_domains=("full", "half-yz", "quarter")
            ),
            registry.EngineInfo(
                "bempp", True, "CPU", "1", symmetry_domains=("full", "half", "quarter")
            ),
        ],
        factory=lambda name: object() if name in {"beat-cuda", "bempp"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(engine="auto", solver_mode="full_3d", symmetry=symmetry),
            engine_registry,
        )
    )

    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["resolved_quadrants"] == 12


def test_forced_backend_refuses_unsupported_symmetry_during_planning() -> None:
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo(
                "beat-cuda", True, "CUDA", "1", symmetry_domains=("full", "half-yz", "quarter")
            )
        ],
        factory=lambda _name: object(),
    )

    with pytest.raises(SymmetryValidationError, match="does not support the xz half"):
        asyncio.run(
            resolve_submission(
                _planner_request(
                    engine="beat-cuda", solver_mode="full_3d", symmetry="half_xz"
                ),
                engine_registry,
            )
        )


def test_unavailable_engine_substitution_skips_incompatible_beat_backend() -> None:
    """The original regression: a missing Metal choice, BEAT-CUDA, and xz half."""

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo(
                "metal", False, "Metal is unavailable", None, symmetry_domains=("full", "half", "quarter")
            ),
            registry.EngineInfo(
                "beat-cuda", True, "CUDA", "1", symmetry_domains=("full", "half-yz", "quarter")
            ),
            registry.EngineInfo(
                "bempp", True, "CPU", "1", symmetry_domains=("full", "half", "quarter")
            ),
        ],
        factory=lambda name: object() if name in {"beat-cuda", "bempp"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(
                engine="metal", solver_mode="full_3d", symmetry="half_xz"
            ),
            engine_registry,
        )
    )

    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["solver_plan"]["engine_substitution"] == {
        "requested": "metal",
        "resolved": "bempp",
        "reason": "Metal is unavailable",
    }


def test_bempp_coupled_infinite_baffle_planner_requires_full_domain() -> None:
    engine_registry = registry.EngineRegistry(
        detector=lambda: [registry.EngineInfo("bempp", True, "CPU", "1")],
        factory=lambda name: object() if name == "bempp" else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(
                engine="bempp",
                solver_mode="full_3d",
                sim_type="infinite-baffle",
            ),
            engine_registry,
        )
    )
    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["resolved_quadrants"] == 1234
    assert "validated full-domain" in resolution.symmetry_metadata["solver_plan"][
        "symmetry_reason"
    ]

    with pytest.raises(SymmetryValidationError, match="requires Full symmetry"):
        asyncio.run(
            resolve_submission(
                _planner_request(
                    engine="bempp",
                    solver_mode="full_3d",
                    sim_type="infinite-baffle",
                    symmetry="quarter",
                ),
                engine_registry,
            )
        )


def test_capability_snapshot_is_reused_by_solve_submission(tmp_path: Path) -> None:
    calls = 0

    def detect_once() -> list[registry.EngineInfo]:
        nonlocal calls
        calls += 1
        return [registry.EngineInfo("dryrun", True, "test", "builtin")]

    shared_registry = registry.EngineRegistry(
        detector=detect_once, factory=lambda _name: DryRunEngine()
    )
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "simulation": {"f1": 500, "f2": 501},
            },
            "options": {"engine": "dryrun", "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        assert (await shared_registry.capabilities())[0].available is True
        runtime = JobRuntime(
            JobStore(tmp_path / "cached.db"), engine_registry=shared_registry
        )
        await runtime.submit(request)
        await runtime.wait_idle()
        await runtime.shutdown()

    asyncio.run(scenario())
    assert calls == 1


def test_real_runtime_seam_persists_artifact_stats_results_and_stages(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb):
            cancel_cb()
            stage_cb("mesh_prepare", 0.5, "Building real mesh")
            stage_cb("frequency_solve", 0.5, "Solving native frequency")
            return EngineRunResult(
                results={
                    "frequencies": [500.0],
                    "directivity": {},
                    "spl_on_axis": {"frequencies": [500.0], "spl": [90.0], "phase_degrees": [0.0]},
                    "impedance": {"frequencies": [500.0], "real": [1.0], "imaginary": [0.0]},
                    "di": {"frequencies": [500.0], "di": {}},
                    "metadata": {"engine": "fake-metal"},
                },
                msh_text="$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                mesh_stats={"vertex_count": 3, "triangle_count": 1},
            )

    monkeypatch.setattr("server.jobs.runtime.get_engine", lambda name: FakeMetal())
    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"f1": 500, "f2": 501, "num_frequencies": 1}},
            "options": {"engine": "metal", "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: FakeMetal(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        job = await runtime.get_job(job_id)
        assert job["status"] == "complete"
        assert job["has_mesh_artifact"] is True
        assert job["mesh_stats"]["triangle_count"] == 1
        assert (await runtime.get_results(job_id))["metadata"]["engine"] == "fake-metal"
        assert "$MeshFormat" in await runtime.get_mesh_artifact(job_id)
        event_types = [event["type"] for event in runtime.store.replay_events(0)]
        assert "stage" in event_types
        assert "log" in event_types
        assert event_types[-1] == "completed"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_runtime_exposes_fresh_field_traces_and_marks_legacy_results(
    tmp_path: Path,
) -> None:
    mesh_text = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
3
1 0 0 0
2 1 0 0
3 0 1 0
$EndNodes
$Elements
1
1 2 2 1 1 1 2 3
$EndElements
"""

    class FakeMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb):
            del stage_cb
            cancel_cb()
            if not request.options.polar_config.field_plane:
                return EngineRunResult(
                    results={
                        "frequencies": [500.0],
                        "metadata": {"engine": "fake-metal"},
                    },
                    msh_text=mesh_text,
                    mesh_stats={"vertex_count": 3, "triangle_count": 1},
                    field_trace_unavailable_reason="disabled_by_option",
                )
            return EngineRunResult(
                results={"frequencies": [500.0], "metadata": {"engine": "fake-metal"}},
                msh_text=mesh_text,
                mesh_stats={"vertex_count": 3, "triangle_count": 1},
                field_traces=FieldTraceArtifact(
                    mesh_text=mesh_text,
                    frequencies_hz=np.asarray([500.0]),
                    k_real=np.asarray([9.159]),
                    k_imag=np.asarray([0.045795]),
                    symmetry_plane=None,
                    solve_path="full-3d",
                    channels=(
                        FieldTraceChannel(
                            "default",
                            np.ones((1, 3), dtype=np.complex128),
                            np.ones((1, 1), dtype=np.complex128),
                        ),
                    ),
                    backend=METAL_FIELD_TRACE_BACKEND,
                ),
            )

    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "simulation": {"f1": 500, "f2": 501, "num_frequencies": 1},
            },
            "options": {"engine": "metal", "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        runtime = JobRuntime(
            store,
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: FakeMetal(),
            ),
        )
        fresh_id = await runtime.submit(request)
        await runtime.wait_idle()

        fresh = await runtime.get_results(fresh_id)
        assert fresh["metadata"]["field_plane_available"] is True
        assert fresh["metadata"]["field_trace_bytes"] == 32
        assert fresh["metadata"]["unavailable_reason"] is None
        fresh_job = await runtime.get_job(fresh_id)
        assert fresh_job["field_plane_available"] is True
        assert fresh_job["field_trace_bytes"] == 32

        disabled_request = SolveRequest.model_validate(
            {
                "design": {
                    "formula": "OSSE",
                    "simulation": {"f1": 500, "f2": 501, "num_frequencies": 1},
                },
                "options": {
                    "engine": "metal",
                    "stage_delay_ms": 0,
                    "polar_config": {"field_plane": False},
                },
            }
        )
        disabled_id = await runtime.submit(disabled_request)
        await runtime.wait_idle()
        disabled = await runtime.get_results(disabled_id)
        assert disabled["metadata"]["field_plane_available"] is False
        assert disabled["metadata"]["field_trace_bytes"] is None
        assert disabled["metadata"]["unavailable_reason"] == "disabled_by_option"
        disabled_job = await runtime.get_job(disabled_id)
        assert disabled_job["field_plane_available"] is False
        assert disabled_job["unavailable_reason"] == "disabled_by_option"

        now = datetime.now().isoformat()
        store.create_job(
            {
                "id": "legacy",
                "status": "complete",
                "created_at": now,
                "updated_at": now,
                "queued_at": now,
                "completed_at": now,
                "progress": 1.0,
                "stage": "complete",
                "stage_message": "complete",
                "config_json": request.model_dump(mode="json"),
                "config_summary_json": {"formula_type": "OSSE"},
                "task_metadata": {},
            }
        )
        store.store_results("legacy", {"frequencies": [500.0], "metadata": {}})
        legacy = await runtime.get_results("legacy")
        assert legacy["metadata"] == {
            "field_plane_available": False,
            "field_trace_bytes": None,
            "unavailable_reason": "solve_predates_traces",
        }
        legacy_job = await runtime.get_job("legacy")
        assert legacy_job["field_plane_available"] is False
        assert legacy_job["unavailable_reason"] == "solve_predates_traces"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_real_runtime_persists_mesh_before_a_native_solve_failure(
    tmp_path: Path, monkeypatch
) -> None:
    class FailingMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb, artifact_cb):
            cancel_cb()
            await artifact_cb("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n", {"triangle_count": 7})
            raise RuntimeError("native solve failed")

    monkeypatch.setattr("server.jobs.runtime.get_engine", lambda name: FailingMetal())
    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"f1": 500, "f2": 501}},
            "options": {"engine": "metal"},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "failed.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: FailingMetal(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        job = await runtime.get_job(job_id)
        assert job["status"] == "error"
        assert job["has_mesh_artifact"] is True
        assert job["mesh_stats"]["triangle_count"] == 7
        assert "$MeshFormat" in await runtime.get_mesh_artifact(job_id)
        await runtime.shutdown()

    asyncio.run(scenario())


def test_real_runtime_persists_advisory_mesh_warning_in_job_log(
    tmp_path: Path, monkeypatch
) -> None:
    warning = (
        "Large solve mesh: 7,173 triangles against a warning threshold of "
        "4,500. The solve will continue."
    )

    class WarningMetal:
        name = "metal"

        async def run(self, request, *, cancel_cb, stage_cb, artifact_cb):
            cancel_cb()
            await artifact_cb(
                "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                {"triangle_count": 7_173, "warnings": [warning]},
            )
            return EngineRunResult(
                results={
                    "frequencies": [500.0],
                    "directivity": {},
                    "spl_on_axis": {"frequencies": [500.0], "spl": [90.0]},
                    "metadata": {},
                },
            )

    request = SolveRequest.model_validate(
        {
            "design": {"formula": "OSSE", "simulation": {"f1": 500, "f2": 501}},
            "options": {"engine": "metal"},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "warning.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("metal", True, "test", "1")],
                factory=lambda _name: WarningMetal(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        job = await runtime.get_job(job_id)
        assert job["status"] == "complete"
        assert job["mesh_stats"]["warnings"] == [warning]
        assert warning in job["log_tail"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_auto_skips_beat_for_a_coupled_infinite_baffle_solve() -> None:
    """AUTO must resolve against the mounting, not just the host's engine list.

    On a GPU host AUTO prefers BEAT's accelerator over bempp. BeatEngine.run
    rejects every coupled infinite-baffle request, so for such a design that
    preference persisted a job which could only fail, while the
    coupling-capable BEMPP sitting beside it on the same host could have
    solved it.
    """

    def gpu_host(*, coupled_bempp: bool) -> list[registry.EngineInfo]:
        return [
            registry.EngineInfo(
                "beat-cuda",
                True,
                "CUDA",
                "1",
                formulations=("full-3d",),
                mountings=("free-standing",),
            ),
            registry.EngineInfo(
                "bempp",
                True,
                "CPU",
                "1",
                formulations=("full-3d",),
                mountings=(
                    ("free-standing", "infinite-baffle")
                    if coupled_bempp
                    else ("free-standing",)
                ),
            ),
        ]

    coupled = registry.EngineRegistry(
        detector=lambda: gpu_host(coupled_bempp=True),
        factory=lambda _name: object(),
    )
    resolution = asyncio.run(
        resolve_submission(
            _planner_request(solver_mode="full_3d", sim_type="infinite-baffle"),
            coupled,
        )
    )
    assert resolution.engine_name == "bempp"

    # The GPU engine is still preferred for everything it can actually solve;
    # note that sim_type spells this "freestanding" while EngineInfo.mountings
    # says "free-standing", so the filter must not be a blind membership test.
    free_standing = asyncio.run(
        resolve_submission(_planner_request(solver_mode="full_3d"), coupled)
    )
    assert free_standing.engine_name == "beat-cuda"

    # With a pre-coupling BEMPP nothing on the host can do it, and AUTO now
    # says so at submission instead of persisting a doomed job.
    uncoupled = registry.EngineRegistry(
        detector=lambda: gpu_host(coupled_bempp=False),
        factory=lambda _name: object(),
    )
    with pytest.raises(EngineUnavailableError, match="infinite-baffle"):
        asyncio.run(
            resolve_submission(
                _planner_request(solver_mode="full_3d", sim_type="infinite-baffle"),
                uncoupled,
            )
        )


def test_an_unavailable_stored_engine_falls_back_instead_of_refusing() -> None:
    """A stored selection must not be able to disable Solve on its own.

    The reported symptom was a permanently grey Solve button: an engine chosen
    on one machine, remembered, and then unavailable on the next one made
    ``/api/solve/plan`` answer 503 for the whole session. The frontend treats a
    503 as a refusal and deliberately never retries it, so nothing healed, and
    the only text explaining any of it lived in a ``title`` tooltip on a
    disabled button.

    The engine now falls back to what AUTO would have chosen, and says so.
    """

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("bempp", True, "CPU", "1"),
            registry.EngineInfo("beat", False, "No Julia executable was found.", None),
        ],
        factory=lambda name: object() if name == "bempp" else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(engine="beat", solver_mode="full_3d"), engine_registry
        )
    )

    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["solver_plan"]["engine"] == "bempp"
    assert resolution.symmetry_metadata["solver_plan"]["engine_substitution"] == {
        "requested": "beat",
        "resolved": "bempp",
        "reason": "No Julia executable was found.",
    }


def test_the_fallback_follows_auto_order_rather_than_any_available_engine() -> None:
    """The AUTO ordering is the contract, not whichever engine happens to be left."""

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("metal", True, "helper loadable", "1"),
            registry.EngineInfo("bempp", True, "CPU", "1"),
            registry.EngineInfo("beat", False, "no Julia", None),
        ],
        factory=lambda name: object() if name in {"metal", "bempp"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(engine="beat", solver_mode="full_3d"), engine_registry
        )
    )

    # metal outranks bempp in ("metal", "beat", "bempp", "dryrun").
    assert resolution.engine_name == "metal"


def test_the_fallback_respects_the_infinite_baffle_mounting_filter() -> None:
    """The substitute has to be able to run the request, not merely exist.

    BEAT advertises no coupled infinite-baffle mounting, so it must not be
    picked as a stand-in for an unavailable Metal on such a solve even though
    it ranks above BEMPP in the AUTO order.
    """

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("metal", False, "requires macOS", None),
            registry.EngineInfo(
                "beat", True, "GPU ready", "1", mountings=("free-standing",)
            ),
            registry.EngineInfo(
                "bempp",
                True,
                "CPU",
                "1",
                mountings=("free-standing", "infinite-baffle"),
            ),
        ],
        factory=lambda name: object() if name in {"beat", "bempp"} else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(
                engine="metal", solver_mode="full_3d", sim_type="infinite-baffle"
            ),
            engine_registry,
        )
    )

    assert resolution.engine_name == "bempp"
    substitution = resolution.symmetry_metadata["solver_plan"]["engine_substitution"]
    assert substitution["requested"] == "metal"


def test_a_substituted_engine_still_gets_its_own_mounting_treatment() -> None:
    """Falling back onto BEMPP must not skip BEMPP infinite-baffle handling.

    The substitution happens before the mounting rules rather than after, so a
    solve that lands on BEMPP by fallback is configured exactly like one that
    asked for it. Ordering is the whole content of this test.
    """

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("metal", False, "requires macOS", None),
            registry.EngineInfo(
                "bempp",
                True,
                "CPU",
                "1",
                mountings=("free-standing", "infinite-baffle"),
            ),
        ],
        factory=lambda name: object() if name == "bempp" else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(
                engine="metal", solver_mode="full_3d", sim_type="infinite-baffle"
            ),
            engine_registry,
        )
    )

    assert resolution.engine_name == "bempp"
    assert resolution.symmetry_metadata["resolved_quadrants"] == 1234
    assert resolution.symmetry_metadata["solver_plan"]["symmetry_reason"] == (
        "BEMPP coupled infinite-baffle uses the validated full-domain formulation"
    )


def test_dryrun_never_gets_a_real_engine_substituted_for_it() -> None:
    """The dry-run gate exists to stop real solves, so it must stay a refusal."""

    engine_registry = registry.EngineRegistry(
        detector=lambda: [registry.EngineInfo("bempp", True, "CPU", "1")],
        factory=lambda name: object() if name == "bempp" else None,
    )

    with pytest.raises(EngineUnavailableError) as caught:
        asyncio.run(
            resolve_submission(
                _planner_request(engine="dryrun", solver_mode="full_3d"),
                engine_registry,
            )
        )

    assert "WG2_ENABLE_DRYRUN=1" in str(caught.value)
    assert "bempp" not in str(caught.value)


def test_the_callers_request_engine_is_left_untouched() -> None:
    """The caller's own request object must survive the substitution.

    ``resolve_submission`` deep-copies before substituting. That is what lets
    the UI keep showing -- and keep persisting -- the engine the user actually
    chose, so it re-engages by itself once that engine can run here.
    """

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("bempp", True, "CPU", "1"),
            registry.EngineInfo("beat", False, "no Julia", None),
        ],
        factory=lambda name: object() if name == "bempp" else None,
    )
    request = _planner_request(engine="beat", solver_mode="full_3d")

    resolution = asyncio.run(resolve_submission(request, engine_registry))

    assert request.options.engine == "beat"
    assert resolution.request.options.engine == "bempp"


def test_the_plan_endpoint_reports_the_substitution_to_the_client(
    tmp_path: Path,
) -> None:
    """The swap has to reach the UI, or it is just a quieter silent failure."""

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("bempp", True, "CPU", "1"),
            registry.EngineInfo("beat", False, "No Julia executable was found.", None),
        ],
        factory=lambda name: object() if name == "bempp" else None,
    )
    runtime = JobRuntime(
        JobStore(tmp_path / "jobs.db"),
        engine_registry=engine_registry,
    )
    endpoint = next(
        route.endpoint
        for route in create_jobs_router(runtime).routes
        if getattr(route, "path", None) == "/api/solve/plan"
    )

    plan = asyncio.run(endpoint(_planner_request(engine="beat", solver_mode="full_3d")))

    assert plan.engine == "bempp"
    assert plan.engine_substitution is not None
    assert plan.engine_substitution.requested == "beat"
    assert plan.engine_substitution.resolved == "bempp"
    assert plan.engine_substitution.reason == "No Julia executable was found."


def test_an_available_engine_reports_no_substitution() -> None:
    """The field is absent on the ordinary path, so the UI shows no notice."""

    engine_registry = registry.EngineRegistry(
        detector=lambda: [registry.EngineInfo("bempp", True, "CPU", "1")],
        factory=lambda name: object() if name == "bempp" else None,
    )

    resolution = asyncio.run(
        resolve_submission(
            _planner_request(engine="bempp", solver_mode="full_3d"), engine_registry
        )
    )

    assert "engine_substitution" not in resolution.symmetry_metadata["solver_plan"]
