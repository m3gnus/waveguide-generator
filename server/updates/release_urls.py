"""Trusted origins for release discovery and asset downloads.

Every release URL the update checker and installer client follow is bound to
this repository on GitHub, or to a literal loopback origin set for a private
rehearsal through ``WG2_UPDATES_API_BASE``. Redirects are checked hop by hop.
"""

from __future__ import annotations

import ipaddress
import os
import urllib.parse
import urllib.request

from shared.release_assets import is_release_tag

DEFAULT_UPDATES_API_BASE = "https://api.github.com"
UPDATES_API_BASE_ENV = "WG2_UPDATES_API_BASE"
GITHUB_REPOSITORY = "m3gnus/waveguide-generator"
GITHUB_RELEASE_ORIGIN = "https://github.com"
GITHUB_RELEASE_CDN_HOSTS = frozenset({"release-assets.githubusercontent.com"})


class ReleaseUrlError(RuntimeError):
    """A release URL or the configured API origin is not trusted."""


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None or parsed.hostname is None:
        return None
    if parsed.scheme == "https":
        return ("https", parsed.hostname.lower(), port or 443)
    if parsed.scheme == "http":
        return ("http", parsed.hostname.lower(), port or 80)
    return None


def _literal_loopback_hostname(hostname: str | None) -> bool:
    if hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def updates_api_base() -> str:
    """Return the releases API origin; overridable for loopback rehearsals only."""

    configured = os.environ.get(UPDATES_API_BASE_ENV, "").strip() or DEFAULT_UPDATES_API_BASE
    try:
        parsed = urllib.parse.urlsplit(configured)
        parsed.port
    except ValueError as exc:
        raise ReleaseUrlError("WG2_UPDATES_API_BASE is not a valid origin.") from exc
    if (
        parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ReleaseUrlError(
            "WG2_UPDATES_API_BASE must be the GitHub API origin or a literal loopback origin."
        )
    if _origin(configured) == _origin(DEFAULT_UPDATES_API_BASE):
        return DEFAULT_UPDATES_API_BASE
    if parsed.scheme != "http" or not _literal_loopback_hostname(parsed.hostname):
        raise ReleaseUrlError(
            "WG2_UPDATES_API_BASE must be the GitHub API origin or a literal HTTP loopback origin."
        )
    return urllib.parse.urlunsplit(("http", parsed.netloc, "", "", ""))


def _github_release_asset_url(
    url: str,
    *,
    tag: str | None = None,
    asset_name: str | None = None,
) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
    except ValueError:
        return False
    if (
        _origin(url) != _origin(GITHUB_RELEASE_ORIGIN)
        or parsed.query
        or parsed.fragment
    ):
        return False
    raw_parts = parsed.path.removeprefix("/").split("/")
    if len(raw_parts) != 6:
        return False
    parts = [urllib.parse.unquote(part) for part in raw_parts]
    repository_parts = GITHUB_REPOSITORY.split("/")
    if parts[:4] != [*repository_parts, "releases", "download"]:
        return False
    # A tag is a release of this project or its supporting-assets companion
    # -- one shape, shared with the release parser so the two cannot drift. It admits pre-release labels, which is what a beta is; every such
    # asset is still bound to this repository by the checks above, so this widens
    # the tag shape and not the origin.
    if not is_release_tag(parts[4]):
        return False
    if any("/" in part or "\\" in part for part in parts):
        return False
    return (tag is None or parts[4] == tag) and (
        asset_name is None or parts[5] == asset_name
    )


def _github_release_cdn_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in GITHUB_RELEASE_CDN_HOSTS
        and parsed.port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and bool(parsed.path and parsed.path != "/")
        and not parsed.fragment
    )


def trusted_asset_url(
    url: str,
    *,
    tag: str | None = None,
    asset_name: str | None = None,
) -> bool:
    """Accept repository-bound GitHub assets or the configured rehearsal origin."""

    try:
        api_base = updates_api_base()
    except ReleaseUrlError:
        return False
    if api_base == DEFAULT_UPDATES_API_BASE:
        return _github_release_asset_url(url, tag=tag, asset_name=asset_name)
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and _origin(url) == _origin(api_base)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _validate_url(url: str, *, purpose: str, redirect: bool = False) -> None:
    api_base = updates_api_base()
    if purpose == "api":
        parsed = urllib.parse.urlsplit(url)
        if _origin(url) != _origin(api_base) or parsed.fragment:
            raise ReleaseUrlError("The update API URL escaped its trusted origin.")
        return
    if purpose != "asset":
        raise ValueError(f"Unsupported update URL purpose: {purpose}")
    if trusted_asset_url(url) or (
        redirect and api_base == DEFAULT_UPDATES_API_BASE and _github_release_cdn_url(url)
    ):
        return
    raise ReleaseUrlError("The update asset URL escaped its trusted release origin.")


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, purpose: str) -> None:
        super().__init__()
        self.purpose = purpose

    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, file_pointer, code, message, headers, new_url
    ):
        target = urllib.parse.urljoin(request.full_url, new_url)
        previous_scheme = urllib.parse.urlsplit(request.full_url).scheme
        target_scheme = urllib.parse.urlsplit(target).scheme
        if previous_scheme == "https" and target_scheme != "https":
            raise ReleaseUrlError("Update redirects must not downgrade from HTTPS.")
        _validate_url(target, purpose=self.purpose, redirect=True)
        return super().redirect_request(
            request, file_pointer, code, message, headers, target
        )


def open_trusted_url(
    request: urllib.request.Request,
    *,
    timeout: float,
    purpose: str,
):
    """Open one update URL with origin checks applied to every redirect hop."""

    _validate_url(request.full_url, purpose=purpose)
    opener = urllib.request.build_opener(_TrustedRedirectHandler(purpose))
    response = opener.open(request, timeout=timeout)
    try:
        _validate_url(
            response.geturl(),
            purpose=purpose,
            redirect=response.geturl() != request.full_url,
        )
    except Exception:
        response.close()
        raise
    return response
