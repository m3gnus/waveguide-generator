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


def test_the_build_stamp_reaches_every_declared_copy(tmp_path, monkeypatch) -> None:
    """One stamp or none: a packaged app that disagrees with the file it was
    published under is the failure this exists to prevent."""

    for attribute in ("VERSION_FILE", "PACKAGE_JSON", "PACKAGE_LOCK", "APP_PLIST"):
        source = getattr(bump_version, attribute)
        copy = tmp_path / source.name
        copy.write_bytes(source.read_bytes())
        monkeypatch.setattr(bump_version, attribute, copy)
    # The OpenAPI snapshot is written by gen_openapi.py, not by this script, so
    # it is excluded here exactly as `write` excludes it.
    monkeypatch.setattr(bump_version, "_openapi_version", lambda: "0.4.0-main.7")

    bump_version.write("0.4.0-main.7", allow_prerelease=True)

    assert set(bump_version.declared_versions().values()) == {"0.4.0-main.7"}
    assert bump_version.check() == []
    # And the release path is unchanged: the same call without the flag refuses.
    with pytest.raises(bump_version.VersionError):
        bump_version.write("0.4.0-main.8")
