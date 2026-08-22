#!/usr/bin/env python3
"""Build relocatable Waveguide Generator release layers and a macOS app."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request
import zipfile


_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from scripts import fetch_spa  # noqa: E402
from server.platform.paths import app_root  # noqa: E402
from shared.runtime_id import runtime_id_from_files  # noqa: E402


REPO_ROOT = app_root()
# ``uv python list`` resolved the 3.13 request to this python-build-standalone
# patch release when the bundle implementation was written. Release builds use
# the full string so a later uv catalog update cannot silently change a layer.
PYTHON_VERSION = "3.13.12"
PYTHON_SERIES = "3.13"
MACOS_PLATFORM = "macos-arm64"
APP_TEMPLATE = Path("launchers/macos/Waveguide Generator.app")
APP_SOURCE_DIRECTORIES = (
    "server",
    "launch",
    "launchers",
    "shared",
    "scripts",
    "integrations/wglink",
    "docs",
)
APP_SOURCE_FILES = ("LICENSE", "README.md")
PRUNE_RELATIVE_PATHS = (
    "lib/python3.13/idlelib",
    "lib/python3.13/tkinter",
    "lib/python3.13/turtledemo",
    "lib/python3.13/ensurepip",
    "lib/python3.13/site-packages/pip",
)
PRUNE_LIBRARY_GLOBS = ("lib/tcl*", "lib/tk*", "lib/itcl*", "lib/python3.13/site-packages/pip-*.dist-info")
# The interpreter entries in bin/. Every other file there is a console-script
# wrapper whose shebang names the temporary build prefix; the bundle runs
# modules through python3.13 directly, so the wrappers would only ship a dead
# absolute path.
RUNTIME_BIN_KEEP = frozenset({"python", "python3", f"python{PYTHON_SERIES}"})
# The bundle is sealed by codesign, so nothing may be written into it after
# the build. Python would otherwise create __pycache__ beside every module it
# imports and numba would cache compiled kernels beside their source.
CACHE_ENVIRONMENT = ("PYTHONPYCACHEPREFIX", "NUMBA_CACHE_DIR")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
METAL_HELPER = Path(
    "lib/python3.13/site-packages/hornlab_metal_bem/metal/native_helper/"
    ".build/release/HornlabMetalBemNative"
)

RunCallable = Callable[..., subprocess.CompletedProcess[Any]]
ProcessFactory = Callable[..., subprocess.Popen[str]]


class BundleError(RuntimeError):
    """A release bundle could not be built or verified safely."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_runtime_manifest(
    runtime_root: Path,
    *,
    python_version: str,
    runtime_id: str,
    requirements: bytes,
    pins: bytes,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "python": python_version,
        "platform": MACOS_PLATFORM,
        "requirementsSha256": sha256_bytes(requirements),
        "pinsSha256": sha256_bytes(pins),
        "runtimeId": runtime_id,
    }
    write_json(runtime_root / "RUNTIME-MANIFEST.json", payload)
    return payload


def write_app_manifest(
    app_root: Path,
    *,
    version: str,
    commit: str,
    runtime_id: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "version": version,
        "commit": commit,
        "runtimeId": runtime_id,
    }
    write_json(app_root / "APP-MANIFEST.json", payload)
    return payload


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def prune_runtime(runtime_root: Path) -> list[str]:
    """Remove development-only runtime content and return removed relative paths."""

    candidates: set[Path] = set()
    for pattern in PRUNE_LIBRARY_GLOBS:
        candidates.update(runtime_root.glob(pattern))
    candidates.update(runtime_root / relative for relative in PRUNE_RELATIVE_PATHS)

    bin_dir = runtime_root / "bin"
    if bin_dir.is_dir():
        candidates.update(
            entry for entry in bin_dir.iterdir() if entry.name not in RUNTIME_BIN_KEEP
        )

    site_packages = runtime_root / "lib" / f"python{PYTHON_SERIES}" / "site-packages"
    if site_packages.is_dir():
        for base, directories, _files in os.walk(site_packages, topdown=True):
            for name in list(directories):
                if name in {"test", "tests"}:
                    candidate = Path(base) / name
                    candidates.add(candidate)
                    directories.remove(name)

    candidates.update(runtime_root.rglob("__pycache__"))
    removed: list[str] = []
    for candidate in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        if candidate.exists() or candidate.is_symlink():
            removed.append(candidate.relative_to(runtime_root).as_posix())
            _remove(candidate)
    return sorted(removed)


def _allowed_app_path(path: PurePosixPath) -> bool:
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return False
    value = path.as_posix()
    if value in APP_SOURCE_FILES:
        return True
    return any(value.startswith(directory + "/") for directory in APP_SOURCE_DIRECTORIES)


def git_tracked_app_files(
    repo_root: Path,
    *,
    runner: RunCallable = subprocess.run,
) -> tuple[PurePosixPath, ...]:
    command = [
        "git",
        "ls-files",
        "-z",
        "--",
        *APP_SOURCE_DIRECTORIES,
        *APP_SOURCE_FILES,
    ]
    result = runner(command, cwd=repo_root, check=False, capture_output=True)
    if result.returncode != 0:
        stderr = os.fsdecode(result.stderr or b"").strip()
        raise BundleError(f"git ls-files failed: {stderr or f'exit {result.returncode}'}")
    stdout = result.stdout
    encoded = stdout if isinstance(stdout, bytes) else stdout.encode()
    paths = tuple(
        PurePosixPath(os.fsdecode(item)) for item in encoded.split(b"\0") if item
    )
    refused = [path.as_posix() for path in paths if not _allowed_app_path(path)]
    if refused:
        raise BundleError(f"git ls-files returned paths outside the app filter: {refused}")
    return tuple(sorted(paths, key=lambda item: item.as_posix()))


def copy_tracked_app_files(
    repo_root: Path,
    destination: Path,
    *,
    runner: RunCallable = subprocess.run,
) -> tuple[PurePosixPath, ...]:
    """Copy only Git-tracked app sources; ignored and untracked files cannot enter."""

    tracked = git_tracked_app_files(repo_root, runner=runner)
    for relative in tracked:
        source = repo_root.joinpath(*relative.parts)
        target = destination.joinpath(*relative.parts)
        if not source.is_file() and not source.is_symlink():
            raise BundleError(f"Tracked app source is missing or not a file: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)
    return tracked


def _validated_existing_spa(repo_root: Path, version: str) -> Path:
    dist = repo_root / "frontend" / "dist"
    index = dist / "index.html"
    stamp_path = dist / fetch_spa.STAMP_NAME
    if not index.is_file():
        raise BundleError(
            "frontend/dist is missing. Pass --spa with a release SPA tarball or "
            "install a stamped release SPA first."
        )
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(
            "frontend/dist is not checksum-stamped. Pass --spa with a release SPA "
            "tarball instead of packaging a local build."
        ) from exc
    digest = stamp.get("sha256") if isinstance(stamp, dict) else None
    if (
        not isinstance(stamp, dict)
        or stamp.get("version") != version
        or not isinstance(digest, str)
        or fetch_spa.DIGEST_RE.fullmatch(digest.lower()) is None
    ):
        raise BundleError(
            f"frontend/dist does not contain a verified release stamp for {version}. "
            "Pass --spa with the matching release SPA tarball."
        )
    return dist


def install_spa_layer(
    app_root: Path,
    *,
    version: str,
    archive: Path | None,
    repo_root: Path,
) -> None:
    if archive is None:
        source = _validated_existing_spa(repo_root, version)
        shutil.copytree(source, app_root / "frontend" / "dist")
        return

    archive = archive.resolve()
    if not archive.is_file():
        raise BundleError(f"No such SPA archive: {archive}")
    try:
        digest = fetch_spa._resolve_local_checksum(archive, None)
        fetch_spa.verify_archive(archive, digest)
        fetch_spa.install_archive(
            archive,
            version=version,
            digest=digest,
            source=f"release asset {archive.name}",
            root=app_root,
        )
    except fetch_spa.SpaError as exc:
        raise BundleError(str(exc)) from exc
    lock_path = app_root / "frontend" / fetch_spa.LOCK_NAME
    if lock_path.exists():
        lock_path.unlink()


def substitute_info_plist(template: Path, output: Path, version: str) -> None:
    with template.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["CFBundleShortVersionString"] = version
    payload["CFBundleVersion"] = version
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)


def launcher_stub() -> str:
    return """#!/bin/bash
set -u

# LaunchServices can start a script bundle through a translated shell. Re-exec
# natively so the bundled arm64 interpreter loads only arm64 extensions.
if [[ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" == "1" ]]; then
    exec arch -arm64 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="$(cd "$SCRIPT_DIR/../Resources" && pwd)"
export WG2_BUNDLE=1
export WG2_APP_ROOT="$RESOURCES/app"

# The bundle is code-signed and must stay byte-identical after it runs, so
# bytecode and numba kernel caches go to the user's cache directory instead of
# beside the sources they belong to.
CACHE_ROOT="$HOME/Library/Caches/WaveguideGenerator"
export PYTHONPYCACHEPREFIX="$CACHE_ROOT/pycache"
export NUMBA_CACHE_DIR="$CACHE_ROOT/numba"

cd "$WG2_APP_ROOT"
exec "../runtime/bin/python3.13" -m launchers.desktop "$@"
"""


def write_launcher_stub(path: Path) -> None:
    path.write_text(launcher_stub(), encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _iter_zip_entries(root: Path) -> Iterable[tuple[Path, str]]:
    discovered: list[tuple[Path, str]] = []

    def visit(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            if entry.name.startswith("._"):
                continue
            member = f"{prefix}{entry.name}"
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                discovered.append((path, member + "/"))
                visit(path, member + "/")
            else:
                discovered.append((path, member))

    visit(root, "")
    return sorted(discovered, key=lambda item: item[1])


def deterministic_zip(source: Path, output: Path) -> None:
    """Archive a layer in stable path order without unsafe link members."""

    output.parent.mkdir(parents=True, exist_ok=True)
    source_root = source.resolve()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path, member in _iter_zip_entries(source):
            if path.is_symlink():
                resolved = path.resolve()
                if not resolved.is_relative_to(source_root) or not resolved.is_file():
                    raise BundleError(
                        f"Layer symlink does not resolve to a file inside the layer: {path}"
                    )
                metadata = resolved.stat()
            else:
                metadata = path.lstat()
            info = zipfile.ZipInfo(member, ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (metadata.st_mode & 0xFFFF) << 16
            if member.endswith("/"):
                info.external_attr |= 0x10
                archive.writestr(info, b"")
            else:
                with path.open("rb") as source_handle, archive.open(info, "w") as target:
                    shutil.copyfileobj(source_handle, target, length=1 << 20)


def write_checksum(asset: Path) -> Path:
    sidecar = asset.with_name(asset.name + ".sha256")
    sidecar.write_text(f"{file_sha256(asset)}  {asset.name}\n", encoding="ascii")
    return sidecar


def declared_version(repo_root: Path) -> str:
    try:
        value = json.loads(
            (repo_root / "shared" / "version.json").read_text(encoding="utf-8")
        )["version"]
    except (OSError, ValueError, KeyError) as exc:
        raise BundleError(f"Could not read shared/version.json: {exc}") from exc
    if not isinstance(value, str) or not value:
        raise BundleError("shared/version.json has no non-empty string version")
    return value


class BundleBuilder:
    def __init__(
        self,
        repo_root: Path,
        *,
        runner: RunCallable = subprocess.run,
        process_factory: ProcessFactory = subprocess.Popen,
        machine: Callable[[], str] = platform.machine,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runner = runner
        self.process_factory = process_factory
        self.machine = machine
        self.command_environment = dict(os.environ)
        self.command_environment["COPYFILE_DISABLE"] = "1"
        # uv-managed python-build-standalone installations carry PEP 668's
        # EXTERNALLY-MANAGED marker. This build intentionally owns its isolated
        # temporary interpreter, so permit the required direct layer install.
        self.command_environment["UV_BREAK_SYSTEM_PACKAGES"] = "1"

    def run_command(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[Any]:
        print("+ " + shlex.join(command), flush=True)
        result = self.runner(
            list(command),
            cwd=cwd or self.repo_root,
            env=self.command_environment,
            check=False,
            capture_output=capture,
            text=capture,
        )
        if result.returncode != 0:
            detail = ""
            if capture:
                detail = str(result.stderr or result.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise BundleError(
                f"Command failed with exit {result.returncode}: {shlex.join(command)}{suffix}"
            )
        return result

    def git_commit(self) -> str:
        result = self.run_command(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture=True
        )
        commit = str(result.stdout).strip()
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise BundleError(f"git rev-parse returned an invalid commit: {commit!r}")
        return commit

    def build_runtime(
        self,
        destination: Path,
        *,
        python_version: str,
        runtime_id: str,
    ) -> None:
        install_parent = destination.parent / "python-install"
        install_parent.mkdir()
        install_command = [
            "uv",
            "python",
            "install",
            "--install-dir",
            str(install_parent),
            python_version,
        ]
        self.run_command(install_command)
        candidates = {
            candidate.resolve()
            for candidate in install_parent.glob("*/bin/python3.13")
            if candidate.is_file()
        }
        if len(candidates) != 1:
            raise BundleError(
                "uv did not install exactly one relocatable python3.13 tree under "
                f"{install_parent}: {sorted(map(str, candidates))}"
            )
        installed_root = next(iter(candidates)).parents[1]
        if not installed_root.is_relative_to(install_parent.resolve()):
            raise BundleError(f"uv resolved the installed Python outside {install_parent}")
        shutil.move(str(installed_root), destination)
        runtime_python = destination / "bin" / "python3.13"
        self.run_command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(runtime_python),
                "--no-cache",
                "-r",
                str(self.repo_root / "server" / "requirements-runtime.txt"),
                "-r",
                str(self.repo_root / "server" / "requirements-pins.txt"),
            ]
        )
        helper = destination / METAL_HELPER
        if self.machine() == "arm64" and not helper.is_file():
            raise BundleError(
                "The Apple Silicon Metal native helper is missing after installation: "
                f"{helper}. Ensure Swift is available and rebuild the runtime."
            )
        removed = prune_runtime(destination)
        print(f"Pruned {len(removed)} runtime directories.")
        write_runtime_manifest(
            destination,
            python_version=python_version,
            runtime_id=runtime_id,
            requirements=(self.repo_root / "server" / "requirements-runtime.txt").read_bytes(),
            pins=(self.repo_root / "server" / "requirements-pins.txt").read_bytes(),
        )

    def build_app(
        self,
        destination: Path,
        *,
        version: str,
        runtime_id: str,
        spa: Path | None,
    ) -> dict[str, object]:
        destination.mkdir()
        tracked = copy_tracked_app_files(
            self.repo_root, destination, runner=self.runner
        )
        print(f"Copied {len(tracked)} tracked app files.")
        install_spa_layer(
            destination,
            version=version,
            archive=spa,
            repo_root=self.repo_root,
        )
        return write_app_manifest(
            destination,
            version=version,
            commit=self.git_commit(),
            runtime_id=runtime_id,
        )

    def assemble_bundle(
        self,
        destination: Path,
        *,
        runtime_root: Path,
        app_root: Path,
        version: str,
    ) -> None:
        template = self.repo_root / APP_TEMPLATE
        if not template.is_dir():
            raise BundleError(f"macOS app template is missing: {template}")
        shutil.copytree(template, destination, symlinks=True)
        contents = destination / "Contents"
        substitute_info_plist(
            template / "Contents" / "Info.plist",
            contents / "Info.plist",
            version,
        )
        resources = contents / "Resources"
        shutil.copytree(runtime_root, resources / "runtime", symlinks=True)
        shutil.copytree(app_root, resources / "app", symlinks=True)
        write_launcher_stub(contents / "MacOS" / "Waveguide Generator")
        self.run_command(
            ["codesign", "--force", "--deep", "--sign", "-", str(destination)]
        )

    def create_dmg(self, bundle: Path, output: Path, staging: Path) -> None:
        staging.mkdir()
        shutil.copytree(bundle, staging / bundle.name, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        self.run_command(
            [
                "hdiutil",
                "create",
                "-volname",
                "Waveguide Generator",
                "-srcfolder",
                str(staging),
                "-format",
                "UDZO",
                str(output),
            ]
        )

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _http_status(url: str) -> int | None:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:  # noqa: S310
                return int(response.status)
        except (OSError, urllib.error.URLError):
            return None

    def verify_bundle(self, bundle: Path, scratch: Path) -> None:
        copied_bundle = scratch / bundle.name
        shutil.copytree(bundle, copied_bundle, symlinks=True)
        resources = copied_bundle / "Contents" / "Resources"
        app = resources / "app"
        python = resources / "runtime" / "bin" / "python3.13"
        environment = dict(self.command_environment)
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment["WG2_BUNDLE"] = "1"
        environment["WG2_APP_ROOT"] = str(app)
        # The same redirection the launcher stub performs, so that the seal
        # check below proves the bundle can run without modifying itself.
        for name in CACHE_ENVIRONMENT:
            environment[name] = str(scratch / "caches" / name.lower())

        print("+ " + shlex.join([str(python), "scripts/check_backends.py"]), flush=True)
        backends = self.runner(
            [str(python), "scripts/check_backends.py"],
            cwd=app,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        output = (str(backends.stdout or "") + str(backends.stderr or "")).strip()
        print(output)
        if backends.returncode != 0:
            raise BundleError("Bundled backend verification failed")
        if self.machine() == "arm64" and "Metal (Apple Silicon): ready" not in output:
            raise BundleError("Bundled backend verification did not report Metal ready")

        port = self._free_port()
        data_dir = scratch / "data"
        log_path = scratch / "server-verification.log"
        command = [
            str(python),
            "launch/serve.py",
            "--no-browser",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
        ]
        print("+ " + shlex.join(command), flush=True)
        with log_path.open("w+", encoding="utf-8") as log_handle:
            process = self.process_factory(
                command,
                cwd=app,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            root_status: int | None = None
            health_status: int | None = None
            deadline = time.monotonic() + 120.0
            try:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    root_status = self._http_status(f"http://127.0.0.1:{port}/")
                    health_status = self._http_status(
                        f"http://127.0.0.1:{port}/health"
                    )
                    if root_status == 200 and health_status == 200:
                        break
                    time.sleep(0.25)
                if root_status != 200 or health_status != 200:
                    log_handle.flush()
                    log_handle.seek(0)
                    detail = log_handle.read()[-8000:]
                    raise BundleError(
                        "Bundled server verification failed: "
                        f"/={root_status}, /health={health_status}\n{detail}"
                    )
                print("Server verification: / -> 200; /health -> 200")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)

        # Running the bundle must not have written into it: a Gatekeeper
        # assessment of a modified bundle fails on every launch after the first.
        self.run_command(
            ["codesign", "--verify", "--deep", "--strict", str(copied_bundle)],
            capture=True,
        )
        print("Seal verification: the bundle is unchanged after running.")


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _register_asset(assets: list[Path], asset: Path) -> None:
    assets.append(asset)
    assets.append(write_checksum(asset))


def print_asset_sizes(assets: Iterable[Path]) -> None:
    print("Assets:")
    for asset in sorted(assets, key=lambda item: item.name):
        size = asset.stat().st_size
        print(f"  {asset.name}: {size} bytes ({_format_size(size)})")


def build(args: argparse.Namespace, *, builder: BundleBuilder | None = None) -> list[Path]:
    if args.platform != "macos":
        raise BundleError("Choose --platform macos on a supported macOS host")
    if sys.platform != "darwin":
        raise BundleError("The macOS bundle must be built on macOS")

    builder = builder or BundleBuilder(REPO_ROOT)
    if builder.machine() != "arm64":
        raise BundleError("This step builds only the macos-arm64 runtime and app bundle")
    if not args.python_version.startswith(PYTHON_SERIES + "."):
        raise BundleError(
            f"The macOS layout requires a {PYTHON_SERIES}.x Python version, "
            f"not {args.python_version!r}"
        )

    version = declared_version(builder.repo_root)
    requirements_path = builder.repo_root / "server" / "requirements-runtime.txt"
    pins_path = builder.repo_root / "server" / "requirements-pins.txt"
    runtime_id = runtime_id_from_files(
        requirements_path,
        pins_path,
        args.python_version,
    )
    output = args.output
    if not output.is_absolute():
        output = builder.repo_root / output
    output.mkdir(parents=True, exist_ok=True)
    assets: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="wg-bundle-") as raw_scratch:
        scratch = Path(raw_scratch)
        runtime_root = scratch / "runtime"
        app_root = scratch / "app"

        if not args.app_only:
            builder.build_runtime(
                runtime_root,
                python_version=args.python_version,
                runtime_id=runtime_id,
            )
            runtime_asset = output / (
                f"waveguide-generator-runtime-macos-arm64-{runtime_id}.zip"
            )
            deterministic_zip(runtime_root, runtime_asset)
            _register_asset(assets, runtime_asset)

        if not args.runtime_only:
            builder.build_app(
                app_root,
                version=version,
                runtime_id=runtime_id,
                spa=args.spa,
            )
            app_asset = output / f"waveguide-generator-app-{version}.zip"
            deterministic_zip(app_root, app_asset)
            _register_asset(assets, app_asset)
            manifest_asset = output / (
                f"waveguide-generator-app-{version}.manifest.json"
            )
            shutil.copy2(app_root / "APP-MANIFEST.json", manifest_asset)
            _register_asset(assets, manifest_asset)

        if not args.runtime_only and not args.app_only:
            bundle = scratch / "Waveguide Generator.app"
            builder.assemble_bundle(
                bundle,
                runtime_root=runtime_root,
                app_root=app_root,
                version=version,
            )
            dmg_asset = output / f"Waveguide Generator-{version}-macos-arm64.dmg"
            if dmg_asset.exists():
                dmg_asset.unlink()
            builder.create_dmg(bundle, dmg_asset, scratch / "dmg-staging")
            _register_asset(assets, dmg_asset)
            if args.skip_verify:
                print("Verification skipped by --skip-verify.")
            else:
                try:
                    builder.verify_bundle(bundle, scratch / "verification")
                except (BundleError, OSError):
                    print_asset_sizes(assets)
                    raise
                print("Bundle verification passed.")
        elif not args.skip_verify:
            print("Bundle verification is not applicable to a layer-only build.")

    print_asset_sizes(assets)
    return assets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=("macos",),
        default="macos" if sys.platform == "darwin" else None,
        help="bundle platform (defaults to macos on Darwin)",
    )
    parser.add_argument("--output", type=Path, default=Path("build/bundle"))
    parser.add_argument("--spa", type=Path, help="verified release SPA tarball to install")
    layers = parser.add_mutually_exclusive_group()
    layers.add_argument("--runtime-only", action="store_true")
    layers.add_argument("--app-only", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--python-version", default=PYTHON_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.platform is None:
        parser.error("--platform is required outside macOS")
    if args.runtime_only and args.spa is not None:
        parser.error("--spa cannot be used with --runtime-only")
    try:
        build(args)
    except (BundleError, OSError, plistlib.InvalidFileException) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
