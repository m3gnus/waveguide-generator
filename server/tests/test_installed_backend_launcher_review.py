"""Regressions for installed admission review findings."""

import ctypes
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from launchers import backend_start, ensure_backend as entry
from launchers.statusapp.controller import StatusController
from server.platform import backend_owner, process_tree
from server.platform.paths import migrate_legacy_data_dir, resolve_data_dir, default_runs_dir
from server.tests import backend_breakaway
from server.tests.backend_breakaway import (
    WINDOWS_BREAKAWAY_REFUSAL, backend_breakaway_permitted,
    backend_spawn_refusal_message, require_backend_breakaway,
)


def isolate_home(tmp_path, monkeypatch):
    for key in ("WG2_DATA_DIR", "WG2_BUNDLE", "WG2_APP_ROOT", "XDG_DATA_HOME", "XDG_DOCUMENTS_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData/Roaming"))
    monkeypatch.setenv("WG2_PORT", "3100")


def test_default_admission_migrates_before_lock_and_preserves_gui_arguments(tmp_path, monkeypatch, capsys):
    isolate_home(tmp_path, monkeypatch)
    root = resolve_data_dir()
    legacy = root.with_name("WaveguideGenerator2")
    legacy.mkdir(parents=True)
    (legacy / "old-settings").write_text("retained")
    monkeypatch.setattr(entry, "resolve_bundle_paths", lambda *args: None)
    controller = StatusController(environ=dict(os.environ))
    gui_args = controller._command(3100, tmp_path / "control")

    def start(data, argv, deadline, env):
        assert not legacy.exists()
        assert (data / "old-settings").read_text() == "retained"
        assert "--data-dir" not in argv
        assert "WG2_DATA_DIR" not in env
        assert argv == gui_args[:gui_args.index("--status-control")] + ["--lifetime=headless"]
        return backend_owner.BackendHandle("http://127.0.0.1:3100", 123, 3100, data, "1", "abc")

    monkeypatch.setattr(backend_owner, "_discover", lambda *args: None)
    monkeypatch.setattr(backend_owner, "_start_and_wait", start)
    assert entry.main([]) == 0
    assert json.loads(capsys.readouterr().out)["data_dir"] == str(root.resolve())


@pytest.mark.parametrize("legacy_exports", [False, True])
def test_default_admission_runs_supported_app_with_gui_workspace_defaults(tmp_path, monkeypatch, capsys, legacy_exports, retire_event):
    """Exercise real serve/app routes with a socket-free Uvicorn driver."""
    from launch import serve
    from server import app as app_module

    isolate_home(tmp_path, monkeypatch)
    exports = tmp_path / "legacy-exports"
    if legacy_exports:
        (exports / "old-run").mkdir(parents=True)
    monkeypatch.setattr(app_module, "LEGACY_WORKSPACE_DIR", exports)
    for name in ("setup_logging", "flush_logs", "_release_interface_error", "_no_gui_healthy_start",
                 "_start_temporary_session", "_start_beat_cpu_provisioning", "harden_console"):
        monkeypatch.setattr(serve, name, lambda *args: None)
    monkeypatch.setattr(serve, "InstanceLock", lambda *args: SimpleNamespace(
        acquire=lambda *args: None, update_port=lambda *args: None, release=lambda: None,
    ))
    listener = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(serve, "reserve_port", lambda *args, **kwargs: (listener, 3100))
    observed = []
    class Driver:
        def __init__(self, config):
            self.app = getattr(config.app, "app", config.app)
        def run(self, **kwargs):
            def route(path):
                return next(item.endpoint for item in self.app.routes if item.path == path)
            observed.append((asyncio.run(route("/health")()),
                             asyncio.run(route("/api/workspace/path")())))
    monkeypatch.setattr(serve.uvicorn, "Server", Driver)
    assert serve.main(["--no-browser"]) == 0
    root = resolve_data_dir()
    assert Path(observed[0][1]["path"]) == (exports if legacy_exports else default_runs_dir())
    # Keep the data migration evidence, but discard any saved workspace choice
    # so the second start must also perform its own legacy-export adoption.
    (root / "workspace_settings.json").unlink(missing_ok=True)
    (root / "folders.json").unlink(missing_ok=True)  # the WG root records the choice too
    legacy_data = root.with_name("WaveguideGenerator2")
    root.rename(legacy_data)
    (legacy_data / "retained").write_text("old settings")
    monkeypatch.setattr(entry, "resolve_bundle_paths", lambda *args: None)
    monkeypatch.setattr(backend_owner, "_discover", lambda *args: None)
    def start(data, argv, deadline, env):
        with monkeypatch.context() as patch:
            for key, value in env.items():
                patch.setenv(key, value)
            assert serve.main(argv[2:]) == 0
        health, _ = observed[-1]
        return backend_owner.BackendHandle("http://127.0.0.1:3100", health["pid"], 3100,
                                         Path(health["data_dir"]), health["version"], health["commit"])
    monkeypatch.setattr(backend_owner, "_start_and_wait", start)
    assert entry.main([]) == 0
    assert observed[1][0]["data_dir"] == observed[0][0]["data_dir"]
    assert observed[1][1] == observed[0][1]
    assert (root / "retained").read_text() == "old settings"
    assert not legacy_data.exists()
    assert len(capsys.readouterr().out.splitlines()) == 1


def test_started_default_backend_health_workspace_and_migration_equal_gui(tmp_path, monkeypatch, capsys, retire_event):
    """Real supported serve.py through both starts, with only provisioning disabled."""
    require_backend_breakaway()
    isolate_home(tmp_path, monkeypatch)
    root = resolve_data_dir()
    processes = []
    real_spawn = backend_owner._spawn
    real_argv = backend_start.backend_argv
    # This test must never provision/download a solver. The supported launcher,
    # application, instance lock and HTTP routes still run unchanged.
    monkeypatch.setenv("WG2_TEST_RETIRE_EVENT", retire_event)
    boot = "from launch import serve; serve._start_beat_cpu_provisioning=lambda:None; from server.tests.backend_lifetime_main import main; raise SystemExit(main())"

    def argv(**kwargs):
        result = real_argv(**kwargs)
        return [sys.executable, "-c", boot, *result[2:]]

    def spawn(*args):
        child, log = real_spawn(*args)
        processes.append(child)
        return child, log

    def read(base, path):
        with urlopen(base + path, timeout=5) as response:
            return json.load(response)

    monkeypatch.setattr(backend_owner, "_spawn", spawn)
    monkeypatch.setattr(entry, "backend_argv", argv)
    monkeypatch.setattr(entry, "resolve_bundle_paths", lambda *args: None)
    real_ensure = backend_owner.ensure_backend
    monkeypatch.setattr(entry, "ensure_backend", lambda *args, **kwargs: real_ensure(*args, timeout=25, **kwargs))
    control = tmp_path / "stop-gui"
    controller = StatusController(environ=dict(os.environ))
    gui_command = controller._command(3100, control)
    gui_command = [sys.executable, "-c", boot, *gui_command[2:]]
    try:
        migrate_legacy_data_dir(emit_log=False)
        gui = real_ensure(root, gui_command, start_env=controller._server_environment(), timeout=25)
        gui_health = read(gui.base_url, "/health")
        gui_workspace = read(gui.base_url, "/api/workspace/path")["path"]
        assert Path(gui_workspace) == default_runs_dir()
        control.touch()
        processes[-1].wait(timeout=10)
        legacy = root.with_name("WaveguideGenerator2")
        root.rename(legacy)
        (legacy / "legacy-proof").write_text("retained")
        assert entry.main([]) == 0, capsys.readouterr().out
        answer = json.loads(capsys.readouterr().out)
        health = read(answer["base_url"], "/health")
        assert health["data_dir"] == gui_health["data_dir"]
        assert read(answer["base_url"], "/api/workspace/path")["path"] == gui_workspace
        assert (root / "legacy-proof").read_text() == "retained"
        assert not legacy.exists()
    finally:
        for child in processes:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)


@pytest.mark.parametrize("explicit", [None, Path("/chosen/data")])
def test_shared_builder_keeps_data_override_optional(explicit):
    argv = backend_start.backend_argv(port=3100, data_dir=explicit)
    env = backend_start.backend_environment({}, data_dir=explicit)
    assert ("--data-dir" in argv) == (explicit is not None)
    assert env.get("WG2_DATA_DIR") == (str(explicit) if explicit is not None else None)


@pytest.mark.parametrize("marker,image", [(None, b"old stub"), (b"wrong", b"old stub"),
                                          (b"WG_NATIVE_ENSURE_BACKEND_V1", b"old stub"),
                                          (None, b"WG_NATIVE_ENSURE_BACKEND_V1")])
def test_windows_advertisement_requires_marker_and_current_native_support(tmp_path, marker, image):
    app = tmp_path / "app"
    app.mkdir()
    (tmp_path / "Waveguide Generator.exe").write_bytes(image)
    if marker is not None:
        (tmp_path / "Waveguide Generator.ensure-backend-v1").write_bytes(marker)
    assert backend_start.installed_launcher_argv(
        environ={"WG2_BUNDLE": "1", "WG2_APP_ROOT": str(app)}, platform_name="win32",
    ) is None


@pytest.mark.parametrize("error", [LookupError("unexpected"), TypeError("unexpected"),
                                  SystemExit(0), KeyboardInterrupt()])
def test_ensure_catches_all_start_exceptions_as_one_json_line(tmp_path, monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(entry, "ensure_backend", fail)
    monkeypatch.setattr(entry, "resolve_bundle_paths", lambda *args: None)
    assert entry.main(["--data-dir", str(tmp_path)]) == 5
    result = capsys.readouterr()
    assert result.err == ""
    assert len(result.out.splitlines()) == 1
    assert json.loads(result.out)["error"]["type"] == type(error).__name__


@pytest.mark.parametrize("member,flags,expected", [(False, 0, 0), (True, 0x2800, 0x1000000),
                                                  (True, 0x3000, 0), (True, 0x2000, None),
                                                  (True, 0, None), (True, 0x800, 0x1000000),
                                                  (True, 0x1000, 0)])
def test_windows_breakaway_policy_queries_job(tmp_path, monkeypatch, member, flags, expected):
    def in_job(process, job, result):
        result._obj.value = member
        return True
    def query(job, kind, result, size, returned):
        assert job is None and kind == 2
        result._obj.LimitFlags = flags
        return True
    kernel = SimpleNamespace(GetCurrentProcess=lambda: 42, IsProcessInJob=in_job, QueryInformationJobObject=query)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    if expected is None:
        with pytest.raises(process_tree.BreakawayRefused) as error:
            process_tree.windows_backend_breakaway_flags()
        assert str(error.value) == WINDOWS_BREAKAWAY_REFUSAL
    else:
        assert process_tree.windows_backend_breakaway_flags() == expected


@pytest.mark.parametrize("flags", [0, 0x2000])
def test_residual_outer_job_is_refused_before_admission(monkeypatch, flags):
    def in_job(process, job, result):
        result._obj.value = True
        return True
    def query(job, kind, result, size, returned):
        result._obj.LimitFlags = flags
        return True
    kernel = SimpleNamespace(IsProcessInJob=in_job, QueryInformationJobObject=query)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    with pytest.raises(process_tree.BreakawayRefused) as error:
        process_tree.require_windows_job_detached(42)
    assert str(error.value) == (
        "The backend could not detach from the caller's Windows job chain. "
        "Start Waveguide Generator outside that job, or enable breakaway in every parent job."
    )


@pytest.mark.parametrize("subject", ["caller", "backend"])
def test_windows_job_membership_query_failure_is_plain_oserror(monkeypatch, subject):
    def in_job(process, job, result):
        return False
    kernel = SimpleNamespace(GetCurrentProcess=lambda: 42, IsProcessInJob=in_job)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    probe = (process_tree.windows_backend_breakaway_flags if subject == "caller"
             else lambda: process_tree.require_windows_job_detached(42))
    with pytest.raises(OSError, match=f"Could not check the {subject}'s Windows job") as error:
        probe()
    assert type(error.value) is OSError


def test_windows_spawn_requests_breakaway_and_checks_child_before_job(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(backend_owner.sys, "platform", "win32")
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", lambda: 0x01000000)
    monkeypatch.setattr(process_tree, "require_windows_job_detached", lambda h: calls.append(("detached", h)))
    job = SimpleNamespace(terminate=lambda: None, close=lambda: None)
    def confine(pid, **kwargs):
        assert calls[-1] == ("detached", 42)
        return job
    monkeypatch.setattr(process_tree, "confine_to_windows_job", confine)
    def popen(argv, **kwargs):
        assert kwargs["creationflags"] & 0x01000000
        return SimpleNamespace(pid=123, _handle=42)
    monkeypatch.setattr(backend_owner.subprocess, "Popen", popen)
    child, _ = backend_owner._spawn(["backend"], tmp_path)
    assert child._wg_start_job is job
    assert calls == [("detached", 42)]


@pytest.mark.parametrize("platform,flags", [("darwin", None), ("linux", None),
                                           ("win32", 0), ("win32", 0x01000000)])
def test_breakaway_gate_allows_posix_and_granted_windows_jobs(monkeypatch, platform, flags):
    calls = []
    def probe():
        calls.append(True)
        assert flags is not None, "POSIX must not query Windows jobs"
        return flags
    monkeypatch.setattr(backend_breakaway, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", probe)
    assert backend_breakaway_permitted()
    require_backend_breakaway()
    assert len(calls) == (2 if platform == "win32" else 0)


def test_breakaway_gate_skips_only_the_exact_windows_refusal(monkeypatch):
    def refuse():
        raise OSError(WINDOWS_BREAKAWAY_REFUSAL)
    monkeypatch.setattr(backend_breakaway, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", refuse)
    assert not backend_breakaway_permitted()
    with pytest.raises(pytest.skip.Exception) as skipped:
        require_backend_breakaway()
    assert "runner's Windows job forbids backend breakaway" in str(skipped.value)


@pytest.mark.parametrize("message", [
    "Could not check the caller's Windows job; cannot detach the backend",
    "Could not query the caller's Windows job; cannot detach the backend",
])
def test_breakaway_gate_does_not_hide_job_query_errors(monkeypatch, message):
    error = OSError(message)
    def refuse():
        raise error
    monkeypatch.setattr(backend_breakaway, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", refuse)
    with pytest.raises(OSError) as raised:
        require_backend_breakaway()
    assert raised.value is error


@pytest.mark.parametrize("launcher", [False, True])
def test_windows_breakaway_refusal_prevents_backend_creation(tmp_path, monkeypatch, capsys, launcher):
    def refuse():
        raise process_tree.BreakawayRefused(WINDOWS_BREAKAWAY_REFUSAL)
    monkeypatch.setattr(backend_owner, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", refuse)
    _assert_breakaway_refusal(tmp_path, monkeypatch, capsys, launcher=launcher)


def _assert_breakaway_refusal(tmp_path, monkeypatch, capsys, *, launcher):
    argv = [sys.executable, "-c", "pass"]
    def forbidden(*args, **kwargs):
        pytest.fail("a refused job must not create a backend process")
    monkeypatch.setattr(backend_owner.subprocess, "Popen", forbidden)
    monkeypatch.setattr(backend_owner, "_discover", lambda *args: None)
    if launcher:
        monkeypatch.setattr(entry, "backend_argv", lambda **kwargs: argv)
        monkeypatch.setattr(entry, "resolve_bundle_paths", lambda *args: None)
        assert entry.main(["--data-dir", str(tmp_path)]) == 5
        result = capsys.readouterr()
        assert result.err == ""
        assert len(result.out.splitlines()) == 1
        assert json.loads(result.out) == {
            "ok": False,
            "error": {"type": "BackendSpawnError", "message": backend_spawn_refusal_message(argv[0])},
        }
    else:
        with pytest.raises(backend_owner.BackendSpawnError) as error:
            backend_owner.ensure_backend(tmp_path, argv, timeout=1)
        assert str(error.value) == backend_spawn_refusal_message(argv[0])
        assert str(error.value.__cause__) == WINDOWS_BREAKAWAY_REFUSAL
        assert isinstance(error.value.__cause__, process_tree.BreakawayRefused)
    assert not (tmp_path / "logs").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires the current Windows Job Object")
def test_installed_launcher_refuses_when_runner_job_forbids_breakaway(tmp_path, monkeypatch, capsys):
    if backend_breakaway_permitted():
        pytest.skip("this runner permits backend breakaway; granted startup tests cover it")
    _assert_breakaway_refusal(tmp_path, monkeypatch, capsys, launcher=True)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects and CREATE_BREAKAWAY_FROM_JOB")
@pytest.mark.parametrize("allow_breakaway", [False, True])
def test_windows_backend_survives_kill_on_close_caller_job(tmp_path, allow_breakaway):
    """Run the real owner spawn in a caller held by our kill-on-close job."""
    if allow_breakaway:
        # A breakaway-capable inner job cannot release a forbidding runner job.
        require_backend_breakaway()
    record = tmp_path / "child.json"
    ready = tmp_path / "ready"
    script = (
        "import json,sys,time\nfrom pathlib import Path\n"
        "from server.platform.backend_owner import _spawn,BackendSpawnError\n"
        "record,ready,data=map(Path,sys.argv[1:])\nready.write_text('ready')\n"
        "while not ready.with_suffix('.go').exists(): time.sleep(.01)\n"
        "try:\n child,_=_spawn([sys.executable,'-c','import time;time.sleep(60)'],data)\n"
        "except BackendSpawnError as e: record.write_text(json.dumps({'error':str(e)}))\n"
        "else:\n child._wg_start_job.close();record.write_text(json.dumps({'pid':child.pid}))\n"
        "time.sleep(60)\n"
    )
    caller = subprocess.Popen([sys.executable, "-c", script, str(record), str(ready), str(tmp_path / "data")])
    job = None
    pid = None
    try:
        import time
        from server.platform.instance import pid_is_running
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        job = process_tree.confine_to_windows_job(caller.pid, allow_breakaway=allow_breakaway)
        assert job is not None
        ready.with_suffix(".go").touch()
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        answer = json.loads(record.read_text())
        if not allow_breakaway:
            assert answer == {"error": backend_spawn_refusal_message(sys.executable)}
        else:
            assert "error" not in answer, answer
            pid = answer["pid"]
        job.close()
        job = None
        caller.wait(timeout=5)
        if pid is not None:
            assert pid_is_running(pid)
    finally:
        if pid is not None:
            os.kill(pid, signal.SIGTERM)
        if job is not None:
            job.terminate()
            job.close()
        if caller.poll() is None:
            caller.terminate()
        caller.wait(timeout=5)
