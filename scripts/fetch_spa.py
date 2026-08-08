#!/usr/bin/env python3
"""Install the prebuilt SPA that a v2 release publishes beside its tag.

Running v2 must not require a Node runtime (rebuild plan goal 6).  CI builds the
interface once per tag and attaches ``waveguide-generator-v2-spa-<version>.tar.gz``
to the GitHub release with a ``.sha256`` next to it; this script downloads that
pair, **refuses to extract an archive whose digest does not match**, and installs
the result over ``frontend/dist``.

Deliberately standard library only.  It runs on whichever CPython 3.13 the
installer located, *before* ``.venv`` exists, so that a missing or corrupted
release asset is reported in seconds instead of after a multi-minute dependency
install.

What the digest does and does not prove: it proves the transfer, not the
publisher.  Anyone able to replace the archive on a release can replace the
checksum beside it.  It defends against the failures that actually happen --
a truncated download, a proxy serving a stale build, a half-written file -- and
that is the whole claim being made for it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "m3gnus/waveguide-generator"
DEFAULT_TIMEOUT = 60.0
FRONTEND = REPO_ROOT / "frontend"
DIST = FRONTEND / "dist"
STAMP_NAME = ".wg2-spa.json"
STAGING_NAME = ".wg2-spa-staging"
PREVIOUS_NAME = ".wg2-dist-previous"
MEMBER_ROOT = "dist"
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

BUILD_LOCALLY = (
    "If you are working on the interface itself, build it instead:\n"
    "  cd frontend && npm ci && npm run build"
)


class SpaError(RuntimeError):
    """A failure whose message is already written for the person installing."""


def declared_version(root: Path = REPO_ROOT) -> str:
    """The version this checkout claims to be.

    ``release.yml`` refuses to build a tag that disagrees with this file, so the
    version declared by the tree is exactly the tag whose assets we want.  That
    is what makes "get the repo at a tag, then fetch that tag's SPA" a single
    lookup rather than a second piece of state to keep in sync.
    """

    path = root / "shared" / "version.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError) as exc:
        raise SpaError(f"Could not read the declared version from {path}: {exc}") from exc


def asset_name(version: str) -> str:
    return f"waveguide-generator-v2-spa-{version}.tar.gz"


def release_base_url(repo: str, version: str) -> str:
    return f"https://github.com/{repo}/releases/download/v{version}"


def _read_url(url: str, timeout: float) -> bytes:
    """Fetch a URL, translating "it is not there" into an installer message."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - explicit URL
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _missing_release(url) from exc
        raise SpaError(f"Downloading {url} failed: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        # A file:// base URL -- which is how this path is tested without a
        # network -- reports a missing asset as a wrapped FileNotFoundError
        # rather than as HTTP 404.
        if isinstance(exc.reason, FileNotFoundError):
            raise _missing_release(url) from exc
        raise SpaError(f"Downloading {url} failed: {exc.reason}") from exc
    except OSError as exc:
        raise SpaError(f"Downloading {url} failed: {exc}") from exc


def _missing_release(url: str) -> SpaError:
    return SpaError(
        f"No release asset was found at:\n  {url}\n\n"
        "Either that release has not been published yet, or this checkout "
        "declares a version that was never tagged. Check the releases page:\n"
        "  https://github.com/" + DEFAULT_REPO + "/releases\n\n" + BUILD_LOCALLY
    )


def expected_digest(checksum_text: str, archive_name: str) -> str:
    """Parse ``sha256sum`` output and return the digest for ``archive_name``.

    ``release.yml`` writes ``sha256sum <asset> > <asset>.sha256``, so the file
    is one ``<hex>  <name>`` line.  Requiring the name to match stops a checksum
    file for a *different* asset from silently validating this one.
    """

    for line in checksum_text.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        digest = parts[0].lower()
        if not DIGEST_RE.fullmatch(digest):
            continue
        named = parts[1].lstrip("*").rsplit("/", 1)[-1] if len(parts) > 1 else archive_name
        if named == archive_name:
            return digest
    raise SpaError(
        f"The checksum file does not contain a SHA-256 line for {archive_name}. "
        "It may belong to a different release asset."
    )


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path, expected: str) -> str:
    """Return the archive's digest, or refuse loudly when it is not the expected one."""

    actual = file_digest(path)
    if actual != expected.lower():
        raise SpaError(
            "REFUSING TO EXTRACT: the downloaded SPA archive does not match its "
            "published checksum.\n"
            f"  archive:  {path.name}\n"
            f"  expected: {expected.lower()}\n"
            f"  actual:   {actual}\n\n"
            "Nothing was installed. The download was most likely truncated or "
            "served from a stale cache -- try again. If it keeps failing, do not "
            "extract the archive by hand; report it."
        )
    return actual


def _checked_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Every member must be a plain file or directory under ``dist/``.

    ``filter="data"`` already refuses absolute paths, ``..`` escapes, links that
    point outside the destination, and device nodes.  This adds the shape this
    particular archive is contracted to have, so an archive that is merely *safe*
    but not *ours* is rejected before anything is written.
    """

    members = archive.getmembers()
    if not members:
        raise SpaError("The SPA archive is empty.")
    for member in members:
        name = member.name.replace("\\", "/").rstrip("/")
        # A prefix test alone accepts "dist/../../evil.sh". The data filter
        # catches that too, but it should be refused here, in the same words as
        # everything else the archive is not allowed to contain.
        parts = [part for part in name.split("/") if part not in ("", ".")]
        escapes = not parts or ".." in parts or name.startswith("/") or ":" in parts[0]
        inside = name == MEMBER_ROOT or name.startswith(MEMBER_ROOT + "/")
        if escapes or not inside:
            hint = ""
            if name.rsplit("/", 1)[-1].startswith("._"):
                # macOS bsdtar writes an AppleDouble sidecar per file when the
                # source carries extended attributes, so an archive rehearsed on
                # a Mac does not have the shape CI's GNU tar produces.
                hint = (
                    "\nThat is a macOS AppleDouble sidecar. Repackage with "
                    "COPYFILE_DISABLE=1 tar -czf ... , or use the archive built by CI."
                )
            raise SpaError(
                f"REFUSING TO EXTRACT: the archive contains {member.name!r}, which is "
                f"outside the expected {MEMBER_ROOT}/ directory.{hint}"
            )
        if not (member.isfile() or member.isdir()):
            raise SpaError(
                f"REFUSING TO EXTRACT: the archive contains {member.name!r}, which is "
                "neither a regular file nor a directory."
            )
    return members


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def install_archive(
    archive: Path,
    *,
    version: str,
    digest: str,
    source: str,
    root: Path = REPO_ROOT,
) -> Path:
    """Extract a *verified* archive over ``frontend/dist``.

    The live directory is only swapped once a complete, self-consistent tree
    exists beside it, so an interrupted install leaves the previous interface
    working rather than half of two.
    """

    frontend = root / "frontend"
    dist = frontend / "dist"
    staging = frontend / STAGING_NAME
    previous = frontend / PREVIOUS_NAME

    frontend.mkdir(parents=True, exist_ok=True)
    _remove(staging)
    _remove(previous)
    staging.mkdir()

    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = _checked_members(handle)
            handle.extractall(staging, members=members, filter="data")
    except tarfile.TarError as exc:
        _remove(staging)
        raise SpaError(f"The SPA archive could not be read: {exc}") from exc
    except SpaError:
        _remove(staging)
        raise

    staged_dist = staging / MEMBER_ROOT
    if not (staged_dist / "index.html").is_file():
        _remove(staging)
        raise SpaError(
            "The SPA archive contains no dist/index.html, so it would install an "
            "interface that serves nothing. Nothing was changed."
        )

    (staged_dist / STAMP_NAME).write_text(
        json.dumps(
            {"version": version, "asset": archive.name, "sha256": digest, "source": source},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    if dist.exists():
        dist.rename(previous)
    try:
        staged_dist.rename(dist)
    except OSError:
        if previous.exists() and not dist.exists():
            previous.rename(dist)
        raise
    finally:
        _remove(staging)
    _remove(previous)
    return dist


def installed(root: Path = REPO_ROOT) -> dict[str, object] | None:
    """What the currently installed SPA says about itself, if it says anything.

    A locally built ``frontend/dist`` carries no stamp; that is reported as
    "present but unstamped" rather than as absent, because replacing a
    developer's build without saying so would be worse than re-downloading.
    """

    dist = root / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        return None
    try:
        return json.loads((dist / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _resolve_local_checksum(archive: Path, given: str | None) -> str:
    """A local archive still has to prove itself."""

    if given and DIGEST_RE.fullmatch(given.strip().lower()):
        return given.strip().lower()
    candidate = Path(given) if given else archive.with_name(archive.name + ".sha256")
    if not candidate.is_file():
        raise SpaError(
            f"No checksum was supplied for {archive.name}, and {candidate} does not exist.\n"
            "Download the .sha256 published beside the archive, or pass the digest "
            "with --sha256. This installer does not extract unverified archives."
        )
    return expected_digest(candidate.read_text(encoding="utf-8"), archive.name)


def run(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    version = args.version or declared_version(root)
    name = asset_name(version)
    current = installed(root)

    if args.check:
        if current is None:
            print("The built interface is missing.", file=sys.stderr)
            return 1
        if current.get("version") == version:
            print(f"SPA {version} is installed.")
            return 0
        stamped = current.get("version") or "an unrecorded build"
        print(f"The installed interface is {stamped}, not {version}.", file=sys.stderr)
        return 1

    if not args.force and current is not None and current.get("version") == version:
        print(f"SPA {version} is already installed; nothing to download.")
        return 0

    with tempfile.TemporaryDirectory(prefix="wg2-spa-") as scratch:
        if args.archive:
            archive = args.archive.resolve()
            if not archive.is_file():
                raise SpaError(f"No such archive: {archive}")
            digest = _resolve_local_checksum(archive, args.sha256)
            source = str(archive)
        else:
            base = (args.base_url or release_base_url(args.repo, version)).rstrip("/")
            source = f"{base}/{name}"
            print(f"Downloading {name}")
            # Checksum first: it is a few dozen bytes, and fetching it before the
            # archive means a release that was published without one fails before
            # anything large is transferred.
            checksum_text = _read_url(f"{source}.sha256", args.timeout).decode("utf-8", "replace")
            digest = expected_digest(checksum_text, name)
            archive = Path(scratch) / name
            archive.write_bytes(_read_url(source, args.timeout))

        verify_archive(archive, digest)
        print(f"Checksum verified: {digest}")
        install_archive(archive, version=version, digest=digest, source=source, root=root)

    print(f"Installed the prebuilt interface at {root / 'frontend' / 'dist'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help="release version to install (default: shared/version.json)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub owner/name holding the release")
    parser.add_argument("--base-url", help="directory URL holding the asset and its .sha256")
    parser.add_argument("--archive", type=Path, help="install this local archive instead of downloading")
    parser.add_argument("--sha256", help="expected digest, or a path to a .sha256 file, for --archive")
    parser.add_argument("--force", action="store_true", help="reinstall even when the version matches")
    parser.add_argument("--check", action="store_true", help="report what is installed; install nothing")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-request timeout")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (SpaError, OSError) as exc:
        # Progress goes to stdout and this goes to stderr. When the installer's
        # output is piped to a log the two are buffered independently, and
        # without this the error appears above the line explaining what was
        # being attempted.
        sys.stdout.flush()
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
