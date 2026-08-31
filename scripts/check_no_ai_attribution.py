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
    parser.add_argument("--upstream", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)

    commits = commits_in_range(args.upstream, args.head)
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
