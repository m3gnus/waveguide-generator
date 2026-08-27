"""The partial-merge check is built against the failure it exists for.

Two things are pinned. It flags a branch whose prefix `main` has absorbed, which
is the shape of PR #29, and it stays quiet for the two shapes that look similar
and are ordinary: a branch still in flight, and one merged at its tip.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.check_partial_merges import absorbed_prefix, main  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "test")
    _commit(path, "base")
    monkeypatch.chdir(path)
    return path


def test_a_branch_merged_short_of_its_tip_is_flagged(repo: Path) -> None:
    """PR #29's shape: main takes a prefix, the enabler stays on the tail."""

    _git(repo, "checkout", "-b", "feature")
    consumer = _commit(repo, "consumer")
    _commit(repo, "enabler")

    # main merges the branch at an intermediate commit, exactly as a range merge
    # does, so the merge base becomes a commit of the branch.
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "Merge feature (partially)", consumer)

    found = absorbed_prefix("feature", "main")
    assert found is not None
    base, left = found
    assert base == consumer
    assert len(left) == 1
    assert "enabler" in _git(repo, "log", "-1", "--format=%s", left[0])

    assert main(["feature", "--upstream", "main"]) == 1


def test_an_in_flight_branch_is_not_flagged(repo: Path) -> None:
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "work-one")
    _commit(repo, "work-two")
    _git(repo, "checkout", "main")

    assert absorbed_prefix("feature", "main") is None
    assert main(["feature", "--upstream", "main"]) == 0


def test_a_branch_merged_at_its_tip_is_not_flagged(repo: Path) -> None:
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "work-one")
    _commit(repo, "work-two")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "Merge feature", "feature")

    assert absorbed_prefix("feature", "main") is None
    assert main(["feature", "--upstream", "main"]) == 0


def test_a_branch_that_merged_main_into_itself_is_not_flagged(repo: Path) -> None:
    """The common false positive: catching up with main moves the merge base."""

    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "work-one")
    _git(repo, "checkout", "main")
    _commit(repo, "main-moved")
    _git(repo, "checkout", "feature")
    _git(repo, "merge", "--no-ff", "-m", "Merge main into feature", "main")
    _commit(repo, "work-two")

    assert absorbed_prefix("feature", "main") is None
    assert main(["feature", "--upstream", "main"]) == 0
