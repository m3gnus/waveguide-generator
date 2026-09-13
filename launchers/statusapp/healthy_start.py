"""Settle an update transaction on a healthy start, in every launch mode.

``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.5. Settling is two things:
``commit_transaction``, which writes the completion record and closes the
journal, and the healthy-start cleanup that is scoped to the committed
transaction (§2.5). Until a start settles, the transaction keeps ``.previous``
and the next update is refused, so a mode that never settles can update once
and never again.

This module is the one code path. The window and browser modes reach it through
``StatusController.settle_update_transaction`` -- the controller settles on the
first snapshot that satisfies the frontend-ready predicate, and the desktop
window delegates to it at the moment it has always used, once its native event
loop is running. ``--no-gui`` reaches it from ``launch/serve.py`` after a
self-probe of ``/health`` and the interface route. Every mode therefore writes
the same ``update.log`` lines.

It is never reached from ``create_app``: every mode's server runs that, and a
commit there would pre-empt the evidence the controller waits for.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import shutil
import sys
import threading

from launchers.apply_update import (
    ApplyUpdateError,
    append_update_log,
    bundle_from_app_layer,
    cleanup_previous_layers,
    commit_transaction,
    journal_describes,
    read_journal,
    reclaim_committed_staging,
    repair_bundle,
    resources_directory,
)


#: ``(bundle, resources, data directory)`` of an installed bundle.
BundlePaths = tuple[Path, Path, Path]
Report = Callable[[str], None]


def resolve_bundle_paths(
    environ: Mapping[str, str], app_layer: str | os.PathLike[str], data_dir: str | os.PathLike[str]
) -> BundlePaths | None:
    """The installation around ``app_layer``, or ``None`` when this is not a bundle.

    A source checkout has no swappable layers and no transaction to settle.
    """

    if environ.get("WG2_BUNDLE") != "1":
        return None
    try:
        layer = Path(app_layer).resolve()
        bundle = bundle_from_app_layer(layer, sys.platform)
        data = Path(data_dir).resolve()
    except (ApplyUpdateError, OSError, RuntimeError, TypeError, ValueError):
        return None
    return bundle, resources_directory(bundle, sys.platform), data


def previous_generation_paths(resources: Path) -> list[Path]:
    """Return layer and launcher backups that belong to one pending update."""

    paths = [
        path
        for name in ("app.previous", "runtime.previous")
        if ((path := resources / name).exists() or path.is_symlink())
    ]
    paths.extend(
        path
        for path in sorted(resources.glob("*.previous"))
        if path not in paths and (path.is_file() or path.is_symlink())
    )
    return paths


def cleanup_holding_directory(bundle: Path) -> Path:
    """Where the macOS path holds ``.previous`` while it re-seals the bundle."""

    return bundle.with_name(f".{bundle.name}.update-rollback")


def open_transaction(data_dir: Path, resources: Path) -> str | None:
    """Name this installation's open update transaction, or ``None`` when there is none."""

    journal = read_journal(data_dir, resources)
    if journal is None or not journal_describes(journal, resources):
        return None
    identifier = str(journal.get("transaction") or "unidentified")
    return f"update transaction {identifier} (state {str(journal.get('state') or '')!r})"


def report_unconfirmed_start(paths: BundlePaths | None, reason: str) -> bool:
    """Say in ``update.log`` that this start could not confirm the build, and why.

    Contract §4.5: a mode that cannot confirm the build never leaves the
    transaction open silently. Nothing is committed or removed. Returns whether
    there was an open transaction to report.
    """

    if paths is None:
        return False
    _bundle, resources, data_dir = paths
    transaction = open_transaction(data_dir, resources)
    if transaction is None:
        return False
    append_update_log(
        data_dir,
        f"This start did not confirm {transaction}: {reason}. The transaction stays open "
        "and its rollback material is kept until a start confirms the build.",
    )
    return True


def _report_to_the_user(message: str) -> None:
    """The default failure channel: the status application's own reporter.

    Imported here, not at module level: ``__main__`` is the entry point that
    imports the controller, and this module is imported by the controller.
    """

    from launchers.statusapp.__main__ import _report_startup_failure

    _report_startup_failure(message)


class HealthyStartSettlement:
    """Commit one start's update transaction and reclaim what it spent, once.

    ``paths`` is asked at settle time, not at construction, because the
    controller that owns this can be built before its data directory is final.
    """

    def __init__(self, paths: Callable[[], BundlePaths | None]) -> None:
        self._paths = paths
        self._lock = threading.Lock()
        self._settled = False

    @property
    def settled(self) -> bool:
        with self._lock:
            return self._settled

    def settle(self, *, ready: bool, evidence: str, report: Report | None = None) -> bool:
        """Settle when ``ready``; otherwise say why not. Returns whether it is settled.

        ``evidence`` names what was observed, for the line that declines. It is
        what made the two earlier breakages findable: both times the whole
        symptom was a gigabyte that never came back and an ``update.log`` that
        stopped after "Relaunched", with nothing to search for.
        """

        with self._lock:
            if self._settled:
                return True
            paths = self._paths()
            if paths is None:
                return False
            bundle, resources, data_dir = paths

            def log(message: str) -> None:
                append_update_log(data_dir, message)

            if not ready:
                transaction = open_transaction(data_dir, resources)
                log(
                    f"Not reclaiming the previous layers yet: {evidence}."
                    + (f" {transaction[0].upper()}{transaction[1:]} stays open." if transaction else "")
                )
                return False
            # A healthy interface is the only evidence that an update worked.
            # Close the transaction here, and refuse to reclaim anything while
            # one is still open: a ``.previous`` removed under an undecided
            # transaction is the rollback material for a failure nobody has
            # ruled out yet.
            committed, commit_detail = commit_transaction(data_dir, resources=resources, log=log)
            if not committed:
                log(f"Not reclaiming the previous layers: {commit_detail}.")
                return False
            self._settled = True
            if commit_detail.startswith("update transaction"):
                log(f"Healthy start: {commit_detail}.")
            _reclaim(bundle, resources, data_dir, log, report or _report_to_the_user)
            return True


def _reclaim(
    bundle: Path, resources: Path, data_dir: Path, log: Report, report: Report
) -> None:
    previous = previous_generation_paths(resources)
    if sys.platform == "darwin":
        if not previous:
            # Nothing to reseal around. A start that stopped part-way through
            # this cleanup may still have left the committed transaction's
            # staging; its completion record says so.
            reclaim_committed_staging(data_dir, resources, log=log)
            return
        _reclaim_macos(bundle, resources, data_dir, previous, log, report)
        return

    # ``.failed`` is the trail a rollback leaves: Windows would not let the
    # helper delete a directory whose DLLs were still mapped, so the deletion
    # was deferred to exactly here, where nothing holds them and the download
    # that produced them is equally spent.
    had_previous = any(
        (resources / f"{name}{suffix}").exists()
        for name in ("app", "runtime")
        for suffix in (".previous", ".failed")
    )
    try:
        cleanup_previous_layers(resources, log=log)
    except OSError as exc:
        log(f"Could not remove healthy-start rollback layers: {exc}")
    finally:
        if had_previous:
            repair_bundle(bundle, platform_name=sys.platform, log=log)
        # The staged layers moved into the bundle; what is left of the
        # committed transaction's staging is its downloaded archives (the
        # runtime zip alone is well over 100 MB). Only that transaction's own
        # folders go: <data>/updates is shared with other transactions and
        # other installations, so it is never removed whole.
        reclaim_committed_staging(data_dir, resources, log=log)


def _restore_held_previous(holding: Path, moved: list[tuple[Path, Path]]) -> list[str]:
    errors: list[str] = []
    for original, saved in reversed(moved):
        try:
            if (saved.exists() or saved.is_symlink()) and not (
                original.exists() or original.is_symlink()
            ):
                os.replace(saved, original)
        except OSError as exc:
            errors.append(f"{original}: {exc}")
    try:
        holding.rmdir()
    except FileNotFoundError:
        pass
    except OSError as exc:
        if holding.exists():
            errors.append(f"{holding}: {exc}")
    return errors


def _reclaim_macos(
    bundle: Path,
    resources: Path,
    data_dir: Path,
    previous: list[Path],
    log: Report,
    report: Report,
) -> None:
    """Remove sealed rollback content only around a required sign/verify."""

    holding = cleanup_holding_directory(bundle)
    if holding.exists() or holding.is_symlink():
        message = (
            f"Waveguide Generator could not finish update cleanup because recovery material "
            f"already exists at {holding}. The current version remains open and rollback "
            "material was retained."
        )
        log(message)
        report(message)
        return

    moved: list[tuple[Path, Path]] = []
    try:
        holding.mkdir()
        for original in previous:
            saved = holding / original.name
            os.replace(original, saved)
            moved.append((original, saved))
    except OSError as exc:
        restore_errors = _restore_held_previous(holding, moved)
        if moved and not restore_errors:
            try:
                repair_bundle(bundle, platform_name="darwin", log=log)
            except ApplyUpdateError as repair_exc:
                restore_errors.append(str(repair_exc))
        detail = "; ".join(restore_errors) if restore_errors else "rollback material restored"
        message = (
            f"Waveguide Generator could not stage healthy-update cleanup: {exc}. "
            f"Recovery result: {detail}."
        )
        log(message)
        report(message)
        return
    try:
        repair_bundle(bundle, platform_name="darwin", log=log)
    except ApplyUpdateError as exc:
        restore_errors = _restore_held_previous(holding, moved)
        repair_error: ApplyUpdateError | None = None
        if not restore_errors:
            try:
                repair_bundle(bundle, platform_name="darwin", log=log)
            except ApplyUpdateError as restored_exc:
                repair_error = restored_exc
        if restore_errors:
            outcome = "Rollback material could not be fully restored: " + "; ".join(
                restore_errors
            )
        elif repair_error is not None:
            outcome = (
                "Rollback material was restored, but the restored bundle also failed "
                f"signature verification: {repair_error}"
            )
        else:
            outcome = "Rollback material was restored and the current version remains open."
        message = f"Waveguide Generator could not verify healthy-update cleanup: {exc}. {outcome}"
        log(message)
        report(message)
        return

    try:
        shutil.rmtree(holding)
    except OSError as exc:
        # The holding directory is outside the signed bundle. Failure to remove
        # it wastes space but cannot invalidate the verified app.
        log(f"Could not remove obsolete update rollback material {holding}: {exc}")
    for original in previous:
        kind = "layer" if original.name in {"app.previous", "runtime.previous"} else "launcher file"
        log(f"Removed healthy-start rollback {kind}: {original}")
    # Only the committed transaction's own staging, as on the other path.
    reclaim_committed_staging(data_dir, resources, log=log)


__all__ = [
    "BundlePaths",
    "HealthyStartSettlement",
    "cleanup_holding_directory",
    "open_transaction",
    "previous_generation_paths",
    "report_unconfirmed_start",
    "resolve_bundle_paths",
]
