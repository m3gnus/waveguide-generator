"""SPA recovery and swap state is isolated between concurrent installers."""

from __future__ import annotations

import io
import json
import multiprocessing
from pathlib import Path
import tarfile
from typing import Any

import pytest

from scripts import fetch_spa


def _make_archive(path: Path, index: str, *, include_index: bool = True) -> Path:
    member = "dist/index.html" if include_index else "dist/asset.js"
    content = index.encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return path


def test_checkout_lock_lives_in_git_metadata_not_the_worktree(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    with fetch_spa._spa_install_lock(tmp_path):
        assert (git_dir / fetch_spa.LOCK_NAME.lstrip(".")).is_file()

    assert not (tmp_path / "frontend" / fetch_spa.LOCK_NAME).exists()


def test_linked_worktree_lock_uses_its_private_git_directory(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    private_git_dir = tmp_path / "repository" / ".git" / "worktrees" / "checkout"
    checkout.mkdir()
    private_git_dir.mkdir(parents=True)
    (checkout / ".git").write_text(
        "gitdir: ../repository/.git/worktrees/checkout\n", encoding="utf-8"
    )

    with fetch_spa._spa_install_lock(checkout):
        assert (private_git_dir / fetch_spa.LOCK_NAME.lstrip(".")).is_file()

    assert not (checkout / "frontend" / fetch_spa.LOCK_NAME).exists()


def _install_after_barrier(
    root: str,
    archive: str,
    ready: Any,
    release: Any,
) -> None:
    checkout = Path(root)
    with fetch_spa._spa_install_lock(checkout):
        staging = checkout / "frontend" / fetch_spa.STAGING_NAME
        staging.mkdir()
        (staging / "first-is-extracting").write_text("owned", encoding="utf-8")
        ready.set()
        if not release.wait(15):
            raise TimeoutError("test did not release the first installer")
        fetch_spa._install_archive_locked(
            Path(archive),
            version="first",
            digest="a" * 64,
            source="first archive",
            root=checkout,
        )


def _install_and_signal(
    root: str,
    archive: str,
    started: Any,
    finished: Any,
) -> None:
    started.set()
    try:
        fetch_spa.install_archive(
            Path(archive),
            version="second",
            digest="b" * 64,
            source="second archive",
            root=Path(root),
        )
    finally:
        finished.set()


def test_concurrent_installers_serialize_without_cross_deleting_state(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("old", encoding="utf-8")
    first_archive = _make_archive(tmp_path / "first.tar.gz", "first")
    second_archive = _make_archive(tmp_path / "second.tar.gz", "second")

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    started = context.Event()
    finished = context.Event()
    first = context.Process(
        target=_install_after_barrier,
        args=(str(tmp_path), str(first_archive), ready, release),
    )
    second = context.Process(
        target=_install_and_signal,
        args=(str(tmp_path), str(second_archive), started, finished),
    )

    first.start()
    assert ready.wait(10), "first installer did not acquire the lock"
    second.start()
    try:
        assert started.wait(10), "second installer did not start"

        # The second process reached install_archive, but must not delete or replace
        # anything owned by the process that currently holds the checkout lock.
        assert not finished.wait(0.5)
        assert (frontend / fetch_spa.STAGING_NAME / "first-is-extracting").is_file()
        assert (dist / "index.html").read_text(encoding="utf-8") == "old"
    finally:
        release.set()
        first.join(15)
        second.join(15)
    assert first.exitcode == 0
    assert second.exitcode == 0

    assert (dist / "index.html").read_text(encoding="utf-8") == "second"
    stamp = json.loads((dist / fetch_spa.STAMP_NAME).read_text(encoding="utf-8"))
    assert stamp == {
        "asset": second_archive.name,
        "sha256": "b" * 64,
        "source": "second archive",
        "version": "second",
    }
    assert not (frontend / fetch_spa.STAGING_NAME).exists()
    assert not (frontend / fetch_spa.PREVIOUS_NAME).exists()


def test_install_lock_is_released_when_archive_installation_fails(tmp_path: Path) -> None:
    bad_archive = _make_archive(tmp_path / "bad.tar.gz", "bad", include_index=False)
    good_archive = _make_archive(tmp_path / "good.tar.gz", "good")

    with pytest.raises(fetch_spa.SpaError, match="no dist/index.html"):
        fetch_spa.install_archive(
            bad_archive,
            version="bad",
            digest="a" * 64,
            source="bad archive",
            root=tmp_path,
        )

    # This second acquisition would block if the exceptional path leaked the
    # descriptor or retained the exclusive lock.
    installed = fetch_spa.install_archive(
        good_archive,
        version="good",
        digest="b" * 64,
        source="good archive",
        root=tmp_path,
    )
    assert (installed / "index.html").read_text(encoding="utf-8") == "good"
    assert json.loads((installed / fetch_spa.STAMP_NAME).read_text(encoding="utf-8"))[
        "version"
    ] == "good"
