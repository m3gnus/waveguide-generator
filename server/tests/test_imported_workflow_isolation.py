"""A live worker warmed without a session must not enter numerical acceptance."""

import asyncio
import os

import pytest

from server.platform import temp_session
from server.solver import bempp_process
from server.solver.context import SolverContext
import test_imported_workflow_contract as workflow
from test_imported_workflow_contract import isolated_import_bempp_host as isolated_import_bempp_host
import test_real_pipeline as pipeline


def _session_reporting_worker(connection):
    """Exercise real spawning/session adoption without a native numerical solve."""
    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            job_id, _ = command
            event = "warm" if job_id == bempp_process._WARMUP_JOB_ID else "done"
            connection.send((event, job_id, {
                "worker_pid": os.getpid(), "session_root": temp_session.spawned_directory_root(),
            }))
    finally:
        connection.close()


def _worker_session(host):
    return asyncio.run(host.run(
        "unused", SolverContext(design=None, frequency_range=(500, 1000), num_frequencies=2),
        mesh_metadata={}, mesh_stats={}, cancel_cb=lambda: None, stage_cb=lambda *_: None,
        result_cb=None,
    ))


@pytest.fixture
def sessionless_host(monkeypatch):
    host = bempp_process.BemppProcessHost(target=_session_reporting_worker)
    monkeypatch.setattr(bempp_process, "_HOST", host)
    try:
        with monkeypatch.context() as no_session:
            no_session.setattr(temp_session, "_active_root", None)
            no_session.setattr(temp_session, "_parent_root", None)
            host.prewarm()
            stale_process = host._process
            assert stale_process is not None and stale_process.is_alive()
            assert _worker_session(host)["session_root"] is None
        yield host, stale_process
    finally:
        host.close()


@pytest.fixture
def real_pipeline_session(sessionless_host, monkeypatch):
    # Warm the stale host BEFORE starting the same real session fixture the
    # numerical acceptance uses. Pytest runs the imported isolation fixture
    # after this dependency and tears it down before this directory is removed.
    session_fixture = pipeline.real_pipeline_session.__wrapped__(monkeypatch)
    session = next(session_fixture)
    host, _ = sessionless_host
    try:
        yield session
    finally:
        try:
            assert host._process is None, "Worker must close before its session is removed"
            assert session.path.is_dir()
        finally:
            session_fixture.close()
        assert not session.path.exists()


@pytest.mark.usefixtures("isolated_import_bempp_host")
def test_acceptance_fixture_replaces_a_live_sessionless_host_and_closes_its_replacement(
    sessionless_host, real_pipeline_session,
):
    host, stale_process = sessionless_host
    assert not stale_process.is_alive()
    assert host._process is None

    host.prewarm()
    replacement = host._process
    assert replacement is not None and replacement.is_alive()
    result = _worker_session(host)
    assert result["worker_pid"] != stale_process.pid
    assert result["session_root"] == str(real_pipeline_session.path)

    # Cover the acceptance's opt-in as well: a correct helper left unused by
    # the numerical test would let the original order-dependent failure return.
    acceptance = workflow.test_equivalent_cad_and_file_meshes_use_same_engine_and_agree
    assert any(mark.name == "usefixtures" and "isolated_import_bempp_host" in mark.args
               for mark in acceptance.pytestmark)
