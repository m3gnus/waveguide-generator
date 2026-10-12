"""Application data paths and the one-time legacy-directory migration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import ctypes
import ntpath
import uuid
import os
from pathlib import Path
import platform
from typing import Literal, Mapping


DATA_DIR_ENV = "WG2_DATA_DIR"
APP_ROOT_ENV = "WG2_APP_ROOT"
APP_DIRECTORY = "WaveguideGenerator"
LEGACY_APP_DIRECTORY = "WaveguideGenerator2"

log = logging.getLogger("wg.paths")


def display_path(value: str) -> str:
    """Use ordinary Windows path spelling in messages, preserving other inputs."""
    if os.name == "nt":
        if (value[:8].upper() == "\\\\?\\UNC\\"
                and len(parts := value[8:].split("\\")) >= 2
                and parts[0] and parts[1]):
            return "\\\\" + value[8:]
        if (value.startswith("\\\\?\\") and len(value) >= 7
                and value[4] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                and value[5:7] == ":\\"):
            return value[4:]
    return value


def app_root(*, environ: Mapping[str, str] | None = None) -> Path:
    """Return the application source layer, whether bundled or checked out."""

    env = os.environ if environ is None else environ
    configured = env.get(APP_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class DataPaths:
    """All persistent paths owned by Waveguide Generator."""

    root: Path
    db: Path
    logs: Path
    locks: Path
    workspace: Path


@dataclass(frozen=True, slots=True)
class DataDirectoryMigration:
    """Outcome of checking the retired product-named data directory."""

    state: Literal["not_applicable", "unchanged", "moved", "both_exist"]
    old: Path | None
    new: Path


def resolve_data_dir(
    override: str | os.PathLike[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the application data directory without creating it.

    Explicit ``override`` wins over ``WG2_DATA_DIR``.  The injectable keyword
    arguments keep OS-specific behavior deterministic in tests.
    """

    env = os.environ if environ is None else environ
    configured = override if override is not None else env.get(DATA_DIR_ENV)
    if configured:
        return Path(configured).expanduser().absolute()

    os_name = platform.system() if system is None else system
    home_dir = Path.home() if home is None else Path(home)

    if os_name == "Darwin":
        root = home_dir / "Library" / "Application Support"
    elif os_name == "Windows":
        appdata = env.get("APPDATA")
        if not appdata:
            raise RuntimeError(
                "APPDATA is not set, so the Windows data directory cannot be "
                "determined. Set APPDATA or WG2_DATA_DIR and start again."
            )
        root = Path(appdata)
    else:
        xdg_data_home = env.get("XDG_DATA_HOME")
        root = Path(xdg_data_home) if xdg_data_home else home_dir / ".local" / "share"

    return (root / APP_DIRECTORY).expanduser().absolute()


def migrate_legacy_data_dir(
    override: str | os.PathLike[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
    emit_log: bool = True,
) -> DataDirectoryMigration:
    """Move the old default data directory when it is unambiguous to do so.

    Explicit data-directory overrides have no implied legacy sibling and are
    never migrated. If both default directories exist, the current directory
    wins and the legacy directory is deliberately left untouched.
    """

    env = os.environ if environ is None else environ
    new = resolve_data_dir(
        override,
        system=system,
        environ=env,
        home=home,
    )
    if override is not None or env.get(DATA_DIR_ENV):
        return DataDirectoryMigration("not_applicable", None, new)

    old = new.with_name(LEGACY_APP_DIRECTORY)
    if new.exists():
        state: Literal["unchanged", "both_exist"] = (
            "both_exist" if old.exists() else "unchanged"
        )
        result = DataDirectoryMigration(state, old, new)
    elif old.is_dir():
        old.rename(new)
        result = DataDirectoryMigration("moved", old, new)
    else:
        result = DataDirectoryMigration("unchanged", old, new)

    if emit_log:
        log_data_dir_migration(result)
    return result


def log_data_dir_migration(result: DataDirectoryMigration) -> None:
    """Record a migration decision after logging has been configured."""

    if result.state == "moved":
        log.warning("Moved legacy data directory from %s to %s", result.old, result.new)
    elif result.state == "both_exist":
        log.warning(
            "Both the legacy data directory %s and current data directory %s exist; "
            "using the current directory and leaving the legacy directory untouched",
            result.old,
            result.new,
        )


DOCUMENTS_DIRECTORY = "Waveguide Generator"
RUNS_SUBDIRECTORY = "runs"
CADLINK_SUBDIRECTORY = "cadlink"


def documents_root(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """The user-visible parent folder, beside their other documents.

    Runs and the CAD exchange used to default into the application checkout and
    the application-data directory respectively: one of those does not survive a
    reinstall, and neither is somewhere a user looks. They are the user's own
    output, so they belong where the user keeps output.
    """

    env = os.environ if environ is None else environ
    os_name = platform.system() if system is None else system
    home_dir = Path(home) if home is not None else Path.home()

    if os_name == "Windows":
        profile = env.get("USERPROFILE")
        documents = windows_documents_dir() or ((Path(profile) if profile else home_dir) / "Documents")
    else:
        configured = env.get("XDG_DOCUMENTS_DIR")
        documents = Path(configured) if configured else home_dir / "Documents"
    return (documents / DOCUMENTS_DIRECTORY).expanduser().absolute()


def default_runs_dir(**kwargs: object) -> Path:
    """Where run exports land unless the user chooses somewhere else."""

    return documents_root(**kwargs) / RUNS_SUBDIRECTORY  # type: ignore[arg-type]


def legacy_default_runs_dir(
    *, system: str | None = None, environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> Path:
    """The 0.3.5 implicit runs spelling, before known-folder redirection."""
    env = os.environ if environ is None else environ
    home_dir = Path(home) if home is not None else Path.home()
    if (system or platform.system()) == "Windows":
        profile = env.get("USERPROFILE")
        documents = (Path(profile) if profile else home_dir) / "Documents"
    else:
        configured = env.get("XDG_DOCUMENTS_DIR")
        documents = Path(configured) if configured else home_dir / "Documents"
    return (documents / DOCUMENTS_DIRECTORY).expanduser().absolute() / RUNS_SUBDIRECTORY


def proposed_cadlink_dir(**kwargs: object) -> Path:
    """Default CAD exchange folder beneath the user-visible root."""

    return documents_root(**kwargs) / CADLINK_SUBDIRECTORY  # type: ignore[arg-type]


def data_paths(data_dir: str | os.PathLike[str] | None = None, **kwargs: object) -> DataPaths:
    """Build the application path set without touching the filesystem."""

    root = resolve_data_dir(data_dir, **kwargs)
    return DataPaths(
        root=root,
        db=root / "db",
        logs=root / "logs",
        locks=root / "locks",
        workspace=root / "workspace",
    )


def ensure_data_layout(
    data_dir: str | os.PathLike[str] | None = None,
    *,
    migrate_legacy: bool = True,
    **kwargs: object,
) -> DataPaths:
    """Create and return the application data layout."""

    if migrate_legacy:
        migrate_legacy_data_dir(data_dir, **kwargs)
    paths = data_paths(data_dir, **kwargs)
    for path in (paths.root, paths.db, paths.logs, paths.locks, paths.workspace):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def windows_documents_dir() -> Path | None:
    """Ask Windows for Documents, including OneDrive/folder redirection."""
    class GUID(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                    ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    identifier = GUID.from_buffer_copy(uuid.UUID("fdd39ad0-238f-46af-adb4-6c85480369c7").bytes_le)
    output = ctypes.c_void_p()
    try:
        shell = ctypes.windll.shell32
        ole = ctypes.windll.ole32
        shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32,
                                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        if shell.SHGetKnownFolderPath(ctypes.byref(identifier), 0, None, ctypes.byref(output)) != 0:
            return None
        return Path(ctypes.wstring_at(output)) if output.value else None
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        if output.value:
            ole.CoTaskMemFree(output)


def same_folder(left: str | os.PathLike[str], right: str | os.PathLike[str],
                *, system: str | None = None) -> bool:
    """Compare spelling first, then existing filesystem aliases; never rewrite it."""
    module = ntpath if (system or platform.system()) == "Windows" else os.path
    if module.normcase(module.normpath(str(left))) == module.normcase(module.normpath(str(right))):
        return True
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError):
        return False


def default_folder_root(data_dir: Path, *, isolated: bool = False, **kwargs: object) -> Path:
    return data_dir / "workspace-root" if isolated else documents_root(**kwargs)


def validate_folder_root(path: str | os.PathLike[str], *, system: str | None = None) -> None:
    """Reserve Windows MAX_PATH space for nested run exports and staging names.

    Project/run names are user supplied. Reserve 180 characters below runs for
    their names plus export/member/staging components; never truncate a choice.
    """
    if (system or platform.system()) == "Windows":
        units = len(ntpath.join(str(path), "runs").encode("utf-16-le")) // 2
        if units + 180 >= 260:
            raise ValueError("The WG root folder path is too long for Windows run files. "
                             "Choose a shorter path (at most 74 characters) to leave room "
                             "for project names, run names and exports.")
