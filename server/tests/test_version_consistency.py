"""The product version must read the same everywhere it is written down.

``shared/version.json`` is the runtime source -- ``server/app.py`` serves it
from ``/health`` and ``frontend/vite.config.ts`` injects it as
``__WG2_VERSION__`` -- but npm keeps two more copies in ``package.json`` and
``package-lock.json``; the macOS app bundle now carries two copies as well. The
npm copies had drifted to three different values before anyone looked, because the release
workflow only compares the tag against ``shared/version.json``.
"""

from __future__ import annotations

import importlib.util
import json
import plistlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_bump_version():
    """Import scripts/bump_version.py without putting scripts/ on sys.path."""

    spec = importlib.util.spec_from_file_location(
        "wg2_bump_version", ROOT / "scripts" / "bump_version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bump_version = _load_bump_version()


#: Every file `bump_version.write` rewrites. Tests copy all of them, because a
#: test that patches four of five paths writes the fifth into the repository --
#: which is how a suite run left the committed OpenAPI snapshot naming a build
#: version that was never released.
WRITTEN_PATHS = (
    "VERSION_FILE",
    "PACKAGE_JSON",
    "PACKAGE_LOCK",
    "APP_PLIST",
    "OPENAPI_SNAPSHOT",
)


def isolate_version_files(tmp_path, monkeypatch) -> None:
    """Point every copy at a scratch duplicate of the real one."""

    for attribute in WRITTEN_PATHS:
        source = getattr(bump_version, attribute)
        copy = tmp_path / source.name
        copy.write_bytes(source.read_bytes())
        monkeypatch.setattr(bump_version, attribute, copy)


def test_every_declared_version_agrees() -> None:
    assert bump_version.check() == []


def test_the_version_is_a_plain_semver_triple() -> None:
    # The release workflow builds the tag as "v" + this string, so anything the
    # installer or an update check cannot compare is not usable here.
    version = bump_version.current()
    assert bump_version.SEMVER.match(version), version


def test_the_served_version_is_the_shared_one() -> None:
    from server.app import VERSION

    declared = json.loads(
        (ROOT / "shared" / "version.json").read_text(encoding="utf-8")
    )["version"]
    assert VERSION == declared


@pytest.mark.parametrize(
    ("part", "expected"),
    [("major", "3.0.0"), ("minor", "2.5.0"), ("patch", "2.4.1")],
)
def test_bump_arithmetic(part: str, expected: str, monkeypatch) -> None:
    monkeypatch.setattr(bump_version, "current", lambda: "2.4.0")
    assert bump_version.next_version(part) == expected


def test_a_non_semver_version_is_refused() -> None:
    with pytest.raises(bump_version.VersionError):
        bump_version.parse("2.0.0-rc.1", where="test")
    with pytest.raises(bump_version.VersionError):
        bump_version.parse("2.0", where="test")


def test_a_pre_release_is_a_version_only_when_a_build_asks_for_one() -> None:
    """`--build-stamp` names a build of `main`, and nothing else.

    A release is never named this way: `release.yml` independently refuses
    anything but MAJOR.MINOR.PATCH, and this stays strict everywhere the label
    is not explicitly allowed -- so the widening cannot leak into a release
    commit by default.
    """

    assert bump_version.parse("0.4.0-main.7", where="test", allow_prerelease=True) == (0, 4, 0)
    with pytest.raises(bump_version.VersionError):
        bump_version.parse("0.4.0-main.7", where="test")
    for refused in ("0.4.0-", "0.4.0-main..7", "0.4.0+build", "0.4-main.7"):
        with pytest.raises(bump_version.VersionError):
            bump_version.parse(refused, where="test", allow_prerelease=True)


def test_native_version_fields_are_numbers_the_platforms_accept() -> None:
    """A SemVer pre-release is not a value the native version slots take.

    Inno Setup's VersionInfoVersion is the binary VERSIONINFO resource -- up to
    four dot-separated numbers -- and Apple documents CFBundleShortVersionString
    and CFBundleVersion as one to three integers. `0.4.0-main.7` is invalid in
    all three, so ISCC refuses the compile outright. A release maps to itself,
    which is exactly what the installers already carry.
    """

    from shared.release_assets import native_version_fields

    assert native_version_fields("0.3.1") == ("0.3.1", "0.3.1", "0.3.1")
    assert native_version_fields("0.4.0-main.7") == ("0.4.0", "7", "0.4.0.7")
    assert native_version_fields("0.4.0-beta.1") == ("0.4.0", "1", "0.4.0.1")
    # No number to take: the slot still has to hold one.
    assert native_version_fields("0.4.0-main") == ("0.4.0", "0", "0.4.0.0")
    # Each VERSIONINFO component is a 16-bit word, so this cannot be carried at
    # all -- and wrapping it silently into a lower build number would be worse.
    with pytest.raises(ValueError, match="65535"):
        native_version_fields("0.4.0-main.70000")
    with pytest.raises(ValueError, match="Not a version"):
        native_version_fields("0.4")


def test_the_build_stamp_writes_each_native_field_in_its_own_format(
    tmp_path, monkeypatch
) -> None:
    """The plist carries two versions and they stop being the same string."""

    plist = tmp_path / "Info.plist"
    plist.write_bytes(bump_version.APP_PLIST.read_bytes())
    monkeypatch.setattr(bump_version, "APP_PLIST", plist)

    bump_version._replace_plist_version("0.4.0-main.7")

    written = plistlib.loads(plist.read_bytes())
    assert written["CFBundleShortVersionString"] == "0.4.0"
    assert written["CFBundleVersion"] == "7"

    # A release is unchanged: both fields carry the version, as they always did.
    bump_version._replace_plist_version("0.4.1")
    written = plistlib.loads(plist.read_bytes())
    assert written["CFBundleShortVersionString"] == "0.4.1"
    assert written["CFBundleVersion"] == "0.4.1"


def test_check_validates_a_build_stamp_only_when_asked_to(tmp_path, monkeypatch) -> None:
    """`--check` is the verification the stamp would otherwise skip.

    A plain `--check` must still refuse a stamped tree -- that is what keeps a
    release commit from carrying one -- so the build has its own route to the
    same check rather than no check at all.
    """

    isolate_version_files(tmp_path, monkeypatch)
    bump_version.write("0.4.0-main.7", allow_prerelease=True)

    # The natives disagree with shared/version.json on purpose, and `check`
    # knows what each of them should say instead.
    assert bump_version.check() == []
    assert bump_version.main(["--check", "--build-stamp"]) == 0
    assert bump_version.main(["--check"]) == 1


def test_check_still_reports_a_native_field_that_drifted(tmp_path, monkeypatch) -> None:
    isolate_version_files(tmp_path, monkeypatch)
    bump_version.write("0.4.0-main.7", allow_prerelease=True)
    plist = bump_version.APP_PLIST
    plist.write_text(
        plist.read_text(encoding="utf-8").replace(
            "<string>7</string>", "<string>9</string>"
        ),
        encoding="utf-8",
    )

    assert bump_version.check() == [
        "macOS app CFBundleVersion says '9', expected '7'"
    ]


def test_writing_a_version_touches_nothing_outside_the_paths_it_declares(
    tmp_path, monkeypatch
) -> None:
    """A test that isolates some of the copies edits the repository with the rest.

    That is not hypothetical: adding the OpenAPI snapshot to `write` left three
    tests patching four of the five paths, and a suite run rewrote the committed
    snapshot to a build version. `WRITTEN_PATHS` is the list they share, and
    this is what keeps it complete.
    """

    isolate_version_files(tmp_path, monkeypatch)
    watched = {
        attribute: getattr(bump_version, attribute)
        for attribute in WRITTEN_PATHS
    }
    before = {
        attribute: path.read_bytes() for attribute, path in watched.items()
    }

    bump_version.write("0.4.0-main.7", allow_prerelease=True)

    # Every declared path is a scratch copy, so every file `write` touched is
    # one of these -- and each of them did change, which is what makes the list
    # a complete description of the write rather than a superset.
    for attribute, path in watched.items():
        assert path.read_bytes() != before[attribute], attribute
        assert path.parent == tmp_path, attribute


def test_the_build_stamp_reaches_every_declared_copy(tmp_path, monkeypatch) -> None:
    """One stamp or none: a packaged app that disagrees with the file it was
    published under is the failure this exists to prevent.

    "Every copy" means every copy of the *product* version. The two macOS
    bundle fields are the same identity in the only formats Apple's own
    documentation allows, which is a separate test above.
    """

    isolate_version_files(tmp_path, monkeypatch)

    bump_version.write("0.4.0-main.7", allow_prerelease=True)

    declared = bump_version.declared_versions()
    native = {"macOS app CFBundleShortVersionString", "macOS app CFBundleVersion"}
    assert {
        value for where, value in declared.items() if where not in native
    } == {"0.4.0-main.7"}
    assert declared["macOS app CFBundleShortVersionString"] == "0.4.0"
    assert declared["macOS app CFBundleVersion"] == "7"
    assert bump_version.check() == []
    # And the release path is unchanged: the same call without the flag refuses.
    with pytest.raises(bump_version.VersionError):
        bump_version.write("0.4.0-main.8")
