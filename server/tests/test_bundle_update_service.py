"""Standalone-bundle release classification and asset selection contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from server.updates.service import ReleaseResponse, UpdateService, checkout_status


OLD_RUNTIME = "111111111111"
NEW_RUNTIME = "222222222222"


def _asset(name: str, size: int = 100) -> dict[str, object]:
    return {
        "name": name,
        "state": "uploaded",
        "size": size,
        "browser_download_url": (
            "https://github.com/m3gnus/waveguide-generator/releases/"
            f"download/v2.0.1/{name}"
        ),
    }


def _pair(name: str, size: int = 100) -> list[dict[str, object]]:
    return [_asset(name, size), _asset(name + ".sha256", 96)]


def _manifest(version: str = "2.0.1", runtime_id: str = NEW_RUNTIME) -> bytes:
    return json.dumps(
        {
            "schemaVersion": 1,
            "version": version,
            "commit": "a" * 40,
            "runtimeId": runtime_id,
        }
    ).encode()


def _release(
    *,
    include_runtime: bool,
    include_installer: bool = True,
    runtime_id: str = NEW_RUNTIME,
    platform: str = "macos-arm64",
) -> tuple[dict[str, Any], dict[str, bytes]]:
    version = "2.0.1"
    app_name = f"update-app-{version}.zip"
    manifest_name = f"update-app-{version}.manifest.json"
    manifest = _manifest(version, runtime_id)
    assets = [*_pair(app_name, 1_500), *_pair(manifest_name, len(manifest))]
    if include_runtime:
        assets += _pair(f"update-runtime-{platform}-{runtime_id}.zip", 7_500)
    if include_installer:
        extension = "dmg" if platform == "macos-arm64" else "zip"
        assets += _pair(f"Waveguide.Generator-{version}-{platform}.{extension}", 9_000)
    checksum = f"{hashlib.sha256(manifest).hexdigest()}  {manifest_name}\n".encode()
    return (
        {
            "tag_name": f"v{version}",
            "published_at": "2026-08-22T12:00:00Z",
            "assets": assets,
        },
        {manifest_name: manifest, manifest_name + ".sha256": checksum},
    )


def _bundle_checkout(runtime_id: str = OLD_RUNTIME) -> dict[str, object]:
    return {
        "kind": "bundle",
        "branch": None,
        "head": None,
        "atDeclaredTag": False,
        "trackedChanges": False,
        "aheadCount": None,
        "behindCount": None,
        "updateSupported": True,
        "installedVersion": "2.0.0",
        "runtimeId": runtime_id,
        "reason": None,
    }


def _service(
    tmp_path: Path,
    payload: dict[str, Any],
    fetched: dict[str, bytes],
    platform_name: str = "darwin",
    **kwargs: Any,
) -> UpdateService:
    def fetch_asset(url: str, limit: int) -> bytes:
        value = fetched[Path(url).name]
        assert len(value) <= limit
        return value

    return UpdateService(
        running_version="2.0.0",
        data_dir=tmp_path / "data",
        repo_root=tmp_path / "Resources" / "app",
        fetcher=lambda _etag: ReleaseResponse(payload, '"bundle"'),
        asset_fetcher=fetch_asset,
        clock=lambda: 1_700_000_000.0,
        platform_name=platform_name,
        checkout_probe=lambda _root, _version: _bundle_checkout(),
        update_request_path=tmp_path / "control" / "update.json",
        **kwargs,
    )


def test_bundle_checkout_reads_installed_app_and_runtime_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Resources" / "app"
    runtime = tmp_path / "Resources" / "runtime"
    app.mkdir(parents=True)
    runtime.mkdir()
    (app / "APP-MANIFEST.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "version": "2.0.0",
                "commit": "a" * 40,
                "runtimeId": OLD_RUNTIME,
            }
        ),
        encoding="utf-8",
    )
    (runtime / "RUNTIME-MANIFEST.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runtimeId": OLD_RUNTIME,
                "python": "3.13.12",
                "platform": "macos-arm64",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WG2_BUNDLE", "1")

    result = checkout_status(app, "9.9.9")

    assert result["kind"] == "bundle"
    assert result["updateSupported"] is True
    assert result["installedVersion"] == "2.0.0"
    assert result["runtimeId"] == OLD_RUNTIME


def test_windows_bundle_checkout_reads_manifests_beside_the_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "Waveguide Generator"
    app = folder / "app"
    runtime = folder / "runtime"
    app.mkdir(parents=True)
    runtime.mkdir()
    (folder / "Waveguide Generator.exe").write_bytes(b"launcher")
    (app / "APP-MANIFEST.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "version": "2.0.0",
                "commit": "a" * 40,
                "runtimeId": OLD_RUNTIME,
            }
        ),
        encoding="utf-8",
    )
    (runtime / "RUNTIME-MANIFEST.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runtimeId": OLD_RUNTIME,
                "python": "3.13.12",
                "platform": "windows-x86_64",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WG2_BUNDLE", "1")

    result = checkout_status(app, "9.9.9")

    assert result["kind"] == "bundle"
    assert result["updateSupported"] is True
    assert result["installedVersion"] == "2.0.0"
    assert result["runtimeId"] == OLD_RUNTIME


def test_current_release_records_all_bundle_assets_and_downloads_both_layers(
    tmp_path: Path,
) -> None:
    payload, fetched = _release(include_runtime=True)
    recent_calls = 0

    def recent() -> list[dict[str, Any]]:
        nonlocal recent_calls
        recent_calls += 1
        return []

    result = _service(tmp_path, payload, fetched, recent_releases_fetcher=recent).get_status()

    assert result["availability"] == "available"
    assert result["release"]["runtimeId"] == NEW_RUNTIME
    assert {asset["layer"] for asset in result["release"]["bundleAssets"]} == {
        "app",
        "runtime",
        "manifest",
        "installer",
    }
    assert result["action"] == {
        "kind": "bundle_download",
        "assets": [
            {
                "name": "update-app-2.0.1.zip",
                "url": "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/update-app-2.0.1.zip",
                "sha256Url": "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/update-app-2.0.1.zip.sha256",
                "bytes": 1_500,
                "layer": "app",
            },
            {
                "name": f"update-runtime-macos-arm64-{NEW_RUNTIME}.zip",
                "url": f"https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/update-runtime-macos-arm64-{NEW_RUNTIME}.zip",
                "sha256Url": f"https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/update-runtime-macos-arm64-{NEW_RUNTIME}.zip.sha256",
                "bytes": 7_500,
                "layer": "runtime",
            },
        ],
        "downloadBytes": 9_000,
    }
    assert result["canInstall"] is True
    assert result["installState"] == "idle"
    assert recent_calls == 0


def test_unchanged_installed_runtime_needs_only_the_app_layer(tmp_path: Path) -> None:
    payload, fetched = _release(include_runtime=False)
    recent_calls = 0

    def recent() -> list[dict[str, Any]]:
        nonlocal recent_calls
        recent_calls += 1
        return []

    update = _service(
        tmp_path,
        payload,
        fetched,
        recent_releases_fetcher=recent,
    )
    update.checkout_probe = lambda _root, _version: _bundle_checkout(NEW_RUNTIME)

    result = update.get_status()

    assert result["release"]["assetsReady"] is True
    assert [asset["layer"] for asset in result["action"]["assets"]] == ["app"]
    assert result["action"]["downloadBytes"] == 1_500
    assert recent_calls == 0


def test_runtime_is_selected_from_an_earlier_release_and_the_list_is_cached(
    tmp_path: Path,
) -> None:
    payload, fetched = _release(include_runtime=False)
    runtime_name = f"update-runtime-macos-arm64-{NEW_RUNTIME}.zip"
    recent_calls = 0

    def recent() -> list[dict[str, Any]]:
        nonlocal recent_calls
        recent_calls += 1
        return [{"tag_name": "v2.0.1", "assets": _pair(runtime_name, 7_500)}]

    update = _service(tmp_path, payload, fetched, recent_releases_fetcher=recent)

    first = update.get_status()
    second = update.get_status()

    runtime = next(asset for asset in first["action"]["assets"] if asset["layer"] == "runtime")
    assert runtime["name"] == runtime_name
    assert first["release"]["assetsReady"] is True
    assert second["action"] == first["action"]
    assert recent_calls == 1


def test_bad_release_manifest_digest_is_a_guarded_update_error(tmp_path: Path) -> None:
    payload, fetched = _release(include_runtime=True)
    manifest_name = "update-app-2.0.1.manifest.json"
    fetched[manifest_name + ".sha256"] = f"{'0' * 64}  {manifest_name}\n".encode()

    result = _service(tmp_path, payload, fetched).get_status()

    assert result["availability"] == "unknown"
    assert "does not match" in result["lastError"]
    assert result["action"] is None


@pytest.mark.parametrize(
    "untrusted_url",
    [
        "https://github.com/another/project/releases/download/v2.0.1/{name}",
        (
            "https://github.com/m3gnus/waveguide-generator/releases/"
            "download/v2.0.0/{name}"
        ),
    ],
)
def test_release_assets_are_bound_to_this_repository_and_release_tag(
    tmp_path: Path,
    untrusted_url: str,
) -> None:
    payload, fetched = _release(include_runtime=True)
    app_name = "update-app-2.0.1.zip"
    app_asset = next(asset for asset in payload["assets"] if asset["name"] == app_name)
    app_asset["browser_download_url"] = untrusted_url.format(name=app_name)

    result = _service(tmp_path, payload, fetched).get_status()

    assert result["availability"] == "incomplete"
    assert result["release"]["assetsReady"] is False
    assert result["action"] is None


def test_install_request_carries_release_and_installed_runtime_ids(tmp_path: Path) -> None:
    payload, fetched = _release(include_runtime=True)
    calls: list[tuple[str, str, str]] = []

    class RecordingInstaller:
        active_version: str | None = None

        def status(self) -> dict[str, object]:
            return {
                "installState": "downloading" if self.active_version else "idle",
                "activeVersion": self.active_version,
                "downloadedBytes": 0,
                "totalBytes": 0,
                "error": None,
            }

        def start(
            self,
            version: str,
            _assets: list[dict[str, object]],
            *,
            expected_runtime_id: str,
            installed_runtime_id: str,
        ) -> dict[str, object]:
            calls.append((version, expected_runtime_id, installed_runtime_id))
            self.active_version = version
            return self.status()

    update = _service(
        tmp_path,
        payload,
        fetched,
        bundle_installer=RecordingInstaller(),  # type: ignore[arg-type]
    )

    result = update.request_install()

    assert result["accepted"] is True
    assert result["activeVersion"] == "2.0.1"
    assert calls == [("2.0.1", NEW_RUNTIME, OLD_RUNTIME)]
    assert update.get_status()["activeVersion"] == "2.0.1"


def test_windows_release_uses_windows_runtime_and_full_zip_asset_names(
    tmp_path: Path,
) -> None:
    payload, fetched = _release(
        include_runtime=True,
        platform="windows-x86_64",
    )

    result = _service(
        tmp_path,
        payload,
        fetched,
        platform_name="win32",
        recent_releases_fetcher=lambda: [],
    ).get_status()

    names = {asset["name"] for asset in result["release"]["bundleAssets"]}
    assert f"update-runtime-windows-x86_64-{NEW_RUNTIME}.zip" in names
    assert "Waveguide.Generator-2.0.1-windows-x86_64.zip" in names
    assert result["action"]["assets"][1]["name"] == (
        f"update-runtime-windows-x86_64-{NEW_RUNTIME}.zip"
    )
