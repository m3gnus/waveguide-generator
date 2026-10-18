"""Snapshot, setup and provenance inputs shared by CAD delivery and job preparation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from server.jobs.models import SolveRequest

from server.cadlink.intake.ingest import (
    read_snapshot,
    retain_snapshot,
    retained_snapshot_path,
)
from server.cadlink.joblane.default_setup import (
    SolveDefaultsDamaged,
    default_setup,
)
from server.cadlink.joblane.project_setup import (
    inventory_sha256,
    project_setup,
    solver_selection,
)
from server.cadlink.intake.readings import snapshot_project, solver_anchor
from server.jobs.imported_setup import (
    DEFAULTS_ORIGIN,
    CadSolveSetup,
    setup_content,
    setup_digest,
    validate_setup,
)
from server.cadlink.interpretation.domain_decision import decision_problem
from server.cadlink.interpretation.domain_interpretation import record_plan_identity
from server.cadlink.interpretation.solver_frame import (
    CONTRACT as FRAME_CONTRACT,
    REASON as FRAME_CONFIRMATION_REQUIRED,
    record_frame_identity,
    record_is_unlinked,
)
from server.cadlink.intake.readings import (
    frame_provenance,
    record_solved_frame_provenance,
)
from server.cadlink.store import UNFILED_MODEL_ID, CadLinkStore
from server.cadlink.wgreturn import WgReturnError


logger = logging.getLogger(__name__)


@dataclass
class SnapshotContext:
    """Storage and exchange paths used while preparing a retained model."""

    store: CadLinkStore
    data_dir: Path
    workspace_root: Path | None


DAMAGED_COPY_MESSAGE = (
    "WG's copy of this model is damaged, and it could not take a fresh one from the WGLink "
    "folder. Send the model again from Fusion."
)
_ENGINE_CANNOT_SOLVE_CODES = frozenset({
    "imported_engine_unsupported", "imported_return_unsupported_by_engine",
})


class SnapshotUnavailable(OSError):
    """The return cannot be read now; the operation waits instead of failing for good."""


def _inputs(row: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(row["inputs_json"]) if row.get("inputs_json") else {}


def exchange_bundle_path(workspace_root: Path | None, bundle_path: str) -> Path:
    """The return a solve command names, inside the selected WGLink folder."""

    from server.workspace.api import _path_segments, _strictly_inside

    if workspace_root is None:
        raise SnapshotUnavailable(
            "No WGLink folder is selected, so WG cannot read the return Fusion sent. "
            "Choose it in Settings → CAD Link, then press Solve now."
        )
    segments = _path_segments(bundle_path, "bundlePath")
    if not segments or segments[0].casefold() != "wgreturn" or not segments[-1].endswith(".wgreturn"):
        raise WgReturnError("bundlePath must name a .wgreturn bundle under the workspace's wgreturn/")
    path = workspace_root.joinpath(*segments).resolve()
    _strictly_inside(path, workspace_root, "bundlePath")
    if not path.is_dir():
        # The folder was switched, a drive is not mounted, or the return was
        # removed before WG kept a copy: cannot proceed now, not never.
        raise SnapshotUnavailable(
            "The return Fusion sent is not in the WGLink folder, and WG has no copy it can "
            "use. Check the WGLink folder in Settings → CAD Link, or send it again from Fusion."
        )
    return path


def _snapshot_record(store: CadLinkStore, retained: Mapping[str, Any]) -> dict[str, Any]:
    """What an operation stores of its snapshot: hashes and whose it is, never a path.

    The copy's place follows from the manifest hash (``retained_snapshot_path``),
    so a moved data directory does not orphan it. The document's name and the
    project it belongs to, when WG knows one, are what lets the UI say which
    project to open for its settings.
    """

    manifest = _retained_manifest(retained)
    return {
        "manifest_sha256": str(retained["manifest_sha256"]),
        "artifact_sha256": str(retained["artifact_sha256"]),
        "document_name": _document_name(manifest),
        "project_lineage_id": snapshot_project(store, manifest) if manifest else None,
    }


def _retained(data_dir: Path, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The operation's retained snapshot, when WG still holds the copy.

    A copy WG cannot read is not one it holds, and saying otherwise was a dead
    end: the caller would not retain again, and content-addressed staging skips
    an existing copy of an unchanged model, so neither pressing Solve now nor
    sending the same model again from Fusion could ever get past it. Reported
    absent, the return is taken again from the WGLink folder and the damaged
    copy is replaced (``ingest._stage_bundle_cas``).
    """

    snapshot = json.loads(row["snapshot_json"]) if row.get("snapshot_json") else None
    if not isinstance(snapshot, Mapping):
        return None
    try:
        path = retained_snapshot_path(data_dir, str(snapshot.get("manifest_sha256") or ""))
    except ValueError:
        return None
    if not path.is_dir():
        return None
    retained = {**snapshot, "retained_path": str(path)}
    return retained if _retained_manifest(retained) is not None else None


def _load_setup(
    ctx: SnapshotContext, revision_id: str | None, retained: Mapping[str, Any]
) -> tuple[CadSolveSetup, str] | None:
    """The setup this preparation uses, and its revision id.

    One the request names; otherwise the snapshot's project's own, with the
    engine selected in WG (CAD-OPERATIONS.md, "Project setups"). None when the
    project has none for these sources yet -- the caller then uses WG's
    default settings -- or when the revision named is gone.
    """

    if revision_id:
        row = ctx.store.get_setup_revision(revision_id)
        if row is None:
            return None
        return validate_setup(json.loads(row["setup_json"])), str(row["revision_id"])
    manifest = read_snapshot(str(retained["retained_path"]), retained=True).manifest
    lineage_id = snapshot_project(ctx.store, manifest)
    if lineage_id is None:
        return None
    return project_setup(ctx.store, lineage_id, manifest.get("sources") or [])


def _retained_manifest(retained: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """A retained snapshot's manifest, when the copy still is the bundle it is named for.

    The directory's name *is* the manifest's sha256 (``retained_snapshot_path``),
    so hashing the bytes read back and comparing them to it is what makes the
    path a claim about the content rather than an assumption about it. Without
    it, a different but internally valid bundle left at this path is read as
    this one: every check inside the bundle passes, and B's geometry is meshed
    and solved under A's recorded identity. Reading the manifest without that
    comparison is exactly the digest/path mismatch this storage exists to
    reject.
    """

    try:
        path = Path(str(retained["retained_path"]))
        raw = (path / "wgreturn.json").read_bytes()
    except (OSError, KeyError, TypeError):
        return None
    if hashlib.sha256(raw).hexdigest() != path.name.removesuffix(".wgreturn"):
        logger.warning("The retained copy %s does not hash to its own name.", path.name)
        return None
    try:
        manifest = json.loads(raw)
    except ValueError:
        return None
    return manifest if isinstance(manifest, Mapping) else None


def _retain_from_return(ctx: SnapshotContext, row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the return this operation names, from the WGLink folder."""

    inputs = _inputs(row)
    return retain_snapshot(
        exchange_bundle_path(ctx.workspace_root, str(inputs.get("bundle_path") or "")),
        ctx.data_dir,
        expected_manifest_sha256=str(inputs.get("manifest_sha256") or "") or None,
    )


def _copy_is_whole(retained: Mapping[str, Any]) -> bool:
    """Whether every member of the retained copy is still what the manifest declares.

    ``_retained_manifest`` settles the copy's *identity* for the price of one
    small hash, which is what every caller needs. This is the rest of the
    claim, and only preparation needs it, because preparation is what is about
    to mesh from those bytes: a torn write lands on the big STEP far more often
    than on the tiny manifest, and the manifest alone cannot see that. Left to
    the ingest, the same damage surfaced as a terminal ``snapshot_invalid``
    that blamed the user's return while the good one sat in the WGLink folder.
    """

    path = Path(str(retained["retained_path"]))
    try:
        found = read_snapshot(path, retained=True)
    except (WgReturnError, OSError, ValueError):
        return False
    # Defence in depth, and no more than that: every caller reaches this only
    # through ``_retained``, which has already compared the manifest's hash to
    # this same name. ``_retained``'s gate is the one holding the identity
    # requirement up -- a later simplification of it would take the real check
    # away, and this line would not notice.
    return found.manifest_sha256 == "sha256:" + path.name.removesuffix(".wgreturn")


def _document_name(manifest: Mapping[str, Any] | None) -> str | None:
    """The CAD document a snapshot came from, as the user named it."""

    document = manifest.get("document") if manifest is not None else None
    name = str(document.get("name") or "").strip() if isinstance(document, Mapping) else ""
    return name or None


def resumable_record(
    store: CadLinkStore,
    preparation: Mapping[str, Any] | None,
    revision_id: str,
    manifest_sha256: str,
    semantics: str,
    frame: Mapping[str, Any] | None = None,
    domain: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Whether a recorded preparation still matches all inputs.

    ``preparation`` names its ``setup_revision_id``, ``snapshot_sha256``,
    ``meshing_semantics`` and ``ingest_id``: the operation's row, or the job's
    ``task_metadata.cad.preparation`` (server/jobs/cad_preparation.py).
    """

    if (
        preparation is None
        or preparation["setup_revision_id"] != revision_id
        or preparation["snapshot_sha256"] != manifest_sha256
        or preparation["meshing_semantics"] != semantics
    ):
        return None
    ingest = store.get_ingest(str(preparation["ingest_id"]))
    if ingest is None:
        return None
    record = json.loads(ingest["record_json"])
    if frame is not None:
        # A record prepared under an earlier frame contract, or with another
        # roll, keeps its own meaning; a new attempt meshes in this frame.
        if frame.get("contract") != FRAME_CONTRACT or record_frame_identity(record) != dict(frame):
            return None
    held = record_plan_identity(record)
    if held != (dict(domain) if domain is not None else None):
        return None
    # The record's sealed domain decision is what a submission will solve; a
    # record whose decision no longer describes it is prepared again.
    if decision_problem(record) is not None:
        return None
    return record


def _record_frame_axis(record: Mapping[str, Any]) -> str | None:
    """The solver frame axis an unlinked record was meshed in; None when linked."""

    if not record_is_unlinked(record):
        return None
    normalisation = record.get("normalisation")
    frame = normalisation.get("solver_frame") if isinstance(normalisation, Mapping) else None
    axis = frame.get("axis") if isinstance(frame, Mapping) else None
    return str(axis) if axis else None


def _project_gate(retained: Mapping[str, Any]) -> dict[str, str]:
    """The design and instance the ingest's project gate is given for a snapshot.

    The backend prepares into the snapshot's own project -- the lineage of its
    solver anchor instance's WG design, as ``snapshot_project`` resolves it --
    never into a model that is open. So the gate is told that design and that
    exact instance. A snapshot whose anchor names no design, authored in CAD,
    names nothing, as before.
    """

    anchor = solver_anchor(_retained_manifest(retained) or {})
    if anchor is None:
        return {}
    design_id = anchor.get("design_id")
    if not isinstance(design_id, str) or not design_id.strip():
        return {}
    gate = {"expected_design_id": design_id}
    instance_id = anchor.get("instance_id")
    if isinstance(instance_id, str) and instance_id:
        gate["expected_instance_id"] = instance_id
    return gate


class _DefaultsUnavailable(ValueError):
    """WG's default settings cannot solve this model; the message says why."""


def _default_setup(
    ctx: SnapshotContext, retained: Mapping[str, Any]
) -> tuple[CadSolveSetup, str]:
    """WG's default setup for a first-time model, as a setup revision.

    With the engine and accuracy selected in WG, as every project setup is
    (``project_setup``). The job records the revision so a continuation reuses
    it and the run names exactly what it solved.
    """

    manifest = read_snapshot(str(retained["retained_path"]), retained=True).manifest
    try:
        setup = default_setup(manifest, solver_selection(ctx.store))
    except SolveDefaultsDamaged:
        raise
    except ValueError as exc:
        raise _DefaultsUnavailable(str(exc)) from exc
    revision = ctx.store.create_setup_revision(setup_content(setup), setup_digest(setup))
    return setup, str(revision["revision_id"])


def _remember_default_setup(
    store: CadLinkStore,
    manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    revision_id: str,
) -> str | None:
    """Make the defaults a first-time model was solved with its project's setup.

    Once the ingest has filed the snapshot under a project, so the next solve
    of the same sources reuses them and the user changes them in WG like any
    other settings. Never over settings the project has meanwhile: a person's
    own choice always wins over WG's defaults. The write is one conditional
    insert, so a setup the user records at any moment is never replaced.

    Returns the revision to prepare with instead when the project already has
    its own settings (with the engine selected in WG), else None.
    """

    project = record.get("project")
    lineage_id = snapshot_project(store, manifest) or (
        str(project.get("lineage_id") or "").strip() if isinstance(project, Mapping) else ""
    )
    if not lineage_id or lineage_id == UNFILED_MODEL_ID:
        # No project, or a return claiming the reserved id for "none": the
        # defaults stay this solve's own and are recorded for no model.
        return None
    sources = [source for source in manifest.get("sources") or [] if isinstance(source, Mapping)]
    inventory = inventory_sha256(sources)
    standing = store.record_project_setup_if_absent(lineage_id, inventory, revision_id)
    if standing["revision_id"] == revision_id or _is_default_setup(store, standing["revision_id"]):
        # Ours, or WG's defaults recorded before (with another engine, say):
        # nothing a person chose, so this solve goes on as it is.
        return None
    try:
        theirs = project_setup(store, lineage_id, sources)
    except ValueError:
        # Their settings cannot be used; this solve keeps the defaults, and
        # their next one waits with the reason (the ValueError branch).
        return None
    return theirs[1] if theirs is not None and theirs[1] != revision_id else None


def cad_provenance_record(
    store: CadLinkStore,
    operation_id: str,
    solve_request: SolveRequest,
    revision_id: str | None,
    *,
    preparation: Mapping[str, Any] | None,
    approvals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The job's ``task_metadata.cad``, from the parts of its preparation."""

    provenance: dict[str, Any] = {"operation_id": operation_id}
    revision = store.get_setup_revision(revision_id) if revision_id else None
    if revision is not None:
        provenance["setup"] = {
            "revision_id": revision["revision_id"],
            "digest": revision["content_sha256"],
            "origin": "wg_defaults" if _is_default_setup(store, revision_id) else "user",
        }
    prepared_id = str(getattr(solve_request.geometry, "ingest_id", "") or "")
    # Marked used before it is read: the job this provenance goes on names it.
    if (touch := getattr(store, "touch_ingest", None)) is not None:
        touch(prepared_id)
    ingest = store.get_ingest(prepared_id)
    if ingest is not None:
        record = json.loads(ingest["record_json"])
        provenance["frame"] = frame_provenance(
            store, record,
            automatic=record_solved_frame_provenance(store, record) == "automatic",
        )
    if preparation is not None:
        provenance["preparation"] = {
            "preparation_id": preparation["preparation_id"],
            "report_sha256": preparation["report_sha256"],
            "blocking_finding_ids": list(preparation["blocking_finding_ids"]),
            "approvals": [
                item for item in approvals
                if isinstance(item, Mapping)
                and item.get("preparation_id") == preparation["preparation_id"]
            ],
            "meshing_semantics": preparation["meshing_semantics"],
        }
    return provenance


def _is_default_setup(store: CadLinkStore, revision_id: str | None) -> bool:
    """Whether a setup revision is WG's default settings (``default_setup``)."""

    row = store.get_setup_revision(revision_id) if revision_id else None
    if row is None:
        return False
    try:
        return json.loads(row["setup_json"]).get("origin") == DEFAULTS_ORIGIN
    except (TypeError, ValueError, AttributeError):
        return False


def _submission_refusal_reason(exc: BaseException, code: str) -> str:
    """The operation reason for a request the jobs system refused."""

    if code == FRAME_CONFIRMATION_REQUIRED:
        return FRAME_CONFIRMATION_REQUIRED
    if type(exc).__name__ == "EngineUnavailableError":
        return "engine_unavailable"
    if code in _ENGINE_CANNOT_SOLVE_CODES:
        return "engine_cannot_solve_return"
    return "submission_refused"
