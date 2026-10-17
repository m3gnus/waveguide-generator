"""The restart latch an approved update handoff raises (contract §4.2, §4.3).

From the moment the handoff request is written until the launcher restarts or
calls it off, routes that start installation-owned work refuse, queued jobs
wait, and the latch comes down on its own if no handoff follows.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import textwrap
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from launch import serve
from launch.serve_options import UPDATE_RELEASED_FILENAME
from launchers.statusapp import controller as controller_module
from launchers.statusapp.controller import StatusController
from server.app import create_app
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.models import SolveRequest
from server.jobs.runtime import (
    QUIT_INTERRUPTED_MESSAGE,
    QUIT_INTERRUPTED_STAGE_MESSAGE,
    RESTART_RECOVERY_MESSAGE,
    JobRuntime,
)
from server.jobs.store import JobStore
from server.solver.base import EngineRunResult
from server.tests._asgi import SOLVE_BODY, _post
from server.updates.restart import RestartApproval


_FAKE_SERVER = textwrap.dedent(
    r'''
    """Answer the status controller the way launch/serve.py does, and no more."""

    import argparse
    from pathlib import Path
    import time

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--status-control", type=Path, required=True)
    args, _unknown = parser.parse_known_args()
    args.status_control.with_name("ready.json").write_text(
        '{"host":"127.0.0.1","port":%d}\n' % args.port, encoding="utf-8"
    )
    print("fake server ready", flush=True)
    while not args.status_control.is_file():
        time.sleep(0.02)
    '''
)


def _with_interface(app_layer: Path) -> None:
    """The built interface every launch mode checks for before it starts."""

    dist = app_layer / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")


def test_a_solve_submitted_after_restart_approval_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A solve that started after approval would be ended by a restart it never heard of.

    Approval is the moment the handoff request is written (contract §4.1), and
    the restart latch goes up with it (§4.2).
    """

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> tuple[int, bytes]:
        app = create_app(data_dir=tmp_path / "data")
        app.state.update_restart.approve("v2.0.1")
        runtime = app.state.jobs_runtime
        try:
            return await _post(app, "/api/solve", SOLVE_BODY)
        finally:
            await runtime.wait_idle()
            await runtime.shutdown()

    status, raw = asyncio.run(scenario())

    assert status == 409, f"a solve was accepted (HTTP {status}) after the restart was approved: {raw[:200]!r}"
    assert json.loads(raw).get("error", {}).get("code") == "update_restart_pending"


def test_a_request_the_launcher_discards_releases_the_servers_latch(tmp_path: Path) -> None:
    """Contract §4.2: a server must never stay latched with no handoff pending.

    The launcher deletes a request it cannot trust instead of handing off, and
    the same server keeps running. It says so over the status control channel,
    and the server's own watcher clears the latch.
    """

    app_layer = tmp_path / "app"
    _with_interface(app_layer)
    fake_server = tmp_path / "fake_server.py"
    fake_server.write_text(_FAKE_SERVER, encoding="utf-8")
    controller = StatusController(
        repo_root=app_layer,
        server_command=(sys.executable, str(fake_server)),
        server_args=("--data-dir", str(tmp_path / "data")),
        environ=dict(os.environ),
        request_timeout=0.2,
        shutdown_timeout=1.0,
    )
    latch = RestartApproval()
    latch.approve("v2.0.1")
    server_app = SimpleNamespace(state=SimpleNamespace(update_restart=latch))
    stop = threading.Event()
    try:
        controller.start()
        request = controller.update_request_path
        if request is None:
            pytest.fail("set-up: the controller started with no status control directory")
        request.write_text("{not a request", encoding="utf-8")

        assert controller.take_update_request() is None
        notice = request.with_name(UPDATE_RELEASED_FILENAME)
        assert notice.is_file(), "the launcher discarded the request and told the server nothing"

        watcher = threading.Thread(
            target=serve._watch_statusapp,
            args=(SimpleNamespace(should_exit=False), request.with_name("stop"), None, stop),
            kwargs={
                "on_update_released": lambda reason: serve._release_update_restart(
                    server_app, reason
                )
            },
            daemon=True,
        )
        watcher.start()
        deadline = time.monotonic() + 10.0
        while latch.pending is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        stop.set()
        watcher.join(timeout=2.0)
    finally:
        stop.set()
        controller.close()

    assert latch.pending is None, "the server stayed latched for a restart nobody will start"
    assert not notice.exists(), "the release notice was left to release again"


def test_a_retry_after_restart_approval_is_refused_while_reads_stay_open(
    tmp_path: Path,
) -> None:
    """Contract §4.2: every route that starts installation-owned work refuses alike."""

    async def scenario() -> list[tuple[int, bytes]]:
        app = create_app(data_dir=tmp_path / "data")
        app.state.update_restart.approve("v2.0.1")
        runtime = app.state.jobs_runtime
        try:
            return [
                await _post(app, "/api/jobs/any-job/retry", {}),
                await _post(
                    app, "/api/updates/install", {}, headers=((b"x-wg-update", b"install"),)
                ),
                await _post(app, "/api/jobs", None, method="GET"),
            ]
        finally:
            await runtime.shutdown()

    (retry, retry_raw), (install, install_raw), (listing, _listing_raw) = asyncio.run(
        scenario()
    )

    assert retry == 409, f"a retry started after the restart was approved: {retry_raw[:200]!r}"
    error = json.loads(retry_raw)["error"]
    assert error["code"] == "update_restart_pending"
    assert error["stage"] == "submission"
    assert error["retryable"] is True
    assert "restart" in error["message"] and "v2.0.1" in error["message"]
    assert install == 409
    assert json.loads(install_raw)["error"]["code"] == "update_restart_pending"
    assert listing == 200, "a read route closed while a restart was pending"


def test_a_release_notice_that_cannot_be_written_is_logged_and_the_server_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract §4.2: a notice that cannot be written is retried, then logged.

    The server is not restarted for it: that would end running solves for a
    restart that installs nothing. Its latch expires on its own instead
    (``test_an_approved_restart_expires_when_no_handoff_follows``).
    """

    app_layer = tmp_path / "app"
    _with_interface(app_layer)
    fake_server = tmp_path / "fake_server.py"
    fake_server.write_text(_FAKE_SERVER, encoding="utf-8")
    data_dir = tmp_path / "data"
    controller = StatusController(
        repo_root=app_layer,
        server_command=(sys.executable, str(fake_server)),
        server_args=("--data-dir", str(data_dir)),
        environ=dict(os.environ),
        request_timeout=0.2,
        shutdown_timeout=1.0,
    )
    attempts: list[Path] = []

    def refuse(notice: Path, _reason: str) -> None:
        attempts.append(notice)
        raise OSError("read-only file system")

    monkeypatch.setattr(controller_module, "RELEASE_NOTICE_RETRY_DELAY", 0.0)
    monkeypatch.setattr(controller, "_write_release_notice", refuse)
    try:
        controller.start()
        first = controller.process
        if first is None:
            pytest.fail("set-up: the controller started no server")

        assert controller.release_update_restart("Discarded an invalid update request.") is False

        assert controller.process is first, "the server was replaced"
        assert first.poll() is None, "the server was stopped"
    finally:
        controller.close()

    assert len(attempts) == controller_module.RELEASE_NOTICE_ATTEMPTS
    written = (data_dir / "logs" / "update.log").read_text(encoding="utf-8")
    assert "Could not tell the server" in written and "read-only file system" in written
    assert "Discarded an invalid update request" in written


def test_an_approved_restart_expires_when_no_handoff_follows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract §4.2: a server still running long after approval has no handoff pending.

    The latch comes down on its own, once, tells its listeners -- the install
    status shows the attempt as failed -- and logs one line.
    """

    from server.updates import restart as restart_module

    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        restart_module,
        "log",
        SimpleNamespace(
            info=lambda *_args: None,
            warning=lambda *args: warnings.append(args),
            exception=lambda *_args: None,
        ),
    )
    now = [1000.0]
    latch = RestartApproval(ttl=300.0, clock=lambda: now[0])
    told: list[tuple[str, str]] = []
    latch.add_release_listener(lambda target, reason: told.append((target, reason)))
    latch.approve("v2.0.1")

    now[0] += 299.0
    assert latch.pending == "v2.0.1" and latch.refusal() is not None
    now[0] += 1.0
    assert latch.refusal() is None
    assert latch.pending is None

    assert [target for target, _reason in told] == ["v2.0.1"]
    assert "no handoff followed" in told[0][1]
    assert len(warnings) == 1, warnings


def test_a_late_release_for_an_earlier_approval_leaves_the_new_one_latched() -> None:
    """Contract §4.2 (the review's U13): a release names the approval it answers.

    ``approve`` returns an id, and a release that carries an older one -- a
    notice about attempt A arriving after attempt B was approved -- changes
    nothing. A release without an id, the launcher's notice, still releases
    whatever is pending, since only one approval is ever pending.
    """

    latch = RestartApproval()
    first = latch.approve("v2.0.1")
    assert latch.release("the handoff request could not be written", first)
    second = latch.approve("v2.0.2")
    assert second != first

    assert not latch.release("a late notice about the first attempt", first)
    assert latch.pending == "v2.0.2"

    assert latch.release("the status window did not hand off")
    assert latch.pending is None


def test_a_cad_return_ingested_after_restart_approval_is_refused(tmp_path: Path) -> None:
    """Contract §4.2: ingest starts work that outlives its request, so it refuses too.

    It is refused before its handler runs, so the return is not read, copied
    or changed, and it stays listed for after the restart.
    """

    async def scenario() -> tuple[int, bytes]:
        app = create_app(data_dir=tmp_path / "data")
        app.state.update_restart.approve("v2.0.1")
        return await _post(app, "/api/cadlink/ingest", {"bundlePath": "returns/x.wgreturn"})

    status, raw = asyncio.run(scenario())

    assert status == 409, raw[:200]
    error = json.loads(raw)["error"]
    assert error["code"] == "update_restart_pending"
    assert error["retryable"] is True


def test_a_cad_preparation_requested_after_restart_approval_is_held(tmp_path: Path) -> None:
    """The latch allows durable acceptance, while the job lane copies and meshes nothing."""

    async def scenario():
        app = create_app(data_dir=tmp_path / "data")
        store = app.state.cadlink_store
        from test_cad_preparation import _write_return, _accept
        from server.cadlink.joblane.preparation import retain_operation_snapshot
        workspace = tmp_path / "exchange"
        bundle, manifest = _write_return(workspace)
        _accept(store, "cmd-1", bundle, manifest)
        retain_operation_snapshot(store, tmp_path / "data", workspace, "cmd-1")
        app.state.update_restart.approve("v2.0.1")
        try:
            # A press on Fusion's request (the retired prepare route's, cad_backends.compat_prepare).
            from cad_backends import compat_prepare

            body = await compat_prepare(app.state, "cmd-1", frame_axis="+z")
            assert (body["operation"]["state"], body["operation"]["reason"]) == (
                "needs_user_input", "update_restart_pending",
            )
            assert "v2.0.1" in body["operation"]["message"]
            row = store.get_operation("cmd-1")
            job = app.state.jobs_runtime.store.latest_cad_job("cmd-1")
            assert (row["state"], row["attempt_generation"]) == ("accepted", 0)
            assert job["status"] == "preparing" and job["started_at"] is None
            assert job["config_json"]["frame_axis"] == "+z"
            assert job["task_metadata"]["cad"].get("preparation") is None
            assert job["run_number"] is None
        finally:
            for task in list(app.state.cad_preparations):
                task.cancel()
            await asyncio.gather(*list(app.state.cad_preparations), return_exceptions=True)
            await app.state.jobs_runtime.shutdown()
            store.close()
    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Contract §4.3: jobs and the restart
# ---------------------------------------------------------------------------


#: What a job the update restart ended reads as (``server/jobs/runtime/``).
UPDATE_RESTART_STAGE = "Ended by the update restart"


def _solve_request() -> SolveRequest:
    """A small solve for the engines below, built as ``test_jobs_runtime.py`` builds one."""

    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "mesh": {"wall_thickness": 0},
                "enclosure": {"depth": 0},
                "simulation": {
                    "f1": 250,
                    "f2": 8000,
                    "num_frequencies": 2,
                    "sim_type": "freestanding",
                },
            },
            "options": {"engine": "bempp", "stage_delay_ms": 0},
        }
    )


class _GatedSolve:
    """An engine whose solves wait for ``release``, checking for cancellation as they wait."""

    name = "bempp"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.solves = 0

    async def run(self, _request: SolveRequest, *, cancel_cb: Any, stage_cb: Any) -> Any:
        del stage_cb

        def solve() -> Any:
            self.solves += 1
            self.started.set()
            while not self.release.wait(timeout=0.01):
                cancel_cb()
            return EngineRunResult(
                results={
                    "frequencies": [500.0],
                    "directivity": {},
                    "spl_on_axis": {
                        "frequencies": [500.0],
                        "spl": [90.0],
                        "phase_degrees": [0.0],
                    },
                    "impedance": {"frequencies": [500.0], "real": [1.0], "imaginary": [0.0]},
                    "di": {"frequencies": [500.0], "di": {}},
                    "metadata": {"engine": "fake-bempp"},
                },
                msh_text="$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
                mesh_stats={"vertex_count": 3, "triangle_count": 1},
            )

        return await asyncio.to_thread(solve)


def _registry_for(engine: Any) -> EngineRegistry:
    return EngineRegistry(
        detector=lambda: [EngineInfo("bempp", True, "test", "test")],
        factory=lambda _name: engine,
    )


def _status(runtime: JobRuntime, job_id: str) -> str | None:
    row = runtime.store.get_job_row(job_id)
    return None if row is None else str(row["status"])


async def _until(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the job runtime")
        await asyncio.sleep(0.01)


FINISHED = {"complete", "error", "cancelled"}


def test_a_job_the_update_restart_ends_reads_as_ended_by_the_update_restart(
    tmp_path: Path,
) -> None:
    """Contract §4.3: stopped at a checkpoint by the restart, it names the restart, not Quit."""

    engine = _GatedSolve()
    approval = RestartApproval()
    database = tmp_path / "jobs.db"

    async def scenario() -> str:
        runtime = JobRuntime(
            JobStore(database),
            engine_registry=_registry_for(engine),
            restart_approval=approval,
        )
        job_id = await runtime.submit(_solve_request())
        if not await asyncio.to_thread(engine.started.wait, 5.0):
            pytest.fail("set-up: the solve never started")
        approval.approve("v2.0.1")  # the handoff request is written
        try:
            await asyncio.wait_for(runtime.shutdown(), timeout=5.0)  # the launcher stops it
        finally:
            engine.release.set()
        return job_id

    job_id = asyncio.run(scenario())
    inspection = JobStore(database)
    inspection.initialize()
    try:
        row = inspection.get_job_row(job_id)
    finally:
        inspection.close()

    assert row is not None
    assert row["status"] == "cancelled"
    assert row["stage_message"] == UPDATE_RESTART_STAGE
    assert "update restart" in row["error_message"]
    assert "Quit" not in row["error_message"]


def test_a_job_the_restart_cut_off_reads_as_ended_by_it_and_as_quit_to_an_older_start(
    tmp_path: Path,
) -> None:
    """Contract §4.3, when the shutdown budget ends a job before its next checkpoint.

    The mark is written with the cancellation request, so the next start reads
    the reason. A start that knows only the Quit reason -- a release that
    predates this mark, which an automatic rollback reopens -- reads the Quit
    mark written beside it: a stop, not a crash.
    """

    request = _solve_request().model_dump(mode="json")

    def marked_store(name: str) -> JobStore:
        store = JobStore(tmp_path / name)
        store.initialize()
        now = datetime.now().isoformat()
        store.create_job(
            {
                "id": "cut-off",
                "status": "running",
                "created_at": now,
                "updated_at": now,
                "queued_at": now,
                "started_at": now,
                "progress": 0.3,
                "stage": "solve",
                "config_json": request,
                "config_summary_json": {"formula_type": "OSSE"},
                "task_metadata": {},
            }
        )
        marked = store.request_cancellation(
            "cut-off",
            {"stage": "cancelling", "cancellation_requested": True},
            {"stage": "cancelling", "message": "Update restart requested"},
            interrupted_by_quit=True,
            interrupted_by_update_restart=True,
        )
        if marked is None:
            pytest.fail("set-up: the running job was not marked")
        return store

    async def next_start() -> dict[str, Any]:
        runtime = JobRuntime(marked_store("jobs.db"))
        await runtime.start()
        row = await runtime.get_job("cut-off")
        await runtime.shutdown()
        return row

    after_the_update = asyncio.run(next_start())
    older = marked_store("older.db")
    try:
        older.recover_on_startup(
            RESTART_RECOVERY_MESSAGE,
            quit_stage_message=QUIT_INTERRUPTED_STAGE_MESSAGE,
            quit_error_message=QUIT_INTERRUPTED_MESSAGE,
        )
        after_a_rollback = older.get_job_row("cut-off")
    finally:
        older.close()

    assert after_the_update["status"] == "cancelled"
    assert after_the_update["stage_message"] == UPDATE_RESTART_STAGE
    assert "update restart" in after_the_update["error_message"]
    assert after_a_rollback is not None
    assert after_a_rollback["status"] == "cancelled"
    assert after_a_rollback["error_message"] == QUIT_INTERRUPTED_MESSAGE


def test_a_job_queued_before_restart_approval_does_not_start_after_it(tmp_path: Path) -> None:
    """Contract §4.3: a queued job waits for the restart rather than starting under it.

    It stays queued, so the new process runs it. If the restart is called off,
    this process runs it.
    """

    engine = _GatedSolve()
    approval = RestartApproval()

    async def scenario() -> tuple[str | None, int, str | None, int]:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=_registry_for(engine),
            restart_approval=approval,
        )
        running_id = await runtime.submit(_solve_request())
        if not await asyncio.to_thread(engine.started.wait, 5.0):
            pytest.fail("set-up: the first solve never started")
        queued_id = await runtime.submit(_solve_request())
        approval.approve("v2.0.1")
        engine.release.set()  # the running job finishes, which frees the queue
        await _until(lambda: _status(runtime, running_id) in FINISHED)
        await asyncio.sleep(0.3)
        while_approved = _status(runtime, queued_id)
        solves_while_approved = engine.solves
        approval.release("the launcher discarded the request")
        await _until(lambda: _status(runtime, queued_id) in FINISHED)
        after_release = _status(runtime, queued_id)
        await runtime.shutdown()
        return while_approved, solves_while_approved, after_release, engine.solves

    while_approved, solves_while_approved, after_release, solves = asyncio.run(scenario())

    assert while_approved == "queued"
    assert solves_while_approved == 1
    assert after_release == "complete"
    assert solves == 2


def test_a_solve_the_restart_approval_overtakes_is_queued_not_started(tmp_path: Path) -> None:
    """Contract §4.2 and §4.3: the narrow race is closed where a job starts.

    A solve that passed the route's check just before the approval is queued,
    and one the scheduler had already taken is overtaken while its engine is
    looked up. Neither starts under the approval: both stay queued for the new
    process.
    """

    engine = _GatedSolve()
    engine.release.set()
    approval = RestartApproval()

    class ApprovesDuringTheLookup(EngineRegistry):
        armed = False

        async def get_engine(self, name: str) -> Any | None:
            if self.armed:
                approval.approve("v2.0.1")  # the latch goes up while the job is prepared
            return await super().get_engine(name)

    registry = ApprovesDuringTheLookup(
        detector=lambda: [EngineInfo("bempp", True, "test", "test")],
        factory=lambda _name: engine,
    )

    async def scenario() -> tuple[str | None, str | None, int]:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=registry,
            restart_approval=approval,
        )
        # Accepted by the route an instant before the approval.
        approval.approve("v2.0.1")
        accepted = await runtime.submit(_solve_request())
        await asyncio.sleep(0.3)
        accepted_status = _status(runtime, accepted)
        approval.release("called off")
        await _until(lambda: _status(runtime, accepted) in FINISHED)

        # Taken by the scheduler, then overtaken during its engine lookup.
        taken = await runtime.submit(_solve_request())
        registry.armed = True
        await _until(lambda: approval.pending is not None)
        await asyncio.sleep(0.3)
        taken_status = _status(runtime, taken)
        solves = engine.solves
        await runtime.shutdown()
        return accepted_status, taken_status, solves

    accepted_status, taken_status, solves = asyncio.run(scenario())

    assert accepted_status == "queued"
    assert taken_status == "queued"
    assert solves == 1, "a job started under an approved restart"


def test_the_job_runtime_reads_the_servers_restart_latch(tmp_path: Path) -> None:
    """Contract §4.3: the runtime holds the same latch the update routes set."""

    app = create_app(data_dir=tmp_path / "data")

    assert app.state.jobs_runtime.restart_approval is app.state.update_restart


def test_queued_jobs_start_when_an_approval_nobody_reads_expires(tmp_path: Path) -> None:
    """Contract §4.2 and §4.3: the expiry does not wait for something else to read the latch.

    It is the backstop for a launcher that never said it discarded the
    request, so the queue the approval held must start without a status read.
    """

    engine = _GatedSolve()
    engine.release.set()
    approval = RestartApproval(ttl=0.3)

    async def scenario() -> str | None:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=_registry_for(engine),
            restart_approval=approval,
        )
        approval.approve("v2.0.1")
        job_id = await runtime.submit(_solve_request())
        # From here on only the runtime itself reads the latch.
        await _until(lambda: _status(runtime, job_id) in FINISHED, timeout=5.0)
        finished = _status(runtime, job_id)
        await runtime.shutdown()
        return finished

    assert asyncio.run(scenario()) == "complete"


def test_an_approval_waits_for_a_job_that_is_being_marked_running() -> None:
    """Contract §4.3: marking a job running and approving a restart take one lock.

    So no job is marked running once an approval is set: it was running
    before, and the restart ends it with its own reason, or it stays queued.
    """

    approval = RestartApproval()
    order: list[str] = []

    def mark_running() -> threading.Thread:
        def approve() -> None:
            approval.approve("v2.0.1")
            order.append("approved")

        approver = threading.Thread(target=approve)
        approver.start()
        approver.join(0.2)  # held off while the job is marked running
        order.append("marked running")
        return approver

    admitted, approver = approval.admit(mark_running)
    approver.join(5.0)
    late, started = approval.admit(lambda: "started")

    assert admitted is True
    assert order == ["marked running", "approved"]
    assert (late, started) == (False, None)
