from __future__ import annotations

import threading
from types import SimpleNamespace as NS

import pytest

from server.solver.beat_runtime import host, registry as r


@pytest.mark.parametrize("blocks", [False, True])
def test_close_records_failed_bounded_engine_retirement(launch, monkeypatch, blocks):
    key, directory, _ = launch
    release, finished = threading.Event(), threading.Event()

    class Engine:
        def __init__(self, **kwargs):
            pass

        def terminate(self):
            try:
                if blocks:
                    release.wait()
            finally:
                finished.set()

    monkeypatch.setattr(host, "RETIREMENT_TIMEOUT", 0.2)
    owner = host.WorkerHost(key, directory, engine_factory=Engine)
    owner._engine = owner._build_engine()  # No listener needed to test retirement.
    try:
        assert not owner._retirement_failed
        owner.close()
        assert owner._retirement_failed is blocks
    finally:
        release.set()
        assert finished.wait(1)


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
@pytest.mark.parametrize("target", ["ownership", "engine"])
def test_close_records_only_timed_out_retirement(launch, failure, target):
    key, directory, _ = launch
    owner = host.WorkerHost(key, directory)

    def fail():
        raise failure("fixture retirement failure")

    if target == "ownership":
        owner._ownership = NS(shutdown=fail)
    else:
        owner._engine = NS(terminate=fail)
    owner.close()
    assert owner._retirement_failed is (failure is TimeoutError)


@pytest.mark.parametrize("startup", [False, True])
@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
def test_cancel_records_only_timed_out_retirement(launch, startup, failure):
    key, directory, _ = launch
    owner = host.WorkerHost(key, directory)

    def fail():
        raise failure("fixture retirement failure")

    job = host._Job({}, lambda _: None, 1)
    job.running = True
    owner._engine = NS(terminate=fail)
    if not startup:
        job.stream = NS(cancel=fail)
    owner._cancel_job(job, retire_startup=startup)
    assert owner._retirement_failed is (failure is TimeoutError)
    assert owner._stopping.is_set()


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("close_raises", [False, True])
def test_main_hard_exits_only_after_failed_retirement(launch, monkeypatch, failed, close_raises):
    key, directory, _ = launch
    directory = r.private_directory(directory)
    spec = r.write_launch_spec(key, directory)
    observed = []

    class StandIn:
        def __init__(self, *args, **kwargs):
            self.identifier = r.key_id(key)
            self._retirement_failed = False

        def bind(self):
            return None

        def serve(self):
            observed.append("serve")

        def close(self):
            observed.append("close")
            self._retirement_failed = failed
            if close_raises:
                raise RuntimeError("fixture close failure")

        def _log(self, message):
            observed.append(message)

    monkeypatch.chdir(directory)  # main's cwd is restored by monkeypatch.
    monkeypatch.setattr(host, "WorkerHost", StandIn)
    monkeypatch.setattr(host, "sweep_orphan_socket", lambda *args, **kwargs: None)
    monkeypatch.setattr(r, "write_record", lambda *args: None)
    monkeypatch.setattr(host.os, "_exit", lambda code: observed.append(("exit", code)))
    arguments = ["--key", str(spec), "--dir", str(directory)]
    if close_raises:
        with pytest.raises(RuntimeError, match="fixture close failure"):
            host.main(arguments)
    else:
        assert host.main(arguments) == 0
    assert observed[:2] == ["serve", "close"]
    # The hard exit keeps the status the host would have exited with.
    expected = ["host close failed: fixture close failure"] if close_raises else []
    if failed:
        expected.extend(["retirement failed; exiting without interpreter shutdown",
                         ("exit", 1 if close_raises else 0)])
    assert observed[2:] == expected
