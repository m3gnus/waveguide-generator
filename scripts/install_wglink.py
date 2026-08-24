#!/usr/bin/env python3
"""Install WGLink from its exact pinned source using WG's existing runtime."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile


_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from server.platform.paths import app_root  # noqa: E402


REPO_ROOT = app_root()
BUILDER_PATH = REPO_ROOT / "scripts" / "build_wglink_package.py"
RUNTIME_PARENT = REPO_ROOT / "integrations" / "wglink" / "runtime"
RUNTIME_FILE = "wglink_runtime.json"
INSTALL_MARKER = "wglink_install.json"
MANAGED_BY = "waveguide-generator"
MAX_MEMBERS = 512
MAX_EXPANDED_BYTES = 32 * 1024 * 1024


class InstallError(RuntimeError):
    """A package or target that must not be installed over."""


def _load_builder():
    spec = importlib.util.spec_from_file_location("wg_build_wglink", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise InstallError(f"Could not load {BUILDER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"{label} must be a JSON object: {path}")
    return value


def _platform_name(value: str = "auto") -> str:
    if value != "auto":
        return value
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return "linux"


def default_addins_dir(
    platform: str,
    *,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Path | None:
    home = Path.home() if home is None else home
    environ = dict(os.environ) if environ is None else environ
    if platform == "macos":
        base = home / "Library" / "Application Support" / "Autodesk"
        legacy = base / "Autodesk Fusion 360" / "API" / "AddIns"
        current = base / "Autodesk Fusion" / "API" / "AddIns"
        return legacy if legacy.exists() else current
    if platform == "windows":
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        base = base / "Autodesk"
        legacy = base / "Autodesk Fusion 360" / "API" / "AddIns"
        current = base / "Autodesk Fusion" / "API" / "AddIns"
        return legacy if legacy.exists() else current
    if platform == "linux":
        return None
    raise InstallError(f"Unsupported platform {platform!r}")


def _venv_python(root: Path) -> Path:
    windows = root / ".venv" / "Scripts" / "python.exe"
    posix = root / ".venv" / "bin" / "python"
    return windows if windows.is_file() else posix


def _safe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    pure = PurePosixPath(name)
    mode = (info.external_attr >> 16) & 0o170000
    return (
        not info.is_dir()
        and "\\" not in name
        and not pure.is_absolute()
        and pure.parts
        and pure.parts[0] == "wglink"
        and ".." not in pure.parts
        and mode != 0o120000
    )


def verify_package(
    archive_path: Path,
    *,
    root: Path = REPO_ROOT,
) -> tuple[dict[str, object], dict[str, bytes]]:
    builder = _load_builder()
    expected_source = builder.source_spec(root / "integrations" / "wglink" / "source.json")
    expected_version = builder.declared_version(root / "shared" / "version.json")
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallError(f"WGLink package is not a readable zip archive: {exc}") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if not infos or len(infos) > MAX_MEMBERS or len(names) != len(set(names)):
            raise InstallError("WGLink package has an invalid or duplicate member inventory")
        if any(not _safe_member(info) for info in infos):
            raise InstallError("REFUSING TO EXTRACT: WGLink package has an unsafe member")
        expanded = sum(info.file_size for info in infos)
        if expanded > MAX_EXPANDED_BYTES:
            raise InstallError("WGLink package exceeds its expanded-size limit")
        try:
            payloads = {info.filename: archive.read(info) for info in infos}
            provenance = json.loads(payloads["wglink/provenance.json"])
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise InstallError(f"WGLink package provenance is missing or invalid: {exc}") from exc
    if not isinstance(provenance, dict) or provenance.get("schema") != 1:
        raise InstallError("WGLink package provenance must use schema 1")
    expected_fields = {
        "sourceRepository": expected_source["repository"],
        "sourceCommit": expected_source["commit"],
        "sourceLicense": expected_source["license"],
        "addinVersion": expected_source["addinVersion"],
        "waveguideGeneratorVersion": expected_version,
    }
    for name, expected in expected_fields.items():
        if provenance.get(name) != expected:
            raise InstallError(
                f"WGLink package {name} is {provenance.get(name)!r}, expected {expected!r}"
            )
    files = provenance.get("files")
    if not isinstance(files, dict):
        raise InstallError("WGLink package provenance has no files table")
    actual_names = set(payloads).difference({"wglink/provenance.json"})
    if actual_names != set(files):
        raise InstallError("WGLink package inventory does not match its provenance")
    for name, expected in files.items():
        if not isinstance(expected, str):
            raise InstallError(f"WGLink package has no digest for {name}")
        actual = hashlib.sha256(payloads[name]).hexdigest()
        if actual != expected:
            raise InstallError(f"REFUSING TO EXTRACT: WGLink package hash mismatch for {name}")
    return provenance, payloads


def _cache_path(root: Path, version: str, commit: str) -> Path:
    return (
        root
        / "integrations"
        / "wglink"
        / "runtime"
        / "packages"
        / f"wglink-{version}-{commit}.zip"
    )


def _fetch_package(root: Path) -> Path:
    builder = _load_builder()
    spec = builder.source_spec(root / "integrations" / "wglink" / "source.json")
    version = builder.declared_version(root / "shared" / "version.json")
    commit = str(spec["commit"])
    cached = _cache_path(root, version, commit)
    if cached.is_file():
        try:
            verify_package(cached, root=root)
            return cached
        except InstallError:
            cached.unlink(missing_ok=True)

    cached.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wg2-wglink-source-") as temporary:
        source = Path(temporary) / "hornlab-fusion-addin"
        commands = (
            ["git", "init", "--quiet", str(source)],
            ["git", "-C", str(source), "remote", "add", "origin", str(spec["repository"])],
            ["git", "-C", str(source), "fetch", "--quiet", "--depth", "1", "origin", commit],
            ["git", "-C", str(source), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        )
        for command in commands:
            completed = subprocess.run(command, check=False)
            if completed.returncode != 0:
                raise InstallError(
                    "Could not fetch the pinned WGLink source commit; check the network "
                    "connection and re-run the installer."
                )
        temporary_archive = Path(temporary) / cached.name
        builder.build_package(source, temporary_archive, spec=spec, version=version)
        temporary_archive.replace(cached)
    verify_package(cached, root=root)
    return cached


def _runtime_id(provenance: dict[str, object]) -> str:
    version = str(provenance["waveguideGeneratorVersion"])
    commit = str(provenance["sourceCommit"])
    return f"wg-{version}-source-{commit}"


def _runtime_matches(
    root: Path,
    provenance: dict[str, object],
    payloads: dict[str, bytes],
) -> bool:
    try:
        installed = _read_json(root / "provenance.json", "installed WGLink provenance")
        if installed != provenance:
            return False
        expected = set(payloads).difference({"wglink/provenance.json"})
        all_paths = list(root.rglob("*"))
        if any(path.is_symlink() for path in all_paths):
            return False
        actual = {
            relative
            for path in all_paths
            if path.is_file()
            and (relative := path.relative_to(root).as_posix()) != "provenance.json"
        }
        if actual != {name.removeprefix("wglink/") for name in expected}:
            return False
        return all(
            hashlib.sha256(
                (root / name.removeprefix("wglink/")).read_bytes()
            ).hexdigest()
            == hashlib.sha256(data).hexdigest()
            for name, data in payloads.items()
            if name != "wglink/provenance.json"
        )
    except (InstallError, OSError):
        return False


def _materialize_runtime(
    archive_path: Path,
    *,
    root: Path,
) -> tuple[Path, dict[str, object]]:
    provenance, payloads = verify_package(archive_path, root=root)
    parent = root / "integrations" / "wglink" / "runtime" / "payloads"
    destination = parent / _runtime_id(provenance)
    if _runtime_matches(destination / "wglink", provenance, payloads):
        return destination / "wglink", provenance
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    staging = Path(tempfile.mkdtemp(prefix=".wglink-payload-", dir=parent))
    try:
        for name, data in payloads.items():
            path = staging.joinpath(*PurePosixPath(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (staging / "wglink" / "provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        try:
            staging.rename(destination)
        except FileExistsError:
            shutil.rmtree(staging)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return destination / "wglink", provenance


def _marker(target: Path) -> dict[str, object] | None:
    if target.is_symlink() or not target.is_dir():
        return None
    try:
        value = json.loads((target / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def is_managed_target(target: Path, root: Path) -> bool:
    marker = _marker(target)
    if marker is None or marker.get("schema") != 1 or marker.get("managedBy") != MANAGED_BY:
        return False
    try:
        return Path(str(marker.get("waveguideGeneratorRoot"))).resolve() == root.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def install(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
    archive_path: Path | None = None,
    python: Path | None = None,
    replace_external: bool = False,
) -> tuple[str, Path | None]:
    root = root.resolve()
    platform = _platform_name(platform)
    addins_dir = addins_dir or default_addins_dir(platform)
    if addins_dir is None:
        return "unsupported", None
    target = addins_dir.expanduser().resolve() / "WGLink"
    if (target.exists() or target.is_symlink()) and not is_managed_target(target, root):
        if not replace_external:
            return "preserved-external", target
    python = (python or _venv_python(root)).resolve()
    if not python.is_file():
        raise InstallError(
            f"WGLink needs Waveguide Generator's prepared Python environment: {python}"
        )
    archive_path = archive_path.resolve() if archive_path else _fetch_package(root)
    runtime_root, provenance = _materialize_runtime(archive_path, root=root)
    source_addin = runtime_root / "fusion-addins" / "WGLink"
    resampler = runtime_root / "scripts" / "wglink_resample.py"
    if not (source_addin / "WGLink.py").is_file() or not resampler.is_file():
        raise InstallError("WGLink package is missing its add-in or resampler")

    addins_dir = target.parent
    addins_dir.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(tempfile.mkdtemp(prefix=".WGLink-install-", dir=addins_dir))
    staging = staging_parent / "WGLink"
    previous = staging_parent / "previous"
    try:
        shutil.copytree(source_addin, staging)
        (staging / RUNTIME_FILE).write_text(
            json.dumps(
                {"schema": 1, "root": str(runtime_root), "python": str(python)},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (staging / INSTALL_MARKER).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "managedBy": MANAGED_BY,
                    "waveguideGeneratorRoot": str(root),
                    "waveguideGeneratorVersion": provenance["waveguideGeneratorVersion"],
                    "sourceCommit": provenance["sourceCommit"],
                    "addinVersion": provenance["addinVersion"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if target.exists() or target.is_symlink():
            target.rename(previous)
        try:
            staging.rename(target)
        except Exception:
            if previous.exists() and not target.exists():
                previous.rename(target)
            raise
        if previous.is_symlink() or previous.is_file():
            previous.unlink()
        elif previous.exists():
            shutil.rmtree(previous)
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)
    return "installed", target


def uninstall(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
) -> tuple[str, Path | None]:
    root = root.resolve()
    addins_dir = addins_dir or default_addins_dir(_platform_name(platform))
    if addins_dir is None:
        return "unsupported", None
    target = addins_dir.expanduser().resolve() / "WGLink"
    if not is_managed_target(target, root):
        return "preserved-external", target if target.exists() or target.is_symlink() else None
    shutil.rmtree(target)
    runtime = root / "integrations" / "wglink" / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    return "removed", target


def managed_target(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
) -> Path | None:
    root = root.resolve()
    addins_dir = addins_dir or default_addins_dir(_platform_name(platform))
    if addins_dir is None:
        return None
    target = addins_dir.expanduser().resolve() / "WGLink"
    return target if is_managed_target(target, root) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="install a locally built package")
    parser.add_argument("--addins-dir", type=Path, help="override Fusion's AddIns directory")
    parser.add_argument("--platform", choices=("auto", "macos", "windows", "linux"), default="auto")
    parser.add_argument("--replace-external", action="store_true", help="replace a non-WG-managed WGLink")
    parser.add_argument("--uninstall", action="store_true", help="remove only this WG install's managed copy")
    parser.add_argument("--print-managed-target", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--yes", action="store_true", help="confirm --uninstall")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--python", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.print_managed_target:
            target = managed_target(
                root=args.root, platform=args.platform, addins_dir=args.addins_dir
            )
            if target is None:
                return 1
            print(target)
            return 0
        if args.uninstall:
            if not args.yes:
                raise InstallError("--uninstall requires --yes")
            status, target = uninstall(
                root=args.root, platform=args.platform, addins_dir=args.addins_dir
            )
        else:
            status, target = install(
                root=args.root,
                platform=args.platform,
                addins_dir=args.addins_dir,
                archive_path=args.archive,
                python=args.python,
                replace_external=args.replace_external,
            )
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Could not install WGLink: {exc}", file=sys.stderr)
        return 2
    if status == "unsupported":
        print("WGLink: skipped (Fusion 360 is supported on macOS and Windows).")
    elif status == "preserved-external":
        print(f"WGLink: preserved the existing non-WG install at {target}.")
    elif status == "removed":
        print(f"WGLink: removed {target}.")
    else:
        print(f"WGLink: installed {target}. Restart Fusion and enable Run on Startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
