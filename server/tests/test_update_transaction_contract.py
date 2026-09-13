"""Tests for ``docs/reference/UPDATE-TRANSACTION-CONTRACT.md``.

Two kinds of test live here.

**Strict expected failures** (contract §4). Each one encodes a
requirement the updater does not meet yet, and is marked
``xfail(strict=True, raises=AssertionError)``:

* While the behaviour is missing, the contract assertion fails and pytest
  reports an expected failure, so the suite stays green and nothing broken is
  committed as passing.
* When a change implements the behaviour but leaves the marker in place, the
  test passes unexpectedly and ``strict`` turns that into a failure. Remove the
  marker in the change that implements the behaviour.
* Any exception other than ``AssertionError`` is an ordinary failure. Set-up
  checks therefore use ``pytest.fail``, never ``assert``: a fixture that broke
  must not pass itself off as the behaviour that is missing.

**Regression tests** (contract §2 and §3). The completion record and the
scoped cleanup of §2 are implemented, and these keep them. An old release's
launcher runs the candidate's helper with the old command line, and that
already works; the §3 tests keep it working.
"""

from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import textwrap
import time
from typing import Any, NamedTuple
import zipfile

import pytest

from launch import serve
from launchers import apply_update as apply_update_module
from launchers import desktop
from launchers import update_lock
from launchers.apply_update import (
    begin_update_transaction,
    commit_transaction,
    installation_key,
    plan_layer_swap,
    read_journal,
    set_journal_state,
    swap_staged_layers,
)
from launchers.statusapp import __main__ as statusapp_main
from launchers.statusapp.controller import (
    LampStatus,
    ServiceState,
    StatusController,
    StatusSnapshot,
)
from server.app import create_app
from server.platform.paths import ensure_data_layout


REPOSITORY_ROOT = Path(apply_update_module.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _updater_state_stays_in_this_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No updater dialog, and no claim or grant outside this test's directory.

    The same two isolations ``test_apply_update.py`` applies, for the same
    reasons: the updater's failure channel is a modal dialog on a desktop, and
    ``update_lock`` keeps its claim and relaunch grants under the host's own
    cache directory.
    """

    monkeypatch.setattr(
        apply_update_module,
        "_show_update_failure_dialog",
        lambda message, platform_name: None,
    )
    monkeypatch.setattr(update_lock, "cache_root", lambda **_kwargs: tmp_path / "cache")


# ---------------------------------------------------------------------------
# An installed copy with one update staged, and that update swapped in
# ---------------------------------------------------------------------------


class Installation(NamedTuple):
    bundle: Path
    resources: Path
    data_dir: Path
    staged_app: Path
    staged_runtime: Path


def _write_layer(layer: Path, generation: str) -> None:
    layer.mkdir(parents=True)
    (layer / "marker.txt").write_text(generation, encoding="utf-8")
    manifest = "APP-MANIFEST.json" if layer.name == "app" else "RUNTIME-MANIFEST.json"
    (layer / manifest).write_text(
        json.dumps({"schemaVersion": 1, "version": "9.9.9", "runtimeId": generation}),
        encoding="utf-8",
    )


def _installation(tmp_path: Path, platform_name: str = "linux") -> Installation:
    """One installed copy, with an update staged where every release stages it.

    Staging is ``<data>/updates/<version>/staged/<layer>`` (``bundle.py:866``).
    The bundle is laid out for ``platform_name`` the way ``resources_directory``
    expects it.
    """

    root = tmp_path.resolve()
    if platform_name == "darwin":
        bundle = root / "Waveguide Generator.app"
        resources = bundle / "Contents" / "Resources"
    else:
        bundle = root / "Waveguide Generator"
        resources = bundle
    data_dir = root / "data"
    staged = data_dir / "updates" / "9.9.9" / "staged"
    for layer, generation in (
        (resources / "app", "old0"),
        (resources / "runtime", "old0"),
        (staged / "app", "new1"),
        (staged / "runtime", "new1"),
    ):
        _write_layer(layer, generation)
    (data_dir / "logs").mkdir(parents=True)
    return Installation(bundle, resources, data_dir, staged / "app", staged / "runtime")


def _decided_update(installation: Installation, platform_name: str = "linux") -> str:
    """Swap the staged layers in and record the transaction as installed.

    This is the state a relaunched new version starts in: the renames are done
    and decided, and only a healthy start may close the transaction and reclaim
    ``.previous``. Returns the transaction id.
    """

    planned = plan_layer_swap(
        installation.resources, installation.staged_app, installation.staged_runtime
    )
    journal = begin_update_transaction(
        data_dir=installation.data_dir,
        bundle=installation.bundle,
        resources=installation.resources,
        layers=planned,
        platform_name=platform_name,
    )
    swap_staged_layers(
        installation.resources,
        installation.staged_app,
        installation.staged_runtime,
        journal_dir=installation.data_dir,
    )
    set_journal_state(installation.data_dir, installation.resources, "installed")
    recorded = read_journal(installation.data_dir, installation.resources) or {}
    if (
        recorded.get("state") != "installed"
        or not (installation.resources / "app.previous").is_dir()
    ):
        pytest.fail(f"set-up: expected a decided update with rollback material: {recorded!r}")
    return str(journal["transaction"])


def _with_interface(app_layer: Path) -> None:
    """The built interface every launch mode checks for before it starts."""

    dist = app_layer / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")


def _update_log(installation: Installation) -> str:
    log = installation.data_dir / "logs" / "update.log"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


def _healthy_snapshot() -> StatusSnapshot:
    return StatusSnapshot(
        backend=LampStatus(ServiceState.OK, "Healthy"),
        frontend=LampStatus(ServiceState.OK, "Serving the interface"),
        url="http://127.0.0.1:3199/",
        pid=123,
        exit_code=None,
    )


# ---------------------------------------------------------------------------
# Contract §2: records and cleanup
# ---------------------------------------------------------------------------


def test_a_committed_update_leaves_a_completion_record_when_its_journal_goes(
    tmp_path: Path,
) -> None:
    """A commit deletes the journal, so it records the outcome first.

    ``commit_transaction`` removes the journal as soon as its state is terminal.
    Without the completion record nothing would hold the outcome, and WG could
    neither explain it later nor suppress a build that failed.
    """

    installation = _installation(tmp_path)
    transaction = _decided_update(installation)

    allowed, detail = commit_transaction(installation.data_dir, resources=installation.resources)
    if not allowed or read_journal(installation.data_dir, installation.resources) is not None:
        pytest.fail(f"set-up: the healthy-start commit did not close the transaction: {detail}")

    key = installation_key(installation.resources)
    record = installation.data_dir / f"update-result-{key}.json"
    assert record.is_file(), (
        f"transaction {transaction} was committed and its journal deleted, and no "
        f"completion record exists at <data>/{record.name}"
    )
    payload = json.loads(record.read_text(encoding="utf-8"))
    assert payload.get("transaction") == transaction
    assert payload.get("outcome") == "installed"
    assert payload.get("installation") == key


class _UnusedController:
    """The cleanup path never talks to the controller; ``_bundle_paths`` is replaced."""

    url = "http://127.0.0.1:3199/"


def test_healthy_start_cleanup_removes_only_the_committed_transactions_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``<data>/updates`` is shared, so healthy-start cleanup never deletes all of it.

    Both paths used to: off macOS and on it. Whichever one this host takes,
    another transaction's download must survive, and the committed
    transaction's own staging must not. ``CLEANUP_PATHS`` tests run both.
    """

    installation = _installation(tmp_path)
    _decided_update(installation)
    other = installation.data_dir / "updates" / "9.9.10" / "downloads" / "update-app-9.9.10.zip"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another transaction's download")

    window = desktop.DesktopWindow(
        _UnusedController(),  # type: ignore[arg-type]
        pythonnet_loader=lambda: object(),
        webview2_probe=lambda: True,
    )
    monkeypatch.setattr(
        window,
        "_bundle_paths",
        lambda: (installation.bundle, installation.resources, installation.data_dir),
    )
    # The macOS path reseals with codesign. What is under test here is which
    # files the cleanup removes, not the seal.
    monkeypatch.setattr(desktop, "repair_bundle", lambda *_args, **_kwargs: None)

    window._finish_healthy_bundle_update(_healthy_snapshot())

    if (installation.resources / "app.previous").exists():
        pytest.fail(
            "set-up: the healthy start did not commit and reclaim; update.log: "
            + _update_log(installation)
        )
    assert other.is_file(), (
        "healthy-start cleanup deleted a download that belongs to another transaction: "
        f"{other.relative_to(installation.data_dir).as_posix()}"
    )
    assert not (installation.data_dir / "updates" / "9.9.9").exists(), (
        "healthy-start cleanup kept the committed transaction's own staging"
    )


# ---------------------------------------------------------------------------
# Contract §2.2-§2.6 in detail: who writes the record, and what cleanup spares
# ---------------------------------------------------------------------------


def _stamp_build(layer: Path, version: str, commit: str, runtime_id: str) -> dict[str, str]:
    """Give a layer the identity a real build's ``APP-MANIFEST.json`` carries (§2.3)."""

    identity = {"version": version, "commit": commit, "runtimeId": runtime_id}
    (layer / "APP-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, **identity}), encoding="utf-8"
    )
    return identity


def _completion_record(data_dir: Path, resources: Path) -> dict[str, Any] | None:
    path = data_dir / f"update-result-{installation_key(resources)}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _signed(command: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Stands in for codesign: every macOS-shaped reseal in these tests succeeds."""

    return subprocess.CompletedProcess(command, 0, "", "")


def _healthy_start(
    installation: Installation, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """The desktop window's healthy-start commit and cleanup, on one platform's path."""

    window = desktop.DesktopWindow(
        _UnusedController(),  # type: ignore[arg-type]
        pythonnet_loader=lambda: object(),
        webview2_probe=lambda: True,
    )
    monkeypatch.setattr(
        window,
        "_bundle_paths",
        lambda: (installation.bundle, installation.resources, installation.data_dir),
    )
    monkeypatch.setattr(desktop, "repair_bundle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(desktop.sys, "platform", platform_name)
    window._finish_healthy_bundle_update(_healthy_snapshot())


#: Healthy-start cleanup has two paths, ``desktop.py``'s macOS one and the
#: other; the contract's cleanup rule holds on both.
CLEANUP_PATHS = pytest.mark.parametrize(
    "platform_name", ["darwin", "linux"], ids=["macos-path", "other-path"]
)


def _roll_back_an_update(install: ReleasedClientInstall) -> tuple[dict[str, Any], dict[str, str]]:
    """Install 2.0.0 -> 2.0.1 with this helper, and have the relaunch fail.

    The helper then rolls back on its own. Returns the journal it leaves and
    the identity of the build that failed.
    """

    _stamp_build(install.resources / "app", "2.0.0", "a" * 40, "rt")
    failed = _stamp_build(install.staged_app, "2.0.1", "b" * 40, "rt")
    confirmations = iter(["exited at once with status 1", None])
    reported: list[str] = []

    exit_code = apply_update_module.apply_update(
        bundle=install.bundle,
        data_dir=install.data_dir,
        staged_app=install.staged_app,
        staged_runtime=None,
        parent_pid=4321,
        platform_name="darwin",
        runner=_signed,
        relauncher=lambda *_args, **_kwargs: object(),
        confirm=lambda _process: next(confirmations),
        waiter=lambda _pid: True,
        failure_reporter=reported.append,
    )

    journal = read_journal(install.data_dir, install.resources)
    if exit_code != 5 or journal is None or journal.get("state") != "rolled-back":
        pytest.fail(
            f"set-up: expected a rolled-back update (exit {exit_code}, journal {journal!r}, "
            f"reported {reported!r})"
        )
    if (install.resources / "app" / "marker.txt").read_text(encoding="utf-8") != "old app":
        pytest.fail("set-up: the rollback did not restore the old app layer")
    return journal, failed


def test_a_committed_record_names_both_builds_and_the_transactions_staging(
    tmp_path: Path,
) -> None:
    """Contract §2.2: the fields, including the build identities the journal now carries."""

    installation = _installation(tmp_path)
    before = _stamp_build(installation.resources / "app", "9.9.8", "a" * 40, "old0")
    after = _stamp_build(installation.staged_app, "9.9.9", "b" * 40, "new1")
    transaction = _decided_update(installation)
    journal = read_journal(installation.data_dir, installation.resources) or {}
    assert (journal.get("fromVersion"), journal.get("toVersion")) == ("9.9.8", "9.9.9")

    allowed, detail = commit_transaction(installation.data_dir, resources=installation.resources)
    if not allowed:
        pytest.fail(f"set-up: the healthy-start commit refused: {detail}")

    record = _completion_record(installation.data_dir, installation.resources)
    assert record is not None, "no completion record after a commit"
    assert record["schema"] == 1
    assert record["installation"] == installation_key(installation.resources)
    assert record["transaction"] == transaction
    assert record["operation"] == "update"
    assert record["outcome"] == "installed"
    assert isinstance(record["detail"], str) and isinstance(record["recordedAt"], str)
    assert record["from"] == before
    assert record["to"] == after
    assert "channel" in record
    assert record["verificationBasis"] == "release-digest"
    assert record["stagingRoots"] == [str(installation.data_dir / "updates" / "9.9.9")]
    assert record["rollbackMaterial"] == "retained"
    assert record["suppressedBuilds"] == []


def test_an_automatic_rollback_is_recorded_before_an_older_release_deletes_the_journal(
    tmp_path: Path,
) -> None:
    """Contract §2.2 and §2.3: the version a rollback reopens may predate the record.

    v0.3.2's healthy start deletes a decided journal and records nothing, so
    the helper that rolls back writes the record at the moment it decides.
    """

    install = _released_client_install(tmp_path)
    journal, failed = _roll_back_an_update(install)
    # What v0.3.2's healthy start does with that journal.
    apply_update_module.remove_journal(install.data_dir, install.resources)

    record = _completion_record(install.data_dir, install.resources)
    assert record is not None, "the rollback's outcome was lost with its journal"
    assert record["transaction"] == journal["transaction"]
    assert record["operation"] == "update"
    assert record["outcome"] == "rolled-back"
    assert record["to"] == failed
    assert record["suppressedBuilds"] == [failed]
    assert record["stagingRoots"] == [str(install.data_dir / "updates" / "2.0.1")]


def test_an_abandoned_update_is_recorded_before_an_older_release_deletes_the_journal(
    tmp_path: Path,
) -> None:
    """Contract §2.2: the helper records ``aborted`` as it decides it, too.

    An update abandoned before any layer moved reopens the old version, which
    may predate the record and delete the journal without one. Nothing ran, so
    nothing is suppressed.
    """

    installation = _installation(tmp_path)
    planned = plan_layer_swap(
        installation.resources, installation.staged_app, installation.staged_runtime
    )
    journal = begin_update_transaction(
        data_dir=installation.data_dir,
        bundle=installation.bundle,
        resources=installation.resources,
        layers=planned,
        platform_name="linux",
    )
    set_journal_state(
        installation.data_dir, installation.resources, "aborted", detail="the first rename failed"
    )
    # What v0.3.2's healthy start does with that journal.
    apply_update_module.remove_journal(installation.data_dir, installation.resources)

    record = _completion_record(installation.data_dir, installation.resources)
    assert record is not None, "the abandoned update's outcome was lost with its journal"
    assert record["transaction"] == journal["transaction"]
    assert record["outcome"] == "aborted"
    assert record["suppressedBuilds"] == [], "an update that never ran suppressed its build"


def test_rolling_back_a_start_that_failed_suppresses_the_build_it_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract §2.3, for the rollback helper a failed start hands off to.

    Its transaction is a rollback, so the failed build is the one it rolls back
    *from*; and the update it undoes staged a root nothing else will name again.
    """

    install = _released_client_install(tmp_path)
    _stamp_build(install.resources / "app", "2.0.0", "a" * 40, "rt")
    failed = _stamp_build(install.staged_app, "2.0.1", "b" * 40, "rt")
    arguments = _v03x_handoff_arguments(
        bundle=install.bundle,
        data_dir=install.data_dir,
        staged_app=install.staged_app,
        staged_runtime=None,
        parent_pid=4321,
        server_args=(),
    )
    exit_code, _calls, _relaunched, failures = _run_candidate_helper(arguments, monkeypatch)
    if exit_code != 0 or failures:
        pytest.fail(f"set-up: the update did not install (exit {exit_code}): {failures}")

    result = apply_update_module.rollback_bundle(
        bundle=install.bundle,
        data_dir=install.data_dir,
        parent_pid=4321,
        platform_name="darwin",
        runner=_signed,
        relauncher=lambda *_args, **_kwargs: object(),
        waiter=lambda _pid: True,
        confirm=lambda _process: None,
    )
    journal = read_journal(install.data_dir, install.resources)
    if result != 0 or journal is None or journal.get("operation") != "rollback":
        pytest.fail(f"set-up: expected a completed rollback (exit {result}): {journal!r}")
    if journal.get("state") != "rolled-back":
        pytest.fail(f"set-up: the rollback did not finish: {journal!r}")

    record = _completion_record(install.data_dir, install.resources)
    assert record is not None, "a rollback of a failed start recorded nothing"
    assert record["transaction"] == journal["transaction"]
    assert record["operation"] == "rollback"
    assert record["outcome"] == "rolled-back"
    assert record["from"] == failed
    assert record["suppressedBuilds"] == [failed]
    assert record["stagingRoots"] == [str(install.data_dir / "updates" / "2.0.1")]


def test_recovery_records_a_journal_it_cannot_read_as_unverified(tmp_path: Path) -> None:
    """Contract §2.2: an untrusted journal that recovery removes is ``unverified``.

    Nothing the unreadable record might say is repeated as fact, and no staging
    root is taken from it.
    """

    installation = _installation(tmp_path)
    planned = plan_layer_swap(
        installation.resources, installation.staged_app, installation.staged_runtime
    )
    begin_update_transaction(
        data_dir=installation.data_dir,
        bundle=installation.bundle,
        resources=installation.resources,
        layers=planned,
        platform_name="linux",
    )
    swap_staged_layers(
        installation.resources,
        installation.staged_app,
        installation.staged_runtime,
        journal_dir=installation.data_dir,
    )
    apply_update_module.journal_path(installation.data_dir, installation.resources).write_text(
        "{ truncated", encoding="utf-8"
    )

    outcome = apply_update_module.recover_transaction(
        data_dir=installation.data_dir, resources=installation.resources, platform_name="linux"
    )
    if outcome.action != "rolled-back":
        pytest.fail(f"set-up: recovery did not restore: {outcome}")
    if read_journal(installation.data_dir, installation.resources) is not None:
        pytest.fail("set-up: recovery kept the unreadable journal")

    record = _completion_record(installation.data_dir, installation.resources)
    assert record is not None, "recovery removed a journal and recorded nothing about it"
    assert record["outcome"] == "unverified"
    assert record["transaction"] is None
    assert record["from"] is None and record["to"] is None
    assert record["stagingRoots"] == []
    assert record["suppressedBuilds"] == []


def test_a_commit_that_cannot_record_its_outcome_keeps_the_journal(tmp_path: Path) -> None:
    """Contract §2.2, durability: no record, no reclaiming."""

    installation = _installation(tmp_path)
    transaction = _decided_update(installation)
    blocked = (
        installation.data_dir / f"update-result-{installation_key(installation.resources)}.json"
    )
    blocked.mkdir()
    logged: list[str] = []

    allowed, detail = commit_transaction(
        installation.data_dir, resources=installation.resources, log=logged.append
    )

    assert allowed is False, f"the commit let the rollback material go with no record: {detail}"
    journal = read_journal(installation.data_dir, installation.resources)
    assert journal is not None and journal.get("transaction") == transaction
    assert transaction in detail
    assert any(blocked.name in line for line in logged), logged


def test_a_later_transactions_record_carries_suppressed_builds_forward(tmp_path: Path) -> None:
    """Contract §2.2: a new transaction replaces the outcome, not the suppression."""

    installation = _installation(tmp_path)
    key = installation_key(installation.resources)
    earlier_failure = {"version": "9.9.7", "commit": "c" * 40, "runtimeId": "old0"}
    (installation.data_dir / f"update-result-{key}.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "installation": key,
                "transaction": "e" * 32,
                "operation": "update",
                "outcome": "rolled-back",
                "rollbackMaterial": "reclaimed",
                "suppressedBuilds": [earlier_failure],
            }
        ),
        encoding="utf-8",
    )
    transaction = _decided_update(installation)

    allowed, detail = commit_transaction(installation.data_dir, resources=installation.resources)
    if not allowed:
        pytest.fail(f"set-up: the healthy-start commit refused: {detail}")

    record = _completion_record(installation.data_dir, installation.resources) or {}
    assert record.get("transaction") == transaction
    assert record.get("outcome") == "installed"
    assert record.get("suppressedBuilds") == [earlier_failure]


@CLEANUP_PATHS
def test_cleanup_spares_what_another_installations_journal_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """Contract §2.5: a second copy sharing the data directory keeps its staging.

    While staging is keyed by version, two copies updating to one version stage
    into one folder. The committed transaction's root is then also a root the
    other copy's open journal names, and it stays.
    """

    installation = _installation(tmp_path)
    _decided_update(installation)
    second = tmp_path.resolve() / "Second copy"
    second_staged = installation.data_dir / "updates" / "9.9.9" / "staged" / "app"
    second_staged.mkdir(parents=True)
    (second_staged / "marker.txt").write_text("the second copy's staged app", encoding="utf-8")
    apply_update_module.write_journal(
        installation.data_dir,
        second,
        {
            "schema": 1,
            "transaction": "f" * 32,
            "operation": "update",
            "state": "planned",
            "bundle": str(second),
            "resources": str(second),
            "layers": [{"name": "app", "staged": str(second_staged)}],
        },
    )

    _healthy_start(installation, monkeypatch, platform_name)

    if (installation.resources / "app.previous").exists():
        pytest.fail("set-up: the healthy start did not reclaim; " + _update_log(installation))
    assert (second_staged / "marker.txt").is_file(), (
        "cleanup removed staging another installation's open journal names"
    )


@CLEANUP_PATHS
def test_cleanup_removes_nothing_under_updates_while_another_journal_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """Contract §2.5: what an unreadable journal names cannot be ruled out."""

    installation = _installation(tmp_path)
    _decided_update(installation)
    unreadable = installation.data_dir / "update-transaction-0123456789abcdef.json"
    unreadable.write_text("{ truncated", encoding="utf-8")

    _healthy_start(installation, monkeypatch, platform_name)

    if (installation.resources / "app.previous").exists():
        pytest.fail("set-up: the healthy start did not reclaim; " + _update_log(installation))
    assert (installation.data_dir / "updates" / "9.9.9").is_dir(), (
        "cleanup removed staging while another installation's journal could not be read"
    )
    assert unreadable.name in _update_log(installation)


@CLEANUP_PATHS
def test_a_journal_naming_another_installation_reclaims_nothing_under_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """Contract §2.5: a commit that finds another installation's journal.

    This installation's last committed transaction still has staging to
    reclaim, because the start that committed it stopped before its cleanup.
    The record at this installation's name now describes another copy, so
    nothing under ``<data>/updates`` is this cleanup's to remove.
    """

    installation = _installation(tmp_path)
    transaction = _decided_update(installation)
    journal = read_journal(installation.data_dir, installation.resources) or {}
    allowed, detail = commit_transaction(installation.data_dir, resources=installation.resources)
    if not allowed:
        pytest.fail(f"set-up: the healthy-start commit refused: {detail}")
    elsewhere = tmp_path.resolve() / "Another copy"
    apply_update_module.write_journal(
        installation.data_dir,
        installation.resources,
        {
            **journal,
            "transaction": "c" * 32,
            "resources": str(elsewhere),
            "bundle": str(elsewhere),
        },
    )

    _healthy_start(installation, monkeypatch, platform_name)

    assert (installation.data_dir / "updates" / "9.9.9").is_dir()
    assert read_journal(installation.data_dir, installation.resources) is not None, (
        "the record was not left for the installation it describes"
    )
    record = _completion_record(installation.data_dir, installation.resources) or {}
    assert record.get("transaction") == transaction, (
        "another installation's transaction was recorded as this one's"
    )
    assert record.get("rollbackMaterial") == "retained"


@CLEANUP_PATHS
@pytest.mark.parametrize(
    "layout", ["the-updates-folder", "outside-updates", "a-link-out", "a-link-to-a-sibling"]
)
def test_cleanup_never_removes_the_updates_folder_or_anything_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_name: str, layout: str
) -> None:
    """Contract §2.5: a root is removed only if it resolves strictly inside ``<data>/updates``.

    And a link is never followed: one inside the folder that points at a
    sibling resolves inside it too, and would take the sibling with it.
    """

    installation = _installation(tmp_path)
    updates = installation.data_dir / "updates"
    neighbour = updates / "9.9.10" / "downloads" / "app.zip"
    neighbour.parent.mkdir(parents=True)
    neighbour.write_bytes(b"another transaction's download")
    outside = installation.data_dir / "outside"
    outside.mkdir()
    (outside / "kept.txt").write_text("not the updater's", encoding="utf-8")
    if layout == "the-updates-folder":
        staged = updates / "staged" / "app"
    elif layout == "outside-updates":
        staged = outside / "staged" / "app"
    else:
        link = updates / "9.9.11"
        target = outside if layout == "a-link-out" else neighbour.parents[1]
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"this host cannot create a directory link: {exc}")
        staged = link / "staged" / "app"
    (installation.resources / "app.previous").mkdir()
    apply_update_module.write_journal(
        installation.data_dir,
        installation.resources,
        {
            "schema": 1,
            "transaction": "d" * 32,
            "operation": "update",
            "state": "installed",
            "bundle": str(installation.bundle),
            "resources": str(installation.resources),
            "layers": [{"name": "app", "staged": str(staged)}],
        },
    )

    _healthy_start(installation, monkeypatch, platform_name)

    if (installation.resources / "app.previous").exists():
        pytest.fail("set-up: the healthy start did not reclaim; " + _update_log(installation))
    assert updates.is_dir(), "cleanup removed <data>/updates itself"
    assert neighbour.is_file(), "cleanup removed another transaction's download"
    assert (outside / "kept.txt").is_file(), "cleanup reached outside <data>/updates"


@CLEANUP_PATHS
def test_cleanup_finishes_after_an_interrupted_start_and_never_runs_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """Contract §2.5 and §2.6: the record, not ``.previous``, says what is left to reclaim.

    A start that committed and reclaimed ``.previous`` but stopped before the
    staging leaves nothing on the bundle to show that anything is pending. Once
    the cleanup has run, a later staging into the same version folder belongs
    to a later transaction.
    """

    installation = _installation(tmp_path)
    _decided_update(installation)
    download = installation.data_dir / "updates" / "9.9.9" / "downloads" / "app.zip"
    download.parent.mkdir(parents=True)
    download.write_bytes(b"spent")
    allowed, detail = commit_transaction(installation.data_dir, resources=installation.resources)
    if not allowed:
        pytest.fail(f"set-up: the healthy-start commit refused: {detail}")
    apply_update_module.cleanup_previous_layers(installation.resources)

    _healthy_start(installation, monkeypatch, platform_name)

    assert not (installation.data_dir / "updates" / "9.9.9").exists(), (
        "the committed transaction's staging outlived an interrupted cleanup"
    )
    record = _completion_record(installation.data_dir, installation.resources) or {}
    assert record.get("rollbackMaterial") == "reclaimed"

    download.parent.mkdir(parents=True)
    download.write_bytes(b"a later transaction's download")
    _healthy_start(installation, monkeypatch, platform_name)

    assert download.is_file(), "a finished cleanup ran again over a later transaction's staging"


# ---------------------------------------------------------------------------
# Contract §4.5: every launch mode settles its transaction
# ---------------------------------------------------------------------------


_FAKE_SERVER = textwrap.dedent(
    r'''
    """Answer the status controller the way launch/serve.py does, and no more."""

    import argparse
    from pathlib import Path
    import time

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--status-control", type=Path, required=True)
    args, _unknown = parser.parse_known_args()
    args.status_control.with_name("ready.json").write_text(
        '{"host":"127.0.0.1","port":%d}\n' % args.port, encoding="utf-8"
    )
    print("fake server ready", flush=True)
    while not args.status_control.is_file():
        time.sleep(0.02)
    '''
)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §4.5: only the desktop window commits the update transaction",
)
def test_a_browser_mode_start_settles_the_update_transaction(tmp_path: Path) -> None:
    """Browser mode reaches a healthy start and leaves the transaction open.

    ``commit_transaction`` has one caller, the desktop window
    (``desktop.py:671``). Browser mode runs the same ``StatusController``
    without that window, and Linux lands in it on every relaunch when Qt
    cannot open a window (``desktop.py:1778-1780``). The open transaction keeps
    ``.previous``, and the next update is then refused
    (``apply_update.py:948-950``).
    """

    installation = _installation(tmp_path, sys.platform)
    transaction = _decided_update(installation, sys.platform)
    app_layer = installation.resources / "app"
    _with_interface(app_layer)
    fake_server = tmp_path / "fake_server.py"
    fake_server.write_text(_FAKE_SERVER, encoding="utf-8")

    controller = StatusController(
        repo_root=app_layer,
        server_command=(sys.executable, str(fake_server)),
        server_args=("--data-dir", str(installation.data_dir)),
        environ={**os.environ, "WG2_BUNDLE": "1", "WG2_APP_ROOT": str(app_layer)},
        request_timeout=0.2,
        shutdown_timeout=1.0,
        request_probe=lambda url, _timeout: (
            (200, b'{"version":"test"}')
            if url.endswith("/health")
            else (200, b"<!doctype html><html><body>fake SPA</body></html>")
        ),
    )
    try:
        controller.start()
        deadline = time.monotonic() + 20.0
        snapshot = controller.poll()
        while not (
            snapshot.backend.state is ServiceState.OK
            and snapshot.frontend.state in {ServiceState.OK, ServiceState.WARNING}
        ):
            if time.monotonic() > deadline:
                pytest.fail(f"set-up: the controller never reported a healthy start: {snapshot}")
            time.sleep(0.05)
            snapshot = controller.poll()
        # One more look, as the status window's own loop takes after startup.
        controller.poll()
    finally:
        controller.close()

    journal = read_journal(installation.data_dir, installation.resources)
    assert journal is None, (
        "a browser-mode start reached a healthy backend and interface, and update "
        f"transaction {transaction} is still open in state {journal and journal.get('state')!r}"
    )


class _FakeLock:
    def acquire(self, _port: int) -> None:
        pass

    def update_port(self, _port: int) -> None:
        pass

    def release(self) -> None:
        pass


class _FakeListener:
    def close(self) -> None:
        pass


class _ServerThatNeverServed:
    """Stands in for uvicorn: it is created, serves nothing, and returns."""

    def __init__(self, _config: object) -> None:
        self.should_exit = False
        self.started = False

    def run(self, *, sockets: list[object]) -> None:
        return None


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §4.5: a --no-gui start neither confirms nor reports the transaction",
)
def test_a_no_gui_start_confirms_or_reports_the_update_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-gui`` runs the server in-process, with no controller and no window.

    Nothing on that path (``statusapp/__main__.py:493-518``) commits the
    transaction or says why it did not. The stand-in server never serves, so
    there is no evidence of a healthy start, and the only right answer is the
    report: the transaction stays open and ``update.log`` names it. Committing
    here would be the wrong fix. The positive half, a live server that confirms
    its build, needs a real server and belongs with the implementation.

    The stand-in replaces ``uvicorn.Server`` where ``launch/serve.py`` looks it
    up at call time. The interface check before the server starts reads the
    checkout's own built SPA (``FRONTEND_INDEX`` is resolved at import), which
    every suite run builds first.
    """

    installation = _installation(tmp_path, sys.platform)
    transaction = _decided_update(installation, sys.platform)
    app_layer = installation.resources / "app"
    paths = ensure_data_layout(installation.data_dir)

    monkeypatch.delenv("WG2_PORT", raising=False)
    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_APP_ROOT", str(app_layer))
    monkeypatch.setenv("WG2_DATA_DIR", str(installation.data_dir))
    monkeypatch.setenv("WG2_NO_BROWSER", "1")
    monkeypatch.setattr(serve, "ensure_data_layout", lambda: paths)
    monkeypatch.setattr(serve, "setup_logging", lambda _paths: None)
    monkeypatch.setattr(serve, "flush_logs", lambda: None)
    monkeypatch.setattr(serve, "InstanceLock", lambda _path: _FakeLock())
    monkeypatch.setattr(serve, "_release_interface_error", lambda: None)
    monkeypatch.setattr(serve, "auto_migrate_v1", lambda *_args: [])
    monkeypatch.setattr(serve, "reserve_port", lambda *_args, **_kwargs: (_FakeListener(), 3100))
    monkeypatch.setattr(serve, "create_app", lambda **_kwargs: object())
    monkeypatch.setattr(serve.uvicorn, "Server", _ServerThatNeverServed)
    monkeypatch.setattr(serve, "harden_console", lambda *_args: None)
    before = _update_log(installation)

    exit_code = statusapp_main.main(["--no-gui", "--data-dir", str(installation.data_dir)])

    if exit_code != 0:
        pytest.fail(
            f"set-up: the --no-gui start did not run (exit {exit_code}); update.log: "
            + _update_log(installation)
        )
    journal = read_journal(installation.data_dir, installation.resources)
    written = _update_log(installation)[len(before) :]
    assert journal is not None and journal.get("state") == "installed", (
        f"a --no-gui start whose server never served settled update transaction "
        f"{transaction} anyway (journal now {journal!r}); nothing proved a healthy start"
    )
    assert transaction in written, (
        f"a --no-gui start ran and exited, left update transaction {transaction} open, "
        "and wrote nothing about it to update.log"
    )


# ---------------------------------------------------------------------------
# Contract §4.2: no new installation-owned work after restart approval
# ---------------------------------------------------------------------------


RUNTIME_ID = "0123456789ab"

SOLVE_BODY: dict[str, Any] = {
    "design": {
        "formula": "OSSE",
        "L": 120,
        "a": 45,
        "simulation": {"f1": 300, "f2": 3000, "num_frequencies": 4},
    },
    "options": {"engine": "dryrun", "stage_delay_ms": 1},
}


def _zip(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, value)
    return output.getvalue()


def _app_archive(version: str) -> bytes:
    manifest = {
        "schemaVersion": 1,
        "version": version,
        "commit": "a" * 40,
        "runtimeId": RUNTIME_ID,
    }
    return _zip(
        {
            "APP-MANIFEST.json": json.dumps(manifest).encode(),
            "launchers/apply_update.py": b"# staged updater\n",
        }
    )


async def _post(app: Any, path: str, body: dict[str, Any]) -> tuple[int, bytes]:
    """One loopback POST straight through the ASGI app, as ``test_jobs_api.py`` sends them."""

    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {
                "type": "http.request",
                "body": json.dumps(body).encode(),
                "more_body": False,
            }
        # A live connection blocks here until the client goes away; answering
        # with a disconnect instead can cancel the response before it starts.
        await asyncio.Event().wait()
        raise RuntimeError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"127.0.0.1:3100"), (b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 3100),
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    response = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return start["status"], response


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="contract §4.2: nothing refuses new work once a restart has been approved",
)
def test_a_solve_submitted_after_restart_approval_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A solve that starts after approval is ended by a restart it never heard of.

    Approval is the moment the handoff request is written (contract §4.1). The
    install route has no job check (``updates/api.py:70-87``), and neither has
    ``/api/solve`` (``jobs/api.py:351``).
    """

    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    request_path = tmp_path / "control" / "update.json"
    archive = _app_archive("2.0.1")
    digest = hashlib.sha256(archive).hexdigest()
    name = "update-app-2.0.1.zip"
    base = "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/"

    def download(_url: str, destination: Path, _limit: int, progress: Any) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive)
        progress(len(archive))

    async def scenario() -> tuple[int, bytes]:
        app = create_app(data_dir=tmp_path / "data", update_request_path=request_path)
        installer = app.state.update_service.bundle_installer
        if installer is None:
            pytest.fail("set-up: an app given an update request path has no bundle installer")
        # The app's own installer, with the network and the disk probes replaced;
        # everything from the checksum to writing the handoff request is real.
        installer.downloader = download
        installer.small_fetcher = lambda _url, _limit: f"{digest}  {name}\n".encode()
        installer.volume_probe = lambda _path: "one volume"
        installer.free_space_probe = lambda _path: 10**12
        installer.start(
            "2.0.1",
            [
                {
                    "name": name,
                    "url": base + name,
                    "sha256Url": base + name + ".sha256",
                    "bytes": len(archive),
                    "layer": "app",
                }
            ],
            expected_runtime_id=RUNTIME_ID,
            installed_runtime_id=RUNTIME_ID,
        )
        deadline = time.monotonic() + 20.0
        while (state := installer.status()["installState"]) not in {"ready", "failed"}:
            if time.monotonic() > deadline:
                pytest.fail(f"set-up: staging never finished: {installer.status()}")
            await asyncio.sleep(0.02)
        if state != "ready" or not request_path.is_file():
            pytest.fail(f"set-up: the restart was never approved: {installer.status()}")

        runtime = app.state.jobs_runtime
        try:
            return await _post(app, "/api/solve", SOLVE_BODY)
        finally:
            await runtime.wait_idle()
            await runtime.shutdown()

    status, raw = asyncio.run(scenario())

    assert status == 409, (
        f"a solve was accepted (HTTP {status}) after the update's restart was approved "
        f"and its handoff request written: {raw[:200]!r}"
    )
    assert json.loads(raw).get("error", {}).get("code") == "update_restart_pending"


# ---------------------------------------------------------------------------
# Contract §3: an old release's launcher runs this checkout's helper
# ---------------------------------------------------------------------------


def _v03x_handoff_arguments(
    *,
    bundle: Path,
    data_dir: Path,
    staged_app: Path,
    staged_runtime: Path | None,
    parent_pid: int,
    server_args: tuple[str, ...],
) -> list[str]:
    """What the v0.3.1 and v0.3.2 launchers pass the staged helper.

    Frozen from ``launch_bundle_update_handoff`` in
    ``launchers/statusapp/updater.py`` at both tags: the interpreter, then
    ``<staged app>/launchers/apply_update.py``, then these, in this order.
    ``test_a_released_launcher_hands_the_candidate_a_command_it_accepts``
    rebuilds it from the released code wherever the tags are reachable.
    """

    arguments = [
        "--bundle",
        str(bundle),
        "--data-dir",
        str(data_dir),
        "--staged-app-dir",
        str(staged_app),
        "--parent-pid",
        str(parent_pid),
    ]
    if staged_runtime is not None:
        arguments += ["--staged-runtime-dir", str(staged_runtime)]
    arguments += [f"--relaunch-arg={argument}" for argument in server_args]
    return arguments


class ReleasedClientInstall(NamedTuple):
    bundle: Path
    resources: Path
    data_dir: Path
    staged_app: Path
    staged_runtime: Path


def _released_client_install(tmp_path: Path) -> ReleasedClientInstall:
    """A macOS-shaped install with an update staged where v0.3.x stages it.

    macOS-shaped on every host, as in ``test_apply_update.py``: the reseal runs
    through an injected runner, so no codesign runs anywhere.
    """

    root = tmp_path.resolve()
    bundle = root / "Waveguide Generator.app"
    resources = bundle / "Contents" / "Resources"
    data_dir = root / "data"
    staged_app = data_dir / "updates" / "2.0.1" / "staged" / "app"
    staged_runtime = staged_app.parent / "runtime"
    for path, marker in (
        (resources / "app", "old app"),
        (resources / "runtime", "old runtime"),
        (staged_app, "new app"),
        (staged_runtime, "new runtime"),
    ):
        path.mkdir(parents=True)
        (path / "marker.txt").write_text(marker, encoding="utf-8")
    # What an old launcher checks for before it starts anything.
    (staged_app / "launchers").mkdir()
    (staged_app / "launchers" / "apply_update.py").write_text(
        "# the candidate's helper\n", encoding="utf-8"
    )
    (staged_runtime / "bin").mkdir()
    (staged_runtime / "bin" / "python3.13").write_text("python\n", encoding="utf-8")
    return ReleasedClientInstall(bundle, resources, data_dir, staged_app, staged_runtime)


def _run_candidate_helper(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> tuple[int, list[dict[str, Any]], list[list[str]], list[str]]:
    """Run this checkout's ``apply_update.main`` on an old launcher's command line.

    ``main`` parses the arguments exactly as the staged script would. The
    transaction it starts is the real one, with the platform fixed to macOS and
    the process-level effects (waiting for the parent, codesign, the relaunch)
    injected.
    """

    real_apply_update = apply_update_module.apply_update
    calls: list[dict[str, Any]] = []
    relaunched: list[list[str]] = []
    failures: list[str] = []

    def apply_update_without_process_effects(**from_main: Any) -> int:
        calls.append(dict(from_main))
        return real_apply_update(
            **from_main,
            platform_name="darwin",
            runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
            relauncher=lambda command, platform_name, **_kwargs: relaunched.append(
                [str(part) for part in command]
            ),
            waiter=lambda _pid: True,
            failure_reporter=failures.append,
        )

    monkeypatch.setattr(apply_update_module, "apply_update", apply_update_without_process_effects)
    return apply_update_module.main(arguments), calls, relaunched, failures


@pytest.mark.parametrize("with_runtime", [True, False], ids=["app-and-runtime", "app-only"])
def test_the_candidate_helper_installs_from_a_v03x_launchers_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_runtime: bool
) -> None:
    """Contract §3.2: the old launcher runs the candidate's helper.

    So the helper owns compatibility with the old command line and the
    version-keyed staging layout under the data directory.
    """

    install = _released_client_install(tmp_path)
    staged_runtime = install.staged_runtime if with_runtime else None
    arguments = _v03x_handoff_arguments(
        bundle=install.bundle,
        data_dir=install.data_dir,
        staged_app=install.staged_app,
        staged_runtime=staged_runtime,
        parent_pid=4321,
        server_args=("--port", "3110"),
    )

    exit_code, calls, relaunched, failures = _run_candidate_helper(arguments, monkeypatch)

    assert failures == []
    assert exit_code == 0
    # Only what the old command line determines; main may pass more.
    from_command_line = {
        "bundle": install.bundle,
        "data_dir": install.data_dir,
        "staged_app": install.staged_app,
        "staged_runtime": staged_runtime,
        "parent_pid": 4321,
        "relaunch_arguments": ["--port", "3110"],
    }
    assert len(calls) == 1
    assert {key: calls[0].get(key) for key in from_command_line} == from_command_line
    installed = install.resources
    assert (installed / "app" / "marker.txt").read_text(encoding="utf-8") == "new app"
    assert (installed / "app.previous" / "marker.txt").read_text(encoding="utf-8") == "old app"
    runtime_marker = (installed / "runtime" / "marker.txt").read_text(encoding="utf-8")
    assert runtime_marker == ("new runtime" if with_runtime else "old runtime")
    assert len(relaunched) == 1


_RELEASED_LAUNCHER_DRIVER = textwrap.dedent(
    '''
    """Run one released launcher's handoff, from that release's own files."""

    import importlib.util
    import json
    from pathlib import Path
    import subprocess
    import sys

    tree, request_path, data_dir, app_layer = (Path(argument) for argument in sys.argv[1:5])
    sys.path.insert(0, str(tree))
    spec = importlib.util.spec_from_file_location(
        "released_statusapp_updater", tree / "launchers" / "statusapp" / "updater.py"
    )
    updater = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = updater
    spec.loader.exec_module(updater)
    for name in ("launchers.apply_update", "server.platform.paths"):
        origin = Path(sys.modules[name].__file__).resolve()
        if not origin.is_relative_to(tree.resolve()):
            raise SystemExit(f"{name} came from {origin}, not from the released tree")

    request = updater.consume_update_request(request_path, data_dir=data_dir)
    if not isinstance(request, updater.BundleUpdateRequest):
        raise SystemExit(f"the released launcher did not accept the request: {request!r}")
    started = []
    subprocess.Popen = lambda command, **_options: started.append([str(p) for p in command])
    updater.launch_bundle_update_handoff(
        app_layer,
        request,
        4321,
        environ={"WG2_DATA_DIR": str(data_dir)},
        server_args=("--port", "3110"),
        platform_name="darwin",
    )
    print(json.dumps(started[0]))
    '''
)


def _released_tree(tag: str, destination: Path) -> Path:
    """The launcher side of a release, extracted from its tag."""

    try:
        archive = subprocess.run(  # noqa: S603 - fixed program and arguments
            [  # noqa: S607 - git from PATH, as every repository script finds it
                "git",
                "-C",
                str(REPOSITORY_ROOT),
                "archive",
                "--format=tar",
                tag,
                "launchers",
                "shared",
                "server/__init__.py",
                "server/platform",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"git could not read {tag} here ({exc}); the frozen command line is tested")
    if archive.returncode != 0:
        pytest.skip(
            f"{tag} is not reachable from this checkout (a CI checkout has one commit and "
            "no tags); the frozen command line is still tested"
        )
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as released:
        released.extractall(destination, filter="data")
    return destination


@pytest.mark.parametrize("tag", ["v0.3.1", "v0.3.2"])
def test_a_released_launcher_hands_the_candidate_a_command_it_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: str
) -> None:
    """Contract §3.1 and §3.2, end to end, with the released launcher's own code.

    The released launcher consumes the schema-1 request its own server wrote
    and builds the helper's command line; this checkout's helper then installs
    from it.
    """

    tree = _released_tree(tag, tmp_path / "released")
    install = _released_client_install(tmp_path / "install")
    request = tmp_path.resolve() / "control" / "update.json"
    request.parent.mkdir(parents=True)
    # What server/updates/bundle.py writes at both tags.
    request.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "apply_bundle",
                "version": "2.0.1",
                "stagedAppDir": str(install.staged_app),
                "stagedRuntimeDir": str(install.staged_runtime),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    driver = tmp_path / "released_launcher_driver.py"
    driver.write_text(_RELEASED_LAUNCHER_DRIVER, encoding="utf-8")

    completed = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
        [
            sys.executable,
            str(driver),
            str(tree),
            str(request),
            str(install.data_dir),
            str(install.resources / "app"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    command = json.loads(completed.stdout.strip().splitlines()[-1])
    assert command[1] == str(install.staged_app / "launchers" / "apply_update.py")
    assert command[2:] == _v03x_handoff_arguments(
        bundle=install.bundle,
        data_dir=install.data_dir,
        staged_app=install.staged_app,
        staged_runtime=install.staged_runtime,
        parent_pid=4321,
        server_args=("--port", "3110"),
    )
    assert not request.exists()

    exit_code, _calls, relaunched, failures = _run_candidate_helper(command[2:], monkeypatch)

    assert failures == []
    assert exit_code == 0
    installed = install.resources
    assert (installed / "app" / "marker.txt").read_text(encoding="utf-8") == "new app"
    assert (installed / "runtime" / "marker.txt").read_text(encoding="utf-8") == "new runtime"
    assert len(relaunched) == 1


_RELEASED_COMMIT_DRIVER = textwrap.dedent(
    '''
    """Read and commit a journal with one release's own apply_update."""

    import importlib.util
    import json
    from pathlib import Path
    import sys

    tree, data_dir, resources = (Path(argument) for argument in sys.argv[1:4])
    sys.path.insert(0, str(tree))
    spec = importlib.util.spec_from_file_location(
        "released_apply_update", tree / "launchers" / "apply_update.py"
    )
    released = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = released
    spec.loader.exec_module(released)

    journal = released.read_journal(data_dir, resources)
    allowed, detail = released.commit_transaction(data_dir, resources=resources)
    state = None if journal is None else journal.get("state")
    print(json.dumps({"state": state, "allowed": allowed, "detail": detail}))
    '''
)


def test_the_v032_reader_accepts_this_helpers_journal_and_its_commit_keeps_the_record(
    tmp_path: Path,
) -> None:
    """Contract §2.2 and §3.3, with v0.3.2's own code.

    A rollback reopens the old version, and v0.3.2 is the oldest journal
    reader. It must accept the optional keys this helper adds, and its commit,
    which deletes the journal and knows nothing of the record, must leave the
    record in place.
    """

    tree = _released_tree("v0.3.2", tmp_path / "released")
    install = _released_client_install(tmp_path / "install")
    journal, _failed = _roll_back_an_update(install)
    driver = tmp_path / "released_commit_driver.py"
    driver.write_text(_RELEASED_COMMIT_DRIVER, encoding="utf-8")

    completed = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script
        [sys.executable, str(driver), str(tree), str(install.data_dir), str(install.resources)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["state"] == "rolled-back", (
        f"v0.3.2 did not accept this helper's journal: {result}"
    )
    assert result["allowed"] is True, result
    assert read_journal(install.data_dir, install.resources) is None
    record = _completion_record(install.data_dir, install.resources)
    assert record is not None, "the record did not survive v0.3.2's commit"
    assert record["transaction"] == journal["transaction"]
    assert record["outcome"] == "rolled-back"
