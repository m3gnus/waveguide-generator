from __future__ import annotations

import os
from pathlib import Path

from scripts.frontend_freshness import (
    STAMP_NAME,
    frontend_freshness,
    installer_hint,
    mark_fresh,
    refresh_hint,
    vite_executable,
)


def _checkout(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "checkout"
    source = root / "frontend" / "src" / "main.tsx"
    index = root / "frontend" / "dist" / "index.html"
    source.parent.mkdir(parents=True)
    index.parent.mkdir(parents=True)
    source.write_text("export const value = 1;\n", encoding="utf-8")
    index.write_text("<!doctype html>\n", encoding="utf-8")
    return root, source, index


def test_unstamped_release_build_uses_conservative_mtime_fallback(tmp_path: Path) -> None:
    root, source, index = _checkout(tmp_path)
    os.utime(source, ns=(1_000_000_000, 1_000_000_000))
    os.utime(index, ns=(2_000_000_000, 2_000_000_000))

    assert frontend_freshness(root)[0]

    os.utime(source, ns=(3_000_000_000, 3_000_000_000))
    fresh, reason = frontend_freshness(root)
    assert not fresh
    assert "newer" in reason


def test_local_build_stamp_detects_content_changes_even_with_older_mtime(tmp_path: Path) -> None:
    root, source, _index = _checkout(tmp_path)
    mark_fresh(root)
    assert frontend_freshness(root)[0]

    source.write_text("export const value = 2;\n", encoding="utf-8")
    os.utime(source, ns=(1, 1))

    fresh, reason = frontend_freshness(root)
    assert not fresh
    assert "changed" in reason


def test_missing_dist_is_not_fresh(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()

    fresh, reason = frontend_freshness(root)
    assert not fresh
    assert "missing" in reason


def test_malformed_local_stamp_becomes_a_warning_instead_of_crashing(tmp_path: Path) -> None:
    root, _source, index = _checkout(tmp_path)
    (index.parent / STAMP_NAME).write_bytes(b"\xff\xfe")

    fresh, reason = frontend_freshness(root)
    assert not fresh
    assert "check failed" in reason


def test_refresh_hint_sends_a_release_install_to_its_installer(tmp_path: Path) -> None:
    # No node_modules: running v2 requires no Node, so a Vite build is a command
    # this install does not have and does not need.
    root, _source, _index = _checkout(tmp_path)

    hint = refresh_hint(root)
    assert installer_hint() in hint
    assert "npm" not in hint


def test_refresh_hint_sends_a_buildable_checkout_to_vite(tmp_path: Path) -> None:
    root, _source, _index = _checkout(tmp_path)
    vite = vite_executable(root)
    vite.parent.mkdir(parents=True)
    vite.write_text("", encoding="utf-8")

    assert "npm run build" in refresh_hint(root)
