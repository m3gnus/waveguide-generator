from __future__ import annotations

from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEXT_SOURCE_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".ts",
    ".tsx",
}


def test_tracked_frontend_and_server_sources_contain_no_literal_nul_bytes() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "frontend/src", "server"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    sources = [
        REPOSITORY_ROOT / raw.decode("utf-8")
        for raw in tracked
        if raw and Path(raw.decode("utf-8")).suffix.lower() in TEXT_SOURCE_SUFFIXES
    ]

    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in sources
        if b"\0" in path.read_bytes()
    ]

    assert offenders == [], f"literal NUL bytes found in source files: {offenders}"
