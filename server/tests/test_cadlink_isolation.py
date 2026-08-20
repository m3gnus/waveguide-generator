"""The untrusted-STEP process boundary, demonstrated rather than asserted.

Each test here corresponds to a line in the acceptance list of
``docs/plans/STEP-PARSER-ISOLATION.md``.  They drive the real harness -- real
``subprocess``, real deadline, real process-group kill -- with a child that
misbehaves on purpose (``server/tests/isolation_doubles.py``), because a gate
that is only unit-tested against a mock is not a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import pytest

from server.cadlink import isolation
from server.cadlink.isolation import (
    ChildBudget,
    ChildRefusal,
    child_environment,
    isolated_step_task,
    process_group_rss_bytes,
)
from server.cadlink.limits import MAX_STEP_INPUT_BYTES


DOUBLE = "server.tests.isolation_doubles"
_MINIMAL_STEP = (
    b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nENDSEC;\n"
    b"DATA;\n#1=CARTESIAN_POINT('p',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n"
)


@pytest.fixture
def step_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.step"
    path.write_bytes(_MINIMAL_STEP)
    return path


def _budget(**overrides: float | int) -> ChildBudget:
    defaults: dict[str, float | int] = {
        "wall_time_s": 30.0,
        "memory_bytes": 2 * 1024**3,
        "result_bytes": 64 * 1024,
        "artifact_bytes": 64 * 1024,
    }
    defaults.update(overrides)
    return ChildBudget(**defaults)  # type: ignore[arg-type]


@dataclass(frozen=True)
class _Snapshot:
    """What survives the harness context, since staging does not."""

    result: dict
    artifacts: dict[str, bytes]
    diagnostics: str


def _run(
    step: Path, behaviour: str, *, budget: ChildBudget | None = None, **payload: object
) -> _Snapshot:
    with isolated_step_task(
        "mesh",
        {"misbehaviour": behaviour, **payload},
        step_path=step,
        budget=budget or _budget(),
        allowed_artifacts=("mesh.msh",),
        stage="stage 7 meshing",
        entrypoint=DOUBLE,
    ) as outcome:
        return _Snapshot(
            result=outcome.result,
            artifacts={name: path.read_bytes() for name, path in outcome.artifacts.items()},
            diagnostics=outcome.diagnostics,
        )


# -- the happy path the rest of the tests are a deviation from ---------------


def test_a_well_behaved_child_hands_back_a_verified_artifact(step_file: Path) -> None:
    outcome = _run(step_file, "ok")
    assert outcome.result == {"built": {"ok": True}}
    assert set(outcome.artifacts) == {"mesh.msh"}
    assert outcome.artifacts["mesh.msh"].startswith(b"$MeshFormat")


def test_staging_is_destroyed_when_the_invocation_ends(step_file: Path) -> None:
    with isolated_step_task(
        "mesh",
        {"misbehaviour": "ok"},
        step_path=step_file,
        budget=_budget(),
        allowed_artifacts=("mesh.msh",),
        stage="stage 7 meshing",
        entrypoint=DOUBLE,
    ) as outcome:
        staged = outcome.artifacts["mesh.msh"]
        assert staged.is_file()
    assert not staged.exists()
    assert not staged.parent.exists()


# -- deadline and process-tree termination -----------------------------------


def test_a_hanging_child_and_its_descendants_are_killed_at_the_deadline(
    step_file: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "descendant.txt"
    started = time.monotonic()
    with pytest.raises(ChildRefusal, match="deadline"):
        _run(step_file, "hang", budget=_budget(wall_time_s=3.0), descendant_marker=str(marker))
    elapsed = time.monotonic() - started
    assert elapsed < 20.0, "the deadline did not stop the child promptly"

    # The grandchild is what a naive terminate() would leave running.
    assert marker.is_file(), "the double never started its descendant"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _any_descendant_alive(marker):
            break
        time.sleep(0.2)
    assert not _any_descendant_alive(marker), "a descendant outlived the process-tree kill"


def test_descendants_are_killed_before_a_clean_child_result_is_trusted(
    step_file: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "clean-exit-descendant.txt"
    outcome = _run(
        step_file,
        "clean_exit_with_descendant",
        descendant_marker=str(marker),
    )
    assert outcome.result == {"built": {"ok": True}}
    assert marker.is_file(), "the double never started its descendant"
    assert not _any_descendant_alive(marker), "a clean-exit descendant survived verification"


def _any_descendant_alive(marker: Path) -> bool:
    """True while the child PID recorded in the marker still exists."""

    try:
        pid = int(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return _pid_is_alive(pid)


def _pid_is_alive(pid: int) -> bool:
    if platform.system() != "Windows":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_pid_markers(*markers: Path, timeout: float = 15.0) -> list[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(marker.is_file() for marker in markers):
            return [int(marker.read_text(encoding="utf-8")) for marker in markers]
        time.sleep(0.05)
    return []


def _wait_for_pids_to_exit(pids: list[int], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [pid for pid in pids if _pid_is_alive(pid)]
        if not alive:
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(platform.system() != "Windows", reason="job objects are Windows-only")
def test_windows_kill_on_job_close_removes_the_tree_when_the_parent_dies(
    step_file: Path, tmp_path: Path
) -> None:  # pragma: no cover - Windows only
    """The child has no Windows parent watchdog; the job handle is the gate."""

    child_marker = tmp_path / "child-pid.txt"
    grandchild_marker = tmp_path / "grandchild-pid.txt"
    repo_root = Path(__file__).resolve().parents[2]
    probe = "\n".join(
        [
            "import sys",
            "from server.cadlink.isolation import ChildBudget, isolated_step_task",
            "with isolated_step_task(",
            "    'mesh',",
            "    {'misbehaviour': 'hang', 'child_marker': sys.argv[2], "
            "'descendant_marker': sys.argv[3]},",
            "    step_path=sys.argv[1],",
            "    budget=ChildBudget(wall_time_s=600.0, memory_bytes=512 * 1024**2, "
            "result_bytes=64 * 1024, artifact_bytes=64 * 1024),",
            "    allowed_artifacts=('mesh.msh',),",
            "    stage='stage 7 meshing',",
            "    entrypoint='server.tests.isolation_doubles',",
            "):",
            "    pass",
        ]
    )
    parent = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-s",
            "-B",
            "-c",
            probe,
            str(step_file),
            str(child_marker),
            str(grandchild_marker),
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pids: list[int] = []
    try:
        pids = _wait_for_pid_markers(child_marker, grandchild_marker)
        assert len(pids) == 2, "the isolated child tree did not start"
        assert all(_wait_for_pids_to_exit([pid], timeout=0.01) is False for pid in pids)

        parent.kill()
        parent.wait(timeout=10)
        assert _wait_for_pids_to_exit(pids), "the parent's closed job left an orphan"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        for pid in pids:
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def test_a_normal_import_succeeds_after_a_deadline_kill(step_file: Path, tmp_path: Path) -> None:
    """The gate's wording: killed at the deadline, *after which* a normal import works.

    This is really a test of the concurrency slot: a refusal that forgot to
    release it would deadlock the very next import rather than fail it.
    """

    with pytest.raises(ChildRefusal):
        _run(
            step_file,
            "hang",
            budget=_budget(wall_time_s=2.0),
            descendant_marker=str(tmp_path / "descendant.txt"),
        )
    assert _run(step_file, "ok").result == {"built": {"ok": True}}


# -- native crash ------------------------------------------------------------


def test_a_native_crash_becomes_a_refusal_and_leaves_the_parent_healthy(
    step_file: Path,
) -> None:
    with pytest.raises(ChildRefusal, match="without writing a structured result"):
        _run(step_file, "crash")
    # The parent is a normal, working process afterwards; a SIGABRT inside the
    # boundary is data about one file, not an event in this interpreter.
    assert _run(step_file, "ok").result == {"built": {"ok": True}}


# -- result and artifact limits ----------------------------------------------


def test_an_oversized_child_result_is_refused_at_its_stated_boundary(
    step_file: Path,
) -> None:
    with pytest.raises(ChildRefusal, match="byte limit for a child's structured result"):
        _run(
            step_file,
            "oversized_result",
            budget=_budget(result_bytes=4096),
            padding_bytes=16384,
        )


def test_an_oversized_artifact_is_refused_at_its_stated_boundary(step_file: Path) -> None:
    with pytest.raises(ChildRefusal, match="byte limit for one staged artifact"):
        _run(
            step_file,
            "oversized_artifact",
            budget=_budget(artifact_bytes=1024),
            artifact_bytes=4096,
        )


@pytest.mark.parametrize(
    ("behaviour", "message"),
    [
        ("not_json", "not readable JSON"),
        ("wrong_protocol", "declares protocol"),
        ("no_result", "without writing a structured result"),
        ("nonfinite", "not readable JSON"),
        ("checksum_mismatch", "declared checksum"),
        ("undeclared_artifact", "undeclared file"),
    ],
)
def test_a_malformed_child_answer_is_refused(
    step_file: Path, behaviour: str, message: str
) -> None:
    with pytest.raises(ChildRefusal, match=message):
        _run(step_file, behaviour)


def test_an_artifact_name_outside_the_allowed_set_is_refused(step_file: Path) -> None:
    with pytest.raises(ChildRefusal, match="unexpected artifact"):
        with isolated_step_task(
            "mesh",
            {"misbehaviour": "unexpected_artifact_name"},
            step_path=step_file,
            budget=_budget(),
            allowed_artifacts=("viewport.msh",),
            stage="stage 7 meshing",
            entrypoint=DOUBLE,
        ):
            pass


# -- escape attempts ---------------------------------------------------------


def test_an_absolute_artifact_path_cannot_escape_staging(
    step_file: Path, tmp_path: Path
) -> None:
    escape = tmp_path / "escaped.msh"
    with pytest.raises(ChildRefusal, match="unsafe artifact name"):
        _run(step_file, "absolute_artifact", escape_target=str(escape))


def test_a_traversing_artifact_path_cannot_escape_staging(step_file: Path) -> None:
    with pytest.raises(ChildRefusal, match="unsafe artifact name"):
        _run(step_file, "traversal_artifact")


def test_a_symlinked_artifact_cannot_smuggle_a_file_out_of_staging(
    step_file: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    with pytest.raises(ChildRefusal, match="symlink"):
        _run(step_file, "symlink_artifact", escape_target=str(secret))


# -- child authority ---------------------------------------------------------


def test_the_child_inherits_no_credentials_and_no_data_directory(
    step_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "must-not-leak")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "must-not-leak-either")
    monkeypatch.setenv("WG_DATA_DIR", "/somewhere/private")
    monkeypatch.setenv("DATABASE_URL", "postgres://nope")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\real-profile")
    monkeypatch.setenv("APPDATA", r"C:\Users\real-profile\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\real-profile\AppData\Local")

    outcome = _run(step_file, "report_environment")
    environment = outcome.result["environment"]

    assert "ONSHAPE_ACCESS_KEY" not in environment
    assert "ONSHAPE_SECRET_KEY" not in environment
    assert "WG_DATA_DIR" not in environment
    assert "DATABASE_URL" not in environment
    assert "must-not-leak" not in json.dumps(environment)
    # A library hunting the platform's profile directories sees only the
    # child's empty staging home, never the real user's credential store.
    if platform.system() == "Windows":
        assert Path(environment["USERPROFILE"]).parts[-2:] == ("staging", "home")
        assert Path(environment["APPDATA"]).parts[-4:] == (
            "staging",
            "home",
            "AppData",
            "Roaming",
        )
        assert Path(environment["LOCALAPPDATA"]).parts[-4:] == (
            "staging",
            "home",
            "AppData",
            "Local",
        )
        assert "real-profile" not in json.dumps(environment)
        assert Path(environment["TMPDIR"]).parts[-2:] == ("staging", "tmp")
    else:
        assert environment.get("HOME", "").endswith("staging/home")
        assert environment["TMPDIR"].endswith("staging/tmp")
    assert Path(outcome.result["cwd"]).parts[-2:] == ("staging", "tmp")


def test_the_child_reads_the_checksum_bound_copy_not_the_original(
    step_file: Path,
) -> None:
    outcome = _run(step_file, "report_environment")
    assert outcome.result["source_head"].startswith("ISO-10303-21;")


def test_the_child_runs_in_its_own_session_so_the_group_is_the_tree(
    step_file: Path,
) -> None:
    if os.name != "posix":  # pragma: no cover - POSIX-only assertion
        pytest.skip("process sessions are a POSIX concept")
    outcome = _run(step_file, "report_environment")
    assert outcome.result["session_leader"] is True


def test_python_level_network_access_is_refused_inside_the_child(
    step_file: Path,
) -> None:
    """Only true for children that go through the real entrypoint's confinement.

    The double deliberately applies none, so this asserts on the production
    child instead: it is the one that ever touches a STEP. Confinement is keyed
    on the harness's environment marker, because it is process-wide and
    irreversible -- importing the child module in the server must not take the
    server's own sockets away.
    """

    repo_root = Path(__file__).resolve().parents[2]
    probe = (
        "import sys, socket\n"
        "import server.cadlink.child_main  # noqa: F401 - imported for its confinement\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1', 9), timeout=0.2)\n"
        "except OSError as exc:\n"
        "    sys.stdout.write(str(exc))\n"
    )
    refused = subprocess.run(
        [sys.executable, "-s", "-B", "-c", probe],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(repo_root),
            "WG_ISOLATED_CAD_CHILD": "1",
        },
        check=False,
    )
    assert "not allowed to use the network" in refused.stdout

    # And the same module imported without the marker leaves sockets alone, so
    # inspecting it from the server does not disarm the server.
    unconfined = subprocess.run(
        [sys.executable, "-s", "-B", "-c", probe],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(repo_root)},
        check=False,
    )
    assert "not allowed to use the network" not in unconfined.stdout


def test_native_chatter_is_retained_only_as_a_bounded_tail(step_file: Path) -> None:
    outcome = _run(step_file, "noisy_then_ok", noise_lines=20000)
    assert outcome.result == {"fine": True}
    assert len(outcome.diagnostics.encode("utf-8")) <= 9 * 1024
    assert "earlier output discarded" in outcome.diagnostics


# -- the environment builder, in isolation from any process ------------------


def test_the_child_environment_is_an_allowlist_on_every_platform(tmp_path: Path) -> None:
    hostile = {
        "PATH": "/usr/bin",
        "ONSHAPE_ACCESS_KEY": "k",
        "AWS_SECRET_ACCESS_KEY": "k",
        "WG_DATA_DIR": "/data",
        "SYSTEMROOT": r"C:\Windows",
        "HOME": "/Users/someone",
        "USERPROFILE": r"C:\Users\someone",
    }
    for system in ("Darwin", "Linux", "Windows"):
        env = child_environment(tmp_path / "staging", environ=hostile, system=system)
        assert env["PATH"] == "/usr/bin"
        assert "ONSHAPE_ACCESS_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "WG_DATA_DIR" not in env
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert env["TEMP"].endswith("tmp")
        if system == "Windows":
            assert env["USERPROFILE"] != r"C:\Users\someone"
        else:
            assert env["HOME"] != "/Users/someone"


# -- memory containment ------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="the RSS sampler shells out to ps")
def test_resident_memory_is_sampled_and_over_budget_is_contained(step_file: Path) -> None:
    """Containment on macOS and Linux.

    ``RLIMIT_AS`` is not the mechanism -- a Python process with gmsh imported
    reserves hundreds of gigabytes of address space against tens of megabytes
    resident, so an address-space cap at the gate's resident figure refuses
    every legitimate import. The parent samples resident memory instead, which
    is the number the gate actually names. Windows gets the same containment
    from the job object's commit limit, which this platform cannot exercise.
    """

    assert process_group_rss_bytes(os.getpgid(0)) is not None
    with pytest.raises(ChildRefusal, match="resident"):
        _run(
            step_file,
            "memory_hog",
            budget=_budget(wall_time_s=60.0, memory_bytes=192 * 1024 * 1024),
        )


@pytest.mark.skipif(platform.system() != "Windows", reason="job objects are Windows-only")
def test_windows_contains_memory_with_a_job_object(step_file: Path) -> None:  # pragma: no cover
    started = time.monotonic()
    with pytest.raises(ChildRefusal) as caught:
        _run(
            step_file,
            "memory_hog",
            budget=_budget(wall_time_s=60.0, memory_bytes=192 * 1024 * 1024),
        )
    assert caught.value.stage == "stage 7 meshing"
    assert time.monotonic() - started < 20.0, "the kernel memory cap did not stop the child"
    assert _run(step_file, "ok").result == {"built": {"ok": True}}


@pytest.mark.skipif(platform.system() != "Windows", reason="job objects are Windows-only")
def test_windows_job_api_uses_pointer_sized_signatures() -> None:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    isolation._configure_windows_job_api(kernel32, ctypes, wintypes)

    assert kernel32.CreateJobObjectW.restype is wintypes.HANDLE
    assert kernel32.CreateJobObjectW.argtypes == [ctypes.c_void_p, wintypes.LPCWSTR]
    assert kernel32.SetInformationJobObject.argtypes[0] is wintypes.HANDLE
    assert kernel32.SetInformationJobObject.argtypes[2] is ctypes.c_void_p
    assert kernel32.AssignProcessToJobObject.argtypes == [
        wintypes.HANDLE,
        ctypes.c_void_p,
    ]
    assert kernel32.TerminateJobObject.argtypes[0] is wintypes.HANDLE
    assert kernel32.CloseHandle.argtypes == [wintypes.HANDLE]


@pytest.mark.skipif(platform.system() != "Windows", reason="job objects are Windows-only")
def test_windows_child_waits_for_its_envelope_until_after_job_assignment(
    tmp_path: Path,
) -> None:  # pragma: no cover - Windows only
    staging = tmp_path / "staging"
    for directory in (staging / "tmp", staging / "home"):
        directory.mkdir(parents=True)
    # Launch through the production helper, not a hand-rolled Popen: a runner
    # inside a job object refuses CREATE_BREAKAWAY_FROM_JOB, and reimplementing
    # only the first attempt here made this test fail on hosted Windows CI while
    # the product itself retried correctly.
    process = isolation.start_child_process(
        [sys.executable, "-s", "-B", "-m", "server.cadlink.child_main"],
        {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "cwd": staging / "tmp",
            "env": child_environment(staging),
            "close_fds": True,
            "creationflags": isolation._windows_creation_flags(),
        },
        stage="test",
    )
    started = time.monotonic()
    job = isolation._assign_windows_job(process, _budget())
    assigned_in = time.monotonic() - started
    try:
        time.sleep(0.2)
        assert process.poll() is None, "the child ran before it received an envelope"
        assert int(job._handle) > 0
        assert assigned_in < 2.0, "job assignment left a material unconfined window"
    finally:
        isolation.terminate_process_tree(process, job)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        job.close()


@pytest.mark.skipif(platform.system() != "Windows", reason="job objects are Windows-only")
def test_windows_breakaway_failure_retries_and_remains_memory_contained(
    step_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # pragma: no cover - Windows only
    """The first launch failure is injected; the fallback child and job are real."""

    real_popen = subprocess.Popen
    attempts: list[int] = []

    def popen_with_forbidden_breakaway(*args: object, **kwargs: object) -> object:
        flags = int(kwargs.get("creationflags", 0))
        attempts.append(flags)
        if len(attempts) == 1 and flags & 0x01000000:
            raise OSError(5, "the parent job forbids breakaway")
        return real_popen(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(isolation.subprocess, "Popen", popen_with_forbidden_breakaway)
    started = time.monotonic()
    with pytest.raises(ChildRefusal) as caught:
        _run(
            step_file,
            "memory_hog",
            budget=_budget(wall_time_s=60.0, memory_bytes=192 * 1024 * 1024),
        )
    assert attempts[0] & 0x01000000
    assert attempts[1] & 0x01000000 == 0
    assert caught.value.stage == "stage 7 meshing"
    assert "could not confine" not in str(caught.value)
    assert time.monotonic() - started < 20.0


def test_windows_job_assignment_failure_stops_the_uncontained_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = object()
    stopped: list[object] = []
    monkeypatch.setattr(isolation, "_assign_windows_job", lambda *_args: None)
    monkeypatch.setattr(
        isolation,
        "terminate_process_tree",
        lambda child, _job=None, **_kwargs: stopped.append(child),
    )

    with pytest.raises(ChildRefusal, match="could not confine.*Windows job"):
        isolation._required_windows_job(  # type: ignore[arg-type]
            process, _budget(), stage="stage 7 meshing"
        )
    assert stopped == [process]


# -- typed refusals survive the boundary -------------------------------------


def test_a_child_refusal_keeps_its_wording_and_its_remedy(step_file: Path) -> None:
    with pytest.raises(ChildRefusal) as caught:
        _run(
            step_file,
            "refuse",
            error_type="RoleResolutionError",
            error_message="role resolution: two sources claim face 7",
            area_drift_sources=["source-hf"],
        )
    assert "two sources claim face 7" in caught.value.detail
    assert caught.value.error_type == "RoleResolutionError"
    assert caught.value.details["area_drift_sources"] == ["source-hf"]


def test_a_missing_source_step_is_refused_before_any_child_starts(tmp_path: Path) -> None:
    with pytest.raises(ChildRefusal, match="not a regular file"):
        _run(tmp_path / "absent.step", "ok")


def test_step_staging_enforces_its_size_ceiling_before_starting_a_child(
    tmp_path: Path,
) -> None:
    oversized = tmp_path / "oversized.step"
    with oversized.open("wb") as handle:
        handle.truncate(MAX_STEP_INPUT_BYTES + 1)
    with pytest.raises(ChildRefusal, match="limit for one STEP input"):
        _run(oversized, "ok")


def test_verified_step_size_and_hash_are_rechecked_during_staging(
    step_file: Path,
) -> None:
    verified = step_file.read_bytes()
    expected_hash = "sha256:" + hashlib.sha256(verified).hexdigest()
    expected_size = len(verified)

    # A same-sized replacement defeats a stat-only check but not the verified
    # digest carried from the bundle reader.
    step_file.write_bytes(b"X" * expected_size)
    with pytest.raises(ChildRefusal, match="checksum changed after bundle verification"):
        with isolated_step_task(
            "mesh",
            {"misbehaviour": "ok"},
            step_path=step_file,
            budget=_budget(),
            allowed_artifacts=("mesh.msh",),
            stage="stage 7 meshing",
            entrypoint=DOUBLE,
            expected_sha256=expected_hash,
            expected_size_bytes=expected_size,
        ):
            pass

    step_file.write_bytes(verified + b"changed")
    with pytest.raises(ChildRefusal, match="expected .* bytes, found"):
        with isolated_step_task(
            "mesh",
            {"misbehaviour": "ok"},
            step_path=step_file,
            budget=_budget(),
            allowed_artifacts=("mesh.msh",),
            stage="stage 7 meshing",
            entrypoint=DOUBLE,
            expected_sha256=expected_hash,
            expected_size_bytes=expected_size,
        ):
            pass


def test_only_one_external_step_child_runs_at_a_time(step_file: Path) -> None:
    """The gate allows exactly one concurrent external-STEP child.

    Two threads race into the harness; the overlap counter proves the second
    waited rather than doubling the memory and CPU an ingest can claim.
    """

    import threading

    from server.cadlink.limits import MAX_CONCURRENT_STEP_CHILDREN

    assert MAX_CONCURRENT_STEP_CHILDREN == 1

    inside = 0
    peak = 0
    guard = threading.Lock()
    failures: list[BaseException] = []

    def run() -> None:
        nonlocal inside, peak
        try:
            with isolated_step_task(
                "mesh",
                {"misbehaviour": "noisy_then_ok", "noise_lines": 200},
                step_path=step_file,
                budget=_budget(),
                allowed_artifacts=("mesh.msh",),
                stage="stage 7 meshing",
                entrypoint=DOUBLE,
            ):
                with guard:
                    inside += 1
                    peak = max(peak, inside)
                time.sleep(0.3)
                with guard:
                    inside -= 1
        except BaseException as exc:  # noqa: BLE001 - reported on the main thread
            failures.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=90)

    assert not failures, failures
    assert peak == 1


def test_numpy_values_cross_the_boundary_as_plain_json(tmp_path: Path) -> None:
    """The mesher's records carry numpy scalars; JSON does not.

    Importing the child entrypoint here is safe on purpose: its confinement is
    keyed on the harness's environment marker, so reading the module in the
    server does not confine the server.
    """

    import numpy as np

    from server.cadlink import child_main

    result = tmp_path / "result.json"
    child_main._write_result(
        result,
        {
            "protocol": 1,
            "ok": True,
            "result": {"triangles": np.int64(7), "valid": np.bool_(True)},
        },
    )
    assert json.loads(result.read_text(encoding="utf-8"))["result"] == {
        "triangles": 7,
        "valid": True,
    }

    with pytest.raises(ValueError, match="Out of range float"):
        child_main._write_result(
            tmp_path / "nan.json", {"protocol": 1, "value": np.float64("nan")}
        )


@pytest.mark.skipif(os.name != "posix", reason="Windows gets this from KILL_ON_JOB_CLOSE")
def test_a_child_exits_when_its_parent_goes_away() -> None:
    """The child owns a fresh session, so nothing signals it when the parent dies.

    That is the price of having a clean process group to kill, and the watchdog
    is what pays it: an orphaned child still holding a STEP and a gigabyte of
    OCC is exactly the leak this boundary exists to prevent.
    """

    repo_root = Path(__file__).resolve().parents[2]
    # The grandchild's stdout goes to /dev/null, not to the inherited pipe:
    # otherwise this test waits on the very process it is trying to outlive.
    launcher = (
        "import subprocess, sys\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-s', '-B', '-c',\n"
        "     'import server.cadlink.child_main as m; import time;"
        " m._watch_parent(); time.sleep(120)'],\n"
        "    start_new_session=True,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "print(child.pid, flush=True)\n"
        # Outlive the child's first moments: the watchdog notices a *change* of
        # parent, so it has to observe the real one before it disappears. A
        # real server is long-lived, which is why this is a test artefact.
        "import time; time.sleep(3)\n"
    )
    launched = subprocess.run(
        [sys.executable, "-s", "-B", "-c", launcher],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(repo_root),
            "WG_ISOLATED_CAD_CHILD": "1",
        },
        check=True,
        timeout=60,
    )
    orphan = int(launched.stdout.strip())

    # The launcher has exited, so the watchdog's next poll sees a new ppid.
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if not _pid_alive(orphan):
            break
        time.sleep(0.25)
    alive = _pid_alive(orphan)
    if alive:  # pragma: no cover - only on failure
        os.kill(orphan, 9)
    assert not alive, "the child outlived its parent"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
