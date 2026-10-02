"""Bounded updater diagnostics: source contracts and real Win32 behavior."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import time
import shutil
import ast
import operator
import re

import pytest

from scripts.tests.test_windows_native_recovery import BOOT, _old_root, native as native
from scripts import build_bundle

pytestmark = pytest.mark.xdist_group("windows_native_setup_mutex")
ROOT = Path(__file__).resolve().parents[2]
LIMIT = 262144


def test_logger_dispatch_precedes_all_application_admission() -> None:
    source = (ROOT / "launchers/windows/launcher.c").read_text()
    main = source[source.index("int WINAPI wWinMain"):]
    dispatch = main[main.index('L"--update-log"'):main.index('L"--installer-"')]
    assert "LocalFree(argv); return code;" in dispatch
    assert "argc == 4" in dispatch and "code = 3;" in main[:main.index('L"--installer-"')]
    assert "start(" not in dispatch and "transaction_lock(" not in dispatch
    assert "ReplaceIfExists = FALSE" in source
    assert "FILE_DISPOSITION_INFO disposition = {TRUE}" in source
    assert "WaitForSingleObject(pi.hProcess, 5000)" in source
    assert "TerminateProcess(pi.hProcess, 3)" in source
    assert "nNumberOfLinks != 1" in source


def test_inno_bounded_route_and_failed_initialization_keep_truthful_outcomes() -> None:
    script = (ROOT / "installers/windows/bundle-setup.iss").read_text()
    assert "SetupLogging=no" in script
    init = script[script.index("function InitializeSetup()"):script.index("procedure DeinitializeSetup()")]
    assert init.index("InitializeWgLog()") < init.index("ValidateWgLinkAddInsOverride")
    wrapper = script[script.index("procedure WgLog("):script.index("procedure RecordCopyStart")]
    assert "if not WgLogReady then\n    exit;" in wrapper
    assert wrapper.count("WgLogReady := False") == 2
    assert "Log(S);" in wrapper  # manual vendor /LOG compatibility
    initializer = script[script.index("function InitializeWgLog()"):script.index("procedure WgLog(")]
    assert "'/LOG'" in initializer and "'/LOG='" in initializer
    assert "--update-log-init" in initializer
    assert "JsonStringOrNull(OutcomeLogPath())" in script
    assert "OutcomeLogPath() +" in script[script.index("function RunNative("):]
    assert script.count("BeforeInstall: RecordCopyStart; AfterInstall: RecordCopyDone") == 4
    assert "Last file not confirmed complete:" in script
    assert " [message truncated]" in script
    assert "Native verified outcome:" in (ROOT / "launchers/windows/launcher.c").read_text()


def test_actual_progress_threshold_expression_is_exact_and_32bit_bounded() -> None:
    script = (ROOT / "installers/windows/bundle-setup.iss").read_text()
    body = script.split("function InstallPercent(", 1)[1].split("procedure CurInstallProgressChanged", 1)[0]
    expression = re.search(r"Threshold := (.*?);", body, re.S).group(1)
    expression = re.sub(r"\bdiv\b", "//", expression)
    expression = re.sub(r"\bmod\b", "%", expression)
    tree = ast.parse("(" + expression.strip() + ")", mode="eval").body
    ops = {ast.Add: operator.add, ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}

    def calculate(node, values):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return values[node.id]
        assert isinstance(node, ast.BinOp) and type(node.op) in ops
        answer = ops[type(node.op)](calculate(node.left, values), calculate(node.right, values))
        assert 0 <= answer <= 2147483647
        return answer

    assert "if (CurProgress <= 0) or (MaxProgress <= 0) then\n    exit;" in body
    assert "Middle := (Low + High + 1) div 2;" in body
    assert "if CurProgress >= Threshold then\n      Low := Middle\n    else\n      High := Middle - 1;" in body
    for maximum in (1, 2, 99, 100, 101, 199, 200, 201, 2147483601, 2147483647):
        for progress in (0, 1, maximum // 2, maximum - 1, maximum):
            if progress >= maximum:
                actual = 100
            else:
                low, high = 0, 100
                while low < high:
                    middle = (low + high + 1) // 2
                    threshold = calculate(tree, {"MaxProgress": maximum, "Middle": middle})
                    if progress >= threshold:
                        low = middle
                    else:
                        high = middle - 1
                actual = low
            assert actual == progress * 100 // maximum


def _paths(tmp_path):
    folder = tmp_path / "update-install"
    folder.mkdir()
    return folder / "install.log", folder / "install.log.1"


def _run(native, log, message="test", mode="--update-log-append"):
    return subprocess.run([str(native), mode, str(log), message], timeout=8).returncode


def _bounded(log, backup):
    for p in (log, backup):
        if p.exists():
            data = p.read_bytes()
            assert len(data) <= LIMIT
            data.decode("utf-8")


@pytest.fixture(scope="module")
def log_pausing_native(native, tmp_path_factory):
    repo = tmp_path_factory.mktemp("log-pausing-native")
    source = repo / build_bundle.WINDOWS_NATIVE_SOURCE
    source.parent.mkdir(parents=True)
    code = (ROOT / build_bundle.WINDOWS_NATIVE_SOURCE).read_text()
    pause = r'''
static void log_test_pause(const wchar_t *stage) {
    wchar_t wanted[32], marker[1024]; HANDLE h; DWORD done; ULONGLONG end;
    if (!GetEnvironmentVariableW(L"WG_LOG_TEST_PAUSE", wanted, 32) || wcscmp(wanted, stage) ||
        !GetEnvironmentVariableW(L"WG_LOG_TEST_MARKER", marker, 1024)) return;
    h = CreateFileW(marker, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    WriteFile(h, "paused", 6, &done, NULL); FlushFileBuffers(h); CloseHandle(h);
    end = GetTickCount64() + 60000;
    while (GetFileAttributesW(marker) != INVALID_FILE_ATTRIBUTES && GetTickCount64() < end) Sleep(10);
}
'''
    code = code.replace("static int log_write(", pause + "\nstatic int log_write(", 1)
    assert code.count("    /* Both leaves are proven") == 1
    code = code.replace("    /* Both leaves are proven", '    log_test_pause(L"locked");\n    /* Both leaves are proven', 1)
    assert code.count("        /* No-clobber also preserves") == 1
    code = code.replace("        /* No-clobber also preserves", '        log_test_pause(L"rename");\n        /* No-clobber also preserves', 1)
    source.write_text(code)
    shutil.copy2(ROOT / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE, repo / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE)
    target = repo / "helper.exe"
    build_bundle.write_windows_launcher(target, repo_root=repo)
    return target


def _paused(native, log, tmp_path, stage, mode="--update-log-worker-init"):
    marker = tmp_path / (stage + ".txt")
    env = dict(os.environ, WG_LOG_TEST_PAUSE=stage, WG_LOG_TEST_MARKER=str(marker))
    child = subprocess.Popen([str(native), mode, str(log), "paused record"], env=env)
    end = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < end:
        assert child.poll() is None
        time.sleep(0.01)
    assert marker.exists()
    return child


def test_native_logging_needs_no_application_runtime_or_running_admission(native, tmp_path):
    log, backup = _paths(tmp_path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, "WaveguideGeneratorSetup")
    assert handle
    try:
        assert _run(native, log, 'quotes "inside"; tail\\; 😀\r\nnext', "--update-log-init") == 0
        data = log.read_text(encoding="utf-8")
        assert 'quotes "inside"; tail\\; 😀\\r\\nnext' in data
        assert data.count("\n") == 1
        _bounded(log, backup)
        assert not (tmp_path / ".wg-install-lock").exists()
        kernel.OpenMutexW.restype = ctypes.c_void_p
        running = kernel.OpenMutexW(0x00100000, False, "WaveguideGeneratorRunning")
        assert not running and ctypes.get_last_error() == 2
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.parametrize("mode,args", [
    ("--update-log", []), ("--update-log-unknown", []),
    ("--update-log-init", []), ("--update-log-worker", []),
    ("--update-log-worker-unknown", []), ("--update-log-append", ["extra"]),
])
def test_malformed_reserved_logger_modes_never_fall_through(native, tmp_path, mode, args):
    log, _ = _paths(tmp_path)
    command = [str(native), mode, str(log)] + args
    assert subprocess.run(command, timeout=8).returncode == 3
    assert not log.exists() and not (tmp_path / ".wg-install-lock").exists()


def test_oversized_and_invalid_path_arguments_refuse_without_writes(native, tmp_path):
    log, _ = _paths(tmp_path)
    assert _run(native, log, "x" * 4097) == 3
    assert _run(native, log.with_name("foreign.txt")) == 3
    assert _run(native, "x" * 1024) == 3
    assert not log.exists()


def test_both_old_slots_normalize_to_valid_bounded_tails_before_rotation(native, tmp_path):
    log, backup = _paths(tmp_path)
    log.write_bytes(("😀 current\n" * 40000).encode())
    backup.write_bytes(("é backup\n" * 40000).encode())
    assert _run(native, log, "new attempt", "--update-log-init") == 0
    _bounded(log, backup)
    assert b"new attempt" in log.read_bytes()
    assert b"current" in backup.read_bytes()
    assert b"previous log tail retained" in backup.read_bytes()


def test_actual_native_flood_keeps_both_slots_bounded_and_one_rotation(native, tmp_path):
    log, backup = _paths(tmp_path)
    assert _run(native, log, "begin", "--update-log-init") == 0
    children = []
    for i in range(120):
        message = f"record {i}: " + "😀" * 2000
        child = subprocess.Popen([str(native), "--update-log-append", str(log), message])
        children.append(child)
        while child.poll() is None:
            # Windows denies write while reading; size observation needs no open.
            for p in (log, backup):
                try:
                    assert p.stat().st_size <= LIMIT
                except FileNotFoundError:
                    pass
            time.sleep(0.005)
        assert child.returncode == 0
        _bounded(log, backup)
    assert b"record 119:" in log.read_bytes()
    assert backup.exists() and not list(log.parent.glob("install.log.[2-9]*"))


@pytest.mark.parametrize("leaf", ["install.log", "install.log.1", ".install-log.lock"])
def test_foreign_multilink_log_leaves_preserved(native, tmp_path, leaf):
    log, _ = _paths(tmp_path)
    foreign = tmp_path / "foreign.txt"
    foreign.write_bytes(b"foreign bytes")
    target = log.parent / leaf
    os.link(foreign, target)
    assert _run(native, log, "begin", "--update-log-init") == 3
    assert foreign.read_bytes() == target.read_bytes() == b"foreign bytes"


@pytest.mark.parametrize("leaf", ["install.log", "install.log.1", ".install-log.lock"])
def test_foreign_directory_at_log_leaf_preserved(native, tmp_path, leaf):
    log, _ = _paths(tmp_path)
    target = log.parent / leaf
    target.mkdir()
    sentinel = target / "foreign.txt"
    sentinel.write_bytes(b"preserve")
    assert _run(native, log, "begin", "--update-log-init") == 3
    assert sentinel.read_bytes() == b"preserve"


def test_interrupted_final_utf8_character_is_repaired_but_invalid_interior_is_refused(native, tmp_path):
    log, backup = _paths(tmp_path)
    log.write_bytes(b"last complete\n\xf0\x9f")
    assert _run(native, log) == 0
    _bounded(log, backup)
    before = b"foreign \xff interior"
    log.write_bytes(before)
    assert _run(native, log) == 3 and log.read_bytes() == before


def test_terminated_actual_writer_releases_all_log_handles(log_pausing_native, native, tmp_path):
    log, backup = _paths(tmp_path)
    log.write_bytes(b"old active\n")
    backup.write_bytes(b"old backup\n")
    child = _paused(log_pausing_native, log, tmp_path, "locked")
    try:
        child.kill()
        child.wait(timeout=3)
        assert _run(native, log, "after writer death", "--update-log-init") == 0
        _bounded(log, backup)
        assert b"after writer death" in log.read_bytes()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)


@pytest.mark.parametrize("prior_backup", [False, True])
def test_raced_backup_is_never_clobbered_after_proven_old_slot_deletion(log_pausing_native, tmp_path, prior_backup):
    log, backup = _paths(tmp_path)
    log.write_bytes(b"old active\n")
    if prior_backup:
        backup.write_bytes(b"old verified backup\n")
    child = _paused(log_pausing_native, log, tmp_path, "rename")
    try:
        assert not backup.exists()
        backup.write_bytes(b"foreign raced backup")
        raced = backup.stat()
        # Resume the actual rename after placing a new occupant. ReplaceIfExists
        # would overwrite this file and return success, making this case fail.
        (tmp_path / "rename.txt").unlink()
        assert child.wait(timeout=3) == 3
        assert backup.read_bytes() == b"foreign raced backup"
        assert backup.stat().st_ino == raced.st_ino
        assert log.read_bytes() == b"old active\n"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)


def test_supervisor_terminates_only_its_actual_paused_writer_within_deadline(log_pausing_native, native, tmp_path):
    log, backup = _paths(tmp_path)
    marker = tmp_path / "timeout.txt"
    env = dict(os.environ, WG_LOG_TEST_PAUSE="locked", WG_LOG_TEST_MARKER=str(marker))
    start = time.monotonic()
    child = subprocess.run([str(log_pausing_native), "--update-log-init", str(log), "bounded"], env=env, timeout=7)
    assert child.returncode == 3 and marker.exists() and time.monotonic() - start < 7
    assert _run(native, log, "after timeout", "--update-log-init") == 0
    _bounded(log, backup)


@pytest.mark.parametrize("logging_blocked", [False, True])
def test_real_native_commit_caps_flooded_diagnostics_without_overriding_its_verdict(native, tmp_path, logging_blocked):
    root, record = _old_root(tmp_path, native)
    log, backup = _paths(tmp_path)
    assert subprocess.run([str(native), "--installer-prepare", str(root), "0.3.4", "0.3.5",
                           str(record), str(log)], timeout=15).returncode == 0
    for name in ("app", "runtime", "recovery"):
        (root / name / "new.txt").write_text(name)
    for name in BOOT:
        (root / ".wg-install-new" / name).write_bytes(("new:" + name).encode())
    log.write_bytes(("😀 old diagnostics\n" * 40000).encode())
    backup.write_bytes(("é older diagnostics\n" * 40000).encode())
    if logging_blocked:
        (log.parent / ".install-log.lock").write_bytes(b"foreign lock content")
    assert subprocess.run([str(native), "--installer-commit", str(root)], timeout=15).returncode == 0
    import json
    outcome = json.loads(record.read_text())
    assert outcome["result"] == "installed" and outcome["previousKept"] is False
    assert Path(outcome["log"]) == log
    assert not (root / ".upgrade-in-progress").exists()
    if logging_blocked:
        assert (log.parent / ".install-log.lock").read_bytes() == b"foreign lock content"
    else:
        _bounded(log, backup)
        assert b"Native verified outcome: installed; previousKept=false." in log.read_bytes()


def test_junction_log_parent_is_refused_without_target_changes(native, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    sentinel = real / "install.log"
    sentinel.write_bytes(b"foreign target")
    junction = tmp_path / "update-install"
    subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(real)],
                   check=True, capture_output=True)
    assert _run(native, junction / "install.log", "begin", "--update-log-init") == 3
    assert sentinel.read_bytes() == b"foreign target"
    assert not (real / ".install-log.lock").exists()
