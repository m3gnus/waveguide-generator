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
        assert argv == gui_args[:gui_args.index("--status-control")]
        return backend_owner.BackendHandle("http://127.0.0.1:3100", 123, 3100, data, "1", "abc")

    monkeypatch.setattr(backend_owner, "_discover", lambda *args: None)
    monkeypatch.setattr(backend_owner, "_start_and_wait", start)
    assert entry.main([]) == 0
    assert json.loads(capsys.readouterr().out)["data_dir"] == str(root.resolve())


@pytest.mark.parametrize("legacy_exports", [False, True])
def test_default_admission_runs_supported_app_with_gui_workspace_defaults(tmp_path, monkeypatch, capsys, legacy_exports):
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
            self.app = config.app
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


def test_started_default_backend_health_workspace_and_migration_equal_gui(tmp_path, monkeypatch, capsys):
    """Real supported serve.py through both starts, with only provisioning disabled."""
    isolate_home(tmp_path, monkeypatch)
    root = resolve_data_dir()
    processes = []
    real_spawn = backend_owner._spawn
    real_argv = backend_start.backend_argv
    # This test must never provision/download a solver. The supported launcher,
    # application, instance lock and HTTP routes still run unchanged.
    boot = "from launch import serve; serve._start_beat_cpu_provisioning=lambda:None; raise SystemExit(serve.main())"

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


@pytest.mark.parametrize("member,flags,expected", [(False, 0, 0), (True, 0x800, 0x1000000),
                                                  (True, 0x1000, 0), (True, 0x2000, None)])
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
        with pytest.raises(OSError, match="does not allow backend breakaway"):
            process_tree.windows_backend_breakaway_flags()
    else:
        assert process_tree.windows_backend_breakaway_flags() == expected


def test_residual_outer_job_is_refused_before_admission(monkeypatch):
    def in_job(process, job, result):
        result._obj.value = True
        return True
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(IsProcessInJob=in_job), raising=False)
    with pytest.raises(OSError, match="job chain"):
        process_tree.require_windows_job_detached(42)


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


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects and CREATE_BREAKAWAY_FROM_JOB")
@pytest.mark.parametrize("allow_breakaway", [False, True])
def test_windows_backend_survives_kill_on_close_caller_job(tmp_path, allow_breakaway):
    """Run the real owner spawn in a caller held by our kill-on-close job."""
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
            assert "breakaway" in answer["error"]
        else:
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
