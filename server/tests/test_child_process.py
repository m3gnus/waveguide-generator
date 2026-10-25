"""Shared lifecycle policies, including both callers' diagnostic contracts."""

from __future__ import annotations

import ctypes
import io
import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from server.cadlink.intake import isolation
from server.platform import child_process, process_tree
from server.solver import bempp_process


@pytest.mark.parametrize(
    "code,cad,bempp",
    [
        (None, "with no recorded code", "its exit status could not be read"),
        (0, "with code 0", "it ended with exit code 0"),
        (7, "with code 7", "it ended with exit code 7"),
        (-15, "on signal SIGTERM (-15)", "it was stopped by signal SIGTERM (15)"),
        (-99, "on signal 99 (-99)", "it was stopped by signal 99"),
        (0x80000000, "with code 0x80000000", "it ended with status 0x80000000"),
        (0xC0000005, "with code 0xC0000005", "it ended with status 0xC0000005 (access violation)"),
        (
            0xC000001D,
            "with code 0xC000001D",
            "it ended with status 0xC000001D (illegal instruction)",
        ),
        (0xC0000017, "with code 0xC0000017", "it ended with status 0xC0000017 (out of memory)"),
        (
            0xC0000094,
            "with code 0xC0000094",
            "it ended with status 0xC0000094 (integer division by zero)",
        ),
        (0xC00000FD, "with code 0xC00000FD", "it ended with status 0xC00000FD (stack overflow)"),
        (
            0xC000013A,
            "with code 0xC000013A",
            "it ended with status 0xC000013A (interrupted with Ctrl+C)",
        ),
        (0xC0000374, "with code 0xC0000374", "it ended with status 0xC0000374 (heap corruption)"),
        (
            0xC0000409,
            "with code 0xC0000409",
            "it ended with status 0xC0000409 (fail-fast exception (stack buffer overrun))",
        ),
        (0xC0000420, "with code 0xC0000420", "it ended with status 0xC0000420 (assertion failure)"),
        (0xE0434352, "with code 0xE0434352", "it ended with status 0xE0434352"),
    ],
)
def test_exit_wording_is_byte_identical(code, cad, bempp):
    assert child_process.describe_exit(code, wording=isolation._EXIT_WORDING, windows=False) == cad
    assert (
        child_process.describe_exit(code, wording=bempp_process._EXIT_WORDING, windows=True)
        == bempp
    )
    if code is not None and code >= 0x80000000:
        assert (
            child_process.describe_exit(code, wording=bempp_process._EXIT_WORDING, windows=False)
            == f"it ended with exit code {code}"
        )


def test_pipe_tail_keeps_exact_bytes_and_truncation_prefix():
    stream = io.BytesIO(b"old output\n" + b"\xfflast\n")
    drain = child_process.PipeDrain(stream, 6, truncation_prefix="discarded\n")
    assert drain.text() == "discarded\n\ufffdlast\n"
    assert stream.closed


def test_pipe_tail_at_limit_has_no_prefix():
    drain = child_process.PipeDrain(io.BytesIO(b"last\n"), 5, truncation_prefix="discarded\n")
    assert drain.text() == "last\n"


def test_pipe_failure_still_closes_stream():
    class FailingStream(io.BytesIO):
        def read1(self, _size):
            raise OSError("broken pipe")

    stream = FailingStream()
    drain = child_process.PipeDrain(stream, 5, truncation_prefix="discarded\n")
    assert drain.text() == ""
    assert stream.closed


def _excerpt(raw, *, truncated=False):
    return child_process.stderr_excerpt(
        raw, truncated=truncated, lines_limit=12, line_chars=300, chars_limit=2000
    )


def test_file_tail_forwards_caps_disk_and_resets_between_jobs(tmp_path, capsys):
    path = tmp_path / "stderr.log"
    path.write_bytes(b"dropped\npartial\nlast\n")
    capture = child_process.FileStderr(
        str(path), tail_bytes=13, file_cap_bytes=21, excerpt=_excerpt
    )
    assert capture.excerpt() == "last"
    assert capsys.readouterr().err == "dropped\npartial\nlast\n"
    assert path.stat().st_size == 0
    capture.clear_tail()
    path.write_bytes(b"next job\n")
    assert capture.excerpt() == "next job"
    assert capsys.readouterr().err == "next job\n"
    capture.discard()
    assert not path.exists()


def test_file_capture_handles_missing_file_and_external_truncation(tmp_path, capsys):
    path = tmp_path / "stderr.log"
    capture = child_process.FileStderr(
        str(path), tail_bytes=64, file_cap_bytes=128, excerpt=_excerpt
    )
    assert capture.excerpt() == ""
    path.write_bytes(b"old long line\n")
    capture.clear_tail()
    path.write_bytes(b"new\n")
    assert capture.excerpt() == "new"
    assert capsys.readouterr().err == "old long line\nnew\n"
    capture.discard()


def test_excerpt_enforces_partial_line_line_count_line_length_and_total_limits():
    raw = b"partial\n\n" + b"old\n" * 20 + b"x" * 400 + b" \nlast  \n"
    excerpt = _excerpt(raw, truncated=True)
    assert len(excerpt.splitlines()) == 12
    assert excerpt.splitlines()[-2] == "x" * 297 + "..."
    assert excerpt.endswith("\nlast")
    assert len(_excerpt((b"x" * 300 + b"\n") * 12)) == 2000
    assert _excerpt((b"x" * 300 + b"\n") * 12).startswith("...")


def test_supervision_checks_deadline_before_memory_and_stops_once(monkeypatch):
    calls = []
    ticks = iter([10.0, 11.0])
    monkeypatch.setattr(child_process.time, "monotonic", lambda: next(ticks))

    def wait(*, timeout):
        calls.append(("wait", timeout))
        raise subprocess.TimeoutExpired("child", timeout)

    reason = child_process.supervise(
        SimpleNamespace(wait=wait),
        wall_time_s=1.0,
        interval_s=0.5,
        stop=lambda: calls.append("stop"),
        resource_check=lambda: calls.append("memory"),
        timeout_reason="deadline",
    )
    assert reason == "deadline"
    assert calls == [("wait", 0.5), "stop"]


def test_supervision_clean_exit_does_not_stop_or_sample():
    calls = []
    reason = child_process.supervise(
        SimpleNamespace(wait=lambda **kwargs: calls.append(kwargs)),
        wall_time_s=1.0,
        interval_s=0.5,
        stop=lambda: pytest.fail("stopped"),
        resource_check=lambda: pytest.fail("sampled"),
        timeout_reason="deadline",
    )
    assert reason is None
    assert calls == [{"timeout": 0.5}]


def test_supervision_resource_refusal_stops_once():
    calls = []

    def wait(*, timeout):
        raise subprocess.TimeoutExpired("child", timeout)

    assert (
        child_process.supervise(
            SimpleNamespace(wait=wait),
            wall_time_s=60,
            interval_s=0.5,
            stop=lambda: calls.append("stop"),
            resource_check=lambda: "memory",
            timeout_reason="deadline",
        )
        == "memory"
    )
    assert calls == ["stop"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX sessions and signal-based timeout")
def test_timeout_stops_a_real_owned_child():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    group = child_process.posix_process_group(process)
    try:
        assert (
            child_process.supervise(
                process,
                wall_time_s=0.05,
                interval_s=0.02,
                stop=lambda: child_process.terminate_process_tree(
                    process, group=group, grace_seconds=0.1
                ),
                resource_check=lambda: None,
                timeout_reason="deadline",
            )
            == "deadline"
        )
        assert process.returncode == -signal.SIGTERM
    finally:
        child_process.terminate_process_tree(process, group=group, grace_seconds=0.1)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group signals")
def test_tree_stop_escalates_the_resolved_group_and_reaps(monkeypatch):
    calls = []
    monkeypatch.setattr(child_process.os, "getpgid", lambda pid: 10)
    monkeypatch.setattr(child_process.os, "killpg", lambda group, sig: calls.append((group, sig)))
    waits = iter([False, True])
    monkeypatch.setattr(
        child_process,
        "_wait_for_tree_exit",
        lambda *args: calls.append(("wait", args[1:])) or next(waits),
    )
    process = SimpleNamespace(poll=lambda: None, kill=lambda: calls.append("kill"))
    job = SimpleNamespace(stop=lambda timeout: calls.append(("job", timeout)) or True)
    assert child_process.terminate_process_tree(process, job, group=20, grace_seconds=2.0)
    assert calls == [
        ("job", 2.0),
        (20, signal.SIGTERM),
        ("wait", (20, 2.0)),
        (20, signal.SIGKILL),
        "kill",
        ("wait", (20, 2.0)),
    ]


@pytest.mark.parametrize("stop", ["survivor", "raises"])
def test_tree_stop_reports_a_job_member_it_could_not_confirm_gone(monkeypatch, stop):
    def job_stop(timeout):
        if stop == "raises":
            raise OSError("job query failed")
        return False

    monkeypatch.setattr(child_process, "_wait_for_tree_exit", lambda *args: True)
    process = SimpleNamespace(poll=lambda: 0)
    assert not child_process.terminate_process_tree(
        process, SimpleNamespace(stop=job_stop), grace_seconds=0.1
    )


def test_tree_stop_reports_a_root_that_outlived_the_kill(monkeypatch):
    monkeypatch.setattr(child_process, "_wait_for_tree_exit", lambda *args: False)
    process = SimpleNamespace(poll=lambda: None, terminate=lambda: None, kill=lambda: None)
    assert not child_process.terminate_process_tree(process, grace_seconds=0.1)


def _fake_job_kernel(members, *, survivors=()):
    """The Win32 calls ``WindowsJob.stop`` makes, over a scripted member table.

    Plain functions, not bound methods: the wrapper declares ``argtypes`` on them.
    """

    state = {"members": list(members)}
    events, open_handles = [], set()

    def query(job, info_class, listing, size, returned):
        assert info_class == 3
        listing._obj.NumberOfAssignedProcesses = len(state["members"])
        listing._obj.NumberOfProcessIdsInList = len(state["members"])
        for index, pid in enumerate(state["members"]):
            listing._obj.ProcessIdList[index] = pid
        events.append(("list", tuple(state["members"])))
        return True

    def open_process(access, inherit, pid):
        open_handles.add(1000 + pid)
        return 1000 + pid

    def is_in_job(process, job, member):
        member._obj.value = True
        return True

    def terminate(job, code):
        events.append("terminate")
        # Exiting members leave the id list before they have finished exiting.
        state["members"] = []
        return True

    def wait(handle, timeout_ms):
        events.append(("wait", handle - 1000))
        return 0x102 if handle - 1000 in survivors else 0

    kernel = SimpleNamespace(
        QueryInformationJobObject=query, OpenProcess=open_process,
        IsProcessInJob=is_in_job, TerminateJobObject=terminate,
        WaitForSingleObject=wait, CloseHandle=open_handles.discard,
    )
    return kernel, events, open_handles


@pytest.mark.parametrize("survivors,stopped", [((), True), ((8,), False)])
def test_windows_job_stop_waits_for_every_member_it_saw_before_the_kill(survivors, stopped):
    kernel, events, open_handles = _fake_job_kernel([7, 8], survivors=survivors)
    job = process_tree.WindowsJob(55, kernel, suppress_errors=False)
    assert job.stop(1.0) is stopped
    assert events[:3] == [("list", (7, 8)), "terminate", ("list", ())]
    assert [event[1] for event in events[3:]] == [7, 8]
    assert open_handles == set(), "member handles leaked"


@pytest.mark.skipif(os.name != "posix", reason="POSIX server-group guard")
def test_tree_stop_never_signals_the_servers_group(monkeypatch):
    calls = []
    monkeypatch.setattr(child_process.os, "getpgid", lambda pid: 10)
    monkeypatch.setattr(
        child_process.os, "killpg", lambda *args: pytest.fail("server group signalled")
    )
    monkeypatch.setattr(child_process, "_wait_for_tree_exit", lambda *args: True)
    child_process.terminate_process_tree(
        SimpleNamespace(poll=lambda: None, terminate=lambda: calls.append("terminate")), group=10
    )
    assert calls == ["terminate"]


def test_worker_stop_keeps_root_protocol_tree_order_and_join_limits(monkeypatch):
    calls = []
    alive = iter([True, True, False])
    process = SimpleNamespace(
        is_alive=lambda: next(alive),
        terminate=lambda: calls.append("terminate"),
        kill=lambda: calls.append("kill"),
        join=lambda *args: calls.append(("join", args)),
    )
    child_process.terminate_worker(process, join_seconds=0.5)
    calls.append("protocol teardown")
    monkeypatch.setattr(child_process, "_process_group_alive", lambda group: False)
    child_process.finish_worker_tree(
        process, None, 42, kill_group=lambda group: calls.append(("group", group))
    )
    assert calls == [
        "terminate",
        ("join", (0.5,)),
        "kill",
        ("join", (0.5,)),
        "protocol teardown",
        ("group", 42),
        ("join", ()),
    ]


def test_worker_job_owns_tree_instead_of_announced_group():
    calls = []
    job = SimpleNamespace(
        stop=lambda timeout: calls.append(("stop job", timeout)) or True,
        close=lambda: calls.append("close job"),
    )
    process = SimpleNamespace(is_alive=lambda: False, join=lambda: calls.append("join"))
    assert child_process.finish_worker_tree(
        process, job, 42, kill_group=lambda group: pytest.fail("group used")
    )
    assert calls == [("stop job", child_process.TREE_STOP_GRACE_S), "close job", "join"]


def test_worker_tree_reports_a_job_member_it_could_not_confirm_gone():
    closed = []
    job = SimpleNamespace(stop=lambda timeout: False, close=lambda: closed.append(True))
    process = SimpleNamespace(is_alive=lambda: False, join=lambda: None)
    assert not child_process.finish_worker_tree(process, job, None, kill_group=pytest.fail)
    assert closed == [True], "an unconfirmed job is still closed"


@pytest.mark.parametrize("survives", [False, True])
def test_worker_tree_waits_for_the_killed_group_to_be_gone(monkeypatch, survives):
    checks = []
    monkeypatch.setattr(
        child_process, "_process_group_alive", lambda group: checks.append(group) or survives
    )
    process = SimpleNamespace(is_alive=lambda: False, join=lambda: None)
    stopped = child_process.finish_worker_tree(
        process, None, 42, kill_group=lambda group: None, grace_seconds=0.05
    )
    assert stopped is not survives
    assert checks and set(checks) == {42}


def test_group_liveness_never_calls_killpg_off_posix(monkeypatch):
    """Windows has no ``os.killpg``; a group there is never polled, only reported gone."""

    monkeypatch.setattr(child_process.os, "name", "nt")
    monkeypatch.delattr(child_process.os, "killpg", raising=False)
    assert child_process._process_group_alive(42) is False


def test_an_unconfirmed_stop_is_a_developer_incident_not_user_text(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        child_process, "record_incident",
        lambda kind, phase, **kwargs: recorded.append((kind, phase, kwargs)),
    )
    child_process.record_unconfirmed_stop("bempp-stop", "the BEMPP worker", pid=9)
    assert recorded == [(
        "process-tree-survived", "bempp-stop",
        {"evidence": {"subject": "the BEMPP worker", "pid": 9}},
    )]


@pytest.mark.parametrize("error", [AssertionError, OSError, ValueError])
def test_unreadable_worker_exitcode_is_unknown(error):
    def join(_timeout):
        raise error()

    assert child_process.worker_exitcode(SimpleNamespace(join=join), join_seconds=2.0) is None


def test_windows_retry_changes_only_creation_flags(monkeypatch):
    monkeypatch.setattr(child_process, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(child_process.subprocess, "CREATE_NO_WINDOW", 8, raising=False)
    monkeypatch.setattr(child_process.subprocess, "CREATE_NEW_PROCESS_GROUP", 16, raising=False)
    calls = []
    result = object()

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            raise OSError(5, "access denied")
        return result

    monkeypatch.setattr(child_process.subprocess, "Popen", popen)
    kwargs = {"creationflags": 0x01000018, "env": {"allowed": "value"}, "close_fds": True}
    assert child_process.start_popen(["python"], kwargs, retry_windows=True) is result
    assert calls[0][1]["creationflags"] == 0x01000018
    assert (
        calls[1][1]
        == kwargs
        == {"creationflags": 24, "env": {"allowed": "value"}, "close_fds": True}
    )


@pytest.mark.parametrize("by_pid", [False, True])
def test_windows_job_limits_and_handle_ownership(monkeypatch, by_pid):
    calls = []

    class Function:
        def __init__(self, name, result):
            self.name, self.result = name, result

        def __call__(self, *args):
            calls.append((self.name, args))
            if self.name == "SetInformationJobObject":
                limits = args[2]._obj
                calls.append(
                    (
                        "limits",
                        (
                            limits.BasicLimitInformation.LimitFlags,
                            limits.BasicLimitInformation.ActiveProcessLimit,
                            limits.ProcessMemoryLimit,
                            limits.JobMemoryLimit,
                        ),
                    )
                )
            return self.result

    kernel = SimpleNamespace(
        **{
            name: Function(name, result)
            for name, result in [
                ("CreateJobObjectW", 123),
                ("SetInformationJobObject", True),
                ("OpenProcess", 456),
                ("AssignProcessToJobObject", True),
                ("TerminateJobObject", True),
                ("CloseHandle", True),
            ]
        }
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    options = (
        {"by_pid": True, "suppress_errors": True, "error_before_close": False}
        if by_pid
        else {
            "memory_bytes": 1024,
            "active_process_limit": 32,
        }
    )
    job = process_tree.create_windows_job(789, **options)
    assert ("limits", (0x2000, 0, 0, 0) if by_pid else (0x2308, 32, 1024, 1024)) in calls
    assert ("AssignProcessToJobObject", (123, 456 if by_pid else 789)) in calls
    if by_pid:
        assert ("CloseHandle", (456,)) in calls
    else:
        assert not any(c[0] == "CloseHandle" for c in calls)
    job.terminate()
    job.close()
    assert calls[-2:] == [("TerminateJobObject", (123, 1)), ("CloseHandle", (123,))]


@pytest.mark.parametrize("by_pid", [False, True])
@pytest.mark.parametrize(
    "failed_api", ["CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"]
)
def test_windows_job_failures_keep_error_capture_order_and_close_handles(
    monkeypatch, by_pid, failed_api
):
    closed = []
    error = [5]

    class Function:
        def __init__(self, name, result):
            self.name, self.result = name, result

        def __call__(self, *args):
            if self.name == "CloseHandle":
                closed.append(args[0])
                error[0] = 999
            return 0 if self.name == failed_api else self.result

    kernel = SimpleNamespace(
        **{
            name: Function(name, result)
            for name, result in [
                ("CreateJobObjectW", 123),
                ("SetInformationJobObject", True),
                ("OpenProcess", 456),
                ("AssignProcessToJobObject", True),
                ("TerminateJobObject", True),
                ("CloseHandle", True),
            ]
        }
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error[0], raising=False)
    with pytest.raises(OSError, match=f"{failed_api} failed") as caught:
        process_tree.create_windows_job(789, by_pid=by_pid, error_before_close=not by_pid)
    expected_error = 999 if by_pid and failed_api != "CreateJobObjectW" else 5
    assert caught.value.errno == expected_error
    expected_closed = [] if failed_api == "CreateJobObjectW" else [123]
    if by_pid and failed_api == "AssignProcessToJobObject":
        expected_closed.append(456)
    assert closed == expected_closed


@pytest.mark.parametrize("suppress_errors", [False, True])
def test_windows_job_cleanup_exception_policy(suppress_errors):
    def fail(*_args):
        raise OSError("cleanup failed")

    job = process_tree.WindowsJob(
        123,
        SimpleNamespace(TerminateJobObject=fail, CloseHandle=fail),
        suppress_errors=suppress_errors,
    )
    for action in (job.terminate, job.close):
        if suppress_errors:
            action()
        else:
            with pytest.raises(OSError, match="cleanup failed"):
                action()


def test_rss_sampler_keeps_command_and_group_aggregation(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="42 10\nother\n43 500\n42 20\n42 bad\n")

    monkeypatch.setattr(child_process.subprocess, "run", run)
    assert child_process.process_group_rss_bytes(42) == 30 * 1024
    assert calls == [
        (
            ["ps", "-A", "-o", "pgid=,rss="],
            {
                "capture_output": True,
                "text": True,
                "timeout": 5.0,
                "check": False,
            },
        )
    ]


@pytest.mark.skipif(os.name != "nt", reason="real Win32 job handles and pointer-sized ABI")
def test_windows_shared_job_can_hold_and_stop_a_real_child():
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    process_tree._declare_job_api(kernel, ctypes, wintypes)
    assert kernel.CreateJobObjectW.restype is wintypes.HANDLE
    assert kernel.AssignProcessToJobObject.argtypes == [wintypes.HANDLE, wintypes.HANDLE]
    process = child_process.start_popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        {"creationflags": child_process.windows_creation_flags(breakaway=False)},
        retry_windows=True,
    )
    job = None
    try:
        job = process_tree.create_windows_job(
            process, memory_bytes=1024**3, active_process_limit=32
        )
        child_process.terminate_process_tree(process, job, grace_seconds=0.1)
        assert process.wait(timeout=5) == 1
    finally:
        if job is not None:
            job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
