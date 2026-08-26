#!/usr/bin/env python3
"""Move the product version, in every file that carries a copy of it.

``shared/version.json`` is the single source at runtime: ``server/app.py``
reads it for ``/health`` and the FastAPI metadata, and ``frontend/vite.config.ts``
injects it as ``__WG2_VERSION__`` at build time. But npm keeps its own copies in
``frontend/package.json`` and ``frontend/package-lock.json``, and those drifted
to 2.4.1 and 1.0.0 respectively while the runtime said something else again.

The release workflow refuses to build when the tag disagrees with
``shared/version.json``, so a drifted npm copy will not stop a release -- it
will ship one that misreports itself in ``npm ls`` and in any SBOM built from
the lockfile. Hence one command that moves all of them, and ``--check`` to prove
they agree.

    python scripts/bump_version.py --check
    python scripts/bump_version.py patch      # 2.0.0 -> 2.0.1
    python scripts/bump_version.py minor      # 2.0.1 -> 2.1.0
    python scripts/bump_version.py major      # 2.1.0 -> 3.0.0
    python scripts/bump_version.py --set 2.0.0

Releasing is then: bump, commit, ``git tag v<version>``, push the tag. The tag
must match, which is what makes this the last chance to notice a drift.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import sys

_IMPORT_ROOT = Path(
    os.environ.get("WG2_APP_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from server.platform.paths import app_root  # noqa: E402


REPO_ROOT = app_root()
VERSION_FILE = REPO_ROOT / "shared" / "version.json"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
PACKAGE_LOCK = REPO_ROOT / "frontend" / "package-lock.json"
#: Regenerated from the live application rather than rewritten, because the
#: snapshot has a "version" key on many schemas and only one of them is the
#: product's. It is still a copy of the version, so it still drifts on a bump.
OPENAPI_SNAPSHOT = REPO_ROOT / "docs" / "reference" / "openapi.v1.json"
APP_PLIST = (
    REPO_ROOT
    / "launchers"
    / "macos"
    / "Waveguide Generator.app"
    / "Contents"
    / "Info.plist"
)

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionError(RuntimeError):
    """A version file is missing, unreadable, or not a plain semver triple."""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionError(f"{path.relative_to(REPO_ROOT)}: {exc}") from exc


def parse(version: str, *, where: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if match is None:
        # Pre-release and build metadata are deliberately unsupported: the tag
        # check builds "v" + this string, and a release named v2.0.0-rc.1+build
        # is not something the installer or the update check can compare.
        raise VersionError(f"{where}: {version!r} is not a MAJOR.MINOR.PATCH version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def current() -> str:
    version = _read_json(VERSION_FILE).get("version")
    if not isinstance(version, str):
        raise VersionError("shared/version.json has no string 'version'")
    parse(version, where="shared/version.json")
    return version


def declared_versions() -> dict[str, str]:
    """Every copy, keyed by a repo-relative description of where it lives."""

    lock = _read_json(PACKAGE_LOCK)
    root_package = lock.get("packages", {}).get("", {})
    try:
        app = plistlib.loads(APP_PLIST.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise VersionError(f"{APP_PLIST.relative_to(REPO_ROOT)}: {exc}") from exc
    return {
        "shared/version.json": _read_json(VERSION_FILE).get("version"),
        "frontend/package.json": _read_json(PACKAGE_JSON).get("version"),
        "frontend/package-lock.json": lock.get("version"),
        'frontend/package-lock.json packages[""]': root_package.get("version"),
        "macOS app CFBundleShortVersionString": app.get("CFBundleShortVersionString"),
        "macOS app CFBundleVersion": app.get("CFBundleVersion"),
        "docs/reference/openapi.v1.json": _openapi_version(),
    }


def _openapi_version() -> str | None:
    """The product version recorded in the committed OpenAPI snapshot.

    Missing rather than fatal when the file is absent: the snapshot is a
    documentation artifact, and a checkout without it should still be able to
    move its version.
    """

    try:
        info = _read_json(OPENAPI_SNAPSHOT).get("info")
    except VersionError:
        return None
    return info.get("version") if isinstance(info, dict) else None


def check() -> list[str]:
    """Return a list of disagreements; empty means every copy matches."""

    versions = declared_versions()
    source = versions["shared/version.json"]
    return [
        f"{where} says {found!r}, shared/version.json says {source!r}"
        for where, found in versions.items()
        if where != "shared/version.json" and found != source
    ]


VERSION_KEY = re.compile(r'("version"\s*:\s*)"[^"]*"')
PLIST_VERSION = re.compile(
    r"(<key>CFBundle(?:ShortVersionString|Version)</key>\s*<string>)[^<]*(</string>)"
)


def _replace_version(path: Path, new: str, *, occurrences: int) -> None:
    """Rewrite the first N ``"version"`` keys, preserving formatting elsewhere.

    json.dump would reflow package-lock.json entirely and produce a diff nobody
    can review, so the edit is textual. Position is the anchor because the
    lockfile's own two copies come first, before any dependency's: the root
    object, then ``packages[""]``. ``"lockfileVersion"`` does not match -- the
    pattern requires the quote immediately before ``version``.
    """

    text = path.read_text(encoding="utf-8")
    text, count = VERSION_KEY.subn(rf'\g<1>"{new}"', text, count=occurrences)
    if count != occurrences:
        raise VersionError(
            f"{path.relative_to(REPO_ROOT)}: expected {occurrences} version "
            f"key(s) to rewrite, found {count}"
        )
    path.write_text(text, encoding="utf-8")


def _replace_plist_version(new: str) -> None:
    text = APP_PLIST.read_text(encoding="utf-8")
    text, count = PLIST_VERSION.subn(rf"\g<1>{new}\g<2>", text)
    if count != 2:
        raise VersionError(
            f"{APP_PLIST.relative_to(REPO_ROOT)}: expected 2 bundle versions, found {count}"
        )
    APP_PLIST.write_text(text, encoding="utf-8")


def write(new: str) -> None:
    parse(new, where="--set")
    _replace_version(VERSION_FILE, new, occurrences=1)
    _replace_version(PACKAGE_JSON, new, occurrences=1)
    # The lockfile carries it twice before any dependency's own version: once at
    # the root, which humans read, and once in packages[""], which npm reads.
    _replace_version(PACKAGE_LOCK, new, occurrences=2)
    _replace_plist_version(new)


def next_version(part: str) -> str:
    major, minor, patch = parse(current(), where="shared/version.json")
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("part", nargs="?", choices=("major", "minor", "patch"))
    group.add_argument("--set", dest="exact", help="set an exact MAJOR.MINOR.PATCH")
    group.add_argument(
        "--check",
        action="store_true",
        help="verify every copy agrees; exit 1 if they do not",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            problems = check()
            if problems:
                for problem in problems:
                    print(f"version drift: {problem}", file=sys.stderr)
                return 1
            print(f"version {current()} is consistent across all files")
            return 0

        new = args.exact if args.exact else next_version(args.part)
        was = current()
        write(new)
        print(f"{was} -> {new}")
        # The snapshot is generated from the live app, so this script cannot
        # move it. Saying so here is the difference between noticing now and
        # noticing when all three server jobs go red on the release commit,
        # which is how 0.2.5 found out.
        print("next: python scripts/gen_openapi.py --write")
        print(f"      git commit, then git tag v{new} && git push origin v{new}")
        return 0
    except VersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
