"""The policy is identical at both submission boundaries and at HTTP/CLI edges."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.models import SolveRequest
from server.jobs.runtime import EngineUnavailableError, ImportedSolveRefusal, resolve_submission
from server.jobs.runtime.core import resolve_imported_submission
from server.jobs.runtime.engine_policy import SolveBlocker


def info(name, state="ready", **kwargs):
    return EngineInfo(
        name,
        state == "ready",
        "Preparation disabled by installation settings"
        if state == "disabled"
        else "No device"
        if state == "unavailable"
        else state,
        "1",
        label={"beat-metal": "BEAT Metal", "beat-cpu": "BEAT CPU", "metal": "Metal-BEM"}.get(
            name, name
        ),
        geometry_sources=("parametric", "imported"),
        mountings=("free-standing",),
        symmetry_domains=("full", "half", "quarter"),
        runtime_state="unprepared" if state == "disabled" else state,
        runtime_preparable=state in {"unprepared", "failed"},
        **kwargs,
    )


def request(kind, engine="beat-metal", accuracy="fast"):
    data = {
        "design": {"formula": "OSSE", "L": 120, "a": 45},
        "options": {"engine": engine, "accuracy": accuracy, "symmetry": "full"},
    }
    if kind == "imported":
        del data["design"]
        data["options"]["frequencies_hz"] = [500, 1000]
        data["geometry"] = {
            "type": "imported",
            "ingest_id": "wgi_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "manifest_sha256": "sha256:" + "a" * 64,
            "artifact_sha256": "sha256:" + "b" * 64,
            "drive_channels": [{"id": "drive", "source_ids": ["source"]}],
        }
    return SolveRequest.model_validate(data)


def registry(*rows):
    return EngineRegistry(detector=lambda: rows, factory=lambda name: SimpleNamespace(name=name))


async def resolve(kind, req, engines):
    if kind == "parametric":
        return await resolve_submission(req, engines)
    return await resolve_imported_submission(
        req, engines, symmetry_metadata={"resolved_quadrants": 1234}, request_refusal=None
    )


@pytest.mark.parametrize("kind", ["parametric", "imported"])
@pytest.mark.parametrize(
    "state", ["ready", "unprepared", "preparing", "disabled", "unavailable", "failed"]
)
def test_explicit_engine_states(kind, state):
    req = request(kind)
    engines = registry(info("beat-metal", state), info("metal"), info("beat-cpu"))
    if state == "ready":
        result = asyncio.run(resolve(kind, req, engines))
        assert result.engine_name == "beat-metal"
        return
    with pytest.raises(EngineUnavailableError) as caught:
        asyncio.run(resolve(kind, req, engines))
    blocker = caught.value.blocker
    assert blocker.engine == "beat-metal"
    assert req.options.engine == "beat-metal"
    assert caught.value.details["blocker"] == blocker.model_dump(mode="json")
    from server.jobs.model_solve import refusal_from
    model_refusal = refusal_from(caught.value)
    assert model_refusal.details == caught.value.details
    assert model_refusal.status == 503
    alternatives = [r for r in blocker.remedies if r.kind == "use_engine"]
    assert [r.engine for r in alternatives] == ["beat-cpu", "metal"]
    assert alternatives[0].same_solver and not alternatives[1].same_solver
    assert alternatives[1].label == "Use Metal-BEM"
    assert all(remedy.label not in blocker.message for remedy in alternatives)
    if state == "preparing":
        assert blocker.message == "BEAT Metal is getting ready."
    prepare = next((r for r in blocker.remedies if r.kind == "prepare"), None)
    assert bool(prepare) == (state in {"unprepared", "disabled", "failed"})
    if prepare:
        assert bool(prepare.disabled_reason) == (state == "disabled")
    assert any(r.kind == "wait" for r in blocker.remedies) == (state == "preparing")
    assert any(r.kind == "settings" for r in blocker.remedies)


@pytest.mark.parametrize("kind", ["parametric", "imported"])
@pytest.mark.parametrize("accuracy,expected", [("fast", "metal"), ("accurate", "beat-cpu")])
def test_auto_chooses_ready_engine_and_reports_it(kind, accuracy, expected):
    req = request(kind, "auto", accuracy)
    result = asyncio.run(
        resolve(
            kind, req, registry(info("beat-metal", "unprepared"), info("metal"), info("beat-cpu"))
        )
    )
    assert result.engine_name == result.request.options.engine == expected
    assert result.symmetry_metadata["solver_plan"]["engine"] == expected
    assert result.symmetry_metadata["solver_plan"]["requested"] == "auto"
    assert req.options.engine == "auto"


@pytest.mark.parametrize("kind", ["parametric", "imported"])
def test_unsupported_features_offer_only_engines_that_pass_the_verdict(kind):
    req = request(kind)
    rows = [info("beat-metal"), info("metal"), info("beat-cpu")]
    if kind == "parametric":
        req.options.ground_plane.enabled = True
        rows[-1] = replace(rows[-1], mountings=("free-standing", "ground-plane"))
        error = ValueError
    else:
        req.geometry.passive_cardioid_rear_volume_l = 10
        rows[-1] = replace(rows[-1], imported_features=("passive-cardioid",))
        error = ImportedSolveRefusal
    with pytest.raises(error) as caught:
        asyncio.run(resolve(kind, req, registry(*rows)))
    blocker = SolveBlocker.model_validate(caught.value.details["blocker"])
    assert [r.engine for r in blocker.remedies if r.kind == "use_engine"] == ["beat-cpu"]


def test_driver_model_never_offers_metal_bem():
    from server.jobs.runtime.engine_policy import EngineVerdict, select_engine
    from server.jobs.runtime.imported_refusals import _imported_capability_blocker

    rows = [
        info("beat-metal", "unprepared", imported_features=("exterior-transducers",)),
        info("metal", imported_features=("passive-cardioid",)),
        info("beat-cpu", imported_features=("exterior-transducers",)),
    ]

    async def verdict(candidate):
        problem = _imported_capability_blocker(candidate, 1234, {"exterior-transducers"})
        if problem:
            return EngineVerdict(False, "capability", *problem)
        return EngineVerdict(
            candidate.available, "availability" if not candidate.available else None
        )

    blocker = asyncio.run(select_engine("beat-metal", "fast", rows, verdict))
    assert isinstance(blocker, SolveBlocker)
    assert [r.engine for r in blocker.remedies if r.kind == "use_engine"] == ["beat-cpu"]


def test_parametric_explicit_unready_beat_metal_refused_without_substitution():
    req = request("parametric")
    engines = registry(info("beat-metal", "unprepared"), info("metal"))
    with pytest.raises(EngineUnavailableError) as caught:
        asyncio.run(resolve_submission(req, engines))
    assert caught.value.blocker.engine == "beat-metal"
    preview = asyncio.run(resolve_submission(req, engines, preview=True))
    plan = preview.symmetry_metadata["solver_plan"]
    assert "engine_substitution" not in plan
    assert plan["readiness"] == caught.value.details["blocker"]
    assert preview.engine_name == req.options.engine == "beat-metal"


def test_plan_and_solve_report_identical_blocker_and_no_job(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.jobs.api import create_jobs_router
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    engines = registry(info("beat-metal", "unprepared"), info("beat-cpu"), info("metal"))
    store = JobStore(tmp_path / "jobs.db")
    runtime = JobRuntime(store, engine_registry=engines)
    app = FastAPI()
    app.include_router(create_jobs_router(runtime))
    body = request("parametric").model_dump(mode="json")
    with TestClient(app) as client:
        plan = client.post("/api/solve/plan", json=body)
        refused = client.post("/api/solve", json=body)
        assert store.list_jobs()[1] == 0
    assert plan.status_code == 200
    assert refused.status_code == 503
    assert plan.json()["readiness"] == refused.json()["error"]["details"]["blocker"]
    assert plan.json()["engine"] == "beat-metal"
    assert plan.json()["engine_substitution"] is None


@pytest.mark.parametrize("kind", ["parametric", "imported"])
def test_auto_blocker_offers_preparable_cpu_instead_of_absent_metal(kind):
    req = request(kind, "auto")
    engines = registry(info("metal", "unavailable"), info("beat-cpu", "unprepared"))
    with pytest.raises(EngineUnavailableError) as caught:
        asyncio.run(resolve(kind, req, engines))
    blocker = caught.value.blocker
    assert blocker.engine == "beat-cpu"
    assert blocker.remedies[0].kind == "prepare"
    assert blocker.remedies[0].disabled_reason is None


@pytest.mark.parametrize("native", [False, True])
def test_bempp_remedy_checks_its_full_geometry_admission(native):
    from server.tests.test_native_formulas import design_payload
    from server.jobs.runtime import SymmetryValidationError

    if native:
        req = SolveRequest.model_validate(
            {
                "design": design_payload("OSSE-AXIAL"),
                "options": {"engine": "beat-metal", "symmetry": "full"},
            }
        )
    else:
        req = request("parametric")
        req.design.root.simulation.sim_type = "infinite-baffle"
        req.options.symmetry = "quarter"
    metal = replace(
        info("beat-metal", "unavailable"), mountings=("free-standing", "infinite-baffle")
    )
    bempp = replace(info("bempp"), mountings=("free-standing", "infinite-baffle"))
    with pytest.raises((EngineUnavailableError, SymmetryValidationError)) as caught:
        asyncio.run(resolve_submission(req, registry(metal, bempp)))
    assert not any(
        r["kind"] == "use_engine" and r["engine"] == "bempp"
        for r in caught.value.details["blocker"]["remedies"]
    )



def test_invalid_geometry_precedes_engine_readiness_in_plan_and_solve(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.jobs.api import create_jobs_router
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    req = request("parametric", "auto", "accurate")
    req.options.symmetry = "quarter"
    req.options.ground_plane.enabled = True
    runtime = JobRuntime(JobStore(tmp_path / "jobs.db"), engine_registry=registry())
    app = FastAPI()
    app.include_router(create_jobs_router(runtime))
    with TestClient(app) as client:
        planned = client.post("/api/solve/plan", json=req.model_dump(mode="json"))
        submitted = client.post("/api/solve", json=req.model_dump(mode="json"))
    assert planned.status_code == submitted.status_code == 422
    assert planned.json()["detail"] == submitted.json()["detail"]
    assert "Forced symmetry mode 'quarter' is invalid" in planned.json()["detail"]


@pytest.mark.parametrize("case", ["unknown", "removed", "symmetry", "mounting", "unavailable", "accurate-auto"])
def test_parametric_plan_reports_every_submission_refusal(tmp_path, case):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.jobs.api import create_jobs_router
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    req = request("parametric")
    rows = [info("beat-metal")]
    if case == "unknown":
        req.options.engine = "unknown"
    elif case == "removed":
        req.options.engine = "axisym"
    elif case == "symmetry":
        req.options.symmetry = "quarter"
        req.options.ground_plane.enabled = True
    elif case == "mounting":
        req.design.root.simulation.sim_type = "infinite-baffle"
    elif case == "unavailable":
        rows = [info("beat-metal", "disabled")]
    else:
        req.options.engine, req.options.accuracy = "auto", "accurate"
        rows = [info("metal")]
    store = JobStore(tmp_path / "jobs.db")
    runtime = JobRuntime(store, engine_registry=registry(*rows))
    app = FastAPI()
    app.include_router(create_jobs_router(runtime))
    with TestClient(app) as client:
        body = req.model_dump(mode="json")
        planned = client.post("/api/solve/plan", json=body)
        submitted = client.post("/api/solve", json=body)
        assert submitted.status_code in {422, 503}
        if planned.status_code == 200:
            assert planned.json()["readiness"] == submitted.json()["error"]["details"]["blocker"]
        else:
            assert planned.json()["detail"] == submitted.json()["detail"]
        assert store.list_jobs()[1] == 0
