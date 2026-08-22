"""Pure contracts for the macOS and Windows standalone bundle builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from launchers.macos import generate_icon
from scripts.build_bundle import (
    BundleBuilder,
    BundleError,
    MSVC_RUNTIME_DLLS,
    PRUNE_LIBRARY_GLOBS,
    PRUNE_RELATIVE_PATHS,
    WINDOWS_ICON_NAME,
    WINDOWS_LAUNCHER_NAME,
    WINDOWS_PLATFORM,
    WINDOWS_PTH_NAME,
    WINDOWS_PRUNE_RELATIVE_PATHS,
    build,
    copy_tracked_app_files,
    deterministic_zip,
    is_x64_pe,
    launcher_stub,
    locate_msvc_runtime_dlls,
    prune_runtime,
    substitute_info_plist,
    windows_desktop_bootstrap,
    windows_pth,
    write_app_manifest,
    write_checksum,
    write_json,
    write_runtime_manifest,
    write_windows_bootstrap,
)
from server.platform.paths import app_root
from shared.runtime_id import compute_runtime_id, runtime_id_from_files


def write_pe_stub(path: Path, *, machine: int = 0x8664) -> Path:
    """Write the smallest file the architecture probe accepts as a PE image."""

    pe_offset = 0x40
    header = bytearray(pe_offset + 6)
    header[0:2] = b"MZ"
    header[0x3C:0x40] = struct.pack("<I", pe_offset)
    header[pe_offset : pe_offset + 4] = b"PE\0\0"
    header[pe_offset + 4 : pe_offset + 6] = struct.pack("<H", machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + path.name.encode())
    return path


def test_runtime_id_hashes_requirements_pins_and_exact_python_without_separators(
    tmp_path: Path,
) -> None:
    requirements = b"fastapi==1\n"
    pins = b"git+https://example.invalid/module@abc\n"
    version = "3.13.12"
    expected = hashlib.sha256(requirements + pins + version.encode()).hexdigest()[:12]
    requirements_path = tmp_path / "runtime.txt"
    pins_path = tmp_path / "pins.txt"
    requirements_path.write_bytes(requirements)
    pins_path.write_bytes(pins)

    assert compute_runtime_id(requirements, pins, version) == expected
    assert runtime_id_from_files(requirements_path, pins_path, version) == expected


def test_manifest_writers_record_layer_identity_and_stable_hashes(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = tmp_path / "app"
    runtime.mkdir()
    app.mkdir()

    runtime_payload = write_runtime_manifest(
        runtime,
        python_version="3.13.12",
        runtime_id="0123456789ab",
        requirements=b"runtime\n",
        pins=b"pins\n",
    )
    app_payload = write_app_manifest(
        app,
        version="1.2.3",
        commit="a" * 40,
        runtime_id="0123456789ab",
    )

    assert runtime_payload == {
        "schemaVersion": 1,
        "python": "3.13.12",
        "platform": "macos-arm64",
        "requirementsSha256": hashlib.sha256(b"runtime\n").hexdigest(),
        "pinsSha256": hashlib.sha256(b"pins\n").hexdigest(),
        "runtimeId": "0123456789ab",
    }
    assert app_payload == {
        "schemaVersion": 1,
        "version": "1.2.3",
        "commit": "a" * 40,
        "runtimeId": "0123456789ab",
    }
    assert json.loads((runtime / "RUNTIME-MANIFEST.json").read_text()) == runtime_payload
    assert json.loads((app / "APP-MANIFEST.json").read_text()) == app_payload

    windows_runtime = tmp_path / "windows-runtime"
    windows_runtime.mkdir()
    windows_payload = write_runtime_manifest(
        windows_runtime,
        python_version="3.13.12",
        runtime_id="0123456789ab",
        requirements=b"runtime\n",
        pins=b"pins\n",
        platform_name=WINDOWS_PLATFORM,
    )
    assert windows_payload["platform"] == "windows-x86_64"


def test_git_ls_files_copy_never_includes_ignored_or_untracked_files(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    destination = tmp_path / "app"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "tracked.py").write_text("tracked = True\n", encoding="utf-8")
    (repo / "server" / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    (repo / "server" / "untracked.py").write_text("untracked = True\n", encoding="utf-8")
    (repo / ".gitignore").write_text("server/ignored.py\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "server/tracked.py"], cwd=repo, check=True)

    copied = copy_tracked_app_files(repo, destination)

    assert [path.as_posix() for path in copied] == ["server/tracked.py"]
    assert (destination / "server" / "tracked.py").is_file()
    assert not (destination / "server" / "ignored.py").exists()
    assert not (destination / "server" / "untracked.py").exists()


def test_prune_runtime_removes_the_contract_list_and_nested_test_caches(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    for relative in PRUNE_RELATIVE_PATHS:
        target = runtime / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    for relative in ("lib/tcl8.6", "lib/tk8.6", "lib/itcl4.2"):
        (runtime / relative).mkdir(parents=True)
    site = runtime / "lib" / "python3.13" / "site-packages"
    (site / "pip-26.0.1.dist-info").mkdir(parents=True)
    (site / "pip-26.0.1.dist-info" / "RECORD").write_text("", encoding="utf-8")
    bin_dir = runtime / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3.13").write_bytes(b"\xcf\xfa\xed\xfe")
    (bin_dir / "python3").symlink_to("python3.13")
    (bin_dir / "python").symlink_to("python3.13")
    (bin_dir / "uvicorn").write_text(
        "#!/tmp/wg-bundle-build/runtime/bin/python3.13\n", encoding="utf-8"
    )
    (bin_dir / "python3.13-config").write_text("#!/bin/sh\n", encoding="utf-8")
    for relative in ("package/tests", "package/sub/test", "package/__pycache__"):
        target = site / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    kept = site / "package" / "module.py"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("keep = True\n", encoding="utf-8")

    removed = prune_runtime(runtime)

    assert kept.is_file()
    assert not any((runtime / relative).exists() for relative in PRUNE_RELATIVE_PATHS)
    assert not (site / "package" / "tests").exists()
    assert not (site / "package" / "sub" / "test").exists()
    assert not (site / "package" / "__pycache__").exists()
    assert not (site / "pip-26.0.1.dist-info").exists()
    assert sorted(entry.name for entry in bin_dir.iterdir()) == [
        "python",
        "python3",
        "python3.13",
    ]
    assert (bin_dir / "python3").is_symlink()
    assert {"lib/tcl*", "lib/tk*", "lib/itcl*"} <= set(PRUNE_LIBRARY_GLOBS)
    assert removed


def test_windows_prune_runtime_uses_the_flat_install_layout(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    for relative in WINDOWS_PRUNE_RELATIVE_PATHS:
        target = runtime / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    for relative in ("tcl", "DLLs/tcl86t.dll", "DLLs/tk86t.dll", "DLLs/_tkinter.pyd"):
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if "." in target.name:
            target.write_bytes(b"dll")
        else:
            target.mkdir(exist_ok=True)
    site = runtime / "Lib" / "site-packages"
    for relative in ("package/tests", "package/sub/test", "package/__pycache__"):
        target = site / relative
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("remove", encoding="utf-8")
    kept = site / "package" / "module.py"
    kept.write_text("keep = True\n", encoding="utf-8")
    (runtime / "python.exe").write_bytes(b"python")
    (runtime / "pythonw.exe").write_bytes(b"pythonw")

    removed = prune_runtime(runtime, platform_name=WINDOWS_PLATFORM)

    assert kept.is_file()
    assert (runtime / "python.exe").is_file()
    assert (runtime / "pythonw.exe").is_file()
    assert not any((runtime / relative).exists() for relative in WINDOWS_PRUNE_RELATIVE_PATHS)
    assert not (runtime / "tcl").exists()
    assert not (runtime / "DLLs" / "_tkinter.pyd").exists()
    assert not (site / "package" / "tests").exists()
    assert removed


def test_info_plist_substitution_updates_both_bundle_versions(tmp_path: Path) -> None:
    template = tmp_path / "template.plist"
    output = tmp_path / "output.plist"
    with template.open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": "is.hornlab.waveguide-generator-v2",
                "CFBundleShortVersionString": "0.0.1",
                "CFBundleVersion": "0.0.1",
            },
            handle,
        )

    substitute_info_plist(template, output, "1.2.3")

    with output.open("rb") as handle:
        payload = plistlib.load(handle)
    assert payload["CFBundleShortVersionString"] == "1.2.3"
    assert payload["CFBundleVersion"] == "1.2.3"
    assert payload["CFBundleIdentifier"] == "is.hornlab.waveguide-generator-v2"


def test_launcher_stub_sets_bundle_root_and_keeps_rosetta_guard() -> None:
    stub = launcher_stub()

    assert "sysctl.proc_translated" in stub
    assert 'exec arch -arm64 "$0" "$@"' in stub
    assert "export WG2_BUNDLE=1" in stub
    assert 'export WG2_APP_ROOT="$RESOURCES/app"' in stub
    # Caches must leave the sealed bundle untouched.
    assert 'export PYTHONPYCACHEPREFIX="$CACHE_ROOT/pycache"' in stub
    assert 'export NUMBA_CACHE_DIR="$CACHE_ROOT/numba"' in stub
    assert 'CACHE_ROOT="$HOME/Library/Caches/WaveguideGenerator"' in stub
    assert 'exec "../runtime/bin/python3.13" -m launchers.desktop "$@"' in stub


def test_layer_zip_is_sorted_and_omits_appledouble_files(tmp_path: Path) -> None:
    source = tmp_path / "layer"
    source.mkdir()
    (source / "z.txt").write_text("last\n", encoding="utf-8")
    (source / "a.txt").write_text("first\n", encoding="utf-8")
    (source / "._a.txt").write_text("metadata\n", encoding="utf-8")
    (source / "include" / "nested").mkdir(parents=True)
    (source / "include" / "nested" / "header.h").write_text("/* stable */\n", encoding="utf-8")
    archive = tmp_path / "layer.zip"

    deterministic_zip(source, archive)

    with zipfile.ZipFile(archive) as handle:
        assert handle.namelist() == [
            "a.txt",
            "include/",
            "include/nested/",
            "include/nested/header.h",
            "z.txt",
        ]
        assert {item.date_time for item in handle.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_layer_zip_materializes_internal_file_symlinks_as_regular_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime"
    (source / "bin").mkdir(parents=True)
    (source / "bin" / "python3.13").write_bytes(b"python")
    (source / "bin" / "python3").symlink_to("python3.13")
    archive = tmp_path / "runtime.zip"

    deterministic_zip(source, archive)

    with zipfile.ZipFile(archive) as handle:
        alias = handle.getinfo("bin/python3")
        assert stat.S_IFMT(alias.external_attr >> 16) == stat.S_IFREG
        assert handle.read(alias) == b"python"


def test_platform_neutral_app_zip_is_byte_identical_across_host_metadata(
    tmp_path: Path,
) -> None:
    first = tmp_path / "mac-app"
    second = tmp_path / "windows-app"
    for root in (first, second):
        (root / "nested").mkdir(parents=True)
        (root / "nested" / "bytes.txt").write_bytes(b"preserve\r\nthese\x00bytes\n")
    (first / "nested" / "bytes.txt").chmod(0o755)
    (second / "nested" / "bytes.txt").chmod(0o600)
    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"

    for source, output in ((first, first_zip), (second, second_zip)):
        deterministic_zip(
            source,
            output,
            canonical_modes=True,
        )

    assert first_zip.read_bytes() == second_zip.read_bytes()
    with zipfile.ZipFile(first_zip) as archive:
        assert archive.read("nested/bytes.txt") == b"preserve\r\nthese\x00bytes\n"
        assert archive.getinfo("nested/bytes.txt").compress_type == zipfile.ZIP_DEFLATED


def test_windows_distribution_zip_retains_the_enclosing_folder(tmp_path: Path) -> None:
    source = tmp_path / "Waveguide Generator"
    (source / "app").mkdir(parents=True)
    (source / "Waveguide Generator.exe").write_bytes(b"launcher")
    archive = tmp_path / "Waveguide Generator-1.2.3-windows-x86_64.zip"

    deterministic_zip(source, archive, archive_root=source.name)

    with zipfile.ZipFile(archive) as handle:
        assert handle.namelist() == [
            "Waveguide Generator/",
            "Waveguide Generator/Waveguide Generator.exe",
            "Waveguide Generator/app/",
        ]


def test_windows_pth_and_bootstrap_keep_direct_launch_separate_from_workers(
    tmp_path: Path,
) -> None:
    expected = "runtime\\Lib\nruntime\\DLLs\nruntime\\Lib\\site-packages\napp\nimport site\n"
    assert windows_pth() == expected
    bootstrap = windows_desktop_bootstrap()
    assert 'os.environ["WG2_BUNDLE"] = "1"' in bootstrap
    assert 'os.environ["WG2_APP_ROOT"] = str(app_root)' in bootstrap
    assert "from launchers.desktop import main" in bootstrap
    assert "_report_startup_failure" in bootstrap
    # An update renames the app layer out from under the running process, and
    # Windows refuses to rename any process's current directory.
    assert "os.chdir(bundle_root)" in bootstrap
    assert "os.chdir(app_root)" not in bootstrap
    # PYTHONPYCACHEPREFIX is read at interpreter start-up, so setting it in the
    # environment here only ever reaches children.
    assert "sys.pycache_prefix = os.environ[" in bootstrap

    app = tmp_path / "app"
    app.mkdir()
    write_windows_bootstrap(app)
    assert (app / "wg_desktop_bootstrap.py").read_text(encoding="utf-8") == bootstrap
    assert (app / "sitecustomize.py").read_text(encoding="utf-8") == (
        "import wg_desktop_bootstrap\n"
    )


def _bootstrap_launch_predicate() -> object:
    """Compile only the bootstrap's declarations, without running its body."""

    head, separator, _ = windows_desktop_bootstrap().partition("\n_drop_user_site()")
    assert separator, "the bootstrap no longer calls _drop_user_site at import time"
    namespace: dict[str, object] = {}
    exec(compile(head, "wg_desktop_bootstrap", "exec"), namespace)  # noqa: S102
    return namespace["_is_direct_launch"]


@pytest.mark.parametrize(
    ("argv0", "executable", "expected"),
    [
        # A double-click: CPython leaves argv[0] empty because it was given no
        # script, no -c and no -m. This is the case the bundle exists for, and
        # the one a `-c` build probe can never reach.
        ("", r"C:\Apps\Waveguide Generator\Waveguide Generator.exe", True),
        ("", r"C:\Apps\Waveguide Generator\WAVEGUIDE GENERATOR.EXE", True),
        # Worker subprocesses reuse the launcher as sys.executable and must
        # stay inert, whichever form they take.
        ("-c", r"C:\Apps\Waveguide Generator\Waveguide Generator.exe", False),
        (
            r"C:\Apps\Waveguide Generator\app\launch\serve.py",
            r"C:\Apps\Waveguide Generator\Waveguide Generator.exe",
            False,
        ),
        # And a plain interpreter is never the launcher.
        ("", r"C:\Python313\python.exe", False),
    ],
)
def test_direct_launch_is_recognised_only_for_an_argument_free_launcher_start(
    monkeypatch: pytest.MonkeyPatch, argv0: str, executable: str, expected: bool
) -> None:
    predicate = _bootstrap_launch_predicate()
    monkeypatch.setattr(sys, "argv", [argv0])
    monkeypatch.setattr(sys, "executable", executable)
    assert predicate() is expected


def test_manifest_and_checksum_writers_always_emit_lf(tmp_path: Path) -> None:
    """CRLF here breaks the release workflow's cross-platform identity check."""

    manifest = tmp_path / "APP-MANIFEST.json"
    write_json(manifest, {"schemaVersion": 1, "version": "0.2.4"})
    assert b"\r\n" not in manifest.read_bytes()

    asset = tmp_path / "layer.zip"
    asset.write_bytes(b"payload")
    sidecar = write_checksum(asset)
    body = sidecar.read_bytes()
    assert b"\r\n" not in body
    assert body.endswith(b"  layer.zip\n")


def test_msvc_dll_discovery_uses_where_then_system32_then_visual_studio(
    tmp_path: Path,
) -> None:
    system_root = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    where_dll = tmp_path / "path" / MSVC_RUNTIME_DLLS[0]
    system_dll = system_root / "System32" / MSVC_RUNTIME_DLLS[1]
    visual_dll = (
        program_files
        / "Microsoft Visual Studio"
        / "2022"
        / "BuildTools"
        / "VC"
        / "Redist"
        / "MSVC"
        / "14.40"
        / "x64"
        / "Microsoft.VC143.CRT"
        / MSVC_RUNTIME_DLLS[2]
    )
    for path in (where_dll, system_dll, visual_dll):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_pe_stub(path)
    # System32 also carries the DLL that where.exe reports, so the two sources
    # compete for the same name.
    system_first = system_root / "System32" / MSVC_RUNTIME_DLLS[0]
    write_pe_stub(system_first)

    def where(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output = str(where_dll) if command[-1] == where_dll.name else ""
        return subprocess.CompletedProcess(command, 0 if output else 1, output, "")

    located = locate_msvc_runtime_dlls(
        runner=where,
        environ={
            "SystemRoot": str(system_root),
            "ProgramFiles": str(program_files),
        },
    )

    # The installed redistributable wins over whatever sits first on PATH: a
    # where.exe hit is some unrelated application's private copy as often as
    # not, and the redistributable is only supported as a matched set.
    assert located == {
        MSVC_RUNTIME_DLLS[0]: system_first.resolve(),
        MSVC_RUNTIME_DLLS[1]: system_dll.resolve(),
        MSVC_RUNTIME_DLLS[2]: visual_dll.resolve(),
    }


def test_msvc_dll_discovery_skips_a_32_bit_candidate(tmp_path: Path) -> None:
    """A 32-bit DLL beside a 64-bit interpreter fails at load with no useful error."""

    system_root = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    redist = program_files / "Microsoft Visual Studio" / "2022" / "VC" / "x64"
    for name in MSVC_RUNTIME_DLLS:
        write_pe_stub(system_root / "System32" / name, machine=0x014C)
        write_pe_stub(redist / name)

    located = locate_msvc_runtime_dlls(
        runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", ""),
        environ={"SystemRoot": str(system_root), "ProgramFiles": str(program_files)},
    )

    assert located == {name: (redist / name).resolve() for name in MSVC_RUNTIME_DLLS}


def test_msvc_dll_discovery_fails_loudly_when_a_required_file_is_absent(
    tmp_path: Path,
) -> None:
    with pytest.raises(BundleError, match=MSVC_RUNTIME_DLLS[0]):
        locate_msvc_runtime_dlls(
            runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", ""),
            environ={
                "SystemRoot": str(tmp_path / "Windows"),
                "ProgramFiles": str(tmp_path / "Program Files"),
            },
        )


def test_pe_architecture_probe_reads_the_machine_field(tmp_path: Path) -> None:
    assert is_x64_pe(write_pe_stub(tmp_path / "x64.dll")) is True
    assert is_x64_pe(write_pe_stub(tmp_path / "x86.dll", machine=0x014C)) is False
    not_pe = tmp_path / "text.dll"
    not_pe.write_bytes(b"not an executable at all")
    assert is_x64_pe(not_pe) is False
    assert is_x64_pe(tmp_path / "missing.dll") is False


def test_windows_runtime_build_requests_the_explicit_host_target_and_copies_msvc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "requirements-runtime.txt").write_bytes(b"runtime\n")
    (repo / "server" / "requirements-pins.txt").write_bytes(b"pins\n")
    # Point the two preferred sources at empty directories so this stays a test
    # of the builder rather than of whatever the host happens to have installed.
    monkeypatch.setenv("SystemRoot", str(tmp_path / "no-windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-program-files"))
    redist = tmp_path / "redist"
    redist.mkdir()
    for filename in MSVC_RUNTIME_DLLS:
        write_pe_stub(redist / filename)
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["uv", "python", "install"]:
            install_dir = Path(command[command.index("--install-dir") + 1])
            runtime = install_dir / "managed"
            (runtime / "Lib" / "site-packages").mkdir(parents=True)
            (runtime / "DLLs").mkdir()
            for filename in ("python.exe", "pythonw.exe", "python313.dll", "python3.dll"):
                (runtime / filename).write_bytes(filename.encode())
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "where.exe":
            path = redist / command[1]
            return subprocess.CompletedProcess(command, 0, str(path), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    destination = tmp_path / "scratch" / "runtime"
    destination.parent.mkdir()
    builder = BundleBuilder(repo, runner=run, system=lambda: "Windows", machine=lambda: "AMD64")

    builder.build_runtime(
        destination,
        python_version="3.13.12",
        runtime_id="0123456789ab",
        platform_name=WINDOWS_PLATFORM,
    )

    install = next(command for command in commands if command[:3] == ["uv", "python", "install"])
    assert install[-1] == "cpython-3.13.12-windows-x86_64-none"
    pip = next(command for command in commands if command[:3] == ["uv", "pip", "install"])
    assert pip[pip.index("--python") + 1] == str(destination / "python.exe")
    for filename in MSVC_RUNTIME_DLLS:
        assert (destination / filename).read_bytes() == (redist / filename).read_bytes()
    manifest = json.loads((destination / "RUNTIME-MANIFEST.json").read_text())
    assert manifest["platform"] == "windows-x86_64"


def test_windows_layout_writer_copies_launcher_dlls_layers_pth_and_icon(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime-source"
    app = tmp_path / "app-source"
    destination = tmp_path / "Waveguide Generator"
    runtime.mkdir()
    app.mkdir()
    (runtime / "pythonw.exe").write_bytes(b"gui python")
    (runtime / "python313.dll").write_bytes(b"python dll")
    (runtime / "python3.dll").write_bytes(b"stable dll")
    for filename in MSVC_RUNTIME_DLLS:
        (runtime / filename).write_bytes(filename.encode())
    (runtime / "Lib").mkdir()
    (runtime / "Lib" / "os.py").write_text("# stdlib\n", encoding="utf-8")
    (app / "APP-MANIFEST.json").write_text("{}\n", encoding="utf-8")
    builder = BundleBuilder(tmp_path)

    builder.assemble_windows_bundle(
        destination,
        runtime_root=runtime,
        app_root=app,
        icon_writer=lambda path: path.write_bytes(b"ico"),
    )

    assert (destination / WINDOWS_LAUNCHER_NAME).read_bytes() == b"gui python"
    assert (destination / WINDOWS_PTH_NAME).read_text(encoding="utf-8") == windows_pth()
    assert (destination / WINDOWS_ICON_NAME).read_bytes() == b"ico"
    assert (destination / "runtime" / "Lib" / "os.py").is_file()
    assert (destination / "app" / "APP-MANIFEST.json").is_file()
    for filename in ("python313.dll", "python3.dll", *MSVC_RUNTIME_DLLS):
        assert (destination / filename).read_bytes() == (runtime / filename).read_bytes()


def test_stdlib_icon_generator_writes_a_multi_resolution_ico(tmp_path: Path) -> None:
    icon = tmp_path / "WaveguideGenerator.ico"

    generate_icon.build_ico(icon)

    payload = icon.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", payload)
    assert (reserved, kind, count) == (0, 1, len(generate_icon.ICO_SIZES))
    widths = [payload[6 + index * 16] or 256 for index in range(count)]
    assert widths == list(generate_icon.ICO_SIZES)
    assert b"\x89PNG\r\n\x1a\n" in payload


def test_windows_target_refuses_a_non_windows_host_before_building(tmp_path: Path) -> None:
    builder = BundleBuilder(
        tmp_path,
        system=lambda: "Darwin",
        machine=lambda: "arm64",
    )
    args = SimpleNamespace(
        platform="windows",
        python_version="3.13.12",
        output=tmp_path / "output",
        app_only=False,
        runtime_only=False,
        spa=None,
        skip_verify=True,
    )

    with pytest.raises(BundleError, match="Windows host.*cannot cross-install"):
        build(args, builder=builder)


def test_app_root_uses_checkout_by_default_and_bundle_environment_when_set(
    tmp_path: Path,
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    bundled = tmp_path / "Resources" / "app"

    assert app_root(environ={}) == checkout
    assert app_root(environ={"WG2_APP_ROOT": str(bundled)}) == bundled.resolve()
