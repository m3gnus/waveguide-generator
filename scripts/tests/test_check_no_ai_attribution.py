"""The gate that stops AI attribution reaching history again.

Two trailers reached published branches on 2026-08-27 within hours of each other,
both through reviewed pull requests, because the rule lived only in `AGENTS.md`
and nothing executed it. These tests pin the shapes that must fail and, just as
importantly, the ones that must not: a check that cries wolf on ordinary
co-authorship is a check people will learn to override.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.check_no_ai_attribution import main, offending_lines  # noqa: E402


REAL_TRAILER = "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"


@pytest.mark.parametrize(
    "line",
    [
        REAL_TRAILER,
        "Co-authored-by: Claude <noreply@anthropic.com>",
        "  co-authored-by: ChatGPT <bot@openai.com>",
        "Co-Authored-By: GitHub Copilot <copilot@github.com>",
        "🤖 Generated with Claude Code",
        "Generated with cursor",
        "Assisted-By: AI",
    ],
)
def test_the_shapes_that_must_fail(line: str) -> None:
    assert offending_lines(f"Do a thing\n\nBody text.\n\n{line}\n") == [line.strip()]


@pytest.mark.parametrize(
    "line",
    [
        # A real person who happens to work at Anthropic, or is called Claude.
        "Co-Authored-By: Claude Debussy <claude@example.com>",
        # The word appears in prose about the product, not as attribution.
        "This fixes the Claude Code integration described in #12.",
        "Generated with gmsh 4.13, which is what the mesher pins.",
        "Co-Authored-By: Someone Real <someone@example.com>",
    ],
)
def test_the_shapes_that_must_not_fail(line: str) -> None:
    """False positives are how a check gets disabled."""

    assert offending_lines(f"Do a thing\n\n{line}\n") == []


def test_a_message_with_no_trailer_at_all_is_clean() -> None:
    assert offending_lines("Subject\n\nA body that explains why.\n") == []


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL)


def _repo(tmp_path: Path) -> Path:
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "T", cwd=tmp_path)
    (tmp_path / "f.txt").write_text("one\n", encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "base", cwd=tmp_path)
    return tmp_path


def _commit(repo: Path, text: str, message: str) -> None:
    (repo / "f.txt").write_text(text, encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", message, cwd=repo)


def test_a_clean_branch_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    _git("branch", "upstream", cwd=repo)
    _commit(repo, "two\n", "Add a thing\n\nBecause of a reason.")
    monkeypatch.chdir(repo)

    assert main(["--upstream", "upstream", "--head", "HEAD"]) == 0


def test_a_trailer_on_a_new_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _git("branch", "upstream", cwd=repo)
    _commit(repo, "two\n", f"Add a thing\n\nBecause of a reason.\n\n{REAL_TRAILER}")
    monkeypatch.chdir(repo)

    assert main(["--upstream", "upstream", "--head", "HEAD"]) == 1


def test_history_the_branch_did_not_add_is_not_its_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason this checks a range rather than all of history.

    `8a5b6bd3` carries a trailer and is reachable from `main`. Removing it means
    rewriting the default branch under every clone, which is a decision this
    check must not force on anyone by failing every build until it happens.
    """

    repo = _repo(tmp_path)
    _commit(repo, "two\n", f"Old and already merged\n\n{REAL_TRAILER}")
    _git("branch", "upstream", cwd=repo)
    _commit(repo, "three\n", "New work, clean")
    monkeypatch.chdir(repo)

    assert main(["--upstream", "upstream", "--head", "HEAD"]) == 0


def test_a_merge_commit_is_checked_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub copies the pull request body into the merge commit it creates.

    So a trailer can reach history through the merge even when every commit on
    the branch is clean -- which is exactly how one of the two got in.
    """

    repo = _repo(tmp_path)
    _git("branch", "upstream", cwd=repo)
    _git("checkout", "-qb", "side", cwd=repo)
    # Separate files, so the merge exercises the message rather than a conflict.
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "Clean work on a branch", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    _commit(repo, "main\n", "Clean work on main")
    subprocess.run(
        ["git", "merge", "--no-ff", "-m", f"Merge side\n\n{REAL_TRAILER}", "side"],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    monkeypatch.chdir(repo)

    assert main(["--upstream", "upstream", "--head", "HEAD"]) == 1
