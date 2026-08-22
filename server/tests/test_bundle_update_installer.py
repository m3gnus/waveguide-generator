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
    BundleInstallError,
    BundleUpdateInstaller,
    MAX_ARCHIVE_BYTES,
    extract_layer_archive,
)


RUNTIME_ID = "0123456789ab"


def _zip(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, value)
    return output.getvalue()


def _app_zip(version: str = "2.0.1") -> bytes:
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


def _asset(name: str, payload: bytes, layer: str) -> dict[str, object]:
    return {
        "name": name,
        "url": f"https://github.com/example/releases/download/v2.0.1/{name}",
        "sha256Url": f"https://github.com/example/releases/download/v2.0.1/{name}.sha256",
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
        request_path=request,
        downloader=download,
        small_fetcher=fetch,
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
    first = installer.start("2.0.1", assets)
    assert first["installState"] == "downloading"
    assert download_entered.wait(2)
    duplicate = installer.start("2.0.1", assets)
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
        "downloadedBytes": len(app) + len(runtime),
        "totalBytes": len(app) + len(runtime),
        "error": None,
    }
    payload = json.loads(request.read_text(encoding="utf-8"))
    assert payload["kind"] == "apply_bundle"
    assert payload["version"] == "2.0.1"
    assert Path(payload["stagedAppDir"]).is_dir()
    assert Path(payload["stagedRuntimeDir"]).is_dir()


def test_digest_failure_is_reported_and_never_writes_a_request(tmp_path: Path) -> None:
    app = _app_zip()
    name = "waveguide-generator-app-2.0.1.zip"
    download, _fetch = _fakes({name: app})
    installer = BundleUpdateInstaller(
        data_dir=tmp_path / "data",
        request_path=tmp_path / "control" / "update.json",
        downloader=download,
        small_fetcher=lambda _url, _limit: f"{'0' * 64}  {name}\n".encode(),
    )

    installer.start("2.0.1", [_asset(name, app, "app")])
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

    installer = BundleUpdateInstaller(
        data_dir=tmp_path / "data",
        request_path=tmp_path / "control" / "update.json",
        downloader=download,
    )
    asset = _asset("waveguide-generator-app-2.0.1.zip", b"small", "app")
    asset["bytes"] = MAX_ARCHIVE_BYTES + 1

    installer.start("2.0.1", [asset])
    installer.wait(2)

    assert installer.status()["installState"] == "failed"
    assert "invalid asset" in str(installer.status()["error"])
    assert downloads == 0


@pytest.mark.parametrize("unsafe_name", ["../outside", "/absolute", "._metadata"])
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


def test_asset_urls_trust_https_and_loopback_http_only_under_a_loopback_api_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.updates import bundle as bundle_module

    monkeypatch.delenv(bundle_module.UPDATES_API_BASE_ENV, raising=False)
    assert bundle_module.updates_api_base() == "https://api.github.com"
    assert bundle_module.trusted_asset_url("https://github.com/x/y.zip")
    assert not bundle_module.trusted_asset_url("http://127.0.0.1:9/y.zip")
    assert not bundle_module.trusted_asset_url("http://github.com/x/y.zip")

    monkeypatch.setenv(bundle_module.UPDATES_API_BASE_ENV, "http://127.0.0.1:9/")
    assert bundle_module.updates_api_base() == "http://127.0.0.1:9"
    assert bundle_module.trusted_asset_url("http://127.0.0.1:9/y.zip")
    assert not bundle_module.trusted_asset_url("http://github.com/x/y.zip")
    with pytest.raises(bundle_module.BundleInstallError):
        bundle_module._request("http://github.com/x/y.zip")

    monkeypatch.setenv(bundle_module.UPDATES_API_BASE_ENV, "http://evil.example/")
    assert not bundle_module.trusted_asset_url("http://127.0.0.1:9/y.zip")
