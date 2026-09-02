"""Release-aware update checks with a persistent, rate-conscious cache."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
import math
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

from scripts.fetch_spa import SpaError, expected_digest
from shared import release_assets
from server.platform.process import background_process_kwargs
from server.settings.store import SettingsStore
from server.updates.bundle import (
    BundleInstallError,
    BundleUpdateInstaller,
    GITHUB_REPOSITORY,
    SmallFetcher,
    archive_size_limit,
    open_trusted_url,
    trusted_asset_url,
    updates_api_base,
)


REPOSITORY = GITHUB_REPOSITORY
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RECENT_RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=20"


def latest_release_api() -> str:
    return f"{updates_api_base()}/repos/{REPOSITORY}/releases/latest"


def recent_releases_api() -> str:
    return f"{updates_api_base()}/repos/{REPOSITORY}/releases?per_page=20"


RELEASE_PAGE_ROOT = f"https://github.com/{REPOSITORY}/releases/tag"
# A release tag is `v<major>.<minor>.<patch>`, optionally followed by a SemVer
# pre-release label: `v0.4.0-beta.1`. Betas are published from `next` as GitHub
# pre-releases, which `releases/latest` does not return, so a stable install
# never sees one -- the label only has to parse for the beta channel's own scan.
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
# The strict shape, for the places that must not accept a pre-release: a beta is
# not a stable release, and the companion that carries the update layers is a
# pre-release too. See `_is_update_layer_carrier`.
STABLE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
RUNTIME_ID_RE = re.compile(r"^[0-9a-f]{12}$")

#: Where the chosen update channel is remembered.
#:
#: Server-side, and deliberately so: the setting has to survive the update it
#: controls, and a browser-scoped copy does not -- a beta install that lost the
#: preference on its first restart would silently be back on stable and the
#: channel would buy nothing. ``SettingsStore`` is a generic namespace map, so
#: this needs no schema of its own.
UPDATE_SETTINGS_NAMESPACE = "updates"
STABLE_CHANNEL = "stable"
BETA_CHANNEL = "beta"
UPDATE_CHANNELS = (STABLE_CHANNEL, BETA_CHANNEL)
CACHE_SCHEMA = 1
MAX_RESPONSE_BYTES = 1_000_000
MAX_RELEASE_LIST_BYTES = 5_000_000
MAX_MANIFEST_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 4.0
MANUAL_REFRESH_FLOOR_SECONDS = 60.0
CURRENT_TTL_SECONDS = 6 * 60 * 60
AVAILABLE_TTL_SECONDS = 12 * 60 * 60
INCOMPLETE_TTL_SECONDS = 2 * 60
ERROR_BACKOFF_SECONDS = (5 * 60, 10 * 60, 20 * 60, 60 * 60)
_CACHE_FIELDS = frozenset(
    {
        "schemaVersion",
        "etag",
        "release",
        "releaseMode",
        "availability",
        "lastAttemptEpoch",
        "checkedAtEpoch",
        "nextCheckEpoch",
        "failureCount",
        "lastError",
        "channel",
    }
)
_RELEASE_CACHE_FIELDS = frozenset({"version", "tag", "url", "publishedAt", "assetsReady"})
_BUNDLE_RELEASE_CACHE_FIELDS = frozenset({"runtimeId", "bundleAssets"})
_AVAILABILITY_VALUES = frozenset({"unknown", "incomplete", "available", "current", "ahead"})

log = logging.getLogger("wg.updates")


@dataclass(frozen=True, slots=True)
class ReleaseResponse:
    """One GitHub response, including conditional-request metadata."""

    payload: dict[str, Any] | None
    etag: str | None
    not_modified: bool = False


ReleaseFetcher = Callable[[str | None], ReleaseResponse]
RecentReleasesFetcher = Callable[[], list[dict[str, Any]]]
Clock = Callable[[], float]


class UpdateRateLimitError(RuntimeError):
    """GitHub refused the check and named when another attempt is allowed."""

    def __init__(self, message: str, *, retry_at: float | None) -> None:
        super().__init__(message)
        self.retry_at = retry_at


class UpdateInstallUnavailable(RuntimeError):
    """The current process cannot safely hand a release to the installer."""


class UpdateChannelUnavailable(RuntimeError):
    """This process has nowhere durable to remember an update channel."""


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _prerelease_precedence(label: str | None) -> tuple[Any, ...]:
    """Order a SemVer pre-release label against its own release (rule 11).

    A release outranks any pre-release sharing its core numbers, so the absent
    label sorts highest. Within pre-releases, identifiers compare left to right:
    numeric ones numerically and below alphanumeric ones, and when everything to
    the left is equal the longer set wins -- `0.4.0-beta.1` < `0.4.0-beta.1.2`.
    """

    if label is None:
        return (1,)
    identifiers: list[tuple[int, int, str]] = []
    for identifier in label.split("."):
        if identifier.isdigit():
            identifiers.append((0, int(identifier), ""))
        else:
            identifiers.append((1, 0, identifier))
    return (0, tuple(identifiers))


def _version(tag_or_version: str) -> tuple[Any, ...]:
    """Parse a tag or version into a tuple that sorts by release precedence."""

    # `updates` is a syntactically valid SemVer pre-release identifier, so
    # `v0.4.0-updates` parses as a version unless it is refused here. It is not
    # one: it names the companion release that carries another version's update
    # layers, and sorting it just below `v0.4.0` would let a companion stand in
    # for the release it belongs to.
    if tag_or_version.endswith(release_assets.UPDATES_TAG_SUFFIX):
        raise ValueError(f"Not a version, but an update companion: {tag_or_version!r}")
    match = TAG_RE.fullmatch(
        tag_or_version if tag_or_version.startswith("v") else f"v{tag_or_version}"
    )
    if match is None:
        raise ValueError(f"Unsupported release version: {tag_or_version!r}")
    major, minor, patch, label = match.groups()
    return (int(major), int(minor), int(patch), _prerelease_precedence(label))


def _is_update_layer_carrier(tag: str) -> bool:
    """May this release's assets hold the app, manifest and runtime layers?

    Today they live on the stable release itself. `-updates` companions are the
    shape they move to, so both qualify. A plain pre-release does not: a beta and
    a companion are both GitHub pre-releases, and treating `v0.4.0-beta.1` as a
    companion would offer a beta's layers to a stable install.
    """

    suffix = release_assets.UPDATES_TAG_SUFFIX
    if tag.endswith(suffix):
        return TAG_RE.fullmatch(tag.removesuffix(suffix)) is not None
    return STABLE_TAG_RE.fullmatch(tag) is not None


def _is_offerable_release_tag(tag: str, *, allow_prerelease: bool) -> bool:
    """May this tag be offered to a user as a release to move to?

    Stable keeps the narrow shape: ``releases/latest`` is defined as the most
    recent non-pre-release, so anything else arriving there is a surprise and
    should stay refused.

    Beta admits a SemVer pre-release label, but **not** an ``-updates``
    companion. Both are GitHub pre-releases, so a scan that filtered only on
    that flag would offer `v0.4.0-beta.1-updates` -- a bag of update layers with
    no installer on it -- as though it were the release itself.
    """

    if not allow_prerelease:
        return STABLE_TAG_RE.fullmatch(tag) is not None
    if tag.endswith(release_assets.UPDATES_TAG_SUFFIX):
        return False
    return TAG_RE.fullmatch(tag) is not None


def _is_installable_tag(tag: str) -> bool:
    """May WG build an install command or an install handoff from this tag?

    Widened past ``STABLE_TAG_RE`` for the beta channel. A beta is a real
    release page carrying real installer assets, and the installers take
    ``--tag <tag>`` verbatim, so refusing one here would show a beta install an
    update it could never apply -- the exact opposite of what the channel is
    for, since its whole purpose is exercising packaging and the install path
    before a stable version number is spent.

    This does not let a beta reach a stable install: ``_parse_release`` is the
    only way a tag becomes an offered release, and on the stable channel it
    still refuses every pre-release. What the narrow shape was really protecting
    against -- an ``-updates`` companion -- stays refused here.
    """

    return _is_offerable_release_tag(tag, allow_prerelease=True)


def channel_of(stored: Any) -> str:
    """Read a stored ``updates`` namespace defensively.

    The settings store is a generic map the frontend writes, so this namespace
    may hold anything at all: a corrupt file, a hand edit, or a shape from a
    later version. Only the exact recognised strings opt in; everything else
    means the stable channel, which is the answer that cannot surprise anyone.
    """

    if isinstance(stored, dict):
        channel = stored.get("channel")
        if isinstance(channel, str) and channel in UPDATE_CHANNELS:
            return channel
    return STABLE_CHANNEL


def _cache_epoch(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"update-cache {field} must be a timestamp")
    timestamp = float(value)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError(f"update-cache {field} must be a timestamp")
    try:
        _iso(timestamp)
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError(f"update-cache {field} must be a timestamp") from exc
    return timestamp


def _validated_bundle_asset(value: Any, *, release_tag: str) -> dict[str, Any]:
    # Two shapes, because a cache written before the digest change is still on
    # disk and discarding it silently would cost a user their update check for no
    # reason. `sha256` carries GitHub's own per-asset digest; `sha256Url` is the
    # older sidecar form, kept for releases that predate it.
    digest_fields = {"name", "url", "sha256", "bytes", "layer"}
    sidecar_fields = {"name", "url", "sha256Url", "bytes", "sha256Bytes", "layer"}
    if not isinstance(value, dict) or set(value) not in (digest_fields, sidecar_fields):
        raise ValueError("update-cache bundle asset has invalid fields")
    if "sha256" in value:
        digest = value.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest.lower())
        ):
            raise ValueError("update-cache bundle asset has an invalid sha256")
    name = value.get("name")
    url = value.get("url")
    checksum_url = value.get("sha256Url")
    has_sidecar = "sha256Url" in value
    size = value.get("bytes")
    checksum_size = value.get("sha256Bytes")
    layer = value.get("layer")
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or not isinstance(url, str)
        or not trusted_asset_url(
            url,
            tag=(
                release_tag
                if isinstance(layer, str) and layer in {"app", "manifest", "installer"}
                else None
            ),
            asset_name=name,
        )
        or (
            has_sidecar
            and (
                not isinstance(checksum_url, str)
                or not trusted_asset_url(
                    checksum_url,
                    tag=(
                        release_tag
                        if isinstance(layer, str)
                        and layer in {"app", "manifest", "installer"}
                        else None
                    ),
                    asset_name=release_assets.checksum_name(name),
                )
                or isinstance(checksum_size, bool)
                or not isinstance(checksum_size, int)
                or checksum_size <= 0
            )
        )
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or not isinstance(layer, str)
        or layer not in {"app", "runtime", "manifest", "installer"}
        or (
            layer in {"app", "runtime"}
            and isinstance(size, int)
            and size > archive_size_limit(layer)
        )
    ):
        raise ValueError("update-cache bundle asset is invalid")
    if not has_sidecar:
        return {
            "name": name,
            "url": url,
            "sha256": str(value["sha256"]).lower(),
            "bytes": size,
            "layer": layer,
        }
    return {
        "name": name,
        "url": url,
        "sha256Url": checksum_url,
        "bytes": size,
        "sha256Bytes": checksum_size,
        "layer": layer,
    }


def _validated_cached_release(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) not in {
        _RELEASE_CACHE_FIELDS,
        _RELEASE_CACHE_FIELDS | _BUNDLE_RELEASE_CACHE_FIELDS,
    }:
        raise ValueError("update-cache release has invalid fields")
    version = value.get("version")
    tag = value.get("tag")
    url = value.get("url")
    published_at = value.get("publishedAt")
    assets_ready = value.get("assetsReady")
    if not isinstance(version, str):
        raise ValueError("update-cache release version is invalid")
    _version(version)
    if tag != f"v{version}" or url != f"{RELEASE_PAGE_ROOT}/{tag}":
        raise ValueError("update-cache release identity is invalid")
    if not isinstance(assets_ready, bool):
        raise ValueError("update-cache release assetsReady is invalid")
    if published_at is not None:
        if not isinstance(published_at, str):
            raise ValueError("update-cache release publishedAt is invalid")
        try:
            parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("update-cache release publishedAt is invalid") from exc
        if parsed.utcoffset() is None:
            raise ValueError("update-cache release publishedAt is invalid")
    normalized = {
        "version": version,
        "tag": tag,
        "url": url,
        "publishedAt": published_at,
        "assetsReady": assets_ready,
    }
    if "runtimeId" in value:
        runtime_id = value.get("runtimeId")
        bundle_assets = value.get("bundleAssets")
        if (
            not isinstance(runtime_id, str)
            or RUNTIME_ID_RE.fullmatch(runtime_id) is None
            or not isinstance(bundle_assets, list)
        ):
            raise ValueError("update-cache bundle release is invalid")
        normalized["runtimeId"] = runtime_id
        normalized["bundleAssets"] = [
            _validated_bundle_asset(asset, release_tag=tag) for asset in bundle_assets
        ]
    return normalized


def _validated_cache(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _CACHE_FIELDS:
        raise ValueError("update-cache has invalid fields")
    schema = value.get("schemaVersion")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != CACHE_SCHEMA:
        raise ValueError("unsupported update-cache schema")

    normalized: dict[str, Any] = {"schemaVersion": CACHE_SCHEMA}
    if "etag" in value:
        etag = value["etag"]
        if not isinstance(etag, str) or not etag:
            raise ValueError("update-cache etag is invalid")
        normalized["etag"] = etag
    if "release" in value:
        normalized["release"] = _validated_cached_release(value["release"])
    if "availability" in value:
        availability = value["availability"]
        if not isinstance(availability, str) or availability not in _AVAILABILITY_VALUES:
            raise ValueError("update-cache availability is invalid")
        normalized["availability"] = availability
    if "releaseMode" in value:
        release_mode = value["releaseMode"]
        if release_mode not in {"checkout", "bundle"}:
            raise ValueError("update-cache releaseMode is invalid")
        normalized["releaseMode"] = release_mode
    if "channel" in value:
        # Which channel produced the cached release. A cache written on beta and
        # read after a switch back to stable describes a different question, so
        # this is compared exactly as ``releaseMode`` is and forces a re-check.
        channel = value["channel"]
        if channel not in UPDATE_CHANNELS:
            raise ValueError("update-cache channel is invalid")
        normalized["channel"] = channel
    for field in ("lastAttemptEpoch", "checkedAtEpoch", "nextCheckEpoch"):
        if field in value:
            normalized[field] = _cache_epoch(value[field], field)
    if "failureCount" in value:
        failures = value["failureCount"]
        if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
            raise ValueError("update-cache failureCount is invalid")
        normalized["failureCount"] = failures
    if "lastError" in value:
        last_error = value["lastError"]
        if last_error is not None and not isinstance(last_error, str):
            raise ValueError("update-cache lastError is invalid")
        normalized["lastError"] = last_error
    return normalized


def fetch_latest_release(etag: str | None = None) -> ReleaseResponse:
    """Read GitHub's publisher-selected latest full release."""

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "WaveguideGenerator-UpdateCheck",
    }
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(latest_release_api(), headers=headers)
    try:
        with open_trusted_url(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            purpose="api",
        ) as response:
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


def fetch_recent_releases() -> list[dict[str, Any]]:
    """List recent releases only when a content-addressed runtime must be located."""

    request = urllib.request.Request(
        recent_releases_api(),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "WaveguideGenerator-UpdateCheck",
        },
    )
    try:
        with open_trusted_url(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            purpose="api",
        ) as response:
            body = response.read(MAX_RELEASE_LIST_BYTES + 1)
        if len(body) > MAX_RELEASE_LIST_BYTES:
            raise RuntimeError("GitHub release list exceeded the size limit")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise RuntimeError("GitHub release list was not an array of objects")
        return payload
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not list GitHub releases: {exc}") from exc


def fetch_small_release_asset(url: str, max_bytes: int) -> bytes:
    """Fetch manifest/checksum metadata under the update check's short guard."""

    if not trusted_asset_url(url):
        raise RuntimeError("GitHub release asset URL is outside the trusted repository")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "WaveguideGenerator-UpdateCheck",
        },
    )
    try:
        with open_trusted_url(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            purpose="asset",
        ) as response:
            body = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Could not fetch release metadata: {exc}") from exc
    if len(body) > max_bytes:
        raise RuntimeError("GitHub release metadata exceeded the size limit")
    return body


def _run_git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
            **background_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _installed_bundle_identity(app_layer: Path) -> tuple[str, str]:
    try:
        app_manifest = json.loads((app_layer / "APP-MANIFEST.json").read_text(encoding="utf-8"))
        runtime_manifest = json.loads(
            (app_layer.parent / "runtime" / "RUNTIME-MANIFEST.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the installed bundle manifests: {exc}") from exc
    if not isinstance(app_manifest, dict) or app_manifest.get("schemaVersion") != 1:
        raise ValueError("The installed APP-MANIFEST.json is unsupported.")
    if not isinstance(runtime_manifest, dict) or runtime_manifest.get("schemaVersion") != 1:
        raise ValueError("The installed RUNTIME-MANIFEST.json is unsupported.")
    version = app_manifest.get("version")
    app_runtime_id = app_manifest.get("runtimeId")
    runtime_id = runtime_manifest.get("runtimeId")
    if not isinstance(version, str):
        raise ValueError("The installed app manifest has no version.")
    _version(version)
    if (
        not isinstance(app_runtime_id, str)
        or RUNTIME_ID_RE.fullmatch(app_runtime_id) is None
        or not isinstance(runtime_id, str)
        or RUNTIME_ID_RE.fullmatch(runtime_id) is None
    ):
        raise ValueError("The installed bundle manifest has an invalid runtime id.")
    if app_runtime_id != runtime_id:
        raise ValueError("The installed app and runtime manifests do not match.")
    return version, runtime_id


def checkout_status(repo_root: Path, running_version: str) -> dict[str, Any]:
    """Classify local install safety without fetching or mutating Git state."""

    if os.environ.get("WG2_BUNDLE") == "1":
        try:
            installed_version, runtime_id = _installed_bundle_identity(repo_root)
        except ValueError as exc:
            return {
                "kind": "bundle",
                "branch": None,
                "head": None,
                "atDeclaredTag": False,
                "trackedChanges": False,
                "aheadCount": None,
                "behindCount": None,
                "updateSupported": False,
                "installedVersion": running_version,
                "runtimeId": None,
                "reason": str(exc),
            }
        return {
            "kind": "bundle",
            "branch": None,
            "head": None,
            "atDeclaredTag": False,
            "trackedChanges": False,
            "aheadCount": None,
            "behindCount": None,
            "updateSupported": True,
            "installedVersion": installed_version,
            "runtimeId": runtime_id,
            "reason": None,
        }

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
    upstream = _run_git(
        repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    if upstream:
        counts = _run_git(repo_root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
        if counts:
            parts = counts.split()
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                ahead, behind = (int(parts[0]), int(parts[1]))

    if changed:
        kind = "modified"
        reason = (
            "Tracked files have local changes. Commit or stash them before installing a release."
        )
    elif at_declared_tag:
        kind = "release"
        reason = None
    elif branch is None:
        kind = "detached"
        reason = (
            "This checkout is detached at a commit that does not match its declared release tag."
        )
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


def update_action(
    repo_root: Path,
    platform_name: str,
    tag: str,
    *,
    release: dict[str, Any] | None = None,
    checkout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the appropriate bundle download or checkout installer action."""

    if not _is_installable_tag(tag):
        raise ValueError("Refusing to build an update command from an invalid tag")
    if checkout is not None and checkout.get("kind") == "bundle":
        if release is None or not isinstance(release.get("bundleAssets"), list):
            raise ValueError("The bundle release has no downloadable assets")
        required_runtime = release.get("runtimeId")
        installed_runtime = checkout.get("runtimeId")
        assets: list[dict[str, object]] = []
        for asset in release["bundleAssets"]:
            if not isinstance(asset, dict):
                continue
            layer = asset.get("layer")
            if layer == "app" or (layer == "runtime" and required_runtime != installed_runtime):
                handoff = {
                    "name": asset["name"],
                    "url": asset["url"],
                    "bytes": asset["bytes"],
                    "layer": layer,
                }
                # Pass on whichever proof this release supplied.
                if "sha256" in asset:
                    handoff["sha256"] = asset["sha256"]
                else:
                    handoff["sha256Url"] = asset["sha256Url"]
                assets.append(handoff)
        if not any(asset["layer"] == "app" for asset in assets):
            raise ValueError("The bundle release has no app layer")
        if required_runtime != installed_runtime and not any(
            asset["layer"] == "runtime" for asset in assets
        ):
            raise ValueError("The bundle release has no required runtime layer")
        return {
            "kind": "bundle_download",
            "assets": assets,
            "downloadBytes": sum(int(asset["bytes"]) for asset in assets),
        }
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
        recent_releases_fetcher: RecentReleasesFetcher = fetch_recent_releases,
        asset_fetcher: SmallFetcher = fetch_small_release_asset,
        clock: Clock = time.time,
        platform_name: str = sys.platform,
        checkout_probe: Callable[[Path, str], dict[str, Any]] = checkout_status,
        update_request_path: Path | None = None,
        bundle_installer: BundleUpdateInstaller | None = None,
        settings: SettingsStore | None = None,
    ) -> None:
        _version(running_version)
        self.running_version = running_version
        self.data_dir = Path(data_dir)
        self.repo_root = Path(repo_root).resolve()
        self.fetcher = fetcher
        self.recent_releases_fetcher = recent_releases_fetcher
        self.asset_fetcher = asset_fetcher
        self.clock = clock
        self.platform_name = platform_name
        self.checkout_probe = checkout_probe
        self.settings = settings
        self.update_request_path = (
            Path(update_request_path).resolve() if update_request_path is not None else None
        )
        self.cache_path = self.data_dir / "cache" / "update-status.json"
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._recent_releases: list[dict[str, Any]] | None = None
        self._runtime_asset_cache: dict[str, dict[str, Any] | None] = {}
        self.bundle_installer = bundle_installer
        if self.bundle_installer is None and self.update_request_path is not None:
            self.bundle_installer = BundleUpdateInstaller(
                data_dir=self.data_dir,
                destination_app_dir=self.repo_root,
                request_path=self.update_request_path,
            )

    def channel(self) -> str:
        """Which release channel this installation follows.

        Stable unless the stored preference says otherwise, including when no
        settings store was supplied -- an embedded caller without one gets the
        behaviour it had before channels existed.
        """

        if self.settings is None:
            return STABLE_CHANNEL
        return channel_of(self.settings.get(UPDATE_SETTINGS_NAMESPACE))

    def set_channel(self, channel: str) -> str:
        """Remember the channel, and make the next status check honour it.

        The cached release describes the *other* channel's question, so it is
        discarded here rather than left to expire: a user who switches to beta
        and sees the stable answer for the next twelve hours would reasonably
        conclude the switch did nothing.
        """

        if channel not in UPDATE_CHANNELS:
            raise ValueError(f"Unsupported update channel: {channel!r}")
        if self.settings is None:
            raise UpdateChannelUnavailable(
                "This process has no settings store, so the update channel cannot be changed."
            )
        with self._lock:
            self.settings.put(UPDATE_SETTINGS_NAMESPACE, {"channel": channel})
            self._recent_releases = None
            self._runtime_asset_cache.clear()
            cache = self._load_cache()
            cache.pop("release", None)
            cache.pop("etag", None)
            cache.pop("availability", None)
            cache["channel"] = channel
            cache["nextCheckEpoch"] = 0.0
            try:
                self._save_cache(cache)
            except OSError as exc:
                log.warning("Could not persist update status cache: %s", exc)
        return channel

    def _load_cache(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        try:
            candidate = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._cache = _validated_cache(candidate)
        except (OSError, ValueError, json.JSONDecodeError):
            self._cache = {"schemaVersion": CACHE_SCHEMA}
        return self._cache

    def _save_cache(self, cache: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.cache_path)

    @staticmethod
    def _uploaded_assets(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        uploaded: dict[str, dict[str, Any]] = {}
        assets = payload.get("assets")
        if not isinstance(assets, list):
            return uploaded
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = asset.get("name")
            size = asset.get("size")
            if (
                isinstance(name, str)
                and asset.get("state") == "uploaded"
                and isinstance(size, int)
                and not isinstance(size, bool)
                and size > 0
            ):
                uploaded[name] = asset
        return uploaded

    @staticmethod
    def _asset_digest(asset: dict[str, Any]) -> str | None:
        """The SHA-256 GitHub itself reports for an uploaded asset.

        GitHub serves ``digest`` as ``sha256:<hex>`` on every release asset, and
        it is strictly better evidence than the ``.sha256`` sidecar this replaced:
        it arrives inside the same authenticated release response as the download
        URL, so there is no second file that can be stale, mismatched or missing.
        The sidecars also made the release page unreadable -- seven of fourteen
        assets were checksums of the other seven.
        """

        digest = asset.get("digest")
        if not isinstance(digest, str):
            return None
        algorithm, separator, hex_digest = digest.partition(":")
        if separator != ":" or algorithm.lower() != "sha256":
            return None
        hex_digest = hex_digest.strip().lower()
        if len(hex_digest) != 64 or any(c not in "0123456789abcdef" for c in hex_digest):
            return None
        return hex_digest

    @classmethod
    def _paired_asset(
        cls, uploaded: dict[str, dict[str, Any]], name: str, layer: str, *, tag: str
    ) -> dict[str, Any] | None:
        asset = uploaded.get(name)
        if asset is None:
            return None
        url = asset.get("browser_download_url")
        if (
            not isinstance(url, str)
            or not trusted_asset_url(url, tag=tag, asset_name=name)
            or (
                layer in {"app", "runtime"}
                and int(asset["size"]) > archive_size_limit(layer)
            )
        ):
            return None
        sha256 = cls._asset_digest(asset)
        if sha256 is None:
            # Releases published before GitHub served per-asset digests, and any
            # asset it declines to digest. Fall back to the sidecar rather than
            # refusing the update: an installed client must be able to move off a
            # release cut before this change.
            checksum = uploaded.get(release_assets.checksum_name(name))
            if checksum is None:
                return None
            checksum_url = checksum.get("browser_download_url")
            if not isinstance(checksum_url, str) or not trusted_asset_url(
                checksum_url, tag=tag, asset_name=release_assets.checksum_name(name)
            ):
                return None
            return {
                "name": name,
                "url": url,
                "sha256Url": checksum_url,
                "bytes": int(asset["size"]),
                "sha256Bytes": int(checksum["size"]),
                "layer": layer,
            }
        return {
            "name": name,
            "url": url,
            "sha256": sha256,
            "bytes": int(asset["size"]),
            "layer": layer,
        }

    def _bundle_platform(self) -> str:
        if self.platform_name == "darwin":
            return "macos-arm64"
        if self.platform_name == "win32":
            return "windows-x86_64"
        return self.platform_name

    def _bundle_installer_name(self, version: str) -> str | None:
        return release_assets.installer_name(self._bundle_platform(), version)

    def _manifest_runtime_id(
        self,
        manifest_asset: dict[str, Any],
        version: str,
    ) -> str:
        if int(manifest_asset["bytes"]) > MAX_MANIFEST_BYTES:
            raise RuntimeError("The release app manifest exceeds the size limit")
        manifest_bytes = self.asset_fetcher(str(manifest_asset["url"]), MAX_MANIFEST_BYTES)
        if "sha256" in manifest_asset:
            # GitHub's own digest for the asset, already validated in shape.
            wanted = str(manifest_asset["sha256"])
        else:
            checksum_bytes = self.asset_fetcher(
                str(manifest_asset["sha256Url"]), MAX_MANIFEST_BYTES
            )
            try:
                wanted = expected_digest(
                    checksum_bytes.decode("utf-8", "replace"),
                    str(manifest_asset["name"]),
                )
            except SpaError as exc:
                raise RuntimeError(
                    f"The release app manifest checksum is invalid: {exc}"
                ) from exc
        actual = hashlib.sha256(manifest_bytes).hexdigest()
        if actual != wanted:
            raise RuntimeError("The release app manifest does not match its checksum")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("The release app manifest is not valid JSON") from exc
        runtime_id = manifest.get("runtimeId") if isinstance(manifest, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("schemaVersion") != 1
            or manifest.get("version") != version
            or not isinstance(runtime_id, str)
            or RUNTIME_ID_RE.fullmatch(runtime_id) is None
        ):
            raise RuntimeError("The release app manifest has invalid bundle identity")
        return runtime_id

    def _updates_release(self, version: str) -> dict[str, Any] | None:
        """The companion pre-release carrying this version's update layers.

        The user-facing release holds only the installers, so the layers live on
        ``v<version>-updates``. It is found in the recent-releases list that
        ``_earlier_runtime_asset`` already fetches, rather than through a request
        of its own: one list answers both questions.
        """

        if self._recent_releases is None:
            self._recent_releases = self.recent_releases_fetcher()
        wanted = release_assets.updates_tag(version)
        for release in self._recent_releases:
            if release.get("tag_name") == wanted:
                return release
        return None

    def _beta_release_payload(self) -> dict[str, Any]:
        """The highest version among recent releases, pre-releases included.

        The beta channel cannot use ``releases/latest``: GitHub defines that as
        the most recent non-pre-release, which is precisely what makes the
        stable channel free. So it scans the recent list instead -- the same one
        the companion lookup already fetches -- and takes the maximum by release
        precedence.

        ``_version`` does the filtering that matters. It refuses ``-updates``
        companions outright, so a beta's own companion (`v0.4.0-beta.1-updates`,
        a pre-release like the beta itself) can never be selected here and
        offered as a release.
        """

        if self._recent_releases is None:
            self._recent_releases = self.recent_releases_fetcher()
        best: dict[str, Any] | None = None
        best_version: tuple[Any, ...] | None = None
        for entry in self._recent_releases:
            if not isinstance(entry, dict) or entry.get("draft") is True:
                continue
            tag = entry.get("tag_name")
            if not isinstance(tag, str) or not _is_offerable_release_tag(
                tag, allow_prerelease=True
            ):
                continue
            try:
                version = _version(tag)
            except ValueError:
                continue
            if best_version is None or version > best_version:
                best, best_version = entry, version
        if best is None:
            raise RuntimeError("No recent GitHub release has a supported version tag")
        return best

    def _earlier_runtime_asset(self, runtime_id: str) -> dict[str, Any] | None:
        if runtime_id in self._runtime_asset_cache:
            return self._runtime_asset_cache[runtime_id]
        if self._recent_releases is None:
            self._recent_releases = self.recent_releases_fetcher()
        name = release_assets.runtime_layer_name(self._bundle_platform(), runtime_id)
        found: dict[str, Any] | None = None
        for release in self._recent_releases:
            tag = release.get("tag_name")
            # Runtime layers live on the release itself today and on a companion
            # pre-release once #57 lands; a plain beta carries neither.
            if not isinstance(tag, str) or not _is_update_layer_carrier(tag):
                continue
            found = self._paired_asset(
                self._uploaded_assets(release),
                name,
                "runtime",
                tag=tag,
            )
            if found is not None:
                break
        self._runtime_asset_cache[runtime_id] = found
        return found

    def _parse_release(
        self,
        payload: dict[str, Any],
        *,
        checkout: dict[str, Any] | None = None,
        allow_prerelease: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        tag = payload.get("tag_name")
        # Strict on the stable channel, where `releases/latest` never returns a
        # pre-release and anything else arriving is a surprise. The beta scan is
        # the one caller that passes ``allow_prerelease``, and even then an
        # ``-updates`` companion is refused -- see `_is_offerable_release_tag`.
        if not isinstance(tag, str) or not _is_offerable_release_tag(
            tag, allow_prerelease=allow_prerelease
        ):
            raise RuntimeError("GitHub's latest release has an unsupported version tag")
        version = tag.removeprefix("v")
        uploaded = self._uploaded_assets(payload)
        if checkout is not None and checkout.get("kind") == "bundle":
            app_name = release_assets.app_layer_name(version)
            manifest_name = release_assets.app_manifest_name(version)
            # The layers live on the companion pre-release, not on the release the
            # user downloads from. Absent, the release is simply reported
            # incomplete and no update is offered -- which is the correct answer:
            # there is nothing to install.
            updates_release = self._updates_release(version)
            updates_tag = release_assets.updates_tag(version)
            layers = self._uploaded_assets(updates_release) if updates_release else {}
            app_asset = self._paired_asset(layers, app_name, "app", tag=updates_tag)
            manifest_asset = self._paired_asset(
                layers, manifest_name, "manifest", tag=updates_tag
            )
            base_release = {
                "version": version,
                "tag": tag,
                "url": f"{RELEASE_PAGE_ROOT}/{tag}",
                "publishedAt": (
                    payload.get("published_at")
                    if isinstance(payload.get("published_at"), str)
                    else None
                ),
                "assetsReady": False,
            }
            if manifest_asset is None:
                return base_release, False
            runtime_id = self._manifest_runtime_id(manifest_asset, version)
            runtime_name = release_assets.runtime_layer_name(
                self._bundle_platform(), runtime_id
            )
            runtime_asset = self._paired_asset(
                layers, runtime_name, "runtime", tag=updates_tag
            )
            installed_runtime = checkout.get("runtimeId")
            if runtime_asset is None and installed_runtime != runtime_id:
                runtime_asset = self._earlier_runtime_asset(runtime_id)

            installer_name = self._bundle_installer_name(version)
            installer_asset = (
                self._paired_asset(uploaded, installer_name, "installer", tag=tag)
                if installer_name is not None
                else None
            )
            bundle_assets = [
                asset
                for asset in (
                    app_asset,
                    manifest_asset,
                    runtime_asset,
                    installer_asset,
                )
                if asset is not None
            ]
            ready = (
                app_asset is not None
                and manifest_asset is not None
                and (runtime_asset is not None or installed_runtime == runtime_id)
            )
            return (
                base_release
                | {
                    "assetsReady": ready,
                    "runtimeId": runtime_id,
                    "bundleAssets": bundle_assets,
                },
                ready,
            )

        archive = release_assets.spa_archive_name(version)
        expected = {archive, f"{archive}.sha256"}
        ready_assets = set(uploaded) & expected
        release = {
            "version": version,
            "tag": tag,
            "url": f"{RELEASE_PAGE_ROOT}/{tag}",
            "publishedAt": payload.get("published_at")
            if isinstance(payload.get("published_at"), str)
            else None,
            "assetsReady": ready_assets == expected,
        }
        return release, ready_assets == expected

    def _availability(self, cache: dict[str, Any], *, installed_version: str | None = None) -> str:
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
        current = _version(installed_version or self.running_version)
        if latest > current:
            return "available"
        if latest == current:
            return "current"
        return "ahead"

    def _refresh(
        self,
        cache: dict[str, Any],
        now: float,
        checkout: dict[str, Any],
        channel: str = STABLE_CHANNEL,
    ) -> None:
        cache["lastAttemptEpoch"] = now
        # A new remote observation starts here, so any release list memoized by
        # an earlier check is discarded. Within one refresh the list is still
        # fetched once and shared by the beta scan, the companion lookup and the
        # earlier-runtime search.
        self._recent_releases = None
        self._runtime_asset_cache.clear()
        try:
            release_mode = "bundle" if checkout.get("kind") == "bundle" else "checkout"
            cached_context_mismatch = (
                cache.get("releaseMode") != release_mode
                or (cache.get("channel") or STABLE_CHANNEL) != channel
            )
            if channel == BETA_CHANNEL:
                # No conditional request: this reads the release *list*, whose
                # ETag would answer a different question than the cached
                # release, and a beta check is rare enough not to need one.
                payload = self._beta_release_payload()
                response = ReleaseResponse(payload, None)
            else:
                response = self.fetcher(
                    cache.get("etag")
                    if isinstance(cache.get("etag"), str) and not cached_context_mismatch
                    else None
                )
            if response.not_modified:
                if not isinstance(cache.get("release"), dict):
                    raise RuntimeError("GitHub returned not-modified before any release was cached")
            else:
                if response.payload is None:
                    raise RuntimeError("GitHub returned no release payload")
                release, _assets_ready = self._parse_release(
                    response.payload,
                    checkout=checkout,
                    allow_prerelease=channel == BETA_CHANNEL,
                )
                cache["release"] = release
                cache["releaseMode"] = release_mode
                cache["channel"] = channel
                cache["availability"] = self._availability(
                    cache,
                    installed_version=(
                        str(checkout["installedVersion"])
                        if isinstance(checkout.get("installedVersion"), str)
                        else None
                    ),
                )
                if response.etag:
                    cache["etag"] = response.etag
                elif channel == BETA_CHANNEL:
                    # Otherwise a stale stable ETag would survive the beta check
                    # and suppress the next stable one with a 304.
                    cache.pop("etag", None)

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
        channel = self.channel()
        refreshed = False
        with self._lock:
            now = self.clock()
            cache = self._load_cache()
            # Re-evaluate persisted release facts against the version that is
            # running now; this process may be the result of an update.
            installed_version = (
                str(checkout["installedVersion"])
                if isinstance(checkout.get("installedVersion"), str)
                else None
            )
            cache["availability"] = self._availability(cache, installed_version=installed_version)
            due = now >= float(cache.get("nextCheckEpoch") or 0)
            release_mode = "bundle" if checkout.get("kind") == "bundle" else "checkout"
            # A cached answer from the other channel answers a different
            # question, so it is re-checked for the same reason a mode change is.
            # A cache with no channel at all was written before channels existed
            # and can only have come from the stable endpoint.
            cached_channel = cache.get("channel") or STABLE_CHANNEL
            if cache.get("releaseMode") != release_mode or cached_channel != channel:
                due = True
            floor_elapsed = (
                now - float(cache.get("lastAttemptEpoch") or 0) >= MANUAL_REFRESH_FLOOR_SECONDS
            )
            if due or (force and floor_elapsed):
                self._refresh(cache, now, checkout, channel)
                try:
                    self._save_cache(cache)
                except OSError as exc:
                    log.warning("Could not persist update status cache: %s", exc)
                refreshed = True

            mode_matches = cache.get("releaseMode") == release_mode and (
                cache.get("channel") or STABLE_CHANNEL
            ) == channel
            release = (
                cache.get("release")
                if mode_matches and isinstance(cache.get("release"), dict)
                else None
            )
            availability = (
                str(cache.get("availability") or "unknown") if mode_matches else "unknown"
            )
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
                action = update_action(
                    self.repo_root,
                    self.platform_name,
                    str(release["tag"]),
                    release=release,
                    checkout=checkout,
                )

            install_status = (
                self.bundle_installer.status()
                if checkout.get("kind") == "bundle" and self.bundle_installer is not None
                else {
                    "installState": "idle",
                    "activeVersion": None,
                    "downloadedBytes": 0,
                    "totalBytes": 0,
                    "error": None,
                }
            )

            return {
                "schemaVersion": 1,
                "runningVersion": installed_version or self.running_version,
                "channel": channel,
                "availability": availability,
                "freshness": freshness,
                "cached": not refreshed,
                "release": release,
                "checkedAt": _iso(float(checked_at)) if checked_at is not None else None,
                "nextCheckAt": _iso(float(cache["nextCheckEpoch"]))
                if cache.get("nextCheckEpoch") is not None
                else None,
                "checkout": checkout,
                "action": action,
                "canInstall": action is not None and self.update_request_path is not None,
                "lastError": last_error,
                **install_status,
            }

    def request_install(self) -> dict[str, object]:
        """Start bundle staging or signal the checkout installer as appropriate."""

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
        if not _is_installable_tag(tag):
            raise UpdateInstallUnavailable("The offered release tag is invalid.")

        if action.get("kind") == "bundle_download":
            installer = self.bundle_installer
            assets = action.get("assets")
            version = release.get("version")
            expected_runtime_id = release.get("runtimeId")
            checkout = status.get("checkout")
            installed_runtime_id = (
                checkout.get("runtimeId") if isinstance(checkout, dict) else None
            )
            if (
                installer is None
                or not isinstance(assets, list)
                or not isinstance(version, str)
                or not isinstance(expected_runtime_id, str)
                or not isinstance(installed_runtime_id, str)
            ):
                raise UpdateInstallUnavailable(
                    "The bundle update installer is unavailable in this process."
                )
            try:
                progress = installer.start(
                    version,
                    assets,
                    expected_runtime_id=expected_runtime_id,
                    installed_runtime_id=installed_runtime_id,
                )
            except BundleInstallError as exc:
                raise UpdateInstallUnavailable(str(exc)) from exc
            return {"accepted": True, "version": version, **progress}

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
