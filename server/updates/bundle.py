"""Download and stage signed-bundle update layers without touching the live app."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
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


DEFAULT_UPDATES_API_BASE = "https://api.github.com"
UPDATES_API_BASE_ENV = "WG2_UPDATES_API_BASE"
DOWNLOAD_TIMEOUT_SECONDS = 60.0
MAX_ARCHIVE_BYTES = 2_000_000_000
MAX_CHECKSUM_BYTES = 64 * 1024
MAX_EXTRACTED_BYTES = 4_000_000_000
MAX_ARCHIVE_MEMBERS = 250_000
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

ProgressCallback = Callable[[int], None]
ArchiveDownloader = Callable[[str, Path, int, ProgressCallback], None]
SmallFetcher = Callable[[str, int], bytes]


class BundleInstallError(RuntimeError):
    """A bundle update could not be downloaded or staged safely."""


def updates_api_base() -> str:
    """Return the releases API origin; overridable for loopback rehearsals only."""

    return os.environ.get(UPDATES_API_BASE_ENV, "").strip().rstrip("/") or DEFAULT_UPDATES_API_BASE


def _loopback_http(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def trusted_asset_url(url: str) -> bool:
    """HTTPS always; plain HTTP only to loopback while the API base is loopback too.

    The override exists so an end-to-end update rehearsal can serve a fake
    release from a local stdlib server. It never widens trust beyond the
    machine itself.
    """

    if url.startswith("https://"):
        return True
    return _loopback_http(url) and _loopback_http(updates_api_base())


def _request(url: str, *, headers: Mapping[str, str] | None = None) -> urllib.request.Request:
    if not trusted_asset_url(url):
        raise BundleInstallError("Update assets must use HTTPS URLs.")
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
        with urllib.request.urlopen(  # noqa: S310 - HTTPS is checked above
            _request(url), timeout=DOWNLOAD_TIMEOUT_SECONDS
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
        with urllib.request.urlopen(  # noqa: S310 - HTTPS is checked by _request
            _request(url, headers=headers), timeout=DOWNLOAD_TIMEOUT_SECONDS
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
    parts = [part for part in trimmed.split("/") if part not in {"", "."}]
    if (
        not trimmed
        or raw.startswith("/")
        or "\x00" in raw
        or not parts
        or ".." in parts
        or ":" in parts[0]
    ):
        raise BundleInstallError(
            f"REFUSING TO EXTRACT: the archive contains unsafe path {member.filename!r}."
        )
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
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES,
) -> None:
    """Extract a flat app/runtime layer after validating every ZIP member."""

    temporary = destination.with_name(destination.name + ".extracting")
    _remove(temporary)
    _remove(destination)
    temporary.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members:
                raise BundleInstallError(f"The update archive is empty: {archive_path.name}")
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise BundleInstallError("The update archive contains too many members.")
            checked: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            seen: set[PurePosixPath] = set()
            extracted_bytes = 0
            for member in members:
                relative = _member_path(member)
                if relative in seen:
                    raise BundleInstallError(
                        f"REFUSING TO EXTRACT: duplicate member {member.filename!r}."
                    )
                seen.add(relative)
                if not member.is_dir():
                    extracted_bytes += member.file_size
                    if extracted_bytes > max_extracted_bytes:
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
                    shutil.copyfileobj(source, output, length=1 << 20)
                mode = ((member.external_attr >> 16) & 0o777) or 0o644
                target.chmod(mode)
                timestamp = datetime(*member.date_time, tzinfo=UTC).timestamp()
                os.utime(target, (timestamp, timestamp))
        temporary.rename(destination)
    except (BundleInstallError, OSError, OverflowError, ValueError, zipfile.BadZipFile) as exc:
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
        request_path: Path,
        downloader: ArchiveDownloader = download_url,
        small_fetcher: SmallFetcher = fetch_url_bytes,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.request_path = Path(request_path).resolve()
        self.downloader = downloader
        self.small_fetcher = small_fetcher
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, object] = {
            "installState": "idle",
            "downloadedBytes": 0,
            "totalBytes": 0,
            "error": None,
        }

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)

    def start(self, version: str, assets: Sequence[Mapping[str, object]]) -> dict[str, object]:
        """Start once; repeated requests while active return the same progress."""

        with self._lock:
            if self._state["installState"] in {"downloading", "verifying", "ready"}:
                return dict(self._state)
            copied = tuple(dict(asset) for asset in assets)
            total = sum(
                int(asset.get("bytes", 0))
                for asset in copied
                if isinstance(asset.get("bytes"), int)
            )
            self._state = {
                "installState": "downloading",
                "downloadedBytes": 0,
                "totalBytes": total,
                "error": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(version, copied),
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

    @staticmethod
    def _validated_assets(assets: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
        normalized: list[dict[str, object]] = []
        layers: set[str] = set()
        for asset in assets:
            name = asset.get("name")
            url = asset.get("url")
            checksum_url = asset.get("sha256Url")
            size = asset.get("bytes")
            layer = asset.get("layer")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.endswith(".zip")
                or not isinstance(url, str)
                or not isinstance(checksum_url, str)
                or isinstance(size, bool)
                or not isinstance(size, int)
                or not 0 < size <= MAX_ARCHIVE_BYTES
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

    def _run(self, version: str, assets: Sequence[Mapping[str, object]]) -> None:
        try:
            if VERSION_RE.fullmatch(version) is None:
                raise BundleInstallError("The bundle update version is invalid.")
            normalized = self._validated_assets(assets)
            update_dir = (self.data_dir / "updates" / version).resolve()
            if not update_dir.is_relative_to(self.data_dir):
                raise BundleInstallError("The bundle update directory escaped the data directory.")
            downloads = update_dir / "downloads"
            staged_root = update_dir / "staged"
            completed = 0
            archives: dict[str, Path] = {}
            for asset in normalized:
                name = str(asset["name"])
                destination = downloads / name

                def report(current: int, *, base: int = completed) -> None:
                    self._set_state(downloadedBytes=base + current)

                self.downloader(str(asset["url"]), destination, int(asset["bytes"]), report)
                completed += destination.stat().st_size
                self._set_state(downloadedBytes=completed)
                archives[str(asset["layer"])] = destination

            self._set_state(installState="verifying")
            for asset in normalized:
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
                extract_layer_archive(archive, destination)
                staged[layer] = destination.resolve()

            app_manifest = _manifest(staged["app"], "APP-MANIFEST.json")
            if app_manifest.get("version") != version:
                raise BundleInstallError("The staged app manifest names a different version.")
            if not (staged["app"] / "launchers" / "apply_update.py").is_file():
                raise BundleInstallError("The staged app layer has no bundle updater.")
            required_runtime = app_manifest.get("runtimeId")
            if not isinstance(required_runtime, str) or not required_runtime:
                raise BundleInstallError("The staged app manifest has no runtime id.")
            runtime = staged.get("runtime")
            if runtime is not None:
                runtime_manifest = _manifest(runtime, "RUNTIME-MANIFEST.json")
                if runtime_manifest.get("runtimeId") != required_runtime:
                    raise BundleInstallError(
                        "The staged runtime does not match the app layer's runtime id."
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
