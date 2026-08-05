#!/usr/bin/env python3
"""Create and validate Waveguide Generator v2's repository-local environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import venv


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENV = REPO_ROOT / ".venv"
STAMP_NAME = ".wg2-bootstrap.json"
BOOTSTRAP_VERSION = 1
PYTHON_SERIES = (3, 13)
PIP_VERSION = "26.1.2"
REQUIREMENT_FILES = (
    REPO_ROOT / "server" / "requirements-runtime.txt",
    REPO_ROOT / "server" / "requirements-pins.txt",
    REPO_ROOT / "server" / "requirements-dev.txt",
    REPO_ROOT / "server" / "requirements-lock.txt",
    REPO_ROOT / "pins.json",
)
REQUIRED_DISTRIBUTIONS = (
    "fastapi",
    "gmsh",
    "hornlab-bempp-bem",
    "hornlab-metal-bem",
    "hornlab-plots",
    "hornlab-waveguide-mesher",
    "matplotlib",
    "meshio",
    "numpy",
    "pydantic",
    "pytest",
    "scipy",
    "uvicorn",
)
GIT_REQUIREMENT_RE = re.compile(
    r"^git\+[^@]+@(?P<sha>[0-9a-f]{40})#egg=(?P<name>[A-Za-z0-9_.-]+)$"
)


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(f"bootstrap:{BOOTSTRAP_VERSION}\n".encode())
    digest.update(f"python:{PYTHON_SERIES[0]}.{PYTHON_SERIES[1]}\n".encode())
    digest.update(f"pip:{PIP_VERSION}\n".encode())
    for path in REQUIREMENT_FILES:
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _run(command: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        text=True,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
        check=False,
    )


def _locked_versions() -> dict[str, str]:
    versions = {"pip": PIP_VERSION}
    lock_path = REPO_ROOT / "server" / "requirements-lock.txt"
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if not separator or not name or not version:
            raise RuntimeError(f"Invalid exact constraint in {lock_path}: {raw_line!r}")
        versions[name] = version
    return versions


def _locked_git_commits() -> dict[str, str]:
    commits: dict[str, str] = {}
    pins_path = REPO_ROOT / "server" / "requirements-pins.txt"
    for raw_line in pins_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = GIT_REQUIREMENT_RE.fullmatch(line)
        if match is None:
            raise RuntimeError(f"Invalid exact Git pin in {pins_path}: {raw_line!r}")
        commits[match.group("name")] = match.group("sha")
    if not commits:
        raise RuntimeError(f"No exact Git pins found in {pins_path}")
    return commits


def _validate(environment: Path, expected_fingerprint: str) -> tuple[bool, str]:
    python = _venv_python(environment)
    stamp_path = environment / STAMP_NAME
    if not python.is_file():
        return False, f"{python} does not exist"
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False, f"{stamp_path} is missing or invalid"
    if stamp.get("fingerprint") != expected_fingerprint:
        return False, "the dependency manifests or bootstrap version changed"

    expected_versions = _locked_versions()
    expected_git_commits = _locked_git_commits()
    probe = (
        "import json, sys; from importlib.metadata import distribution, version; "
        f"assert sys.version_info[:2] == {PYTHON_SERIES!r}; "
        f"expected = {expected_versions!r}; "
        "assert all(version(name) == wanted for name, wanted in expected.items()); "
        f"[version(name) for name in {REQUIRED_DISTRIBUTIONS!r}]; "
        f"git_expected = {expected_git_commits!r}; "
        "direct_urls = {name: json.loads(distribution(name).read_text('direct_url.json') or '{}') "
        "for name in git_expected}; "
        "assert all(direct_urls[name].get('vcs_info', {}).get('vcs') == 'git' "
        "and direct_urls[name].get('vcs_info', {}).get('commit_id') == wanted "
        "and direct_urls[name].get('vcs_info', {}).get('requested_revision') == wanted "
        "for name, wanted in git_expected.items()); "
        "import fastapi, uvicorn"
    )
    if _run([str(python), "-c", probe], quiet=True).returncode != 0:
        return False, "one or more required packages are unavailable"
    if _run([str(python), "-m", "pip", "check"], quiet=True).returncode != 0:
        return False, "pip reports an inconsistent dependency set"
    return True, "ready"


def _require_supported_python() -> None:
    current = sys.version_info[:2]
    if current != PYTHON_SERIES:
        expected = ".".join(map(str, PYTHON_SERIES))
        actual = ".".join(map(str, current))
        raise RuntimeError(
            f"CPython {expected} is required to bootstrap v2 (running {actual}). "
            f"Run this script with python{expected}."
        )


def _write_stamp(environment: Path, fingerprint: str) -> None:
    stamp = {
        "bootstrapVersion": BOOTSTRAP_VERSION,
        "fingerprint": fingerprint,
        "python": f"{PYTHON_SERIES[0]}.{PYTHON_SERIES[1]}",
    }
    temporary = environment / f"{STAMP_NAME}.tmp"
    temporary.write_text(json.dumps(stamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(environment / STAMP_NAME)


def bootstrap(environment: Path, *, force: bool = False) -> None:
    _require_supported_python()
    fingerprint = _fingerprint()
    valid, reason = _validate(environment, fingerprint)
    if valid and not force:
        print(f"Waveguide Generator v2 environment is already ready: {environment}")
        return

    python = _venv_python(environment)
    if environment.exists() and not python.is_file():
        raise RuntimeError(
            f"{environment} exists but is not a usable virtual environment. "
            "Move it aside and run the bootstrap again."
        )
    if not environment.exists():
        print(f"Creating CPython {PYTHON_SERIES[0]}.{PYTHON_SERIES[1]} environment at {environment}")
        venv.EnvBuilder(with_pip=True).create(environment)

    print(f"Installing locked dependencies ({reason})")
    commands = (
        [str(python), "-m", "pip", "install", f"pip=={PIP_VERSION}"],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-c",
            str(REPO_ROOT / "server" / "requirements-lock.txt"),
            "-r",
            str(REPO_ROOT / "server" / "requirements-runtime.txt"),
            "-r",
            str(REPO_ROOT / "server" / "requirements-pins.txt"),
            "-r",
            str(REPO_ROOT / "server" / "requirements-dev.txt"),
        ],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            "-r",
            str(REPO_ROOT / "server" / "requirements-pins.txt"),
        ],
    )
    for command in commands:
        if _run(command).returncode != 0:
            raise RuntimeError("Dependency installation failed; review the pip output above.")

    _write_stamp(environment, fingerprint)
    valid, reason = _validate(environment, fingerprint)
    if not valid:
        (environment / STAMP_NAME).unlink(missing_ok=True)
        raise RuntimeError(f"The environment was installed but validation failed: {reason}.")
    print(f"Waveguide Generator v2 environment is ready: {environment}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate without installing")
    parser.add_argument("--force", action="store_true", help="reinstall even when the stamp is current")
    parser.add_argument("--venv", type=Path, default=DEFAULT_VENV, help="environment path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    environment = args.venv.expanduser().resolve()
    try:
        fingerprint = _fingerprint()
        if args.check:
            valid, reason = _validate(environment, fingerprint)
            if valid:
                print(f"Waveguide Generator v2 environment is ready: {environment}")
                return 0
            print(f"Waveguide Generator v2 environment needs bootstrap: {reason}", file=sys.stderr)
            return 1
        bootstrap(environment, force=args.force)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Could not bootstrap Waveguide Generator v2: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
