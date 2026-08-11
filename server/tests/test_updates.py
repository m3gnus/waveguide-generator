"""Release status, cache, checkout and API contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import threading
from typing import Any

from fastapi import FastAPI

from server.updates.api import mount_updates
from server.updates.service import (
    INCOMPLETE_TTL_SECONDS,
    ReleaseResponse,
    UpdateRateLimitError,
    UpdateService,
    checkout_status,
    update_action,
)


def release(version: str, *, ready: bool = True) -> dict[str, Any]:
    archive = f"waveguide-generator-v2-spa-{version}.tar.gz"
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
