"""A damped (complex_k) infinite-baffle job never satisfies a real-k request.

The wire request does not carry the BEM formulation; the adapters derive it from
the mounting. So the same request bytes stood for a complex_k solve before the
infinite baffle moved to real k and stand for a real-k one now. The only place a
stored job can stand in for a new request is the submission key's replay, and it
compares a hash of the request. That hash now includes the formulation the
request will run with, for infinite-baffle requests only.

Restart recovery and retry do not reuse solved rows: partial results live in
memory for one run and a retry is a new run, so both execute under the current
formulation and record it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.engines import registry
from server.integration.provenance import canonical_json_sha256
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore, SubmissionConflictError
from server.solver.base import EngineRunResult


def _request(*, sim_type: str, key: str) -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "simulation": {"f1": 500, "f2": 8000, "num_frequencies": 3, "sim_type": sim_type},
            },
            "options": {"engine": "metal", "solver_mode": "full_3d", "accuracy": "fast"},
            "client_request_id": key,
        }
    )


class _FakeEngine:
    name = "metal"

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


def _runtime(tmp_path: Path, monkeypatch) -> tuple[JobRuntime, JobStore]:
    monkeypatch.setattr("server.jobs.runtime.get_engine", lambda name: _FakeEngine())
    metal = registry.EngineInfo(
        "metal", True, "ok", "1", mountings=("free-standing", "infinite-baffle")
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [metal], factory=lambda _n: _FakeEngine(), cpu_refresh=False
    )
    store = JobStore(tmp_path / "jobs.db")
    return JobRuntime(store, engine_registry=engine_registry), store


def _stored_hash(store: JobStore, key: str) -> str:
    with store._connection() as conn:  # noqa: SLF001 - the row is the thing under test
        return str(
            conn.execute(
                "SELECT request_sha256 FROM job_submissions WHERE submission_key = ?", (key,)
            ).fetchone()["request_sha256"]
        )


def _pretend_it_was_stored_by_the_complex_k_build(store: JobStore, key: str, request) -> None:
    """Rewrite the row to the hash the previous build stored: the plain request."""

    # Today's model dump includes new defaults that the complex-k build never
    # serialized. Keep this independent of the production identity function.
    legacy_wire = request.model_dump(mode="json")
    legacy_wire["options"].pop("adaptive_frequency_sampling", None)
    legacy = canonical_json_sha256(legacy_wire)
    with store._transaction() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE job_submissions SET request_sha256 = ? WHERE submission_key = ?",
            (legacy, key),
        )


def test_an_old_complex_k_infinite_baffle_job_is_not_replayed_for_a_real_k_request(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, store = _runtime(tmp_path, monkeypatch)
    request = _request(sim_type="infinite-baffle", key="ib-1")

    async def scenario() -> None:
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        assert store.get_job_row(job_id)["status"] == "complete"
        # Same key, same request: a genuine transport replay still returns the job.
        assert await runtime.submit(request) == job_id

        _pretend_it_was_stored_by_the_complex_k_build(store, "ib-1", request)
        before = store.list_jobs()[1]
        with pytest.raises(SubmissionConflictError, match="different request"):
            await runtime.submit(request)
        assert store.list_jobs()[1] == before
        await runtime.shutdown()

    asyncio.run(scenario())


def test_the_identity_of_other_mountings_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    """A free-standing replay keeps working across the change."""

    runtime, store = _runtime(tmp_path, monkeypatch)
    request = _request(sim_type="freestanding", key="fs-1")

    async def scenario() -> None:
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        assert store.get_job_row(job_id)["status"] == "complete"
        _pretend_it_was_stored_by_the_complex_k_build(store, "fs-1", request)
        assert await runtime.submit(request) == job_id
        await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("explicit_off", [False, True])
def test_default_off_identity_matches_base_bytes(explicit_off: bool) -> None:
    from server.integration.provenance import _canonical_json
    from server.jobs.runtime import _submission_identity

    request = _request(sim_type="freestanding", key="fs-1")
    if explicit_off:
        request.options.adaptive_frequency_sampling = False
    # Canonical bytes produced by 44226173's models and identity function.
    legacy = (Path(__file__).parent / "fixtures" / "fs-1-legacy-identity.json").read_bytes()
    assert _canonical_json(_submission_identity(request)) == legacy


def test_the_infinite_baffle_identity_names_the_real_k_formulation() -> None:
    from server.jobs.runtime import _submission_identity

    ib = _submission_identity(_request(sim_type="infinite-baffle", key="k"))
    assert ib["effective_bem_formulation"] == {"formulation": "standard", "complex_k_shift": 0.0}
    fs = _submission_identity(_request(sim_type="freestanding", key="k"))
    assert "effective_bem_formulation" not in fs
