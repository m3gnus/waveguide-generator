"""The server-owned identity and immutable location of every run archive.

The registry is additive to cadlink.db's older format. Folder aliases deliberately
share a location; allocation and alias adoption run in the caller's transaction.
Legacy job locations are derived only, never backfilled or renamed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, NamedTuple
import unicodedata

from server.platform.acl_repair import path_has_reparse_point
from server.workspace.archive import archive_folder_slug
from server.workspace.portable_paths import path_segments

logger = logging.getLogger(__name__)


class ArchiveLocation(NamedTuple):
    key: str
    folder: str
    run: str


def name_key(name: object) -> str:
    return "name:" + unicodedata.normalize("NFKC", str(name or "").strip())


def owner_key(
    *,
    lineage_id: str | None = None,
    design_uid: str | None = None,
    model_id: str | None = None,
    name: object = None,
) -> str:
    if lineage_id:
        return f"lineage:{lineage_id}"
    if model_id and model_id.startswith("mesh-file:"):
        return f"model:{model_id}"
    if design_uid:
        return f"design:{design_uid}"
    return name_key(name or "design")


def initialize_registry(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS archive_folders (
        owner_key TEXT PRIMARY KEY, folder TEXT NOT NULL COLLATE NOCASE,
        created_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS archive_folders_by_folder ON archive_folders(folder)")
    mirrors = []
    for table, column, prefix in (
        ("lineage_cad_names", "lineage_id", "lineage:"),
        ("imported_models", "model_id", "model:"),
    ):
        for row in conn.execute(
            f"SELECT {column}, archive_stem FROM {table} "
            "WHERE archive_stem IS NOT NULL AND archive_stem != ''"
        ):
            # Imported CAD models are lineages, not file identities.
            identity = str(row[0])
            key = (
                "lineage:" + identity
                if table == "imported_models" and not identity.startswith("mesh-file:")
                else prefix + identity
            )
            mirrors.append((table, column, identity, key, str(row[1])))

    # Keep every original location reserved, including rows not yet registered
    # and locations whose mirror moves below. Older releases can add mirrors
    # without consulting the registry, and lineage stems have a unique index.
    reserved = {folder.casefold() for *_, folder in mirrors}
    reserved.update(str(row[0]).casefold() for row in conn.execute("SELECT folder FROM archive_folders"))
    for table, column, identity, key, old_folder in mirrors:
        if conn.execute("SELECT 1 FROM archive_folders WHERE owner_key=?", (key,)).fetchone():
            continue
        conn.execute("SAVEPOINT archive_mirror_backfill")
        try:
            folder = claim_in_transaction(
                conn, key, old_folder,
                reserved_folders=reserved - {old_folder.casefold()},
            )
            if folder != old_folder:
                conn.execute(f"UPDATE {table} SET archive_stem=? WHERE {column}=?", (folder, identity))
                logger.warning("Archive mirror ownership conflict for %s; new runs use %s instead of %s", key, folder, old_folder)
        except (sqlite3.IntegrityError, ValueError) as exc:
            # A conflicting mirror must not prevent the rest of the database
            # from opening. Roll back its claim too, then retry on next open.
            conn.execute("ROLLBACK TO archive_mirror_backfill")
            logger.warning("Archive mirror backfill conflict for %s; leaving %s unchanged: %s", key, old_folder, exc)
        finally:
            conn.execute("RELEASE archive_mirror_backfill")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def archive_root(store: Any) -> Path | None:
    provider = getattr(store, "archive_root", None)
    try:
        value = provider() if callable(provider) else provider
        return Path(value) if value else None
    except (OSError, RuntimeError, ValueError):
        return None


def registered_folder(store: Any, key: str) -> str | None:
    if store is None:
        return None
    row = store._read_one("SELECT folder FROM archive_folders WHERE owner_key = ?", (key,))
    return str(row["folder"]) if row else None


def project_folder(store: Any, lineage_id: str, filename: str = "") -> str:
    """Read-only project lookup; a listing must never claim a name."""
    registered = registered_folder(store, f"lineage:{lineage_id}")
    names = store.get_lineage_cad_names(lineage_id) or {}
    return (
        registered
        or str(names.get("archive_stem") or "").strip()
        or str(names.get("bundle_stem") or "").strip()
        or Path(filename).stem
        or str(names.get("document_name") or "").strip()
    )


def _record_key(record: Mapping[str, Any]) -> str:
    # Accept pointers from before the registry existed, by exact name only.
    return str(
        record.get("owner")
        or owner_key(
            lineage_id=record.get("lineageId"),
            design_uid=record.get("designUid"),
            model_id=record.get("modelId"),
            name=record.get("name"),
        )
    )


def _disk_matches(folder: Path, key: str, aliases: set[str]) -> bool:
    allowed = {key, *aliases}
    try:
        if folder.is_symlink() or path_has_reparse_point(folder) or not folder.is_dir():
            return False
        pointer = folder / "design.json"
        has_pointer = pointer.exists() or pointer.is_symlink()
        if has_pointer:
            if pointer.is_symlink():
                return False
            record = json.loads(pointer.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or _record_key(record) not in allowed:
                return False
            if record.get("name"):
                allowed.add(name_key(record["name"]))
        # A matching pointer cannot license runs belonging to a different owner.
        for child in folder.iterdir():
            if child.name in {"cad", ".DS_Store", "design.json"}:
                continue
            if child.is_symlink() or path_has_reparse_point(child):
                return False
            run = child / "run.json"
            if not run.is_file():
                if not has_pointer:
                    return False
                continue
            if run.is_symlink():
                return False
            record = json.loads(run.read_text(encoding="utf-8"))
            design = record.get("design") or {}
            lineage = design.get("lineageId")
            uid = design.get("uid")
            model = (record.get("importedMesh") or {}).get("modelId")
            run_key = owner_key(lineage_id=lineage, design_uid=uid, model_id=model)
            if (lineage or uid or model) and run_key not in allowed:
                return False
            if not (lineage or uid or model) and design.get("name") and name_key(design["name"]) not in allowed:
                return False
            if not has_pointer:
                return False
        return has_pointer or key.startswith("lineage:")
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _valid_folder(folder: str) -> bool:
    try:
        return path_segments(folder, "archive folder") == [folder]
    except ValueError:
        return False


def _safe_base(preferred: object) -> str:
    base = archive_folder_slug(preferred, "design")[:230]
    if not _valid_folder(base):
        base = base.replace(".", "_").rstrip(" ") + "-design"
    # Every candidate, including numeric suffixes, is checked before publication.
    path_segments(base, "archive folder")
    return base


def claim_in_transaction(
    conn: sqlite3.Connection,
    key: str,
    preferred: object,
    *,
    root: Path | None = None,
    aliases: tuple[str, ...] = (),
    adopt_aliases: tuple[str, ...] = (),
    reserved_folders: set[str] | frozenset[str] = frozenset(),
) -> str:
    # Backfill supplies case-folded locations held by other legacy mirrors.
    rows = {str(r[0]): str(r[1]) for r in conn.execute("SELECT owner_key, folder FROM archive_folders")}
    disk: dict[str, Path] = {}
    if root is not None:
        try:
            disk = {p.name.casefold(): p for p in root.iterdir()}
        except OSError:
            pass  # Allocation may reserve offline; every later claim rechecks disk.
    registered = rows.get(key)
    shared = {owner for owner, folder in rows.items() if registered and folder.casefold() == registered.casefold()}
    target = registered or next((rows[a] for a in adopt_aliases if a in rows), None)
    adopted_owners = {owner for owner, folder in rows.items() if target and folder.casefold() == target.casefold()}
    allowed = {key, *adopt_aliases, *shared, *adopted_owners}

    def taken(candidate: str) -> bool:
        if candidate.casefold() in reserved_folders:
            return True
        if key.startswith("lineage:") and any(
            owner.startswith("lineage:") and owner != key and folder.casefold() == candidate.casefold()
            for owner, folder in rows.items()
        ):
            return True
        if not _valid_folder(candidate):
            return True
        if any(folder.casefold() == candidate.casefold() and owner not in allowed for owner, folder in rows.items()):
            return True
        existing = disk.get(candidate.casefold())
        return existing is not None and not _disk_matches(existing, key, allowed)

    adopt = registered or next((rows[a] for a in adopt_aliases if a in rows), None)
    if adopt and taken(adopt):
        adopt = None
    base = _safe_base(registered or preferred)
    if not adopt and not registered:
        for existing in disk.values():
            if (existing / "design.json").is_file() and not taken(existing.name):
                adopt = existing.name
                break
    folder = adopt or base
    count = 2
    # A repaired registration always leaves its old folder for existing runs.
    if registered and not adopt:
        folder = f"{base}-{count}"
        count += 1
    while taken(folder):
        folder = f"{base}-{count}"
        count += 1
    if folder.casefold() in disk:
        folder = disk[folder.casefold()].name
    path_segments(folder, "archive folder")
    if registered and folder != registered:
        logger.warning("Archive registration conflict for %s; new runs use %s instead of %s", key, folder, registered)
        # Move intentional aliases together, but never rewrite a run's sealed metadata.
        for owner in shared:
            conn.execute("UPDATE archive_folders SET folder=? WHERE owner_key=?", (folder, owner))
    conn.execute("INSERT OR REPLACE INTO archive_folders VALUES (?, ?, ?)", (key, folder, _now()))
    for alias in aliases:
        if alias.startswith("name:") and any(
            existing.name.casefold() != folder.casefold() and _disk_matches(existing, alias, set())
            for existing in disk.values()
        ):
            continue
        conn.execute("INSERT OR IGNORE INTO archive_folders VALUES (?, ?, ?)", (alias, folder, _now()))
    return folder


def claim_project_folder(
    store: Any,
    key: str,
    preferred: object,
    *,
    aliases: tuple[str, ...] = (),
    adopt_aliases: tuple[str, ...] = (),
) -> str:
    if store is None:
        return _safe_base(preferred)
    store.initialize()
    if key.startswith("lineage:"):
        record = store.record_lineage_cad_names(
            key[len("lineage:") :],
            archive_stem=str(preferred or "design"),
            archive_aliases=aliases,
            adopt_archive_aliases=adopt_aliases,
        )
        return str(record["archive_stem"])
    with store._lock, store._transaction() as conn:
        return claim_in_transaction(
            conn,
            key,
            preferred,
            root=archive_root(store),
            aliases=aliases,
            adopt_aliases=adopt_aliases,
        )


def suffixed_legacy_stem(base: str, lineage_id: str, used: set[str]) -> str:
    """Keep pre-registry migrations byte-compatible; new claims use -2, -3."""
    digest = hashlib.sha256(lineage_id.encode("utf-8")).hexdigest()
    for length in range(12, len(digest) + 1, 4):
        candidate = f"{base}-{digest[:length]}"
        if candidate.casefold() not in used:
            return candidate
    count = 2
    while f"{base}-{digest}-{count}".casefold() in used:
        count += 1
    return f"{base}-{digest}-{count}"


def bind_design_lineage(
    store: Any, lineage_id: str, design_uid: str | None, preferred: str
) -> str | None:
    """First linking aliases an existing design folder before CAD can claim one."""
    if not design_uid or not registered_folder(store, f"design:{design_uid}"):
        return None
    folder = claim_project_folder(
        store,
        f"lineage:{lineage_id}",
        preferred,
        aliases=(f"design:{design_uid}",),
        adopt_aliases=(f"design:{design_uid}",),
    )
    return folder


def _design_name(config: Mapping[str, Any], label: object = None) -> str:
    if "design_name" in config:
        return str(config.get("design_name") or "Untitled")
    return str(config.get("label") or label or "design")


def submission_archive(
    store: Any, config: Mapping[str, Any], metadata: Mapping[str, Any]
) -> dict[str, str]:
    imported = metadata.get("imported_geometry") or {}
    design_id = config.get("design_id") or imported.get("anchor_design_id")
    design = store.get_design(design_id) if store is not None and design_id else None
    lineage = imported.get("anchor_lineage_id") or (design or {}).get("lineage_id")
    uid = config.get("design_uid")
    name = _design_name(config)
    model = imported.get("model_id")
    key = owner_key(lineage_id=lineage, design_uid=uid, model_id=model, name=name)
    legacy = (config.get("client_metadata") or {}).get("legacy_design_name")
    aliases = (name_key(legacy or name),) if uid and not lineage and not model else ()
    adopt = (name_key(legacy),) if uid and legacy else ()
    if lineage and uid:
        adopt = (f"design:{uid}",)
        aliases = (f"design:{uid}",)
    preferred = imported.get("archive_stem") or name
    if lineage and store is not None:
        names = store.get_lineage_cad_names(lineage) or {}
        preferred = names.get("archive_stem") or names.get("bundle_stem") or preferred
    folder = claim_project_folder(store, key, preferred, aliases=aliases, adopt_aliases=adopt)
    return {"key": key, "folder": folder}


def run_folder_name(number: int | None, label: object, formula: object = None) -> str:
    return f"{max(1, int(number)) if number is not None else 'pending'}_{archive_folder_slug(label or formula or 'design', 'design')}"


def job_archive_location(
    row: Mapping[str, Any], cad_source: Mapping[str, Any] | None = None
) -> ArchiveLocation:
    cad_source = cad_source or {}
    metadata = row.get("task_metadata") or {}
    archive = metadata.get("archive") or {}
    imported = metadata.get("imported_geometry") or {}
    config = row.get("config_json") or {}
    summary = row.get("config_summary_json") or {}
    # This is exactly the old browser path, including cad-import and decorated labels.
    folder = (
        archive.get("folder")
        or imported.get("archive_stem")
        or cad_source.get("archive_stem")
        or archive_folder_slug(
            row.get("label") or summary.get("formula_type") or "design", "design"
        )
    )
    run = archive.get("run") or run_folder_name(
        row.get("run_number"), row.get("label"), summary.get("formula_type")
    )
    key = archive.get("key") or owner_key(
        lineage_id=imported.get("anchor_lineage_id") or cad_source.get("lineage_id"),
        design_uid=config.get("design_uid"),
        model_id=imported.get("model_id"),
        name=config.get("design_name") or row.get("label") or summary.get("formula_type"),
    )
    previous = metadata.get("archived_path")
    if previous and Path(previous).is_absolute():
        # Absolute recorded locations also win over subsequently repaired mirrors.
        folder, run = Path(previous).parent.name, Path(previous).name
    return ArchiveLocation(str(key), str(folder), str(run))


def record_run_segment(conn: sqlite3.Connection, job_id: str) -> None:
    """Seal the run segment in the same transaction that assigns its number."""
    row = conn.execute(
        "SELECT s.label, s.config_summary_json, s.task_metadata_json, i.run_number "
        "FROM simulation_jobs s JOIN job_identity i ON i.job_id=s.id WHERE s.id=?",
        (job_id,),
    ).fetchone()
    metadata = json.loads(row["task_metadata_json"] or "{}")
    if metadata.get("archive"):
        metadata["archive"].setdefault(
            "run",
            run_folder_name(
                row["run_number"],
                row["label"],
                json.loads(row["config_summary_json"] or "{}").get("formula_type"),
            ),
        )
        conn.execute(
            "UPDATE simulation_jobs SET task_metadata_json=? WHERE id=?",
            (json.dumps(metadata), job_id),
        )


class RunFolderUnavailableError(OSError):
    def __init__(self, root: Path) -> None:
        super().__init__(
            f"The folder for this run is not available: {root}. Reconnect it and try again, "
            "or delete the run without protecting its Fusion model."
        )


def claim_run_write_path(jobs: Any, row: Mapping[str, Any], current_root: Any,
                         cad_source: Mapping[str, Any] | None = None) -> Path:
    """Pin first write; later writes use that absolute location or fail offline.

    Selection changes affect only runs without a recorded write location.
    Never recreate an unavailable recorded Runs root (for example a missing drive).
    """
    from server.workspace.run_moves import archive_write

    with jobs._lock:
        fresh = jobs.get_job_row(row["id"]) or row
        previous = (fresh.get("task_metadata") or {}).get("archived_path")
        location = job_archive_location(fresh, cad_source)
        project = Path(previous).parent if previous else Path(current_root()) / location.folder
        with archive_write(project):
            if previous:
                path = Path(previous)
                root = path.parent.parent
                if not path.is_absolute() or not root.is_dir():
                    raise RunFolderUnavailableError(root)
            else:
                root = Path(current_root())
                if not root.is_dir():
                    raise RunFolderUnavailableError(root)
                path = root / location.folder / location.run
                jobs.mutate_job_metadata(row["id"], {"archived_path": str(path.absolute())})
            return path


def complete_archive(
    store: Any, row: Mapping[str, Any], cad_source: Mapping[str, Any] | None = None
) -> str | None:
    """Write the server pointer and record a completion's absolute location once."""
    previous = (row.get("task_metadata") or {}).get("archived_path")
    root = Path(previous).parent.parent if previous else archive_root(store)
    if root is None:
        return previous
    if previous and not root.is_dir():
        raise RunFolderUnavailableError(root)
    path = write_project_record(root, row, store, cad_source)
    return previous or str(path.absolute())


def write_project_record(
    root: Path,
    row: Mapping[str, Any],
    store: Any = None,
    cad_source: Mapping[str, Any] | None = None,
) -> Path:
    """Serialize pointer checks and publication with other registry users."""
    from server.workspace.run_moves import archive_write

    with archive_write(root / job_archive_location(row, cad_source).folder):
        if store is not None:
            with store._lock:
                return _write_project_record(root, row, store, cad_source)
        return _write_project_record(root, row, store, cad_source)


def _write_project_record(
    root: Path,
    row: Mapping[str, Any],
    store: Any = None,
    cad_source: Mapping[str, Any] | None = None,
    *, publish: Callable[[Path, dict], None] | None = None,
) -> Path:
    """Create/update our pointer; a foreign pointer never prevents archiving."""
    location = job_archive_location(row, cad_source)
    folder = root / location.folder
    if folder.is_symlink() or (folder.exists() and path_has_reparse_point(folder)):
        raise OSError("Archive project folder must not be a link")
    folder.mkdir(parents=True, exist_ok=True)
    metadata = row.get("task_metadata") or {}
    config = row.get("config_json") or {}
    imported = metadata.get("imported_geometry") or {}
    kind, _, identity = location.key.partition(":")
    pointer = folder / "design.json"
    aliases = {location.key}
    if store is not None and registered_folder(store, location.key) == location.folder:
        with store._lock:
            aliases.update(
                str(r[0])
                for r in store._connect().execute(
                    "SELECT owner_key FROM archive_folders WHERE folder = ? COLLATE NOCASE",
                    (location.folder,),
                )
            )
    if pointer.exists() or pointer.is_symlink():
        try:
            record = (
                json.loads(pointer.read_text(encoding="utf-8"))
                if not pointer.is_symlink()
                else None
            )
        except (OSError, ValueError):
            record = None
        if not isinstance(record, dict) or _record_key(record) not in aliases:
            logger.warning("Leaving another owner's archive design.json in %s", location.folder)
            return folder / location.run
    record = {
        "schemaVersion": 1,
        "folder": location.folder,
        "kind": kind,
        "name": _design_name(config, row.get("label")),
        "lineageId": identity if kind == "lineage" else imported.get("anchor_lineage_id"),
        "designId": config.get("design_id") or imported.get("anchor_design_id"),
        "designUid": identity if kind == "design" else config.get("design_uid"),
        "modelId": identity if kind == "model" else imported.get("model_id"),
    }
    # Reuse the workspace's atomic JSON writer, without its client boundary.
    from server.platform.json_file import write_json_atomic

    (publish or write_json_atomic)(pointer, record)
    return folder / location.run
