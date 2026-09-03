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

from scripts.check_partial_merges import (  # noqa: E402
    absorbed_prefix,
    acknowledged,
    default_upstream,
    main,
)


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


def test_a_deliberate_partial_merge_can_be_acknowledged(repo: Path) -> None:
    """Allowed, but it has to be said, and said somewhere reviewable."""

    _git(repo, "checkout", "-b", "feature")
    consumer = _commit(repo, "consumer")
    _commit(repo, "held-back")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "Merge feature (partially)", consumer)

    note = repo / "partial-merges.txt"
    assert main(["feature", "--upstream", "main", "--acknowledged", str(note)]) == 1

    note.write_text(
        "# held out of a release cut deliberately\n"
        "feature: the tail is a no-op in production and lands next release\n",
        encoding="utf-8",
    )
    assert acknowledged(note) == {
        "feature": "the tail is a no-op in production and lands next release"
    }
    assert main(["feature", "--upstream", "main", "--acknowledged", str(note)]) == 0


def test_an_acknowledgement_without_a_reason_is_refused(repo: Path) -> None:
    """A bare branch name would make the escape hatch a rubber stamp."""

    note = repo / "partial-merges.txt"
    note.write_text("feature\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="<branch>: <reason>"):
        acknowledged(note)


def test_default_upstream_is_main_even_when_a_next_ref_still_exists(tmp_path, monkeypatch):
    """The one-branch model retired `next`, but the ref outlives the decision.

    This pins the bug that the old "prefer origin/next when it exists" default became
    on 2026-09-03: `next` stops moving while `main` advances, so probing for the ref
    resolves the upstream to a frozen branch and every commit that lands on `main`
    widens the gap. A hand-run then reports branches as partially merged against a ref
    nothing merges into -- and this is the check people run by hand precisely when they
    already distrust CI.

    Deliberately constructed so the old implementation fails it: `origin/next` exists
    and is reachable, so a `rev-parse --verify` probe would succeed and return it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "base")
    # A frozen `next`, exactly as the migration leaves it: present, and behind.
    _git(repo, "update-ref", "refs/remotes/origin/next", "HEAD")
    _commit(repo, "advance")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    monkeypatch.chdir(repo)
    assert default_upstream() == "origin/main"
