"""A build has to be able to name itself.

``shared/version.json`` names the last release tag. Between v0.2.4 and
2026-08-26 that one string covered 380 commits, because the installer
fast-forwards a branch rather than checking out a tag. Two installs could
report the identical version and be hundreds of commits apart, which made user
bug reports unattributable and comparisons between them meaningless.
"""

from __future__ import annotations

import json

import pytest

from shared import build_identity as identity


@pytest.fixture(autouse=True)
def _clear_cache():
    identity.build_identity.cache_clear()
    yield
    identity.build_identity.cache_clear()


def _tree(root, version="1.2.3"):
    (root / "shared").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    return root


def test_a_stamp_identifies_the_commit(tmp_path):
    root = _tree(tmp_path)
    (root / "shared" / "build.json").write_text(
        json.dumps({"commit": "8a6078c70ebe0741", "dirty": False}), encoding="utf-8"
    )

    assert identity.build_label(root) == "1.2.3+g8a6078c7"
    assert identity.build_identity(root)["source"] == "stamp"


def test_a_modified_tree_says_so(tmp_path):
    """A build with local edits is not the commit it names, and a bug report
    against it must not be read as one against that commit."""

    root = _tree(tmp_path)
    (root / "shared" / "build.json").write_text(
        json.dumps({"commit": "8a6078c70ebe0741", "dirty": True}), encoding="utf-8"
    )

    assert identity.build_label(root) == "1.2.3+g8a6078c7.dirty"


def test_an_unresolvable_commit_is_reported_not_guessed(tmp_path):
    """A bundled install with no stamp and no git must say ``unknown`` rather
    than let the version stand in for a build."""

    root = _tree(tmp_path)

    assert identity.build_label(root) == "1.2.3+unknown"
    assert identity.build_identity(root)["commit"] is None
    assert identity.build_identity(root)["source"] == "unavailable"


def test_the_bare_version_stays_available_for_semver_comparisons(tmp_path):
    """``launch/serve.py`` compares the SPA release stamp against the backend
    tree. That comparison must never see a build suffix."""

    root = _tree(tmp_path)
    (root / "shared" / "build.json").write_text(
        json.dumps({"commit": "deadbeefcafe", "dirty": True}), encoding="utf-8"
    )

    assert identity.version(root) == "1.2.3"


def test_a_corrupt_stamp_degrades_instead_of_raising(tmp_path):
    """The stamp is written by an installer that can be interrupted. A
    half-written file must not stop the app from starting."""

    root = _tree(tmp_path)
    (root / "shared" / "build.json").write_text("{not json", encoding="utf-8")

    assert identity.build_label(root).startswith("1.2.3+")


def test_a_live_probe_beats_a_stale_stamp(tmp_path, monkeypatch):
    """The stamp records where the tree was when the installer last ran. Anything
    that moves HEAD afterwards leaves it naming a commit this code is not, and
    believing it would reintroduce the misreporting with more credibility."""

    root = _tree(tmp_path)
    (root / "shared" / "build.json").write_text(
        json.dumps({"commit": "0000000000stale", "dirty": False}), encoding="utf-8"
    )
    monkeypatch.setattr(
        identity, "_probed_identity", lambda base: ("abcdef1234", False, "git")
    )

    assert identity.build_label(root) == "1.2.3+gabcdef12"
    assert identity.build_identity(root)["source"] == "git"


def test_a_stamp_without_a_commit_is_not_a_stamp(tmp_path):
    root = _tree(tmp_path)
    (root / "shared" / "build.json").write_text(
        json.dumps({"dirty": True}), encoding="utf-8"
    )

    assert identity.build_identity(root)["source"] != "stamp"


def test_a_packaged_build_identifies_itself_from_the_app_manifest(tmp_path):
    """The case that matters most: a bundle has no .git and no installer stamp.

    ``scripts/build_bundle.py`` materializes tracked files from git blobs into a
    fresh directory, so nothing there is a repository. Without the manifest as a
    source, every packaged build -- the artifact most users actually install --
    reported ``+unknown``, which is the exact population whose bug reports could
    not be attributed in the first place.
    """

    root = _tree(tmp_path, version="0.2.5")
    (root / "APP-MANIFEST.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "version": "0.2.5",
                "commit": "6070dab6" + "0" * 32,
                "runtimeId": "abc",
                "treeSha256": "def",
            }
        ),
        encoding="utf-8",
    )

    assert identity.build_label(root) == "0.2.5+g6070dab6"
    assert identity.build_identity(root)["source"] == "manifest"
    # The bundler refuses a dirty worktree, so a packaged build is never dirty.
    assert identity.build_identity(root)["dirty"] is False


def test_the_manifest_outranks_an_installer_stamp(tmp_path):
    """A packaged tree should not be described by a stamp some copy step left
    behind; the manifest was written from the commit the bundle was built from."""

    root = _tree(tmp_path, version="0.2.5")
    (root / "APP-MANIFEST.json").write_text(
        json.dumps({"commit": "b" * 40}), encoding="utf-8"
    )
    (root / "shared" / "build.json").write_text(
        json.dumps({"commit": "c" * 40, "dirty": True}), encoding="utf-8"
    )

    assert identity.build_identity(root)["source"] == "manifest"
    assert identity.build_label(root) == "0.2.5+gbbbbbbbb"


def test_a_manifest_without_a_commit_falls_through(tmp_path):
    root = _tree(tmp_path)
    (root / "APP-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "version": "1.2.3"}), encoding="utf-8"
    )

    assert identity.build_identity(root)["source"] == "unavailable"
