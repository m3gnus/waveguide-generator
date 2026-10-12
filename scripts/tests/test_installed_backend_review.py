"""Bootstrap JSON and installed cold-start harness regressions."""

import builtins
import ast
import ctypes
import json
import types
import os
from pathlib import Path
import runpy
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from launchers import full_installer
from scripts import build_bundle, qualify_installed_cpu as gate
from server.platform import process_tree
from server.tests.backend_breakaway import WINDOWS_BREAKAWAY_REFUSAL


NOT_EXERCISED = {
    "exercised": False,
    "result": "cold start via --ensure-backend: not exercised (host refuses breakaway)",
    "start_path": "the gate's own direct launch/serve.py --status-control start, used on every host",
    "probe": "server.platform.process_tree.windows_backend_breakaway_flags",
    "refusal": WINDOWS_BREAKAWAY_REFUSAL,
}


def test_cold_start_phase_records_refused_breakaway(tmp_path, monkeypatch, capsys):
    def refuse():
        raise OSError(WINDOWS_BREAKAWAY_REFUSAL)
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="win32", stderr=sys.stderr))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", refuse)
    monkeypatch.setattr(gate, "check_ensure_backend_cold_start",
                        lambda *args, **kwargs: pytest.fail("refused job entered cold-start harness"))
    assert gate.ensure_backend_cold_start_phase(
        tmp_path / "python", tmp_path / "app", tmp_path / "work", tmp_path / "output",

    ) == NOT_EXERCISED
    assert capsys.readouterr().err.splitlines() == [NOT_EXERCISED["result"]]


@pytest.mark.parametrize("platform,flags", [("win32", 0), ("win32", 0x01000000),
                                           ("linux", None), ("darwin", None)])
def test_cold_start_phase_runs_when_breakaway_permitted(tmp_path, monkeypatch, capsys, platform, flags):
    probes, calls = [], []
    def probe():
        assert platform == "win32", "non-Windows must not query Windows jobs"
        probes.append(True)
        return flags
    result = {"argv": ["installed-launcher"], "survives_caller": True,
              "gui_defaults_match": True, "stopped_by_pid": 123}
    def cold_start(*args, **kwargs):
        calls.append((args, kwargs))
        return result
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", probe)
    monkeypatch.setattr(gate, "check_ensure_backend_cold_start", cold_start)
    args = (tmp_path / "python", tmp_path / "app", tmp_path / "work", tmp_path / "output")
    assert gate.ensure_backend_cold_start_phase(*args, ) == {"exercised": True, **result}
    assert calls == [(args, {})]
    assert probes == ([True] if platform == "win32" else [])
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("message", [
    "Could not check the caller's Windows job; cannot detach the backend",
    "Could not query the caller's Windows job; cannot detach the backend",
    WINDOWS_BREAKAWAY_REFUSAL + " extra detail",
])
def test_cold_start_phase_propagates_job_query_errors(tmp_path, monkeypatch, capsys, message):
    error = OSError(message)
    def fail_query():
        raise error
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="win32", stderr=sys.stderr))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", fail_query)
    monkeypatch.setattr(gate, "check_ensure_backend_cold_start",
                        lambda *args, **kwargs: pytest.fail("failed job query entered cold-start harness"))
    with pytest.raises(OSError) as raised:
        gate.ensure_backend_cold_start_phase(tmp_path, tmp_path, tmp_path, tmp_path)
    assert raised.value is error
    assert capsys.readouterr().err == ""


def test_breakaway_refusal_matches_product_probe(monkeypatch):
    assert gate.WINDOWS_BREAKAWAY_REFUSAL == WINDOWS_BREAKAWAY_REFUSAL
    calls = []
    def in_job(process, job, result):
        assert process == 42 and job is None
        result._obj.value = True
        calls.append("membership")
        return True
    def query(job, kind, result, size, returned):
        assert job is None and kind == 2
        result._obj.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, without breakaway.
        calls.append("limits")
        return True
    kernel = SimpleNamespace(GetCurrentProcess=lambda: 42, IsProcessInJob=in_job, QueryInformationJobObject=query)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="win32"))
    assert not gate.backend_breakaway_permitted()
    assert calls == ["membership", "limits"]


@pytest.mark.parametrize("via_main", [False, True])
@pytest.mark.parametrize("permitted", [False, True])
def test_qualify_breakaway_decision_keeps_server_start_adoption_and_report(
    tmp_path, monkeypatch, capsys, via_main, permitted,
):
    app, interpreter = tmp_path / "payload/app", tmp_path / "python"
    work, output = tmp_path / "work", tmp_path / "output"
    environment = {"WG2_BEAT_RUNTIME_DIR": "runtime",
                   "WG2_BEAT_WORKER_DIR": "workers", "JULIA_DEPOT_PATH": "julia",
                   "PYTHONPYCACHEPREFIX": "python-cache", "NUMBA_CACHE_DIR": "numba-cache"}
    starts, adoptions, cold_starts = [], [], []
    cold_result = {"argv": ["installed-launcher"], "survives_caller": True}
    def probe():
        if not permitted:
            raise OSError(WINDOWS_BREAKAWAY_REFUSAL)
        return 0
    def cold_start(*args, **kwargs):
        assert permitted, "refused job entered cold-start harness"
        cold_starts.append((args, kwargs))
        return cold_result
    class Server:
        base = "http://127.0.0.1:3199"
        def __init__(self, *args):
            starts.append(args)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def adopt(*args, **kwargs):
        adoptions.append((args, kwargs))
        return {"same_owner": True}
    def stop_after_adoption(*args):
        raise gate.QualificationError("sentinel after warm adoption")
    def forbidden(*args, **kwargs):
        pytest.fail("test must not start any real process")
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="win32", stderr=sys.stderr))
    monkeypatch.setattr(process_tree, "windows_backend_breakaway_flags", probe)
    monkeypatch.setattr(gate.subprocess, "run", forbidden)
    monkeypatch.setattr(gate.subprocess, "Popen", forbidden)
    monkeypatch.setattr(gate, "resolve_payload", lambda *args: (app.parent, app, interpreter))
    monkeypatch.setattr(gate, "isolated_environment", lambda *args, **kwargs: environment)
    monkeypatch.setattr(gate, "pins_from_file", lambda *args: {"beat-engine": "pinned"})
    monkeypatch.setattr(gate, "expectations_from_build_manifest", lambda *args: {})
    monkeypatch.setattr(gate, "check_manifest", lambda *args: {})
    monkeypatch.setattr(gate, "check_pins", lambda *args: {})
    monkeypatch.setattr(gate, "check_ensure_backend_cold_start", cold_start)
    monkeypatch.setattr(gate, "Server", Server)
    monkeypatch.setattr(gate, "check_ensure_backend", adopt)
    monkeypatch.setattr(gate, "await_cpu_row", stop_after_adoption)
    monkeypatch.setattr(gate, "stop_our_workers", lambda *args: {"contained": True})
    argv = ["--payload", str(app.parent), "--work", str(work), "--output", str(output)]
    if via_main:
        assert gate.main(argv) == 1
        report = json.loads((output / "cpu-qualification.json").read_text(encoding="utf-8"))
        assert report["qualified"] is False
        assert report["error"] == "sentinel after warm adoption"
    else:
        arguments = gate.build_parser().parse_args(argv)
        report = {}
        with pytest.raises(gate.QualificationError, match="sentinel after warm adoption"):
            gate.qualify(arguments, report)
        assert arguments.cleanup == (interpreter, environment, output)
    assert starts == [(interpreter, app, environment, work / "data", work / "status/stop-1", output / "server.log")]
    assert adoptions == [((app, work / "data", environment, Server.base), {"cwd": output})]
    assert report["ensure_backend"] == {"same_owner": True}
    assert report["ensure_backend_cold_start"] == ({"exercised": True, **cold_result} if permitted else NOT_EXERCISED)
    assert cold_starts == ([((interpreter, app, work, output), {})] if permitted else [])
    lines = capsys.readouterr().err.splitlines()
    assert lines.count(NOT_EXERCISED["result"]) == (0 if permitted else 1)


def test_desktop_installer_guard_reports_json_before_importing_gui(monkeypatch, capsys):
    def blocked(*args):
        raise full_installer.InstallerStartBlocked("installation is active")
    monkeypatch.setattr(full_installer, "guard_full_installer_start", blocked)
    monkeypatch.setattr(sys, "argv", ["launcher", "--ensure-backend"])
    monkeypatch.delitem(sys.modules, "launchers.desktop", raising=False)
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("launchers.desktop", run_name="guard_test")
    assert exited.value.code == 5
    output = capsys.readouterr()
    assert output.err == ""
    assert len(output.out.splitlines()) == 1
    assert json.loads(output.out)["error"]["type"] == "InstallerStartBlocked"


@pytest.mark.parametrize("problem", ["missing-app", "unexpected-main", "setup-guard"])
def test_windows_bootstrap_ensure_failures_never_enter_dialog(tmp_path, monkeypatch, capsys, problem):
    for name in ("WG2_APP_ROOT", "WG2_BUNDLE", "PYTHONPYCACHEPREFIX", "NUMBA_CACHE_DIR"):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["", "--ensure-backend"])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Waveguide Generator.exe"))
    original_import = builtins.__import__
    def imported(name, *args, **kwargs):
        if name == "launchers.desktop":
            if problem == "missing-app":
                raise ModuleNotFoundError("app layer is missing")
            def fail(argv):
                raise LookupError("unexpected bootstrap failure")
            return SimpleNamespace(main=fail)
        if name == "launchers.statusapp.__main__":
            pytest.fail("modal reporter entered in ensure mode")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", imported)
    source = build_bundle.windows_desktop_bootstrap()
    if problem == "setup-guard":
        # Use the generated guard's failure decision without calling Win32 on
        # this host. Its reporting path is the production generated code.
        source = source.replace("if not _wait_for_setup_mutex():", "if True:")
    with pytest.raises(SystemExit) as exited:
        exec(compile(source, "bootstrap", "exec"), {})
    assert exited.value.code == 5
    output = capsys.readouterr()
    assert len(output.out.splitlines()) == 1
    assert json.loads(output.out)["ok"] is False


def test_native_windows_python_recovery_failure_is_one_json_line(monkeypatch, capsys, tmp_path):
    source = (Path(__file__).resolve().parents[2] / "launchers/windows/launcher.c").read_text()
    section = source.split("if (!arguments[0] || ensure_backend) {", 1)[1].split("hidden, arguments, public_path", 1)[0]
    command = "".join(ast.literal_eval(value) for value in re.findall(r'L("(?:[^"\\]|\\.)*")', section))
    program = command.split('-c "', 1)[1].rsplit('" %s "%s"', 1)[0]
    monkeypatch.setattr(sys, "argv", ["", "--ensure-backend", str(tmp_path / "Waveguide Generator.exe")])
    monkeypatch.setattr(sys, "executable", sys.executable)
    monkeypatch.setattr(sys, "path", list(sys.path))
    original_import = builtins.__import__
    def imported(name, *args, **kwargs):
        if name == "wg_bundle_recovery":
            raise LookupError("recovery failed")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", imported)
    with pytest.raises(SystemExit) as exited:
        exec(compile(program, "native_boot", "exec"), {})
    assert exited.value.code == 5
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert json.loads(output)["error"]["type"] == "LookupError"


def test_native_windows_boot_success_exit_passes_through(monkeypatch, capsys, tmp_path):
    """A complete install's bootstrap ends with SystemExit(result); that is not a failure."""
    source = (Path(__file__).resolve().parents[2] / "launchers/windows/launcher.c").read_text()
    section = source.split("if (!arguments[0] || ensure_backend) {", 1)[1].split("hidden, arguments, public_path", 1)[0]
    command = "".join(ast.literal_eval(value) for value in re.findall(r'L("(?:[^"\\]|\\.)*")', section))
    program = command.split('-c "', 1)[1].rsplit('" %s "%s"', 1)[0]
    monkeypatch.setattr(sys, "argv", ["", "--ensure-backend", str(tmp_path / "Waveguide Generator.exe")])
    monkeypatch.setattr(sys, "executable", sys.executable)
    monkeypatch.setattr(sys, "path", list(sys.path))
    recovery = types.ModuleType("wg_bundle_recovery")
    def windows_boot():
        print(json.dumps({"ok": True}), flush=True)
        raise SystemExit(0)
    recovery.windows_boot = windows_boot
    monkeypatch.setitem(sys.modules, "wg_bundle_recovery", recovery)
    with pytest.raises(SystemExit) as exited:
        exec(compile(program, "native_boot", "exec"), {})
    assert exited.value.code == 0
    output = capsys.readouterr().out
    assert output.splitlines() == ['{"ok": true}']


def test_windows_early_admission_failure_is_json_before_exit(monkeypatch, capsys):
    source = (Path(__file__).resolve().parents[2] / "launchers/windows/startup_hook.py").read_text()
    declarations, body = source.split('if sys.platform == "win32":', 1)
    namespace = {}
    exec(compile(declarations, "hook", "exec"), namespace)
    def fail():
        raise LookupError("native admission failed")
    namespace["_admit"] = fail
    monkeypatch.setattr(sys, "argv", ["", "--ensure-backend"])
    monkeypatch.setattr(os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit) as exited:
        exec(compile("if True:" + body, "hook", "exec"), namespace)
    assert exited.value.code == 5
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert json.loads(output)["error"]["type"] == "LookupError"


def test_windows_installer_marker_is_native_owned_and_removed_on_uninstall():
    root = Path(__file__).resolve().parents[2]
    source = (root / "launchers/windows/launcher.c").read_text()
    helper = source.split("static int ensure_backend_marker", 1)[1].split("static HANDLE startup_claim", 1)[0]
    assert '"WG_NATIVE_ENSURE_BACKEND_V1"' in helper
    assert 'L"Waveguide Generator.ensure-backend-v1"' in helper
    assert "if (equal_files(public_path, self)) return ensure_backend_marker(root) ? 0 : 3;" in helper
    assert helper.index("MoveFileExW(tmp, public_path") < helper.rindex("return ensure_backend_marker(root)")
    assert 'Name: "{app}\\Waveguide Generator.ensure-backend-v1"' in (root / "installers/windows/bundle-setup.iss").read_text()


@pytest.mark.parametrize("damage", [None, "extra-line", "exit", "dead-after-caller", "folder", "workspace", "gui-folder"])
def test_cold_start_harness_requires_survival_gui_defaults_and_stops_pid(tmp_path, monkeypatch, damage):
    app = tmp_path / "payload/app"
    app.mkdir(parents=True)
    launcher = (app.parent / ("Waveguide Generator.exe" if sys.platform == "win32" else "waveguide-generator"))
    launcher.touch()
    work, output = tmp_path / "work", tmp_path / "output"
    output.mkdir()
    profile = work / "ensure-cold-start"
    data = profile / "xdg-data/WaveguideGenerator"
    workspace = profile / "documents/Waveguide Generator/runs"
    base, pid = "http://127.0.0.1:3199", 123
    events = []

    def run(argv, **kwargs):
        assert argv == [str(launcher), "--ensure-backend"]
        assert "WG2_DATA_DIR" not in kwargs["env"]
        assert kwargs["env"]["HOME"] == str(profile / "profile")
        assert not data.exists()
        data.mkdir(parents=True)
        events.append("caller-exited")
        answer = {"ok": True, "pid": pid, "base_url": base, "data_dir": str(data)}
        return subprocess.CompletedProcess(argv, 5 if damage == "exit" else 0,
                stdout=json.dumps(answer) + ("\nextra" if damage == "extra-line" else "\n"), stderr="")

    def http(url, path, **kwargs):
        assert events[0] == "caller-exited"
        if path == "/health":
            if url == base:
                if damage == "dead-after-caller":
                    raise OSError("caller killed backend")
                return {"pid": pid, "data_dir": str(data if damage != "folder" else profile / "wrong")}
            return {"pid": 456, "data_dir": str(data if damage != "gui-folder" else profile / "wrong")}
        assert path == "/api/workspace/path"
        return {"path": str(workspace if url != base or damage != "workspace" else data / "workspace")}

    def stop(url, process_id, folder):
        assert (url, process_id, folder) == (base, pid, data)
        events.append("stopped-by-pid")

    class GUI:
        base = "gui"
        def __init__(self, python, app_layer, env, override, control, log):
            assert "stopped-by-pid" in events
            assert override is None and "WG2_DATA_DIR" not in env
            assert not data.exists() and (profile / "retained-cold-data").exists()
            data.mkdir()
            events.append("gui-started")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            events.append("gui-stopped")

    monkeypatch.setenv("WG2_DATA_DIR", "inherited-override")
    monkeypatch.setattr(gate.subprocess, "run", run)
    monkeypatch.setattr(gate, "http", http)
    monkeypatch.setattr(gate, "free_port", lambda: 3199)
    monkeypatch.setattr(gate, "stop_detached_backend", stop)
    monkeypatch.setattr(gate, "Server", GUI)
    monkeypatch.setattr(gate, "stop_our_workers", lambda *args: events.append("workers-stopped"))
    if damage:
        with pytest.raises((gate.QualificationError, OSError)):
            gate.check_ensure_backend_cold_start(Path(sys.executable), app, work, output)
    else:
        answer = gate.check_ensure_backend_cold_start(Path(sys.executable), app, work, output)
        assert answer["survives_caller"] and answer["gui_defaults_match"]
        assert answer["stopped_by_pid"] == pid
        assert events == ["caller-exited", "stopped-by-pid", "gui-started", "gui-stopped", "workers-stopped"]
    if damage not in ("extra-line", "exit"):
        assert "stopped-by-pid" in events


def test_cold_start_refuses_reused_profile(tmp_path):
    (tmp_path / "ensure-cold-start").mkdir()
    with pytest.raises(gate.QualificationError, match="requires a fresh"):
        gate.check_ensure_backend_cold_start(Path(sys.executable), tmp_path / "app", tmp_path, tmp_path)


def test_gui_baseline_server_uses_default_without_override(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(gate, "free_port", lambda: 3199)
    def popen(argv, **kwargs):
        captured.update(argv=argv, environment=kwargs["env"])
        return SimpleNamespace()
    monkeypatch.setattr(gate.subprocess, "Popen", popen)
    server = gate.Server(Path("installed-python"), tmp_path / "app", {}, None,
                         tmp_path / "control/stop", tmp_path / "gui.log")
    server._log.close()
    assert "--data-dir" not in captured["argv"]
    assert "WG2_DATA_DIR" not in captured["environment"]
    assert "--status-control" in captured["argv"]


def test_detached_cleanup_refuses_changed_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "http", lambda *args, **kwargs: {"pid": 999, "data_dir": str(tmp_path)})
    monkeypatch.setattr(gate.os, "kill", lambda *args: pytest.fail("signalled an unconfirmed process"))
    with pytest.raises(gate.QualificationError, match="identity changed"):
        gate.stop_detached_backend("http://127.0.0.1:3199", 123, tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pid signalling; Windows uses a process handle")
def test_detached_cleanup_signals_exact_confirmed_pid(monkeypatch, tmp_path):
    calls = []
    def http(*args, **kwargs):
        if calls:
            raise OSError("stopped")
        return {"pid": 123, "data_dir": str(tmp_path)}
    monkeypatch.setattr(gate, "http", http)
    monkeypatch.setattr(gate.os, "kill", lambda pid, signum: calls.append((pid, signum)))
    gate.stop_detached_backend("http://127.0.0.1:3199", 123, tmp_path)
    assert calls == [(123, gate.signal.SIGTERM)]
