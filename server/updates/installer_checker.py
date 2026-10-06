"""Signed full-installer release eligibility and bounded GitHub pagination."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from shared import release_assets

from .bundle import GITHUB_REPOSITORY, _validate_url, open_trusted_url, trusted_asset_url, updates_api_base
from .manifest import ManifestError, verify_manifest

MAX_API_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024
TIMEOUT_SECONDS = 4.0
MAX_RELEASE_PAGES = 5
_CORE = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
_TAG = re.compile(rf"^v{_CORE}(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")


class InstallerClientError(RuntimeError):
    """The full-installer client refused an unsafe or unavailable operation."""


@dataclass(frozen=True)
class ReleaseResponse:
    payload: Any = None
    etag: str | None = None
    link: str | None = None
    not_modified: bool = False


def version_precedence(version: str) -> tuple:
    match = _TAG.fullmatch(version if version.startswith("v") else "v" + version)
    if match is None or version.endswith(release_assets.UPDATES_TAG_SUFFIX):
        raise InstallerClientError("The release version is unsupported.")
    label = match.group(4)
    if label and any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in label.split(".")):
        raise InstallerClientError("The release has a non-canonical SemVer identifier.")
    return release_assets.version_precedence(version)


def fetch_release(url: str, etag: str | None = None) -> ReleaseResponse:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "WaveguideGenerator-UpdateCheck"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with open_trusted_url(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_SECONDS, purpose="api") as response:
            body = response.read(MAX_API_BYTES + 1)
            if len(body) > MAX_API_BYTES:
                raise InstallerClientError("Release metadata exceeds its size limit.")
            return ReleaseResponse(json.loads(body), response.headers.get("ETag"), response.headers.get("Link"))
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return ReleaseResponse(not_modified=True)
        raise InstallerClientError(f"Release check returned HTTP {exc.code}.") from exc


def small_asset(asset: dict, *, tag: str, limit: int, opener: Callable) -> bytes:
    if not trusted_asset_url(asset["url"], tag=tag, asset_name=asset["name"]):
        raise InstallerClientError("Release metadata uses an untrusted asset URL.")
    with opener(urllib.request.Request(asset["url"]), timeout=TIMEOUT_SECONDS, purpose="asset") as response:
        _validate_url(response.geturl(), purpose="asset", redirect=response.geturl() != asset["url"])
        body = response.read(limit + 1)
    if len(body) > limit or len(body) != asset["size"]:
        raise InstallerClientError("Release metadata does not match its declared size.")
    return body


def eligible_release(payload: Any, *, channel: str, platform_name: str | None, opener: Callable) -> tuple[dict, dict] | None:
    if not isinstance(payload, dict) or payload.get("draft") is not False:
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        return None
    try:
        version_precedence(tag)
        if release_assets.is_build_stamp(tag):
            return None
        if channel == "stable" and (release_assets.is_prerelease(tag) or payload.get("prerelease") is not False):
            return None
        if type(payload.get("prerelease")) is not bool or payload["prerelease"] != release_assets.is_prerelease(tag):
            return None
        version = tag[1:]
        expected = set(release_assets.user_download_names(version)) | {"SHA256SUMS", "SHA256SUMS.sig"}
        assets = {}
        for asset in payload.get("assets", []):
            if not isinstance(asset, dict) or asset.get("name") not in expected:
                continue
            name, size, url = asset["name"], asset.get("size"), asset.get("browser_download_url")
            if (name in assets or asset.get("state") != "uploaded" or type(size) is not int
                    or size <= 0 or size > MAX_INSTALLER_BYTES or not isinstance(url, str)
                    or not trusted_asset_url(url, tag=tag, asset_name=name)):
                return None
            assets[name] = {"name": name, "size": size, "url": url}
        if set(assets) != expected:
            return None
        data = small_asset(assets["SHA256SUMS"], tag=tag, limit=MAX_MANIFEST_BYTES, opener=opener)
        signature = small_asset(assets["SHA256SUMS.sig"], tag=tag, limit=64, opener=opener)
        entries = verify_manifest(data, signature, tag)
        if not set(release_assets.user_download_names(version)).issubset(entries):
            return None
        installer_name = release_assets.user_download_name(platform_name, version)
        installer = assets[installer_name] | {"sha256": entries[installer_name]} if installer_name else None
        release = {"version": version, "tag": tag,
                   "url": f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/{tag}",
                   "publishedAt": payload.get("published_at") if isinstance(payload.get("published_at"), str) else None,
                   "notes": (payload.get("body") or "")[:65536] if isinstance(payload.get("body"), str) else "",
                   "assetsReady": True, "installer": installer}
        proof = {"manifest": data.hex(), "signature": signature.hex(), "assets": assets}
        return release, proof
    except (ValueError, KeyError, TypeError, InstallerClientError, ManifestError, OSError):
        return None


def next_page(link: str | None, *, current: str, page: int) -> str | None:
    if not link:
        return None
    found = [match.group(1) for match in re.finditer(r'<([^>]+)>\s*;\s*rel="next"', link)]
    if not found:
        return None
    if len(found) != 1:
        raise InstallerClientError("Release pagination has ambiguous next links.")
    url = urllib.parse.urljoin(current, found[0])
    parsed, original = urllib.parse.urlsplit(url), urllib.parse.urlsplit(current)
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    if (parsed.scheme != original.scheme or parsed.netloc != original.netloc
            or parsed.path != f"/repos/{GITHUB_REPOSITORY}/releases"
            or parsed.fragment or parsed.username or parsed.password
            or query != {"per_page": ["100"], "page": [str(page + 1)]}):
        raise InstallerClientError("Release pagination escaped the trusted next page.")
    return url


def check_releases(*, channel: str, platform_name: str | None, fetcher: Callable, opener: Callable, responses: dict,
                   running_version: str | None = None) -> tuple[dict | None, dict | None, dict]:
    root = f"{updates_api_base()}/repos/{GITHUB_REPOSITORY}/releases"
    refreshed = {}

    def read_response(url: str) -> tuple[Any, str | None]:
        old = responses.get(url, {})
        response = fetcher(url, old.get("etag"))
        if response.not_modified:
            if "payload" not in old:
                raise InstallerClientError("Not-modified response has no cached release metadata.")
            payload, link = old["payload"], old.get("link")
            refreshed[url] = old
        else:
            payload, link = response.payload, response.link
            refreshed[url] = {"payload": payload, "link": link, "etag": response.etag}
        return payload, link

    if channel == "stable":
        payload, _ = read_response(root + "/latest")
        result = eligible_release(payload, channel=channel, platform_name=platform_name, opener=opener)
        if result is not None:
            return *result, refreshed

    # An old client may not trust latest's signing key. Find a signed bridge
    # without relaxing eligibility or offering a version below its installation.
    minimum = version_precedence(running_version) if running_version is not None else None
    best = None
    best_version = None
    url = root + "?per_page=100&page=1"
    for page in range(1, MAX_RELEASE_PAGES + 1):
        candidates, link = read_response(url)
        if not isinstance(candidates, list) or len(candidates) > 100:
            raise InstallerClientError("Release list is malformed or unbounded.")
        # GitHub pages are not guaranteed to be in SemVer order. Stable fallback
        # takes the highest verified version across all bounded pages; beta keeps
        # its existing first-eligible-page behavior.
        ordered = []
        for candidate in candidates:
            try:
                if isinstance(candidate, dict) and isinstance(candidate.get("tag_name"), str):
                    ordered.append((version_precedence(candidate["tag_name"]), candidate))
            except InstallerClientError:
                continue
        for version, candidate in sorted(ordered, key=lambda item: item[0], reverse=True):
            if channel == "stable" and ((minimum is not None and version < minimum)
                                         or (best_version is not None and version <= best_version)):
                continue
            result = eligible_release(candidate, channel=channel, platform_name=platform_name, opener=opener)
            if result is not None:
                if channel != "stable":
                    return *result, refreshed
                best, best_version = result, version
                break
        if page == MAX_RELEASE_PAGES:
            break
        following = next_page(link, current=url, page=page)
        if following is None:
            break
        url = following
    if best is not None:
        return *best, refreshed
    return None, None, refreshed
