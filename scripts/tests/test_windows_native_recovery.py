"""Native Win32 behavior runs only on Windows; build contracts run everywhere."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import struct
import threading
import time
import uuid
import zlib

import pytest

from scripts import build_bundle

pytestmark = pytest.mark.xdist_group("windows_native_setup_mutex")

ROOT = Path(__file__).resolve().parents[2]
BOOT = ["wg-python.exe", "python313.dll", "python3.dll", "vcruntime140.dll",
        "vcruntime140_1.dll", "msvcp140.dll", "wg-python._pth",
        "Waveguide Generator._pth", "pyvenv.cfg", "WaveguideGenerator.ico"]
NATIVE_TIMEOUT = 180  # Native admission/transaction locks themselves allow 120 s.
READY_TIMEOUT = 60
EXIT_TIMEOUT = 30


def _read_pause_marker(marker: Path, helper: subprocess.Popen, *, timeout: float = READY_TIMEOUT,
                       expected: str = "ready") -> str:
    """Observe complete, closed publication, not merely pathname creation."""
    until = time.monotonic() + timeout
    while True:
        try:
            value = marker.read_text()
            if value == expected:
                return value
            assert not value, f"unexpected marker {marker}: {value!r}"
            raise FileNotFoundError(f"marker {marker} has not published {expected!r}")
        except (FileNotFoundError, PermissionError) as error:
            # Publication can remain temporarily unreadable on Windows. Only
            # the private ready marker gets this bounded retry.
            if helper.poll() is not None or time.monotonic() >= until:
                error.add_note(f"waiting for {marker}; helper exit={helper.returncode}")
                raise
        time.sleep(0.01)


@pytest.mark.parametrize("transient", [FileNotFoundError, PermissionError])
def test_pause_marker_waits_for_readable_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    transient: type[OSError]) -> None:
    marker = tmp_path / "paused.txt"
    marker.write_text("ready")
    read_text = Path.read_text
    attempts = []

    def read(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise transient("publication is not yet readable")
        return read_text(path)

    class Helper:
        def poll(self):
            return None

    monkeypatch.setattr(Path, "read_text", read)
    assert _read_pause_marker(marker, Helper()) == "ready"
    assert attempts == [marker, marker]


@pytest.mark.parametrize("stop", ["deadline", "process_exit"])
def test_pause_marker_sharing_refusal_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str) -> None:
    attempts = []
    sleeps = []

    def refused(path):
        attempts.append(path)
        raise PermissionError("marker remains locked")

    class Helper:
        returncode = 3 if stop == "process_exit" else None

        def poll(self):
            return 3 if stop == "process_exit" else None

    clock = iter([10, 29, 30])
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", refused)
        patch.setattr(time, "monotonic", lambda: next(clock))
        patch.setattr(time, "sleep", sleeps.append)
        with pytest.raises(PermissionError, match="marker remains locked"):
            _read_pause_marker(tmp_path / "paused.txt", Helper(), timeout=20)
    assert len(attempts) == (2 if stop == "deadline" else 1)
    assert sleeps == ([0.01] if stop == "deadline" else [])


def test_pause_marker_waits_for_payload_even_when_path_exists(tmp_path, monkeypatch):
    marker = tmp_path / "paused.txt"
    marker.write_text("")

    class Helper:
        def poll(self):
            return None

    monkeypatch.setattr(time, "sleep", lambda _: marker.write_text("ready"))
    assert _read_pause_marker(marker, Helper()) == "ready"


def _probe_fixture_file(path: Path, *, write: bool) -> None:
    """Match native flush/image access without changing any fixture bytes."""
    if sys.platform != "win32":
        with path.open("r+b" if write else "rb"):
            return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # DELETE access also checks sharing needed by prepare/commit/log rotation.
    handle = kernel.CreateFileW(str(path), 0x40010000 if write else 0x80000000,
                               1 if write else 5, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _wait_for_fixture_files(paths, *, write: bool = True, timeout: float = READY_TIMEOUT) -> None:
    """Wait out sharing locks from fixture publication, never retry a native verdict.

    Closing Python's writer is not evidence that a Windows scanner has released
    its handle. This gate is only for bytes the test itself has just created;
    malformed/foreign product objects are still passed straight to the helper.
    """
    for path in paths:
        until = time.monotonic() + timeout
        while True:
            try:
                _probe_fixture_file(path, write=write)
                break
            except PermissionError as error:
                if time.monotonic() >= until:
                    error.add_note(f"fixture file still unavailable: {path}")
                    raise
            time.sleep(0.01)


def test_fixture_sharing_gate_waits_without_rewriting_bytes(tmp_path, monkeypatch):
    target = tmp_path / "boot.dll"
    target.write_bytes(b"exact staged bytes")
    probe = _probe_fixture_file
    attempts = []

    def locked_once(path, *, write):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError("scanner still holds fixture")
        probe(path, write=write)

    monkeypatch.setattr(__name__ + "._probe_fixture_file", locked_once)
    _wait_for_fixture_files([target])
    assert attempts == [target, target]
    assert target.read_bytes() == b"exact staged bytes"


def test_fixture_sharing_gate_is_bounded_and_missing_files_fail(tmp_path, monkeypatch):
    target = tmp_path / "boot.dll"
    with pytest.raises(FileNotFoundError):
        _wait_for_fixture_files([target])

    def locked(path, *, write):
        raise PermissionError("scanner still holds fixture")

    monkeypatch.setattr(__name__ + "._probe_fixture_file", locked)
    with pytest.raises(PermissionError, match="scanner still holds fixture"):
        _wait_for_fixture_files([target], timeout=0)


def _old_receiver():
    spec = importlib.util.spec_from_file_location("wg_v032_apply_update", ROOT / "scripts/tests/fixtures/windows_v032/apply_update.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compiled_source_changes_shared_runtime_identity(tmp_path: Path) -> None:
    source = tmp_path / build_bundle.WINDOWS_NATIVE_SOURCE
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first source")
    hook = tmp_path / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE
    hook.write_bytes(b"first hook")
    first = build_bundle.runtime_recipe(tmp_path)
    source.write_bytes(b"changed source")
    assert first != build_bundle.runtime_recipe(tmp_path)
    assert first.startswith("wg2-bundle-runtime-v3:windows-native-")
    second = build_bundle.runtime_recipe(tmp_path)
    hook.write_bytes(b"changed hook")
    assert second != build_bundle.runtime_recipe(tmp_path)


def test_manifest_bridge_refreshes_both_native_entry_and_worker_python(tmp_path: Path) -> None:
    root = tmp_path / "WG"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    (root / "Waveguide Generator.exe").write_bytes(b"old pythonw")
    entries = []
    for source, destination in build_bundle.windows_launcher_files():
        (runtime / source).write_bytes(source.encode())
        entries.append({"source": source, "destination": destination})
    (runtime / "RUNTIME-MANIFEST.json").write_text(json.dumps({"launcherFiles": entries}))
    _old_receiver().refresh_launcher_files(root)
    assert (root / "Waveguide Generator.exe").read_bytes() == b"wg-native.exe"
    assert (root / "wg-python.exe").read_bytes() == b"pythonw.exe"
    assert (root / "Waveguide Generator.exe.previous").read_bytes() == b"old pythonw"
    assert all("/" not in e["destination"] and "\\" not in e["destination"] for e in entries)


@pytest.mark.parametrize("stop_after", range(len(build_bundle.windows_launcher_files())))
def test_exact_old_receiver_death_between_publications_never_exposes_native_without_dependencies(tmp_path: Path,
                                                                                                  stop_after: int) -> None:
    class ReceiverDeath(BaseException):
        pass

    root = tmp_path / "WG"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    public = root / "Waveguide Generator.exe"
    public.write_bytes(b"previous pythonw")
    entries = []
    for source, destination in build_bundle.windows_launcher_files():
        (runtime / source).write_bytes(source.encode())
        entries.append({"source": source, "destination": destination})
    (runtime / "RUNTIME-MANIFEST.json").write_text(json.dumps({"launcherFiles": entries}))
    published = 0

    def rename(source, target):
        nonlocal published
        source.rename(target)
        if source.parent.name == ".launcher-update":
            published += 1
            if published == stop_after + 1:
                raise ReceiverDeath  # bypass caught-error rollback like process death

    with pytest.raises(ReceiverDeath):
        _old_receiver().refresh_launcher_files(root, renamer=rename)
    if public.read_bytes() == b"wg-native.exe":
        for source, destination in build_bundle.windows_launcher_files()[:-1]:
            assert (root / destination).read_bytes() == source.encode()
    else:
        assert public.read_bytes() == b"previous pythonw"


@pytest.fixture(scope="module")
def native(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if sys.platform != "win32":
        pytest.skip("requires real Windows C compiler and Win32 filesystem/process APIs")
    target = tmp_path_factory.mktemp("native") / "helper.exe"
    build_bundle.write_windows_launcher(target, repo_root=ROOT)
    _wait_for_fixture_files([target], write=False)
    return target


@pytest.fixture(scope="module")
def changed_natives(native: Path, tmp_path_factory: pytest.TempPathFactory):
    variants = []
    for version in ("B", "B1", "B2"):
        repo = tmp_path_factory.mktemp("native-" + version)
        source = repo / build_bundle.WINDOWS_NATIVE_SOURCE
        source.parent.mkdir(parents=True)
        shutil.copy2(ROOT / build_bundle.WINDOWS_NATIVE_SOURCE, source)
        hook = (ROOT / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE).read_bytes() + f"\n# packaged hook {version}\n".encode()
        (repo / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE).write_bytes(hook)
        target = repo / "helper.exe"
        build_bundle.write_windows_launcher(target, repo_root=repo)
        _wait_for_fixture_files([target], write=False)
        variants.append((target, hook))
    return variants


@pytest.fixture(scope="module")
def pausing_native(native: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Instrument only the private test compile, around the real Win32 I/O."""
    repo = tmp_path_factory.mktemp("pausing-native")
    source = repo / build_bundle.WINDOWS_NATIVE_SOURCE
    source.parent.mkdir(parents=True)
    code = (ROOT / build_bundle.WINDOWS_NATIVE_SOURCE).read_text()
    pause = r'''
static void test_pause(const wchar_t *target, const wchar_t *stage) {
    wchar_t wanted[64], kind[64], marker[1024], temporary[1024]; DWORD written; HANDLE file;
    const wchar_t *leaf = wcsrchr(target, L'\\');
    if (!GetEnvironmentVariableW(L"WG_NATIVE_TEST_STAGE", wanted, 64) || wcscmp(wanted, stage) ||
        !GetEnvironmentVariableW(L"WG_NATIVE_TEST_TARGET", kind, 64) || !leaf || wcscmp(leaf + 1, kind) ||
        !GetEnvironmentVariableW(L"WG_NATIVE_TEST_MARKER", marker, 1024)) return;
    /* Publish readiness only after the exclusive publishing handle closes. */
    if (swprintf_s(temporary, 1024, L"%s.%lu.tmp", marker, GetCurrentProcessId()) <= 0) ExitProcess(3);
    file = CreateFileW(temporary, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) ExitProcess(3);
    if (!WriteFile(file, "ready", 5, &written, NULL) || written != 5 || !FlushFileBuffers(file)) ExitProcess(3);
    if (!CloseHandle(file) || !MoveFileExW(temporary, marker, MOVEFILE_WRITE_THROUGH)) ExitProcess(3);
    for (;;) Sleep(1000);
}
'''
    code = code.replace("static int hook_write(", pause + "\nstatic int hook_write(", 1)
    begin = code.index("static int hook_write(")
    end = code.index("static int hook_temp_name(", begin)
    part = code[begin:end].replace("    ok = WriteFile", "    test_pause(target, L\"created\");\n    ok = WriteFile", 1)
    part = part.replace("    if (ok) ok = SetFileInformationByHandle", "    if (ok) test_pause(target, L\"flushed\");\n    if (ok) ok = SetFileInformationByHandle", 1)
    part = part.replace("    if (!CloseHandle(h))", "    if (ok) test_pause(target, L\"cleared\");\n    if (!CloseHandle(h))", 1)
    source.write_text(code[:begin] + part + code[end:])
    code = source.read_text().replace("    if (equal_files(public_path, self)) return 0;",
                                    "    test_pause(public_path, L\"entry\");\n    if (equal_files(public_path, self)) return 0;", 1)
    source.write_text(code)
    hook = (ROOT / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE).read_bytes() + b"\n# interruption candidate\n"
    (repo / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE).write_bytes(hook)
    target = repo / "helper.exe"
    build_bundle.write_windows_launcher(target, repo_root=repo)
    _wait_for_fixture_files([target], write=False)
    return target


def _old_root(tmp_path: Path, native: Path) -> tuple[Path, Path]:
    root = tmp_path / "WG"
    root.mkdir()
    for name in ("app", "runtime", "recovery"):
        (root / name).mkdir()
        (root / name / "old.txt").write_text(name)
    for name in BOOT:
        (root / name).write_bytes(("old:" + name).encode())
    (root / "Waveguide Generator.exe").write_bytes(native.read_bytes())
    _wait_for_fixture_files([root / name for name in BOOT + ["Waveguide Generator.exe"]] +
                            [root / name / "old.txt" for name in ("app", "runtime", "recovery")])
    outcome = tmp_path / "outcome.json"
    return root, outcome


def _prepare_owner(command: list[str], outcome: Path) -> subprocess.Popen[str]:
    diagnostic = outcome.with_name(f"{outcome.name}.{uuid.uuid4().hex}.owner-log")
    code = ("import subprocess,sys;"
            f"result=subprocess.run(sys.argv[1:],capture_output=True,timeout={NATIVE_TIMEOUT});"
            "sys.stderr.buffer.write(result.stdout+result.stderr);sys.stderr.flush();"
            "assert result.returncode==0, f'prepare exited {result.returncode}';"
            "print('ready',flush=True);sys.stdin.read()")
    with diagnostic.open("ab") as transcript:
        owner = subprocess.Popen([sys.executable, "-c", code, *command], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=transcript, text=True)
    assert owner.stdout is not None
    ready = queue.Queue()
    # Windows pipe handles are not selectable. A reader thread delivers the
    # publication/EOF event without blocking pytest's deadline enforcement.
    reader = threading.Thread(target=lambda: ready.put(owner.stdout.readline()), daemon=True)
    reader.start()
    try:
        try:
            line = ready.get(timeout=NATIVE_TIMEOUT)
        except queue.Empty as error:
            raise TimeoutError(f"prepare owner {owner.pid} did not publish readiness") from error
        assert line.strip() == "ready", f"prepare owner {owner.pid} exited before readiness"
    except BaseException as error:
        _kill_owner(owner)
        error.add_note(diagnostic.read_text(errors="replace"))
        raise
    finally:
        reader.join(timeout=EXIT_TIMEOUT)
    return owner


def _prepare(native: Path, root: Path, outcome: Path, *, dead_owner: bool) -> subprocess.Popen[str] | None:
    command = [str(native), "--installer-prepare", str(root), "0.3.4", "0.3.5", str(outcome), ""]
    if dead_owner:
        # Hold a real owner until the partial/foreign objects are ready. The
        # monitor may restore immediately after owner.kill(), before startup.
        return _prepare_owner(command, outcome)
    result = subprocess.run(command, check=False, capture_output=True, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return None


def _kill_owner(owner: subprocess.Popen[str] | None) -> None:
    assert owner is not None
    if owner.poll() is None:
        owner.kill()
    owner.wait(timeout=EXIT_TIMEOUT)
    if owner.stdout is not None:
        owner.stdout.close()
    if owner.stdin is not None:
        owner.stdin.close()


def test_owner_ready_event_keeps_the_actual_prepare_parent_alive(tmp_path):
    parent = tmp_path / "parent.txt"
    command = [sys.executable, "-c",
               f"import os;from pathlib import Path;Path({str(parent)!r}).write_text(str(os.getppid()))"]
    owner = _prepare_owner(command, tmp_path / "outcome.json")
    try:
        assert owner.poll() is None
        assert int(parent.read_text()) == owner.pid
    finally:
        _kill_owner(owner)
    assert owner.poll() is not None


def test_failed_prepare_exits_without_a_ready_event_and_reports_its_diagnostics(tmp_path):
    command = [sys.executable, "-c", "import sys;print('native failure detail',file=sys.stderr);sys.exit(3)"]
    with pytest.raises(AssertionError, match="exited before readiness") as failure:
        _prepare_owner(command, tmp_path / "outcome.json")
    notes = "\n".join(failure.value.__notes__)
    assert "native failure detail" in notes and "prepare exited 3" in notes


def test_dead_owner_partial_runtime_is_restored_before_python(native: Path, tmp_path: Path) -> None:
    root, record = _old_root(tmp_path, native)
    owner = _prepare(native, root, record, dead_owner=True)
    try:
        (root / "runtime" / "partial.dll").write_bytes(b"truncated runtime")
        (root / "app" / "new.txt").write_text("partial app")
    finally:
        _kill_owner(owner)
    # -c stays inert after recovery; the fake Python image intentionally cannot
    # run. Restoration and its outcome must already be complete before that.
    subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "pass"], check=False, timeout=NATIVE_TIMEOUT)
    assert (root / "runtime" / "old.txt").read_text() == "runtime"
    assert not (root / "runtime" / "partial.dll").exists()
    assert not (root / "app" / "new.txt").exists()
    assert (root / "wg-python.exe").read_bytes() == b"old:wg-python.exe"
    assert json.loads(record.read_text())["previousKept"] is True
    assert json.loads(record.read_text())["result"] == "failed"
    assert not (root / ".upgrade-in-progress").exists()


def test_foreign_live_directory_is_preserved_and_real_backup_reported(native: Path, tmp_path: Path) -> None:
    root, record = _old_root(tmp_path, native)
    owner = _prepare(native, root, record, dead_owner=True)
    try:
        displaced = root / "owned-partial"
        (root / "runtime").rename(displaced)
        (root / "runtime").mkdir()
        (root / "runtime" / "foreign.txt").write_text("preserve")
    finally:
        _kill_owner(owner)
    result = subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "pass"], check=False, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 3
    assert (root / "runtime" / "foreign.txt").read_text() == "preserve"
    data = json.loads(record.read_text())
    assert data["result"] == "rollback_incomplete" and not data["previousKept"]
    assert Path(data["backupPath"]) == root / ".wg-install-old" / "runtime"
    assert Path(data["backupPath"]).is_dir()
    assert (root / ".upgrade-in-progress").exists()


def test_successful_commit_replaces_entire_layers_and_exact_boot_files(native: Path, tmp_path: Path) -> None:
    root, record = _old_root(tmp_path, native)
    _prepare(native, root, record, dead_owner=False)
    for name in ("app", "runtime", "recovery"):
        (root / name / "new.txt").write_text(name)
    for name in BOOT:
        (root / ".wg-install-new" / name).write_bytes(("new:" + name).encode())
    _wait_for_fixture_files([root / name / "new.txt" for name in ("app", "runtime", "recovery")] +
                            [root / ".wg-install-new" / name for name in BOOT])
    result = subprocess.run([str(native), "--installer-commit", str(root)], check=False, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0
    for name in ("app", "runtime", "recovery"):
        assert not (root / name / "old.txt").exists()
        assert (root / name / "new.txt").read_text() == name
    for name in BOOT:
        assert (root / name).read_bytes() == ("new:" + name).encode()
    assert json.loads(record.read_text())["result"] == "installed"
    assert not (root / ".upgrade-in-progress").exists()
    assert not (root / ".wg-install-old").exists()


def test_malformed_journal_never_touches_installed_files(native: Path, tmp_path: Path) -> None:
    root, record = _old_root(tmp_path, native)
    (root / ".upgrade-in-progress").write_bytes(b"truncated or foreign journal")
    before = (root / "runtime" / "old.txt").read_bytes()
    result = subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "pass"], check=False, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 3
    assert (root / "runtime" / "old.txt").read_bytes() == before
    assert not record.exists()


def _host_python_old_hook(root: Path) -> None:
    """Real host interpreter plus exact v0.3.2 hook bytes, not a release bundle."""
    base = Path(sys.base_prefix)
    python = base / "python.exe"
    assert python.is_file()
    shutil.copy2(python, root / "wg-python.exe")
    shutil.copy2(python, root / "Waveguide Generator.exe")
    (root / "pyvenv.cfg").write_text(build_bundle.windows_pyvenv_cfg())
    for name in ("python313.dll", "python3.dll"):
        shutil.copy2(base / name, root / name)
    for name, source in build_bundle.locate_msvc_runtime_dlls().items():
        shutil.copy2(source, root / name)

    def link_or_copy(source: str, target: str) -> str:
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        return target

    shutil.copytree(base / "Lib", root / "runtime/Lib", copy_function=link_or_copy,
                    ignore=shutil.ignore_patterns("__pycache__", "site-packages", "test", "ensurepip", "turtledemo"))
    shutil.copytree(base / "DLLs", root / "runtime/DLLs", copy_function=link_or_copy)
    fixture = ROOT / "scripts/tests/fixtures/windows_v032"
    for name in ("Waveguide Generator._pth",):
        shutil.copy2(fixture / name, root / name)
    (root / "wg-python._pth").unlink()
    shutil.copy2(fixture / "wg_desktop_bootstrap.py", root / "app/wg_desktop_bootstrap.py")
    shutil.copy2(fixture / "sitecustomize.py", root / "recovery/sitecustomize.py")
    for source, target in (("bundle_recovery.py", "wg_bundle_recovery.py"),
                           ("apply_update.py", "apply_update.py"), ("update_lock.py", "update_lock.py")):
        shutil.copy2(ROOT / "launchers" / source, root / "recovery" / target)
    (root / "app/launchers").mkdir()
    (root / "app/launchers/__init__.py").write_text("")
    (root / "app/launchers/desktop.py").write_text(
        "from pathlib import Path\nimport os\ndef main(argv):\n"
        " Path(os.environ['WG_TEST_STARTED']).write_text('old desktop ran')\n return 0\n")


def test_native_first_manual_rollback_starts_actual_old_hook_and_forwards_args(native: Path, tmp_path: Path) -> None:
    root, record = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    # The frozen direct-launch shim requires the packaged layer markers even
    # after native rollback. This host-Python fixture must model those files.
    manifests = {root / "app/APP-MANIFEST.json": b"{}\n", root / "runtime/RUNTIME-MANIFEST.json": b"{}\n"}
    for marker, data in manifests.items():
        marker.write_bytes(data)
    original_pth = (root / "Waveguide Generator._pth").read_bytes()
    assert subprocess.run([str(native), "--installer-entry", str(root)], check=False, timeout=NATIVE_TIMEOUT).returncode == 0
    owner = _prepare(native, root, record, dead_owner=True)
    try:
        (root / "runtime/partial.dll").write_bytes(b"incomplete")
    finally:
        _kill_owner(owner)
    started = tmp_path / "old-desktop.txt"
    environment = dict(os.environ, WG_TEST_STARTED=str(started), WG2_DATA_DIR=str(tmp_path / "private-data"),
                       WG2_FUSION_ADDINS_DIR=str(tmp_path / "private-AddIns"))
    result = subprocess.run([str(root / "Waveguide Generator.exe")], env=environment, check=False, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0 and started.read_text() == "old desktop ran"
    assert all(marker.read_bytes() == data for marker, data in manifests.items())
    assert (root / "Waveguide Generator._pth").read_bytes() == original_pth
    assert json.loads(record.read_text())["result"] == "failed"
    result = subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "import json,sys;print(json.dumps(sys.argv))", "value with spaces"],
                            env=environment, capture_output=True, text=True, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0 and json.loads(result.stdout) == ["-c", "value with spaces"]
    (root / "app/argv_probe.py").write_text("import json,sys;print(json.dumps(sys.argv[1:]))")
    result = subprocess.run([str(root / "Waveguide Generator.exe"), "-m", "argv_probe", "quoted value"],
                            env=environment, capture_output=True, text=True, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0 and json.loads(result.stdout) == ["quoted value"]
    # Nested workers return through the public native image too; stdout and
    # quoted argv survive both admissions.
    worker = ("import sys;from pathlib import Path;assert getattr(sys,'_wg_native_start_admitted',False);"
              f"assert Path(sys.executable)==Path({str(root / 'Waveguide Generator.exe')!r});print(sys.argv[1])")
    # Windows Popen with all streams None and default close_fds supplies no
    # standard handles to a GUI child. Explicit redirection transfers only the
    # intended streams; the native launcher must preserve them at both hops.
    code = (f"import subprocess,sys;subprocess.run([sys.executable,'-c',{worker!r},'nested value'],"
            "stdout=sys.stdout,stderr=sys.stderr,check=True)")
    result = subprocess.run([str(root / "Waveguide Generator.exe"), "-c", code], env=environment,
                            capture_output=True, text=True, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0 and result.stdout.strip() == "nested value"


def test_forged_capability_never_executes_worker_code(native: Path, tmp_path: Path) -> None:
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    assert subprocess.run([str(native), "--installer-entry", str(root)], check=False, timeout=NATIVE_TIMEOUT).returncode == 0
    # One legitimate inert invocation publishes the embedded hook/private pth.
    assert subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "pass"], check=False, timeout=NATIVE_TIMEOUT).returncode == 0
    worker_wrote = tmp_path / "forged-worker.txt"
    environment = dict(os.environ, WG_NATIVE_START=f"{os.getpid()},0,0,123,124,125,0")
    result = subprocess.run([str(root / "wg-python.exe"), "-B", "-c", f"open({str(worker_wrote)!r},'w').write('bad')"],
                            env=environment, check=False, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 4 and not worker_wrote.exists()


def test_missing_capability_refuses_active_setup(native: Path, tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    assert subprocess.run([str(native), "--installer-entry", str(root)], check=False, timeout=NATIVE_TIMEOUT).returncode == 0
    assert subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "pass"], check=False, timeout=NATIVE_TIMEOUT).returncode == 0
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = k.CreateMutexW(None, False, "WaveguideGeneratorSetup")
    assert mutex
    environment = dict(os.environ)
    environment.pop("WG_NATIVE_START", None)
    wrote = tmp_path / "unadmitted.txt"
    try:
        result = subprocess.run([str(root / "wg-python.exe"), "-B", "-c", f"open({str(wrote)!r},'w').write('bad')"],
                                env=environment, check=False, timeout=NATIVE_TIMEOUT)
        assert result.returncode == 4 and not wrote.exists()
    finally:
        k.CloseHandle(mutex)


def test_known_old_hook_refreshes_before_runtime_source_changes(native: Path, tmp_path: Path) -> None:
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    sidecar = root / ".native-start"
    sidecar.mkdir()
    old_hook = b"# prior packaged native admission hook\n"
    (sidecar / "sitecustomize.py").write_bytes(old_hook)
    (root / "runtime/wg-startup-hook.py").write_bytes(old_hook)
    # Native entry publication has access to the old source before prepare
    # moves its runtime. After it disappears, the embedded new hook is exact.
    assert subprocess.run([str(native), "--installer-entry", str(root)], check=False, timeout=NATIVE_TIMEOUT).returncode == 0
    expected = (ROOT / build_bundle.WINDOWS_NATIVE_HOOK_SOURCE).read_bytes()
    assert (sidecar / "sitecustomize.py").read_bytes() == expected
    (root / "runtime/wg-startup-hook.py").write_bytes(expected)
    result = subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "print('new hook admitted')"],
                            check=False, capture_output=True, text=True, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0 and result.stdout.strip() == "new hook admitted"


def test_junction_root_refused_before_any_native_entry_or_lock_write(native: Path, tmp_path: Path) -> None:
    target, _ = _old_root(tmp_path, native)
    previous = (target / "Waveguide Generator.exe").read_bytes()
    junction = tmp_path / "foreign-root"
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        result = subprocess.run([str(native), "--installer-entry", str(junction)], check=False, timeout=NATIVE_TIMEOUT)
        assert result.returncode == 3
        assert (target / "Waveguide Generator.exe").read_bytes() == previous
        assert not (target / ".wg-install-lock").exists()
        assert not (target / ".native-start").exists()
    finally:
        junction.rmdir()


def _entry(native: Path, root: Path) -> int:
    return subprocess.run([str(native), "--installer-entry", str(root)], check=False, timeout=NATIVE_TIMEOUT).returncode


def _run_admitted(root: Path) -> None:
    result = subprocess.run([str(root / "Waveguide Generator.exe"), "-c",
                             "import sys;assert sys._wg_native_start_admitted;print('admitted')"],
                            check=False, capture_output=True, text=True, timeout=NATIVE_TIMEOUT)
    assert result.returncode == 0 and result.stdout.strip() == "admitted", result.stderr


def test_changed_hook_rollback_then_next_setup_is_recognised(changed_natives, tmp_path: Path) -> None:
    (b, b_hook), (b1, b1_hook), (b2, b2_hook) = changed_natives
    root, record = _old_root(tmp_path, b)
    _host_python_old_hook(root)
    assert _entry(b, root) == 0
    (root / "runtime/wg-startup-hook.py").write_bytes(b_hook)
    _run_admitted(root)
    assert _entry(b1, root) == 0
    owner = _prepare(b1, root, record, dead_owner=True)
    try:
        (root / "runtime/wg-startup-hook.py").write_bytes(b1_hook)
        (root / "runtime/partial.dll").write_bytes(b"incomplete B1")
    finally:
        _kill_owner(owner)
    _run_admitted(root)
    assert (root / "runtime/wg-startup-hook.py").read_bytes() == b_hook
    assert (root / ".native-start/sitecustomize.py").read_bytes() == b1_hook
    assert json.loads(record.read_text())["result"] == "failed"
    assert _entry(b2, root) == 0  # neither previous sidecar nor runtime matches B2
    _run_admitted(root)
    assert (root / ".native-start/sitecustomize.py").read_bytes() == b2_hook


def test_exact_v032_bridge_can_swap_runtime_before_new_hook_admission(changed_natives, tmp_path: Path) -> None:
    (b, b_hook), (b1, b1_hook), _ = changed_natives
    root, _ = _old_root(tmp_path, b)
    _host_python_old_hook(root)
    assert _entry(b, root) == 0
    (root / "runtime/wg-startup-hook.py").write_bytes(b_hook)
    _run_admitted(root)
    (root / "runtime/wg-startup-hook.py").write_bytes(b1_hook)
    (root / "runtime/wg-native.exe").write_bytes(b1.read_bytes())
    (root / "runtime/RUNTIME-MANIFEST.json").write_text(json.dumps({
        "launcherFiles": [{"source": "wg-native.exe", "destination": "Waveguide Generator.exe"}]}))
    _old_receiver().refresh_launcher_files(root)
    assert (root / ".native-start/sitecustomize.py").read_bytes() == b_hook
    _run_admitted(root)
    assert (root / ".native-start/sitecustomize.py").read_bytes() == b1_hook


def test_interrupted_entry_publication_old_image_accepts_recorded_new_hook(changed_natives, tmp_path: Path) -> None:
    (b, b_hook), (b1, b1_hook), _ = changed_natives
    root, _ = _old_root(tmp_path, b)
    _host_python_old_hook(root)
    assert _entry(b, root) == 0
    (root / "runtime/wg-startup-hook.py").write_bytes(b_hook)
    _run_admitted(root)
    previous_image = (root / "Waveguide Generator.exe").read_bytes()
    assert _entry(b1, root) == 0
    # Exact on-disk boundary after durable hook/ledger publication and before
    # the public native image's atomic rename. No journal exists yet.
    (root / "Waveguide Generator.exe").write_bytes(previous_image)
    assert (root / ".native-start/sitecustomize.py").read_bytes() == b1_hook
    _run_admitted(root)
    assert (root / ".native-start/sitecustomize.py").read_bytes() == b_hook


def _history_rebuild(original: bytes, records: list[bytes]) -> bytes:
    header = bytearray(original[:80])
    payload = b"".join(struct.pack("<I", len(value)) + value for value in records)
    struct.pack_into("<III", header, 28, 80 + len(payload), len(records), 0)
    result = header + payload
    struct.pack_into("<I", result, 36, zlib.crc32(result))
    return bytes(result)


@pytest.mark.parametrize("malformation", ["crc", "total", "count", "length", "zero_length", "truncated", "trailing",
                                         "oversized", "foreign_directory"])
def test_malformed_or_foreign_history_refused_without_altering_hook(native: Path, tmp_path: Path, malformation: str) -> None:
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    assert _entry(native, root) == 0
    sidecar = root / ".native-start"
    ledger = sidecar / "known-hooks"
    original_hook = (sidecar / "sitecustomize.py").read_bytes()
    data = bytearray(ledger.read_bytes())
    if malformation == "crc":
        data[-1] ^= 1
    elif malformation == "total":
        struct.pack_into("<I", data, 28, 0xffffffff)
    elif malformation == "count":
        struct.pack_into("<I", data, 32, 0xffffffff)
    elif malformation in {"length", "zero_length", "truncated", "trailing"}:
        if malformation in {"length", "zero_length"}:
            struct.pack_into("<I", data, 80, 0xffffffff if malformation == "length" else 0)
        elif malformation == "truncated":
            data = data[:-1]
        else:
            data += b"x"
        struct.pack_into("<I", data, 28, len(data))
        struct.pack_into("<I", data, 36, 0)
        struct.pack_into("<I", data, 36, zlib.crc32(data))  # reach bounded record parsing
    elif malformation == "oversized":
        data = bytearray(b"x" * (1024 * 1024 + 1))
    else:
        sidecar.rename(root / ".previous-native-start")
        sidecar.mkdir()
        (sidecar / "sitecustomize.py").write_bytes(original_hook)
    ledger.write_bytes(data)
    assert _entry(native, root) == 3
    assert ledger.read_bytes() == data
    assert (sidecar / "sitecustomize.py").read_bytes() == original_hook


def test_history_exhaustion_preserves_previous_recognition(changed_natives, tmp_path: Path) -> None:
    (b, b_hook), (b1, _), _ = changed_natives
    root, _ = _old_root(tmp_path, b)
    _host_python_old_hook(root)
    assert _entry(b, root) == 0
    ledger = root / ".native-start/known-hooks"
    full = _history_rebuild(ledger.read_bytes(), [b_hook] + [f"known prior hook {i}\n".encode() for i in range(63)])
    ledger.write_bytes(full)
    assert _entry(b1, root) == 3
    assert ledger.read_bytes() == full
    assert (root / ".native-start/sitecustomize.py").read_bytes() == b_hook
    _run_admitted(root)  # recognised hooks still run when no new history fits


def test_unknown_sidecar_entry_and_hook_bytes_are_preserved(native: Path, tmp_path: Path) -> None:
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    assert _entry(native, root) == 0
    sidecar = root / ".native-start"
    unknown = sidecar / "foreign.tmp"
    unknown.write_bytes(b"not native-owned")
    assert _entry(native, root) == 3 and unknown.read_bytes() == b"not native-owned"
    unknown.unlink()
    (sidecar / "sitecustomize.py").write_bytes(b"# unknown hook\n")
    assert _entry(native, root) == 3
    assert (sidecar / "sitecustomize.py").read_bytes() == b"# unknown hook\n"


def test_legal_payload_becomes_long_backup_path_and_recovers(native: Path, tmp_path: Path) -> None:
    root, record = _old_root(tmp_path, native)
    # The payload remains below MAX_PATH; inserting .wg-install-old raises the
    # backup above it. Native traversal must work without registry long-path opt-in.
    base = root / "runtime"
    remaining = 250 - len(str(base)) - 1
    assert remaining > 30, "Windows test temp directory must leave payload path budget"
    relative = "n" * (remaining - 9) + "/old.txt"
    previous = base / relative
    previous.parent.mkdir()
    previous.write_bytes(b"deep previous payload")
    assert len(str(previous)) < 260 and len(str(root / ".wg-install-old/runtime" / relative)) > 260
    owner = _prepare(native, root, record, dead_owner=True)
    _kill_owner(owner)
    subprocess.run([str(root / "Waveguide Generator.exe"), "-c", "pass"], check=False, timeout=NATIVE_TIMEOUT)
    assert previous.read_bytes() == b"deep previous payload"
    assert json.loads(record.read_text())["previousKept"] is True
    assert not (root / ".upgrade-in-progress").exists()


@pytest.mark.parametrize("target", ["known-hooks", "sitecustomize.py"])
@pytest.mark.parametrize("stage", ["created", "flushed", "cleared"])
def test_actual_terminated_sidecar_writer_recovers(native: Path, pausing_native: Path, tmp_path: Path,
                                                   target: str, stage: str) -> None:
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    assert _entry(native, root) == 0
    _run_admitted(root)
    marker = tmp_path / "paused.txt"
    environment = dict(os.environ, WG_NATIVE_TEST_STAGE=stage, WG_NATIVE_TEST_TARGET=target,
                       WG_NATIVE_TEST_MARKER=str(marker))
    helper = subprocess.Popen([str(pausing_native), "--installer-entry", str(root)], env=environment)
    try:
        assert _read_pause_marker(marker, helper) == "ready"
        temporary = root / ".native-start" / f"{target}.{helper.pid}.tmp"
        helper.kill()
        helper.wait(timeout=EXIT_TIMEOUT)
        if stage == "cleared":
            assert temporary.exists()  # Ex genuinely cleared on-close deletion
        else:
            assert not temporary.exists()  # OS deleted even an interrupted writer
        _run_admitted(root)
        assert not temporary.exists()
        assert _entry(pausing_native, root) == 0  # full clear/close/rename also survives
        _run_admitted(root)
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=EXIT_TIMEOUT)


@pytest.mark.parametrize("target", ["known-hooks", "sitecustomize.py"])
def test_foreign_or_incomplete_native_named_temporary_preserved(native: Path, tmp_path: Path, target: str) -> None:
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    assert _entry(native, root) == 0
    temporary = root / ".native-start" / f"{target}.12345.tmp"
    temporary.write_bytes(b"foreign or interrupted partial data")
    assert _entry(native, root) == 3
    assert temporary.read_bytes() == b"foreign or interrupted partial data"


def test_actual_entry_termination_after_hook_before_public_image(native: Path, pausing_native: Path, tmp_path: Path) -> None:
    root, _ = _old_root(tmp_path, native)
    _host_python_old_hook(root)
    assert _entry(native, root) == 0
    _run_admitted(root)
    old_image = (root / "Waveguide Generator.exe").read_bytes()
    old_hook = (root / ".native-start/sitecustomize.py").read_bytes()
    marker = tmp_path / "entry-paused.txt"
    environment = dict(os.environ, WG_NATIVE_TEST_STAGE="entry", WG_NATIVE_TEST_TARGET="Waveguide Generator.exe",
                       WG_NATIVE_TEST_MARKER=str(marker))
    helper = subprocess.Popen([str(pausing_native), "--installer-entry", str(root)], env=environment)
    try:
        assert _read_pause_marker(marker, helper) == "ready"
        assert (root / "Waveguide Generator.exe").read_bytes() == old_image
        assert (root / ".native-start/sitecustomize.py").read_bytes() != old_hook
        helper.kill()
        helper.wait(timeout=EXIT_TIMEOUT)
        _run_admitted(root)
        assert (root / ".native-start/sitecustomize.py").read_bytes() == old_hook
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=EXIT_TIMEOUT)
