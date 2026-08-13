"""Release-aware update checks with a persistent, rate-conscious cache."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.request


REPOSITORY = "m3gnus/waveguide-generator"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASE_PAGE_ROOT = f"https://github.com/{REPOSITORY}/releases/tag"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
CACHE_SCHEMA = 1
MAX_RESPONSE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 4.0
MANUAL_REFRESH_FLOOR_SECONDS = 60.0
CURRENT_TTL_SECONDS = 6 * 60 * 60
AVAILABLE_TTL_SECONDS = 12 * 60 * 60
INCOMPLETE_TTL_SECONDS = 2 * 60
ERROR_BACKOFF_SECONDS = (5 * 60, 10 * 60, 20 * 60, 60 * 60)

log = logging.getLogger("wg.updates")


@dataclass(frozen=True, slots=True)
class ReleaseResponse:
    """One GitHub response, including conditional-request metadata."""

    payload: dict[str, Any] | None
    etag: str | None
    not_modified: bool = False


ReleaseFetcher = Callable[[str | None], ReleaseResponse]
Clock = Callable[[], float]


class UpdateRateLimitError(RuntimeError):
    """GitHub refused the check and named when another attempt is allowed."""

    def __init__(self, message: str, *, retry_at: float | None) -> None:
        super().__init__(message)
        self.retry_at = retry_at


class UpdateInstallUnavailable(RuntimeError):
    """The current process cannot safely hand a release to the installer."""


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _version(tag_or_version: str) -> tuple[int, int, int]:
    match = TAG_RE.fullmatch(tag_or_version if tag_or_version.startswith("v") else f"v{tag_or_version}")
    if match is None:
        raise ValueError(f"Unsupported release version: {tag_or_version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def fetch_latest_release(etag: str | None = None) -> ReleaseResponse:
    """Read GitHub's publisher-selected latest full release."""

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "WaveguideGenerator-UpdateCheck",
    }
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(LATEST_RELEASE_API, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed HTTPS URL
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RuntimeError("GitHub release response exceeded the size limit")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("GitHub release response was not an object")
            return ReleaseResponse(payload, response.headers.get("ETag"))
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return ReleaseResponse(None, etag, not_modified=True)
        if exc.code in (403, 429):
            retry_after = exc.headers.get("Retry-After")
            reset = exc.headers.get("X-RateLimit-Reset")
            retry_at: float | None = None
            if retry_after and retry_after.isdigit():
                retry_at = time.time() + int(retry_after)
            elif reset and reset.isdigit():
                retry_at = float(reset)
            suffix = f"; retry after {retry_after or reset}" if retry_after or reset else ""
            raise UpdateRateLimitError(
                f"GitHub rate-limited the update check{suffix}", retry_at=retry_at
            ) from exc
        raise RuntimeError(f"GitHub update check failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not check GitHub releases: {exc}") from exc


def _run_git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def checkout_status(repo_root: Path, running_version: str) -> dict[str, Any]:
    """Classify local install safety without fetching or mutating Git state."""

    if _run_git(repo_root, "rev-parse", "--is-inside-work-tree") != "true":
        return {
            "kind": "unsupported",
            "branch": None,
            "head": None,
            "atDeclaredTag": False,
            "trackedChanges": False,
            "aheadCount": None,
            "behindCount": None,
            "updateSupported": False,
            "reason": "This folder is not a Git checkout, so the release installer cannot switch versions.",
        }

    head = _run_git(repo_root, "rev-parse", "HEAD")
    status_output = _run_git(repo_root, "status", "--porcelain", "--untracked-files=no")
    if head is None or status_output is None:
        return {
            "kind": "unsupported",
            "branch": None,
            "head": head,
            "atDeclaredTag": False,
            "trackedChanges": False,
            "aheadCount": None,
            "behindCount": None,
            "updateSupported": False,
            "reason": "WG could not inspect this Git checkout safely, so it will not suggest an update command.",
        }
    branch = _run_git(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    changed = bool(status_output)
    declared_commit = _run_git(
        repo_root, "rev-parse", "--verify", f"refs/tags/v{running_version}^{{commit}}"
    )
    at_declared_tag = bool(head and declared_commit and head == declared_commit)

    ahead: int | None = None
    behind: int | None = None
    upstream = _run_git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        counts = _run_git(repo_root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
        if counts:
            parts = counts.split()
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                ahead, behind = (int(parts[0]), int(parts[1]))

    if changed:
        kind = "modified"
        reason = "Tracked files have local changes. Commit or stash them before installing a release."
    elif at_declared_tag:
        kind = "release"
        reason = None
    elif branch is None:
        kind = "detached"
        reason = "This checkout is detached at a commit that does not match its declared release tag."
    else:
        kind = "development"
        reason = "This checkout contains development commits beyond its declared product version."

    return {
        "kind": kind,
        "branch": branch,
        "head": head,
        "atDeclaredTag": at_declared_tag,
        "trackedChanges": changed,
        "aheadCount": ahead,
        "behindCount": behind,
        "updateSupported": kind == "release",
        "reason": reason,
    }


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def update_action(repo_root: Path, platform_name: str, tag: str) -> dict[str, str]:
    """Build a display/copy command from fixed local paths and a validated tag."""

    if TAG_RE.fullmatch(tag) is None:
        raise ValueError("Refusing to build an update command from an invalid tag")
    if platform_name == "win32":
        installer = repo_root / "installers" / "windows" / "install-and-update.bat"
        command = f"& {_powershell_quote(str(installer))} --tag {_powershell_quote(tag)}"
        return {"kind": "copy_command", "shell": "PowerShell", "command": command}
    if platform_name == "darwin":
        installer = repo_root / "installers" / "macos" / "install-wg.command"
    else:
        installer = repo_root / "installers" / "linux" / "install.sh"
    command = f"bash {shlex.quote(str(installer))} --tag {shlex.quote(tag)}"
    return {"kind": "copy_command", "shell": "Terminal", "command": command}


class UpdateService:
    """Serialize remote checks and expose cached observations to every tab."""

    def __init__(
        self,
        *,
        running_version: str,
        data_dir: Path,
        repo_root: Path,
        fetcher: ReleaseFetcher = fetch_latest_release,
        clock: Clock = time.time,
        platform_name: str = sys.platform,
        checkout_probe: Callable[[Path, str], dict[str, Any]] = checkout_status,
        update_request_path: Path | None = None,
    ) -> None:
        _version(running_version)
        self.running_version = running_version
        self.data_dir = Path(data_dir)
        self.repo_root = Path(repo_root).resolve()
        self.fetcher = fetcher
        self.clock = clock
        self.platform_name = platform_name
        self.checkout_probe = checkout_probe
        self.update_request_path = (
            Path(update_request_path).resolve() if update_request_path is not None else None
        )
        self.cache_path = self.data_dir / "cache" / "update-status.json"
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    def _load_cache(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        try:
            candidate = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict) or candidate.get("schemaVersion") != CACHE_SCHEMA:
                raise ValueError("unsupported update-cache schema")
            self._cache = candidate
        except (OSError, ValueError, json.JSONDecodeError):
            self._cache = {"schemaVersion": CACHE_SCHEMA}
        return self._cache

    def _save_cache(self, cache: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.cache_path)

    def _parse_release(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        tag = payload.get("tag_name")
        if not isinstance(tag, str) or TAG_RE.fullmatch(tag) is None:
            raise RuntimeError("GitHub's latest release has an unsupported version tag")
        version = tag.removeprefix("v")
        archive = f"waveguide-generator-v2-spa-{version}.tar.gz"
        expected = {archive, f"{archive}.sha256"}
        ready_assets: set[str] = set()
        assets = payload.get("assets")
        if isinstance(assets, list):
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = asset.get("name")
                if (
                    isinstance(name, str)
                    and name in expected
                    and asset.get("state") == "uploaded"
                    and isinstance(asset.get("size"), int)
                    and asset["size"] > 0
                ):
                    ready_assets.add(name)
        release = {
            "version": version,
            "tag": tag,
            "url": f"{RELEASE_PAGE_ROOT}/{tag}",
            "publishedAt": payload.get("published_at") if isinstance(payload.get("published_at"), str) else None,
            "assetsReady": ready_assets == expected,
        }
        return release, ready_assets == expected

    def _availability(self, cache: dict[str, Any]) -> str:
        """Compare the cached release to *this process's* running version.

        The cache survives an update. Persisting an ``available`` verdict from
        v2.0.0 and trusting it after v2.0.1 starts would advertise the release
        that is already running for up to twelve hours.
        """

        release = cache.get("release")
        if not isinstance(release, dict) or not isinstance(release.get("version"), str):
            return "unknown"
        if release.get("assetsReady") is not True:
            return "incomplete"
        latest = _version(release["version"])
        current = _version(self.running_version)
        if latest > current:
            return "available"
        if latest == current:
            return "current"
        return "ahead"

    def _refresh(self, cache: dict[str, Any], now: float) -> None:
        cache["lastAttemptEpoch"] = now
        try:
            response = self.fetcher(cache.get("etag") if isinstance(cache.get("etag"), str) else None)
            if response.not_modified:
                if not isinstance(cache.get("release"), dict):
                    raise RuntimeError("GitHub returned not-modified before any release was cached")
            else:
                if response.payload is None:
                    raise RuntimeError("GitHub returned no release payload")
                release, _assets_ready = self._parse_release(response.payload)
                cache["release"] = release
                cache["availability"] = self._availability(cache)
                if response.etag:
                    cache["etag"] = response.etag

            availability = cache.get("availability")
            ttl = (
                INCOMPLETE_TTL_SECONDS
                if availability == "incomplete"
                else AVAILABLE_TTL_SECONDS
                if availability == "available"
                else CURRENT_TTL_SECONDS
            )
            cache.update(
                checkedAtEpoch=now,
                nextCheckEpoch=now + ttl,
                failureCount=0,
                lastError=None,
            )
        except Exception as exc:  # noqa: BLE001 - every remote/cache failure becomes status
            failures = int(cache.get("failureCount") or 0) + 1
            backoff = ERROR_BACKOFF_SECONDS[min(failures - 1, len(ERROR_BACKOFF_SECONDS) - 1)]
            if isinstance(exc, UpdateRateLimitError) and exc.retry_at is not None:
                backoff = max(backoff, exc.retry_at - now)
            cache["failureCount"] = failures
            cache["lastError"] = str(exc)
            cache["nextCheckEpoch"] = now + backoff
            cache.setdefault("availability", "unknown")
            log.warning("Update check failed: %s", exc)

    def get_status(self, *, force: bool = False) -> dict[str, Any]:
        checkout = self.checkout_probe(self.repo_root, self.running_version)
        refreshed = False
        with self._lock:
            now = self.clock()
            cache = self._load_cache()
            # Re-evaluate persisted release facts against the version that is
            # running now; this process may be the result of an update.
            cache["availability"] = self._availability(cache)
            due = now >= float(cache.get("nextCheckEpoch") or 0)
            floor_elapsed = now - float(cache.get("lastAttemptEpoch") or 0) >= MANUAL_REFRESH_FLOOR_SECONDS
            if due or (force and floor_elapsed):
                self._refresh(cache, now)
                try:
                    self._save_cache(cache)
                except OSError as exc:
                    log.warning("Could not persist update status cache: %s", exc)
                refreshed = True

            release = cache.get("release") if isinstance(cache.get("release"), dict) else None
            availability = str(cache.get("availability") or "unknown")
            checked_at = cache.get("checkedAtEpoch")
            last_error = cache.get("lastError") if isinstance(cache.get("lastError"), str) else None
            freshness = "unknown" if checked_at is None else "stale" if last_error else "fresh"
            action = None
            if (
                availability == "available"
                and release is not None
                and release.get("assetsReady") is True
                and checkout.get("updateSupported") is True
            ):
                action = update_action(self.repo_root, self.platform_name, str(release["tag"]))

            return {
                "schemaVersion": 1,
                "runningVersion": self.running_version,
                "availability": availability,
                "freshness": freshness,
                "cached": not refreshed,
                "release": release,
                "checkedAt": _iso(float(checked_at)) if checked_at is not None else None,
                "nextCheckAt": _iso(float(cache["nextCheckEpoch"])) if cache.get("nextCheckEpoch") is not None else None,
                "checkout": checkout,
                "action": action,
                "canInstall": action is not None and self.update_request_path is not None,
                "lastError": last_error,
            }

    def request_install(self) -> dict[str, object]:
        """Validate and atomically signal the status owner to install a release."""

        request_path = self.update_request_path
        if request_path is None:
            raise UpdateInstallUnavailable(
                "Automatic installation is available only when WG is running from its status window."
            )

        status = self.get_status()
        release = status.get("release")
        action = status.get("action")
        if (
            status.get("availability") != "available"
            or not isinstance(release, dict)
            or release.get("assetsReady") is not True
            or not isinstance(action, dict)
            or status.get("canInstall") is not True
        ):
            raise UpdateInstallUnavailable(
                "The update is no longer ready for automatic installation. Check again and resolve any checkout warning."
            )

        tag = str(release.get("tag") or "")
        if TAG_RE.fullmatch(tag) is None:
            raise UpdateInstallUnavailable("The offered release tag is invalid.")

        payload = {
            "schemaVersion": 1,
            "kind": "install_release",
            "tag": tag,
            # Give the HTTP response time to reach the browser before the
            # status owner observes this file and gracefully stops the server.
            "readyAtEpoch": time.time() + 0.75,
        }
        temporary = request_path.with_name(
            f".{request_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(request_path)
        except OSError as exc:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise UpdateInstallUnavailable(
                f"WG could not create the update handoff: {exc}"
            ) from exc
        return {"accepted": True, "tag": tag}
