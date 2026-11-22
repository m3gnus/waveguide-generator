"""Refuse a branch that the working branch has taken a prefix of.

The working branch is the upstream this checks against: `origin/dev` by default
(GIT-WORKFLOW section 2), or `origin/$BASE_REF`, or ``--upstream``. The history
below happened on `main` when `main` was the working branch; read "the upstream"
for `main` in the signal.

On 2026-08-26, PR #29 merged `work/windows-issue-260826` at `26f3a14c`, five
commits short of its tip. It took the auto-split sweep default and left behind
the commit that made splitting legal (`daemon=False` on the BEMPP worker), so
every default solve of 80 or more frequencies died on `main`. Neither commit is
wrong alone. Review passed, and CI passed because the test that catches it was
written with the fix and stayed on the unmerged tail.

Nothing in the toolchain looked at *how much* of a branch a merge took.

THE SIGNAL. When a branch forks from the upstream normally, the merge base is a
commit on the upstream's own first-parent chain. When the upstream has absorbed
part of a branch, the merge base is one of the branch's own commits, which reached
the upstream through the second parent of a merge and is therefore NOT on the
first-parent chain. So a branch is partially merged when both hold:

    git rev-list --count <upstream>..<branch>   is greater than zero
    git merge-base <upstream> <branch>          is not on the upstream's first-parent chain

WHAT IT DOES NOT PROVE: that a branch is merged at its tip. A branch that is wholly
unmerged, or fast-forwarded by hand to a middle commit (its merge base is then on
the first-parent chain), passes. `git log <branch> ^dev` being empty is the proof
the policy asks for; this is an extra check for merges that took a prefix.

Replayed against the real refs this flags `work/windows-issue-260826` at the
broken `main` (6070dab6) and flags none of the branches alive today.

WHAT IT CANNOT SEE, stated rather than discovered later: a squash merge. Squashing
rewrites the branch's commits into one new commit on the first-parent chain, so
no merge base points into the branch and nothing here fires. `git cherry` cannot
recover it either -- it lists only commits `main` does not already reach, so an
absorbed prefix never appears in its output at all. That is why this check reads
the graph instead. Against a squash merge the remaining defence is the rule
itself: merge a branch at its tip, or name the commits left behind.

A branch may be left partially merged deliberately -- a commit held back out of
a release cut, say. That is allowed, but it has to be *said*, which is the rule
GIT-WORKFLOW section 2 states as "merge the branch at its tip, or name the
commits you are leaving behind and why each is safe to leave". Listing the branch
in ``.github/partial-merges.txt`` with a reason turns that from prose in a pull
request body into a reviewable artifact, and downgrades the failure to a warning
for that branch alone.

Run with no arguments to check every remote branch, or pass branch names.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


ACKNOWLEDGED_PATH = Path(".github/partial-merges.txt")


def acknowledged(path: Path) -> dict[str, str]:
    """Branches whose partial merge is deliberate, mapped to the stated reason."""

    if not path.is_file():
        return {}
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        branch, separator, reason = stripped.partition(":")
        if not separator or not reason.strip():
            raise SystemExit(
                f"{path}: every entry must be '<branch>: <reason>', got {stripped!r}"
            )
        entries[branch.strip()] = reason.strip()
    return entries


def remote_branches(upstream: str) -> list[str]:
    remote = upstream.split("/", 1)[0]
    listed = _git("for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}")
    return [
        name
        for name in listed.split()
        # refs/remotes/<remote> itself matches when the remote has a HEAD
        # symref, and it is not a branch.
        if name != upstream and name != remote and not name.endswith("/HEAD")
    ]


def absorbed_prefix(branch: str, upstream: str) -> tuple[str, list[str]] | None:
    """Return (merge base, commits left behind) when main holds part of `branch`."""

    left = _git("rev-list", f"{upstream}..{branch}").split()
    if not left:
        return None
    base = _git("merge-base", upstream, branch).strip()
    first_parent = set(_git("rev-list", "--first-parent", upstream).split())
    if base in first_parent:
        return None
    return base, left


def default_upstream() -> str:
    """The branch work lands on: `origin/dev`, or `origin/$BASE_REF` when BASE_REF is set.

    GIT-WORKFLOW section 2 makes `dev` the working branch here and has agents run
    `BASE_REF=dev python scripts/check_partial_merges.py`; `main` moves only at a release,
    so checking against it compared every branch with a ref hundreds of commits behind.
    ``--upstream`` still overrides both.

    The trunk is named outright, never found by probing for a ref: the old "prefer
    `origin/next` when it exists" default resolved to a frozen branch once the model
    changed (2026-09-03), because a ref outlives the decision that retired it, and every
    later landing widened the gap into false absorbed-prefix reports -- at the moment
    someone runs this by hand because they distrust CI. A future model change edits this
    function rather than silently re-pointing it.
    """
    base = os.environ.get("BASE_REF", "").strip()
    return f"origin/{base}" if base else "origin/dev"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("branches", nargs="*", help="default: every origin branch")
    parser.add_argument(
        "--upstream",
        default=None,
        help="default: origin/$BASE_REF, else origin/dev",
    )
    parser.add_argument("--acknowledged", type=Path, default=ACKNOWLEDGED_PATH)
    args = parser.parse_args(argv)
    if args.upstream is None:
        args.upstream = default_upstream()
    verify = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{args.upstream}^{{commit}}"],
                            capture_output=True, text=True, check=False)
    if verify.returncode:
        hint = (" (BASE_REF takes a branch name such as 'dev', not a ref like 'origin/dev')"
                if args.upstream.startswith("origin/origin/") else "")
        print(f"No such upstream: {args.upstream}{hint}. Fetch it, or name one with --upstream.", file=sys.stderr)
        return 2
    excused = acknowledged(args.acknowledged)

    branches = args.branches or remote_branches(args.upstream)
    if not branches:
        print(f"No branches besides {args.upstream}; nothing to check.")
        return 0

    failures = 0
    for branch in branches:
        found = absorbed_prefix(branch, args.upstream)
        if found is None:
            continue
        base, left = found
        reason = excused.get(branch) or excused.get(branch.split("/", 1)[-1])
        if reason is None:
            failures += 1
        subject = _git("log", "-1", "--format=%s", base).strip()
        verdict = "deliberately" if reason else ""
        print(f"{branch} is {verdict}partially merged into {args.upstream}.".replace("  ", " "))
        print(f"  {args.upstream} holds this branch up to {base[:8]}  {subject}")
        print(f"  {len(left)} commit(s) left behind:")
        for commit in left:
            print(f"    {commit[:8]}  {_git('log', '-1', '--format=%s', commit).strip()}")
        if reason:
            print(f"  Acknowledged in {args.acknowledged}: {reason}")
        else:
            print(
                f"  Merge the branch at its tip, or record it in {args.acknowledged} "
                "as '<branch>: <why each commit above is safe to leave behind>'."
            )

    if failures:
        print(f"\n{failures} partially merged branch(es).", file=sys.stderr)
        return 1
    print(f"No partially merged branches ({len(branches)} checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
