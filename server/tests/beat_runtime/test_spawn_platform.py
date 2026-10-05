from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from server.solver.beat_runtime import ipc, registry as r, spawn
from server.tests.beat_runtime.fake_host_worker import events, wait_until


def test_source_launch_uses_module_app_root_devnull_and_reaping(launch, tmp_path, monkeypatch):
    key, directory, children = launch
    source = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    original = spawn.subprocess.Popen
    observed = []

    def popen(command, **options):
        observed.append((command, options))
        return original(command, **options)

    monkeypatch.setattr(spawn.subprocess, "Popen", popen)
    record = spawn.start_host(key, directory, idle_timeout=0.2)
    command, options = observed[0]
    assert command[:4] == [sys.executable, "-m", spawn.HOST_MODULE, "--key"]
    assert options["cwd"] == str(source)
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.STDOUT
    assert options["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(source)
    assert options["env"]["WG2_APP_ROOT"] == str(source)
    assert options["close_fds"]
    if os.name == "posix":
        assert options["start_new_session"]
        assert os.getsid(record.pid) == record.pid
    wait_until(lambda: children[0].returncode is not None)
    assert events(key)[0]["cwd"] == str(source)
    assert r.log_path(record.identifier, directory).exists()



def test_packaged_app_root_imports_without_checkout_on_pythonpath(launch, tmp_path, monkeypatch):
    key, directory, _ = launch
    source = Path(__file__).resolve().parents[3]
    app = tmp_path / "bundle" / "app"
    for relative in ("server/__init__.py", "server/solver/__init__.py", "server/platform/__init__.py",
                     "server/platform/paths.py", "server/tests/beat_runtime/fake_host_worker.py",
                     "server/tests/beat_runtime/fake_host_main.py"):
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    shutil.copytree(source / "server/solver/beat_runtime", app / "server/solver/beat_runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_APP_ROOT", str(app))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.chdir(tmp_path)
    record = spawn.start_host(key, directory)
    assert events(key)[0]["cwd"] == str(app)
    assert record.key == key
    launched = events(key)[0]["kwargs"]["environment"]
    assert {name: launched[name] for name in key["environment"]} == key["environment"]
    assert "PATH" in launched  # The host inherits unkeyed launch settings too.



def test_windows_detach_flags_preserve_job_inheritance():
    assert spawn.detached_options(windows=True) == {"creationflags": 0x208}
    assert spawn.detached_options(windows=False) == {"start_new_session": True}
    assert not spawn.detached_options(windows=True)["creationflags"] & 0x01000000  # No breakaway.



def test_windows_native_bootstrap_accepts_only_verified_interpreter_child(launch, monkeypatch):
    key, directory, _ = launch
    directory = r.private_directory(directory)
    record = r.HostRecord(key, 101, "secret", ipc.Endpoint("tcp", port=1234), "host-start")
    raw = {"record": record.as_dict(), "launcher_pid": 100, "launcher_start": "launcher-start"}
    monkeypatch.setattr(r, "process_start_identity", lambda pid: "host-start" if pid == 101 else "launcher-start")
    assert spawn._bootstrap_record(raw, key, directory, 100, "launcher-start") == record
    for changes in ({"launcher_pid": 99}, {"launcher_pid": True}, {"launcher_start": "reused"}):
        with pytest.raises(r.RecordRefused, match="process identity"):
            spawn._bootstrap_record({**raw, **changes}, key, directory, 100, "launcher-start")
    with pytest.raises(r.RecordRefused):
        spawn._bootstrap_record(raw, key, directory, 100, None)



@pytest.mark.skipif(os.name != "posix", reason="Executable wrapper fixture uses a POSIX shebang")
def test_native_launcher_wrapper_keeps_real_host_pid_and_restores_app_cwd(launch, tmp_path, monkeypatch):
    key, directory, children = launch
    root = Path(__file__).resolve().parents[3]
    wrapper = tmp_path / "native-entry"
    wrapper.write_text(f"#!{sys.executable}\nimport subprocess,sys\n"
                       f"sys.exit(subprocess.call([{sys.executable!r}, *sys.argv[1:]], cwd={str(tmp_path)!r}))\n")
    wrapper.chmod(0o700)
    monkeypatch.setattr(sys, "executable", str(wrapper))
    record = spawn.start_host(key, directory)
    assert record.pid != children[0].pid
    assert events(key)[0]["cwd"] == str(root)
    assert spawn.start_host(key, directory) == record
    assert len(children) == 1


@pytest.mark.parametrize('executable', ['Waveguide Generator.exe', 'wg-python.exe'])
def test_windows_renamed_pythonw_direct_bootstrap_independent_of_executable_name(launch, monkeypatch, executable):
    key, directory, _ = launch
    directory = r.private_directory(directory)
    record = r.HostRecord(key, 100, 'secret', ipc.Endpoint('tcp', port=1234), 'host-start')
    raw = {'record': record.as_dict(), 'launcher_pid': 99, 'launcher_start': 'parent-start'}
    monkeypatch.setattr(sys, 'executable', executable)
    monkeypatch.setattr(r, 'process_start_identity', lambda pid: 'host-start')
    assert spawn._bootstrap_record(raw, key, directory, 100, 'host-start') == record


@pytest.mark.parametrize('executable', ['Waveguide Generator.exe', 'wg-python.exe'])
def test_windows_owned_launcher_bootstrap_independent_of_executable_name(launch, monkeypatch, executable):
    key, directory, _ = launch
    directory = r.private_directory(directory)
    record = r.HostRecord(key, 101, 'secret', ipc.Endpoint('tcp', port=1234), 'host-start')
    raw = {'record': record.as_dict(), 'launcher_pid': 100, 'launcher_start': 'launcher-start'}
    monkeypatch.setattr(sys, 'executable', executable)
    monkeypatch.setattr(r, 'process_start_identity', lambda pid: 'host-start')
    assert spawn._bootstrap_record(raw, key, directory, 100, 'launcher-start') == record
