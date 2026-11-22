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


def test_default_upstream_is_dev_by_name_even_when_other_refs_exist(tmp_path, monkeypatch):
    """`dev` is the working branch (GIT-WORKFLOW section 2), named outright, not probed for.

    A frozen `origin/next` and an `origin/main` that moves only at releases both exist
    here; a probe would pick one of them. The old default compared every branch with
    `origin/main`, hundreds of commits behind `dev`, and ignored BASE_REF.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/next", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _commit(repo, "advance")
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")

    monkeypatch.chdir(repo)
    monkeypatch.delenv("BASE_REF", raising=False)
    assert default_upstream() == "origin/dev"
    monkeypatch.setenv("BASE_REF", "main")
    assert default_upstream() == "origin/main"
    monkeypatch.setenv("BASE_REF", " ")
    assert default_upstream() == "origin/dev"


def test_base_ref_is_what_the_check_compares_against(tmp_path, monkeypatch, capsys):
    """BASE_REF=dev, as the policy tells agents to run it, compares with origin/dev.

    dev took a prefix of `topic` through a merge commit; main never saw `topic`. Against
    origin/dev that is a partial merge (exit 1). The old code ignored BASE_REF and
    compared with origin/main, where `topic` is simply unmerged, and passed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "topic")
    _commit(repo, "topic-1")
    first = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "topic-2")
    _git(repo, "update-ref", "refs/remotes/origin/topic", "HEAD")
    _git(repo, "checkout", "-q", "-b", "dev", "main")
    _commit(repo, "on-dev")
    _git(repo, "merge", "-q", "--no-ff", "-m", "take a prefix", first)
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BASE_REF", "dev")
    assert main(["--acknowledged", str(tmp_path / "none.txt"), "origin/topic"]) == 1
    assert "origin/topic is partially merged into origin/dev" in capsys.readouterr().out
    monkeypatch.setenv("BASE_REF", "main")
    assert main(["--acknowledged", str(tmp_path / "none.txt"), "origin/topic"]) == 0


def test_a_missing_upstream_is_named_not_a_traceback(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "base")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BASE_REF", "origin/dev")
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "No such upstream: origin/origin/dev" in err and "branch name such as 'dev'" in err and "--upstream" in err
