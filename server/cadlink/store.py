"""SQLite registry for durable design heads and immutable export snapshots."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

from server.platform.paths import data_paths
from server.platform.private_paths import ensure_private_directory
from server.platform.sqlite import JournalModeStatus, configure_connection
from server.workspace.archive import archive_folder_slug

from .identity import (
    CadLink,
    SaveIdentity,
    mint_id,
    truncated_design_hash,
    utc_now,
)
from .operations import (
    ACCEPTED,
    CANCELLED,
    CANCEL_REQUESTED,
    CLAIMABLE_STATES,
    DIGEST_VERSION,
    FUSION_STAGES,
    INSERT_LINK,
    MUTATING_KINDS,
    NEEDS_USER_INPUT,
    PREPARE_AND_SOLVE,
    PROCESSING,
    RECEIVED,
    RECOVERY_REQUIRED,
    REJECTED,
    REQUEST_RETURN,
    STAGES,
    STAGE_ADAPTER_RECEIVED,
    STAGE_READY,
    STAGE_RECEIVED,
    TERMINAL_STATES,
    UPDATE_LINK,
    canonical_json,
    check_transition,
    normalize_request,
    request_digest,
    require_kind,
    validate_outcome,
)
from .solve_command import legacy_ledger_path


logger = logging.getLogger(__name__)

# 12 adds cad_operations and folds the solve-command JSON ledger into it.
SCHEMA_VERSION = 12

# What this build writes to ``PRAGMA user_version``: the oldest reader format
# the file still satisfies, not the schema above. Every release refuses a
# user_version above the highest it knows (v0.3.2 reads 0-11), and neither an
# automatic rollback nor Return to Stable restores cadlink.db. So a change an
# older release can ignore -- such as schema 12's table, which it never opens --
# keeps this value and is recognised by what the file holds. Raise it only for
# a change an older reader would misread, above every value an older build
# accepts (this one accepts up to HIGHEST_READABLE_FORMAT), and only with a
# restorable snapshot (docs/reference/UPDATE-TRANSACTION-CONTRACT.md, section 6).
STORE_FORMAT_VERSION = 11

# The highest user_version this build opens. Builds from the operation store
# until STORE_FORMAT_VERSION existed wrote 12; nothing else ever did. A
# literal, not SCHEMA_VERSION, so a later additive schema cannot widen it.
HIGHEST_READABLE_FORMAT = 12


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS designs (
      design_id TEXT PRIMARY KEY,
      lineage_id TEXT NOT NULL,
      edit_version INTEGER NOT NULL CHECK (edit_version >= 1),
      design_hash TEXT NOT NULL,
      snapshot_text TEXT NOT NULL,
      filename TEXT NOT NULL,
      branched_from_design_id TEXT,
      branched_from_edit_version INTEGER,
      branched_from_export_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS designs_by_hash ON designs(design_hash)",
    "CREATE INDEX IF NOT EXISTS designs_by_lineage ON designs(lineage_id)",
    """
    CREATE TABLE IF NOT EXISTS exports (
      export_id TEXT PRIMARY KEY,
      bundle_id TEXT NOT NULL UNIQUE,
      design_id TEXT NOT NULL REFERENCES designs(design_id),
      sequence INTEGER NOT NULL,
      parent_export_id TEXT,
      edit_version INTEGER NOT NULL,
      design_hash TEXT NOT NULL,
      geometry_hash TEXT NOT NULL,
      artifact_sha256 TEXT NOT NULL,
      manifest_json TEXT NOT NULL,
      snapshot_text TEXT NOT NULL,
      idempotency_key TEXT NOT NULL UNIQUE,
      bundle_path TEXT,
      created_at TEXT NOT NULL,
      UNIQUE (design_id, sequence)
    )
    """,
    "CREATE INDEX IF NOT EXISTS exports_by_artifact ON exports(artifact_sha256)",
    """
    CREATE TABLE IF NOT EXISTS export_reservations (
      idempotency_key TEXT PRIMARY KEY,
      export_id TEXT NOT NULL UNIQUE,
      bundle_id TEXT NOT NULL UNIQUE,
      design_id TEXT NOT NULL REFERENCES designs(design_id),
      sequence INTEGER NOT NULL,
      parent_export_id TEXT,
      edit_version INTEGER NOT NULL,
      design_hash TEXT NOT NULL,
      snapshot_text TEXT NOT NULL,
      created_at TEXT NOT NULL,
      state TEXT NOT NULL CHECK (state IN ('building', 'retryable')),
      UNIQUE (design_id, sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingests (
      ingest_id TEXT PRIMARY KEY,
      manifest_sha256 TEXT NOT NULL,
      artifact_sha256 TEXT NOT NULL,
      record_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ingests_by_manifest ON ingests(manifest_sha256)",
    "CREATE INDEX IF NOT EXISTS ingests_by_artifact ON ingests(artifact_sha256)",
    # Onshape has no local add-in to hold link state, so WG owns it. One row per
    # (design, account): the document a design was sent to, and the elements
    # inside it that an update must reuse rather than recreate.
    """
    CREATE TABLE IF NOT EXISTS onshape_links (
      design_id TEXT NOT NULL REFERENCES designs(design_id),
      account_id TEXT NOT NULL,
      instance_id TEXT NOT NULL,
      document_id TEXT NOT NULL,
      workspace_id TEXT NOT NULL,
      blob_element_id TEXT NOT NULL,
      part_studio_element_id TEXT,
      variable_studio_element_id TEXT,
      feature_studio_element_id TEXT,
      native_feature_id TEXT,
      datum_feature_studio_element_id TEXT,
      datum_feature_id TEXT,
      build_mode TEXT,
      document_name TEXT NOT NULL,
      is_public INTEGER NOT NULL DEFAULT 0,
      last_export_id TEXT,
      last_sequence INTEGER,
      last_design_hash TEXT,
      last_geometry_hash TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (design_id, account_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS onshape_links_by_document ON onshape_links(document_id)",
    # Fusion bakes the parameter namespace into the datum and enclosure
    # expressions of the linked document, and no update can retarget them, so
    # the namespace has to outlive both a rename and the fork a conflicting
    # save mints.  Lineage is the only key that survives both: a fork keeps its
    # lineage_id, and a filename is not an identity at all.  The bundle folder
    # name rides along for the same reason -- the document remembers the path
    # it was last built from.
    """
    CREATE TABLE IF NOT EXISTS lineage_cad_names (
      lineage_id TEXT PRIMARY KEY,
      parameter_slug TEXT,
      bundle_stem TEXT,
      archive_stem TEXT,
      document_native_id TEXT,
      document_name TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    # Schema 12: one row per CAD operation, the durable half of
    # docs/architecture/CAD-OPERATIONS.md. The vocabulary (kind, state,
    # reason) is validated in server/cadlink/operations.py rather than by
    # CHECK, so later stages can extend it without rebuilding the table; the
    # CHECKs hold structure only. A legacy row predates the contract: its
    # digest, target and inputs stay NULL rather than being reconstructed.
    """
    CREATE TABLE IF NOT EXISTS cad_operations (
      operation_id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,
      request_digest TEXT,
      digest_version INTEGER,
      target_json TEXT,
      inputs_json TEXT,
      attempt_generation INTEGER NOT NULL DEFAULT 0 CHECK (attempt_generation >= 0),
      state TEXT NOT NULL,
      outcome_json TEXT,
      job_id TEXT,
      reason TEXT,
      legacy INTEGER NOT NULL DEFAULT 0 CHECK (legacy IN (0, 1)),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      CHECK (
        legacy = 1 OR (
          request_digest IS NOT NULL AND digest_version IS NOT NULL
          AND target_json IS NOT NULL AND inputs_json IS NOT NULL
        )
      )
    )
    """,
    "CREATE INDEX IF NOT EXISTS cad_operations_by_kind_state "
    "ON cad_operations(kind, state, updated_at)",
    # Setup revisions and preparations. Both are new tables, so the file stays
    # one an older release opens: it neither reads nor writes them.
    """
    CREATE TABLE IF NOT EXISTS cad_setup_revisions (
      revision_id TEXT PRIMARY KEY,
      content_sha256 TEXT NOT NULL UNIQUE,
      setup_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_preparations (
      preparation_id TEXT PRIMARY KEY,
      operation_id TEXT NOT NULL,
      attempt_generation INTEGER NOT NULL,
      snapshot_sha256 TEXT NOT NULL,
      setup_revision_id TEXT,
      ingest_id TEXT NOT NULL,
      report_sha256 TEXT,
      blocking_findings_json TEXT NOT NULL,
      meshing_semantics TEXT,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS cad_preparations_by_operation "
    "ON cad_preparations(operation_id, created_at)",
    # The setup revision each project is prepared with, per source inventory
    # (CAD-OPERATIONS.md, "Project setups").
    """
    CREATE TABLE IF NOT EXISTS cad_project_setups (
      lineage_id TEXT NOT NULL,
      inventory_sha256 TEXT NOT NULL,
      revision_id TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (lineage_id, inventory_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cad_settings (
      key TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    # The solver frame the user confirmed for an unlinked model, per project
    # (or per snapshot when it belongs to none), under the frame requirement it
    # was confirmed for (server/cadlink/solver_frame.py). Additive: an older
    # release never opens it, and no row means unconfirmed -- the table is
    # never filled at open.
    """
    CREATE TABLE IF NOT EXISTS cad_frame_confirmations (
      key TEXT PRIMARY KEY,
      requirement_json TEXT NOT NULL,
      axis TEXT NOT NULL,
      confirmed_at TEXT NOT NULL
    )
    """,
    # The automatic solver-frame suggestion (server/cadlink/frame_infer.py),
    # one per snapshot and algorithm version: a new algorithm recomputes, and
    # nothing here ever confirms a frame. Additive, like the table above.
    """
    CREATE TABLE IF NOT EXISTS cad_frame_suggestions (
      snapshot_sha256 TEXT NOT NULL,
      algorithm TEXT NOT NULL,
      suggestion_json TEXT NOT NULL,
      ingest_id TEXT NOT NULL,
      computed_at TEXT NOT NULL,
      PRIMARY KEY (snapshot_sha256, algorithm)
    )
    """,
)
# Columns later stages added to cad_operations: nullable (or defaulted), so a
# row written before them -- by an earlier build, or by an older release, which
# never touches this table -- stays valid, and reads its stage from its state.
_OPERATION_COLUMNS = (
    ("stage", "TEXT"),
    ("setup_revision_id", "TEXT"),
    ("request_json", "TEXT"),
    ("snapshot_json", "TEXT"),
    ("preparation_id", "TEXT"),
    ("approvals_json", "TEXT"),
    # When a received snapshot was first found unreadable (UTC ISO-8601), so
    # the bound on waiting for it survives a restart.
    ("snapshot_unreadable_since", "TEXT"),
    # A Fusion-bound request claimed over the live protocol: the installation,
    # live session and claim ids, the generation the claim started and the
    # request it took, so a lost claim answer replays after its file is gone
    # (docs/reference/CADLINK-LIVE-PROTOCOL.md, section 7). Never a token.
    ("claim_json", "TEXT"),
    # The solver frame axis a user's Solve showed (an unlinked snapshot): every
    # later attempt, automatic or not, is held to it until a press names another.
    ("frame_axis", "TEXT"),
)
# The kinds WG asks Fusion to run (``fusion_outcomes.FUSION_KINDS``).
_FUSION_KINDS = (INSERT_LINK, REQUEST_RETURN, UPDATE_LINK)

# What the live Fusion request methods found; the route names the answer.
FUSION_UNKNOWN = "unknown"
FUSION_CLAIMED_ELSEWHERE = "claimed_elsewhere"
FUSION_STALE_ATTEMPT = "stale_attempt"
FUSION_STAGE_OUT_OF_ORDER = "stage_out_of_order"
FUSION_OUTCOME_CONFLICT = "outcome_conflict"
FUSION_RECORDED = "recorded"
FUSION_ALREADY_RECORDED = "already_recorded"


def _frame_confirmation(row: Mapping[str, Any]) -> dict[str, Any]:
    frame = row["frame_json"] if "frame_json" in row.keys() else None
    return {
        "key": row["key"],
        "requirement": json.loads(row["requirement_json"]),
        "axis": row["axis"],
        "confirmed_at": row["confirmed_at"],
        # The complete transform and its up provenance; None for a row
        # confirmed before they were recorded.
        "frame": json.loads(frame) if frame else None,
    }


class StaleAttempt(RuntimeError):
    """An attempt tried to commit after it lost its operation (the fence)."""


class BindingConflict(ValueError):
    """An operation is already bound to a different request."""


def _archive_stem_candidate(value: object) -> str:
    """Return the server-owned portable folder spelling for a CAD lineage."""

    return archive_folder_slug(value, "design")


def _suffixed_archive_stem(base: str, lineage_id: str, used: set[str]) -> str:
    """Disambiguate a portable/case-fold collision from durable identity."""

    digest = hashlib.sha256(lineage_id.encode("utf-8")).hexdigest()
    for length in range(12, len(digest) + 1, 4):
        candidate = f"{base}-{digest[:length]}"
        if candidate.casefold() not in used:
            return candidate
    # A natural filename could theoretically contain the entire digest. Keep
    # the lineage-derived suffix and add a deterministic final discriminator.
    discriminator = 2
    while f"{base}-{digest}-{discriminator}".casefold() in used:
        discriminator += 1
    return f"{base}-{digest}-{discriminator}"


def _migrate_archive_stems(conn: sqlite3.Connection) -> None:
    """Normalize legacy names and resolve their portable collisions stably."""

    conn.execute(
        "UPDATE lineage_cad_names SET archive_stem = NULL "
        "WHERE archive_stem IS NOT NULL AND TRIM(archive_stem) = ''"
    )
    rows = conn.execute(
        "SELECT lineage_id, archive_stem FROM lineage_cad_names "
        "WHERE archive_stem IS NOT NULL AND TRIM(archive_stem) != ''"
    ).fetchall()
    groups: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        lineage_id = str(row["lineage_id"])
        base = _archive_stem_candidate(row["archive_stem"])
        groups.setdefault(base.casefold(), []).append((lineage_id, base))

    allocated: dict[str, str] = {}
    used: set[str] = set()
    collisions: list[tuple[str, str]] = []
    # The lexicographically first lineage keeps each readable base. Sorting
    # makes the migration independent of SQLite row order and repeatable from
    # the same v9 database image.
    for portable_key in sorted(groups):
        members = sorted(groups[portable_key])
        winner_lineage, winner_base = members[0]
        allocated[winner_lineage] = winner_base
        used.add(winner_base.casefold())
        collisions.extend((lineage_id, base) for lineage_id, base in members[1:])
    for lineage_id, base in sorted(collisions, key=lambda item: (item[1].casefold(), item[0])):
        candidate = _suffixed_archive_stem(base, lineage_id, used)
        allocated[lineage_id] = candidate
        used.add(candidate.casefold())

    for lineage_id, archive_stem in allocated.items():
        conn.execute(
            "UPDATE lineage_cad_names SET archive_stem = ? WHERE lineage_id = ?",
            (archive_stem, lineage_id),
        )


def _allocate_archive_stem(
    conn: sqlite3.Connection, lineage_id: str, preferred: object
) -> str:
    base = _archive_stem_candidate(preferred)
    used = {
        str(row["archive_stem"]).casefold()
        for row in conn.execute(
            "SELECT archive_stem FROM lineage_cad_names "
            "WHERE lineage_id != ? AND archive_stem IS NOT NULL AND archive_stem != ''",
            (lineage_id,),
        )
    }
    if base.casefold() not in used:
        return base
    return _suffixed_archive_stem(base, lineage_id, used)

_EXPORT_BUILD_LOCKS_GUARD = threading.Lock()
_EXPORT_BUILD_LOCKS: dict[tuple[str, str], threading.Lock] = {}

_LEGACY_SOLVE_STATES = {"accepted": ACCEPTED, "refused": REJECTED}


def _require_operation_id(value: object) -> str:
    # Opaque, producer-chosen and stored as TEXT: any non-empty string, so
    # every command id earlier versions accepted still has a home.
    if not isinstance(value, str) or not value:
        raise ValueError("operation_id must be a non-empty string")
    return value


def _require_generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("attempt generation must be a non-negative integer")
    return value


def _read_legacy_solve_ledger(path: Path) -> Mapping[str, Any] | None:
    """The commands in an earlier version's JSON ledger, or None to leave it."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        # Held open elsewhere, typically on Windows. The file stays the only
        # copy until an import commits, so waiting for the next open is safe.
        logger.warning(
            "Could not read %s; its import waits for the next start: %s", path.name, exc
        )
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        logger.warning(
            "%s is not valid JSON; it is left in place and nothing is imported", path.name
        )
        return None
    commands = payload.get("commands") if isinstance(payload, Mapping) else None
    if not isinstance(commands, Mapping) or payload.get("schemaVersion", 1) != 1:
        logger.warning("%s is not a recognised ledger; it is left in place", path.name)
        return None
    return commands


def _legacy_solve_row(
    command_id: object, entry: object, now: str
) -> tuple[object, ...] | None:
    """One ledger entry as a legacy operation row, or None to skip it.

    Only what the ledger recorded is kept. It never recorded a request digest,
    target or inputs, so those stay NULL rather than being rebuilt from
    today's data.
    """

    if not isinstance(command_id, str) or not command_id or not isinstance(entry, Mapping):
        return None
    raw_state = entry.get("state")
    state = _LEGACY_SOLVE_STATES.get(raw_state) if isinstance(raw_state, str) else None
    if state is None:
        return None
    job_id = entry.get("jobId")
    reason = entry.get("reason")
    at = entry.get("at")
    recorded_at = at if isinstance(at, str) and at else now
    return (
        command_id,
        PREPARE_AND_SOLVE,
        state,
        canonical_json({"message": reason}) if isinstance(reason, str) and reason else None,
        job_id if isinstance(job_id, str) and job_id else None,
        recorded_at,
        recorded_at,
    )


def _import_legacy_solve_ledger(conn: sqlite3.Connection, path: Path | None) -> bool:
    """Fold the JSON ledger into cad_operations inside the caller's transaction.

    Returns True when the file was read and should be retired once that
    transaction commits. A row the store already holds always wins, so a
    rerun -- after an interruption, or of a ledger an older build wrote since
    -- never duplicates or overwrites anything.
    """

    if path is None:
        return False
    commands = _read_legacy_solve_ledger(path)
    if commands is None:
        return False
    now = utc_now()
    imported = skipped = 0
    for command_id, entry in commands.items():
        row = _legacy_solve_row(command_id, entry, now)
        if row is None:
            skipped += 1
            continue
        cursor = conn.execute(
            """
            INSERT INTO cad_operations (
              operation_id, kind, state, outcome_json, job_id,
              created_at, updated_at, attempt_generation, legacy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
            ON CONFLICT (operation_id) DO NOTHING
            """,
            row,
        )
        imported += cursor.rowcount
    if skipped:
        logger.warning("Skipped %d malformed entries in %s", skipped, path.name)
    logger.info("Imported %d solve-command outcomes from %s", imported, path.name)
    return True


def _retire_legacy_solve_ledger(path: Path) -> None:
    """Rename an imported ledger. Runs only after its import committed."""

    target = path.with_name(path.name + ".migrated")
    if target.exists():
        # An earlier import already left its copy; keep it.
        stamp = utc_now().replace("-", "").replace(":", "")
        target = path.with_name(f"{path.name}.migrated-{stamp}")
    try:
        os.replace(path, target)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(
            "Imported %s but could not rename it; the next start retries: %s",
            path.name,
            exc,
        )


class CadLinkStore:
    """Thread-safe, transaction-per-write CAD-link registry."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        legacy_solve_ledger: str | Path | None = None,
        data_root: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root) if data_root is not None else None
        # An earlier version's JSON solve-command ledger, imported on open.
        # Only ``for_data_dir`` knows where one lives.
        self.legacy_solve_ledger = (
            Path(legacy_solve_ledger) if legacy_solve_ledger is not None else None
        )
        self._lock = threading.RLock()
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._initialized = False
        # Set on the first connection; the mode SQLite granted, not the one asked for.
        self.journal_mode_status: JournalModeStatus | None = None

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> CadLinkStore:
        paths = data_paths(data_dir)
        return cls(
            paths.db / "cadlink.db",
            legacy_solve_ledger=legacy_ledger_path(paths.root),
            data_root=paths.root,
        )

    def initialize(self) -> None:
        if self._initialized:
            return
        if str(self.db_path) != ":memory:":
            ensure_private_directory(
                self.db_path.parent, parents=True, data_root=self.data_root
            )
        with self._lock, self._transaction() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version < 0 or version > HIGHEST_READABLE_FORMAT:
                raise RuntimeError(f"unsupported cadlink.db schema version {version}")
            for statement in _SCHEMA:
                conn.execute(statement)
            export_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(exports)")
            }
            if "bundle_path" not in export_columns:
                conn.execute("ALTER TABLE exports ADD COLUMN bundle_path TEXT")
            onshape_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(onshape_links)")
            }
            if "instance_id" not in onshape_columns:
                conn.execute("ALTER TABLE onshape_links ADD COLUMN instance_id TEXT")
            for column in (
                "feature_studio_element_id",
                "native_feature_id",
                "datum_feature_studio_element_id",
                "datum_feature_id",
                "build_mode",
            ):
                if column not in onshape_columns:
                    conn.execute(f"ALTER TABLE onshape_links ADD COLUMN {column} TEXT")
            # Versions through 7 addressed an Onshape link by design/account and
            # therefore had no placement-like join key. Give each legacy row a
            # durable opaque id exactly once; deriving it later from a document
            # or element id would turn mutable/foreign addresses into identity.
            missing_instances = conn.execute(
                "SELECT design_id, account_id FROM onshape_links "
                "WHERE instance_id IS NULL OR instance_id = ''"
            ).fetchall()
            for row in missing_instances:
                conn.execute(
                    "UPDATE onshape_links SET instance_id = ? "
                    "WHERE design_id = ? AND account_id = ?",
                    (mint_id("wgo_"), row["design_id"], row["account_id"]),
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS onshape_links_by_instance "
                "ON onshape_links(instance_id)"
            )
            lineage_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(lineage_cad_names)")
            }
            if "archive_stem" not in lineage_columns:
                conn.execute("ALTER TABLE lineage_cad_names ADD COLUMN archive_stem TEXT")
            for column in ("document_native_id", "document_name"):
                if column not in lineage_columns:
                    conn.execute(
                        f"ALTER TABLE lineage_cad_names ADD COLUMN {column} TEXT"
                    )
            # Created here rather than in _SCHEMA because the column it indexes
            # only exists after the migration above has run on a v9 database.
            #
            # A document authored in CAD and sent to WG has no design to anchor
            # to, so the Fusion document itself is the project. Its identity is
            # the native id -- a Fusion *lineage* urn, which survives a rename
            # exactly as WG's own lineage does -- never the document name.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS lineage_cad_names_by_document "
                "ON lineage_cad_names(document_native_id) "
                "WHERE document_native_id IS NOT NULL"
            )
            if version < 11:
                _migrate_archive_stems(conn)
            # archive_stem is an ASCII portable folder key from schema 11 on.
            # NOCASE therefore matches the case-insensitive filesystems this
            # key must survive, while BEGIN IMMEDIATE serializes allocation.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS lineage_cad_names_by_archive_stem "
                "ON lineage_cad_names(archive_stem COLLATE NOCASE) "
                "WHERE archive_stem IS NOT NULL AND archive_stem != ''"
            )
            # Schema 11 also adds export_reservations (created by _SCHEMA
            # above): exports are reserved in a short transaction and finalised
            # after the bundle is built outside the registry lock.
            #
            # Schema 12 adds cad_operations (also created by _SCHEMA) and
            # folds the solve-command JSON ledger into it in this same
            # transaction, so an interruption rolls both back and the next
            # open reruns both. The file is renamed only after the commit.
            # The table is additive, so the file keeps the format an older
            # release reads (STORE_FORMAT_VERSION).
            confirmation_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(cad_frame_confirmations)")
            }
            if "frame_json" not in confirmation_columns:
                conn.execute("ALTER TABLE cad_frame_confirmations ADD COLUMN frame_json TEXT")
            operation_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(cad_operations)")
            }
            for column, declaration in _OPERATION_COLUMNS:
                if column not in operation_columns:
                    conn.execute(f"ALTER TABLE cad_operations ADD COLUMN {column} {declaration}")
            imported_ledger = _import_legacy_solve_ledger(conn, self.legacy_solve_ledger)
            conn.execute(f"PRAGMA user_version = {STORE_FORMAT_VERSION}")
        self._initialized = True
        if imported_ledger and self.legacy_solve_ledger is not None:
            _retire_legacy_solve_ledger(self.legacy_solve_ledger)

    # -- CAD operations: docs/architecture/CAD-OPERATIONS.md -------------------
    #
    # Each method is one short transaction. None is held open across Fusion or
    # mesher work.

    def accept_operation(
        self,
        operation_id: str,
        kind: str,
        digest: str,
        target: Mapping[str, Any] | None,
        inputs: Mapping[str, Any] | None = None,
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Persist a delivered request, or recover the operation it repeats.

        Returns the stored row and ``'created'``, ``'recovered'`` or
        ``'conflict'``. A conflict -- the same id with a different digest or
        kind -- leaves the stored operation untouched; the caller rejects the
        delivery and must not answer it with that operation's result. A legacy
        row has no digest to compare, so a delivery of its kind recovers it by
        id alone, and the row is never rewritten.
        """

        _require_operation_id(operation_id)
        normalized_target, normalized_inputs = normalize_request(kind, target, inputs)
        destination = normalized_target.get("destination")
        if (
            kind == INSERT_LINK
            and isinstance(destination, Mapping)
            and destination.get("kind") == "new_document"
            and destination.get("value") != operation_id
        ):
            raise ValueError(
                "insert_link new_document destination value must equal its operation id"
            )
        if digest != request_digest(kind, normalized_target, normalized_inputs):
            raise ValueError(
                "request digest does not match the kind, target and inputs it names"
            )
        self.initialize()
        now = utc_now()
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO cad_operations (
                      operation_id, kind, request_digest, digest_version,
                      target_json, inputs_json, attempt_generation, state,
                      legacy, created_at, updated_at, stage, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        kind,
                        digest,
                        DIGEST_VERSION,
                        canonical_json(normalized_target),
                        canonical_json(normalized_inputs),
                        RECEIVED,
                        now,
                        now,
                        STAGE_RECEIVED,
                        canonical_json(dict(snapshot)) if snapshot is not None else None,
                    ),
                )
                row = conn.execute(
                    "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                result = "created"
            elif str(row["kind"]) != kind:
                result = "conflict"
            elif int(row["legacy"]) == 1 or row["request_digest"] == digest:
                result = "recovered"
                if snapshot is not None and row["snapshot_json"] is None:
                    conn.execute(
                        "UPDATE cad_operations SET snapshot_json = ?, updated_at = ? "
                        "WHERE operation_id = ? AND snapshot_json IS NULL",
                        (canonical_json(dict(snapshot)), now, operation_id),
                    )
                    row = conn.execute(
                        "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone()
            else:
                result = "conflict"
        return dict(row), result

    def claim(self, operation_id: str, expected_generation: int) -> int | None:
        """Start an attempt: a conditional update on the current generation.

        Returns the new generation, or None when another consumer claimed
        first, the generation is stale, or the operation cannot be claimed
        (terminal, ``recovery_required`` or unknown).
        """

        generation = _require_generation(expected_generation)
        self.initialize()
        claimable = sorted(CLAIMABLE_STATES)
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE cad_operations SET attempt_generation = attempt_generation + 1, "
                "state = ?, updated_at = ? "
                "WHERE operation_id = ? AND attempt_generation = ? "
                f"AND state IN ({', '.join('?' for _ in claimable)})",
                (PROCESSING, utc_now(), operation_id, generation, *claimable),
            )
        return generation + 1 if cursor.rowcount == 1 else None

    def record_outcome(
        self,
        operation_id: str,
        generation: int,
        state: str,
        *,
        job_id: str | None = None,
        reason: str | None = None,
        outcome: Mapping[str, Any] | None = None,
        stage: str | None = None,
        release_binding: bool = False,
    ) -> dict[str, Any] | None:
        """Record what the attempt holding ``generation`` found.

        Conditional on that generation and on the operation not being
        terminal: an obsolete attempt, or a second terminal outcome, changes
        nothing and gets None. A job id, once attached, is never cleared.
        ``stage``, when given, is where the attempt got to.

        A dismissal stands: from ``cancel_requested`` the outcome recorded is
        ``cancelled``, whatever the fenced attempt found, except ``accepted``
        with a job -- the job exists, and the user cancels it in the jobs list.
        ``release_binding`` unbinds the request in the same transaction, for a
        submission that created nothing; a request with a job stays bound.
        """

        if stage is not None and stage not in STAGES:
            raise ValueError(f"unknown CAD operation stage {stage!r}")

        attempt = _require_generation(generation)
        outcome_json = validate_outcome(operation_id, state, reason=reason, outcome=outcome)
        if job_id is not None and (not isinstance(job_id, str) or not job_id):
            raise ValueError("job_id must be a non-empty string")
        self.initialize()
        reconciled = outcome is not None and outcome.get("reconciled") is True
        with self._lock, self._transaction() as conn:
            current = conn.execute(
                "SELECT kind, state, attempt_generation, job_id FROM cad_operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if (
                current is None
                or int(current["attempt_generation"]) != attempt
                or current["state"] in TERMINAL_STATES
            ):
                return None
            if current["state"] == CANCEL_REQUESTED and not (
                state == ACCEPTED and (job_id or current["job_id"])
            ):
                state, reason, outcome_json, stage = CANCELLED, None, None, None
            # The read and the write share this IMMEDIATE transaction, so no
            # other writer can move the row between the check and the update.
            check_transition(
                str(current["kind"]), str(current["state"]), state, reconciled=reconciled
            )
            conn.execute(
                "UPDATE cad_operations SET state = ?, job_id = COALESCE(?, job_id), "
                "reason = ?, outcome_json = ?, stage = COALESCE(?, stage), updated_at = ? "
                "WHERE operation_id = ?",
                (state, job_id, reason, outcome_json, stage, utc_now(), operation_id),
            )
            if release_binding and state not in TERMINAL_STATES:
                conn.execute(
                    "UPDATE cad_operations SET setup_revision_id = NULL, request_json = NULL "
                    "WHERE operation_id = ? AND job_id IS NULL",
                    (operation_id,),
                )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row)

    # -- Fusion-bound requests claimed live: CADLINK-LIVE-PROTOCOL.md, section 7 --
    #
    # The request file is the mutual-exclusion token between the live claim and
    # the add-in's file claim; these methods are the store half, each one short
    # transaction with no file I/O inside.

    def claim_fusion_request(
        self, operation_id: str, expected_generation: int, claim: Mapping[str, Any]
    ) -> int | None:
        """Claim a received Fusion request for a live claim whose file WG already hid.

        Conditional on ``expected_generation`` and on the operation still being
        ``received`` and unclaimed. Records ``claim`` (plus the new generation)
        and the ``adapter-received`` stage in the same update. Returns the new
        generation, or None when nothing changed.
        """

        generation = _require_generation(expected_generation)
        record = {**dict(claim), "attemptGeneration": generation + 1}
        self.initialize()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE cad_operations SET attempt_generation = attempt_generation + 1, "
                "state = ?, stage = ?, claim_json = ?, updated_at = ? "
                "WHERE operation_id = ? AND attempt_generation = ? AND state = ? "
                f"AND claim_json IS NULL AND kind IN ({', '.join('?' for _ in _FUSION_KINDS)})",
                (
                    PROCESSING, STAGE_ADAPTER_RECEIVED, canonical_json(record), utc_now(),
                    operation_id, generation, RECEIVED, *_FUSION_KINDS,
                ),
            )
        return generation + 1 if cursor.rowcount == 1 else None

    @staticmethod
    def _fusion_fence(
        row: sqlite3.Row | None, generation: int, installation_id: str
    ) -> str | None:
        """Why a live attempt may not write this row, checked in its transaction."""

        if row is None or row["kind"] not in _FUSION_KINDS:
            return FUSION_UNKNOWN
        try:
            claim = json.loads(row["claim_json"]) if row["claim_json"] else None
        except (TypeError, ValueError):
            claim = None
        if not isinstance(claim, Mapping) or claim.get("installationId") != installation_id:
            return FUSION_CLAIMED_ELSEWHERE
        if int(row["attempt_generation"]) != generation:
            return FUSION_STALE_ATTEMPT
        return None

    def advance_fusion_stage(
        self, operation_id: str, generation: int, stage: str, installation_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Record how far Fusion got with a live-claimed request.

        Fenced on the claiming installation and the attempt generation, and
        only while the attempt runs (``processing``, or ``cancel_requested``).
        A stage moves exactly one step forward; the same stage again changes
        nothing. Returns a ``FUSION_*`` status and the row.
        """

        attempt = _require_generation(generation)
        if stage not in FUSION_STAGES:
            raise ValueError(f"unknown Fusion request stage {stage!r}")
        self.initialize()
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            refused = self._fusion_fence(row, attempt, installation_id)
            if refused is not None:
                return refused, self._row(row)
            if row["state"] not in (PROCESSING, CANCEL_REQUESTED):
                return FUSION_STALE_ATTEMPT, self._row(row)
            current = row["stage"]
            if current == stage:
                return FUSION_ALREADY_RECORDED, self._row(row)
            position = FUSION_STAGES.index(current) if current in FUSION_STAGES else -1
            if FUSION_STAGES.index(stage) != position + 1:
                return FUSION_STAGE_OUT_OF_ORDER, self._row(row)
            conn.execute(
                "UPDATE cad_operations SET stage = ?, updated_at = ? WHERE operation_id = ?",
                (stage, utc_now(), operation_id),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return FUSION_RECORDED, self._row(row)

    def complete_fusion_request(
        self,
        operation_id: str,
        generation: int,
        installation_id: str,
        decide: Callable[[Mapping[str, Any]], tuple[str, str | None, Mapping[str, Any] | None]],
    ) -> tuple[str, dict[str, Any] | None]:
        """Record the outcome a live-claimed request's attempt reports.

        Fenced as :meth:`advance_fusion_stage`. ``decide(row)`` maps the
        reported outcome to ``(state, reason, outcome)`` from the row read in
        this transaction; a ``ValueError`` from it changes nothing. The same
        outcome again is ``already_recorded``, a different one
        ``outcome_conflict``. A dismissal stands: from ``cancel_requested`` the
        outcome is ``cancelled``, and any completion of that attempt afterwards
        is already recorded. From ``recovery_required`` only a reconciled
        ``accepted`` settles. Returns a ``FUSION_*`` status and the row.
        """

        attempt = _require_generation(generation)
        self.initialize()
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            refused = self._fusion_fence(row, attempt, installation_id)
            if refused is not None:
                return refused, self._row(row)
            current = str(row["state"])
            if current == RECEIVED:
                return FUSION_STALE_ATTEMPT, self._row(row)
            state, reason, outcome = decide(dict(row))
            outcome_json = validate_outcome(operation_id, state, reason=reason, outcome=outcome)
            reconciled = outcome is not None and outcome.get("reconciled") is True
            if current in TERMINAL_STATES:
                same = (current, row["reason"]) == (state, reason) or (
                    current == CANCELLED and row["reason"] is None
                )
                return (
                    FUSION_ALREADY_RECORDED if same else FUSION_OUTCOME_CONFLICT
                ), self._row(row)
            if current == RECOVERY_REQUIRED:
                if state == RECOVERY_REQUIRED:
                    return FUSION_ALREADY_RECORDED, self._row(row)
                if not (state == ACCEPTED and reconciled):
                    return FUSION_OUTCOME_CONFLICT, self._row(row)
            if current == CANCEL_REQUESTED:
                state, reason, outcome_json = CANCELLED, None, None
            check_transition(str(row["kind"]), current, state, reconciled=reconciled)
            conn.execute(
                "UPDATE cad_operations SET state = ?, reason = ?, outcome_json = ?, updated_at = ? "
                "WHERE operation_id = ?",
                (state, reason, outcome_json, utc_now(), operation_id),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return FUSION_RECORDED, self._row(row)

    def advance_operation(
        self,
        operation_id: str,
        generation: int,
        *,
        stage: str | None = None,
        snapshot: Mapping[str, Any] | None = None,
        preparation_id: str | None = None,
        setup_revision_id: str | None = None,
        frame_axis: str | None = None,
    ) -> dict[str, Any] | None:
        """Move the attempt holding ``generation`` on, recording what it made.

        The fence: conditional on that generation being current and the
        operation still ``processing``. A takeover, a cancellation or an
        outcome changes one of those, and the obsolete attempt then gets None
        and must stop without committing anything.

        ``setup_revision_id`` is the setup the attempt selected, recorded
        before it meshes so a later revision-less retry still has it. A bound
        request is never rewritten: its revision stays the one it was bound with.

        ``frame_axis`` is the solver frame axis the user's Solve showed; it
        replaces the one held, and omitting it keeps that one.
        """

        attempt = _require_generation(generation)
        if stage is not None and stage not in STAGES:
            raise ValueError(f"unknown CAD operation stage {stage!r}")
        self.initialize()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE cad_operations SET stage = COALESCE(?, stage), "
                "snapshot_json = COALESCE(?, snapshot_json), "
                "preparation_id = COALESCE(?, preparation_id), "
                "setup_revision_id = CASE WHEN request_json IS NULL "
                "THEN COALESCE(?, setup_revision_id) ELSE setup_revision_id END, "
                "frame_axis = COALESCE(?, frame_axis), updated_at = ? "
                "WHERE operation_id = ? AND attempt_generation = ? AND state = ?",
                (
                    stage,
                    canonical_json(dict(snapshot)) if snapshot is not None else None,
                    preparation_id,
                    setup_revision_id,
                    frame_axis,
                    utc_now(),
                    operation_id,
                    attempt,
                    PROCESSING,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row)

    def admit_frame_axis(self, operation_id: str, frame_axis: str) -> bool:
        """Keep the solver frame axis a user's Solve named, as it is admitted.

        Written before anything can return early -- a restart approved while
        the press is reconciled leaves the operation ``received`` -- so every
        later attempt, the delivery loop's included, is held to it. Not an
        attempt's write: no generation fence, only an unfinished operation.
        ``updated_at`` is left alone, so a client's ordering of the row's
        states does not move. False when the operation is finished or unknown.
        """

        self.initialize()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE cad_operations SET frame_axis = ? "
                f"WHERE operation_id = ? AND state NOT IN ({', '.join('?' for _ in TERMINAL_STATES)})",
                (frame_axis, operation_id, *sorted(TERMINAL_STATES)),
            )
            return cursor.rowcount == 1

    def attempt_is_current(
        self, conn: sqlite3.Connection, operation_id: str, generation: int
    ) -> bool:
        """The fence, for use inside another write's transaction on this store."""

        row = conn.execute(
            "SELECT state, attempt_generation FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return (
            row is not None
            and row["state"] == PROCESSING
            and int(row["attempt_generation"]) == int(generation)
        )

    def record_snapshot(
        self, operation_id: str, snapshot: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Record the retained snapshot of an unfinished operation that has none yet.

        Receive-time retention. It is not fenced: the copy is content-addressed
        and verified against the operation's own manifest hash, so every
        writer records the same thing, and the first one stands.
        """

        self.initialize()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE cad_operations SET snapshot_json = ?, updated_at = ? "
                "WHERE operation_id = ? AND snapshot_json IS NULL "
                f"AND state NOT IN ({', '.join('?' for _ in TERMINAL_STATES)})",
                (
                    canonical_json(dict(snapshot)),
                    utc_now(),
                    operation_id,
                    *sorted(TERMINAL_STATES),
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row)

    def note_snapshot_unreadable(self, operation_id: str, at: str) -> str | None:
        """Record when an unfinished operation's snapshot was first unreadable.

        The first time stands. Returns the time recorded, or None when the
        operation is unknown or finished.
        """

        self.initialize()
        with self._lock, self._transaction() as conn:
            conn.execute(
                "UPDATE cad_operations SET snapshot_unreadable_since = ? "
                "WHERE operation_id = ? AND snapshot_unreadable_since IS NULL "
                f"AND state NOT IN ({', '.join('?' for _ in TERMINAL_STATES)})",
                (at, operation_id, *sorted(TERMINAL_STATES)),
            )
            row = conn.execute(
                "SELECT state, snapshot_unreadable_since FROM cad_operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None or row["state"] in TERMINAL_STATES:
            return None
        return row["snapshot_unreadable_since"]

    def operation_page(
        self,
        *,
        kind: str,
        states: Iterable[str],
        after_rowid: int = 0,
        limit: int = 100,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Operations of a kind in acceptance order after a cursor, with their cursor.

        For a reader that must reach every row however many stay in a state.
        """

        self.initialize()
        wanted = sorted(set(states))
        if not wanted:
            return []
        bounded_limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._connect().execute(
                "SELECT rowid AS page_rowid, rowid AS accepted_seq, * FROM cad_operations "
                f"WHERE kind = ? AND state IN ({', '.join('?' for _ in wanted)}) "
                "AND rowid > ? ORDER BY rowid ASC LIMIT ?",
                (kind, *wanted, int(after_rowid), bounded_limit),
            ).fetchall()
        page: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            page.append((int(item.pop("page_rowid")), item))
        return page

    def bind_request(
        self,
        operation_id: str,
        generation: int,
        *,
        setup_revision_id: str,
        request_json: str,
    ) -> dict[str, Any] | None:
        """Bind the exact solve request, immediately before it is submitted.

        This is the binding point: from here the request is immutable, and a
        recovery resubmits exactly it. Binding again with the same request is
        a no-op; a different one is a ``BindingConflict`` -- a changed setup
        is a new operation. Fenced like ``advance_operation``.
        """

        attempt = _require_generation(generation)
        self.initialize()
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if (
                row is None
                or row["state"] != PROCESSING
                or int(row["attempt_generation"]) != attempt
            ):
                return None
            if row["request_json"] is not None:
                if row["request_json"] != request_json:
                    raise BindingConflict(
                        f"operation {operation_id!r} is already bound to another request"
                    )
                return self._row(row)
            conn.execute(
                "UPDATE cad_operations SET setup_revision_id = ?, request_json = ?, "
                "updated_at = ? WHERE operation_id = ?",
                (setup_revision_id, request_json, utc_now(), operation_id),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row)

    def add_approvals(
        self,
        operation_id: str,
        preparation_id: str,
        finding_ids: Iterable[str],
        *,
        generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Approve blocking findings of one of this operation's preparations.

        Merged into the stored approvals in one transaction, so two approvals
        never drop each other. Each is bound to its preparation, and only a
        finding that preparation reported as blocking can be approved
        (``ValueError`` otherwise). An attempt passes its ``generation`` and is
        fenced by it; the user's own approval is refused (None) only on a
        finished operation.
        """

        wanted = list(dict.fromkeys(str(item) for item in finding_ids))
        if not preparation_id or not all(wanted):
            raise ValueError("an approval names a preparation and its finding ids")
        self.initialize()
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None or row["state"] in TERMINAL_STATES:
                return None
            if generation is not None and not self.attempt_is_current(
                conn, operation_id, _require_generation(generation)
            ):
                return None
            preparation = conn.execute(
                "SELECT blocking_findings_json FROM cad_preparations "
                "WHERE preparation_id = ? AND operation_id = ?",
                (preparation_id, operation_id),
            ).fetchone()
            if preparation is None:
                raise ValueError(
                    f"preparation {preparation_id!r} is not one of this operation's"
                )
            unknown = sorted(set(wanted) - set(json.loads(preparation["blocking_findings_json"])))
            if unknown:
                raise ValueError(
                    f"preparation {preparation_id!r} reported no blocking finding {unknown[0]!r}"
                )
            approvals = json.loads(row["approvals_json"]) if row["approvals_json"] else []
            held = {(item["preparation_id"], item["finding_id"]) for item in approvals}
            approvals += [
                {"preparation_id": preparation_id, "finding_id": finding}
                for finding in wanted
                if (preparation_id, finding) not in held
            ]
            conn.execute(
                "UPDATE cad_operations SET approvals_json = ?, updated_at = ? "
                "WHERE operation_id = ?",
                (canonical_json(approvals), utc_now(), operation_id),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row)

    def request_cancel(self, operation_id: str) -> dict[str, Any] | None:
        """Dismiss an operation: at once when idle, at the attempt's next step when running.

        An operation no attempt is working on becomes ``cancelled``. One an
        attempt holds becomes ``cancel_requested``, which fences that attempt;
        it then records ``cancelled``. A terminal operation is returned as it
        is, and so is a CAD mutation under way: it may need recovery from the
        document rather than cancellation.
        """

        self.initialize()
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return None
            state = str(row["state"])
            if state in TERMINAL_STATES or state == CANCEL_REQUESTED:
                return self._row(row)
            if state == PROCESSING and row["kind"] in MUTATING_KINDS:
                return self._row(row)
            new_state = CANCEL_REQUESTED if state == PROCESSING else CANCELLED
            conn.execute(
                "UPDATE cad_operations SET state = ?, updated_at = ? WHERE operation_id = ?",
                (new_state, utc_now(), operation_id),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row)

    def requeue_operation(
        self, operation_id: str, generation: int, *, reason: str
    ) -> dict[str, Any] | None:
        """Put an operation that waits for ``reason`` back to ``received``.

        Conditional on the generation the caller read and on the operation
        still waiting for exactly that reason, so a user who acted on it
        meanwhile keeps what they started. The generation is kept, so the
        delivery loop starts it as an untouched operation; its snapshot,
        preparation and approvals are kept, so that attempt resumes them.
        Returns the row, or None when nothing changed.
        """

        attempt = _require_generation(generation)
        self.initialize()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE cad_operations SET state = ?, stage = ?, reason = NULL, "
                "outcome_json = NULL, updated_at = ? "
                "WHERE operation_id = ? AND attempt_generation = ? AND state = ? AND reason = ?",
                (
                    RECEIVED, STAGE_RECEIVED, utc_now(), operation_id, attempt,
                    NEEDS_USER_INPUT, reason,
                ),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row) if cursor.rowcount == 1 else None

    def settle_cancel(self, operation_id: str, generation: int) -> dict[str, Any] | None:
        """The attempt a dismissal fenced records the dismissal."""

        attempt = _require_generation(generation)
        self.initialize()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE cad_operations SET state = ?, updated_at = ? "
                "WHERE operation_id = ? AND attempt_generation = ? AND state = ?",
                (CANCELLED, utc_now(), operation_id, attempt, CANCEL_REQUESTED),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row) if cursor.rowcount == 1 else None

    def create_setup_revision(self, setup_json: str, content_sha256: str) -> dict[str, Any]:
        """Store an immutable setup revision; identical content is one revision."""

        self.initialize()
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM cad_setup_revisions WHERE content_sha256 = ?",
                (content_sha256,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO cad_setup_revisions (revision_id, content_sha256, "
                    "setup_json, created_at) VALUES (?, ?, ?, ?)",
                    (mint_id("wgs_"), content_sha256, setup_json, utc_now()),
                )
                row = conn.execute(
                    "SELECT * FROM cad_setup_revisions WHERE content_sha256 = ?",
                    (content_sha256,),
                ).fetchone()
        return dict(row)

    def get_setup_revision(self, revision_id: str) -> dict[str, Any] | None:
        self.initialize()
        return self._read_one(
            "SELECT * FROM cad_setup_revisions WHERE revision_id = ?", (revision_id,)
        )

    def record_project_setup(
        self, lineage_id: str, inventory_sha256: str, revision_id: str
    ) -> dict[str, Any]:
        """Make a setup revision the one a project is prepared with, for these sources."""

        if not lineage_id or not inventory_sha256 or not revision_id:
            raise ValueError("a project setup names a lineage, an inventory and a revision")
        self.initialize()
        with self._lock, self._transaction() as conn:
            conn.execute(
                "INSERT INTO cad_project_setups (lineage_id, inventory_sha256, revision_id, "
                "updated_at) VALUES (?, ?, ?, ?) ON CONFLICT (lineage_id, inventory_sha256) "
                "DO UPDATE SET revision_id = excluded.revision_id, "
                "updated_at = excluded.updated_at",
                (lineage_id, inventory_sha256, revision_id, utc_now()),
            )
            row = conn.execute(
                "SELECT * FROM cad_project_setups WHERE lineage_id = ? AND inventory_sha256 = ?",
                (lineage_id, inventory_sha256),
            ).fetchone()
        return dict(row)

    def get_project_setup(
        self, lineage_id: str, inventory_sha256: str
    ) -> dict[str, Any] | None:
        self.initialize()
        return self._read_one(
            "SELECT * FROM cad_project_setups WHERE lineage_id = ? AND inventory_sha256 = ?",
            (lineage_id, inventory_sha256),
        )

    def set_setting(self, key: str, value: Mapping[str, Any]) -> None:
        """Record one of WG's CAD Link settings, such as the solver selection."""

        self.initialize()
        with self._lock, self._transaction() as conn:
            conn.execute(
                "INSERT INTO cad_settings (key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at",
                (key, canonical_json(dict(value)), utc_now()),
            )

    def get_setting(self, key: str) -> dict[str, Any] | None:
        self.initialize()
        row = self._read_one("SELECT value_json FROM cad_settings WHERE key = ?", (key,))
        return json.loads(row["value_json"]) if row is not None else None

    def record_frame_confirmation(
        self,
        key: str,
        requirement: Mapping[str, Any],
        axis: str,
        *,
        frame: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Confirm an unlinked model's solver frame; the latest confirmation wins.

        ``frame`` records the complete transform the axis means and where its
        up came from (``solver_frame.confirm_frame``).
        """

        from .solver_frame import AXES

        if not key or axis not in AXES:
            raise ValueError("a frame confirmation names a key and one of the solver frame axes")
        self.initialize()
        with self._lock, self._transaction() as conn:
            conn.execute(
                "INSERT INTO cad_frame_confirmations "
                "(key, requirement_json, axis, confirmed_at, frame_json) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                "requirement_json = excluded.requirement_json, axis = excluded.axis, "
                "confirmed_at = excluded.confirmed_at, frame_json = excluded.frame_json",
                (
                    key,
                    canonical_json(dict(requirement)),
                    axis,
                    utc_now(),
                    canonical_json(dict(frame)) if frame is not None else None,
                ),
            )
            row = conn.execute(
                "SELECT * FROM cad_frame_confirmations WHERE key = ?", (key,)
            ).fetchone()
        return _frame_confirmation(dict(row))

    def get_frame_confirmation(self, key: str) -> dict[str, Any] | None:
        self.initialize()
        row = self._read_one("SELECT * FROM cad_frame_confirmations WHERE key = ?", (key,))
        return _frame_confirmation(row) if row is not None else None

    def record_frame_suggestion(
        self,
        snapshot_sha256: str,
        algorithm: str,
        suggestion: Mapping[str, Any],
        ingest_id: str,
    ) -> dict[str, Any]:
        """Cache a snapshot's automatic frame suggestion; the first one stays.

        The survey is deterministic for a snapshot, so a second computation
        (two commands racing) is the same answer and is not written.
        """

        if not snapshot_sha256 or not algorithm:
            raise ValueError("a frame suggestion names its snapshot and algorithm")
        self.initialize()
        with self._lock, self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO cad_frame_suggestions "
                "(snapshot_sha256, algorithm, suggestion_json, ingest_id, computed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (snapshot_sha256, algorithm, canonical_json(dict(suggestion)), ingest_id, utc_now()),
            )
            row = conn.execute(
                "SELECT suggestion_json FROM cad_frame_suggestions "
                "WHERE snapshot_sha256 = ? AND algorithm = ?",
                (snapshot_sha256, algorithm),
            ).fetchone()
        return json.loads(row["suggestion_json"])

    def get_frame_suggestion(self, snapshot_sha256: str, algorithm: str) -> dict[str, Any] | None:
        self.initialize()
        row = self._read_one(
            "SELECT suggestion_json FROM cad_frame_suggestions "
            "WHERE snapshot_sha256 = ? AND algorithm = ?",
            (snapshot_sha256, algorithm),
        )
        return json.loads(row["suggestion_json"]) if row is not None else None

    def record_preparation(
        self,
        operation_id: str,
        generation: int,
        *,
        preparation_id: str,
        snapshot_sha256: str,
        setup_revision_id: str | None,
        ingest_id: str,
        report_sha256: str | None,
        blocking_finding_ids: list[str],
        meshing_semantics: str | None = None,
    ) -> dict[str, Any] | None:
        """Record what the attempt prepared, and move it to ``ready``. Fenced.

        A preparation the attempt resumed is already recorded, and keeps the
        generation of the attempt that made it.
        """

        attempt = _require_generation(generation)
        self.initialize()
        now = utc_now()
        with self._lock, self._transaction() as conn:
            if not self.attempt_is_current(conn, operation_id, attempt):
                return None
            conn.execute(
                "INSERT OR IGNORE INTO cad_preparations (preparation_id, operation_id, "
                "attempt_generation, snapshot_sha256, setup_revision_id, ingest_id, "
                "report_sha256, blocking_findings_json, meshing_semantics, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    preparation_id,
                    operation_id,
                    attempt,
                    snapshot_sha256,
                    setup_revision_id,
                    ingest_id,
                    report_sha256,
                    canonical_json(list(blocking_finding_ids)),
                    meshing_semantics,
                    now,
                ),
            )
            conn.execute(
                "UPDATE cad_operations SET preparation_id = ?, stage = ?, updated_at = ? "
                "WHERE operation_id = ?",
                (preparation_id, STAGE_READY, now, operation_id),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row)

    def get_preparation(self, preparation_id: str) -> dict[str, Any] | None:
        self.initialize()
        return self._read_one(
            "SELECT * FROM cad_preparations WHERE preparation_id = ?", (preparation_id,)
        )

    def record_legacy_outcome(
        self,
        operation_id: str,
        *,
        kind: str,
        state: str,
        job_id: str | None = None,
        outcome: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Keep a terminal outcome whose request WG does not hold.

        Without the request its digest, target and inputs are unknown, so the
        row is legacy and they stay NULL. An existing row always wins; the
        flag says whether this call created the row.
        """

        _require_operation_id(operation_id)
        require_kind(kind)
        if state not in TERMINAL_STATES:
            raise ValueError(f"a legacy outcome is terminal, not {state!r}")
        outcome_json = validate_outcome(operation_id, state, outcome=outcome)
        if job_id is not None and (not isinstance(job_id, str) or not job_id):
            raise ValueError("job_id must be a non-empty string")
        self.initialize()
        now = utc_now()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO cad_operations (
                  operation_id, kind, state, outcome_json, job_id,
                  created_at, updated_at, attempt_generation, legacy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
                ON CONFLICT (operation_id) DO NOTHING
                """,
                (operation_id, kind, state, outcome_json, job_id, now, now),
            )
            row = conn.execute(
                "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return dict(row), cursor.rowcount == 1

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        # Initializing first is what imports an earlier version's ledger
        # before anyone asks it a question.
        self.initialize()
        return self._read_one(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        )

    def list_operations(
        self,
        *,
        kind: str | None = None,
        states: Iterable[str] | None = None,
        limit: int = 200,
        oldest_first: bool = False,
    ) -> list[dict[str, Any]]:
        """Operations, most recently changed first, or oldest accepted first.

        ``oldest_first`` is acceptance order, the order solve commands wait in.
        """

        self.initialize()
        clauses: list[str] = []
        parameters: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind)
        if states is not None:
            wanted = sorted(set(states))
            if not wanted:
                return []
            clauses.append(f"state IN ({', '.join('?' for _ in wanted)})")
            parameters.extend(wanted)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        bounded_limit = max(1, min(int(limit), 1000))
        # Acceptance order is insertion order. Rows are never deleted and the
        # store never runs VACUUM, so rowid keeps it exactly; created_at has
        # one-second resolution and follows the wall clock.
        order = (
            "ORDER BY rowid ASC "
            if oldest_first
            else "ORDER BY updated_at DESC, rowid DESC "
        )
        with self._lock:
            rows = self._connect().execute(
                f"SELECT rowid AS accepted_seq, * FROM cad_operations {where}{order}LIMIT ?",
                (*parameters, bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_design(self, design_id: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM designs WHERE design_id = ?", (design_id,)
        )

    def list_designs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List recent registry heads without loading their snapshot text."""

        if not self._initialized and (
            str(self.db_path) == ":memory:" or not self.db_path.exists()
        ):
            return []
        bounded_limit = max(1, min(int(limit), 500))
        with self._lock:
            try:
                rows = self._connect().execute(
                    """SELECT designs.design_id, designs.lineage_id,
                              designs.edit_version, designs.design_hash,
                              designs.filename, designs.branched_from_design_id,
                              designs.branched_from_edit_version,
                              designs.created_at, designs.updated_at,
                              COUNT(exports.export_id) AS export_count,
                              MAX(exports.created_at) AS last_exported_at
                       FROM designs
                       LEFT JOIN exports ON exports.design_id = designs.design_id
                       GROUP BY designs.design_id
                       ORDER BY designs.updated_at DESC, designs.design_id DESC
                       LIMIT ?""",
                    (bounded_limit,),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return []
                raise
        return [dict(row) for row in rows]

    def list_projects(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List one canonical, newest design head per lineage."""

        if not self._initialized and (
            str(self.db_path) == ":memory:" or not self.db_path.exists()
        ):
            return []
        bounded_limit = max(1, min(int(limit), 500))
        with self._lock:
            try:
                rows = self._connect().execute(
                    """SELECT designs.design_id, designs.lineage_id,
                              designs.edit_version, designs.design_hash,
                              designs.filename, designs.branched_from_design_id,
                              designs.branched_from_edit_version,
                              designs.created_at, designs.updated_at,
                              COUNT(exports.export_id) AS export_count,
                              MAX(exports.created_at) AS last_exported_at
                       FROM designs
                       LEFT JOIN exports ON exports.design_id = designs.design_id
                       WHERE NOT EXISTS (
                         SELECT 1 FROM designs AS newer
                         WHERE newer.lineage_id = designs.lineage_id
                           AND (
                             newer.updated_at > designs.updated_at
                             OR (
                               newer.updated_at = designs.updated_at
                               AND newer.design_id > designs.design_id
                             )
                           )
                       )
                       GROUP BY designs.design_id
                       ORDER BY designs.updated_at DESC, designs.design_id DESC
                       LIMIT ?""",
                    (bounded_limit,),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return []
                raise
        return [dict(row) for row in rows]

    def find_latest_design_for_lineage(self, lineage_id: str) -> dict[str, Any] | None:
        """Find a lineage's canonical head without a bounded recent scan."""

        return self._read_one(
            """SELECT * FROM designs WHERE lineage_id = ?
               ORDER BY updated_at DESC, design_id DESC LIMIT 1""",
            (lineage_id,),
        )

    def find_design_by_hash(self, value: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM designs WHERE design_hash = ? ORDER BY updated_at DESC LIMIT 1",
            (value,),
        )

    def get_export(self, export_or_bundle_id: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM exports WHERE export_id = ? OR bundle_id = ?",
            (export_or_bundle_id, export_or_bundle_id),
        )

    def find_export_by_artifact(self, artifact_sha256: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM exports WHERE artifact_sha256 = ? ORDER BY created_at DESC LIMIT 1",
            (artifact_sha256,),
        )

    def find_export_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM exports WHERE idempotency_key = ?", (idempotency_key,)
        )

    def find_latest_export_for_lineage(self, lineage_id: str) -> dict[str, Any] | None:
        """The newest export anywhere in one lineage, forks included.

        The CAD names a lineage owns were only recorded from schema 7 onwards,
        so links made before it recover them from what their last export
        already published.
        """

        return self._read_one(
            """
            SELECT exports.* FROM exports
            JOIN designs ON designs.design_id = exports.design_id
            WHERE designs.lineage_id = ?
            ORDER BY exports.created_at DESC, exports.sequence DESC LIMIT 1
            """,
            (lineage_id,),
        )

    def get_lineage_cad_names(self, lineage_id: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM lineage_cad_names WHERE lineage_id = ?", (lineage_id,)
        )

    def record_lineage_cad_names(
        self,
        lineage_id: str,
        *,
        parameter_slug: str | None = None,
        bundle_stem: str | None = None,
        archive_stem: str | None = None,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        """Claim a lineage's CAD names, first writer per column winning.

        A recorded name is what an already-linked CAD document depends on, so
        it is never overwritten -- ``COALESCE`` keeps the existing value and
        makes a concurrent second export a no-op rather than a rename.
        """

        self.initialize()
        now = recorded_at or utc_now()
        with self._lock, self._transaction() as conn:
            existing = conn.execute(
                "SELECT archive_stem FROM lineage_cad_names WHERE lineage_id = ?",
                (lineage_id,),
            ).fetchone()
            claimed_archive_stem = (
                str(existing["archive_stem"] or "").strip() if existing else ""
            )
            if not claimed_archive_stem and str(archive_stem or "").strip():
                claimed_archive_stem = _allocate_archive_stem(
                    conn, lineage_id, archive_stem
                )
            conn.execute(
                """
                INSERT INTO lineage_cad_names (
                  lineage_id, parameter_slug, bundle_stem, archive_stem,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (lineage_id) DO UPDATE SET
                  parameter_slug = COALESCE(
                    lineage_cad_names.parameter_slug, excluded.parameter_slug
                  ),
                  bundle_stem = COALESCE(
                    lineage_cad_names.bundle_stem, excluded.bundle_stem
                  ),
                  archive_stem = CASE
                    WHEN lineage_cad_names.archive_stem IS NULL
                      OR TRIM(lineage_cad_names.archive_stem) = ''
                    THEN excluded.archive_stem
                    ELSE lineage_cad_names.archive_stem
                  END,
                  updated_at = excluded.updated_at
                """,
                (
                    lineage_id,
                    parameter_slug,
                    bundle_stem,
                    claimed_archive_stem or None,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM lineage_cad_names WHERE lineage_id = ?", (lineage_id,)
            ).fetchone()
        return self._row(row) or {}

    def get_lineage_for_cad_document(self, native_id: str) -> dict[str, Any] | None:
        """The lineage a Fusion document owns, if one has been claimed."""

        if not str(native_id or "").strip():
            return None
        return self._read_one(
            "SELECT * FROM lineage_cad_names WHERE document_native_id = ?",
            (str(native_id).strip(),),
        )

    def claim_cad_document_lineage(
        self,
        native_id: str,
        document_name: str | None = None,
        *,
        recorded_at: str | None = None,
    ) -> str | None:
        """The lineage a CAD-authored document is the project for.

        Geometry authored in CAD arrives with no design to anchor to, so
        without this it belonged to no project at all: its runs carried no
        lineage and dropped out of the project history, and its captured
        document was filed under a folder no run ever wrote to.

        The Fusion native id is a lineage urn and therefore the identity; the
        document name is only a label and follows a rename. The archive stem
        deliberately does not -- see ``claim_archive_stem``.
        """

        native = str(native_id or "").strip()
        if not native:
            return None
        self.initialize()
        now = recorded_at or utc_now()
        name = str(document_name or "").strip() or None
        with self._lock, self._transaction() as conn:
            row = conn.execute(
                "SELECT lineage_id FROM lineage_cad_names WHERE document_native_id = ?",
                (native,),
            ).fetchone()
            if row is not None:
                lineage_id = str(row["lineage_id"])
                if name is not None:
                    conn.execute(
                        "UPDATE lineage_cad_names SET document_name = ?, "
                        "updated_at = ? WHERE lineage_id = ?",
                        (name, now, lineage_id),
                    )
                return lineage_id
            lineage_id = mint_id("wgl_")
            conn.execute(
                """
                INSERT INTO lineage_cad_names (
                  lineage_id, document_native_id, document_name,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (lineage_id, native, name, now, now),
            )
        return lineage_id

    def record_cad_document(
        self,
        lineage_id: str,
        native_id: str | None,
        document_name: str | None = None,
        *,
        recorded_at: str | None = None,
    ) -> None:
        """Note which CAD document a WG-originated lineage is linked to.

        First writer wins on the native id, as everywhere else in this table:
        if another lineage already owns that document, both rows are left
        alone rather than one stealing the other's identity.
        """

        native = str(native_id or "").strip() or None
        name = str(document_name or "").strip() or None
        if native is None and name is None:
            return
        self.initialize()
        now = recorded_at or utc_now()
        with self._lock, self._transaction() as conn:
            if native is not None:
                owner = conn.execute(
                    "SELECT lineage_id FROM lineage_cad_names "
                    "WHERE document_native_id = ?",
                    (native,),
                ).fetchone()
                if owner is not None and str(owner["lineage_id"]) != lineage_id:
                    native = None
            conn.execute(
                """
                INSERT INTO lineage_cad_names (
                  lineage_id, document_native_id, document_name,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (lineage_id) DO UPDATE SET
                  document_native_id = COALESCE(
                    excluded.document_native_id,
                    lineage_cad_names.document_native_id
                  ),
                  document_name = COALESCE(
                    excluded.document_name, lineage_cad_names.document_name
                  ),
                  updated_at = excluded.updated_at
                """,
                (lineage_id, native, name, now, now),
            )

    def list_cad_document_projects(self) -> list[dict[str, Any]]:
        """Projects that exist only in CAD: a document with no WG design."""

        self.initialize()
        with self._lock, self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM lineage_cad_names
                WHERE document_native_id IS NOT NULL
                  AND lineage_id NOT IN (SELECT lineage_id FROM designs)
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def claim_archive_stem(
        self,
        lineage_id: str,
        *,
        preferred: str | None = None,
        recorded_at: str | None = None,
    ) -> str | None:
        """The single folder a lineage's runs and captured CAD documents share.

        Two writers file into the run archive for one project: the ingest files
        a captured Fusion document the moment a return arrives, and the run
        archive files the solve afterwards. They used to derive the folder
        independently -- the bundle stem or the document name on one side, the
        job's own label on the other -- so renaming a run was enough to strand
        its Fusion document in a folder the run never appeared in.

        The name is therefore claimed once per lineage and never changed:
        an already-claimed stem wins, then the ``.wglink`` folder the lineage
        owns, then the caller's suggestion (in practice the CAD document name).
        Returns ``None`` only when the lineage has no usable name yet, which
        leaves the caller to fall back exactly as it did before.
        """

        recorded = self.get_lineage_cad_names(lineage_id) or {}
        claimed = str(recorded.get("archive_stem") or "").strip()
        if claimed:
            return claimed
        stem = (
            str(recorded.get("bundle_stem") or "").strip()
            or str(preferred or "").strip()
        )
        if not stem:
            return None
        written = self.record_lineage_cad_names(
            lineage_id, archive_stem=stem, recorded_at=recorded_at
        )
        # A concurrent claimant may have won; its name is the answer for both.
        return str(written.get("archive_stem") or "").strip() or stem

    def get_ingest(self, ingest_id: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT * FROM ingests WHERE ingest_id = ?", (ingest_id,)
        )

    def allocate_ingest(
        self,
        *,
        manifest_sha256: str,
        artifact_sha256: str,
        record_builder: Callable[[str, str], str],
        created_at: str | None = None,
        commit_guard: Callable[[sqlite3.Connection], bool] | None = None,
    ) -> dict[str, Any]:
        """Atomically publish ingestion artifacts and their immutable record.

        ``commit_guard`` runs inside the transaction, before anything is
        published: a preparation passes its attempt's fence here, so an attempt
        that lost its operation commits no record and publishes no bundle.
        """

        self.initialize()
        now = created_at or utc_now()
        ingest_id = mint_id("wgi_")
        with self._lock, self._transaction() as conn:
            if commit_guard is not None and not commit_guard(conn):
                raise StaleAttempt("the preparation lost its operation before committing")
            record_json = record_builder(ingest_id, now)
            conn.execute(
                """
                INSERT INTO ingests (
                  ingest_id, manifest_sha256, artifact_sha256, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ingest_id,
                    manifest_sha256,
                    artifact_sha256,
                    record_json,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM ingests WHERE ingest_id = ?", (ingest_id,)
            ).fetchone()
        return self._row(row) or {}

    def save(
        self,
        *,
        requested: SaveIdentity | None,
        design_hash: str,
        filename: str,
        snapshot_builder: Callable[[CadLink], str],
        saved_at: str | None = None,
    ) -> dict[str, Any]:
        """Commit a design head with CAS semantics, auto-forking on conflict."""

        self.initialize()
        now = saved_at or utc_now()
        with self._lock, self._transaction() as conn:
            head = None
            if requested is not None:
                head = conn.execute(
                    "SELECT * FROM designs WHERE design_id = ?", (requested.design_id,)
                ).fetchone()

            forked = False
            from_data: dict[str, object] | None = None
            branched_from_export_id: str | None = None
            if requested is None:
                design_id = mint_id("wgd_")
                lineage_id = mint_id("wgl_")
                edit_version = 1
                created_at = now
            elif head is None:
                design_id = requested.design_id
                lineage_id = requested.lineage_id
                edit_version = requested.base_edit_version + 1
                created_at = now
            elif int(head["edit_version"]) == requested.base_edit_version:
                if str(head["lineage_id"]) != requested.lineage_id:
                    raise ValueError("lineageId does not match the registered design")
                design_id = requested.design_id
                lineage_id = requested.lineage_id
                edit_version = requested.base_edit_version + 1
                created_at = str(head["created_at"])
            else:
                forked = True
                design_id = mint_id("wgd_")
                lineage_id = str(head["lineage_id"])
                edit_version = 1
                created_at = now
                latest = conn.execute(
                    "SELECT export_id FROM exports WHERE design_id = ? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (requested.design_id,),
                ).fetchone()
                branched_from_export_id = str(latest["export_id"]) if latest else None
                from_data = {
                    "designId": requested.design_id,
                    "editVersion": requested.base_edit_version,
                    "exportId": branched_from_export_id,
                }

            identity = CadLink(
                design_id=design_id,
                lineage_id=lineage_id,
                edit_version=edit_version,
                saved_at=now,
                saved_design_hash=truncated_design_hash(design_hash),
            )
            snapshot_text = snapshot_builder(identity)
            if head is not None and not forked:
                conn.execute(
                    """
                    UPDATE designs SET edit_version = ?, design_hash = ?, snapshot_text = ?,
                      filename = ?, updated_at = ? WHERE design_id = ?
                    """,
                    (edit_version, design_hash, snapshot_text, filename, now, design_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO designs (
                      design_id, lineage_id, edit_version, design_hash, snapshot_text, filename,
                      branched_from_design_id, branched_from_edit_version,
                      branched_from_export_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        design_id,
                        lineage_id,
                        edit_version,
                        design_hash,
                        snapshot_text,
                        filename,
                        requested.design_id if forked and requested else None,
                        requested.base_edit_version if forked and requested else None,
                        branched_from_export_id,
                        created_at,
                        now,
                    ),
                )
        return {
            "identity": identity,
            "text": snapshot_text,
            "forked": forked,
            "from": from_data,
        }

    def allocate_export(
        self,
        *,
        design_id: str,
        geometry_hash: str | None = None,
        artifact_sha256: str | None = None,
        bundle_path: str | None = None,
        idempotency_key: str,
        manifest_json: str | None = None,
        manifest_builder: Callable[[Mapping[str, object]], str] | None = None,
        export_builder: Callable[[Mapping[str, object]], Mapping[str, str]] | None = None,
        bundle_id: str | None = None,
        parent_export_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Reserve quickly, build unlocked, then atomically finalize an export."""

        builders = sum(
            value is not None
            for value in (manifest_json, manifest_builder, export_builder)
        )
        if builders != 1:
            raise ValueError(
                "provide exactly one of manifest_json, manifest_builder, or export_builder"
            )
        self.initialize()
        build_lock = self._export_build_lock(design_id)
        with build_lock:
            now = created_at or utc_now()
            with self._lock, self._transaction() as conn:
                retry = conn.execute(
                    "SELECT * FROM exports WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if retry is not None:
                    return self._row(retry) or {}
                reservation = conn.execute(
                    "SELECT * FROM export_reservations WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if reservation is None:
                    design = conn.execute(
                        "SELECT * FROM designs WHERE design_id = ?", (design_id,)
                    ).fetchone()
                    if design is None:
                        raise KeyError(f"unknown design_id {design_id}")
                    latest = conn.execute(
                        """
                        SELECT export_id, sequence FROM (
                          SELECT export_id, sequence FROM exports WHERE design_id = ?
                          UNION ALL
                          SELECT export_id, sequence FROM export_reservations
                          WHERE design_id = ?
                        ) ORDER BY sequence DESC LIMIT 1
                        """,
                        (design_id, design_id),
                    ).fetchone()
                    sequence = (int(latest["sequence"]) if latest else 0) + 1
                    resolved_parent = (
                        parent_export_id
                        if parent_export_id is not None
                        else (str(latest["export_id"]) if latest else None)
                    )
                    export_id = mint_id("wge_")
                    resolved_bundle_id = bundle_id or mint_id("wgb_")
                    conn.execute(
                        """
                        INSERT INTO export_reservations (
                          idempotency_key, export_id, bundle_id, design_id,
                          sequence, parent_export_id, edit_version, design_hash,
                          snapshot_text, created_at, state
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'building')
                        """,
                        (
                            idempotency_key,
                            export_id,
                            resolved_bundle_id,
                            design_id,
                            sequence,
                            resolved_parent,
                            design["edit_version"],
                            design["design_hash"],
                            design["snapshot_text"],
                            now,
                        ),
                    )
                    reservation = conn.execute(
                        "SELECT * FROM export_reservations WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                elif str(reservation["design_id"]) != design_id:
                    raise ValueError("idempotency_key is reserved for another design")
                conn.execute(
                    "UPDATE export_reservations SET state = 'building' "
                    "WHERE idempotency_key = ?",
                    (idempotency_key,),
                )

            assert reservation is not None
            facts: dict[str, object] = {
                "exportId": reservation["export_id"],
                "bundleId": reservation["bundle_id"],
                "designId": reservation["design_id"],
                "sequence": reservation["sequence"],
                "parentExportId": reservation["parent_export_id"],
                "editVersion": reservation["edit_version"],
                "designHash": reservation["design_hash"],
                "createdAt": reservation["created_at"],
            }
            try:
                if export_builder is not None:
                    products = export_builder(facts)
                    stored_manifest = products.get("manifest_json")
                    geometry_hash = products.get("geometry_hash")
                    artifact_sha256 = products.get("artifact_sha256")
                    bundle_path = products.get("bundle_path")
                else:
                    stored_manifest = (
                        manifest_builder(facts)
                        if manifest_builder is not None
                        else manifest_json
                    )
                if stored_manifest is None:
                    raise ValueError("manifest builder did not return manifest_json")
                if geometry_hash is None or artifact_sha256 is None:
                    raise ValueError("geometry_hash and artifact_sha256 are required")

                with self._lock, self._transaction() as conn:
                    conn.execute(
                        """
                        INSERT INTO exports (
                          export_id, bundle_id, design_id, sequence, parent_export_id,
                          edit_version, design_hash, geometry_hash, artifact_sha256,
                          manifest_json, snapshot_text, idempotency_key, created_at,
                          bundle_path
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            reservation["export_id"],
                            reservation["bundle_id"],
                            reservation["design_id"],
                            reservation["sequence"],
                            reservation["parent_export_id"],
                            reservation["edit_version"],
                            reservation["design_hash"],
                            geometry_hash,
                            artifact_sha256,
                            stored_manifest,
                            reservation["snapshot_text"],
                            idempotency_key,
                            reservation["created_at"],
                            bundle_path,
                        ),
                    )
                    conn.execute(
                        "DELETE FROM export_reservations WHERE idempotency_key = ?",
                        (idempotency_key,),
                    )
                    row = conn.execute(
                        "SELECT * FROM exports WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
            except BaseException:
                with self._lock, self._transaction() as conn:
                    conn.execute(
                        "UPDATE export_reservations SET state = 'retryable' "
                        "WHERE idempotency_key = ?",
                        (idempotency_key,),
                    )
                raise
        return self._row(row) or {}

    def _export_build_lock(self, design_id: str) -> threading.Lock:
        database = (
            f"memory:{id(self)}"
            if str(self.db_path) == ":memory:"
            else str(self.db_path.absolute())
        )
        key = (database, design_id)
        with _EXPORT_BUILD_LOCKS_GUARD:
            return _EXPORT_BUILD_LOCKS.setdefault(key, threading.Lock())

    def get_onshape_link(
        self, design_id: str, account_id: str | None = None
    ) -> dict[str, Any] | None:
        """The link for one design. ``account_id=None`` means any account.

        Status polling has no Onshape account id to hand -- resolving one costs
        a network round trip, and the panel polls -- so it asks for whichever
        link is newest and reports the account it belongs to.
        """

        if account_id is None:
            return self._read_one(
                "SELECT * FROM onshape_links WHERE design_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (design_id,),
            )
        return self._read_one(
            "SELECT * FROM onshape_links WHERE design_id = ? AND account_id = ?",
            (design_id, account_id),
        )

    def get_onshape_link_by_instance(
        self, instance_id: str, account_id: str | None = None
    ) -> dict[str, Any] | None:
        """Resolve one explicit managed Onshape link identity."""

        if account_id is None:
            return self._read_one(
                "SELECT * FROM onshape_links WHERE instance_id = ?", (instance_id,)
            )
        return self._read_one(
            "SELECT * FROM onshape_links WHERE instance_id = ? AND account_id = ?",
            (instance_id, account_id),
        )

    def find_onshape_links_for_lineage(
        self, lineage_id: str, account_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every linked document in one lineage, newest first.

        A design forks on a conflicting save (see ``save``), which mints a new
        ``design_id`` in the same lineage. Without this the forked design would
        look unlinked and create a second Onshape document for what the user
        experiences as one design.
        """

        if not self._initialized and (
            str(self.db_path) == ":memory:" or not self.db_path.exists()
        ):
            return []
        sql = """
            SELECT onshape_links.* FROM onshape_links
            JOIN designs ON designs.design_id = onshape_links.design_id
            WHERE designs.lineage_id = ?
        """
        parameters: tuple[object, ...] = (lineage_id,)
        if account_id is not None:
            sql += " AND onshape_links.account_id = ?"
            parameters = (lineage_id, account_id)
        sql += " ORDER BY onshape_links.updated_at DESC"
        with self._lock:
            try:
                rows = self._connect().execute(sql, parameters).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return []
                raise
        return [dict(row) for row in rows]

    def save_onshape_link(
        self,
        *,
        design_id: str,
        account_id: str,
        instance_id: str | None = None,
        document_id: str,
        workspace_id: str,
        blob_element_id: str,
        part_studio_element_id: str | None,
        variable_studio_element_id: str | None,
        document_name: str,
        is_public: bool,
        last_export_id: str | None,
        last_sequence: int | None,
        last_design_hash: str | None,
        last_geometry_hash: str | None,
        feature_studio_element_id: str | None = None,
        native_feature_id: str | None = None,
        datum_feature_studio_element_id: str | None = None,
        datum_feature_id: str | None = None,
        build_mode: str | None = None,
        saved_at: str | None = None,
    ) -> dict[str, Any]:
        """Record where a design now lives in Onshape, replacing any prior row."""

        self.initialize()
        now = saved_at or utc_now()
        with self._lock, self._transaction() as conn:
            existing = conn.execute(
                "SELECT created_at, instance_id FROM onshape_links "
                "WHERE design_id = ? AND account_id = ?",
                (design_id, account_id),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            resolved_instance_id = (
                str(existing["instance_id"])
                if existing and existing["instance_id"]
                else instance_id or mint_id("wgo_")
            )
            if (
                existing
                and instance_id is not None
                and instance_id != resolved_instance_id
            ):
                raise ValueError(
                    "an existing Onshape link cannot be reassigned to another instance id"
                )
            conn.execute(
                """
                INSERT INTO onshape_links (
                  design_id, account_id, instance_id, document_id, workspace_id, blob_element_id,
                  part_studio_element_id, variable_studio_element_id,
                  feature_studio_element_id, native_feature_id,
                  datum_feature_studio_element_id, datum_feature_id, build_mode,
                  document_name, is_public, last_export_id, last_sequence,
                  last_design_hash, last_geometry_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (design_id, account_id) DO UPDATE SET
                  instance_id = onshape_links.instance_id,
                  document_id = excluded.document_id,
                  workspace_id = excluded.workspace_id,
                  blob_element_id = excluded.blob_element_id,
                  part_studio_element_id = excluded.part_studio_element_id,
                  variable_studio_element_id = excluded.variable_studio_element_id,
                  feature_studio_element_id = excluded.feature_studio_element_id,
                  native_feature_id = excluded.native_feature_id,
                  datum_feature_studio_element_id = excluded.datum_feature_studio_element_id,
                  datum_feature_id = excluded.datum_feature_id,
                  build_mode = excluded.build_mode,
                  document_name = excluded.document_name,
                  is_public = excluded.is_public,
                  last_export_id = excluded.last_export_id,
                  last_sequence = excluded.last_sequence,
                  last_design_hash = excluded.last_design_hash,
                  last_geometry_hash = excluded.last_geometry_hash,
                  updated_at = excluded.updated_at
                """,
                (
                    design_id,
                    account_id,
                    resolved_instance_id,
                    document_id,
                    workspace_id,
                    blob_element_id,
                    part_studio_element_id,
                    variable_studio_element_id,
                    feature_studio_element_id,
                    native_feature_id,
                    datum_feature_studio_element_id,
                    datum_feature_id,
                    build_mode,
                    document_name,
                    1 if is_public else 0,
                    last_export_id,
                    last_sequence,
                    last_design_hash,
                    last_geometry_hash,
                    created_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM onshape_links WHERE design_id = ? AND account_id = ?",
                (design_id, account_id),
            ).fetchone()
        return self._row(row) or {}

    def delete_onshape_link(self, design_id: str, account_id: str) -> bool:
        """Forget a link so the next send creates a fresh document."""

        self.initialize()
        with self._lock, self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM onshape_links WHERE design_id = ? AND account_id = ?",
                (design_id, account_id),
            )
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for conn in connections:
            conn.close()
        self._local = threading.local()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self.journal_mode_status = configure_connection(
            conn, db_path=str(self.db_path), label="CAD Link database"
        )
        self._local.conn = conn
        with self._connections_lock:
            self._connections.add(conn)
        return conn

    def _read_one(self, sql: str, parameters: tuple[object, ...]) -> dict[str, Any] | None:
        """Read without creating a registry merely because a file was opened."""

        if not self._initialized and (
            str(self.db_path) == ":memory:" or not self.db_path.exists()
        ):
            return None
        with self._lock:
            try:
                row = self._connect().execute(sql, parameters).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return None
                raise
        return self._row(row)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None


__all__ = ["CadLinkStore"]
