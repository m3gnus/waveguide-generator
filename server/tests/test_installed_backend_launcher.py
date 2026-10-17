"""Stable installed admission, shared server start, and cancellation contracts."""

import json
import os
from pathlib import Path
import signal
import subprocess
from dataclasses import replace

import pytest

from launchers import backend_start, desktop, ensure_backend as entry
from launchers.statusapp.controller import StatusController
from server.cadlink.fusion import fusion_delivery
from server.platform import backend_owner
from server.platform.backend_owner import BackendHandle


def handle(data):
    return BackendHandle("http://127.0.0.1:3199", 123, 3199, data.resolve(), "1.2", "abc")


def test_launcher_keeps_contained_fallback_disabled(tmp_path, monkeypatch, capsys):
    def ensure(root, argv, *, start_env, allow_contained=False):
        assert allow_contained is False
        return handle(root)
    monkeypatch.setattr(entry, "ensure_backend", ensure)
    assert entry.main(["--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


@pytest.mark.parametrize("contained", [False, True])
def test_launcher_reports_adopted_owner_lifetime_only_when_contained(tmp_path, monkeypatch, capsys, contained):
    found = replace(handle(tmp_path), lifetime="contained" if contained else "headless")
    monkeypatch.setattr(backend_owner, "_discover", lambda root: found)
    monkeypatch.setattr(backend_owner, "_spawn", lambda *a, **kw: pytest.fail("must adopt owner"))
    assert entry.main(["--data-dir", str(tmp_path)]) == 0
    expected = {
        "ok": True, "base_url": found.base_url, "pid": found.pid,
        "data_dir": str(found.data_dir), "version": found.version, "commit": found.commit,
    }
    if contained:
        expected["lifetime"] = "contained"
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == json.dumps(expected, separators=(",", ":")) + "\n"


@pytest.mark.parametrize("running", [True, False])
def test_entry_finds_or_starts_same_owner(tmp_path, monkeypatch, capsys, running):
    data = tmp_path / "data folder"
    calls = []
    monkeypatch.setenv("WG2_DATA_DIR", str(data))
    monkeypatch.setenv("WG2_PORT", "3199")
    found = handle(data)
    monkeypatch.setattr(backend_owner, "_discover", lambda root: found if running else None)

    def start(root, argv, deadline, env):
        calls.append((root, argv, env))
        return found

    monkeypatch.setattr(backend_owner, "_start_and_wait", start)
    assert entry.main([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "ok": True,
        "base_url": found.base_url,
        "pid": 123,
        "data_dir": str(data.resolve()),
        "version": "1.2",
        "commit": "abc",
    }
    assert len(calls) == (0 if running else 1)
    if calls:
        root, argv, env = calls[0]
        assert root == data.resolve()
        assert "--data-dir" not in argv  # The inherited env remains the explicit override.
        assert "--parent-pid" not in argv and "--status-control" not in argv
        assert "--lifetime=headless" in argv
        assert env["WG2_DATA_DIR"] == str(root)
        assert env["WG2_NO_BROWSER"] == env["PYTHONUNBUFFERED"] == "1"


@pytest.mark.parametrize(
    "error,code",
    [
        (backend_owner.BackendDataDirMismatch, 3),
        (backend_owner.BackendStartTimeout, 4),
        (backend_owner.BackendSpawnError, 5),
        (entry.BackendStartCancelled, 4),
    ],
)
def test_entry_errors_are_one_json_line(tmp_path, monkeypatch, capsys, error, code):
    def fail(*args, **kwargs):
        raise error("actionable reason")

    monkeypatch.setattr(entry, "ensure_backend", fail)
    assert entry.main(["--data-dir", str(tmp_path)]) == code
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": {"type": error.__name__, "message": "actionable reason"},
    }


def test_status_and_headless_share_start_argv_and_environment(tmp_path, monkeypatch):
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    if os.name == "nt":
        python.touch()
    else:
        python.symlink_to(Path(os.sys.executable))
    env = {"WG2_NO_BROWSER": "0", "WG2_UPDATE_STAGING_ROOT": "untrusted", "KEEP": "yes"}
    controller = StatusController(
        repo_root=tmp_path,
        python_executable=python,
        server_args=("--data-dir", str(tmp_path / "data")),
        environ=env,
    )
    args = backend_start.backend_argv(
        repo_root=tmp_path, python_executable=python, server_args=controller.server_args, port=3199
    )
    assert controller._command(3199, tmp_path / "stop") == args + [
        "--status-control",
        str(tmp_path / "stop"),
        "--parent-pid",
        str(os.getpid()),
    ]
    assert args[0] == str(python)  # Preserve the venv symlink.
    assert controller._server_environment() == backend_start.backend_environment(env)
    assert backend_start.backend_environment(env)["KEEP"] == "yes"
    # A pre-0.3.6 launcher's staging root never reaches the server.
    assert "WG2_UPDATE_STAGING_ROOT" not in backend_start.backend_environment(env)


@pytest.mark.parametrize("port", [None, 3199])
@pytest.mark.parametrize("server_command", [None, ("custom-python", "custom-serve")])
def test_shared_argv_omits_only_unspecified_port(tmp_path, port, server_command):
    python = tmp_path / "venv" / "bin" / "python"
    control = tmp_path / "stop"
    expected = list(server_command) if server_command is not None else [
        str(python), str(tmp_path / "launch" / "serve.py"),
    ]
    expected.extend(("--extra", "value", "--data-dir", str(tmp_path / "data"), "--no-browser"))
    if port is not None:
        expected.extend(("--port", str(port)))
    expected.extend(("--status-control", str(control), "--parent-pid", str(os.getpid())))
    assert backend_start.backend_argv(
        repo_root=tmp_path, python_executable=python,
        server_args=("--extra", "value"), server_command=server_command,
        port=port, data_dir=tmp_path / "data", control_path=control,
    ) == expected


@pytest.mark.parametrize(
    "platform,relative,launcher",
    [
        ("darwin", "WG.app/Contents/Resources/app", "WG.app/Contents/MacOS/Waveguide Generator"),
        ("win32", "WG/app", "WG/Waveguide Generator.exe"),
        ("linux", "WG/app", "WG/launch-wg.sh"),
        ("linux", "WG/app", "WG/waveguide-generator"),
    ],
)
def test_capabilities_name_own_installed_entry(tmp_path, monkeypatch, platform, relative, launcher):
    root, executable = tmp_path / relative, tmp_path / launcher
    root.mkdir(parents=True)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    if platform == "win32":
        signature = b"WG_NATIVE_ENSURE_BACKEND_V1"
        executable.write_bytes(signature)
        marker = executable.parent / "Waveguide Generator.ensure-backend-v1"
        marker.write_bytes(signature)
    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_APP_ROOT", str(root))
    monkeypatch.setattr(backend_start.sys, "platform", platform)
    assert fusion_delivery.capabilities()["launcher"] == {
        "argv": [str(executable)],
        "ensureBackend": True,
    }
    assert fusion_delivery.capabilities(solve_delivery=False)["launcher"]["ensureBackend"] is True
    fusion_delivery.advertise_fusion_delivery(tmp_path / "data")
    payload = json.loads((tmp_path / "data/ipc/wglink/wg-capabilities.json").read_text())
    assert payload["launcher"]["argv"] == [str(executable)]
    executable.unlink()
    assert "launcher" not in fusion_delivery.capabilities()


def test_checkout_does_not_advertise_embedded_python(tmp_path, monkeypatch):
    monkeypatch.delenv("WG2_BUNDLE", raising=False)
    monkeypatch.setenv("WG2_APP_ROOT", str(tmp_path))
    assert "launcher" not in fusion_delivery.capabilities()


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_desktop_dispatch_never_enters_gui(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(desktop.sys, "platform", platform)
    calls = []
    monkeypatch.setattr(entry, "main", lambda args: calls.append(args) or 4)
    monkeypatch.setattr(desktop, "parse_arguments", lambda args: pytest.fail("GUI parser entered"))
    assert desktop.main(["--ensure-backend", "--data-dir", str(tmp_path)]) == 4
    assert calls == [["--data-dir", str(tmp_path)]]


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("confirmed", [False, True])
def test_cancellation_reuses_owner_cleanup(tmp_path, monkeypatch, capsys, signum, confirmed):
    # A signal arriving during the health observation checks for an owner once
    # more before touching our child. No network or unrelated process is used.
    child = subprocess.Popen([os.sys.executable, "-c", "import time; time.sleep(60)"])
    found = BackendHandle("http://127.0.0.1:3199", child.pid, 3199, tmp_path, "1.2", "abc")
    observations = 0

    def discover(root):
        nonlocal observations
        observations += 1
        return found if confirmed and observations > 2 else None

    monkeypatch.setattr(backend_owner, "_discover", discover)
    monkeypatch.setattr(backend_owner, "_spawn", lambda *args: (child, tmp_path / "start.log"))

    def observe(root):
        signal.raise_signal(signum)
        return None, None

    monkeypatch.setattr(backend_owner, "_observe", observe)
    previous = signal.getsignal(signum)
    try:
        assert entry.main(["--data-dir", str(tmp_path)]) == 4
        assert json.loads(capsys.readouterr().out)["error"]["type"] == "BackendStartCancelled"
        assert signal.getsignal(signum) == previous
        assert (child.poll() is None) == confirmed
        # Start lock was released even on cancellation.
        lock = backend_owner._StartLock(tmp_path / "locks/backend-start.lock")
        lock.acquire(backend_owner.time.monotonic() + 1)
        lock.release()
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_cancel_while_waiting_for_start_lock_never_spawns(tmp_path, monkeypatch, capsys, signum):
    monkeypatch.setattr(backend_owner, "_discover", lambda root: None)
    monkeypatch.setattr(
        backend_owner,
        "_spawn",
        lambda *args: pytest.fail("spawned while another starter held the lock"),
    )
    closed = []
    real_close = os.close
    monkeypatch.setattr(
        backend_owner.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1]
    )

    def locked(fd):
        signal.raise_signal(signum)

    monkeypatch.setattr(backend_owner, "lock_exclusive", locked)
    assert entry.main(["--data-dir", str(tmp_path)]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "BackendStartCancelled"
    assert len(closed) == 1


def test_statusapp_routes_backend_mode_for_source_shell_launchers(monkeypatch):
    from launchers.statusapp import __main__ as status_entry

    calls = []
    monkeypatch.setattr(entry, "main", lambda argv: calls.append(argv) or 0)
    assert status_entry.main(["--ensure-backend", "--data-dir", "chosen"]) == 0
    assert calls == [["--data-dir", "chosen"]]


@pytest.mark.parametrize("parent_exited", [False, True])
def test_windows_native_parent_watcher_uses_verified_handle(monkeypatch, parent_exited):
    import _thread
    import ctypes
    import threading
    from types import SimpleNamespace

    stop = threading.Event()
    closed, cancelled = [], []

    def open_process(*args):
        pytest.fail("reopened a parent by a potentially reused pid")

    def wait(handle, timeout):
        assert handle == 456 and timeout == 100
        if not parent_exited:
            stop.set()
        return 0 if parent_exited else 258

    def close(handle):
        closed.append(handle)

    kernel = SimpleNamespace(OpenProcess=open_process, WaitForSingleObject=wait, CloseHandle=close)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(entry.sys, "_wg_native_parent_handle", 456, raising=False)
    monkeypatch.setattr(_thread, "interrupt_main", cancelled.append)
    entry._watch_native_parent(stop)
    assert closed == [456]
    assert cancelled == ([signal.SIGTERM] if parent_exited else [])


def test_cancel_terminates_own_unconfirmed_child_when_another_owner_is_healthy(
    tmp_path, monkeypatch
):
    child = subprocess.Popen([os.sys.executable, "-c", "import time; time.sleep(60)"])
    owner = subprocess.Popen([os.sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(backend_owner, "_spawn", lambda *args: (child, tmp_path / "start.log"))
    monkeypatch.setattr(
        backend_owner,
        "_discover",
        lambda root: BackendHandle(
            "http://127.0.0.1:3199", owner.pid, 3199, tmp_path, "1.2", "abc"
        ),
    )

    def cancelled(root):
        raise entry.BackendStartCancelled("cancelled")

    monkeypatch.setattr(backend_owner, "_observe", cancelled)
    try:
        with pytest.raises(entry.BackendStartCancelled):
            backend_owner._start_and_wait(
                tmp_path, ["fake backend"], backend_owner.time.monotonic() + 1
            )
        assert child.poll() is not None
        assert owner.poll() is None
    finally:
        for process in (child, owner):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("argv", [["--data-dir"], ["--unknown"], ["--no-gui"], ["--help"], ["-h"], ["--data-dir", "relative"]])
def test_entry_bad_arguments_use_json_error_and_spawn_code(capsys, argv, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entry, "ensure_backend", lambda *args, **kwargs: pytest.fail("invalid arguments reached backend startup"))
    assert entry.main(argv) == 5
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["error"]["type"] == "ValueError"


@pytest.mark.parametrize("member", [False, True])
def test_windows_job_checks_confirmed_backend_membership(member):
    from types import SimpleNamespace
    from server.platform.process_tree import WindowsJob

    opened, closed = [], []

    def open_process(*args):
        opened.append(args)
        return 9

    def is_in_job(process, job, result):
        assert (process, job) == (9, 42)
        result._obj.value = member
        return True

    kernel = SimpleNamespace(
        OpenProcess=open_process, IsProcessInJob=is_in_job, CloseHandle=closed.append
    )
    assert WindowsJob(42, kernel).contains(123) == member
    assert opened == [(0x1000, False, 123)]
    assert closed == [9]


@pytest.mark.parametrize("member", [False, True])
def test_cancel_preserves_only_confirmed_backend_inside_windows_stub_job(
    tmp_path, monkeypatch, member
):
    from types import SimpleNamespace

    terminated, closed, reaped = [], [], []
    job = SimpleNamespace(
        contains=lambda pid: member,
        terminate=lambda: terminated.append("job"),
        close=lambda: closed.append("job"),
    )
    child = SimpleNamespace(
        pid=10,
        _wg_start_job=job,
        poll=lambda: None,
        terminate=lambda: terminated.append("stub"),
        wait=lambda **kwargs: reaped.append("stub"),
    )
    monkeypatch.setattr(backend_owner, "_spawn", lambda *args: (child, tmp_path / "start.log"))
    monkeypatch.setattr(backend_owner, "_discover", lambda root: handle(tmp_path))

    def cancelled(root):
        raise entry.BackendStartCancelled("cancelled")

    monkeypatch.setattr(backend_owner, "_observe", cancelled)
    with pytest.raises(entry.BackendStartCancelled):
        backend_owner._start_and_wait(
            tmp_path, ["fake backend"], backend_owner.time.monotonic() + 1
        )
    assert terminated == ([] if member else ["job", "stub"])
    assert reaped == ([] if member else ["stub"])
    assert closed == ["job"]
