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

from scripts.check_no_ai_attribution import (  # noqa: E402
    ZERO_SHA,
    main,
    offending_lines,
    select_upstream,
)


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


# --- Which range the event selects -------------------------------------------
#
# The gate was ineffective on the primary landing path for reasons that have
# nothing to do with the patterns above: it read an empty range. `origin/main`
# on a push to trunk IS the commit that was just pushed, so `origin/main..HEAD`
# held nothing and "No AI attribution in 0 new commit(s)" was reported as a
# pass. These tests pin the mapping from an event to a range, and then prove the
# mapping end to end on a real repository, because a range error is invisible in
# the output that a correct clean run produces.


def _rev(repo: Path, revision: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", revision],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def test_a_push_is_measured_against_the_commit_the_ref_was_at() -> None:
    assert (
        select_upstream(event_name="push", base_ref="", before="abc1234") == "abc1234"
    )


def test_a_push_never_selects_a_moving_remote_ref() -> None:
    """The whole defect in one assertion.

    `origin/main` is a moving ref: on the push that lands a batch it already
    points at the batch. It can never be that push's historical base.
    """

    chosen = select_upstream(event_name="push", base_ref="", before="abc1234")
    assert chosen is not None
    assert not chosen.startswith("origin/")


@pytest.mark.parametrize("before", [ZERO_SHA, "0000000", ""])
def test_a_created_ref_has_no_base(before: str) -> None:
    assert select_upstream(event_name="push", base_ref="", before=before) is None


def test_a_pull_request_is_measured_against_its_own_base() -> None:
    assert (
        select_upstream(event_name="pull_request", base_ref="main", before="")
        == "origin/main"
    )
    assert (
        select_upstream(event_name="pull_request", base_ref="release/1.x", before="")
        == "origin/release/1.x"
    )


def test_a_hand_run_or_dispatch_still_measures_against_trunk() -> None:
    assert select_upstream(event_name="", base_ref="", before="") == "origin/main"
    assert (
        select_upstream(event_name="workflow_dispatch", base_ref="", before="")
        == "origin/main"
    )


def _trunk_push(tmp_path: Path) -> tuple[Path, str]:
    """A checkout shaped exactly as CI's is on a push to `main`.

    HEAD is the pushed commit and `origin/main` is the same commit, because the
    workflow fetches the branches after the push landed.
    """

    repo = _repo(tmp_path)
    before = _rev(repo, "HEAD")
    _commit(repo, "two\n", f"Land a thing\n\nWhy.\n\n{REAL_TRAILER}")
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    return repo, before


def test_the_old_trunk_range_was_empty_and_therefore_vacuous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression witness: this is what the gate used to do, and it passed."""

    repo, _before = _trunk_push(tmp_path)
    monkeypatch.chdir(repo)

    assert main(["--upstream", "origin/main", "--head", "HEAD"]) == 0


def test_a_trailer_pushed_to_trunk_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, before = _trunk_push(tmp_path)
    monkeypatch.chdir(repo)

    assert main(["--event", "push", "--before", before, "--head", "HEAD"]) == 1


def test_every_commit_of_a_multi_commit_push_is_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A landing batch is several commits, and the trailer may be on any of them.

    The trailer here is on the FIRST commit of the push, so a check that only
    looked at the tip would miss it.
    """

    repo = _repo(tmp_path)
    before = _rev(repo, "HEAD")
    _commit(repo, "two\n", f"First of the batch\n\n{REAL_TRAILER}")
    _commit(repo, "three\n", "Second of the batch")
    _commit(repo, "four\n", "Third of the batch")
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    monkeypatch.chdir(repo)

    assert main(["--event", "push", "--before", before, "--head", "HEAD"]) == 1


def test_a_clean_push_to_trunk_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And it passes over commits it really read, not over an empty range."""

    repo = _repo(tmp_path)
    before = _rev(repo, "HEAD")
    _commit(repo, "two\n", "Land a thing\n\nBecause of a reason.")
    _commit(repo, "three\n", "Land another\n\nAnd a reason for it.")
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    monkeypatch.chdir(repo)

    assert main(["--event", "push", "--before", before, "--head", "HEAD"]) == 0


def test_history_before_the_push_is_still_not_the_pushs_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`8a5b6bd3` is on `main`. A correct range must not resurrect it."""

    repo = _repo(tmp_path)
    _commit(repo, "two\n", f"Old and already merged\n\n{REAL_TRAILER}")
    before = _rev(repo, "HEAD")
    _commit(repo, "three\n", "New work, clean")
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    monkeypatch.chdir(repo)

    assert main(["--event", "push", "--before", before, "--head", "HEAD"]) == 0


def test_a_first_push_inspects_the_tip_rather_than_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A created ref carries the all-zero SHA, so there is no range to compute.

    The policy is the tip alone: more than the empty range it replaces, and not
    all of history, which would fail permanently on `8a5b6bd3`.
    """

    repo = _repo(tmp_path)
    _commit(repo, "two\n", f"The very first push\n\n{REAL_TRAILER}")
    monkeypatch.chdir(repo)

    assert main(["--event", "push", "--before", ZERO_SHA, "--head", "HEAD"]) == 1


def test_a_first_push_of_a_clean_tip_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "two\n", "The very first push, clean")
    monkeypatch.chdir(repo)

    assert main(["--event", "push", "--before", ZERO_SHA, "--head", "HEAD"]) == 0


def test_a_base_the_checkout_lost_falls_back_to_the_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A force-push over rewritten history names a base that was pruned with it.

    Raising `Could not list ...` there would turn a rewrite into a red trunk;
    the fallback inspects the tip and says so.
    """

    repo = _repo(tmp_path)
    _commit(repo, "two\n", f"Rewritten onto a lost base\n\n{REAL_TRAILER}")
    monkeypatch.chdir(repo)

    assert main(["--event", "push", "--before", "f" * 40, "--head", "HEAD"]) == 1


def test_a_pull_request_range_catches_a_trailer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PR shape: HEAD is the merge ref, `origin/<base_ref>` is the base."""

    repo = _repo(tmp_path)
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    _commit(repo, "two\n", "Clean work on the branch")
    _commit(repo, "three\n", f"And one that is not\n\n{REAL_TRAILER}")
    monkeypatch.chdir(repo)

    assert main(["--event", "pull_request", "--base-ref", "main", "--head", "HEAD"]) == 1


def test_a_clean_pull_request_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    _commit(repo, "two\n", "Clean work on the branch")
    monkeypatch.chdir(repo)

    assert main(["--event", "pull_request", "--base-ref", "main", "--head", "HEAD"]) == 0


def test_the_workflow_hands_the_script_the_events_own_base() -> None:
    """The range lives in the script now, so CI must pass the payload, not a ref."""

    ci = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    step = ci.split("Check new commits for AI attribution", 1)[1].split("\n  drift:", 1)[
        0
    ]
    assert "BEFORE_SHA: ${{ github.event.before }}" in step
    assert "EVENT_NAME: ${{ github.event_name }}" in step
    assert "--before \"$BEFORE_SHA\"" in step
    assert "--event \"$EVENT_NAME\"" in step
    # The moving ref that made the range empty must not come back. Comments may
    # name it -- the step explains what it replaced -- so read the commands only.
    commands = "\n".join(
        line for line in step.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--upstream" not in commands
    assert "origin/" not in commands
