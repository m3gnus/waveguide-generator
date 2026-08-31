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


def _asset(name: str, size: int = 100, tag: str = "v2.0.1") -> dict[str, object]:
    return {
        "name": name,
        "state": "uploaded",
        "size": size,
        "browser_download_url": (
            "https://github.com/m3gnus/waveguide-generator/releases/"
            f"download/{tag}/{name}"
        ),
    }


def _pair(name: str, size: int = 100, tag: str = "v2.0.1") -> list[dict[str, object]]:
    return [_asset(name, size, tag), _asset(name + ".sha256", 96, tag)]


def _manifest(version: str = "2.0.1", runtime_id: str = NEW_RUNTIME) -> bytes:
    return json.dumps(
        {
            "schemaVersion": 1,
            "version": version,
            "commit": "a" * 40,
            "runtimeId": runtime_id,
        }
    ).encode()


#: What ``_release`` hands back: the release a person downloads from, the bytes
#: the service will fetch by asset name, and the companion pre-release carrying
#: the layers. Three values rather than two because there are now two releases,
#: and a fixture that patched one URL to fake the second would test the patch.
ReleaseFixture = tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]


def _release(  # noqa: PLR0913
    *,
    include_runtime: bool,
    include_installer: bool = True,
    runtime_id: str = NEW_RUNTIME,
    platform: str = "macos-arm64",
    layers_on_user_release: bool = False,
) -> ReleaseFixture:
    version = "2.0.1"
    app_name = f"update-app-{version}.zip"
    manifest_name = f"update-app-{version}.manifest.json"
    manifest = _manifest(version, runtime_id)
    # Two releases now. The one a user lands on carries only the installers; every
    # layer the updater consumes is on the companion pre-release, so the download
    # page is not a list of seven files with no explanation.
    layer_tag = f"v{version}" if layers_on_user_release else f"v{version}-updates"
    layer_assets = [
        *_pair(app_name, 1_500, layer_tag),
        *_pair(manifest_name, len(manifest), layer_tag),
    ]
    if include_runtime:
        layer_assets += _pair(
            f"update-runtime-{platform}-{runtime_id}.zip", 7_500, layer_tag
        )
    user_assets: list[dict[str, Any]] = []
    if include_installer:
        extension = "dmg" if platform == "macos-arm64" else "zip"
        user_assets += _pair(
            f"Waveguide.Generator-{version}-{platform}.{extension}", 9_000
        )
    checksum = f"{hashlib.sha256(manifest).hexdigest()}  {manifest_name}\n".encode()
    return (
        {
            "tag_name": f"v{version}",
            "published_at": "2026-08-22T12:00:00Z",
            "assets": user_assets + (layer_assets if layers_on_user_release else []),
        },
        {manifest_name: manifest, manifest_name + ".sha256": checksum},
        {
            "tag_name": f"v{version}-updates",
            "prerelease": True,
            "published_at": "2026-08-22T12:00:00Z",
            "assets": [] if layers_on_user_release else layer_assets,
        },
    )


def _earlier_updates_release(
    runtime_id: str, *, version: str = "2.0.0", platform: str = "macos-arm64"
) -> dict[str, Any]:
    """A previous version's companion release, carrying only its runtime layer.

    Runtime layers are addressed by content, so an unchanged interpreter is
    reachable from whichever release last published it -- and since the split,
    that is a companion pre-release, whose tag ``TAG_RE`` alone would reject.
    """

    tag = f"v{version}-updates"
    return {
        "tag_name": tag,
        "prerelease": True,
        "assets": _pair(f"update-runtime-{platform}-{runtime_id}.zip", 7_500, tag),
    }


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
    payload, fetched, updates = _release(include_runtime=True)
    recent_calls = 0

    def recent() -> list[dict[str, Any]]:
        nonlocal recent_calls
        recent_calls += 1
        return [updates]

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
                "url": "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1-updates/update-app-2.0.1.zip",
                "sha256Url": "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1-updates/update-app-2.0.1.zip.sha256",
                "bytes": 1_500,
                "layer": "app",
            },
            {
                "name": f"update-runtime-macos-arm64-{NEW_RUNTIME}.zip",
                "url": f"https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1-updates/update-runtime-macos-arm64-{NEW_RUNTIME}.zip",
                "sha256Url": f"https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1-updates/update-runtime-macos-arm64-{NEW_RUNTIME}.zip.sha256",
                "bytes": 7_500,
                "layer": "runtime",
            },
        ],
        "downloadBytes": 9_000,
    }
    assert result["canInstall"] is True
    assert result["installState"] == "idle"
    # One list request answers "where are the layers?"; nothing asks a second
    # time, and the installer never needed the list at all.
    assert recent_calls == 1


def test_the_installer_stays_on_the_release_a_person_downloads_from(
    tmp_path: Path,
) -> None:
    """The split, seen from the client: two tags, and each asset on the right one.

    Asserting the tag in the URL rather than only the asset names is the point.
    A layer served from the user-facing release, or an installer hidden on the
    companion, would still carry the right filename.
    """

    payload, fetched, updates = _release(include_runtime=True)

    result = _service(
        tmp_path, payload, fetched, recent_releases_fetcher=lambda: [updates]
    ).get_status()

    by_layer = {
        asset["layer"]: asset for asset in result["release"]["bundleAssets"]
    }
    assert by_layer["installer"]["url"].startswith(
        "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/"
    )
    for layer in ("app", "manifest", "runtime"):
        assert by_layer[layer]["url"].startswith(
            "https://github.com/m3gnus/waveguide-generator/releases/"
            "download/v2.0.1-updates/"
        ), f"the {layer} layer is not on the companion release"


def test_unchanged_installed_runtime_needs_only_the_app_layer(tmp_path: Path) -> None:
    payload, fetched, updates = _release(include_runtime=False)
    recent_calls = 0

    def recent() -> list[dict[str, Any]]:
        nonlocal recent_calls
        recent_calls += 1
        return [updates]

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
    assert recent_calls == 1


def test_runtime_is_selected_from_an_earlier_release_and_the_list_is_cached(
    tmp_path: Path,
) -> None:
    payload, fetched, updates = _release(include_runtime=False)
    runtime_name = f"update-runtime-macos-arm64-{NEW_RUNTIME}.zip"
    earlier = _earlier_updates_release(NEW_RUNTIME)
    recent_calls = 0

    def recent() -> list[dict[str, Any]]:
        nonlocal recent_calls
        recent_calls += 1
        # This release's companion, which does not carry the runtime, and the
        # previous version's, which does. Both are pre-releases with the
        # ``-updates`` suffix, so the search has to accept that tag shape.
        return [updates, earlier]

    update = _service(tmp_path, payload, fetched, recent_releases_fetcher=recent)

    first = update.get_status()
    second = update.get_status()

    runtime = next(asset for asset in first["action"]["assets"] if asset["layer"] == "runtime")
    assert runtime["name"] == runtime_name
    assert runtime["url"].startswith(
        "https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.0-updates/"
    )
    assert first["release"]["assetsReady"] is True
    assert second["action"] == first["action"]
    # One list, shared by the companion lookup and the earlier-runtime search,
    # across two status calls.
    assert recent_calls == 1


def test_bad_release_manifest_digest_is_a_guarded_update_error(tmp_path: Path) -> None:
    payload, fetched, updates = _release(include_runtime=True)
    manifest_name = "update-app-2.0.1.manifest.json"
    fetched[manifest_name + ".sha256"] = f"{'0' * 64}  {manifest_name}\n".encode()

    result = _service(
        tmp_path, payload, fetched, recent_releases_fetcher=lambda: [updates]
    ).get_status()

    assert result["availability"] == "unknown"
    assert "does not match" in result["lastError"]
    assert result["action"] is None


@pytest.mark.parametrize(
    "untrusted_url",
    [
        # Another repository entirely: the origin check, unchanged by the split.
        "https://github.com/another/project/releases/download/v2.0.1/{name}",
        # Another version's companion release.
        (
            "https://github.com/m3gnus/waveguide-generator/releases/"
            "download/v2.0.0-updates/{name}"
        ),
        # The user-facing release of the SAME version. A layer listed on the
        # companion but served from the main tag is the mistake the split makes
        # possible, and the tag binding is what refuses it. Widening the tag
        # SHAPE to allow the suffix must not have widened the tag CHECK.
        (
            "https://github.com/m3gnus/waveguide-generator/releases/"
            "download/v2.0.1/{name}"
        ),
    ],
)
def test_release_assets_are_bound_to_this_repository_and_release_tag(
    tmp_path: Path,
    untrusted_url: str,
) -> None:
    payload, fetched, updates = _release(include_runtime=True)
    app_name = "update-app-2.0.1.zip"
    app_asset = next(asset for asset in updates["assets"] if asset["name"] == app_name)
    app_asset["browser_download_url"] = untrusted_url.format(name=app_name)

    result = _service(
        tmp_path, payload, fetched, recent_releases_fetcher=lambda: [updates]
    ).get_status()

    assert result["availability"] == "incomplete"
    assert result["release"]["assetsReady"] is False
    assert result["action"] is None


def test_install_request_carries_release_and_installed_runtime_ids(tmp_path: Path) -> None:
    payload, fetched, updates = _release(include_runtime=True)
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
        recent_releases_fetcher=lambda: [updates],
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
    payload, fetched, updates = _release(
        include_runtime=True,
        platform="windows-x86_64",
    )

    result = _service(
        tmp_path,
        payload,
        fetched,
        platform_name="win32",
        recent_releases_fetcher=lambda: [updates],
    ).get_status()

    names = {asset["name"] for asset in result["release"]["bundleAssets"]}
    assert f"update-runtime-windows-x86_64-{NEW_RUNTIME}.zip" in names
    assert "Waveguide.Generator-2.0.1-windows-x86_64.zip" in names
    assert result["action"]["assets"][1]["name"] == (
        f"update-runtime-windows-x86_64-{NEW_RUNTIME}.zip"
    )


def test_a_missing_companion_release_is_incomplete_not_a_partial_update(
    tmp_path: Path,
) -> None:
    """The failure the ordering in release.yml exists to prevent, seen by a client.

    The user-facing release is published with its installers and the companion is
    absent -- because it failed, or because a release was cut by hand. There is
    nothing to install, and the only safe answer is to say so and offer nothing.
    Downloading the installer instead, or reporting an error the user is asked to
    act on, would both be wrong.
    """

    payload, fetched, updates = _release(include_runtime=True)

    result = _service(
        tmp_path, payload, fetched, recent_releases_fetcher=lambda: []
    ).get_status()

    assert result["availability"] == "incomplete"
    assert result["release"]["assetsReady"] is False
    assert result["action"] is None
    assert result["canInstall"] is False
    # Not an error: nothing failed, the layers are simply not published yet.
    assert result["lastError"] is None
    # And no half-update on offer. The installer is on the release and trusted,
    # and it must still not be handed to a client as something to apply.
    assert "bundleAssets" not in result["release"]


def test_layers_left_on_the_user_facing_release_are_not_used(tmp_path: Path) -> None:
    """The split asserted from the other side: no fallback to the old layout.

    This fixture is exactly what every release up to and including 0.3.0 looked
    like -- app layer, manifest and runtime on the release the installers are on,
    and no companion at all. Before the split it was the happy path. If the
    service still reads layers from the main release when the companion is
    missing, this passes, and the split is decorative.
    """

    payload, fetched, updates = _release(
        include_runtime=True, layers_on_user_release=True
    )
    assert any(
        asset["name"] == "update-app-2.0.1.zip" for asset in payload["assets"]
    ), "the fixture must actually put the layers on the user-facing release"

    result = _service(
        tmp_path, payload, fetched, recent_releases_fetcher=lambda: [updates]
    ).get_status()

    assert result["availability"] == "incomplete"
    assert result["release"]["assetsReady"] is False
    assert result["action"] is None


def _digest_asset(name: str, *, digest: str | None, size: int = 4096) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": name,
        "state": "uploaded",
        "size": size,
        "browser_download_url": (
            f"https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/{name}"
        ),
    }
    if digest is not None:
        entry["digest"] = digest
    return entry


def test_the_release_digest_replaces_the_sidecar() -> None:
    """Seven of fourteen release assets were checksums of the other seven.

    GitHub serves a per-asset `digest` in the same authenticated release response
    as the download URL, so the sidecars were both redundant and weaker evidence:
    a separate file can be stale, mismatched or absent while the asset it
    describes is fine.
    """

    from server.updates.service import UpdateService

    name = "update-app-2.0.1.zip"
    digest = "a" * 64
    uploaded = {name: _digest_asset(name, digest=f"sha256:{digest}")}

    paired = UpdateService._paired_asset(uploaded, name, "app", tag="v2.0.1")

    assert paired is not None, "a digest alone must be enough"
    assert paired["sha256"] == digest
    assert "sha256Url" not in paired


def test_a_release_without_digests_still_uses_its_sidecar() -> None:
    """A client installed from an older release must be able to move off it."""

    from server.updates.service import UpdateService

    name = "update-app-2.0.1.zip"
    sidecar = f"{name}.sha256"
    uploaded = {
        name: _digest_asset(name, digest=None),
        sidecar: _digest_asset(sidecar, digest=None, size=90),
    }

    paired = UpdateService._paired_asset(uploaded, name, "app", tag="v2.0.1")

    assert paired is not None
    assert paired["sha256Url"].endswith(sidecar)
    assert "sha256" not in paired


def test_a_digest_that_is_not_a_usable_sha256_is_refused() -> None:
    """Never treat an unparseable digest as if it were absent-but-fine.

    Falling through to "no proof" would be the dangerous reading. Each of these
    must either fall back to a sidecar or, with none present, refuse outright.
    """

    from server.updates.service import UpdateService

    name = "update-app-2.0.1.zip"
    for bad in (
        "sha512:" + "a" * 128,   # wrong algorithm
        "sha256:" + "a" * 63,    # truncated
        "sha256:" + "g" * 64,    # not hex
        "a" * 64,                # no algorithm prefix
        "",
    ):
        assert UpdateService._asset_digest({"digest": bad}) is None, bad
        uploaded = {name: _digest_asset(name, digest=bad)}
        assert (
            UpdateService._paired_asset(uploaded, name, "app", tag="v2.0.1") is None
        ), f"{bad!r} was accepted with no sidecar present"
