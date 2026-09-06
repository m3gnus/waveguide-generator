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
from shared.release_assets import native_version_fields  # noqa: E402


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
#: The same triple with a SemVer pre-release label, for a build-only stamp.
#:
#: A release is never named this way -- `release.yml` independently refuses
#: anything but MAJOR.MINOR.PATCH -- and neither is a bump: `next_version` reads
#: the current version to compute the next one, and there is no next patch after
#: `0.4.0-main.7`. It exists for one job: naming a build of `main` that is not a
#: release, where the version has to reach every copy at once or the packaged
#: app disagrees with the file it was published under.
BUILD_STAMP = re.compile(r"^(\d+)\.(\d+)\.(\d+)-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*$")


class VersionError(RuntimeError):
    """A version file is missing, unreadable, or not a plain semver triple."""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionError(f"{path.relative_to(REPO_ROOT)}: {exc}") from exc


def parse(version: str, *, where: str, allow_prerelease: bool = False) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if match is None and allow_prerelease:
        match = BUILD_STAMP.match(version)
    if match is None:
        # Pre-release and build metadata are deliberately unsupported: the tag
        # check builds "v" + this string, and a release named v2.0.0-rc.1+build
        # is not something the installer or the update check can compare.
        raise VersionError(f"{where}: {version!r} is not a MAJOR.MINOR.PATCH version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def current(*, allow_prerelease: bool = False) -> str:
    version = _read_json(VERSION_FILE).get("version")
    if not isinstance(version, str):
        raise VersionError("shared/version.json has no string 'version'")
    parse(version, where="shared/version.json", allow_prerelease=allow_prerelease)
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


#: The copies that carry a platform's own numeric version field rather than the
#: product's version string. They agree with `shared/version.json` for a release
#: -- `native_version_fields` maps a release onto itself -- and carry its
#: numeric form for a build stamp, which is the only shape those fields accept.
NATIVE_VERSION_FIELDS = {
    "macOS app CFBundleShortVersionString": "short",
    "macOS app CFBundleVersion": "bundle",
}


def check() -> list[str]:
    """Return a list of disagreements; empty means every copy matches.

    Every copy of the product version must be the same string, and each native
    field must be that version as its own format can carry it. For a release
    those are the same requirement, so this is one rule rather than a mode.
    """

    versions = declared_versions()
    source = versions["shared/version.json"]
    try:
        native = native_version_fields(source)
    except ValueError as exc:
        return [f"shared/version.json says {source!r}, which is not a version: {exc}"]
    problems = []
    for where, found in versions.items():
        if where == "shared/version.json":
            continue
        expected = (
            getattr(native, NATIVE_VERSION_FIELDS[where])
            if where in NATIVE_VERSION_FIELDS
            else source
        )
        if found != expected:
            problems.append(f"{where} says {found!r}, expected {expected!r}")
    return problems


VERSION_KEY = re.compile(r'("version"\s*:\s*)"[^"]*"')
PLIST_VERSION = re.compile(
    r"(<key>CFBundle(?P<key>ShortVersionString|Version)</key>\s*<string>)[^<]*(</string>)"
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
    """Write the two bundle versions, each in the format Apple documents for it.

    They are the same string for a release. For a build stamp they are not:
    `CFBundleShortVersionString` is one to three integers and `CFBundleVersion`
    is the build, so `0.4.0-main.7` is not a value either field accepts. The
    SemVer string still identifies the build everywhere a person or an asset
    name sees it.
    """

    native = native_version_fields(new)
    text = APP_PLIST.read_text(encoding="utf-8")
    replacements = {
        "CFBundleShortVersionString": native.short,
        "CFBundleVersion": native.bundle,
    }
    count = 0

    def substitute(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{replacements['CFBundle' + match.group('key')]}{match.group(3)}"

    text = PLIST_VERSION.sub(substitute, text)
    if count != 2:
        raise VersionError(
            f"{APP_PLIST.relative_to(REPO_ROOT)}: expected 2 bundle versions, found {count}"
        )
    APP_PLIST.write_text(text, encoding="utf-8")


def _replace_openapi_version(new: str) -> None:
    """Move the version the committed OpenAPI snapshot declares.

    Only ``info.version``: everything else in that file is generated from the
    live application by ``scripts/gen_openapi.py``, and a route change still
    needs that script. The version is the one field a version bump can move on
    its own, and moving it here is what keeps this script **dependency free**.

    That matters where it is used. The snapshot is one of the copies ``check``
    compares, so leaving it behind meant a stamped build could only be verified
    after importing the whole server -- FastAPI and the rest -- which on a build
    runner means installing the dependency set before the version is even
    decided. Nothing here imports anything but the standard library.

    The rewrite is a full ``json.dumps`` with the same options
    ``scripts/gen_openapi.py`` uses, so the file it produces is byte for byte
    the file that script would produce, and a snapshot missing or unreadable is
    skipped exactly as ``_openapi_version`` skips it.
    """

    try:
        document = _read_json(OPENAPI_SNAPSHOT)
    except VersionError:
        return
    info = document.get("info")
    if not isinstance(info, dict) or "version" not in info:
        return
    info["version"] = new
    OPENAPI_SNAPSHOT.write_text(
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write(new: str, *, allow_prerelease: bool = False) -> None:
    parse(new, where="--set", allow_prerelease=allow_prerelease)
    _replace_version(VERSION_FILE, new, occurrences=1)
    _replace_version(PACKAGE_JSON, new, occurrences=1)
    # The lockfile carries it twice before any dependency's own version: once at
    # the root, which humans read, and once in packages[""], which npm reads.
    _replace_version(PACKAGE_LOCK, new, occurrences=2)
    _replace_plist_version(new)
    _replace_openapi_version(new)


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
    parser.add_argument(
        "--build-stamp",
        action="store_true",
        help=(
            "allow a pre-release build version (0.4.0-main.7) on --set, and "
            "accept one on --check. For a build that is not a release; a plain "
            "--check still refuses a stamped tree, which is what keeps a release "
            "commit from carrying one."
        ),
    )
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
            # Reading it back is half the check: a tree carrying a build stamp
            # must fail a plain `--check`, because a release tree may not carry
            # one. `--check --build-stamp` is the build's own route to the same
            # verification, and it is the only way to validate the stamp it just
            # wrote.
            print(f"version {current(allow_prerelease=args.build_stamp)} is consistent across all files")
            return 0

        if args.build_stamp and not args.exact:
            raise VersionError("--build-stamp applies to --set and to --check")
        new = args.exact if args.exact else next_version(args.part)
        was = current(allow_prerelease=args.build_stamp)
        write(new, allow_prerelease=args.build_stamp)
        print(f"{was} -> {new}")
        # The snapshot is generated from the live app, so this script cannot
        # move it. Saying so here is the difference between noticing now and
        # noticing when all three server jobs go red on the release commit,
        # which is how 0.2.5 found out.
        # The snapshot's version moved with everything else; regenerating it is
        # still how a *route* change reaches it, and that needs the server's
        # dependencies.
        print("next: python scripts/gen_openapi.py --write, if any route changed")
        if args.build_stamp:
            # Not a release, so no tag instruction. The commit is what the
            # bundle builder reads: it materializes the app layer from Git
            # blobs and refuses a dirty worktree, so a stamp left uncommitted
            # would either fail the build or ship a packaged app still naming
            # the previous version.
            print("      git commit, in the build only -- never pushed, never tagged")
        else:
            print(f"      git commit, then git tag v{new} && git push origin v{new}")
        return 0
    except VersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
