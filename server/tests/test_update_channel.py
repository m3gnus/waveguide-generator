"""The stable/beta update channel: storage and its endpoints.

The channel exists to buy Windows and packaging coverage before a stable
version number is spent. The properties worth pinning here are that stable is
the default, that the setting lives in Settings so it survives the update it
controls, and that the endpoints validate it. The beta scan itself -- that it
never mistakes an ``-updates`` companion for a release, its pagination and its
ordering -- is pinned in ``test_installer_client.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
import pytest

from server.settings.store import SettingsStore
from server.updates.api import mount_updates
from server.updates.installer_checker import ReleaseResponse
from server.updates.installer_client import InstallerClient
from server.updates.installer_service import InstallerUpdateService, UpdateChannelUnavailable
from shared import release_assets


def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path, settings_path=tmp_path / "ui_settings.json")


def service(tmp_path: Path, *, settings: SettingsStore | None = None) -> InstallerUpdateService:
    # No release is ever found: these tests are about the preference, and a
    # channel switch starts a check that must not reach the network.
    client = InstallerClient(
        running_version="2.0.0", data_dir=tmp_path / "data", repo_root=tmp_path,
        platform_name=release_assets.MACOS_PLATFORM, settings=settings,
        fetcher=lambda _url, _etag: ReleaseResponse(None),
    )
    return InstallerUpdateService(running_version="2.0.0", data_dir=tmp_path / "data",
                                  repo_root=tmp_path, settings=settings, client=client)


@pytest.mark.parametrize("stored", [None, "nightly", {"channel": "nightly"}, {"channel": 1}, {}, ["beta"]])
def test_anything_but_a_recognised_channel_reads_as_stable(tmp_path: Path, stored: Any) -> None:
    settings = store(tmp_path)
    if stored is not None:
        settings.put("updates", stored)
    update = service(tmp_path, settings=settings)
    try:
        assert update.channel() == "stable"
    finally:
        update.close()


def test_the_default_channel_is_stable_and_a_service_without_settings_stays_stable(tmp_path: Path) -> None:
    for settings in (store(tmp_path), None):
        update = service(tmp_path, settings=settings)
        try:
            assert update.channel() == "stable"
        finally:
            update.close()
    update = service(tmp_path)
    try:
        with pytest.raises(UpdateChannelUnavailable):
            update.set_channel("beta")
    finally:
        update.close()


def test_an_unknown_channel_is_refused(tmp_path: Path) -> None:
    update = service(tmp_path, settings=store(tmp_path))
    try:
        with pytest.raises(ValueError):
            update.set_channel("nightly")
        assert update.channel() == "stable"
    finally:
        update.close()


def test_the_channel_survives_the_update_it_controls(tmp_path: Path) -> None:
    """Stored in Settings, so the next build -- a fresh service -- reads it back."""
    first = service(tmp_path, settings=store(tmp_path))
    try:
        assert first.set_channel("beta") == "beta"
    finally:
        first.close()
    second = service(tmp_path, settings=store(tmp_path))
    try:
        assert second.channel() == "beta"
        assert second.get_status()["channel"] == "beta"
    finally:
        second.close()


def test_the_channel_cannot_change_while_a_restart_is_pending(tmp_path: Path) -> None:
    update = service(tmp_path, settings=store(tmp_path))
    try:
        update.restart_approval.approve("2.1.0")
        with pytest.raises(UpdateChannelUnavailable):
            update.set_channel("beta")
        assert update.channel() == "stable"
    finally:
        update.close()


def channel_app(tmp_path: Path, update: InstallerUpdateService) -> FastAPI:
    app = FastAPI()
    mount_updates(app, running_version="2.0.0", data_dir=tmp_path, repo_root=tmp_path, service=update)
    return app


def endpoint(app: FastAPI, method: str):
    return next(
        route.endpoint
        for route in app.routes
        if route.path == "/api/updates/channel" and method in route.methods
    )


def test_the_channel_endpoints_read_and_write_the_preference(tmp_path: Path) -> None:
    update = service(tmp_path, settings=store(tmp_path))
    try:
        app = channel_app(tmp_path, update)
        assert asyncio.run(endpoint(app, "GET")()) == {"channel": "stable"}
        assert asyncio.run(endpoint(app, "PUT")(channel="beta")) == {"channel": "beta"}
        assert asyncio.run(endpoint(app, "GET")()) == {"channel": "beta"}
        assert update.channel() == "beta"
    finally:
        update.close()


def test_the_channel_endpoint_refuses_an_unknown_channel(tmp_path: Path) -> None:
    update = service(tmp_path, settings=store(tmp_path))
    try:
        with pytest.raises(HTTPException) as refused:
            asyncio.run(endpoint(channel_app(tmp_path, update), "PUT")(channel="nightly"))
        assert refused.value.status_code == 400
    finally:
        update.close()


def test_the_channel_endpoint_reports_a_missing_store_as_409(tmp_path: Path) -> None:
    update = service(tmp_path)
    try:
        with pytest.raises(HTTPException) as conflict:
            asyncio.run(endpoint(channel_app(tmp_path, update), "PUT")(channel="beta"))
        assert conflict.value.status_code == 409
    finally:
        update.close()
