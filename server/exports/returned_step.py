"""Resolve a run's exact CAD return, without rebuilding any geometry."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from io import BytesIO
from pathlib import Path
import re
from typing import Any

from server.cadlink.intake.ingest import retained_snapshot_path
from server.cadlink.joblane.preparation_inputs import _retained_manifest
from server.cadlink.limits import MAX_STEP_INPUT_BYTES
from server.cadlink.retained_files import copy_verified_member
from server.mesh.records import get_prepared_mesh
from server.platform.acl_repair import path_has_reparse_point
from server.preview.errors import PublicMessage
from server.workspace.archive_identity import job_archive_location


UNAVAILABLE = (
    "This run's returned STEP is no longer available. "
    "Send the model again from Fusion and solve it before exporting STEP."
)
MISMATCH = (
    "The saved STEP does not match the CAD return used by this run. "
    "Send the model again from Fusion and solve it before exporting STEP."
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ReturnedStepUnavailable(ValueError):
    def __init__(self, reason: str = UNAVAILABLE):
        super().__init__(PublicMessage(reason))


def _plain_path(path: Path) -> bool:
    try:
        return not path_has_reparse_point(path, root=Path(path.anchor))
    except FileNotFoundError:
        return False


def _run_steps(folder: Path, cancel_cb: Callable[[], None]) -> Iterator[Path]:
    """Search only the run's recorded folder, never another run or project."""

    if not _plain_path(folder) or not folder.is_dir():
        return
    pending = [folder]
    while pending:
        cancel_cb()
        for path in sorted(pending.pop().iterdir()):
            if not _plain_path(path):
                continue
            if path.is_dir():
                pending.append(path)
            elif path.suffix.lower() in {".step", ".stp"}:
                yield path


def read_returned_step(
    row: Mapping[str, Any], store: Any, data_dir: Path, runs_root: Path | Callable[[], Path] | None,
    *, cancel_cb: Callable[[], None] = lambda: None,
    progress_cb: Callable[[str], None] = lambda _stage: None,
    check_only: bool = False,
) -> bytes:
    """Bind solve → prepared ingest → manifest → member checksum.

    If retention removed the manifest, the solve request still holds the
    assembly checksum admitted from it. A run-folder copy must match that
    checksum, even when both the bundle and prepared record have been pruned.
    """

    cancel_cb()
    if row.get("status") != "complete":
        raise ReturnedStepUnavailable("This run has not finished. Wait for it to complete before exporting STEP.")
    geometry = (row.get("config_json") or {}).get("geometry") or {}
    if geometry.get("type") != "imported":
        raise ReturnedStepUnavailable("This run has no returned CAD STEP. Export STEP from its parameter design instead.")
    if (row.get("config_summary_json") or {}).get("import_origin") in {"mesh-file", "design-assembly"}:
        raise ReturnedStepUnavailable("This run has no returned CAD STEP. Send a STEP model from Fusion and solve it before exporting STEP.")
    metadata = row.get("task_metadata") or {}
    imported = metadata.get("imported_geometry") or {}
    ingest_id = geometry.get("ingest_id")
    if not ingest_id or (imported.get("ingest_id") and imported["ingest_id"] != ingest_id):
        raise ReturnedStepUnavailable(MISMATCH)
    record = get_prepared_mesh(store, ingest_id) or {}
    if record.get("producer") in {"mesh-file", "design-assembly", "native"} or record.get("native_source"):
        raise ReturnedStepUnavailable("This run has no returned CAD STEP. Send a STEP model from Fusion and solve it before exporting STEP.")
    hashes = {}
    for key in ("manifest_sha256", "artifact_sha256"):
        value = geometry.get(key) or record.get(key)
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise ReturnedStepUnavailable()
        if any(source.get(key) and source[key] != value for source in (geometry, imported, record)):
            raise ReturnedStepUnavailable(MISMATCH)
        hashes[key] = value
    # Prepared geometry is the identity of the mesh actually solved, rather
    # than the document's newest return or the current viewport selection.
    for key in ("solve_model_sha256", "transformed_geometry_hash"):
        if imported.get(key) and record.get(key) and imported[key] != record[key]:
            raise ReturnedStepUnavailable(MISMATCH)
    lineage = imported.get("anchor_lineage_id")
    if lineage and (record.get("project") or {}).get("lineage_id") not in {None, lineage}:
        raise ReturnedStepUnavailable(MISMATCH)

    bundle = retained_snapshot_path(data_dir, hashes["manifest_sha256"])
    manifest_path = bundle / "wgreturn.json"
    evidence: dict[str, Any] = {"sha256": hashes["artifact_sha256"]}
    candidates: list[Path] = []
    if manifest_path.exists():
        if not _plain_path(manifest_path):
            raise ReturnedStepUnavailable(MISMATCH)
        manifest = _retained_manifest({"retained_path": bundle}, strict_io=True)
        if not manifest:
            raise ReturnedStepUnavailable(MISMATCH)
        member = (manifest.get("assembly") or {}).get("file")
        if not isinstance(member, str) or Path(member).name != member or "\\" in member:
            raise ReturnedStepUnavailable(MISMATCH)
        evidence = (manifest.get("files") or {}).get(member) or {}
        if evidence.get("sha256") != hashes["artifact_sha256"] or type(evidence.get("size_bytes")) is not int:
            raise ReturnedStepUnavailable(MISMATCH)
        candidates.append(bundle / member)
    progress_cb("locate")

    def paths() -> Iterator[Path]:
        yield from candidates
        # Resolve the effective root only after the retained return was tried.
        # A disconnected Runs drive must not block a usable retained bundle.
        previous = metadata.get("archived_path")
        folder = Path(previous) if previous and Path(previous).is_absolute() else None
        if folder is None and runs_root is not None:
            root = runs_root() if callable(runs_root) else runs_root
            location = job_archive_location(row)
            folder = root / location.folder / location.run
        if folder is not None:
            yield from _run_steps(folder, cancel_cb)

    mismatched = False
    for path in paths():
        cancel_cb()
        if not _plain_path(path) or not path.is_file():
            continue
        mismatched = True
        if path.stat().st_size > MAX_STEP_INPUT_BYTES:
            continue
        output = None if check_only else BytesIO()
        try:
            verified = copy_verified_member(path, evidence, copy_to=output, cancel_cb=cancel_cb)
        except FileNotFoundError:
            continue  # Retention may remove a candidate between lookup and open.
        if verified:
            progress_cb("verify")
            return output.getvalue() if output is not None else b""
    raise ReturnedStepUnavailable(MISMATCH if mismatched else UNAVAILABLE)
