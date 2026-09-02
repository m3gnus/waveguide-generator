"""The stable/beta update channel: storage, the beta scan, and its endpoints.

The channel exists to buy Windows and packaging coverage before a stable
version number is spent -- the last three release failures were all packaging or
cross-platform, none reproducible on macOS, and each one cost a version. So the
properties worth pinning are that stable is untouched, that the setting survives
the update it controls, and that the beta scan never mistakes an ``-updates``
companion for a release.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
import pytest

from server.settings.store import SettingsStore
from server.updates.api import mount_updates
from server.updates.service import (
    BETA_CHANNEL,
    STABLE_CHANNEL,
    UPDATE_SETTINGS_NAMESPACE,
    ReleaseResponse,
    UpdateChannelUnavailable,
    UpdateInstallUnavailable,
    UpdateService,
    channel_of,
    update_action,
)


def release(version: str, *, ready: bool = True, **extra: Any) -> dict[str, Any]:
    archive = f"update-spa-{version}.tar.gz"
    assets = [
        {"name": archive, "state": "uploaded", "size": 100},
        {"name": f"{archive}.sha256", "state": "uploaded", "size": 64},
    ]
    if not ready:
        assets[1]["state"] = "new"
    return {
        "tag_name": f"v{version}",
        "published_at": "2026-08-11T12:00:00Z",
        "assets": assets,
        **extra,
    }


def companion(version: str) -> dict[str, Any]:
    """The ``-updates`` companion of a release: a GitHub pre-release too."""

    return {
        "tag_name": f"v{version}-updates",
        "prerelease": True,
        "published_at": "2026-08-11T12:00:00Z",
        "assets": [
            {"name": f"update-app-{version}.zip", "state": "uploaded", "size": 100},
        ],
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


def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path, settings_path=tmp_path / "ui_settings.json")


def service(
    tmp_path: Path,
    *,
    fetcher=None,
    recent=None,
    settings: SettingsStore | None = None,
    now: list[float] | None = None,
    running_version: str = "2.0.0",
    platform_name: str = "darwin",
) -> UpdateService:
    clock = now or [1_700_000_000.0]

    def refuse(_etag: str | None) -> ReleaseResponse:
        raise AssertionError("the stable endpoint must not be polled on the beta channel")

    return UpdateService(
        running_version=running_version,
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=fetcher or refuse,
        recent_releases_fetcher=recent or (lambda: []),
        clock=lambda: clock[0],
        checkout_probe=safe_checkout,
        # Pinned, because these tests assert on the exact update command and
        # each platform builds a different one -- Windows wraps the tag in
        # PowerShell quotes. The platform-specific shapes are asserted directly
        # against `update_action` instead, below.
        platform_name=platform_name,
        settings=settings,
    )


# --- the stored preference -------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        None,
        {},
        {"channel": "nightly"},
        {"channel": "BETA"},
        {"channel": True},
        {"channel": ["beta"]},
        "beta",
        ["beta"],
        42,
    ],
)
def test_anything_but_a_recognised_channel_reads_as_stable(stored: Any) -> None:
    """The namespace is a generic map the frontend writes, so it may hold anything."""

    assert channel_of(stored) == STABLE_CHANNEL


def test_the_stored_channel_is_read_back() -> None:
    assert channel_of({"channel": "beta"}) == BETA_CHANNEL
    assert channel_of({"channel": "stable"}) == STABLE_CHANNEL


def test_the_default_channel_is_stable(tmp_path: Path) -> None:
    assert service(tmp_path, settings=store(tmp_path)).channel() == STABLE_CHANNEL


def test_a_service_without_a_settings_store_is_always_stable(tmp_path: Path) -> None:
    """An embedded caller that supplies none keeps pre-channel behaviour."""

    update = service(tmp_path)
    assert update.channel() == STABLE_CHANNEL
    with pytest.raises(UpdateChannelUnavailable):
        update.set_channel(BETA_CHANNEL)


def test_an_unknown_channel_is_refused(tmp_path: Path) -> None:
    update = service(tmp_path, settings=store(tmp_path))
    with pytest.raises(ValueError):
        update.set_channel("nightly")
    assert update.channel() == STABLE_CHANNEL


def test_the_channel_survives_the_update_it_controls(tmp_path: Path) -> None:
    """Server-side storage is the requirement, not an implementation detail.

    A beta install whose preference lived in the browser would be back on stable
    the first time the update it asked for actually landed.
    """

    settings = store(tmp_path)
    service(tmp_path, settings=settings).set_channel(BETA_CHANNEL)

    # A brand new process, reading the same application-data file.
    restarted = service(tmp_path, settings=store(tmp_path), running_version="2.0.1")
    assert restarted.channel() == BETA_CHANNEL
    written = json.loads((tmp_path / "ui_settings.json").read_text(encoding="utf-8"))
    assert written["namespaces"][UPDATE_SETTINGS_NAMESPACE] == {"channel": "beta"}


# --- stable is untouched ---------------------------------------------------


def test_stable_still_polls_releases_latest_and_never_lists(tmp_path: Path) -> None:
    """GitHub excludes pre-releases from ``releases/latest``, so stable is free."""

    def recent() -> list[dict[str, Any]]:
        raise AssertionError("the stable channel must not need the release list")

    result = service(
        tmp_path,
        fetcher=lambda _etag: ReleaseResponse(release("2.0.1"), '"tag-1"'),
        recent=recent,
        settings=store(tmp_path),
    ).get_status()

    assert result["channel"] == STABLE_CHANNEL
    assert result["availability"] == "available"
    assert result["release"]["tag"] == "v2.0.1"


def test_stable_refuses_a_prerelease_arriving_from_releases_latest(tmp_path: Path) -> None:
    """It cannot happen by GitHub's definition, so it stays a hard error."""

    result = service(
        tmp_path,
        fetcher=lambda _etag: ReleaseResponse(release("2.1.0-beta.1"), None),
        settings=store(tmp_path),
    ).get_status()

    assert result["availability"] == "unknown"
    assert "unsupported version tag" in result["lastError"]


# --- the beta scan ---------------------------------------------------------


def beta_service(tmp_path: Path, releases: list[dict[str, Any]], **kwargs: Any) -> UpdateService:
    settings = kwargs.pop("settings", None) or store(tmp_path)
    update = service(tmp_path, recent=lambda: releases, settings=settings, **kwargs)
    update.set_channel(BETA_CHANNEL)
    return update


def test_beta_offers_the_highest_version_including_prereleases(tmp_path: Path) -> None:
    result = beta_service(
        tmp_path, [release("2.0.1"), release("2.1.0-beta.1"), release("2.0.0")]
    ).get_status()

    assert result["channel"] == BETA_CHANNEL
    assert result["availability"] == "available"
    assert result["release"]["version"] == "2.1.0-beta.1"
    assert result["release"]["tag"] == "v2.1.0-beta.1"
    assert result["action"]["command"].endswith(" --tag v2.1.0-beta.1")


def test_beta_offered_an_updates_companion_refuses_it(tmp_path: Path) -> None:
    """#58's trap: a beta and an ``-updates`` companion are both pre-releases.

    Filtering on GitHub's pre-release flag alone would offer
    ``v2.2.0-beta.1-updates`` -- a bag of update layers with no installer on it
    -- as though it were the newest release.
    """

    result = beta_service(
        tmp_path,
        [
            companion("2.2.0-beta.1"),
            companion("2.1.0"),
            release("2.1.0-beta.1"),
            release("2.0.1"),
        ],
    ).get_status()

    assert result["release"]["version"] == "2.1.0-beta.1"
    assert "-updates" not in result["release"]["tag"]
    assert result["action"]["command"].endswith(" --tag v2.1.0-beta.1")


def test_a_list_of_only_companions_offers_nothing(tmp_path: Path) -> None:
    result = beta_service(tmp_path, [companion("2.1.0"), companion("2.0.1")]).get_status()

    assert result["availability"] == "unknown"
    assert result["release"] is None
    assert "supported version tag" in result["lastError"]


def test_beta_takes_a_stable_release_when_it_outranks_every_prerelease(
    tmp_path: Path,
) -> None:
    """A beta install is ahead of stable, not on a separate track."""

    result = beta_service(
        tmp_path, [release("2.1.0"), release("2.1.0-beta.3"), release("2.0.1")]
    ).get_status()

    assert result["release"]["version"] == "2.1.0"


def test_beta_skips_drafts(tmp_path: Path) -> None:
    result = beta_service(
        tmp_path, [release("2.9.0-beta.1", draft=True), release("2.1.0-beta.1")]
    ).get_status()

    assert result["release"]["version"] == "2.1.0-beta.1"


def test_beta_ignores_a_release_whose_assets_are_still_uploading(tmp_path: Path) -> None:
    result = beta_service(tmp_path, [release("2.1.0-beta.1", ready=False)]).get_status()

    assert result["availability"] == "incomplete"
    assert result["action"] is None


# --- switching, and the "ahead" state --------------------------------------


def test_switching_channel_discards_the_other_channels_answer_at_once(
    tmp_path: Path,
) -> None:
    """Otherwise the switch looks inert for up to twelve hours."""

    now = [1_700_000_000.0]
    settings = store(tmp_path)
    update = UpdateService(
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=lambda _etag: ReleaseResponse(release("2.0.1"), '"stable-etag"'),
        recent_releases_fetcher=lambda: [release("2.1.0-beta.1"), release("2.0.1")],
        clock=lambda: now[0],
        checkout_probe=safe_checkout,
        settings=settings,
    )

    assert update.get_status()["release"]["version"] == "2.0.1"
    update.set_channel(BETA_CHANNEL)
    # The clock has not moved, so only the channel change can force this.
    switched = update.get_status()

    assert switched["cached"] is False
    assert switched["release"]["version"] == "2.1.0-beta.1"


def test_returning_to_stable_asks_unconditionally_rather_than_with_a_stale_etag(
    tmp_path: Path,
) -> None:
    """A 304 against the pre-switch ETag would leave the beta release cached."""

    etags: list[str | None] = []

    def fetcher(etag: str | None) -> ReleaseResponse:
        etags.append(etag)
        return ReleaseResponse(release("2.0.1"), '"stable-etag"')

    settings = store(tmp_path)
    update = UpdateService(
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=fetcher,
        recent_releases_fetcher=lambda: [release("2.1.0-beta.1")],
        clock=lambda: 1_700_000_000.0,
        checkout_probe=safe_checkout,
        settings=settings,
    )

    update.get_status()
    update.set_channel(BETA_CHANNEL)
    assert update.get_status()["release"]["version"] == "2.1.0-beta.1"
    update.set_channel(STABLE_CHANNEL)
    back = update.get_status()

    assert etags == [None, None]
    assert back["release"]["version"] == "2.0.1"


def test_switching_back_to_stable_reports_ahead_rather_than_a_new_state(
    tmp_path: Path,
) -> None:
    """#56: the existing ``ahead`` state already covers this. No new one."""

    settings = store(tmp_path)
    settings.put(UPDATE_SETTINGS_NAMESPACE, {"channel": "stable"})
    result = service(
        tmp_path,
        fetcher=lambda _etag: ReleaseResponse(release("2.0.1"), None),
        settings=settings,
        running_version="2.1.0-beta.1",
    ).get_status()

    assert result["availability"] == "ahead"
    assert result["action"] is None


def test_a_channel_changed_outside_set_channel_still_forces_a_recheck(
    tmp_path: Path,
) -> None:
    """``set_channel`` is not the only writer of this namespace.

    ``PUT /api/settings/updates`` reaches the same generic map, and the file
    itself can be edited or replaced between runs. So the cached answer is
    compared against the channel actually in force, not against whatever the
    last channel change happened to leave behind.
    """

    settings = store(tmp_path)
    update = UpdateService(
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=lambda _etag: ReleaseResponse(release("2.0.1"), '"stable-etag"'),
        recent_releases_fetcher=lambda: [release("2.1.0-beta.1"), release("2.0.1")],
        clock=lambda: 1_700_000_000.0,
        checkout_probe=safe_checkout,
        settings=settings,
    )
    assert update.get_status()["release"]["version"] == "2.0.1"

    settings.put(UPDATE_SETTINGS_NAMESPACE, {"channel": "beta"})
    switched = update.get_status()

    assert switched["channel"] == BETA_CHANNEL
    assert switched["release"]["version"] == "2.1.0-beta.1"


def test_a_failed_beta_check_never_presents_the_cached_stable_release(
    tmp_path: Path,
) -> None:
    """A stale answer from the other channel is not an answer for this one."""

    settings = store(tmp_path)
    listed: list[list[dict[str, Any]]] = [[release("2.1.0-beta.1")]]

    def recent() -> list[dict[str, Any]]:
        if not listed[0]:
            raise RuntimeError("GitHub is unreachable")
        return listed[0]

    update = UpdateService(
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path,
        fetcher=lambda _etag: ReleaseResponse(release("2.0.1"), '"stable-etag"'),
        recent_releases_fetcher=recent,
        clock=lambda: 1_700_000_000.0,
        checkout_probe=safe_checkout,
        settings=settings,
    )
    assert update.get_status()["release"]["version"] == "2.0.1"

    listed[0] = []
    settings.put(UPDATE_SETTINGS_NAMESPACE, {"channel": "beta"})
    failed = update.get_status()

    assert failed["availability"] == "unknown"
    assert failed["release"] is None
    assert failed["action"] is None


def test_a_cache_written_before_channels_existed_is_read_as_stable(
    tmp_path: Path,
) -> None:
    """An upgrade must not throw away a working stable answer."""

    cache_path = tmp_path / "data" / "cache" / "update-status.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "release": {
                    "version": "2.0.1",
                    "tag": "v2.0.1",
                    "url": "https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1",
                    "publishedAt": "2026-08-11T12:00:00Z",
                    "assetsReady": True,
                },
                "releaseMode": "checkout",
                "availability": "available",
                "checkedAtEpoch": 1_700_000_000.0,
                "nextCheckEpoch": 1_800_000_000.0,
            }
        ),
        encoding="utf-8",
    )

    def refuse(_etag: str | None) -> ReleaseResponse:
        raise AssertionError("a matching stable cache must not force a re-check")

    result = service(tmp_path, fetcher=refuse, settings=store(tmp_path)).get_status()

    assert result["cached"] is True
    assert result["availability"] == "available"
    assert result["release"]["version"] == "2.0.1"


# --- what the install path accepts -----------------------------------------


def test_an_install_command_may_be_built_from_a_beta_tag(tmp_path: Path) -> None:
    """The channel is for exercising packaging, so the install must be reachable."""

    action = update_action(tmp_path, "darwin", "v2.1.0-beta.1")
    assert action["command"].endswith(" --tag v2.1.0-beta.1")


def test_every_platform_carries_the_beta_tag_into_its_own_installer(
    tmp_path: Path,
) -> None:
    """Windows is the platform this channel exists for, and it quotes differently.

    A beta tag reaching PowerShell unquoted, or not reaching it at all, would
    defeat the point: the packaging path is what the last three release failures
    were in, and none of them reproduced on macOS.
    """

    windows = update_action(tmp_path, "win32", "v2.1.0-beta.1")
    assert windows["shell"] == "PowerShell"
    assert windows["command"].endswith(" --tag 'v2.1.0-beta.1'")
    assert "install-and-update.bat" in windows["command"]

    linux = update_action(tmp_path, "linux", "v2.1.0-beta.1")
    assert linux["command"].endswith(" --tag v2.1.0-beta.1")
    assert "install.sh" in linux["command"]


@pytest.mark.parametrize(
    "tag", ["v2.1.0-updates", "v2.1.0-beta.1-updates", "v2.1", "nightly", "2.1.0"]
)
def test_no_install_command_is_built_from_a_companion_or_a_bad_tag(
    tmp_path: Path, tag: str
) -> None:
    with pytest.raises(ValueError):
        update_action(tmp_path, "darwin", tag)


def test_request_install_accepts_a_beta_release(tmp_path: Path) -> None:
    handoff = tmp_path / "update-request.json"
    update = beta_service(tmp_path, [release("2.1.0-beta.1")])
    update.update_request_path = handoff

    accepted = update.request_install()

    assert accepted == {"accepted": True, "tag": "v2.1.0-beta.1"}
    assert json.loads(handoff.read_text(encoding="utf-8"))["tag"] == "v2.1.0-beta.1"


def test_request_install_refuses_a_companion_tag(tmp_path: Path) -> None:
    update = beta_service(tmp_path, [release("2.1.0-beta.1")])
    update.update_request_path = tmp_path / "update-request.json"

    original = update.get_status

    def companion_status(*, force: bool = False) -> dict[str, Any]:
        status = original(force=force)
        status["release"] = {**status["release"], "tag": "v2.1.0-beta.1-updates"}
        return status

    update.get_status = companion_status  # type: ignore[method-assign]
    with pytest.raises(UpdateInstallUnavailable):
        update.request_install()


# --- the endpoints ---------------------------------------------------------


def channel_app(tmp_path: Path, update: UpdateService) -> FastAPI:
    app = FastAPI()
    mount_updates(
        app,
        running_version="2.0.0",
        data_dir=tmp_path,
        repo_root=tmp_path,
        service=update,
    )
    return app


def endpoint(app: FastAPI, method: str):
    return next(
        route.endpoint
        for route in app.routes
        if route.path == "/api/updates/channel" and method in route.methods
    )


def test_the_channel_endpoints_read_and_write_the_preference(tmp_path: Path) -> None:
    update = service(
        tmp_path,
        recent=lambda: [release("2.1.0-beta.1")],
        settings=store(tmp_path),
    )
    app = channel_app(tmp_path, update)

    assert asyncio.run(endpoint(app, "GET")()) == {"channel": "stable"}
    assert asyncio.run(endpoint(app, "PUT")(channel="beta")) == {"channel": "beta"}
    assert asyncio.run(endpoint(app, "GET")()) == {"channel": "beta"}
    assert update.channel() == BETA_CHANNEL


def test_the_channel_endpoint_refuses_an_unknown_channel(tmp_path: Path) -> None:
    app = channel_app(tmp_path, service(tmp_path, settings=store(tmp_path)))

    with pytest.raises(HTTPException) as refused:
        asyncio.run(endpoint(app, "PUT")(channel="nightly"))

    assert refused.value.status_code == 400
    assert "nightly" in refused.value.detail


def test_the_channel_endpoint_reports_a_missing_store_as_409(tmp_path: Path) -> None:
    app = channel_app(tmp_path, service(tmp_path))

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(endpoint(app, "PUT")(channel="beta"))

    assert conflict.value.status_code == 409


def test_the_status_payload_names_the_channel(tmp_path: Path) -> None:
    result = beta_service(tmp_path, [release("2.1.0-beta.1")]).get_status()
    assert result["channel"] == "beta"
