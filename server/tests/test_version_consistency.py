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
