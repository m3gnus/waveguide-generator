"""The BEMPP worker must not be daemonic, or it cannot split a sweep at all.

``multiprocessing`` forbids a daemonic process from having children:
``BaseProcess.start`` asserts on it. The native sweep splits with a
``ProcessPoolExecutor``, so a daemonic worker fails every split sweep with
"daemonic processes are not allowed to have children" -- on exactly the long
sweeps the parallel default exists to speed up. Measured on this host before the
fix, mirroring the real wiring (spawn context, daemon=True child):

    child pid 3164 daemon = True
    SPLIT FAILED: AssertionError daemonic processes are not allowed to have children

and after:

    child pid 2956 daemon = False
    RESULT: [2, 4, 6]  /  SPLIT OK

This is a regression guard, not a style preference: the failure is invisible
until a sweep is long enough to split, so nothing in the short-sweep path
catches it.
"""

from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from server.solver import bempp_process


def _double(value: int) -> int:
    return value * 2


class _FakeConnection:
    def close(self) -> None:
        return None


class _FakeProcess:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.pid = 1234

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True


class _FakeContext:
    def __init__(self) -> None:
        self.started: list[dict] = []

    def Pipe(self, duplex: bool = True):
        return _FakeConnection(), _FakeConnection()

    def Process(self, **kwargs):
        self.started.append(kwargs)
        return _FakeProcess(**kwargs)


def test_the_worker_is_started_non_daemonic(monkeypatch):
    context = _FakeContext()
    host = bempp_process.BemppProcessHost(process_context=context)
    monkeypatch.setattr(
        bempp_process, "confine_to_windows_job", lambda pid: None, raising=False
    )

    host._ensure_started()

    assert context.started, "the host never started a worker"
    assert context.started[0]["daemon"] is False


def _child_that_splits(queue) -> None:
    try:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=2, mp_context=ctx) as pool:
            queue.put(("ok", list(pool.map(_double, [1, 2, 3]))))
    except BaseException as exc:  # noqa: BLE001 - the assertion is the subject
        queue.put(("failed", f"{type(exc).__name__}: {exc}"))


def test_a_non_daemonic_child_can_actually_split():
    """The behaviour behind the flag, not just the flag.

    Guards against someone restoring daemon=True because the unit assertion
    above looks arbitrary on its own.
    """

    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_child_that_splits, args=(queue,), daemon=False)
    proc.start()
    try:
        outcome, payload = queue.get(timeout=120)
    finally:
        proc.join(30)
        if proc.is_alive():
            proc.kill()

    assert outcome == "ok", f"a non-daemonic child could not split: {payload}"
    assert payload == [2, 4, 6]
