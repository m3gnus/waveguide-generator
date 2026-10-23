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
from typing import Any, Mapping, NamedTuple
import unicodedata

from server.platform.acl_repair import path_has_reparse_point
from server.workspace.archive import archive_folder_slug

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
            conn.execute(
                "INSERT OR IGNORE INTO archive_folders VALUES (?, ?, ?)", (key, row[1], _now())
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def archive_root(store: Any) -> Path | None:
    provider = getattr(store, "archive_root", None)
    try:
        return Path(provider()) if callable(provider) else Path(provider) if provider else None
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
    pointer = folder / "design.json"
    try:
        if folder.is_symlink() or path_has_reparse_point(folder) or not folder.is_dir():
            return False
        if pointer.exists() or pointer.is_symlink():
            if pointer.is_symlink():
                return False
            record = json.loads(pointer.read_text(encoding="utf-8"))
            return isinstance(record, dict) and _record_key(record) in {key, *aliases}
        # Only old capture-only folders can be adopted without a pointer.
        # A directory containing unowned runs is occupied, even for a lineage.
        return key.startswith("lineage:") and all(
            p.name in {"cad", ".DS_Store"} for p in folder.iterdir()
        )
    except (OSError, ValueError):
        return False


def claim_in_transaction(
    conn: sqlite3.Connection,
    key: str,
    preferred: object,
    *,
    root: Path | None = None,
    aliases: tuple[str, ...] = (),
    adopt_aliases: tuple[str, ...] = (),
) -> str:
    rows = {
        str(row[0]): str(row[1])
        for row in conn.execute("SELECT owner_key, folder FROM archive_folders")
    }
    if key in rows:
        if key.startswith("lineage:"):
            for alias in aliases:
                conn.execute(
                    "INSERT OR IGNORE INTO archive_folders VALUES (?, ?, ?)",
                    (alias, rows[key], _now()),
                )
        return rows[key]
    adopt = next((rows[a] for a in adopt_aliases if a in rows), None)
    if (
        adopt
        and key.startswith("lineage:")
        and any(
            owner.startswith("lineage:") and owner != key and folder.casefold() == adopt.casefold()
            for owner, folder in rows.items()
        )
    ):
        adopt = None
        adopt_aliases = ()
    allowed = {key, *adopt_aliases}
    base = archive_folder_slug(preferred, "design")
    disk: dict[str, Path] = {}
    if root is not None:
        try:
            disk = {p.name.casefold(): p for p in root.iterdir()}
        except OSError:
            pass  # An unavailable Runs folder is refused by the archive writer.

    def taken(candidate: str) -> bool:
        if any(
            folder.casefold() == candidate.casefold() and owner not in allowed
            for owner, folder in rows.items()
        ):
            return True
        existing = disk.get(candidate.casefold())
        return existing is not None and not _disk_matches(existing, key, set(adopt_aliases))

    if not adopt:
        for existing in disk.values():
            if (
                (existing / "design.json").is_file()
                and not any(
                    folder.casefold() == existing.name.casefold() and owner not in allowed
                    for owner, folder in rows.items()
                )
                and _disk_matches(existing, key, set(adopt_aliases))
            ):
                adopt = existing.name
                break
    folder = adopt or base
    if not adopt:
        count = 2
        while taken(folder):
            folder = f"{base}-{count}"
            count += 1
        # Preserve the spelling of an adopted folder on case-sensitive volumes.
        if folder.casefold() in disk:
            folder = disk[folder.casefold()].name
    conn.execute("INSERT INTO archive_folders VALUES (?, ?, ?)", (key, folder, _now()))
    for alias in aliases:
        # An unregistered legacy folder still owns its exact name. A deliberate
        # new same-name design must not reserve that old file's future alias.
        if alias.startswith("name:") and any(
            existing.name.casefold() != folder.casefold() and _disk_matches(existing, alias, set())
            for existing in disk.values()
        ):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO archive_folders VALUES (?, ?, ?)", (alias, folder, _now())
        )
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
        return archive_folder_slug(preferred, "design")
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


def complete_archive(
    store: Any, row: Mapping[str, Any], cad_source: Mapping[str, Any] | None = None
) -> str | None:
    """Write the server pointer and record a completion's absolute location once."""
    previous = (row.get("task_metadata") or {}).get("archived_path")
    root = Path(previous).parent.parent if previous else archive_root(store)
    if root is None:
        return previous
    path = write_project_record(root, row, store, cad_source)
    return previous or str(path.absolute())


def write_project_record(
    root: Path,
    row: Mapping[str, Any],
    store: Any = None,
    cad_source: Mapping[str, Any] | None = None,
) -> Path:
    """Serialize pointer checks and publication with other registry users."""
    if store is not None:
        with store._lock:
            return _write_project_record(root, row, store, cad_source)
    return _write_project_record(root, row, store, cad_source)


def _write_project_record(
    root: Path,
    row: Mapping[str, Any],
    store: Any = None,
    cad_source: Mapping[str, Any] | None = None,
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
        "designUid": config.get("design_uid"),
        "modelId": imported.get("model_id"),
    }
    # Reuse the workspace's atomic JSON writer, without its client boundary.
    from server.platform.json_file import write_json_atomic

    write_json_atomic(pointer, record)
    return folder / location.run
