"""``take_by_rename`` makes taking a file by renaming it an exclusive decision.

The race it closes is Windows-only: ``os.rename`` there opens its source by
name, sharing delete access, so two concurrent renames of one file can both
succeed. The rival here renames the way the pinned Fusion add-in claims a
request -- plain ``os.rename``, any ``OSError`` meaning "not taken this pass" --
and exactly one of the two must end up holding the file, every round.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time

import pytest

from server.platform.exclusive_rename import take_by_rename

ROUNDS = 300


def test_a_missing_source_is_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        take_by_rename(tmp_path / "gone.json", tmp_path / ".taken.tmp")


def test_it_renames_and_never_replaces_the_target(tmp_path: Path) -> None:
    source = tmp_path / "request.json"
    source.write_text("request", encoding="utf-8")
    target = tmp_path / ".request.json.live-1.tmp"
    take_by_rename(source, target)
    assert not source.exists() and target.read_text(encoding="utf-8") == "request"

    source.write_text("second", encoding="utf-8")
    with pytest.raises(OSError):
        take_by_rename(source, target)
    assert source.read_text(encoding="utf-8") == "second"
    assert target.read_text(encoding="utf-8") == "request"


def test_an_existing_target_is_file_exists_and_left_alone(tmp_path: Path) -> None:
    source = tmp_path / "request.json"
    source.write_text("new", encoding="utf-8")
    target = tmp_path / ".taken.tmp"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        take_by_rename(source, target)
    assert source.read_text(encoding="utf-8") == "new" and target.read_text(encoding="utf-8") == "old"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path length, attributes and share modes")
def test_a_path_past_max_path_is_taken(tmp_path: Path) -> None:
    folder = tmp_path
    while len(str(folder)) < 280:
        folder = folder / ("d" * 40)
    os.makedirs("\\\\?\\" + str(folder))
    source = folder / "request.json"
    target = folder / ".request.json.live-claim.tmp"
    with open("\\\\?\\" + str(source), "w", encoding="utf-8") as stream:
        stream.write("long")
    take_by_rename(source, target)
    assert os.path.exists("\\\\?\\" + str(target)) and not os.path.exists("\\\\?\\" + str(source))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path length, attributes and share modes")
def test_a_hidden_file_is_taken(tmp_path: Path) -> None:
    import ctypes

    source = tmp_path / ".request.json"
    source.write_text("hidden", encoding="utf-8")
    assert ctypes.windll.kernel32.SetFileAttributesW(str(source), 0x2)  # FILE_ATTRIBUTE_HIDDEN
    target = tmp_path / ".request.json.live-claim.tmp"
    take_by_rename(source, target)
    assert target.read_text(encoding="utf-8") == "hidden" and not source.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path length, attributes and share modes")
def test_a_rival_rename_in_progress_holds_it_off_until_the_wait_ends(tmp_path: Path) -> None:
    """A handle opened for a rename (as MoveFileExW opens one) is waited out, then refused."""

    import ctypes
    from ctypes import wintypes

    source = tmp_path / "request.json"
    source.write_text("held", encoding="utf-8")
    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    rival = create_file(str(source), 0x00010000, 0x7, None, 3, 0, None)  # DELETE, share all
    assert rival not in (None, wintypes.HANDLE(-1).value)
    try:
        started = time.monotonic()
        with pytest.raises(PermissionError):
            take_by_rename(source, tmp_path / ".taken.tmp", wait_seconds=0.2)
        assert time.monotonic() - started >= 0.2
    finally:
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(rival))
    take_by_rename(source, tmp_path / ".taken.tmp")
    assert not source.exists()


def test_against_an_add_in_style_rename_exactly_one_taker_wins(tmp_path: Path) -> None:
    for index in range(ROUNDS):
        folder = tmp_path / f"round-{index}"
        folder.mkdir()
        source = folder / "request.json"
        source.write_text(str(index), encoding="utf-8")
        mine = folder / ".request.json.live-claim.tmp"
        theirs = folder / ".wglink-claim-request-0123456789ab.json"
        barrier = threading.Barrier(2)
        won: dict[str, bool] = {}

        def live() -> None:
            barrier.wait(5)
            try:
                take_by_rename(source, mine)
            except FileNotFoundError:
                won["live"] = False
            else:
                won["live"] = True

        def add_in() -> None:
            barrier.wait(5)
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.rename(source, theirs)
                except FileNotFoundError:
                    won["add_in"] = False
                    return
                except PermissionError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.001)
                    continue
                won["add_in"] = True
                return

        threads = [threading.Thread(target=live), threading.Thread(target=add_in)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert won.get("live") != won.get("add_in"), (index, won)
        remaining = sorted(path.name for path in folder.iterdir())
        assert remaining == [mine.name if won["live"] else theirs.name], (index, remaining)


@pytest.mark.skipif(sys.platform != "win32", reason="the double-rename race is Windows-only")
def test_plain_os_rename_is_not_exclusive_here(tmp_path: Path) -> None:
    """Non-vacuity: the race the helper closes is real on this platform."""

    both = 0
    for index in range(ROUNDS):
        folder = tmp_path / f"round-{index}"
        folder.mkdir()
        source = folder / "request.json"
        source.write_text(str(index), encoding="utf-8")
        barrier = threading.Barrier(2)
        results: list[bool] = []

        def rename(target: Path) -> None:
            barrier.wait(5)
            try:
                os.rename(source, target)
            except OSError:
                results.append(False)
            else:
                results.append(True)

        threads = [threading.Thread(target=rename, args=(folder / f".{name}.tmp",)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        both += results.count(True) == 2
    assert both > 0, "two concurrent os.rename calls never both succeeded; the guarded race is not exercised"
