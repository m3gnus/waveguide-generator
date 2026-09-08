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
    python scripts/bump_version.py rc         # 2.1.0 -> 2.1.1-rc.1
    python scripts/bump_version.py rc         # 2.1.1-rc.1 -> 2.1.1-rc.2
    python scripts/bump_version.py patch      # 2.1.1-rc.2 -> 2.1.1
    python scripts/bump_version.py --set 2.0.0

This script only moves the number. **The tag is created last, by CI** -- see
``README.md``'s *Releasing* section and GIT-WORKFLOW.md section 4 -- so a failed
build spends no version. ``release.yml`` refuses to build when the declared
version disagrees with what it may publish, which is what makes this the last
chance to notice a drift.

**A release candidate and a build stamp share SemVer's pre-release slot and are
not the same thing.** ``0.3.2-rc.1`` is a candidate for the 0.3.2 release, built
and published by ``release.yml``; ``0.4.0-main.7`` is a build of ``main`` that
is not a release at all. The identifier is what tells them apart --
``shared/release_assets.release_prerelease`` owns that whitelist -- and this
script keeps them apart everywhere both can appear: a candidate is an ordinary
release version here, and a build stamp needs ``--build-stamp`` to be written
and to pass ``--check``.
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
from shared.release_assets import (  # noqa: E402
    RELEASE_PRERELEASE_IDENTIFIERS,
    is_build_stamp,
    native_version_fields,
    release_prerelease,
    version_precedence,
)


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
#: The same triple with any SemVer pre-release label.
#:
#: Two different things match this, and the difference is the whole reason the
#: functions below take an ``allow_prerelease`` argument:
#:
#: * A **release pre-release** -- `0.3.2-rc.1`, `0.4.0-beta.2`, `1.0.0-alpha`.
#:   It is a release: `release.yml` builds and publishes it, flagged as a
#:   pre-release so `releases/latest` never returns it, and `next_version` can
#:   compute both the next candidate and the final release from it. A release
#:   tree may carry one, so a plain `--check` accepts it.
#: * A **build stamp** -- `0.4.0-main.7`. Not a release: it names a build of
#:   `main`, published by its own workflow, and there is no next patch after it.
#:   A release tree may **not** carry one, so a plain `--check` refuses it and
#:   `--build-stamp` is the build's own route to the same verification.
#:
#: `shared/release_assets.release_prerelease` is the one place that decides
#: which of the two a label is, because `release.yml` has to make the same call.
#: It was called ``BUILD_STAMP`` while a build stamp was the only thing it could
#: match; nothing outside this module referred to it by that name.
PRERELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*$")

#: The bump levels that produce a release pre-release, and the levels the
#: release script accepts alongside major/minor/patch.
PRERELEASE_LEVELS = RELEASE_PRERELEASE_IDENTIFIERS


class VersionError(RuntimeError):
    """A version file is missing, unreadable, or not a plain semver triple."""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionError(f"{path.relative_to(REPO_ROOT)}: {exc}") from exc


def parse(version: str, *, where: str, allow_prerelease: bool = False) -> tuple[int, int, int]:
    """The core `(major, minor, patch)` of a version this tree may carry.

    ``allow_prerelease`` admits a **build stamp**, and only that. A release
    pre-release is admitted unconditionally, because it names a release: the
    tag check builds "v" + this string and `v0.3.2-rc.1` is a tag the installer
    and the update check both already compare -- `shared/release_assets.TAG_RE`
    parses it, `native_version_fields` maps it onto the platforms' numeric
    slots, and the updater's beta channel orders it. Build metadata (`+build`)
    stays unsupported everywhere; nothing in this project can compare it.
    """

    match = SEMVER.match(version)
    if match is None and PRERELEASE.match(version):
        try:
            stamp = is_build_stamp(version)
        except ValueError as exc:
            # An `-updates` companion parses as a pre-release label and is not a
            # version at all -- the comparator refuses it by name.
            raise VersionError(f"{where}: {exc}") from exc
        if allow_prerelease or not stamp:
            match = PRERELEASE.match(version)
        else:
            raise VersionError(
                f"{where}: {version!r} is a build stamp, not a release version. "
                "A release tree may not carry one; pass --build-stamp if this is "
                "a build."
            )
    if match is None:
        raise VersionError(f"{where}: {version!r} is not a MAJOR.MINOR.PATCH version")
    return tuple(int(part) for part in match.groups()[:3])  # type: ignore[return-value]


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
    """The version ``part`` moves the current one to.

    The rules are node-semver's ``inc`` (npm's `version` command) with one
    deliberate difference, and they are what keep an RC from stranding the
    release it is a candidate for:

    ==================  ============  ==================
    from                level         to
    ==================  ============  ==================
    ``0.3.1``           ``patch``     ``0.3.2``
    ``0.3.1``           ``rc``        ``0.3.2-rc.1``
    ``0.3.2-rc.1``      ``rc``        ``0.3.2-rc.2``
    ``0.3.2-beta.2``    ``rc``        ``0.3.2-rc.1``
    ``0.3.2-rc.2``      ``patch``     ``0.3.2``
    ``0.3.2-rc.2``      ``minor``     ``0.4.0``
    ``0.4.0-rc.1``      ``minor``     ``0.4.0``
    ==================  ============  ==================

    **The RC-to-final transition is the row that matters.** `patch` on a
    pre-release removes the label and keeps the core numbers, so the final
    release of `0.3.2-rc.2` is `0.3.2` -- the version the candidates were
    candidates *for*. Incrementing to `0.3.3` instead would strand `0.3.2`
    forever, because a published tag is immutable (GIT-WORKFLOW.md section 4)
    and `0.3.2` could then never be published while its own RCs sat below it.
    node-semver and `cargo release` both spell this the same way; `cargo
    release` calls the same operation `release`.

    `minor` and `major` drop the label the same way when the core is already the
    version being finalised (`0.4.0-rc.1` + `minor` = `0.4.0`), which is
    node-semver's rule and **not** `cargo release`'s -- that one would produce
    `0.5.0` and strand `0.4.0`. Same reason: no reachable version may be made
    unreachable by a bump.

    Nothing here decides whether the result may be *published*. `release.yml`
    orders it against every published tag with the same comparator; this only
    refuses to move backwards from the version in the tree, which is what makes
    `alpha` on an RC an error rather than a silently unpublishable version.
    """

    version = current()
    major, minor, patch = parse(version, where="shared/version.json")
    label = release_prerelease(version)

    if part == "major":
        # Already the pre-release of that major: finalise it rather than skip it.
        new = (
            f"{major}.{minor}.{patch}"
            if label and minor == 0 and patch == 0
            else f"{major + 1}.0.0"
        )
    elif part == "minor":
        new = (
            f"{major}.{minor}.{patch}"
            if label and patch == 0
            else f"{major}.{minor + 1}.0"
        )
    elif part == "patch":
        new = f"{major}.{minor}.{patch}" if label else f"{major}.{minor}.{patch + 1}"
    elif part in PRERELEASE_LEVELS:
        if label is None:
            # No candidate in the tree, so this is the first one for the next
            # patch -- node-semver's `prerelease` from a stable version, which
            # it defines as `prepatch`, and `cargo release`'s `rc` level.
            new = f"{major}.{minor}.{patch + 1}-{part}.1"
        elif label.identifier == part:
            new = f"{major}.{minor}.{patch}-{part}.{label.number + 1}"
        else:
            # A promotion within the same release: beta.2 -> rc.1. Going the
            # other way produces a lower version and the guard below refuses it.
            new = f"{major}.{minor}.{patch}-{part}.1"
    else:  # pragma: no cover - argparse constrains the choices
        raise VersionError(f"unknown level {part!r}")

    if version_precedence(new) <= version_precedence(version):
        raise VersionError(
            f"{part} would move {version} to {new}, which does not move forward. "
            "Semantic versions only move forward (GIT-WORKFLOW.md section 4)."
        )
    return new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "part",
        nargs="?",
        choices=("major", "minor", "patch", *PRERELEASE_LEVELS),
    )
    group.add_argument(
        "--set",
        dest="exact",
        help="set an exact version (0.3.2, or 0.3.2-rc.1)",
    )
    parser.add_argument(
        "--build-stamp",
        action="store_true",
        help=(
            "allow a build stamp (0.4.0-main.7) on --set, and accept one on "
            "--check. For a build that is not a release; a plain --check still "
            "refuses a stamped tree, which is what keeps a release commit from "
            "carrying one. A release candidate (0.3.2-rc.1) is a release "
            "version and does not need this flag."
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
            #
            # A release candidate is not a build stamp and passes a plain
            # `--check`. It has to: `ci.yml`'s drift job runs exactly this
            # command, `release.yml` requires that run to have succeeded on the
            # release commit, and while this refused `0.3.2-rc.1` no candidate
            # could ever go green -- so none could be published however widely
            # the publisher itself was opened up.
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
            # No `git tag` instruction: the tag is created last, by release.yml,
            # after every asset has been built and validated. Printing one here
            # invited exactly the ordering that spent v0.2.5 and v0.2.6.
            print("      git commit, then ./release.sh <repo> publish when ready")
        return 0
    except VersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
