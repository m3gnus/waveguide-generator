"""Application data paths and the one-time legacy-directory migration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import platform
from typing import Literal, Mapping


DATA_DIR_ENV = "WG2_DATA_DIR"
APP_ROOT_ENV = "WG2_APP_ROOT"
APP_DIRECTORY = "WaveguideGenerator"
LEGACY_APP_DIRECTORY = "WaveguideGenerator2"

log = logging.getLogger("wg.paths")


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
        documents = (Path(profile) if profile else home_dir) / "Documents"
    else:
        configured = env.get("XDG_DOCUMENTS_DIR")
        documents = Path(configured) if configured else home_dir / "Documents"
    return (documents / DOCUMENTS_DIRECTORY).expanduser().absolute()


def default_runs_dir(**kwargs: object) -> Path:
    """Where run exports land unless the user chooses somewhere else."""

    return documents_root(**kwargs) / RUNS_SUBDIRECTORY  # type: ignore[arg-type]


def proposed_cadlink_dir(**kwargs: object) -> Path:
    """What the CAD-link folder picker starts on. Never an implicit fallback.

    ``CadWorkspaceState`` deliberately has no default, because hiding the CAD
    exchange somewhere implicit makes first-time setup impossible to understand
    and lets an output-folder change disconnect Fusion. This only makes
    accepting the obvious location one click instead of a decision.
    """

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
