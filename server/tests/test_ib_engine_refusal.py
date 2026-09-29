"""An engine that cannot solve a coupled infinite baffle is refused up front.

The mounting was gated only for AUTO and for substitution, so an explicit
engine (a stored ``beat-*`` preference, an old client sending Fast, an imported
ATH ``SimType = 1``, a retry) was accepted, persisted, and only then failed in
the adapter. The refusal now sits where the ground-plane one does: before the
job exists, naming only the engines that can do it.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from server.design import textcfg
from server.engines import registry
from server.jobs.models import SolveRequest
from server.jobs.runtime import (
    EngineUnavailableError,
    JobRuntime,
    SymmetryValidationError,
    resolve_submission,
)
from server.jobs.store import JobStore
from server.solver.base import EngineRunResult


def _request(*, engine: str, sim_type: str = "infinite-baffle", accuracy: str = "fast") -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "simulation": {"f1": 500, "f2": 8000, "num_frequencies": 3, "sim_type": sim_type},
            },
            "options": {"engine": engine, "solver_mode": "full_3d", "accuracy": accuracy},
        }
    )


def _host(*, bempp_coupled: bool, beat: bool = True, metal: bool = True) -> list[registry.EngineInfo]:
    ib = ("free-standing", "infinite-baffle")
    fs = ("free-standing",)
    rows = []
    if metal:
        rows.append(registry.EngineInfo("metal", True, "ok", "1", mountings=ib))
    rows.append(registry.EngineInfo("bempp", True, "ok", "1", mountings=ib if bempp_coupled else fs))
    if beat:
        rows.append(registry.EngineInfo("beat-cpu", True, "ok", "1", mountings=fs))
        rows.append(registry.EngineInfo("beat-metal", True, "ok", "1", mountings=fs))
    return rows


def _registry(rows) -> registry.EngineRegistry:
    return registry.EngineRegistry(detector=lambda: list(rows), factory=lambda _n: object())


def _refusal_text(request: SolveRequest, engine_registry) -> str:
    with pytest.raises((SymmetryValidationError, EngineUnavailableError)) as caught:
        asyncio.run(resolve_submission(request, engine_registry))
    return str(caught.value)


def _assert_names_only_capable_engines(text: str) -> None:
    unquoted = re.sub(r"'[^']*'", "", text).lower()
    assert "Metal" in text and "BEMPP" in text
    assert "beat" not in unquoted
    assert "axisym" not in unquoted


@pytest.mark.parametrize("engine", ["beat-cpu", "beat-metal"])
def test_explicit_beat_with_fast_and_infinite_baffle_is_refused(engine: str) -> None:
    text = _refusal_text(_request(engine=engine), _registry(_host(bempp_coupled=True)))
    assert f"'{engine}' cannot solve a coupled infinite baffle" in text
    _assert_names_only_capable_engines(text)


def test_explicit_uncoupled_bempp_with_infinite_baffle_is_refused() -> None:
    text = _refusal_text(_request(engine="bempp"), _registry(_host(bempp_coupled=False)))
    assert "'bempp' cannot solve a coupled infinite baffle" in text
    _assert_names_only_capable_engines(text)


def test_explicit_capable_engines_and_free_standing_are_unchanged() -> None:
    engine_registry = _registry(_host(bempp_coupled=True))
    assert asyncio.run(resolve_submission(_request(engine="metal"), engine_registry)).engine_name == "metal"
    assert asyncio.run(resolve_submission(_request(engine="bempp"), engine_registry)).engine_name == "bempp"
    # The same explicit BEAT engine is fine when the design is free-standing.
    free = asyncio.run(
        resolve_submission(_request(engine="beat-cpu", sim_type="freestanding"), engine_registry)
    )
    assert free.engine_name == "beat-cpu"


def test_auto_still_resolves_a_capable_engine_for_infinite_baffle() -> None:
    resolved = asyncio.run(
        resolve_submission(_request(engine="auto"), _registry(_host(bempp_coupled=True, metal=False)))
    )
    assert resolved.engine_name == "bempp"


def test_auto_and_substitution_remedies_name_only_capable_engines() -> None:
    # AUTO with nothing able to run it.
    text = _refusal_text(_request(engine="auto"), _registry(_host(bempp_coupled=False, metal=False)))
    assert "coupled infinite-baffle" in text
    _assert_names_only_capable_engines(text)

    # A stored engine that is unavailable here, with no capable stand-in.
    rows = [
        registry.EngineInfo("beat-cpu", False, "no Julia", None, mountings=("free-standing",)),
        registry.EngineInfo("bempp", True, "ok", "1", mountings=("free-standing",)),
    ]
    engine_registry = registry.EngineRegistry(
        detector=lambda: rows, factory=lambda name: object() if name == "bempp" else None
    )
    text = _refusal_text(_request(engine="beat-cpu"), engine_registry)
    _assert_names_only_capable_engines(text)


def test_accurate_refusal_is_unchanged() -> None:
    with pytest.raises(SymmetryValidationError, match="Accurate via BEAT cannot solve a coupled infinite baffle"):
        asyncio.run(
            resolve_submission(
                _request(engine="auto", accuracy="accurate"), _registry(_host(bempp_coupled=True))
            )
        )


def test_imported_ath_simtype_one_with_a_stored_beat_preference_is_refused() -> None:
    parsed = textcfg.parse("Simulation.SimType = 1\nCoverage.Angle = 45\n")
    assert parsed.design.root.simulation.sim_type == "infinite-baffle"
    request = SolveRequest.model_validate(
        {
            "design": parsed.design.model_dump(mode="json"),
            "options": {"engine": "beat-cpu", "solver_mode": "full_3d"},
        }
    )
    text = _refusal_text(request, _registry(_host(bempp_coupled=True)))
    assert "'beat-cpu' cannot solve a coupled infinite baffle" in text


def test_submit_refuses_before_persisting_a_job(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    runtime = JobRuntime(store, engine_registry=_registry(_host(bempp_coupled=True)))

    async def scenario() -> None:
        with pytest.raises(SymmetryValidationError):
            await runtime.submit(_request(engine="beat-cpu"))
        rows, total = store.list_jobs()
        assert total == 0 and rows == []
        await runtime.shutdown()

    asyncio.run(scenario())


def test_retry_of_a_stored_job_is_refused_when_the_engine_cannot_do_it(
    tmp_path: Path, monkeypatch
) -> None:
    """A restored job goes back through the same boundary as a new one."""

    class FakeEngine:
        name = "beat-cpu"

        async def run(self, request, *, cancel_cb, stage_cb):
            return EngineRunResult(
                results={
                    "frequencies": [500.0],
                    "directivity": {},
                    "spl_on_axis": {"frequencies": [500.0], "spl": [90.0], "phase_degrees": [0.0]},
                    "impedance": {"frequencies": [500.0], "real": [1.0], "imaginary": [0.0]},
                    "di": {"frequencies": [500.0], "di": {}},
                    "metadata": {"engine": "fake"},
                },
                msh_text="$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                mesh_stats={"vertex_count": 3, "triangle_count": 1},
            )

    monkeypatch.setattr("server.jobs.runtime.get_engine", lambda name: FakeEngine())
    beat_ib = registry.EngineInfo(
        "beat-cpu", True, "ok", "1", mountings=("free-standing", "infinite-baffle")
    )
    beat_fs = registry.EngineInfo("beat-cpu", True, "ok", "1", mountings=("free-standing",))
    rows = [beat_ib]
    engine_registry = registry.EngineRegistry(
        detector=lambda: list(rows), factory=lambda _n: object(), cpu_refresh=False
    )
    store = JobStore(tmp_path / "jobs.db")
    runtime = JobRuntime(store, engine_registry=engine_registry)

    async def scenario() -> None:
        job_id = await runtime.submit(_request(engine="beat-cpu"))
        await runtime.wait_idle()
        before = store.list_jobs()[1]
        rows[:] = [beat_fs]
        engine_registry._cache = None
        with pytest.raises(SymmetryValidationError, match="cannot solve a coupled infinite baffle"):
            await runtime.retry(job_id)
        assert store.list_jobs()[1] == before
        await runtime.shutdown()

    asyncio.run(scenario())


def test_the_registry_advertises_infinite_baffle_from_one_source(monkeypatch) -> None:
    """Metal by name, BEMPP from its probe, BEAT never: the gate reads exactly this."""

    from server.solver import beat as beat_module
    from server.solver import bempp as bempp_module
    from server.solver import metal as metal_module

    ok = {"available": True, "reason": "stub", "version": "t"}
    monkeypatch.setattr(
        beat_module,
        "beat_backend_statuses",
        lambda: {b: {**ok, "backend": b, "surface_traces": False} for b in beat_module.BEAT_BACKENDS},
    )
    monkeypatch.setattr(metal_module, "metal_status", lambda: dict(ok))
    for coupled in (True, False):
        monkeypatch.setattr(
            bempp_module, "bempp_status", lambda coupled=coupled: {**ok, "coupled_infinite_baffle": coupled}
        )
        capable = {
            info.name for info in registry.detect_engines(environ={}) if "infinite-baffle" in info.mountings
        }
        assert capable == ({"metal", "bempp"} if coupled else {"metal"})


def test_a_non_planar_mouth_refusal_from_the_mesher_is_actionable() -> None:
    from server.mesh.builder import _infinite_baffle_geometry_refusal

    detail = (
        "infinite-baffle coupled aperture mesh requires a planar mouth ring; "
        "mouth z spans 3.2 mm"
    )
    text = _infinite_baffle_geometry_refusal({"mode": "infinite-baffle"}, detail)
    assert text is not None
    assert "planar" in text and "free-standing" in text and "3.2 mm" in text
    # Only the infinite-baffle mode, and only this refusal, is rewritten.
    assert _infinite_baffle_geometry_refusal({"mode": "freestanding"}, detail) is None
    assert _infinite_baffle_geometry_refusal({"mode": "infinite-baffle"}, "boom") is None
