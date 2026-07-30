"""
Git-based update checking service.
"""

import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

_UPDATE_CHECK_LOCK = threading.Lock()


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args)} timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"exit code {exc.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}") from exc


def get_update_status() -> Dict[str, Any]:
    """Serialize fetch/status work so concurrent checks cannot contend on .git locks."""
    with _UPDATE_CHECK_LOCK:
        return _get_update_status_unlocked()


def _get_update_status_unlocked() -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".git").exists():
        raise RuntimeError(
            "Git repository not found (.git directory is missing). "
            "If you downloaded the code as a ZIP file, please initialize a git repository or "
            "clone from https://github.com/m3gnus/waveguide-generator"
        )

    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Git version check timed out.") from exc
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("Git is not installed or not in system PATH.")

    current_commit = _run_git(repo_root, "rev-parse", "HEAD")
    current_branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if current_branch == "HEAD":
        raise RuntimeError(
            "Updates cannot be checked while Git is in detached HEAD state. "
            "Switch to a branch and try again."
        )

    try:
        upstream_ref = _run_git(
            repo_root,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"Current branch '{current_branch}' has no upstream branch. "
            "Configure one with git push --set-upstream, then try again."
        ) from exc

    upstream_remote = _run_git(repo_root, "config", f"branch.{current_branch}.remote")
    if upstream_remote != ".":
        try:
            _run_git(repo_root, "remote", "get-url", upstream_remote)
        except RuntimeError as exc:
            raise RuntimeError(
                f"Git remote '{upstream_remote}' for branch '{current_branch}' is not configured."
            ) from exc

        try:
            _run_git(repo_root, "fetch", upstream_remote, "--quiet")
        except RuntimeError as exc:
            raise RuntimeError(
                f"Unable to fetch updates from {upstream_remote}. "
                "Check network and remote access."
            ) from exc

    upstream_branch = upstream_ref
    if upstream_remote != "." and upstream_ref.startswith(f"{upstream_remote}/"):
        upstream_branch = upstream_ref[len(upstream_remote) + 1 :]

    remote_commit = _run_git(repo_root, "rev-parse", upstream_ref)

    counts_raw = _run_git(
        repo_root, "rev-list", "--left-right", "--count", f"HEAD...{upstream_ref}"
    )
    counts = counts_raw.split()
    if len(counts) != 2:
        raise RuntimeError(f"Unexpected git rev-list output: '{counts_raw}'")

    ahead_count = int(counts[0])
    behind_count = int(counts[1])

    return {
        "updateAvailable": behind_count > 0,
        "aheadCount": ahead_count,
        "behindCount": behind_count,
        "currentBranch": current_branch,
        "upstreamRef": upstream_ref,
        "upstreamRemote": upstream_remote,
        "upstreamBranch": upstream_branch,
        "currentCommit": current_commit,
        "remoteCommit": remote_commit,
        "checkedAt": datetime.now().isoformat(),
    }
