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
