"""Exactly once across a WG restart: the request inbox, crashed at every step.

M1 transfer contract C4 and C5; CAD-OPERATIONS.md, "Crash and power-cut
positions". WG takes a request file in four steps: claim it by rename, accept
it into the operation store (``accept_operation`` commits), retain its
snapshot, delete the claim. The delivery loop then prepares an accepted solve
and submits one job under ``cad-solve:<operationId>``.

Each test here stops WG between two of those steps, drops every object the
first process held, reopens the operation store and the jobs store from the
same SQLite files, runs start-up recovery and fresh delivery passes, and then
asserts, for both kinds and for schema 4 and schema 3 files as the add-in
writes them (``fixtures/wglink_inbox/``):

- exactly one operation for the id;
- at most one job for it, under ``cad-solve:<id>``, and the solve submitted
  once across both processes;
- the operation progressed (``accepted``) and is never left in ``received``;
- no request or claim file is left in the inbox.

The stop is an exception no handler in the delivery path catches
(``_Crash`` is a ``BaseException``), raised once at the named step. Module
state a process keeps in memory is dropped with it. One test kills a real child
process mid-pass instead (SIGKILL, or TerminateProcess on Windows).

Meshing is the fenced stand-in ``test_cad_preparation.FakeIngest``, and the
jobs system is a real ``JobStore`` on disk whose submission key is the only
dedupe: the count of submissions is asserted separately, so the key cannot
hide a second submission.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Callable

import pytest

from server.cadlink import preparation, solve_command
from server.cadlink.operations import PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT
from server.cadlink.preparation import PreparationContext, recover_operations, run_delivery_pass
from server.cadlink.solve_command import CAD_SOLVE_SUBMISSION_PREFIX, CLAIM_PREFIX, SOLVE_REQUESTS_DIRECTORY
from server.cadlink.store import CadLinkStore
from server.jobs.store import JobStore

from test_cad_inbox_stall import V3_SOLVE, V4_SNAPSHOT, V4_SOLVE, drop, fixture
from test_cad_preparation import FakeIngest, _setup
from test_cad_project_setup import _project, _project_return, _record_setup


class _Crash(BaseException):
    """WG stopped here. Not an ``Exception``: nothing in the delivery path catches it."""


def _forget_process_state() -> None:
    """What a process keeps in memory, gone with it."""

    solve_command._retention_waits.clear()
    solve_command._unreadable_waits.clear()
    solve_command._refused_claims.clear()
    solve_command._live_waits.clear()


@pytest.fixture(autouse=True)
def _fresh_process_state():
    _forget_process_state()
    yield
    _forget_process_state()


class Wg:
    """One WG process: its operation store and jobs store, opened from the files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data_dir = root / "data"
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.cadlink_db = root / "cadlink.db"
        self.jobs_db = root / "jobs.db"
        self.store = CadLinkStore(self.cadlink_db)
        self.jobs = JobStore(self.jobs_db)
        self.jobs.initialize()
        self.ingest = FakeIngest()
        #: Every request this process handed to the jobs system.
        self.submitted: list[Any] = []
        #: Raised once the job exists, before the operation records it.
        self.crash_after_job: bool = False

    async def _submit(self, request) -> str:
        self.submitted.append(request)
        now = "2026-09-21T12:00:00"
        job_id, _created, _event = self.jobs.create_job_idempotent(
            {
                "id": f"job-{len(self.submitted)}-{os.getpid()}", "status": "queued",
                "created_at": now, "updated_at": now, "queued_at": now, "progress": 0.0,
                "stage": "queued", "stage_message": "queued", "config_json": {},
                "config_summary_json": {}, "task_metadata": {},
            },
            submission_key=str(request.client_request_id),
            request_sha256=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
            initial_event=("queued", {}),
        )
        if self.crash_after_job:
            self.crash_after_job = False
            raise _Crash("stopped after the job was created")
        return job_id

    def context(self) -> PreparationContext:
        return PreparationContext(
            store=self.store,
            data_dir=self.data_dir,
            workspace_root=self.workspace.resolve(),
            submit=self._submit,
            job_for_submission=self.jobs.job_for_submission_key,
            ingest=self.ingest,
        )

    def start(self) -> int:
        """What WG does at start-up before its first delivery pass."""

        return recover_operations(self.context())

    def run_pass(self) -> list[str]:
        """One delivery pass, running every preparation it starts to its end."""

        async def one() -> list[str]:
            started: list[asyncio.Future[Any]] = []
            ids = await run_delivery_pass(
                self.context(),
                spawn=lambda _id, coroutine: started.append(asyncio.ensure_future(coroutine)),
            )
            await asyncio.gather(*started)
            return ids

        return asyncio.run(one())

    def stop(self) -> None:
        self.store.close()
        self.jobs.close()


def _restart(old: Wg) -> Wg:
    """A new process on the same files. The old one's objects are never used again."""

    old.stop()
    _forget_process_state()
    return Wg(old.root)


def _setup_project(wg: Wg) -> tuple[str, str]:
    """A saved project with its solve setup, and a return Fusion exported from it."""

    design_id, lineage_id = _project(wg, 60.0)
    _record_setup(wg, lineage_id, _setup())
    return _project_return(wg, "speaker", design_id, lineage_id)


REQUESTS = {
    "v4-solve": V4_SOLVE,
    "v4-snapshot": V4_SNAPSHOT,
    "v3-solve": V3_SOLVE,
}


def _request(name: str, bundle_path: str, manifest: str) -> dict[str, Any]:
    """The add-in's own file for that schema and kind, pointed at a real return."""

    return fixture(REQUESTS[name], bundlePath=bundle_path, manifestSha256=manifest)


def _inbox(wg: Wg) -> list[str]:
    folder = wg.data_dir / "ipc" / "wglink" / SOLVE_REQUESTS_DIRECTORY
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def _operation_rows(wg: Wg) -> list[tuple[str, str]]:
    """Read from the file, not through the store under test."""

    with closing(sqlite3.connect(wg.cadlink_db)) as conn:
        return conn.execute("SELECT operation_id, state FROM cad_operations").fetchall()


def _jobs_for(wg: Wg, operation_id: str) -> list[str]:
    with closing(sqlite3.connect(wg.jobs_db)) as conn:
        rows = conn.execute(
            "SELECT job_id FROM job_submissions WHERE submission_key = ?",
            (f"{CAD_SOLVE_SUBMISSION_PREFIX}{operation_id}",),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM simulation_jobs").fetchone()[0]
        keyed = conn.execute("SELECT COUNT(*) FROM job_submissions").fetchone()[0]
    assert total == keyed, "a job exists that no submission key names"
    return [row[0] for row in rows]


def _assert_exactly_once(wg: Wg, request: dict[str, Any], submissions: int) -> None:
    operation_id = request["operationId"]
    assert _operation_rows(wg) == [(operation_id, "accepted")]
    assert _inbox(wg) == []
    row = wg.store.get_operation(operation_id)
    assert row is not None
    jobs = _jobs_for(wg, operation_id)
    if request.get("kind", PREPARE_AND_SOLVE) == PREPARE_AND_SOLVE:
        assert row["kind"] == PREPARE_AND_SOLVE
        assert len(jobs) == 1 and row["job_id"] == jobs[0]
        assert submissions == 1
    else:
        assert row["kind"] == RECEIVE_SNAPSHOT
        assert jobs == [] and submissions == 0


def _run_to_rest(wg: Wg) -> None:
    """Start-up recovery, then passes until one starts nothing and the inbox is empty."""

    wg.start()
    for _ in range(4):
        started = wg.run_pass()
        if not started and not _inbox(wg):
            break
    # One more pass after rest: it must start nothing.
    assert wg.run_pass() == []


# -- the baseline, with no stop ------------------------------------------------


@pytest.mark.parametrize("name", list(REQUESTS))
def test_a_request_with_no_stop_is_one_operation_and_at_most_one_job(tmp_path: Path, name: str) -> None:
    """The positive control of every test below: the same measurements, no crash."""

    wg = Wg(tmp_path)
    request = _request(name, *_setup_project(wg))
    drop(wg.data_dir, request)
    _run_to_rest(wg)
    _assert_exactly_once(wg, request, len(wg.submitted))
    wg.stop()


def test_two_commands_for_the_same_return_are_two_jobs(tmp_path: Path) -> None:
    """The job counter can count past one: a new id is a new command."""

    wg = Wg(tmp_path)
    bundle_path, manifest = _setup_project(wg)
    first = _request("v4-solve", bundle_path, manifest)
    second = {**first, "commandId": "second-command", "operationId": "second-command"}
    drop(wg.data_dir, first)
    drop(wg.data_dir, second)
    _run_to_rest(wg)
    assert sorted(_operation_rows(wg)) == sorted(
        [(first["operationId"], "accepted"), ("second-command", "accepted")]
    )
    assert len(_jobs_for(wg, first["operationId"])) == 1
    assert len(_jobs_for(wg, "second-command")) == 1
    assert len(wg.submitted) == 2
    wg.stop()


# -- a stop at each step --------------------------------------------------------


def _crash_before(target: Any, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    original = getattr(target, name)
    fired: list[bool] = []

    def stopped(*args: Any, **kwargs: Any) -> Any:
        if not fired:
            fired.append(True)
            raise _Crash(f"stopped before {name}")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, stopped)


def _crash_after(target: Any, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    original = getattr(target, name)
    fired: list[bool] = []

    def stopped(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not fired:
            fired.append(True)
            raise _Crash(f"stopped after {name}")
        return result

    monkeypatch.setattr(target, name, stopped)


def _claims(wg: Wg) -> list[str]:
    return [name for name in _inbox(wg) if name.startswith(CLAIM_PREFIX)]


def _after_claim(wg: Wg, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    _crash_before(CadLinkStore, "accept_operation", monkeypatch)

    def check(request: dict[str, Any]) -> None:
        assert len(_claims(wg)) == 1 and len(_inbox(wg)) == 1
        assert _operation_rows(wg) == []

    return check


def _after_accept(wg: Wg, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    _crash_after(CadLinkStore, "accept_operation", monkeypatch)

    def check(request: dict[str, Any]) -> None:
        assert len(_claims(wg)) == 1
        assert _operation_rows(wg) == [(request["operationId"], "received")]

    return check


def _after_retention(wg: Wg, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    _crash_after(preparation, "settle_snapshot_operation", monkeypatch)

    def check(request: dict[str, Any]) -> None:
        assert len(_claims(wg)) == 1
        with closing(sqlite3.connect(wg.cadlink_db)) as conn:
            snapshot = conn.execute(
                "SELECT snapshot_json FROM cad_operations WHERE operation_id = ?",
                (request["operationId"],),
            ).fetchone()[0]
        assert snapshot is not None  # retained before the stop

    return check


def _after_claim_delete(wg: Wg, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    _crash_after(solve_command, "_acknowledge", monkeypatch)

    def check(request: dict[str, Any]) -> None:
        assert _inbox(wg) == []
        assert len(_operation_rows(wg)) == 1
        assert wg.submitted == [] and _jobs_for(wg, request["operationId"]) == []

    return check


STOPS = {
    "after-claim": _after_claim,
    "after-accept": _after_accept,
    "after-retention": _after_retention,
    "after-claim-delete": _after_claim_delete,
}


@pytest.mark.parametrize("stop", list(STOPS))
@pytest.mark.parametrize("name", list(REQUESTS))
def test_a_restart_at_any_step_leaves_one_operation_and_at_most_one_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, stop: str
) -> None:
    first = Wg(tmp_path)
    request = _request(name, *_setup_project(first))
    drop(first.data_dir, request)
    check = STOPS[stop](first, monkeypatch)

    with pytest.raises(_Crash):
        first.start()
        first.run_pass()
    check(request)
    submissions = len(first.submitted)
    monkeypatch.undo()

    second = _restart(first)
    _run_to_rest(second)
    _assert_exactly_once(second, request, submissions + len(second.submitted))
    second.stop()


@pytest.mark.parametrize("name", ["v4-solve", "v3-solve"])
def test_a_restart_after_the_job_was_created_does_not_submit_again(tmp_path: Path, name: str) -> None:
    """The job exists and the operation never recorded it: start-up takes the job."""

    first = Wg(tmp_path)
    request = _request(name, *_setup_project(first))
    drop(first.data_dir, request)
    first.crash_after_job = True
    with pytest.raises(_Crash):
        first.start()
        first.run_pass()
    assert len(_jobs_for(first, request["operationId"])) == 1
    assert first.store.get_operation(request["operationId"])["state"] != "accepted"

    second = _restart(first)
    _run_to_rest(second)
    _assert_exactly_once(second, request, len(first.submitted) + len(second.submitted))
    second.stop()


@pytest.mark.parametrize("name", ["v4-solve", "v3-solve"])
def test_a_restart_mid_preparation_waits_visibly_and_solves_once_when_asked(
    tmp_path: Path, name: str
) -> None:
    """Stopped while meshing: never left ``received`` or ``processing``. Start-up
    says so (``interrupted``), and Solve now submits exactly one job."""

    first = Wg(tmp_path)
    request = _request(name, *_setup_project(first))
    drop(first.data_dir, request)

    def stopped() -> None:
        raise _Crash("stopped while meshing")

    first.ingest.during = stopped
    with pytest.raises(_Crash):
        first.start()
        first.run_pass()
    assert _operation_rows(first) == [(request["operationId"], "processing")]

    second = _restart(first)
    _run_to_rest(second)
    row = second.store.get_operation(request["operationId"])
    assert (row["state"], row["reason"]) == ("needs_user_input", "interrupted")
    assert "Press Solve now" in json.loads(row["outcome_json"])["message"]
    assert _inbox(second) == [] and second.submitted == []

    summary = asyncio.run(preparation.prepare_operation(
        second.context(), request["operationId"], preparation.PreparationInput()
    ))
    assert summary["state"] == "accepted"
    _assert_exactly_once(second, request, len(first.submitted) + len(second.submitted))
    second.stop()


# -- a real process, killed mid-pass ---------------------------------------------

_CHILD = r"""
import asyncio, sys, time
from pathlib import Path
sys.path[:0] = [sys.argv[3], sys.argv[4]]
import test_cad_inbox_restart as t
from server.cadlink.store import CadLinkStore

root, point = Path(sys.argv[1]), sys.argv[2]
ready = root / "child-ready"
wg = t.Wg(root)

def hold_here():
    ready.write_text(point)
    while True:  # killed here, from outside
        time.sleep(0.05)

if point == "after-accept":
    original = CadLinkStore.accept_operation
    def accept(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        hold_here()
    CadLinkStore.accept_operation = accept
elif point == "after-job":
    submit = wg._submit
    async def after_job(request):
        await submit(request)
        hold_here()
    wg._submit = after_job
wg.start()
wg.run_pass()
sys.exit(3)  # never reached: the parent kills this process at the hold
"""


@pytest.mark.parametrize(
    ("name", "point"),
    [("v4-solve", "after-accept"), ("v4-snapshot", "after-accept"), ("v4-solve", "after-job")],
)
def test_a_wg_process_killed_mid_pass_is_recovered_by_the_next_start(
    tmp_path: Path, name: str, point: str
) -> None:
    setup = Wg(tmp_path)
    request = _request(name, *_setup_project(setup))
    drop(setup.data_dir, request)
    setup.stop()

    tests = Path(__file__).resolve().parent
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(tmp_path), point, str(tests), str(tests.parents[1])],
        cwd=str(tests.parents[1]),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    ready = tmp_path / "child-ready"
    deadline = time.monotonic() + 60
    try:
        while not ready.exists():
            if child.poll() is not None:
                output = child.stdout.read().decode(errors="replace") if child.stdout else ""
                pytest.fail(f"the child exited ({child.returncode}) before its hold:\n{output}")
            if time.monotonic() > deadline:
                pytest.fail("the child never reached its hold")
            time.sleep(0.05)
    finally:
        child.kill()  # SIGKILL on POSIX, TerminateProcess on Windows
        child.wait(timeout=30)
        if child.stdout:
            child.stdout.close()
    assert child.returncode != 0 and child.returncode != 3

    # What the killed process left: a claim, and an operation not yet finished.
    if point == "after-accept":
        assert len(_claims(setup)) == 1
        assert _operation_rows(setup) == [(request["operationId"], "received")]
    else:
        assert len(_jobs_for(setup, request["operationId"])) == 1
        assert _operation_rows(setup)[0][1] != "accepted"

    _forget_process_state()
    wg = Wg(tmp_path)
    _run_to_rest(wg)
    # The killed process submitted once when its hold was after the job.
    _assert_exactly_once(wg, request, len(wg.submitted) + (1 if point == "after-job" else 0))
    wg.stop()


# -- a power cut: what survives on disk may be older than what WG did --------------


@pytest.mark.parametrize("restart", [True, False], ids=["after-restart", "same-process"])
@pytest.mark.parametrize("as_claim", [False, True], ids=["request-file", "claim-file"])
@pytest.mark.parametrize("name", list(REQUESTS))
def test_a_request_file_that_survives_its_delete_is_safe_to_deliver_again(
    tmp_path: Path, name: str, as_claim: bool, restart: bool
) -> None:
    """The delete of a taken file is not flushed to its directory: after a power
    cut the request file, or its claim, can be back. It is the same id and digest,
    so it recovers the operation: no second operation, no second job (C5). The
    same holds without a restart: the add-in's retry of a write whose outcome it
    could not tell is this same file, again."""

    first = Wg(tmp_path)
    request = _request(name, *_setup_project(first))
    drop(first.data_dir, request)
    _run_to_rest(first)
    submissions = len(first.submitted)

    second = _restart(first) if restart else first
    if not restart:
        second.submitted = []
    path = drop(second.data_dir, request)
    if as_claim:
        path.rename(path.with_name(f"{CLAIM_PREFIX}{'0' * 32}.json"))
    _run_to_rest(second)
    _assert_exactly_once(second, request, submissions + len(second.submitted))
    second.stop()


@pytest.mark.parametrize("name", list(REQUESTS))
def test_a_request_whose_acceptance_was_lost_is_taken_again_without_a_second_job(
    tmp_path: Path, name: str
) -> None:
    """WAL with synchronous=NORMAL can lose the last commits on a power cut, and
    the delete can survive them or not. Where the file survived and the operation
    did not, the file is accepted again; a job its first life made is found by
    its submission key, and nothing is submitted twice."""

    first = Wg(tmp_path)
    request = _request(name, *_setup_project(first))
    drop(first.data_dir, request)
    _run_to_rest(first)
    jobs_before = _jobs_for(first, request["operationId"])
    submissions = len(first.submitted)
    first.stop()

    with closing(sqlite3.connect(first.cadlink_db)) as conn:
        conn.execute("DELETE FROM cad_operations WHERE operation_id = ?", (request["operationId"],))
        conn.commit()
    _forget_process_state()
    second = Wg(tmp_path)
    assert _operation_rows(second) == []
    drop(second.data_dir, request)
    _run_to_rest(second)
    _assert_exactly_once(second, request, submissions + len(second.submitted))
    assert _jobs_for(second, request["operationId"]) == jobs_before
    second.stop()
