#!/usr/bin/env python3
"""Refuse commits that credit an AI assistant.

`AGENTS.md` and `GIT-WORKFLOW.md` §3 forbid AI attribution in Git history: no
`Co-Authored-By` trailer naming an assistant, no "generated with" footer, no tool
name in a commit message. The rule exists because the history is public and the
authorship it records should be the people who own the work.

It was being enforced by asking. That is not enough when the tooling adds the
trailer by default: `8a5b6bd3` reached `main` through PR #51 on 2026-08-27, and
`5c9dde0b` reached `next` through PR #53 the same day. Both passed review. The
27 August history rewrite -- the one `backup/pre-trailer-strip` is named after --
stripped the trailers that existed then, and two more arrived within hours,
because nothing in CI looked.

WHAT IT CHECKS. Only commits the branch adds, `<upstream>..HEAD`. Scanning all of
history would fail permanently on `8a5b6bd3`, which is reachable from `main` and
cannot be removed without rewriting the default branch out from under every
clone. Gating new work is what stops the leak; the one commit already on `main`
is a separate decision, deliberately not forced by this check.

Merge commits are included. GitHub copies a pull request's body into the merge
commit it creates, so a trailer in the body reaches history even when every
commit on the branch is clean.

WHICH RANGE. That depends on the event, and getting it wrong is not visible --
an empty range reports success, so a broken base reads exactly like a clean
branch. A pull request is measured against its own base branch. A push is
measured against `github.event.before`, the commit the ref pointed at *before*
the push. It must not be measured against `origin/<branch>`: on a push to trunk
the checkout is the pushed commit and `origin/main` is that same commit, so
`origin/main..HEAD` is empty and the gate passed over every commit in the batch
without reading one message. That was the state until 2026-09-08, on the primary
landing path.

A push that creates a ref carries the all-zero SHA as its base, and a push over
rewritten history carries a base the checkout no longer has. Neither has a range
this check can compute, and neither may fall back to all of history -- see
`8a5b6bd3` above. So the deliberate policy is: inspect the tip commit alone and
say so on stdout. That is strictly more than the empty range it replaces, it
never fails permanently on published history, and the shape only occurs when a
ref is created or rewritten, which is a human-authorized operation with its own
review.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

#: Broader than any one tool's exact string, but not so broad that it fires on a
#: person. "Co-Authored-By: Claude Debussy <claude@example.com>" is a human, and a
#: check that rejects him is a check somebody will pass `--no-verify` to. So an
#: assistant is recognised by a vendor domain or a product name, never by a bare
#: first name.
PATTERNS = (
    re.compile(
        r"^\s*co-authored-by:.*("
        r"@anthropic\.com|@openai\.com|copilot@|"
        r"claude\s+(code|opus|sonnet|haiku)|chatgpt|github\s+copilot"
        r")",
        re.I,
    ),
    re.compile(r"generated with .*(claude|chatgpt|copilot|cursor)", re.I),
    re.compile(r"^\s*(assisted|authored)-by:.*(ai|assistant)\b", re.I),
)


def offending_lines(message: str) -> list[str]:
    """Every line of ``message`` that credits an assistant."""

    return [
        line.strip()
        for line in message.splitlines()
        if any(pattern.search(line) for pattern in PATTERNS)
    ]


#: What GitHub sends as a push event's base when the push created the ref.
ZERO_SHA = "0" * 40


def select_upstream(
    *, event_name: str, base_ref: str, before: str, default_branch: str = "main"
) -> str | None:
    """The commit the event's new commits are measured against.

    ``None`` means the event names no base this check can use -- a created ref,
    or a base the checkout does not have -- and the caller falls back to the tip
    commit alone.

    Pure, so the mapping from an event payload to a range is testable without a
    repository. It is the part that was wrong.
    """

    if event_name == "pull_request" or event_name == "pull_request_target":
        return f"origin/{(base_ref or default_branch).strip()}"
    if event_name == "push":
        sha = before.strip()
        # A created ref has no base. `set(sha) == {"0"}` rather than an equality
        # with ZERO_SHA, because the payload may abbreviate it.
        if not sha or set(sha) == {"0"}:
            return None
        return sha
    # workflow_dispatch, or a hand run that named no event: the branch's own
    # trunk is the only base available, and a hand run can pass --upstream.
    return f"origin/{(base_ref or default_branch).strip()}"


def resolves(revision: str) -> bool:
    """Whether this checkout has ``revision``.

    A force-push over rewritten history names a base that was pruned with the
    ref it hung from, so the base an event carries is not always fetchable.
    """

    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def commits_in_range(upstream: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "rev-list", f"{upstream}..{head}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"Could not list {upstream}..{head}. CI needs fetch-depth: 0 and the "
            f"upstream ref fetched.\n{result.stderr.strip()}"
        )
    return result.stdout.split()


def message_of(commit: str) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%B", commit],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upstream",
        default=None,
        help="Measure against this commit, overriding the event. A hand run.",
    )
    parser.add_argument(
        "--event", default="", help="GITHUB_EVENT_NAME: push, pull_request, ..."
    )
    parser.add_argument(
        "--base-ref", default="", help="github.base_ref: a pull request's base branch."
    )
    parser.add_argument(
        "--before",
        default="",
        help="github.event.before: what the ref pointed at before a push.",
    )
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)

    from_event = args.upstream is None
    upstream = (
        select_upstream(
            event_name=args.event, base_ref=args.base_ref, before=args.before
        )
        if from_event
        else args.upstream
    )

    if upstream is None:
        # A created ref. Documented in the module docstring: the tip alone,
        # never all of history, and never a silent empty range.
        print(
            f"The {args.event or 'local'} event carries no base commit, so only "
            f"{args.head} is inspected."
        )
        commits = [args.head]
    elif from_event and not resolves(upstream):
        print(
            f"{upstream} is not in this checkout -- history was rewritten under "
            f"the ref -- so only {args.head} is inspected."
        )
        commits = [args.head]
    else:
        print(f"checking {upstream}..{args.head}")
        commits = commits_in_range(upstream, args.head)
    found = [
        (commit, lines)
        for commit in commits
        if (lines := offending_lines(message_of(commit)))
    ]
    if not found:
        print(f"No AI attribution in {len(commits)} new commit(s).")
        return 0

    print(
        f"{len(found)} of {len(commits)} new commit(s) credit an AI assistant, "
        "which AGENTS.md forbids:",
        file=sys.stderr,
    )
    for commit, lines in found:
        subject = subprocess.run(
            ["git", "log", "-1", "--format=%h %s", commit],
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        ).stdout.strip()
        print(f"  {subject}", file=sys.stderr)
        for line in lines:
            print(f"      {line}", file=sys.stderr)
    print(
        "\nRewrite the message rather than adding another commit -- the trailer is "
        "in the history, not in the tree. For the branch tip:\n"
        "    git commit --amend\n"
        "and for anything older:\n"
        "    git rebase -i <upstream>\n"
        "If your tooling adds the trailer automatically, strip it before "
        "committing; AGENTS.md overrides that default.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
