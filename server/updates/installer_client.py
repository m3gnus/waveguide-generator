"""Nonblocking signed-installer client. Installation belongs to the handoff layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from typing import Callable
import urllib.request

from launchers.update_lock import ordinary_path, resolved_path

from .bundle import GITHUB_REPOSITORY, _validate_url, open_trusted_url, trusted_asset_url
from .install_kind import current_platform, probe_install
from .installer_checker import (
    MAX_INSTALLER_BYTES, MAX_MANIFEST_BYTES, InstallerClientError, check_releases, fetch_release, version_precedence,
)
from .installer_checker import ReleaseResponse as ReleaseResponse
from .installer_outcome import read_outcome
from .manifest import verify_file, verify_manifest

log = logging.getLogger("wg.updates.installer")
CHECK_INTERVAL = 6 * 60 * 60
SPACE_RESERVE = 64 * 1024 * 1024
MAX_CACHE_BYTES = 12 * 1024 * 1024


@dataclass(frozen=True)
class VerifiedInstaller:
    tag: str
    version: str
    path: Path
    sha256: str
    size: int
    install_root: Path
    platform: str


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


class InstallerClient:
    def __init__(
        self, *, running_version: str, data_dir: Path, repo_root: Path,
        platform_name: str | None = None, settings=None,
        fetcher: Callable | None = None, opener: Callable | None = None,
        clock: Callable = time.time, install_probe: Callable | None = None,
        disk_usage: Callable = shutil.disk_usage,
    ) -> None:
        version_precedence(running_version)
        self.running_version, self.data_dir, self.repo_root = running_version, Path(data_dir), Path(repo_root)
        self.platform_name = current_platform() if platform_name is None else platform_name
        self.settings, self.fetcher = settings, fetcher or fetch_release
        self.opener, self.clock, self.disk_usage = opener or open_trusted_url, clock, disk_usage
        self.install_probe = install_probe or probe_install
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._generation = 0
        self._checking = False
        self._scheduler: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._download: threading.Thread | None = None
        self._download_cancel = threading.Event()
        self._release: dict | None = None
        self._proof: dict | None = None
        self._responses: dict = {}
        self._checked: float | None = None
        self._next_check = 0.0
        self._last_error: str | None = None
        self._ready: VerifiedInstaller | None = None
        self._download_proof: dict | None = None
        self._progress = {"installState": "idle", "downloadedBytes": 0, "totalBytes": 0,
                          "activeVersion": None, "error": None}
        self._channel = self.channel()
        self._last_outcome = read_outcome(self.data_dir)
        self._load_cache()

    def channel(self) -> str:
        stored = self.settings.get("updates") if self.settings is not None else None
        return "beta" if isinstance(stored, dict) and stored.get("channel") == "beta" else "stable"

    def set_channel(self, channel: str) -> str:
        if channel not in {"stable", "beta"}:
            raise ValueError("Choose stable or beta.")
        with self._lock:
            self._ensure_open()
            if self._download is not None and self._download.is_alive():
                raise InstallerClientError("The channel cannot change during an update download.")
            if self.settings is None:
                raise InstallerClientError("This process has no update settings store.")
            self.settings.put("updates", {"channel": channel})
            self._invalidate_channel(channel)
            self._start_check()
        return channel

    def _invalidate_channel(self, channel: str) -> None:
        self._generation += 1
        self._channel = channel
        self._checking = False
        self._release = self._proof = self._ready = self._download_proof = None
        self._responses = {}
        self._checked, self._next_check, self._last_error = None, 0.0, None
        self._progress.update(installState="idle", activeVersion=None, error=None,
                              downloadedBytes=0, totalBytes=0)

    def _ensure_open(self) -> None:
        if self._closed.is_set():
            raise InstallerClientError("The update client is shutting down.")

    def _ensure_download_active(self) -> None:
        self._ensure_open()
        if self._download_cancel.is_set():
            raise InstallerClientError(str(self._progress.get("error") or "The update download was cancelled."))

    def cancel_download(self, reason: str = "The update download was cancelled.") -> dict:
        with self._lock:
            self._download_cancel.set()
            self._ready = self._download_proof = None
            self._progress.update(installState="failed", error=reason)
            return copy.deepcopy(self._progress)

    def reset_download(self) -> dict:
        with self._lock:
            self._ensure_open()
            if self._download is not None and self._download.is_alive():
                raise InstallerClientError("The update download has not stopped yet.")
            self._ready = self._download_proof = None
            self._progress.update(installState="idle", downloadedBytes=0, totalBytes=0, activeVersion=None, error=None)
            return copy.deepcopy(self._progress)

    @property
    def _cache_path(self) -> Path:
        return self.data_dir / "update-install" / "release-cache.json"

    def _load_cache(self) -> None:
        try:
            path = self._cache_path
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CACHE_BYTES:
                return
            cache = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cache, dict) or cache.get("channel") != self._channel or cache.get("schemaVersion") != 1:
                return
            release, proof = cache.get("release"), cache.get("proof")
            if release is not None:
                if not isinstance(release, dict) or not isinstance(proof, dict):
                    return
                if (not isinstance(release.get("tag"), str) or not isinstance(release.get("version"), str)
                        or not isinstance(proof.get("manifest"), str) or not isinstance(proof.get("signature"), str)
                        or not isinstance(proof.get("assets"), dict)):
                    return
                if len(proof["manifest"]) > MAX_MANIFEST_BYTES * 2 or len(proof["signature"]) != 128:
                    return
                entries = verify_manifest(bytes.fromhex(proof["manifest"]), bytes.fromhex(proof["signature"]), release["tag"])
                version_precedence(release["tag"])
                if release["tag"] != "v" + release["version"]:
                    return
                from shared import release_assets
                if release_assets.is_build_stamp(release["tag"]) or (
                    self._channel == "stable" and release_assets.is_prerelease(release["tag"])
                ):
                    return
                expected = set(release_assets.user_download_names(release["version"]))
                if not expected.issubset(entries) or not expected.issubset(proof["assets"]):
                    return
                for name in expected:
                    asset = proof["assets"][name]
                    if (not isinstance(asset, dict) or asset.get("name") != name
                            or type(asset.get("size")) is not int or not 0 < asset["size"] <= MAX_INSTALLER_BYTES
                            or not isinstance(asset.get("url"), str)
                            or not trusted_asset_url(asset["url"], tag=release["tag"], asset_name=name)):
                        return
                name = release_assets.user_download_name(self.platform_name, release["version"])
                # Signed installer proofs do not authenticate display metadata.
                # Reconstruct trusted fields and normalize the rest exactly as
                # a fresh API observation does.
                release = {"version": release["version"], "tag": release["tag"],
                    "url": f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/{release['tag']}",
                    "publishedAt": release.get("publishedAt") if isinstance(release.get("publishedAt"), str) else None,
                    "notes": release.get("notes", "")[:65536] if isinstance(release.get("notes"), str) else "",
                    "assetsReady": True,
                    "installer": proof["assets"][name] | {"sha256": entries[name]} if name else None}
            checked = cache.get("checked")
            if type(checked) not in {int, float} or not 0 <= checked <= self.clock():
                return
            self._release, self._proof, self._checked = release, proof, checked
            responses = cache.get("responses")
            self._responses = {url: value for url, value in responses.items() if isinstance(url, str) and isinstance(value, dict)} if isinstance(responses, dict) else {}
            self._next_check = checked + CHECK_INTERVAL
        except (OSError, ValueError, KeyError, TypeError, InstallerClientError):
            log.debug("Ignoring an invalid full-installer cache", exc_info=True)

    def _save_cache(self) -> None:
        path = self._cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schemaVersion": 1, "channel": self._channel, "release": self._release,
                   "proof": self._proof, "checked": self._checked, "responses": self._responses}
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _start_check(self) -> None:
        if self._checking or self._closed.is_set():
            return
        self._checking = True
        generation, channel, responses = self._generation, self._channel, copy.deepcopy(self._responses)
        self._worker = threading.Thread(target=self._check, args=(generation, channel, responses), daemon=True, name="wg-installer-check")
        self._worker.start()

    def _check(self, generation: int, channel: str, responses: dict) -> None:
        error = None
        try:
            release, proof, refreshed = check_releases(channel=channel, platform_name=self.platform_name,
                                                      fetcher=self.fetcher, opener=self.opener, responses=responses,
                                                      running_version=self.running_version)
        except Exception as exc:  # remote failures are status, never startup failures
            release = proof = None
            refreshed = responses
            error = str(exc) or type(exc).__name__
        with self._lock:
            if self._closed.is_set() or generation != self._generation:
                return
            self._checking = False
            self._checked = self.clock()
            self._next_check = self._checked + CHECK_INTERVAL
            self._last_error = error
            if error is None:
                self._release, self._proof, self._responses = release, proof, refreshed
                try:
                    self._save_cache()
                except OSError:
                    log.warning("Could not persist the full-installer release cache", exc_info=True)

    def start_checker(self) -> None:
        with self._lock:
            if self._closed.is_set() or self._scheduler is not None:
                return
            if self.clock() >= self._next_check:
                self._start_check()
            self._scheduler = threading.Thread(target=self._schedule, daemon=True, name="wg-installer-schedule")
            self._scheduler.start()

    def _schedule(self) -> None:
        while not self._closed.wait(60):
            with self._lock:
                if self.clock() >= self._next_check:
                    self._start_check()

    def close(self) -> None:
        # No join: a network read may still be completing its bounded timeout.
        self._closed.set()

    def get_status(self, *, force: bool = False) -> dict:
        with self._lock:
            # Keep automatic startup and a forced check atomic: a fast worker
            # must not finish between them and trigger a duplicate request.
            self.start_checker()
            channel = self.channel()
            if channel != self._channel and self._progress["installState"] not in {"downloading", "verifying"}:
                self._invalidate_channel(channel)
            if force or self.clock() >= self._next_check:
                self._start_check()
            checkout = self.install_probe(self.repo_root, self.running_version, self.platform_name)
            release = self._release
            availability = "unknown" if self._checked is None or (self._release is None and self._last_error) else "incomplete"
            if release:
                latest, current = version_precedence(release["version"]), version_precedence(self.running_version)
                availability = "available" if latest > current else "current" if latest == current else "ahead"
            action = None
            if availability == "available" and checkout["updateSupported"] and release.get("installer"):
                installer = release["installer"]
                action = {"kind": "full_installer", "tag": release["tag"], "version": release["version"],
                          "name": installer["name"], "size": installer["size"]}
            return copy.deepcopy({"schemaVersion": 2, "runningVersion": self.running_version,
                "channel": self._channel, "availability": availability,
                "freshness": "unknown" if self._checked is None else "stale" if self._last_error or self.clock() >= self._next_check else "fresh",
                "cached": not self._checking, "checking": self._checking,
                "checkedAt": _iso(self._checked), "nextCheckAt": _iso(self._next_check) if self._checked is not None else None,
                "lastError": self._last_error, "release": release, "checkout": checkout,
                "action": action, "canInstall": action is not None and not self._closed.is_set(),
                "lastOutcome": self._last_outcome, **self._progress})

    def request_download(self, on_verified: Callable[[VerifiedInstaller], None] | None = None) -> dict:
        with self._lock:
            self._ensure_open()
            status = self.get_status()
            if self._download is not None and self._download.is_alive():
                raise InstallerClientError("An update download is already running.")
            if not status["canInstall"] or self._proof is None:
                raise InstallerClientError("No signed update is ready for this installation.")
            release, proof, checkout = copy.deepcopy(self._release), copy.deepcopy(self._proof), status["checkout"]
            self._ready = None
            self._download_cancel = threading.Event()
            self._progress.update(installState="downloading", downloadedBytes=0,
                                  totalBytes=release["installer"]["size"], activeVersion=release["version"], error=None)
            self._download = threading.Thread(target=self._download_installer, args=(release, proof, checkout, on_verified), daemon=True, name="wg-installer-download")
            self._download.start()
            return {"accepted": True, "version": release["version"], **self._progress}

    def _download_installer(self, release: dict, proof: dict, checkout: dict, callback: Callable | None) -> None:
        temporary: Path | None = None
        created = False
        try:
            asset, tag = release["installer"], release["tag"]
            entries = verify_manifest(bytes.fromhex(proof["manifest"]), bytes.fromhex(proof["signature"]), tag)
            if not trusted_asset_url(asset["url"], tag=tag, asset_name=asset["name"]):
                raise InstallerClientError("The installer URL is outside this release.")
            folder = self.data_dir / "update-install" / release["version"]
            folder.mkdir(parents=True, exist_ok=True)
            if folder.is_symlink() or resolved_path(folder) != Path(ordinary_path(str(folder.absolute()))):
                raise InstallerClientError("The update download folder is not a regular directory.")
            if self.disk_usage(folder).free < asset["size"] + SPACE_RESERVE:
                raise InstallerClientError("There is not enough free space to download the installer.")
            target = folder / asset["name"]
            temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.part")
            self._ensure_download_active()
            with temporary.open("xb") as output:
                created = True
                with self.opener(urllib.request.Request(asset["url"]), timeout=30.0, purpose="asset") as response:
                    _validate_url(response.geturl(), purpose="asset", redirect=response.geturl() != asset["url"])
                    header = response.headers.get("Content-Length")
                    if header is not None and (not header.isdecimal() or int(header) != asset["size"]):
                        raise InstallerClientError("The installer Content-Length differs from the release size.")
                    received = 0
                    deadline = time.monotonic() + 30 * 60
                    while True:
                        self._ensure_download_active()
                        if time.monotonic() > deadline:
                            raise InstallerClientError("The installer download exceeded its time limit.")
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > asset["size"]:
                            raise InstallerClientError("The installer exceeds its declared size.")
                        output.write(chunk)
                        with self._lock:
                            self._progress["downloadedBytes"] = received
                output.flush()
                os.fsync(output.fileno())
            if received != asset["size"]:
                raise InstallerClientError("The installer download was truncated.")
            with self._lock:
                self._progress["installState"] = "verifying"
            verify_file(entries, asset["name"], temporary)
            self._ensure_download_active()
            temporary.replace(target)
            ready = VerifiedInstaller(tag, release["version"], target, entries[asset["name"]], asset["size"], Path(checkout["installRoot"]), self.platform_name)
            with self._lock:
                self._ready, self._download_proof = ready, proof
                self._progress["installState"] = "ready"
            self._ensure_download_active()
            if callback is not None:
                callback(ready)
        except Exception as exc:
            with self._lock:
                self._ready = self._download_proof = None
                self._progress.update(installState="failed", error=str(exc) or type(exc).__name__)
        finally:
            if temporary is not None and created:
                temporary.unlink(missing_ok=True)

    def verified_installer(self) -> VerifiedInstaller:
        with self._lock:
            self._ensure_download_active()
            ready, proof = self._ready, self._download_proof
            if ready is None or proof is None:
                raise InstallerClientError("The installer has not been verified.")
        before = ready.path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != ready.size:
            raise InstallerClientError("The downloaded installer was replaced or truncated.")
        entries = verify_manifest(bytes.fromhex(proof["manifest"]), bytes.fromhex(proof["signature"]), ready.tag)
        # A full installer may be large; status and cancellation must remain
        # responsive while its checksum is read.
        verify_file(entries, ready.path.name, ready.path)
        after = ready.path.lstat()
        if (not stat.S_ISREG(after.st_mode)
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise InstallerClientError("The downloaded installer changed during verification.")
        checkout = self.install_probe(self.repo_root, self.running_version, self.platform_name)
        destination = Path(ordinary_path(str(ready.install_root)))
        if (not checkout["updateSupported"]
                or Path(ordinary_path(str(Path(checkout["installRoot"])))) != destination):
            raise InstallerClientError("The installation destination changed before handoff.")
        with self._lock:
            self._ensure_download_active()
            if self._ready is not ready or self._download_proof is not proof:
                raise InstallerClientError("The verified installer is no longer the active download.")
            return ready
