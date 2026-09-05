"""The one-time boot sweep that repairs legacy staging ACLs.

`server/platform/acl_repair.py` explains the descriptor and does the work. This
module decides *where* to look, *when* to stop looking, and reports what it did.

## Where

Two roots, not one: the application data directory, and the run-export
workspace.

The workspace matters more, and it is the one a "repair the app's own data
directory" reading would have missed. Measured on an affected install: the data
directory held two poisoned directories, while the workspace held 53 poisoned
paths -- including every `design.json` the export refusal is about. The run
archive is written into the workspace, so that is where the damage is. It is a
folder the user chose rather than one the app owns, which is why the sweep is
bounded, refuses to follow reparse points, and only ever resets a descriptor
this application is responsible for creating.

## When

Once, and then never again -- unless something was left unrepaired.

The blocking condition is not stable across boots. A descriptor whose owner has
changed cannot be read or rewritten by an unelevated process, but the same
process started elevated can do both, because the descriptor names
Administrators. So the marker records both the outcome and whether the sweep ran
elevated, and a root is revisited only when a later run could actually reach
further than the one before it -- damage left behind *and* an elevated token
this time that the last sweep did not have.

Without that second test the rule degenerates: damage that is permanent on an
unelevated machine would make the app walk the whole workspace at every boot,
forever, to arrive at the same answer. A root that came back clean is finished
outright and costs nothing thereafter.

Nothing here is allowed to fail a startup. A repair that does not happen leaves
the export refusal exactly as it is today, which is a bad outcome; a repair that
raises would leave the user with no application at all, which is a worse one.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import stat
import time

from server.platform.acl_repair import (
    RepairCounts,
    path_has_reparse_point,
    process_is_elevated,
    sweep,
)

log = logging.getLogger("wg.acl")

__all__ = [
    "MARKER_NAME",
    "SWEEP_VERSION",
    "legacy_acl_repair_feedback",
    "repair_legacy_acls",
]

WINDOWS = os.name == "nt"
MARKER_NAME = "acl_repair_state.json"

# Bump when the matcher or the repair changes in a way that should make a
# previously "clean" root worth revisiting.
SWEEP_VERSION = 1


def _read_marker(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    roots = payload.get("roots")
    return roots if isinstance(roots, dict) else {}


def _write_marker(path: Path, roots: dict) -> None:
    try:
        path.write_text(
            json.dumps({"schemaVersion": 1, "roots": roots}, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        # Losing the marker costs a repeated sweep, not correctness.
        log.debug("Could not record the ACL repair marker at %s: %s", path, exc)


def _marker_path_is_safe(path: Path, *, data_root: Path) -> bool:
    """Whether marker I/O stays inside the already-validated data root."""

    try:
        return not path_has_reparse_point(path, root=data_root)
    except FileNotFoundError:
        # The ordinary first-run case: the leaf does not exist yet.  Its
        # validated parent is the data root and the writer creates it.
        return True
    except OSError:
        return False


def _already_finished(record: object, *, elevated_now: bool) -> bool:
    """Whether re-sweeping this root could possibly do anything new.

    A root that came back clean is finished for good. A root with damage left
    is only worth revisiting when this process could reach further than the one
    that swept it -- which means exactly one thing: an elevated token, whose
    enabled Administrators SID the staging descriptor names. Without that test
    a permanently damaged workspace would be walked in full at every boot,
    forever, to reach the same answer.
    """

    if not isinstance(record, dict):
        return False
    if record.get("version") != SWEEP_VERSION:
        return False
    counts = record.get("counts")
    if not isinstance(counts, dict):
        return False
    if counts.get("truncated"):
        # The walk stopped early, so "nothing left" was never established.
        return False
    if not (counts.get("unreadable") or counts.get("failed")):
        return True
    return not (elevated_now and not record.get("elevated", False))


def _record_counts(record: object) -> RepairCounts | None:
    """Read only a current, well-formed marker record into typed counts."""

    if not isinstance(record, dict) or record.get("version") != SWEEP_VERSION:
        return None
    if not isinstance(record.get("elevated"), bool):
        return None
    raw = record.get("counts")
    if not isinstance(raw, dict):
        return None
    values: dict[str, int] = {}
    for name in ("scanned", "repaired", "skipped", "unreadable", "failed"):
        value = raw.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        values[name] = value
    truncated = raw.get("truncated")
    if not isinstance(truncated, bool):
        return None
    return RepairCounts(**values, truncated=truncated)


def legacy_acl_repair_feedback(
    data_root: Path | str,
    workspace_root: Path | str | None,
    fresh_results: dict[str, RepairCounts],
) -> dict[str, object]:
    """Build path-free UI feedback from this sweep and its validated marker.

    A clean root skipped from a previous run stays quiet. An unresolved root
    remains visible even when the same privilege level makes another sweep
    pointless, while repairs from an earlier launch are never presented as new.
    """

    if not WINDOWS:
        return {"platform": "other", "roots": []}

    data_root = Path(data_root)
    try:
        metadata = data_root.lstat()
        if path_has_reparse_point(data_root) or not stat.S_ISDIR(metadata.st_mode):
            return {"platform": "windows", "roots": []}
    except OSError:
        return {"platform": "windows", "roots": []}

    marker_path = data_root / MARKER_NAME
    marker_safe = _marker_path_is_safe(marker_path, data_root=data_root)
    records = _read_marker(marker_path) if marker_safe else {}
    candidates: list[tuple[str, Path]] = [("appData", data_root)]
    if workspace_root is not None:
        workspace = Path(workspace_root)
        if workspace != data_root:
            candidates.append(("workspace", workspace))

    roots: list[dict[str, object]] = []
    for scope, root in candidates:
        try:
            metadata = root.lstat()
            if path_has_reparse_point(root) or not stat.S_ISDIR(metadata.st_mode):
                continue
        except OSError:
            continue

        key = str(root)
        fresh = fresh_results.get(key)
        record = records.get(key)
        counts = fresh if fresh is not None else _record_counts(record)
        if counts is None:
            continue
        remaining = counts.unreadable + counts.failed
        source = "current" if fresh is not None else "previous"
        # A previous clean result is the expected one-time marker state. It has
        # no current user action and must not repeat an old repair claim.
        if source == "previous" and remaining == 0 and not counts.truncated:
            continue
        if (
            source == "current"
            and counts.repaired == 0
            and remaining == 0
            and not counts.truncated
        ):
            continue
        elevated = (
            process_is_elevated()
            if fresh is not None
            else bool(record.get("elevated", False))
            if isinstance(record, dict)
            else False
        )
        roots.append(
            {
                "scope": scope,
                "source": source,
                "repaired": counts.repaired,
                "remaining": remaining,
                "unreadable": counts.unreadable,
                "failed": counts.failed,
                "truncated": counts.truncated,
                "administratorMayHelp": remaining if remaining and not elevated else 0,
            }
        )
    return {"platform": "windows", "roots": roots}


def repair_legacy_acls(
    data_root: Path | str,
    workspace_root: Path | str | None = None,
) -> dict[str, RepairCounts]:
    """Sweep the roots that still need it, and record the result.

    Returns the counts per root actually swept, for tests and for the caller's
    log line. Roots already finished are absent rather than reported as zero.
    """

    if not WINDOWS:
        return {}

    data_root = Path(data_root)
    try:
        # The marker is state for this repair.  Validate its root before even
        # reading it so a redirected data directory cannot move marker I/O
        # outside the application's configured tree.
        data_metadata = data_root.lstat()
        if path_has_reparse_point(data_root) or not stat.S_ISDIR(data_metadata.st_mode):
            return {}
    except OSError:
        return {}
    marker_path = data_root / MARKER_NAME
    marker_safe = _marker_path_is_safe(marker_path, data_root=data_root)
    roots = _read_marker(marker_path) if marker_safe else {}

    candidates: list[Path] = [data_root]
    if workspace_root is not None:
        workspace_root = Path(workspace_root)
        if workspace_root not in candidates:
            candidates.append(workspace_root)

    elevated = process_is_elevated()
    results: dict[str, RepairCounts] = {}
    for root in candidates:
        key = str(root)
        if _already_finished(roots.get(key), elevated_now=elevated):
            continue
        try:
            # Validate the selected root without following it.  A workspace
            # setting can itself name a junction, before ``sweep`` has a chance
            # to enforce its per-entry boundary.
            metadata = root.lstat()
            if path_has_reparse_point(root) or not stat.S_ISDIR(metadata.st_mode):
                continue
        except OSError:
            continue
        try:
            counts = sweep(root)
        except Exception as exc:  # pragma: no cover - defence, not a path
            # Deliberately broad. This runs at boot; nothing it can raise is
            # worth more than the application starting.
            log.warning("Legacy ACL repair failed for %s: %s", root, exc)
            continue
        results[key] = counts
        roots[key] = {
            "version": SWEEP_VERSION,
            "completedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elevated": elevated,
            "counts": asdict(counts),
        }
        if counts.repaired or counts.unreadable or counts.failed:
            log.info(
                "Legacy ACL repair under %s: %s%s",
                root,
                counts.as_log_fields(),
                " (walk truncated)" if counts.truncated else "",
            )
        if counts.unreadable or counts.failed:
            log.info(
                "%d path(s) under %s could not be repaired: their owner is no "
                "longer this account, so an unelevated process holds neither "
                "READ_CONTROL nor WRITE_DAC over them. Running Waveguide "
                "Generator once as an administrator repairs them; this "
                "application will not take ownership on its own.",
                counts.unreadable + counts.failed,
                root,
            )

    # Check the leaf again at the write boundary.  An existing marker symlink
    # must never redirect the migration's state update outside the data root.
    if results and marker_safe and _marker_path_is_safe(
        marker_path, data_root=data_root
    ):
        _write_marker(marker_path, roots)
    return results
