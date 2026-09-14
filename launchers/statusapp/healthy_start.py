"""Settle an update transaction on a healthy start, in every launch mode.

``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.5. Settling is two things:
``commit_transaction``, which writes the completion record and closes the
journal, and the healthy-start cleanup that is scoped to the committed
transaction (§2.5). Until a start settles, the transaction keeps ``.previous``
and the next update is refused, so a mode that never settles can update once
and never again.

This module is the one code path. The window and browser modes reach it through
``StatusController.settle_update_transaction`` -- the controller settles on the
first snapshot from its own server that satisfies the frontend-ready
predicate, and the desktop window delegates to it at the moment it has always
used, once its native event loop is running. ``--no-gui`` reaches it from
``launch/serve.py`` after a self-probe of ``/health`` and the interface route.
Every mode therefore writes the same ``update.log`` lines: the commit's and the
cleanup's when it settles, and :func:`unconfirmed_line` when it cannot.

It is never reached from ``create_app``: every mode's server runs that, and a
commit there would pre-empt the evidence the controller waits for.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import shutil
import sys
import threading

from launchers.apply_update import (
    ROLLBACK_MATERIAL_RETAINED,
    TERMINAL_JOURNAL_STATES,
    ApplyUpdateError,
    append_update_log,
    bundle_from_app_layer,
    cleanup_previous_layers,
    commit_transaction,
    journal_describes,
    journal_live_build,
    read_build_identity,
    read_completion_record,
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


def unconfirmed_line(data_dir: Path, resources: Path, reason: str) -> str | None:
    """The ``update.log`` line of a start that cannot confirm the build, or ``None``.

    ``None`` when a confirmed start would have nothing to settle -- no open
    transaction, no ``.previous``, no staging the completion record still
    retains -- because then there is nothing for a reader to act on, and an
    ordinary start whose interface was slow must not write about updates.
    """

    transaction = open_transaction(data_dir, resources)
    if transaction is None and not previous_generation_paths(resources):
        record = read_completion_record(data_dir, resources)
        if record is None or record.get("rollbackMaterial") != ROLLBACK_MATERIAL_RETAINED:
            return None
    # A reason that is itself a sentence brings its own full stop.
    reason = reason.rstrip().rstrip(".")
    return (
        f"This start did not confirm the build: {reason}. Not reclaiming the previous "
        "layers yet" + (f"; {transaction} stays open" if transaction else "") + "."
    )


def _describe_build(identity: Mapping[str, str | None]) -> str:
    if not any(identity.values()):
        return "a build with no readable APP-MANIFEST.json"
    commit = identity.get("commit")
    return (
        f"build {identity.get('version') or 'unknown'} (commit "
        f"{commit[:12] if commit else 'unknown'}, runtime {identity.get('runtimeId') or 'unknown'})"
    )


def installed_build_mismatch(data_dir: Path, resources: Path) -> str | None:
    """Why the installed app layer is not the build this installation's journal left, or ``None``.

    Contract §4.6: a healthy start needs the *expected* build serving, not
    merely some build. The expected build is the one the decided journal left
    in the app layer (:func:`journal_live_build`), compared field by field with
    the app layer's own ``APP-MANIFEST.json``; every field the journal names
    must match. ``None`` when it does, when the journal names no build (an
    older helper wrote it), and when there is no decided journal of this
    installation to compare with: ``commit_transaction`` decides those.
    """

    journal = read_journal(data_dir, resources)
    if (
        journal is None
        or not journal_describes(journal, resources)
        or str(journal.get("state") or "") not in TERMINAL_JOURNAL_STATES
    ):
        return None
    expected = journal_live_build(journal)
    if expected is None:
        return None
    installed = read_build_identity(resources / "app")
    if all(installed.get(field) == value for field, value in expected.items() if value is not None):
        return None
    transaction = str(journal.get("transaction") or "unidentified")
    return (
        f"the installed app layer is {_describe_build(installed)}, not "
        f"{_describe_build(expected)}, which update transaction {transaction} left installed"
    )


def report_unconfirmed_start(paths: BundlePaths | None, reason: str) -> bool:
    """Say in ``update.log`` that this start could not confirm the build, and why.

    Contract §4.5: a mode that cannot confirm the build never leaves the
    transaction open silently. Nothing is committed or removed. Returns whether
    a line was written.
    """

    if paths is None:
        return False
    _bundle, resources, data_dir = paths
    line = unconfirmed_line(data_dir, resources, reason)
    if line is None:
        return False
    append_update_log(data_dir, line)
    return True


def report_unconfirmed_for_arguments(
    server_args: Sequence[str], reason: str, *, environ: Mapping[str, str] | None = None
) -> bool:
    """:func:`report_unconfirmed_start` for a start that ends before it has a server.

    The data directory comes from the server's own command line, as the
    interrupted-update recovery reads it.
    """

    environment = os.environ if environ is None else environ
    if environment.get("WG2_BUNDLE") != "1":
        return False
    try:
        from server.platform.paths import app_root, resolve_data_dir

        from .updater import _data_dir_override

        paths = resolve_bundle_paths(
            environment,
            app_root(environ=environment),
            resolve_data_dir(_data_dir_override(server_args), environ=environment),
        )
    except Exception:  # noqa: BLE001 - reporting must not replace the refusal itself
        return False
    return report_unconfirmed_start(paths, reason)


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

        ``evidence`` names what was observed, for the line that declines
        (:func:`unconfirmed_line`). That line is what made the two earlier
        breakages findable: both times the whole symptom was a gigabyte that
        never came back and an ``update.log`` that stopped after "Relaunched",
        with nothing to search for.
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
                line = unconfirmed_line(data_dir, resources, evidence)
                if line is not None:
                    log(line)
                return False
            # A healthy interface of some other build confirms nothing about
            # this transaction, and nothing it would reclaim is spent.
            mismatch = installed_build_mismatch(data_dir, resources)
            if mismatch is not None:
                line = unconfirmed_line(data_dir, resources, mismatch)
                if line is not None:
                    log(line)
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
    """Remove sealed rollback content only around a required sign/verify.

    Not safe to interrupt between moving ``.previous`` out and the re-seal: a
    process that dies there leaves the bundle unsealed and the rollback
    material in the holding directory, and the next update is refused, naming
    it, until that is resolved. Callers wait for it rather than abandon it
    (contract §4.5).
    """

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
    "installed_build_mismatch",
    "open_transaction",
    "previous_generation_paths",
    "report_unconfirmed_for_arguments",
    "report_unconfirmed_start",
    "resolve_bundle_paths",
    "unconfirmed_line",
]
