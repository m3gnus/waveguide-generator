"""
Git-based update checking service.
"""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


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
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".git").exists():
        raise RuntimeError(
            "Git repository not found (.git directory is missing). "
            "If you downloaded the code as a ZIP file, please initialize a git repository or "
            "clone from https://github.com/m3gnus/waveguide-generator"
        )

    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("Git is not installed or not in system PATH.")

    try:
        _run_git(repo_root, "remote", "get-url", "origin")
    except RuntimeError:
        raise RuntimeError(
            "Git remote 'origin' is not configured. "
            "Expected remote: https://github.com/m3gnus/waveguide-generator.git"
        )

    try:
        _run_git(repo_root, "fetch", "origin", "--quiet")
    except RuntimeError as exc:
        raise RuntimeError(
            "Unable to fetch updates from origin. Check network and remote access."
        ) from exc

    current_commit = _run_git(repo_root, "rev-parse", "HEAD")
    current_branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")

    try:
        origin_head_ref = _run_git(repo_root, "symbolic-ref", "refs/remotes/origin/HEAD")
    except RuntimeError:
        origin_head_ref = "refs/remotes/origin/main"

    default_branch = origin_head_ref.rsplit("/", 1)[-1]
    remote_ref = f"refs/remotes/origin/{default_branch}"
    remote_commit = _run_git(repo_root, "rev-parse", remote_ref)

    counts_raw = _run_git(
        repo_root, "rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"
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
        "defaultBranch": default_branch,
        "currentCommit": current_commit,
        "remoteCommit": remote_commit,
        "checkedAt": datetime.now().isoformat(),
    }
