"""Download and stage signed-bundle update layers without touching the live app."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from scripts.fetch_spa import SpaError, expected_digest, file_digest
from shared.safe_names import UnsafeName, collision_key, validate_relative_name


DEFAULT_UPDATES_API_BASE = "https://api.github.com"
UPDATES_API_BASE_ENV = "WG2_UPDATES_API_BASE"
GITHUB_REPOSITORY = "m3gnus/waveguide-generator"
GITHUB_RELEASE_ORIGIN = "https://github.com"
GITHUB_RELEASE_CDN_HOSTS = frozenset({"release-assets.githubusercontent.com"})
DOWNLOAD_TIMEOUT_SECONDS = 60.0
MAX_CHECKSUM_BYTES = 64 * 1024
MEBIBYTE = 1024 * 1024
# Measured 2026-08 artifacts are 4.5/177.7 MiB compressed, 41.7/460 MiB
# expanded, and contain 465/8,179 members for app/runtime respectively.
# These per-layer limits leave growth headroom without permitting multi-GB or
# hundreds-of-thousands-of-files extraction outcomes.
APP_MAX_ARCHIVE_BYTES = 64 * MEBIBYTE
APP_MAX_EXTRACTED_BYTES = 128 * MEBIBYTE
APP_MAX_ARCHIVE_MEMBERS = 5_000
APP_MAX_MEMBER_BYTES = 64 * MEBIBYTE
RUNTIME_MAX_ARCHIVE_BYTES = 384 * MEBIBYTE
RUNTIME_MAX_EXTRACTED_BYTES = 768 * MEBIBYTE
RUNTIME_MAX_ARCHIVE_MEMBERS = 20_000
RUNTIME_MAX_MEMBER_BYTES = 256 * MEBIBYTE
MAX_COMPRESSION_RATIO = 2_000.0
DISK_SPACE_HEADROOM_BYTES = 64 * MEBIBYTE
MAX_ARCHIVE_BYTES = RUNTIME_MAX_ARCHIVE_BYTES
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
RUNTIME_ID_RE = re.compile(r"^[0-9a-f]{12}$")

ProgressCallback = Callable[[int], None]
ArchiveDownloader = Callable[[str, Path, int, ProgressCallback], None]
SmallFetcher = Callable[[str, int], bytes]
VolumeProbe = Callable[[Path], object]
FreeSpaceProbe = Callable[[Path], int]


class BundleInstallError(RuntimeError):
    """A bundle update could not be downloaded or staged safely."""


@dataclass(frozen=True, slots=True)
class LayerLimits:
    archive_bytes: int
    extracted_bytes: int
    members: int
    member_bytes: int


LAYER_LIMITS = {
    "app": LayerLimits(
        APP_MAX_ARCHIVE_BYTES,
        APP_MAX_EXTRACTED_BYTES,
        APP_MAX_ARCHIVE_MEMBERS,
        APP_MAX_MEMBER_BYTES,
    ),
    "runtime": LayerLimits(
        RUNTIME_MAX_ARCHIVE_BYTES,
        RUNTIME_MAX_EXTRACTED_BYTES,
        RUNTIME_MAX_ARCHIVE_MEMBERS,
        RUNTIME_MAX_MEMBER_BYTES,
    ),
}


def archive_size_limit(layer: str) -> int:
    try:
        return LAYER_LIMITS[layer].archive_bytes
    except KeyError as exc:
        raise BundleInstallError(f"Unsupported update layer: {layer}") from exc


def _existing_ancestor(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise OSError(f"No existing ancestor for {path}")
        candidate = parent
    return candidate


def filesystem_volume(path: Path) -> object:
    """Return a host-independent identity for the filesystem containing *path*."""

    return _existing_ancestor(path).stat().st_dev


def free_disk_bytes(path: Path) -> int:
    return shutil.disk_usage(_existing_ancestor(path)).free


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
        raise BundleInstallError("WG2_UPDATES_API_BASE is not a valid origin.") from exc
    if (
        parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise BundleInstallError(
            "WG2_UPDATES_API_BASE must be the GitHub API origin or a literal loopback origin."
        )
    if _origin(configured) == _origin(DEFAULT_UPDATES_API_BASE):
        return DEFAULT_UPDATES_API_BASE
    if parsed.scheme != "http" or not _literal_loopback_hostname(parsed.hostname):
        raise BundleInstallError(
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
    if VERSION_RE.fullmatch(parts[4].removeprefix("v")) is None or not parts[4].startswith("v"):
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
    except BundleInstallError:
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
            raise BundleInstallError("The update API URL escaped its trusted origin.")
        return
    if purpose != "asset":
        raise ValueError(f"Unsupported update URL purpose: {purpose}")
    if trusted_asset_url(url) or (
        redirect and api_base == DEFAULT_UPDATES_API_BASE and _github_release_cdn_url(url)
    ):
        return
    raise BundleInstallError("The update asset URL escaped its trusted release origin.")


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
            raise BundleInstallError("Update redirects must not downgrade from HTTPS.")
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


def _request(url: str, *, headers: Mapping[str, str] | None = None) -> urllib.request.Request:
    if not trusted_asset_url(url):
        raise BundleInstallError("Update assets must use the trusted release origin.")
    request_headers = {
        "User-Agent": "WaveguideGenerator-BundleUpdater",
        "Accept": "application/octet-stream",
    }
    if headers:
        request_headers.update(headers)
    return urllib.request.Request(url, headers=request_headers)


def fetch_url_bytes(url: str, max_bytes: int) -> bytes:
    """Fetch one small release asset with an explicit response-size ceiling."""

    try:
        with open_trusted_url(
            _request(url), timeout=DOWNLOAD_TIMEOUT_SECONDS, purpose="asset"
        ) as response:
            body = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BundleInstallError(f"Could not download {url}: {exc}") from exc
    if len(body) > max_bytes:
        raise BundleInstallError(f"Download exceeded its {max_bytes}-byte size limit: {url}")
    return body


def download_url(
    url: str,
    destination: Path,
    max_bytes: int,
    progress: ProgressCallback,
) -> None:
    """Download through a retained ``.part`` file that can safely resume."""

    if max_bytes <= 0 or max_bytes > MAX_ARCHIVE_BYTES:
        raise BundleInstallError("The advertised update archive size is invalid.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        size = destination.stat().st_size
        if size > max_bytes:
            raise BundleInstallError(f"Existing download exceeds its size limit: {destination.name}")
        progress(size)
        return

    partial = destination.with_name(destination.name + ".part")
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > max_bytes:
        partial.unlink()
        offset = 0
    elif offset == max_bytes and offset > 0:
        partial.replace(destination)
        progress(offset)
        return
    headers = {"Range": f"bytes={offset}-"} if offset else None
    try:
        with open_trusted_url(
            _request(url, headers=headers),
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            purpose="asset",
        ) as response:
            resumed = offset > 0 and getattr(response, "status", None) == 206
            mode = "ab" if resumed else "wb"
            written = offset if resumed else 0
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit():
                if written + int(content_length) > max_bytes:
                    raise BundleInstallError(
                        f"Download exceeds its {max_bytes}-byte size limit: {destination.name}"
                    )
            with partial.open(mode) as handle:
                progress(written)
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise BundleInstallError(
                            f"Download exceeds its {max_bytes}-byte size limit: {destination.name}"
                        )
                    handle.write(chunk)
                    progress(written)
                handle.flush()
                os.fsync(handle.fileno())
            if written != max_bytes:
                raise BundleInstallError(
                    f"Download ended at {written} of {max_bytes} bytes: {destination.name}"
                )
    except BundleInstallError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BundleInstallError(f"Could not download {destination.name}: {exc}") from exc
    partial.replace(destination)


def _member_path(member: zipfile.ZipInfo) -> PurePosixPath:
    raw = member.filename.replace("\\", "/")
    trimmed = raw.rstrip("/")
    parts = trimmed.split("/")
    if not trimmed or raw.startswith("/") or raw.endswith("//") or "\x00" in raw:
        raise BundleInstallError(
            f"REFUSING TO EXTRACT: the archive contains unsafe path {member.filename!r}."
        )
    try:
        for part in parts:
            validate_relative_name(part, what=f"archive path component in {member.filename!r}")
    except UnsafeName as exc:
        raise BundleInstallError(f"REFUSING TO EXTRACT: {exc}") from exc
    if any(part.startswith("._") for part in parts):
        raise BundleInstallError(
            f"REFUSING TO EXTRACT: the archive contains AppleDouble file {member.filename!r}."
        )
    mode = (member.external_attr >> 16) & 0xFFFF
    member_type = stat.S_IFMT(mode)
    if member_type == stat.S_IFLNK:
        raise BundleInstallError(
            f"REFUSING TO EXTRACT: the archive contains symlink {member.filename!r}."
        )
    if member.flag_bits & 0x1:
        raise BundleInstallError(
            f"REFUSING TO EXTRACT: the archive contains encrypted member {member.filename!r}."
        )
    if member.is_dir():
        if member_type not in {0, stat.S_IFDIR}:
            raise BundleInstallError(
                f"REFUSING TO EXTRACT: {member.filename!r} is not a regular directory."
            )
    elif member_type not in {0, stat.S_IFREG}:
        raise BundleInstallError(
            f"REFUSING TO EXTRACT: {member.filename!r} is not a regular file."
        )
    return PurePosixPath(*parts)


def extract_layer_archive(
    archive_path: Path,
    destination: Path,
    *,
    layer: str = "runtime",
    max_extracted_bytes: int | None = None,
    max_members: int | None = None,
    max_member_bytes: int | None = None,
    max_compression_ratio: float = MAX_COMPRESSION_RATIO,
) -> None:
    """Extract a flat app/runtime layer after validating every ZIP member."""

    try:
        limits = LAYER_LIMITS[layer]
    except KeyError as exc:
        raise BundleInstallError(f"Unsupported update layer: {layer}") from exc
    extracted_limit = limits.extracted_bytes if max_extracted_bytes is None else max_extracted_bytes
    member_limit = limits.members if max_members is None else max_members
    member_bytes_limit = limits.member_bytes if max_member_bytes is None else max_member_bytes
    temporary = destination.with_name(destination.name + ".extracting")
    _remove(temporary)
    _remove(destination)
    temporary.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members:
                raise BundleInstallError(f"The update archive is empty: {archive_path.name}")
            if len(members) > member_limit:
                raise BundleInstallError("The update archive contains too many members.")
            checked: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            seen: set[PurePosixPath] = set()
            seen_canonical: set[tuple[str, ...]] = set()
            extracted_bytes = 0
            for member in members:
                relative = _member_path(member)
                if relative in seen:
                    raise BundleInstallError(
                        f"REFUSING TO EXTRACT: duplicate member {member.filename!r}."
                    )
                seen.add(relative)
                canonical = tuple(collision_key(part) for part in relative.parts)
                if canonical in seen_canonical:
                    raise BundleInstallError(
                        "REFUSING TO EXTRACT: archive members collide on a "
                        f"case-insensitive filesystem: {member.filename!r}."
                    )
                seen_canonical.add(canonical)
                if not member.is_dir():
                    if member.file_size > member_bytes_limit:
                        raise BundleInstallError(
                            "REFUSING TO EXTRACT: an update archive member exceeds its size limit."
                        )
                    compressed_size = member.compress_size
                    if member.file_size and (
                        compressed_size <= 0
                        or member.file_size / compressed_size > max_compression_ratio
                    ):
                        raise BundleInstallError(
                            "REFUSING TO EXTRACT: an update archive member has an extreme "
                            "compression ratio."
                        )
                    extracted_bytes += member.file_size
                    if extracted_bytes > extracted_limit:
                        raise BundleInstallError(
                            "REFUSING TO EXTRACT: the update archive expands beyond its size limit."
                        )
                checked.append((member, relative))

            for member, relative in checked:
                target = temporary.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as output:
                    written = 0
                    while True:
                        chunk = source.read(1 << 20)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > member_bytes_limit or written > member.file_size:
                            raise BundleInstallError(
                                "REFUSING TO EXTRACT: an update archive member exceeded its "
                                "declared size."
                            )
                        output.write(chunk)
                    if written != member.file_size:
                        raise BundleInstallError(
                            "REFUSING TO EXTRACT: an update archive member ended at an "
                            "unexpected size."
                        )
                mode = ((member.external_attr >> 16) & 0o777) or 0o644
                target.chmod(mode)
                timestamp = datetime(*member.date_time, tzinfo=UTC).timestamp()
                os.utime(target, (timestamp, timestamp))
        temporary.rename(destination)
    except (
        BundleInstallError,
        NotImplementedError,
        OSError,
        OverflowError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        _remove(temporary)
        if isinstance(exc, BundleInstallError):
            raise
        raise BundleInstallError(f"Could not extract {archive_path.name}: {exc}") from exc


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _manifest(path: Path, filename: str) -> dict[str, Any]:
    try:
        payload = json.loads((path / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleInstallError(f"The staged layer has no valid {filename}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise BundleInstallError(f"The staged layer has an unsupported {filename}.")
    return payload


class BundleUpdateInstaller:
    """Own one background bundle download and expose its small state machine."""

    def __init__(
        self,
        *,
        data_dir: Path,
        destination_app_dir: Path,
        request_path: Path,
        downloader: ArchiveDownloader = download_url,
        small_fetcher: SmallFetcher = fetch_url_bytes,
        volume_probe: VolumeProbe = filesystem_volume,
        free_space_probe: FreeSpaceProbe = free_disk_bytes,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.destination_app_dir = Path(destination_app_dir).resolve()
        self.request_path = Path(request_path).resolve()
        self.downloader = downloader
        self.small_fetcher = small_fetcher
        self.volume_probe = volume_probe
        self.free_space_probe = free_space_probe
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active_job_key: tuple[object, ...] | None = None
        self._state: dict[str, object] = {
            "installState": "idle",
            "activeVersion": None,
            "downloadedBytes": 0,
            "totalBytes": 0,
            "error": None,
        }

    def status(self) -> dict[str, object]:
        with self._lock:
            self._reset_consumed_locked()
            return dict(self._state)

    def start(
        self,
        version: str,
        assets: Sequence[Mapping[str, object]],
        *,
        expected_runtime_id: str,
        installed_runtime_id: str,
    ) -> dict[str, object]:
        """Start one release-bound job; an identical active request is idempotent."""

        if VERSION_RE.fullmatch(version) is None:
            raise BundleInstallError("The bundle update version is invalid.")
        if RUNTIME_ID_RE.fullmatch(expected_runtime_id) is None or RUNTIME_ID_RE.fullmatch(
            installed_runtime_id
        ) is None:
            raise BundleInstallError("The bundle update runtime identity is invalid.")
        normalized = self._validated_assets(version, assets)
        has_runtime = any(asset["layer"] == "runtime" for asset in normalized)
        if not has_runtime and installed_runtime_id != expected_runtime_id:
            raise BundleInstallError(
                "The installed runtime does not match the runtime required by this update."
            )
        job_key = self._job_key(
            version,
            normalized,
            expected_runtime_id=expected_runtime_id,
            installed_runtime_id=installed_runtime_id,
        )

        with self._lock:
            self._reset_consumed_locked()
            if self._state["installState"] in {"downloading", "verifying", "ready"}:
                if self._active_job_key == job_key:
                    return dict(self._state)
                active_version = self._state.get("activeVersion")
                raise BundleInstallError(
                    f"Update {active_version} is already active. Wait for it to be consumed "
                    "or reset before installing a different release."
                )
            self._preflight(normalized)
            total = sum(int(asset["bytes"]) for asset in normalized)
            self._active_job_key = job_key
            self._state = {
                "installState": "downloading",
                "activeVersion": version,
                "downloadedBytes": 0,
                "totalBytes": total,
                "error": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(
                    version,
                    normalized,
                    expected_runtime_id,
                    installed_runtime_id,
                ),
                name="wg-bundle-update",
                daemon=True,
            )
            self._thread.start()
            return dict(self._state)

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the worker in tests and orderly diagnostic callers."""

        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _set_state(self, **updates: object) -> None:
        with self._lock:
            self._state.update(updates)

    def _reset_consumed_locked(self) -> None:
        if self._state["installState"] == "ready" and not self.request_path.is_file():
            self._active_job_key = None
            self._state = {
                "installState": "idle",
                "activeVersion": None,
                "downloadedBytes": 0,
                "totalBytes": 0,
                "error": None,
            }

    @staticmethod
    def _job_key(
        version: str,
        assets: Sequence[Mapping[str, object]],
        *,
        expected_runtime_id: str,
        installed_runtime_id: str,
    ) -> tuple[object, ...]:
        asset_identity = tuple(
            sorted(
                (
                    str(asset["layer"]),
                    str(asset["name"]),
                    str(asset["url"]),
                    str(asset["sha256Url"]),
                    int(asset["bytes"]),
                )
                for asset in assets
            )
        )
        return (version, expected_runtime_id, installed_runtime_id, asset_identity)

    def _preflight(self, assets: Sequence[Mapping[str, object]]) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            staging_volume = self.volume_probe(self.data_dir)
            destination_volume = self.volume_probe(self.destination_app_dir)
        except OSError as exc:
            raise BundleInstallError(f"Could not inspect update destination storage: {exc}") from exc
        if staging_volume != destination_volume:
            raise BundleInstallError(
                "The update staging directory and installed application are on different "
                "filesystems, so the update cannot be installed safely. Move the application "
                "or choose a data directory on the same volume."
            )
        required = (
            sum(int(asset["bytes"]) for asset in assets)
            + sum(LAYER_LIMITS[str(asset["layer"])].extracted_bytes for asset in assets)
            + DISK_SPACE_HEADROOM_BYTES
        )
        try:
            available = self.free_space_probe(self.data_dir)
        except OSError as exc:
            raise BundleInstallError(f"Could not inspect free disk space: {exc}") from exc
        if available < required:
            raise BundleInstallError(
                "There is not enough free disk space to download and extract this update "
                f"safely ({required} bytes required, {available} bytes available)."
            )

    @staticmethod
    def _validated_assets(
        version: str,
        assets: Sequence[Mapping[str, object]],
    ) -> tuple[dict[str, object], ...]:
        normalized: list[dict[str, object]] = []
        layers: set[str] = set()
        for asset in assets:
            name = asset.get("name")
            url = asset.get("url")
            checksum_url = asset.get("sha256Url")
            size = asset.get("bytes")
            layer = asset.get("layer")
            try:
                safe_name = validate_relative_name(name, what="bundle update asset name")
            except UnsafeName:
                safe_name = None
            archive_limit = LAYER_LIMITS.get(str(layer))
            if (
                safe_name is None
                or not safe_name.endswith(".zip")
                or not isinstance(url, str)
                or not trusted_asset_url(
                    url,
                    tag=f"v{version}" if layer == "app" else None,
                    asset_name=safe_name,
                )
                or not isinstance(checksum_url, str)
                or not trusted_asset_url(
                    checksum_url,
                    tag=f"v{version}" if layer == "app" else None,
                    asset_name=safe_name + ".sha256",
                )
                or isinstance(size, bool)
                or not isinstance(size, int)
                or archive_limit is None
                or not 0 < size <= archive_limit.archive_bytes
                or layer not in {"app", "runtime"}
                or layer in layers
            ):
                raise BundleInstallError("The bundle update action contains an invalid asset.")
            layers.add(str(layer))
            normalized.append(
                {
                    "name": name,
                    "url": url,
                    "sha256Url": checksum_url,
                    "bytes": size,
                    "layer": layer,
                }
            )
        if "app" not in layers:
            raise BundleInstallError("The bundle update action contains no app layer.")
        return tuple(normalized)

    def _run(
        self,
        version: str,
        assets: Sequence[Mapping[str, object]],
        expected_runtime_id: str,
        installed_runtime_id: str,
    ) -> None:
        try:
            update_dir = (self.data_dir / "updates" / version).resolve()
            if not update_dir.is_relative_to(self.data_dir):
                raise BundleInstallError("The bundle update directory escaped the data directory.")
            downloads = update_dir / "downloads"
            staged_root = update_dir / "staged"
            completed = 0
            archives: dict[str, Path] = {}
            for asset in assets:
                name = str(asset["name"])
                destination = downloads / name

                def report(current: int, *, base: int = completed) -> None:
                    self._set_state(downloadedBytes=base + current)

                self.downloader(str(asset["url"]), destination, int(asset["bytes"]), report)
                completed += destination.stat().st_size
                self._set_state(downloadedBytes=completed)
                archives[str(asset["layer"])] = destination

            self._set_state(installState="verifying")
            for asset in assets:
                archive = archives[str(asset["layer"])]
                checksum = self.small_fetcher(str(asset["sha256Url"]), MAX_CHECKSUM_BYTES)
                try:
                    wanted = expected_digest(checksum.decode("utf-8", "replace"), archive.name)
                except SpaError as exc:
                    raise BundleInstallError(str(exc)) from exc
                actual = file_digest(archive)
                if actual != wanted:
                    archive.unlink(missing_ok=True)
                    raise BundleInstallError(
                        f"REFUSING TO EXTRACT: {archive.name} does not match its published checksum."
                    )

            staged: dict[str, Path] = {}
            for layer, archive in archives.items():
                destination = staged_root / layer
                extract_layer_archive(archive, destination, layer=layer)
                staged[layer] = destination.resolve()

            app_manifest = _manifest(staged["app"], "APP-MANIFEST.json")
            if app_manifest.get("version") != version:
                raise BundleInstallError("The staged app manifest names a different version.")
            if not (staged["app"] / "launchers" / "apply_update.py").is_file():
                raise BundleInstallError("The staged app layer has no bundle updater.")
            required_runtime = app_manifest.get("runtimeId")
            if not isinstance(required_runtime, str) or not required_runtime:
                raise BundleInstallError("The staged app manifest has no runtime id.")
            if required_runtime != expected_runtime_id:
                raise BundleInstallError(
                    "The staged app layer does not match the release's expected runtime id."
                )
            runtime = staged.get("runtime")
            if runtime is not None:
                runtime_manifest = _manifest(runtime, "RUNTIME-MANIFEST.json")
                if runtime_manifest.get("runtimeId") != required_runtime:
                    raise BundleInstallError(
                        "The staged runtime does not match the app layer's runtime id."
                    )
            elif installed_runtime_id != required_runtime:
                raise BundleInstallError(
                    "The installed runtime does not match the staged app layer's runtime id."
                )

            payload = {
                "schemaVersion": 1,
                "kind": "apply_bundle",
                "version": version,
                "stagedAppDir": str(staged["app"]),
                "stagedRuntimeDir": str(runtime) if runtime is not None else None,
            }
            self.request_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.request_path.with_name(
                f".{self.request_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(self.request_path)
            self._set_state(installState="ready", downloadedBytes=completed, error=None)
        except Exception as exc:  # noqa: BLE001 - all worker failures become API state
            self._set_state(installState="failed", error=str(exc) or type(exc).__name__)
