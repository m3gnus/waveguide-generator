"""The app-layer digest must not depend on which host built the layer.

The release builds the app layer twice, on macOS and on Windows, and asserts the
two ``treeSha256`` values match. v0.2.5's build failed that gate with
byte-identical contents on both sides: ``tree_digest`` read the executable bit
back off the filesystem, and NTFS has no POSIX executable bit, so CPython
fabricates one from the file extension. Three ``.bat`` files in the app layer
were enough.
"""

from __future__ import annotations

import os

import pytest

from scripts import build_bundle


def _layer(root):
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "install.bat").write_bytes(b"@echo off\r\n")
    (root / "server").mkdir(parents=True, exist_ok=True)
    (root / "server" / "app.py").write_bytes(b"x = 1\n")
    return root


def test_a_batch_file_does_not_change_the_digest_by_looking_executable(tmp_path, monkeypatch):
    """What Windows actually does, forced on the platform running the test.

    ``_executable_flag`` is the whole mechanism, so driving ``os.name`` is
    enough to reproduce the failure: on ``nt`` the fabricated bit must be
    ignored rather than digested.
    """

    root = _layer(tmp_path / "app")
    posix_digest = build_bundle.tree_digest(root)

    # Windows: CPython would report the .bat as executable. The flag must not.
    monkeypatch.setattr(build_bundle.os, "name", "nt")
    assert build_bundle._executable_flag(root / "scripts" / "install.bat") == "-"
    assert build_bundle.tree_digest(root) == posix_digest


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not observable on Windows")
def test_an_executable_file_in_the_app_layer_fails_the_build(tmp_path):
    """The invariant the digest now rests on, enforced where it is observable.

    Digesting everything as non-executable is correct only while the layer
    really has no executable files. If one appears, both platforms would agree
    on the same wrong answer and the cross-platform gate would stay green, so
    the builder has to refuse instead.
    """

    root = _layer(tmp_path / "app")
    build_bundle.assert_app_layer_is_not_executable(root)  # clean layer passes

    (root / "scripts" / "install.bat").chmod(0o755)
    with pytest.raises(build_bundle.BundleError, match="scripts/install.bat"):
        build_bundle.assert_app_layer_is_not_executable(root)


def _digest_in_order(root, relatives):
    """Reimplement the digest over an explicit ordering, to pin which one wins."""

    import hashlib

    digest = hashlib.sha256()
    for relative in relatives:
        path = root.joinpath(*relative.split("/"))
        digest.update(
            f"f-\x00{relative}\x00{build_bundle.file_sha256(path)}\x00".encode()
        )
    return digest.hexdigest()


def test_the_digest_is_keyed_on_the_posix_path_not_the_platforms_casing_rule(tmp_path):
    """Ordering, not content, was the second cause of the 0.2.5/0.2.6 failures.

    ``sorted()`` over ``Path`` objects compares with the platform's casing rule,
    and on Windows that is case-insensitive. The app layer holds ``LICENSE`` and
    ``README.md`` beside lowercase directories, so the two hosts enumerated the
    same 308 files in different orders and an order-dependent digest disagreed on
    byte-identical content, diverging at the very first entry. A per-file
    comparison cannot see it, which is why it survived a check that found zero
    content mismatches across all 288 tracked files.

    **This test cannot fail on macOS or Linux**, and that is worth stating rather
    than dressing up: there, ``sorted()`` over ``Path`` already yields the posix
    order, so the unfixed code produces the right answer. It fails on Windows
    against the unfixed code, which is where the bug lives and where CI runs it.
    What it pins everywhere is the contract -- the digest follows the posix path
    ordering -- so a future change back to Path ordering is a visible decision
    rather than a silent one.
    """

    root = tmp_path / "app"
    (root / "docs").mkdir(parents=True)
    (root / "LICENSE").write_bytes(b"license\n")
    (root / "README.md").write_bytes(b"readme\n")
    (root / "docs" / "guide.md").write_bytes(b"guide\n")

    posix_order = ["LICENSE", "README.md", "docs/guide.md"]
    windows_order = ["docs/guide.md", "LICENSE", "README.md"]

    # The fixture is only meaningful while the two orderings really disagree.
    assert _digest_in_order(root, posix_order) != _digest_in_order(root, windows_order)

    assert build_bundle.tree_digest(root) == _digest_in_order(root, posix_order)
