"""Bundle download, verification, extraction, and handoff state contracts."""

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
import hashlib
import json
from pathlib import Path
import stat
import threading
import zipfile

import pytest

from server.updates.bundle import (
    APP_MAX_ARCHIVE_BYTES,
    BundleInstallError,
    BundleUpdateInstaller,
    extract_layer_archive,
)


RUNTIME_ID = "0123456789ab"


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


def _app_zip(version: str = "2.0.1", runtime_id: str = RUNTIME_ID) -> bytes:
    manifest = {
        "schemaVersion": 1,
        "version": version,
        "commit": "a" * 40,
        "runtimeId": runtime_id,
    }
    return _zip(
        {
            "APP-MANIFEST.json": json.dumps(manifest).encode(),
            "launchers/apply_update.py": b"# staged updater\n",
        }
    )


def _runtime_zip() -> bytes:
    manifest = {
        "schemaVersion": 1,
        "runtimeId": RUNTIME_ID,
        "python": "3.13.12",
        "platform": "macos-arm64",
    }
    return _zip(
        {
            "RUNTIME-MANIFEST.json": json.dumps(manifest).encode(),
            "bin/python3.13": b"python",
        }
    )


def _asset(
    name: str,
    payload: bytes,
    layer: str,
    *,
    version: str = "2.0.1",
) -> dict[str, object]:
    return {
        "name": name,
        "url": (
            "https://github.com/m3gnus/waveguide-generator/releases/"
            f"download/v{version}/{name}"
        ),
        "sha256Url": (
            "https://github.com/m3gnus/waveguide-generator/releases/"
            f"download/v{version}/{name}.sha256"
        ),
        "bytes": len(payload),
        "layer": layer,
    }


def _fakes(
    payloads: dict[str, bytes],
) -> tuple[
    Callable[[str, Path, int, Callable[[int], None]], None],
    Callable[[str, int], bytes],
]:
    def download(
        url: str, destination: Path, limit: int, progress: Callable[[int], None]
    ) -> None:
        payload = payloads[Path(url).name]
        assert len(payload) <= limit
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        progress(len(payload))

    def fetch(url: str, limit: int) -> bytes:
        name = Path(url).name.removesuffix(".sha256")
        value = f"{hashlib.sha256(payloads[name]).hexdigest()}  {name}\n".encode()
        assert len(value) <= limit
        return value

    return download, fetch


def _installer(tmp_path: Path, **kwargs: object) -> BundleUpdateInstaller:
    kwargs.setdefault("free_space_probe", lambda _path: 10**12)
    return BundleUpdateInstaller(
        data_dir=tmp_path / "data",
        destination_app_dir=tmp_path / "installed" / "app",
        request_path=tmp_path / "control" / "update.json",
        **kwargs,  # type: ignore[arg-type]
    )


def _start(
    installer: BundleUpdateInstaller,
    version: str,
    assets: list[dict[str, object]],
    *,
    expected_runtime_id: str = RUNTIME_ID,
    installed_runtime_id: str = RUNTIME_ID,
) -> dict[str, object]:
    return installer.start(
        version,
        assets,
        expected_runtime_id=expected_runtime_id,
        installed_runtime_id=installed_runtime_id,
    )


def test_installer_moves_through_every_state_and_writes_the_bundle_request(
    tmp_path: Path,
) -> None:
    app = _app_zip()
    runtime = _runtime_zip()
    payloads = {
        "waveguide-generator-app-2.0.1.zip": app,
        f"waveguide-generator-runtime-macos-arm64-{RUNTIME_ID}.zip": runtime,
    }
    base_download, base_fetch = _fakes(payloads)
    download_entered = threading.Event()
    allow_download = threading.Event()
    verify_entered = threading.Event()
    allow_verify = threading.Event()
    calls = 0

    def download(*args: object) -> None:
        nonlocal calls
        calls += 1
        download_entered.set()
        assert allow_download.wait(2)
        base_download(*args)  # type: ignore[arg-type]

    def fetch(*args: object) -> bytes:
        verify_entered.set()
        assert allow_verify.wait(2)
        return base_fetch(*args)  # type: ignore[arg-type]

    request = tmp_path / "control" / "update.json"
    installer = BundleUpdateInstaller(
        data_dir=tmp_path / "data",
        destination_app_dir=tmp_path / "installed" / "app",
        request_path=request,
        downloader=download,
        small_fetcher=fetch,
        free_space_probe=lambda _path: 10**12,
    )
    assets = [
        _asset("waveguide-generator-app-2.0.1.zip", app, "app"),
        _asset(
            f"waveguide-generator-runtime-macos-arm64-{RUNTIME_ID}.zip",
            runtime,
            "runtime",
        ),
    ]

    assert installer.status()["installState"] == "idle"
    first = _start(installer, "2.0.1", assets)
    assert first["installState"] == "downloading"
    assert download_entered.wait(2)
    duplicate = _start(installer, "2.0.1", assets)
    assert duplicate["installState"] == "downloading"
    assert calls == 1

    allow_download.set()
    assert verify_entered.wait(2)
    assert installer.status()["installState"] == "verifying"
    allow_verify.set()
    installer.wait(2)

    state = installer.status()
    assert state == {
        "installState": "ready",
        "activeVersion": "2.0.1",
        "downloadedBytes": len(app) + len(runtime),
        "totalBytes": len(app) + len(runtime),
        "error": None,
    }
    payload = json.loads(request.read_text(encoding="utf-8"))
    assert payload["kind"] == "apply_bundle"
    assert payload["version"] == "2.0.1"
    assert Path(payload["stagedAppDir"]).is_dir()
    assert Path(payload["stagedRuntimeDir"]).is_dir()
    request.unlink()
    assert installer.status() == {
        "installState": "idle",
        "activeVersion": None,
        "downloadedBytes": 0,
        "totalBytes": 0,
        "error": None,
    }


def test_different_release_is_rejected_while_an_identical_job_is_idempotent(
    tmp_path: Path,
) -> None:
    app = _app_zip()
    name = "waveguide-generator-app-2.0.1.zip"
    base_download, fetch = _fakes({name: app})
    entered = threading.Event()
    proceed = threading.Event()
    calls = 0

    def download(*args: object) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        assert proceed.wait(2)
        base_download(*args)  # type: ignore[arg-type]

    installer = _installer(
        tmp_path,
        downloader=download,
        small_fetcher=fetch,
    )
    assets = [_asset(name, app, "app")]
    _start(installer, "2.0.1", assets)
    assert entered.wait(2)

    assert _start(installer, "2.0.1", assets)["activeVersion"] == "2.0.1"
    other_app = _app_zip("2.0.2")
    other_name = "waveguide-generator-app-2.0.2.zip"
    with pytest.raises(BundleInstallError, match="Update 2.0.1 is already active"):
        _start(
            installer,
            "2.0.2",
            [_asset(other_name, other_app, "app", version="2.0.2")],
        )
    assert installer.status()["activeVersion"] == "2.0.1"
    assert calls == 1

    proceed.set()
    installer.wait(2)


def test_app_only_update_is_bound_to_expected_and_installed_runtime_ids(
    tmp_path: Path,
) -> None:
    detached_runtime_id = "abcdefabcdef"
    app = _app_zip(runtime_id=detached_runtime_id)
    name = "waveguide-generator-app-2.0.1.zip"
    download, fetch = _fakes({name: app})
    installer = _installer(
        tmp_path,
        downloader=download,
        small_fetcher=fetch,
    )

    _start(installer, "2.0.1", [_asset(name, app, "app")])
    installer.wait(2)

    state = installer.status()
    assert state["installState"] == "failed"
    assert "expected runtime id" in str(state["error"])
    assert not (tmp_path / "control" / "update.json").exists()


@pytest.mark.parametrize("preflight", ["volume", "disk"])
def test_storage_preflight_refuses_before_any_download(
    tmp_path: Path,
    preflight: str,
) -> None:
    downloads = 0

    def download(*_args: object) -> None:
        nonlocal downloads
        downloads += 1

    kwargs: dict[str, object] = {}
    match = "different filesystems"
    if preflight == "volume":
        kwargs["volume_probe"] = lambda path: "data" if path.name == "data" else "app"
    else:
        kwargs["free_space_probe"] = lambda _path: 0
        match = "not enough free disk space"
    installer = _installer(tmp_path, downloader=download, **kwargs)
    app = _app_zip()
    asset = _asset("waveguide-generator-app-2.0.1.zip", app, "app")

    with pytest.raises(BundleInstallError, match=match):
        _start(installer, "2.0.1", [asset])

    assert downloads == 0
    assert installer.status()["installState"] == "idle"


def test_digest_failure_is_reported_and_never_writes_a_request(tmp_path: Path) -> None:
    app = _app_zip()
    name = "waveguide-generator-app-2.0.1.zip"
    download, _fetch = _fakes({name: app})
    installer = _installer(
        tmp_path,
        downloader=download,
        small_fetcher=lambda _url, _limit: f"{'0' * 64}  {name}\n".encode(),
    )

    _start(installer, "2.0.1", [_asset(name, app, "app")])
    installer.wait(2)

    state = installer.status()
    assert state["installState"] == "failed"
    assert "checksum" in str(state["error"])
    assert not (tmp_path / "control" / "update.json").exists()


def test_advertised_archive_over_the_size_cap_fails_before_downloading(
    tmp_path: Path,
) -> None:
    downloads = 0

    def download(*_args: object) -> None:
        nonlocal downloads
        downloads += 1

    installer = _installer(tmp_path, downloader=download)
    asset = _asset("waveguide-generator-app-2.0.1.zip", b"small", "app")
    asset["bytes"] = APP_MAX_ARCHIVE_BYTES + 1

    with pytest.raises(BundleInstallError, match="invalid asset"):
        _start(installer, "2.0.1", [asset])

    assert installer.status()["installState"] == "idle"
    assert downloads == 0


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../outside",
        "/absolute",
        "._metadata",
        "dir/file:stream",
        "NUL",
        "dir/COM1.txt",
        "trailing.",
        "trailing ",
    ],
)
def test_extractor_rejects_unsafe_and_appledouble_members(
    tmp_path: Path, unsafe_name: str
) -> None:
    archive = tmp_path / "unsafe.zip"
    archive.write_bytes(_zip({unsafe_name: b"owned"}))

    with pytest.raises(BundleInstallError, match="REFUSING TO EXTRACT"):
        extract_layer_archive(archive, tmp_path / "staged")

    assert not (tmp_path / "staged").exists()
    assert not (tmp_path.parent / "outside").exists()


def test_extractor_rejects_symlink_members(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        info = zipfile.ZipInfo("python3")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        handle.writestr(info, b"python3.13")

    with pytest.raises(BundleInstallError, match="symlink"):
        extract_layer_archive(archive, tmp_path / "staged")


def test_extractor_rejects_case_insensitive_member_collisions(tmp_path: Path) -> None:
    archive = tmp_path / "collision.zip"
    archive.write_bytes(_zip({"docs/Readme.txt": b"one", "DOCS/README.TXT": b"two"}))

    with pytest.raises(BundleInstallError, match="case-insensitive"):
        extract_layer_archive(archive, tmp_path / "staged")


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"max_members": 1}, "too many members"),
        ({"max_member_bytes": 4}, "member exceeds"),
        ({"max_extracted_bytes": 6}, "expands beyond"),
    ],
)
def test_extractor_enforces_member_and_expanded_size_limits(
    tmp_path: Path,
    limits: dict[str, int],
    message: str,
) -> None:
    archive = tmp_path / "limited.zip"
    archive.write_bytes(_zip({"one": b"12345", "two": b"67890"}))

    with pytest.raises(BundleInstallError, match=message):
        extract_layer_archive(archive, tmp_path / "staged", layer="app", **limits)


def test_extractor_rejects_extreme_compression_ratios(tmp_path: Path) -> None:
    archive = tmp_path / "compressed.zip"
    archive.write_bytes(_zip({"zeros.bin": b"\0" * 100_000}))

    with pytest.raises(BundleInstallError, match="compression ratio"):
        extract_layer_archive(
            archive,
            tmp_path / "staged",
            layer="app",
            max_compression_ratio=10,
        )


def test_asset_urls_are_repository_bound_and_api_override_is_literal_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.updates import bundle as bundle_module

    monkeypatch.delenv(bundle_module.UPDATES_API_BASE_ENV, raising=False)
    assert bundle_module.updates_api_base() == "https://api.github.com"
    trusted = (
        "https://github.com/m3gnus/waveguide-generator/releases/"
        "download/v2.0.1/app.zip"
    )
    assert bundle_module.trusted_asset_url(
        trusted,
        tag="v2.0.1",
        asset_name="app.zip",
    )
    assert not bundle_module.trusted_asset_url("https://example.com/app.zip")
    assert not bundle_module.trusted_asset_url(
        "https://github.com/another/project/releases/download/v2.0.1/app.zip"
    )
    assert not bundle_module.trusted_asset_url(
        "https://release-assets.githubusercontent.com/signed/app.zip?token=value"
    )
    assert not bundle_module.trusted_asset_url("http://127.0.0.1:9/y.zip")

    monkeypatch.setenv(bundle_module.UPDATES_API_BASE_ENV, "http://127.0.0.1:9/")
    assert bundle_module.updates_api_base() == "http://127.0.0.1:9"
    assert bundle_module.trusted_asset_url("http://127.0.0.1:9/y.zip")
    assert not bundle_module.trusted_asset_url("http://127.0.0.1:10/y.zip")
    assert not bundle_module.trusted_asset_url("http://[invalid/y.zip")
    with pytest.raises(bundle_module.BundleInstallError):
        bundle_module._request("http://127.0.0.1:10/y.zip")

    monkeypatch.setenv(bundle_module.UPDATES_API_BASE_ENV, "http://evil.example/")
    with pytest.raises(bundle_module.BundleInstallError, match="literal HTTP loopback"):
        bundle_module.updates_api_base()
    assert not bundle_module.trusted_asset_url(trusted)

    monkeypatch.setenv(bundle_module.UPDATES_API_BASE_ENV, "http://localhost:8000")
    with pytest.raises(bundle_module.BundleInstallError, match="literal HTTP loopback"):
        bundle_module.updates_api_base()


def test_every_redirect_hop_stays_on_the_configured_loopback_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.updates import bundle as bundle_module

    trusted_origin = "http://127.0.0.1:8080"
    monkeypatch.setenv("WG2_UPDATES_API_BASE", trusted_origin)
    handler = bundle_module._TrustedRedirectHandler("asset")
    first = bundle_module._request(trusted_origin + "/start")

    second = handler.redirect_request(first, None, 302, "Found", {}, "/middle")

    assert second is not None
    assert second.full_url == trusted_origin + "/middle"
    with pytest.raises(BundleInstallError, match="trusted release origin"):
        handler.redirect_request(
            second,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1:9090/payload",
        )
    with pytest.raises(BundleInstallError, match="trusted release origin"):
        handler.redirect_request(
            second,
            None,
            302,
            "Found",
            {},
            "ftp://127.0.0.1/payload",
        )


def test_production_redirects_allow_the_release_cdn_without_scheme_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.updates import bundle as bundle_module

    monkeypatch.delenv(bundle_module.UPDATES_API_BASE_ENV, raising=False)
    handler = bundle_module._TrustedRedirectHandler("asset")
    first = bundle_module._request(
        "https://github.com/m3gnus/waveguide-generator/releases/"
        "download/v2.0.1/app.zip"
    )

    cdn = handler.redirect_request(
        first,
        None,
        302,
        "Found",
        {},
        "https://release-assets.githubusercontent.com/github-production-release-asset/123?token=x",
    )

    assert cdn is not None
    with pytest.raises(BundleInstallError, match="must not downgrade"):
        handler.redirect_request(
            cdn,
            None,
            302,
            "Found",
            {},
            "http://release-assets.githubusercontent.com/payload",
        )
