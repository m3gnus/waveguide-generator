"""Release status, cache, checkout and API contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
import pytest

from server.updates.api import mount_updates
from server.updates.restart import RestartApproval
from server.updates.service import (
    INCOMPLETE_TTL_SECONDS,
    ReleaseResponse,
    UpdateInstallUnavailable,
    UpdateRateLimitError,
    UpdateService,
    checkout_status,
    update_action,
)


def release(version: str, *, ready: bool = True) -> dict[str, Any]:
    archive = f"update-spa-{version}.tar.gz"
    assets = [
        {"name": archive, "state": "uploaded", "size": 100},
        {"name": f"{archive}.sha256", "state": "uploaded", "size": 64},
    ]
    if not ready:
        assets[1]["state"] = "new"
        assets[1]["size"] = 0
    return {
        "tag_name": f"v{version}",
        "published_at": "2026-08-11T12:00:00Z",
        "assets": assets,
    }


def safe_checkout(_root: Path, _version: str) -> dict[str, Any]:
    return {
        "kind": "release",
        "branch": "main",
        "head": "a" * 40,
        "atDeclaredTag": True,
        "trackedChanges": False,
        "aheadCount": 0,
        "behindCount": 0,
        "updateSupported": True,
        "reason": None,
    }


def service(tmp_path: Path, fetcher, now: list[float], **kwargs: Any) -> UpdateService:
    return UpdateService(
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=fetcher,
        clock=lambda: now[0],
        checkout_probe=safe_checkout,
        **kwargs,
    )


def test_newer_ready_release_produces_an_exact_absolute_update_command(tmp_path: Path):
    now = [1_700_000_000.0]
    update = service(
        tmp_path,
        lambda _etag: ReleaseResponse(release("2.0.1"), '"release-1"'),
        now,
        platform_name="darwin",
    )

    result = update.get_status()

    assert result["availability"] == "available"
    assert result["freshness"] == "fresh"
    assert result["release"] == {
        "version": "2.0.1",
        "tag": "v2.0.1",
        "url": "https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1",
        "publishedAt": "2026-08-11T12:00:00Z",
        "assetsReady": True,
    }
    assert result["action"]["command"].startswith("bash ")
    assert str(tmp_path.resolve()) in result["action"]["command"]
    assert result["action"]["command"].endswith(" --tag v2.0.1")
    assert (tmp_path / "data" / "cache" / "update-status.json").is_file()
    assert result["canInstall"] is False


@pytest.mark.parametrize(
    ("field", "corrupt_value"),
    [
        ("release.version", "not-a-version"),
        ("nextCheckEpoch", "tomorrow"),
        ("failureCount", "many"),
    ],
)
def test_any_corrupt_cached_field_discards_the_whole_cache(
    tmp_path: Path, field: str, corrupt_value: object
) -> None:
    now = [1_700_000_000.0]
    cached_release = {
        "version": "2.0.1",
        "tag": "v2.0.1",
        "url": "https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1",
        "publishedAt": "2026-08-11T12:00:00Z",
        "assetsReady": True,
    }
    cache: dict[str, Any] = {
        "schemaVersion": 1,
        "etag": '"stale"',
        "release": cached_release,
        "availability": "available",
        "lastAttemptEpoch": now[0] - 10,
        "checkedAtEpoch": now[0] - 10,
        "nextCheckEpoch": now[0] + 10_000,
        "failureCount": 0,
        "lastError": None,
    }
    if field == "release.version":
        cached_release["version"] = corrupt_value
    else:
        cache[field] = corrupt_value
    cache_path = tmp_path / "data" / "cache" / "update-status.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    etags: list[str | None] = []

    def fetch(etag: str | None) -> ReleaseResponse:
        etags.append(etag)
        return ReleaseResponse(release("2.0.0"), None)

    result = service(tmp_path, fetch, now).get_status()

    assert result["availability"] == "current"
    assert result["cached"] is False
    assert etags == [None]


def test_ready_release_can_signal_the_status_owner_for_installation(tmp_path: Path):
    now = [1_700_000_000.0]
    request_path = tmp_path / "control" / "update.json"
    request_path.parent.mkdir()
    update = service(
        tmp_path,
        lambda _etag: ReleaseResponse(release("2.0.1"), None),
        now,
        platform_name="darwin",
        update_request_path=request_path,
    )

    assert update.get_status()["canInstall"] is True
    result = update.request_install()
    _wait_for(request_path)
    payload = json.loads(request_path.read_text(encoding="utf-8"))

    assert result == {"accepted": True, "tag": "v2.0.1"}
    assert payload["schemaVersion"] == 1
    assert payload["kind"] == "install_release"
    assert payload["tag"] == "v2.0.1"
    # Due on arrival: the delay was served on the server's monotonic clock.
    assert payload["readyAtEpoch"] == 0


def _wait_for(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            pytest.fail(f"set-up: {path.name} never appeared")
        time.sleep(0.01)


def test_a_checkout_request_is_due_on_arrival_whatever_the_wall_clocks_say(tmp_path: Path):
    """Contract §4.2 (the review's U12): readiness is on the monotonic clock.

    The request used to carry the server's wall-clock time plus 0.75 s, and the
    launcher compared that with its own wall clock. A launcher clock behind the
    server's, or a step in either, held the request for as long -- past the
    approval's expiry, so WG could restart after saying it would not. Now the
    server serves the delay itself, on its monotonic clock, before the request
    appears, and the request is due on arrival.
    """

    from launchers.statusapp.updater import consume_update_request

    now = [1_700_000_000.0]
    request_path = tmp_path / "control" / "update.json"
    request_path.parent.mkdir()
    update = service(
        tmp_path,
        lambda _etag: ReleaseResponse(release("2.0.1"), None),
        now,
        platform_name="darwin",
        update_request_path=request_path,
    )

    started = time.monotonic()
    assert update.request_install() == {"accepted": True, "tag": "v2.0.1"}
    # The HTTP answer goes out before the request appears.
    assert not request_path.exists()
    _wait_for(request_path)
    assert time.monotonic() - started >= update.checkout_handoff_delay - 0.05

    # A launcher whose wall clock is ten minutes behind the server's takes it at once.
    assert consume_update_request(request_path, now=time.time() - 600) == "v2.0.1"


def test_an_expired_checkout_restart_revokes_its_request(tmp_path: Path):
    """Contract §4.2: an expiry takes the request back before it says "did not start".

    And once the launcher has taken the request, an expiry says nothing of the
    kind: a handoff is under way.
    """

    from launchers.statusapp.updater import consume_update_request

    now = [1_700_000_000.0]
    ticks = [1000.0]
    request_path = tmp_path / "control" / "update.json"
    request_path.parent.mkdir()
    update = service(
        tmp_path,
        lambda _etag: ReleaseResponse(release("2.0.1"), None),
        now,
        platform_name="darwin",
        update_request_path=request_path,
        restart_approval=RestartApproval(ttl=300.0, clock=lambda: ticks[0]),
    )

    update.request_install()
    _wait_for(request_path)
    ticks[0] += 301.0
    shown = update.get_status()

    assert shown["installState"] == "failed", shown
    assert "did not start" in str(shown["error"])
    assert not request_path.exists()
    assert request_path.with_name("update.json.revoked").is_file()
    assert consume_update_request(request_path) is None

    update.request_install()
    _wait_for(request_path)
    assert consume_update_request(request_path) == "v2.0.1"  # the launcher takes it
    ticks[0] += 301.0
    shown = update.get_status()

    assert shown["installState"] == "idle", shown
    assert shown["error"] is None
    assert update.restart_approval.pending is None


def test_a_checkout_install_latches_the_restart_and_a_failed_handoff_releases_it(
    tmp_path: Path,
):
    """Contract §4.1, §4.2: writing the checkout request approves the restart.

    The latch goes up with the request and stays up, so a second install is
    refused. A request that cannot be written approves nothing that will
    happen, so its latch comes down again.
    """

    now = [1_700_000_000.0]
    request_path = tmp_path / "control" / "update.json"
    request_path.parent.mkdir()
    update = service(
        tmp_path,
        lambda _etag: ReleaseResponse(release("2.0.1"), None),
        now,
        platform_name="darwin",
        update_request_path=request_path,
    )

    update.request_install()

    assert update.restart_approval.pending == "v2.0.1"
    with pytest.raises(UpdateInstallUnavailable, match="about to restart"):
        update.request_install()

    unwritable = tmp_path / "missing" / "update.json"
    failing = service(
        tmp_path / "second",
        lambda _etag: ReleaseResponse(release("2.0.1"), None),
        now,
        platform_name="darwin",
        update_request_path=unwritable,
    )
    approved: list[str] = []
    approve = failing.restart_approval.approve
    failing.restart_approval.approve = lambda target: (approved.append(target), approve(target))[1]  # type: ignore[method-assign]

    with pytest.raises(UpdateInstallUnavailable, match="could not create the update handoff"):
        failing.request_install()

    assert approved == ["v2.0.1"]
    assert failing.restart_approval.pending is None
    shown = failing.get_status()
    assert shown["installState"] == "failed"
    assert "did not start" in str(shown["error"])


def test_incomplete_release_rechecks_quickly_and_never_offers_an_action(tmp_path: Path):
    now = [1_700_000_000.0]
    calls = 0

    def fetch(_etag: str | None) -> ReleaseResponse:
        nonlocal calls
        calls += 1
        return ReleaseResponse(release("2.0.1", ready=calls > 1), '"release-1"')

    update = service(tmp_path, fetch, now)
    first = update.get_status()
    assert first["availability"] == "incomplete"
    assert first["action"] is None

    now[0] += INCOMPLETE_TTL_SECONDS - 1
    assert update.get_status()["cached"] is True
    assert calls == 1

    now[0] += 2
    second = update.get_status()
    assert calls == 2
    assert second["availability"] == "available"
    assert second["action"] is not None


def test_network_failure_retains_last_success_but_marks_it_stale(tmp_path: Path):
    now = [1_700_000_000.0]
    calls = 0

    def fetch(_etag: str | None) -> ReleaseResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ReleaseResponse(release("2.0.1"), '"release-1"')
        raise RuntimeError("offline")

    update = service(tmp_path, fetch, now)
    assert update.get_status()["availability"] == "available"
    now[0] += 61
    result = update.get_status(force=True)

    assert result["availability"] == "available"
    assert result["freshness"] == "stale"
    assert result["lastError"] == "offline"
    assert result["checkedAt"] is not None


def test_rate_limit_reset_is_honored_even_when_longer_than_normal_backoff(tmp_path: Path):
    now = [1_700_000_000.0]
    update = service(
        tmp_path,
        lambda _etag: (_ for _ in ()).throw(
            UpdateRateLimitError("rate limited", retry_at=now[0] + 7_200)
        ),
        now,
    )
    result = update.get_status()
    assert result["availability"] == "unknown"
    assert result["nextCheckAt"] == "2023-11-15T00:13:20Z"


def test_manual_refresh_is_cooled_down_and_304_reuses_the_observation(tmp_path: Path):
    now = [1_700_000_000.0]
    etags: list[str | None] = []

    def fetch(etag: str | None) -> ReleaseResponse:
        etags.append(etag)
        if len(etags) == 1:
            return ReleaseResponse(release("2.0.0"), '"release-1"')
        return ReleaseResponse(None, etag, not_modified=True)

    update = service(tmp_path, fetch, now)
    assert update.get_status()["availability"] == "current"
    assert update.get_status(force=True)["cached"] is True
    now[0] += 61
    assert update.get_status(force=True)["availability"] == "current"
    assert etags == [None, '"release-1"']


def test_persisted_available_result_becomes_current_after_the_app_updates(tmp_path: Path):
    now = [1_700_000_000.0]
    first = service(
        tmp_path,
        lambda _etag: ReleaseResponse(release("2.0.1"), '"release-1"'),
        now,
    )
    assert first.get_status()["availability"] == "available"

    restarted = UpdateService(
        running_version="2.0.1",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=lambda _etag: (_ for _ in ()).throw(AssertionError("cache should still be fresh")),
        clock=lambda: now[0] + 1,
        checkout_probe=safe_checkout,
    )
    result = restarted.get_status()
    assert result["availability"] == "current"
    assert result["cached"] is True
    assert result["action"] is None


def test_modified_checkout_suppresses_the_easy_update_command(tmp_path: Path):
    now = [1_700_000_000.0]
    checkout = safe_checkout(tmp_path, "2.0.0") | {
        "kind": "modified",
        "trackedChanges": True,
        "updateSupported": False,
        "reason": "commit or stash",
    }
    update = UpdateService(
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=lambda _etag: ReleaseResponse(release("2.0.1"), None),
        clock=lambda: now[0],
        checkout_probe=lambda _root, _version: checkout,
    )

    result = update.get_status()
    assert result["availability"] == "available"
    assert result["checkout"]["kind"] == "modified"
    assert result["action"] is None


def test_update_commands_quote_spaces_and_reject_unvalidated_tags(tmp_path: Path):
    root = tmp_path / "WG checkout"
    posix = update_action(root, "darwin", "v2.0.1")["command"]
    windows = update_action(Path("C:/WG's checkout"), "win32", "v2.0.1")["command"]
    assert "'" in posix and "WG checkout" in posix
    assert windows.startswith("& '") and "WG''s checkout" in windows

    import pytest

    with pytest.raises(ValueError, match="invalid tag"):
        update_action(root, "darwin", "v2.0.1; touch owned")


def test_checkout_classifier_only_supports_an_exact_clean_release(tmp_path: Path):
    unsupported = checkout_status(tmp_path, "2.0.0")
    assert unsupported["kind"] == "unsupported"
    assert unsupported["updateSupported"] is False

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("release\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "release"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "v2.0.0"], cwd=repo, check=True)

    exact = checkout_status(repo, "2.0.0")
    assert exact["kind"] == "release"
    assert exact["atDeclaredTag"] is True
    assert exact["updateSupported"] is True

    tracked.write_text("development\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "development"], cwd=repo, check=True)
    development = checkout_status(repo, "2.0.0")
    assert development["kind"] == "development"
    assert development["updateSupported"] is False

    tracked.write_text("modified\n", encoding="utf-8")
    modified = checkout_status(repo, "2.0.0")
    assert modified["kind"] == "modified"
    assert modified["updateSupported"] is False


def test_bundle_with_missing_manifests_never_falls_back_to_the_git_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WG2_BUNDLE", "1")

    bundled = checkout_status(tmp_path, "2.0.0")

    assert bundled["kind"] == "bundle"
    assert bundled["updateSupported"] is False
    assert "manifest" in bundled["reason"]


def test_status_endpoint_runs_the_blocking_service_off_loop(tmp_path: Path):
    class FakeService:
        def get_status(self, *, force: bool = False) -> dict[str, object]:
            return {"force": force, "thread": threading.get_ident()}

    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path,
        repo_root=tmp_path,
        service=FakeService(),  # type: ignore[arg-type]
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/updates/status")
    main_thread = threading.get_ident()
    regular = asyncio.run(endpoint(refresh=False))
    refreshed = asyncio.run(endpoint(refresh=True))
    assert regular["force"] is False and regular["thread"] != main_thread
    assert refreshed["force"] is True and refreshed["thread"] != main_thread


def test_the_diagnostics_endpoint_carries_the_updaters_logs_without_the_home_folder(
    tmp_path: Path,
):
    """The updater review §3.3: "Copy update diagnostics" covers the update logs.

    ``update.log``, ``update-handoff.log`` and ``rollback-handoff.log``, each
    its last 64 KB from its first whole line, with the home folder as ``~`` the
    way a problem report writes it. A log that does not exist reads ``None``.
    """

    now = [1_700_000_000.0]
    update = service(tmp_path, lambda _etag: ReleaseResponse(release("2.0.0"), None), now)
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    staging = Path.home() / "Library" / "Application Support" / "WG" / "updates" / "0.3.4"
    (logs / "update.log").write_bytes(
        f"[2026-09-14T10:00:00] Removed the update downloads: {staging}\n".encode()
    )
    (logs / "update-handoff.log").write_bytes(b"helper started\n")
    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        service=update,
    )
    endpoint = next(
        (route.endpoint for route in app.routes if route.path == "/api/updates/diagnostics"),
        None,
    )
    assert endpoint is not None, "no GET /api/updates/diagnostics"

    answer = asyncio.run(endpoint())

    assert answer["tailBytes"] == 64 * 1024
    assert set(answer["logs"]) == {"update.log", "update-handoff.log", "rollback-handoff.log"}
    assert answer["logs"]["rollback-handoff.log"] is None
    assert answer["logs"]["update-handoff.log"] == "helper started\n"
    text = answer["logs"]["update.log"]
    assert str(Path.home()) not in text
    assert "Removed the update downloads: ~" in text


def test_the_update_status_reports_a_pending_wglink_activation(tmp_path: Path, monkeypatch):
    """The updater review §3.8: WGLink's partial success is said in the update flow.

    The status carries the activation's verdict and detail -- what WG last
    decided, never the pending files, which the updater does not read -- with
    the home folder as ``~``. No report yet, and activation turned off, carry
    nothing.
    """

    from server.cadlink import addin_update

    now = [1_700_000_000.0]
    update = service(tmp_path, lambda _etag: ReleaseResponse(release("2.0.0"), None), now)
    monkeypatch.setattr(addin_update, "_report", None)
    assert update.get_status()["wglink"] is None

    target = Path.home() / "Library" / "Application Support" / "Autodesk" / "WGLink"
    monkeypatch.setattr(
        addin_update,
        "_report",
        addin_update.Activation(
            "pending", f"WGLink activation is pending until Fusion closes; it replaces {target}"
        ).report(),
    )
    shown = update.get_status()["wglink"]

    assert shown is not None and set(shown) == {"verdict", "detail"}
    assert shown["verdict"] == "pending"
    assert str(Path.home()) not in shown["detail"] and "~" in shown["detail"]

    monkeypatch.setattr(
        addin_update, "_report", addin_update.Activation("disabled", "WG2_WGLINK_REFRESH=0").report()
    )
    assert update.get_status()["wglink"] is None


def test_the_update_service_imports_no_cad_link_module(tmp_path: Path):
    """server/updates reads WGLink's verdict without a module-level import of server/cadlink.

    Importing the update service in a fresh interpreter loads nothing from
    ``server.cadlink``, so the two packages can never import each other at
    module level.
    """

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, server.updates.service; "
            "print(sorted(name for name in sys.modules if name.startswith('server.cadlink')))",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "[]", result.stdout


def test_install_endpoint_requires_confirmation_and_runs_off_loop(tmp_path: Path):
    class FakeService:
        def request_install(self) -> dict[str, object]:
            return {
                "accepted": True,
                "tag": "v2.0.1",
                "thread": threading.get_ident(),
            }

    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path,
        repo_root=tmp_path,
        service=FakeService(),  # type: ignore[arg-type]
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/updates/install")
    main_thread = threading.get_ident()

    with pytest.raises(HTTPException) as denied:
        asyncio.run(endpoint(confirmation=None))
    assert denied.value.status_code == 403

    accepted = asyncio.run(endpoint(confirmation="install"))
    assert accepted["accepted"] is True
    assert accepted["thread"] != main_thread


def test_install_endpoint_reports_active_job_conflicts_as_409(tmp_path: Path) -> None:
    class ConflictingService:
        def request_install(self) -> dict[str, object]:
            raise UpdateInstallUnavailable(
                "Update 2.0.1 is already active; wait before installing 2.0.2."
            )

    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path,
        repo_root=tmp_path,
        service=ConflictingService(),  # type: ignore[arg-type]
    )
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/updates/install")

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(endpoint(confirmation="install"))

    assert conflict.value.status_code == 409
    assert "2.0.1" in conflict.value.detail
