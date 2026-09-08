#!/usr/bin/env python3
"""Install WGLink from its exact pinned source using WG's existing runtime."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from server.platform.paths import app_root, data_paths  # noqa: E402
from server.platform.staging import publish_staging_directory  # noqa: E402
from shared.safe_names import UnsafeName, collision_key, validate_relative_name  # noqa: E402


REPO_ROOT = app_root()
BUILDER_PATH = REPO_ROOT / "scripts" / "build_wglink_package.py"
RUNTIME_PARENT = REPO_ROOT / "integrations" / "wglink" / "runtime"
RUNTIME_FILE = "wglink_runtime.json"
INSTALL_MARKER = "wglink_install.json"
DEVELOPER_MARKER = "wglink_dev.json"
MANAGED_BY = "waveguide-generator"
TRANSACTION_JOURNAL = ".WGLink-install-transaction.json"
TRANSACTION_LOCK = ".WGLink-install.lock"
TRANSACTION_SCHEMA = 1
TRANSACTION_PHASES = frozenset({"prepared", "previous-moved", "published"})
TRANSACTION_LOCK_TIMEOUT = 30.0
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
    """The interpreter the add-in should shell out to for the resampler.

    A source checkout has a prepared ``.venv``. An installed bundle does not --
    its interpreter is the one already running this code, under
    ``Resources/runtime`` -- so falling back to ``sys.executable`` is what lets
    a packaged Waveguide Generator install its own add-in at all.
    """

    windows = root / ".venv" / "Scripts" / "python.exe"
    posix = root / ".venv" / "bin" / "python"
    if windows.is_file():
        return windows
    if posix.is_file():
        return posix
    return Path(sys.executable)


def _bundled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get("WG2_BUNDLE") == "1"


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".wg-write-probe"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def state_root(
    root: Path,
    *,
    data_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Where the package cache and materialized payloads may be written.

    A source checkout keeps them where they have always been, inside the
    checkout. An installed bundle cannot: its app layer lives inside a
    code-signed application directory, so writing a payload there would either
    fail or invalidate the signature. That single unwritable-root assumption is
    why the packaged application had never been able to install or update
    WGLink at all, and why the only working install on a developer's machine
    named their *checkout* as its managing root.
    """

    inside = root / "integrations" / "wglink" / "runtime"
    if not _bundled(environ) and _writable(inside):
        return inside
    paths = data_paths(data_dir) if data_dir is not None else data_paths()
    return paths.root / "integrations" / "wglink" / "runtime"


def shipped_package(root: Path, version: str, commit: str) -> Path:
    """The pinned add-in package a release carries beside its source pin.

    Built at release time so an installed application can update the add-in
    with no network at all. Absent from a source checkout, where the cache and
    the pinned fetch still answer.
    """

    return (
        root / "integrations" / "wglink" / "packages"
        / f"wglink-{version}-{commit}.zip"
    )


def _safe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    pure = PurePosixPath(name)
    mode = (info.external_attr >> 16) & 0o170000
    if (
        info.is_dir()
        or pure.is_absolute()
        or len(pure.parts) < 2
        or pure.parts[0] != "wglink"
        or pure.as_posix() != name
        or mode == 0o120000
    ):
        return False
    try:
        for component in pure.parts:
            validate_relative_name(component, what="WGLink archive component")
    except UnsafeName:
        return False
    return True


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
        if not infos or len(infos) > MAX_MEMBERS or len(names) != len({collision_key(name) for name in names}):
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


def _cache_path(state: Path, version: str, commit: str) -> Path:
    return state / "packages" / f"wglink-{version}-{commit}.zip"


def _fetch_package(root: Path, state: Path, *, offline_only: bool = False) -> Path:
    builder = _load_builder()
    spec = builder.source_spec(root / "integrations" / "wglink" / "source.json")
    version = builder.declared_version(root / "shared" / "version.json")
    commit = str(spec["commit"])
    # A release ships the package it pins, so an installed application updates
    # its add-in without reaching the network at all. Checked before the cache
    # because it is the authoritative copy for that build.
    shipped = shipped_package(root, version, commit)
    if shipped.is_file():
        try:
            verify_package(shipped, root=root)
            return shipped
        except InstallError as exc:
            if offline_only:
                raise InstallError(
                    f"Bundled WGLink package failed verification: {shipped}"
                ) from exc
    elif offline_only:
        raise InstallError(
            f"This Waveguide Generator bundle does not contain its WGLink package: {shipped}"
        )
    cached = _cache_path(state, version, commit)
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
            # The package is content-addressed and, once a release ships it, it
            # is inside the app layer's treeSha256 -- which the release workflow
            # asserts is identical on the Windows and macOS build hosts. Git for
            # Windows defaults to core.autocrlf=true and the add-in repository
            # declares no `text` attribute for its sources, so an inherited
            # checkout would arrive CRLF there and LF here, and the two hosts
            # would build different archives from the same commit. Measured: 13
            # of 43 members differ, each longer by a byte a line.
            #
            # waveguide-generator buys this property for its own files with
            # `* text=auto eol=lf`; this reaches outside that, so it has to
            # state the same thing here rather than inherit ambient config.
            ["git", "-C", str(source), "config", "core.autocrlf", "false"],
            ["git", "-C", str(source), "config", "core.eol", "lf"],
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


RUNTIME_ID_COMMIT_LENGTH = 12


def _runtime_id(provenance: dict[str, object]) -> str:
    version = str(provenance["waveguideGeneratorVersion"])
    # Only the directory name is shortened. Identity is still established by the
    # full commit in provenance.json, which _runtime_matches compares verbatim,
    # so this buys 28 characters of Windows MAX_PATH headroom for free.
    commit = str(provenance["sourceCommit"])[:RUNTIME_ID_COMMIT_LENGTH]
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


def ensure_package(root: Path, *, state: Path | None = None) -> Path:
    """The pinned add-in package, from whatever source can produce it.

    Shared by the installer and by the bundle build, so a release ships exactly
    the archive an install would have produced: shipped copy first, then this
    machine's cache, then a shallow fetch of the pinned commit.
    """

    return _fetch_package(root, state if state is not None else state_root(root))


def _materialize_runtime(
    archive_path: Path,
    *,
    root: Path,
    state: Path,
) -> tuple[Path, dict[str, object]]:
    provenance, payloads = verify_package(archive_path, root=root)
    parent = state / "payloads"
    destination = parent / _runtime_id(provenance)
    if _runtime_matches(destination / "wglink", provenance, payloads):
        return destination / "wglink", provenance
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    staging = publish_staging_directory(parent, ".wglink-payload-")
    try:
        for name, data in payloads.items():
            path = staging.joinpath(*PurePosixPath(name).parts)
            if not path.resolve().is_relative_to(staging.resolve()):
                raise InstallError("REFUSING TO EXTRACT: member escapes WGLink staging")
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
    if _path_present(target / DEVELOPER_MARKER):
        return None
    marker_path = target / INSTALL_MARKER
    if marker_path.is_symlink() or not marker_path.is_file():
        return None
    try:
        value = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


_INSTALL_MARKER_FIELDS = frozenset(
    {
        "schema",
        "managedBy",
        "waveguideGeneratorRoot",
        "waveguideGeneratorVersion",
        "sourceCommit",
        "addinVersion",
    }
)


def _valid_install_marker(marker: object, root: Path) -> bool:
    if not isinstance(marker, dict) or set(marker) != _INSTALL_MARKER_FIELDS:
        return False
    if type(marker.get("schema")) is not int or marker["schema"] != 1:
        return False
    if marker.get("managedBy") != MANAGED_BY:
        return False
    if not all(
        isinstance(marker.get(name), str) and bool(marker[name])
        for name in ("waveguideGeneratorRoot", "waveguideGeneratorVersion", "sourceCommit", "addinVersion")
    ):
        return False
    commit = str(marker["sourceCommit"])
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        return False
    try:
        return Path(str(marker["waveguideGeneratorRoot"])).resolve() == root.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def is_managed_target(target: Path, root: Path) -> bool:
    marker = _marker(target)
    return _valid_install_marker(marker, root)


def _path_present(path: Path) -> bool:
    """Existence which does not lose broken symlinks."""

    return path.exists() or path.is_symlink()


def _set_file_lock(handle, *, acquire: bool) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        mode = msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK
        msvcrt.locking(handle.fileno(), mode, 1)
    else:
        import fcntl

        mode = fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN
        fcntl.flock(handle.fileno(), mode)


@contextmanager
def _operation_lock(
    addins_dir: Path,
    *,
    timeout: float = TRANSACTION_LOCK_TIMEOUT,
):
    """Serialize target changes with an OS lock released on process death."""

    addins_dir.mkdir(parents=True, exist_ok=True)
    lock_path = addins_dir / TRANSACTION_LOCK
    if lock_path.is_symlink():
        raise InstallError(f"WGLink operation lock is unsafe: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b")
    except OSError as exc:
        raise InstallError(f"Could not open WGLink operation lock {lock_path}: {exc}") from exc
    acquired = False
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            try:
                _set_file_lock(handle, acquire=True)
                acquired = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise InstallError(
                        "Another WGLink install, update, or uninstall is still running; "
                        "try again when it finishes."
                    ) from exc
                time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))
        yield
    finally:
        try:
            if acquired:
                _set_file_lock(handle, acquire=False)
        finally:
            handle.close()


def _remove_entry(path: Path) -> None:
    """Remove one known transaction entry without following a symlink."""

    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _sync_directory(path: Path) -> None:
    """Best-effort durability for a rename or unlink in *path*.

    Windows does not expose directory handles through ``os.open``. The journal
    file itself is still flushed there; on platforms which permit it, flushing
    the directory also makes its rename durable across a power loss.
    """

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_transaction(path: Path, transaction: dict[str, object]) -> None:
    """Atomically persist and flush the replacement journal."""

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(transaction, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _sync_directory(path.parent)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _file_inventory(root: Path) -> dict[str, str]:
    paths = list(root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise InstallError(f"WGLink replacement payload contains a symlink: {root}")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def _valid_file_inventory(value: object) -> bool:
    if not isinstance(value, dict) or not value or len(value) > MAX_MEMBERS:
        return False
    for name, digest in value.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            return False
        pure = PurePosixPath(name)
        if (
            "\\" in name
            or pure.is_absolute()
            or not pure.parts
            or ".." in pure.parts
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return False
    return {"WGLink.py", INSTALL_MARKER, RUNTIME_FILE}.issubset(value)


_BYTECODE_NAME = re.compile(
    r"^(?P<source>.+)\.[A-Za-z][A-Za-z0-9_]*-\d+[A-Za-z0-9_-]*"
    r"(?:\.opt-\d+)?\.pyc$"
)


def _allowed_generated_bytecode(name: str, expected: Mapping[str, str]) -> bool:
    """Whether *name* is a PEP 3147 cache for an expected Python source."""

    pure = PurePosixPath(name)
    if len(pure.parts) < 2 or pure.parts[-2] != "__pycache__":
        return False
    match = _BYTECODE_NAME.fullmatch(pure.name)
    if match is None:
        return False
    source = PurePosixPath(*pure.parts[:-2], f"{match.group('source')}.py")
    return source.as_posix() in expected


def _read_transaction(path: Path, addins_dir: Path, root: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise InstallError(f"WGLink replacement journal is not a regular file: {path}")
    transaction = _read_json(path, "WGLink replacement journal")
    if (
        transaction.get("schema") != TRANSACTION_SCHEMA
        or transaction.get("managedBy") != MANAGED_BY
        or transaction.get("phase") not in TRANSACTION_PHASES
        or not isinstance(transaction.get("hadPrevious"), bool)
        or not isinstance(transaction.get("replaceExternal"), bool)
        or not isinstance(transaction.get("expectedMarker"), dict)
        or not _valid_file_inventory(transaction.get("expectedFiles"))
    ):
        raise InstallError(f"WGLink replacement journal is invalid: {path}")
    try:
        recorded_root = Path(str(transaction.get("waveguideGeneratorRoot"))).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise InstallError(f"WGLink replacement journal has an invalid owner: {path}") from exc
    if recorded_root != root.resolve():
        raise InstallError(
            "WGLink replacement journal belongs to a different Waveguide Generator "
            f"installation: {path}"
        )
    if not _valid_install_marker(transaction["expectedMarker"], root):
        raise InstallError(f"WGLink replacement journal has an invalid marker: {path}")
    workspace_name = transaction.get("workspace")
    if (
        not isinstance(workspace_name, str)
        or Path(workspace_name).name != workspace_name
        or not workspace_name.startswith(".WGLink-install-")
    ):
        raise InstallError(f"WGLink replacement journal has an unsafe workspace: {path}")
    workspace = addins_dir / workspace_name
    if _path_present(workspace) and (workspace.is_symlink() or not workspace.is_dir()):
        raise InstallError(f"WGLink replacement workspace is unsafe: {workspace}")
    return transaction


def _matches_transaction_target(
    target: Path,
    root: Path,
    transaction: dict[str, object],
) -> bool:
    marker = _marker(target)
    if (
        not _valid_install_marker(marker, root)
        or marker != transaction.get("expectedMarker")
    ):
        return False
    try:
        expected = transaction.get("expectedFiles")
        if not isinstance(expected, dict):
            return False
        actual = _file_inventory(target)
        if any(actual.get(name) != digest for name, digest in expected.items()):
            return False
        if any(
            not _allowed_generated_bytecode(name, expected)
            for name in actual.keys() - expected.keys()
        ):
            return False
        runtime = _read_json(target / RUNTIME_FILE, "installed WGLink runtime")
        runtime_root = Path(str(runtime.get("root")))
        python = Path(str(runtime.get("python")))
        return (
            runtime.get("schema") == 1
            and python.is_file()
            and (runtime_root / "scripts" / "wglink_resample.py").is_file()
        )
    except (InstallError, OSError, RuntimeError, ValueError):
        return False


def _finish_transaction(journal: Path, workspace: Path) -> None:
    _remove_entry(workspace)
    journal.unlink(missing_ok=True)
    _sync_directory(journal.parent)


def _recover_install_transaction(addins_dir: Path, root: Path) -> None:
    """Restore or finish a replacement interrupted after its journal was flushed.

    Moving the prepared directory onto ``WGLink`` is the commit point. Before
    that rename, recovery restores the prior entry. After it, recovery accepts
    the new copy only when its complete ownership marker matches the journal;
    an unexpected entry is preserved and stops recovery.
    """

    journal = addins_dir / TRANSACTION_JOURNAL
    if not _path_present(journal):
        return
    transaction = _read_transaction(journal, addins_dir, root)
    workspace = addins_dir / str(transaction["workspace"])
    staging = workspace / "WGLink"
    previous = workspace / "previous"
    target = addins_dir / "WGLink"
    target_present = _path_present(target)
    staging_present = _path_present(staging)
    previous_present = _path_present(previous)

    if not target_present:
        if previous_present:
            previous.rename(target)
            _sync_directory(addins_dir)
        elif transaction["hadPrevious"]:
            raise InstallError(
                "WGLink replacement was interrupted and its prior installation is "
                f"missing from {workspace}; preserving the journal for manual recovery."
            )
        elif transaction["phase"] == "published":
            raise InstallError(
                "WGLink replacement was published but its installed target is missing; "
                f"preserving the journal at {journal}."
            )
        _finish_transaction(journal, workspace)
        return

    if previous_present:
        # ``staging`` disappears only when its atomic rename to ``target`` has
        # committed. Never discard a prior install merely because a journal
        # claims success: the installed marker must name this exact payload.
        if (
            staging_present
            or not _matches_transaction_target(target, root, transaction)
            or (
                _path_present(previous / DEVELOPER_MARKER)
                and not transaction["replaceExternal"]
            )
        ):
            raise InstallError(
                "WGLink replacement recovery found an unexpected installed target; "
                f"preserving both it and the prior copy in {workspace}."
            )
        _remove_entry(previous)
    elif not staging_present and (
        transaction["phase"] == "published" or not transaction["hadPrevious"]
    ):
        if not _matches_transaction_target(target, root, transaction):
            raise InstallError(
                "WGLink replacement recovery found an unexpected installed target; "
                f"preserving it and the journal at {journal}."
            )

    # A remaining staging entry means publication never happened (or rollback
    # already restored the old target), so removing only the transaction
    # workspace leaves the target untouched.
    _finish_transaction(journal, workspace)


def install(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
    archive_path: Path | None = None,
    python: Path | None = None,
    replace_external: bool = False,
    data_dir: Path | None = None,
    offline_only: bool = False,
) -> tuple[str, Path | None]:
    resolved_platform = _platform_name(platform)
    resolved_addins = addins_dir or default_addins_dir(resolved_platform)
    if resolved_addins is None:
        return "unsupported", None
    resolved_addins = resolved_addins.expanduser().resolve()
    resolved_addins.mkdir(parents=True, exist_ok=True)
    with _operation_lock(resolved_addins):
        return _install_unlocked(
            root=root,
            platform=resolved_platform,
            addins_dir=resolved_addins,
            archive_path=archive_path,
            python=python,
            replace_external=replace_external,
            data_dir=data_dir,
            offline_only=offline_only,
        )


def _install_unlocked(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
    archive_path: Path | None = None,
    python: Path | None = None,
    replace_external: bool = False,
    data_dir: Path | None = None,
    offline_only: bool = False,
) -> tuple[str, Path | None]:
    root = root.resolve()
    platform = _platform_name(platform)
    addins_dir = addins_dir or default_addins_dir(platform)
    if addins_dir is None:
        return "unsupported", None
    target = addins_dir.expanduser().resolve() / "WGLink"
    addins_dir = target.parent
    _recover_install_transaction(addins_dir, root)
    if _path_present(target) and not is_managed_target(target, root):
        if not replace_external:
            return "preserved-external", target
    python = (python or _venv_python(root)).resolve()
    if not python.is_file():
        raise InstallError(
            f"WGLink needs Waveguide Generator's prepared Python environment: {python}"
        )
    state = state_root(root, data_dir=data_dir)
    archive_path = (
        archive_path.resolve()
        if archive_path
        else _fetch_package(root, state, offline_only=offline_only)
    )
    runtime_root, provenance = _materialize_runtime(archive_path, root=root, state=state)
    source_addin = runtime_root / "fusion-addins" / "WGLink"
    resampler = runtime_root / "scripts" / "wglink_resample.py"
    if not (source_addin / "WGLink.py").is_file() or not resampler.is_file():
        raise InstallError("WGLink package is missing its add-in or resampler")

    addins_dir.mkdir(parents=True, exist_ok=True)
    staging_parent = publish_staging_directory(addins_dir, ".WGLink-install-")
    staging = staging_parent / "WGLink"
    previous = staging_parent / "previous"
    expected_marker = {
        "schema": 1,
        "managedBy": MANAGED_BY,
        "waveguideGeneratorRoot": str(root),
        "waveguideGeneratorVersion": provenance["waveguideGeneratorVersion"],
        "sourceCommit": provenance["sourceCommit"],
        "addinVersion": provenance["addinVersion"],
    }
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
            json.dumps(expected_marker, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        expected_files = _file_inventory(staging)
    except BaseException:
        _remove_entry(staging_parent)
        raise

    journal = addins_dir / TRANSACTION_JOURNAL
    transaction: dict[str, object] = {
        "schema": TRANSACTION_SCHEMA,
        "managedBy": MANAGED_BY,
        "waveguideGeneratorRoot": str(root),
        "workspace": staging_parent.name,
        "hadPrevious": _path_present(target),
        "replaceExternal": replace_external,
        "expectedMarker": expected_marker,
        "expectedFiles": expected_files,
        "phase": "prepared",
    }
    try:
        _write_transaction(journal, transaction)
    except BaseException:
        _remove_entry(staging_parent)
        raise

    try:
        if transaction["hadPrevious"]:
            target.rename(previous)
            _sync_directory(addins_dir)
        transaction["phase"] = "previous-moved"
        _write_transaction(journal, transaction)
        staging.rename(target)
        _sync_directory(addins_dir)
        transaction["phase"] = "published"
        _write_transaction(journal, transaction)
        _recover_install_transaction(addins_dir, root)
    except Exception:
        # Ordinary failures roll back immediately. Process termination and
        # power loss leave the flushed journal for the next install/uninstall.
        _recover_install_transaction(addins_dir, root)
        raise
    return "installed", target


def uninstall(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
    data_dir: Path | None = None,
) -> tuple[str, Path | None]:
    resolved_addins = addins_dir or default_addins_dir(_platform_name(platform))
    if resolved_addins is None:
        return "unsupported", None
    resolved_addins = resolved_addins.expanduser().resolve()
    if not resolved_addins.is_dir():
        return "preserved-external", None
    with _operation_lock(resolved_addins):
        return _uninstall_unlocked(
            root=root,
            platform=platform,
            addins_dir=resolved_addins,
            data_dir=data_dir,
        )


def _uninstall_unlocked(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
    data_dir: Path | None = None,
) -> tuple[str, Path | None]:
    root = root.resolve()
    addins_dir = addins_dir or default_addins_dir(_platform_name(platform))
    if addins_dir is None:
        return "unsupported", None
    target = addins_dir.expanduser().resolve() / "WGLink"
    _recover_install_transaction(target.parent, root)
    if not is_managed_target(target, root):
        return "preserved-external", target if _path_present(target) else None
    shutil.rmtree(target)
    # Both, because where the payloads live depends on whether this root was
    # writable when they were installed, and an uninstall must not leave the
    # other one behind.
    for runtime in {
        root / "integrations" / "wglink" / "runtime",
        state_root(root, data_dir=data_dir),
    }:
        if runtime.exists():
            shutil.rmtree(runtime)
    return "removed", target


def managed_target(
    *,
    root: Path = REPO_ROOT,
    platform: str = "auto",
    addins_dir: Path | None = None,
) -> Path | None:
    resolved_addins = addins_dir or default_addins_dir(_platform_name(platform))
    if resolved_addins is None:
        return None
    resolved_addins = resolved_addins.expanduser().resolve()
    if not resolved_addins.is_dir():
        return None
    with _operation_lock(resolved_addins):
        return _managed_target_unlocked(
            root=root,
            platform=platform,
            addins_dir=resolved_addins,
        )


def _managed_target_unlocked(
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
    _recover_install_transaction(target.parent, root)
    return target if is_managed_target(target, root) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="install a locally built package")
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="require the WGLink package shipped in this application bundle",
    )
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
                offline_only=args.offline_only,
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
