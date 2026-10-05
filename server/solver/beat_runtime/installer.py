"""Install WG-owned portable Julia; callers serialize with provision.lock."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import tarfile
import time
import urllib.request
import zipfile

from . import discovery, paths
from .paths import PROVIDER_ID, runtime_dir

JULIA_VERSION = "1.12.7"
CPU_REQUIRED_FREE_BYTES = 2 * 1024**3
GPU_REQUIRED_FREE_BYTES = 6 * 1024**3
_BASE = "https://julialang-s3.julialang.org/bin"
_WINDOWS = os.name == "nt"
WINDOWS_LONGEST_MEMBER = 155
MAX_EXTRACTED_BYTES = 4 * 1024**3
MAX_ARCHIVE_MEMBERS = 100_000
_STAGING_MARKER = ".wg-staging.json"
# https://julialang-s3.julialang.org/bin/checksums/julia-1.12.7.sha256
# Pin every platform so verification needs no
# second network request. Windows retains HBB's pinned artifact checksum.
_RELEASES = {
    ("Darwin", "arm64"): ("mac/aarch64", "macaarch64", "af8fcedfb25b6b9c8c13d99695c38faa2d59bf0162b3f458c8b8b56f14c96919"),
    ("Darwin", "x86_64"): ("mac/x64", "mac64", "a21a15c7b7d294a03482a3598b18cde48d37be86ac7a408e495e51fb3afe0157"),
    ("Windows", "x86_64"): ("winnt/x64", "win64", "ff5c7eb354c2fcb48401114a5fbcfe8e60181f95d9af42b266f975265a5bad47"),
    ("Linux", "x86_64"): ("linux/x64", "linux-x86_64", "4e7e9e776634d24835250de67cde39b0d4af15bc432eb20697e6be6c28ea69e8"),
    ("Linux", "aarch64"): ("linux/aarch64", "linux-aarch64", "9243c0b524c7f300883240a1ee5ea3916a30e070bff718acf8ccaee31a731ef2"),
}
Fetcher = Callable[[str, Path], None]
StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class JuliaDownload:
    platform: str
    filename: str
    url: str
    sha256: str
    windows: bool

    @property
    def directory(self) -> str:
        return JULIA_VERSION if self.windows else f"{JULIA_VERSION}-{self.platform}"


def julia_download(system: str | None = None, machine: str | None = None) -> JuliaDownload:
    system, machine = system or platform.system(), machine or platform.machine()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine.lower(), machine.lower())
    if system == "Darwin" and machine == "aarch64":
        machine = "arm64"
    try:
        directory, suffix, checksum = _RELEASES[(system, machine)]
    except KeyError as exc:
        raise RuntimeError(f"No portable Julia for {system}/{machine}; set {discovery.JULIA_ENV_VAR}") from exc
    filename = f"julia-{JULIA_VERSION}-{suffix}.{'zip' if system == 'Windows' else 'tar.gz'}"
    return JuliaDownload(f"{system.lower()}-{machine}", filename, f"{_BASE}/{directory}/1.12/{filename}", checksum, system == "Windows")


def _report(callback: StatusCallback | None, message: str) -> None:
    if callback is not None:
        try:
            callback(message)
        except Exception:
            pass  # Progress reporting must never fail an installation.


def _fetch(url: str, destination: Path, status_cb: StatusCallback | None = None) -> None:
    with urllib.request.urlopen(url, timeout=30) as response, destination.open("wb") as stream:
        total = int(response.headers.get("Content-Length") or 0)
        read, last_report = 0, time.monotonic()
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
            read += len(chunk)
            now = time.monotonic()
            if now - last_report >= 5:
                last_report = now
                progress = f"{read / 1e6:.0f} / {total / 1e6:.0f}" if total else f"{read / 1e6:.0f}"
                _report(status_cb, f"Downloading {destination.name}: {progress} MB")


def download_archive(
    spec: JuliaDownload, destination: Path, *, fetcher: Fetcher | None = None,
    status_cb: StatusCallback | None = None, root: Path | None = None,
) -> None:
    """Fetch into .part, verify SHA-256, then atomically publish the archive."""
    if not re.fullmatch(r"[0-9a-f]{64}", spec.sha256):
        raise ValueError("A pinned SHA-256 checksum is required")
    paths.checked_root(destination.parent)
    _private_directory(destination.parent, root=root)
    partial = destination.with_name(destination.name + ".part")
    if paths.is_link(destination) or paths.is_link(partial):
        raise RuntimeError("Linked download destination refused")
    _report(status_cb, f"Downloading portable Julia {JULIA_VERSION}: {spec.filename}")
    try:
        if fetcher is None:
            _fetch(spec.url, partial, status_cb)
        else:
            fetcher(spec.url, partial)
        actual = discovery.executable_identity(partial)
        if actual != spec.sha256:
            raise RuntimeError(f"SHA-256 mismatch for {spec.url}: expected {spec.sha256}, got {actual}")
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def check_disk_space(root: Path, required_bytes: int = CPU_REQUIRED_FREE_BYTES) -> None:
    if required_bytes < 0:
        raise ValueError("Disk budget cannot be negative")
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    if free < required_bytes:
        raise RuntimeError(f"Not enough free disk space: {free / 1024**3:.1f} GiB free, {required_bytes / 1024**3:.0f} GiB needed")


def _private_directory(path: Path, *, root: Path | None = None) -> None:
    paths.checked_root(path)
    root = paths.checked_root(root) if root is not None else path
    path, root = path.absolute(), root.absolute()
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError(f"Runtime directory escapes provider root: {path}")
    # Ancestor aliases (/tmp, /var, /home, stowed .local) are legitimate.
    # Only the provider root and components below it must be unlinked.
    if any(paths.is_link(parent) for parent in (path, *path.parents)
           if parent == root or root in parent.parents):
        raise RuntimeError(f"Linked runtime directory refused: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _member_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized:
        raise RuntimeError(f"Unsafe archive member: {name}")
    return normalized


def _archive_budget(sizes: list[int]) -> None:
    if len(sizes) > MAX_ARCHIVE_MEMBERS or sum(sizes) > MAX_EXTRACTED_BYTES:
        raise RuntimeError("Julia archive exceeds extraction size/member limit")


def _unpack(archive: Path, staging: Path) -> None:
    paths.checked_root(staging)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            _archive_budget([member.file_size for member in members])
            for member in members:
                member.filename = _member_path(member.filename)
                if _STAGING_MARKER in PurePosixPath(member.filename).parts:
                    raise RuntimeError("Archive contains reserved staging marker")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RuntimeError(f"Linked ZIP member refused: {member.filename}")
            bundle.extractall(staging, members=members)
            for member in members:
                mode = (member.external_attr >> 16) & 0o777
                if mode and not member.is_dir():
                    (staging / member.filename).chmod(mode)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            _archive_budget([member.size for member in members])
            for member in members:
                _member_path(member.name)
                if _STAGING_MARKER in PurePosixPath(member.name).parts:
                    raise RuntimeError("Archive contains reserved staging marker")
                tarfile.data_filter(member, str(staging))
                if member.issym() or member.islnk():
                    member_path = staging / member.name
                    link = (member_path.parent if member.issym() else staging) / member.linkname
                    top = staging / PurePosixPath(member.name).parts[0]
                    if not link.resolve().is_relative_to(top.resolve()):
                        raise RuntimeError(f"Archive link escapes published Julia tree: {member.name}")
            bundle.extractall(staging, filter="data")


def _executable(tree: Path, windows: bool) -> Path:
    # Official macOS tarballs can carry an application bundle.
    direct = tree / "bin" / ("julia.exe" if windows else "julia")
    return direct if windows or direct.is_file() else tree / "Contents/Resources/julia/bin/julia"


def _owned(tree: Path, spec: JuliaDownload) -> bool:
    if paths.is_link(tree) or paths.is_link(tree / ".wg-julia.json"):
        return False
    try:
        marker = json.loads((tree / ".wg-julia.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return False
    return marker == {"provider": PROVIDER_ID, "version": JULIA_VERSION, "platform": spec.platform}


def _valid_tree(tree: Path, spec: JuliaDownload) -> bool:
    executable = _executable(tree, spec.windows)
    return executable.is_file() and (spec.windows or os.access(executable, os.X_OK)) and executable.resolve().is_relative_to(tree.resolve())


def _layout(root: Path, spec: JuliaDownload) -> tuple[Path, Path, Path]:
    staging = root / "dl/x" if spec.windows else root / "downloads" / f"unpack-{spec.directory}"
    target = root / "julia" / spec.directory
    return staging, target, target.with_name(f".{spec.directory}.previous")


def _windows_long_paths_enabled() -> bool:
    if not _WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def windows_path_length(root: Path, spec: JuliaDownload) -> int:
    """Conservative full member length, including staging and recovery names."""
    return max(len(str(path)) + 1 + WINDOWS_LONGEST_MEMBER for path in _layout(root, spec))


def _check_windows_paths(root: Path, spec: JuliaDownload) -> None:
    if spec.windows and windows_path_length(root, spec) >= 260 and not _windows_long_paths_enabled():
        raise RuntimeError("Julia installation would exceed Windows MAX_PATH (260 characters); "
                           "choose a shorter WG2_BEAT_RUNTIME_DIR or enable Windows long paths")


def _remove_backup(backup: Path, spec: JuliaDownload) -> None:
    # An open DLL on Windows can survive cleanup; retry on the next run.
    paths.checked_root(backup)
    if backup.exists():
        if not _owned(backup, spec):
            raise RuntimeError(f"Unowned recovery tree refused: {backup}")
        _remove_marked_tree(backup, ".wg-julia.json")


def _remove_marked_tree(tree: Path, marker_name: str) -> None:
    """Keep ownership evidence when open files prevent complete cleanup."""
    paths.checked_root(tree)
    marker = tree / marker_name
    with suppress(OSError):
        evidence = marker.read_bytes()
        for child in tree.iterdir():
            if child == marker:
                continue
            with suppress(OSError):
                if paths.is_link(child):
                    child.unlink() if child.is_symlink() else child.rmdir()
                elif child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        if any(child != marker for child in tree.iterdir()):
            return
        marker.unlink()
        try:
            tree.rmdir()
        except OSError:
            # A directory reader can deny removal even after its contents close.
            if tree.exists():
                marker.write_bytes(evidence)


def _recover(root: Path, spec: JuliaDownload) -> None:
    paths.checked_root(root)
    staging, target, backup = _layout(root, spec)
    # Validate root/descendants without creating anything (disk checks stay early).
    for path in (root, staging.parent, target.parent, staging, target, backup):
        if paths.is_link(path):
            raise RuntimeError(f"Linked installation or staging directory refused: {path}")
    if target.exists() and not _owned(target, spec):
        raise RuntimeError(f"Refusing to replace an unowned Julia installation: {target}")
    if not backup.exists():
        return
    if not _owned(backup, spec):
        raise RuntimeError(f"Unowned recovery tree refused: {backup}")
    if target.exists() and _valid_tree(target, spec):
        _remove_backup(backup, spec)
    elif _valid_tree(backup, spec):
        if target.exists():
            _remove_marked_tree(target, ".wg-julia.json")
            if target.exists():
                raise RuntimeError(f"Incomplete Julia installation is still in use: {target}")
        os.replace(backup, target)
    else:
        _remove_backup(backup, spec)


def _staging_owned(staging: Path) -> bool:
    marker = staging / _STAGING_MARKER
    if paths.is_link(staging) or paths.is_link(marker):
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")) == {"provider": PROVIDER_ID}
    except (OSError, ValueError, RecursionError):
        return False


def _validate_links(tree: Path) -> None:
    # Tar's data filter contains links within staging, but publication removes
    # the top-level directory. Every link must stay inside that promoted tree.
    resolved = tree.resolve()
    for path in tree.rglob("*"):
        if paths.is_link(path) and not path.resolve().is_relative_to(resolved):
            raise RuntimeError(f"Archive link escapes published Julia tree: {path}")


def extract_julia(
    archive: Path, root: Path, spec: JuliaDownload, *, status_cb: StatusCallback | None = None,
) -> Path:
    """Validate staging before publication; recover only this WG-owned target."""
    root = paths.checked_root(root)
    if _WINDOWS:
        _check_windows_paths(root.absolute(), spec)
    _recover(root, spec)
    staging, target, backup = _layout(root, spec)
    _private_directory(staging.parent, root=root)
    _private_directory(target.parent, root=root)
    if backup.exists():
        # Cleanup was denied, but publication already succeeded on an earlier run.
        if _owned(target, spec) and _valid_tree(target, spec):
            return _executable(target, spec.windows)
        raise RuntimeError(f"Recovery tree is still in use: {backup}")
    if staging.exists():
        if not _staging_owned(staging):
            raise RuntimeError(f"Unowned staging tree refused: {staging}")
        _remove_marked_tree(staging, _STAGING_MARKER)
        if staging.exists():
            raise RuntimeError(f"Julia staging tree is still in use: {staging}")
    staging.mkdir(mode=0o700)
    # A killed process before this marker is written leaves a refused tree.
    (staging / _STAGING_MARKER).write_text(json.dumps({"provider": PROVIDER_ID}), encoding="utf-8")
    _report(status_cb, f"Unpacking {archive.name}")
    try:
        _unpack(archive, staging)
        entries = [entry for entry in staging.iterdir() if entry.name != _STAGING_MARKER]
        if len(entries) != 1 or not entries[0].is_dir() or paths.is_link(entries[0]):
            raise RuntimeError(f"Unexpected archive layout in {archive.name}")
        tree = entries[0]
        _validate_links(tree)
        if not _valid_tree(tree, spec):
            raise RuntimeError(f"Unpacked Julia has no executable inside {tree}")
        if paths.is_link(tree / ".wg-julia.json"):
            raise RuntimeError("Linked installation marker refused")
        (tree / ".wg-julia.json").write_text(json.dumps({
            "provider": PROVIDER_ID, "version": JULIA_VERSION, "platform": spec.platform,
        }), encoding="utf-8")
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(tree, target)
        except OSError:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        _remove_backup(backup, spec)
        return _executable(target, spec.windows)
    finally:
        if _staging_owned(staging):
            _remove_marked_tree(staging, _STAGING_MARKER)


def ensure_julia(
    root: Path | None = None, *, explicit: str | None = None, configured: str | None = None,
    environ: Mapping[str, str] | None = None, system: str | None = None,
    machine: str | None = None, required_bytes: int = CPU_REQUIRED_FREE_BYTES,
    fetcher: Fetcher | None = None, status_cb: StatusCallback | None = None,
) -> str:
    """Reuse a selected executable or install Julia; never record readiness."""
    env = os.environ if environ is None else environ
    root = runtime_dir(environ=env) if root is None else paths.checked_root(root, environ=env)
    paths.checked_root(root)
    if paths.is_link(root):
        raise RuntimeError(f"Linked runtime directory refused: {root}")
    existing = discovery.discover_julia(explicit, configured=configured, root=root, environ=env)
    configured_path = configured if configured is not None else env.get(discovery.JULIA_ENV_VAR, "")
    selected = bool((explicit or "").strip() or configured_path.strip())
    record = discovery.read_julia_record(root)
    if existing and not selected:
        # Path ownership, rather than a stamped version, distinguishes portable
        # installs from external/custom Julia. Explicit older Julia still wins.
        resolved = Path(existing).resolve()
        relative = resolved.relative_to(root.resolve()) if resolved.is_relative_to(root.resolve()) else None
        managed = relative is not None and len(relative.parts) > 1 and relative.parts[0] == "julia"
        current = managed and (relative.parts[1] == JULIA_VERSION or relative.parts[1].startswith(f"{JULIA_VERSION}-"))
        if managed and not current:
            existing = shutil.which("julia", path=env.get("PATH", os.defpath))
            if existing and Path(existing).resolve().is_relative_to((root / "julia").resolve()):
                existing = None
    if existing and paths.hbb_executable(Path(existing), environ=env):
        existing = None
    if existing:
        resolved = Path(existing).resolve()
        if resolved.is_relative_to((root / "julia").resolve()):
            spec = julia_download(system, machine)
            if resolved.is_relative_to((root / "julia" / spec.directory).resolve()):
                _recover(root, spec)
        _report(status_cb, f"Using existing Julia: {existing}")
        previous = record if record and record["executable"] == existing else None
        selection = previous.get("selection") if previous else "path"
        if (explicit or "").strip():
            selection = "explicit"
        elif configured_path.strip():
            selection = "configured"
        elif selection == "explicit":
            selection = "path"
        _private_directory(root)
        discovery.write_julia_record(
            root, Path(existing),
            origin=previous.get("origin", "external") if previous else "external",
            version=previous.get("version") if previous else None,
            selection=selection,
        )
        return existing
    spec = julia_download(system, machine)
    if _WINDOWS:
        _check_windows_paths(root.absolute(), spec)
    _recover(root, spec)
    target = root / "julia" / spec.directory
    if _owned(target, spec) and _valid_tree(target, spec):
        executable = _executable(target, spec.windows)
    else:
        check_disk_space(root, required_bytes)
        _private_directory(root)
        archive = root / ("dl" if spec.windows else "downloads") / spec.filename
        download_archive(spec, archive, fetcher=fetcher, status_cb=status_cb, root=root)
        executable = extract_julia(archive, root, spec, status_cb=status_cb)
        archive.unlink(missing_ok=True)
    discovery.write_julia_record(root, executable, origin="managed", version=JULIA_VERSION)
    return str(executable)
