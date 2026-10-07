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
    # Windows runs the host from its registry, never the app layer (host.working_directory).
    expected_cwd = str(r.private_directory(directory)) if os.name == "nt" else str(source)
    assert options["cwd"] == expected_cwd
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.STDOUT
    assert options["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(source)
    assert options["env"]["WG2_APP_ROOT"] == str(source)
    assert options["close_fds"]
    if os.name == "posix":
        assert options["start_new_session"]
        assert os.getsid(record.pid) == record.pid
    wait_until(lambda: children[0].returncode is not None)
    assert events(key)[0]["cwd"] == expected_cwd
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
    assert events(key)[0]["cwd"] == (str(r.private_directory(directory)) if os.name == "nt" else str(app))
    assert record.key == key
    launched = events(key)[0]["kwargs"]["environment"]
    assert {name: launched[name] for name in key["environment"]} == key["environment"]
    assert "PATH" in launched  # The host inherits unkeyed launch settings too.



def test_windows_detach_flags_break_away_from_the_launcher_job():
    assert spawn.detached_options(windows=True) == {"creationflags": 0x01000208}
    assert spawn.detached_options(windows=True, breakaway=False) == {"creationflags": 0x208}
    assert spawn.detached_options(windows=False) == {"start_new_session": True}
    assert spawn.detached_options(windows=False, breakaway=False) == {"start_new_session": True}


def _launch_attempts(launch, monkeypatch, refuse):
    """Run one real start_host, refusing CreateProcess attempts as ``refuse`` says."""
    key, directory, _ = launch
    original = spawn.subprocess.Popen
    attempts = []

    def popen(command, **options):
        attempts.append((command, options["creationflags"]))
        error = refuse(options["creationflags"])
        if error is not None:
            raise error
        return original(command, **options)

    monkeypatch.setattr(spawn.subprocess, "Popen", popen)
    return key, directory, attempts


def _breakaway_permitted() -> bool:
    """Whether this test process's own job, if any, lets a child break away.

    A hosted Windows runner's job does not, and no job a test makes can undo
    that, so "granted" is only provable where the environment allows it.
    """
    try:
        probe = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=0x01000208)
    except OSError as exc:
        if getattr(exc, "winerror", None) == spawn.ERROR_ACCESS_DENIED:
            return False
        raise
    probe.wait(timeout=30)
    return True


@pytest.mark.skipif(os.name != "nt", reason="Windows job breakaway")
def test_windows_breakaway_granted_is_recorded_by_the_host(launch, monkeypatch):
    if not _breakaway_permitted():
        pytest.skip("this runner's job forbids breakaway; the refused tests cover it")
    key, directory, attempts = _launch_attempts(launch, monkeypatch, lambda flags: None)
    record = spawn.start_host(key, directory)
    assert [(command[-2:], flags) for command, flags in attempts] == [
        (["--job-breakaway", "granted"], 0x01000208)]
    log = r.log_path(record.identifier, r.private_directory(directory)).read_text()
    assert "job breakaway: granted" in log


@pytest.mark.skipif(os.name != "nt", reason="Windows job breakaway")
def test_windows_breakaway_refused_by_an_enclosing_job_retries_once_inside_it(launch, monkeypatch):
    denied = lambda flags: OSError(13, "Access is denied", None, 5) if flags & 0x01000000 else None  # noqa: E731
    key, directory, attempts = _launch_attempts(launch, monkeypatch, denied)
    bootstraps = _capture_bootstraps(monkeypatch)
    record = spawn.start_host(key, directory)
    assert bootstraps and bootstraps[-1]["job_breakaway"] == "refused"
    assert [(command[-2:], flags) for command, flags in attempts] == [
        (["--job-breakaway", "granted"], 0x01000208), (["--job-breakaway", "refused"], 0x208)]
    log = r.log_path(record.identifier, r.private_directory(directory)).read_text()
    assert "job breakaway: refused" in log
    assert "job breakaway: granted" not in log


@pytest.mark.skipif(os.name != "nt", reason="Windows job breakaway")
@pytest.mark.parametrize("error", [OSError(2, "not found", None, 2), OSError(13, "denied", None, 5)])
def test_windows_other_launch_failures_are_not_retried_as_a_refusal(launch, monkeypatch, error):
    # A second ERROR_ACCESS_DENIED (an unreadable executable, say) is not retried again.
    key, directory, attempts = _launch_attempts(launch, monkeypatch, lambda flags: error)
    with pytest.raises(OSError):
        spawn.start_host(key, directory)
    assert len(attempts) == (2 if error.winerror == 5 else 1)


def _capture_bootstraps(monkeypatch):
    """Record every bootstrap (ready) record start_host reads, unchanged."""
    original = spawn.read_private_json
    bootstraps = []

    def read(path):
        value = original(path)
        if path.name.endswith(".ready.json"):
            bootstraps.append(value)
        return value

    monkeypatch.setattr(spawn, "read_private_json", read)
    return bootstraps


def test_the_host_puts_its_breakaway_outcome_in_its_bootstrap_record(launch, monkeypatch):
    # Never patch subprocess.Popen module-wide here: on macOS the process
    # identity check itself shells out to ps through it.
    key, directory, _ = launch
    bootstraps = _capture_bootstraps(monkeypatch)
    spawn.start_host(key, directory)
    assert bootstraps
    if os.name == "nt":
        # Truthful either way: what this environment's job really allowed.
        expected = "granted" if _breakaway_permitted() else "refused"
        assert bootstraps[-1]["job_breakaway"] == expected
    else:
        assert "job_breakaway" not in bootstraps[-1]  # POSIX passes no outcome.


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
