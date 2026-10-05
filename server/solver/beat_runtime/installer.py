"""Install WG-owned portable Julia; callers serialize with provision.lock."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import tarfile
import urllib.request
import zipfile

from . import discovery
from .paths import PROVIDER_ID, runtime_dir

JULIA_VERSION = "1.12.7"
CPU_REQUIRED_FREE_BYTES = 2 * 1024**3
GPU_REQUIRED_FREE_BYTES = 6 * 1024**3
_BASE = "https://julialang-s3.julialang.org/bin"
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
        return f"{JULIA_VERSION}-{self.platform}"


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


def _fetch(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=30) as response, destination.open("wb") as stream:
        shutil.copyfileobj(response, stream, length=1024 * 1024)


def download_archive(
    spec: JuliaDownload, destination: Path, *, fetcher: Fetcher | None = None,
    status_cb: StatusCallback | None = None,
) -> None:
    """Fetch into .part, verify SHA-256, then atomically publish the archive."""
    if not re.fullmatch(r"[0-9a-f]{64}", spec.sha256):
        raise ValueError("A pinned SHA-256 checksum is required")
    _private_directory(destination.parent)
    partial = destination.with_name(destination.name + ".part")
    if destination.is_symlink() or partial.is_symlink():
        raise RuntimeError("Linked download destination refused")
    _report(status_cb, f"Downloading portable Julia {JULIA_VERSION}: {spec.filename}")
    try:
        (fetcher or _fetch)(spec.url, partial)
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


def _private_directory(path: Path) -> None:
    # Refuse links at every existing component before writing or removing trees.
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise RuntimeError(f"Linked runtime directory refused: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _member_path(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise RuntimeError(f"Unsafe archive member: {name}")


def _unpack(archive: Path, staging: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                _member_path(member.filename)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RuntimeError(f"Linked ZIP member refused: {member.filename}")
            bundle.extractall(staging)
            for member in bundle.infolist():
                mode = (member.external_attr >> 16) & 0o777
                if mode and not member.is_dir():
                    (staging / member.filename).chmod(mode)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                _member_path(member.name)
            bundle.extractall(staging, filter="data")


def _executable(tree: Path, windows: bool) -> Path:
    # Official macOS tarballs can carry an application bundle.
    direct = tree / "bin" / ("julia.exe" if windows else "julia")
    return direct if windows or direct.is_file() else tree / "Contents/Resources/julia/bin/julia"


def _owned(tree: Path, spec: JuliaDownload) -> bool:
    try:
        marker = json.loads((tree / ".wg-julia.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return marker == {"provider": PROVIDER_ID, "version": JULIA_VERSION, "platform": spec.platform}


def _valid_tree(tree: Path, spec: JuliaDownload) -> bool:
    executable = _executable(tree, spec.windows)
    return executable.is_file() and (spec.windows or os.access(executable, os.X_OK)) and executable.resolve().is_relative_to(tree.resolve())


def extract_julia(
    archive: Path, root: Path, spec: JuliaDownload, *, status_cb: StatusCallback | None = None,
) -> Path:
    """Validate staging before publication; recover only this WG-owned target."""
    downloads, installs = root / "downloads", root / "julia"
    _private_directory(downloads)
    _private_directory(installs)
    staging = downloads / f"unpack-{spec.directory}"
    target = installs / spec.directory
    backup = installs / f".{spec.directory}.previous"
    if any(path.is_symlink() for path in (staging, target, backup)):
        raise RuntimeError("Linked installation or staging directory refused")
    if backup.exists():
        if not _owned(backup, spec):
            raise RuntimeError(f"Unowned recovery tree refused: {backup}")
        if not target.exists():
            os.replace(backup, target)
        elif _owned(target, spec) and _valid_tree(target, spec):
            shutil.rmtree(backup)
        else:
            raise RuntimeError(f"Ambiguous interrupted installation: {target}")
    if target.exists() and not _owned(target, spec):
        raise RuntimeError(f"Refusing to replace an unowned Julia installation: {target}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    _report(status_cb, f"Unpacking {archive.name}")
    try:
        _unpack(archive, staging)
        entries = list(staging.iterdir())
        if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
            raise RuntimeError(f"Unexpected archive layout in {archive.name}")
        tree = entries[0]
        if not _valid_tree(tree, spec):
            raise RuntimeError(f"Unpacked Julia has no executable inside {tree}")
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
        if backup.exists():
            shutil.rmtree(backup)
        return _executable(target, spec.windows)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def ensure_julia(
    root: Path | None = None, *, explicit: str | None = None, configured: str | None = None,
    environ: Mapping[str, str] | None = None, system: str | None = None,
    machine: str | None = None, required_bytes: int = CPU_REQUIRED_FREE_BYTES,
    fetcher: Fetcher | None = None, status_cb: StatusCallback | None = None,
) -> str:
    """Reuse a selected executable or install Julia; never record readiness."""
    env = os.environ if environ is None else environ
    root = runtime_dir(environ=env) if root is None else root
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
        current = managed and relative.parts[1].startswith(f"{JULIA_VERSION}-")
        legacy = record is not None and record.get("origin") == "legacy"
        if legacy or (managed and not current):
            existing = shutil.which("julia", path=env.get("PATH", os.defpath))
            if existing and (Path(existing).resolve().is_relative_to((root / "julia").resolve()) or (legacy and Path(existing).resolve() == resolved)):
                existing = None
    if existing:
        _report(status_cb, f"Using existing Julia: {existing}")
        previous = record if discovery.recorded_julia(root) == existing else None
        _private_directory(root)
        discovery.write_julia_record(
            root, Path(existing),
            origin=previous.get("origin", "external") if previous else "external",
            version=previous.get("julia_version") if previous else None,
        )
        return existing
    spec = julia_download(system, machine)
    target = root / "julia" / spec.directory
    if not target.is_symlink() and _owned(target, spec) and _valid_tree(target, spec):
        executable = _executable(target, spec.windows)
    else:
        check_disk_space(root, required_bytes)
        _private_directory(root)
        archive = root / "downloads" / spec.filename
        download_archive(spec, archive, fetcher=fetcher, status_cb=status_cb)
        executable = extract_julia(archive, root, spec, status_cb=status_cb)
        archive.unlink(missing_ok=True)
    discovery.write_julia_record(root, executable, origin="managed", version=JULIA_VERSION)
    return str(executable)
