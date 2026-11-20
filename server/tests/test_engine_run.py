"""The shared engine-run template: call order and the artifact hand-off."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from server.mesh.artifact import ImportedMeshArtifactError, mesh_text_sha256
from server.solver import engine_run
from server.solver.base import (
    CHANNEL_BASES_KEY,
    FIELD_TRACE_UNAVAILABLE_REASON_KEY,
    FIELD_TRACES_KEY,
    RADIATION_IMPEDANCE_KEY,
    EngineRunResult,
)


def test_run_result_takes_every_artifact_out_of_the_solve_results() -> None:
    results = {
        "metadata": {"a": 1},
        CHANNEL_BASES_KEY: b"bases",
        RADIATION_IMPEDANCE_KEY: b"matrix",
        FIELD_TRACES_KEY: "traces",
        FIELD_TRACE_UNAVAILABLE_REASON_KEY: "why",
    }
    run = EngineRunResult.from_solve_results(results, msh_text="mesh", mesh_stats={"n": 1})
    assert run.results is results
    assert results == {"metadata": {"a": 1}}
    assert (run.channel_bases, run.radiation_impedance) == (b"bases", b"matrix")
    assert (run.field_traces, run.field_trace_unavailable_reason) == ("traces", "why")
    assert (run.msh_text, run.mesh_stats) == ("mesh", {"n": 1})


def test_absent_artifacts_stay_none() -> None:
    run = EngineRunResult.from_solve_results({"metadata": {}}, msh_text="m", mesh_stats={})
    assert run.channel_bases is None and run.radiation_impedance is None
    assert run.field_traces is None and run.field_trace_unavailable_reason is None


def test_parametric_run_stores_the_mesh_before_it_solves(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_mesh(design, options, cancel_cb, stage_cb):
        calls.append("mesh")
        return {"msh_text": "mesh", "stats": {"triangles": 2}, "metadata": {}}

    async def artifact_cb(msh_text: str, stats: dict[str, Any]) -> None:
        calls.append(f"artifact:{msh_text}")

    async def solve(mesh):
        calls.append("solve")
        return {"metadata": {"solve_path": "kept-first"}, FIELD_TRACE_UNAVAILABLE_REASON_KEY: "none"}

    def annotate(metadata: dict[str, Any]) -> None:
        calls.append("annotate")
        metadata["solve_path"] = "full-3d"
        metadata["extra"] = True

    monkeypatch.setattr(engine_run, "build_solver_mesh", fake_mesh)
    request = SimpleNamespace(design=None, options=None)
    outcome = asyncio.run(engine_run.run_parametric_engine(
        request, cancel_cb=lambda: calls.append("cancel"), stage_cb=lambda *a: None,
        artifact_cb=artifact_cb, solve=solve, annotate=annotate,
    ))

    assert calls == ["mesh", "artifact:mesh", "cancel", "solve", "annotate"]
    assert list(outcome.results["metadata"]) == ["solve_path", "mesh_stats", "extra"]
    assert outcome.results["metadata"]["solve_path"] == "full-3d"
    assert outcome.field_trace_unavailable_reason == "none"
    assert (outcome.msh_text, outcome.mesh_stats) == ("mesh", {"triangles": 2})


def test_imported_run_solves_the_verified_execution_text() -> None:
    text = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
    record = {
        "_execution_msh_text": text,
        "mesh": {"stats": {"triangles": 3}},
        "mesh_content_sha256": mesh_text_sha256(text),
    }
    seen: list[str] = []

    async def solve(msh_text: str):
        seen.append(msh_text)
        return {CHANNEL_BASES_KEY: b"npz"}

    outcome = asyncio.run(engine_run.run_imported_engine(
        record, cancel_cb=lambda: None, artifact_cb=None, solve=solve,
    ))
    assert seen == [text]
    assert outcome.results == {"metadata": {"mesh_stats": {"triangles": 3}}}
    assert outcome.channel_bases == b"npz"
    assert outcome.mesh_stats == {"triangles": 3}


def test_imported_run_refuses_text_that_is_not_the_record_mesh() -> None:
    record = {"_execution_msh_text": "tampered", "mesh_content_sha256": mesh_text_sha256("original")}

    async def solve(msh_text: str):
        raise AssertionError("a mismatched mesh must never be solved")

    with pytest.raises(ImportedMeshArtifactError):
        asyncio.run(engine_run.run_imported_engine(
            record, cancel_cb=lambda: None, artifact_cb=None, solve=solve,
        ))
