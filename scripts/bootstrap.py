#!/usr/bin/env python3
"""Create and validate Waveguide Generator's repository-local environment."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import venv

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENV = REPO_ROOT / ".venv"
STAMP_NAME = ".wg2-bootstrap.json"
LOCK_NAME_PREFIX = "wg2-bootstrap-"
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


def _lock_descriptor(descriptor: int) -> None:
    """Block until this process exclusively owns the persistent lock file."""

    if sys.platform == "win32":
        # LK_LOCK gives up after roughly ten seconds. A bootstrap can take much
        # longer, so retry the non-blocking operation until the owner exits.
        while True:
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in {13, 33, 36}:
                    raise
                time.sleep(0.1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _repository_git_directory(root: Path) -> Path | None:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        try:
            label, value = marker.read_text(encoding="utf-8").strip().split(":", 1)
            if label.strip().lower() == "gitdir":
                git_dir = Path(value.strip())
                if not git_dir.is_absolute():
                    git_dir = marker.parent / git_dir
                return git_dir.resolve()
        except (OSError, ValueError):
            pass
    return None


def _bootstrap_lock_path(environment: Path) -> Path:
    """Locate a persistent lock named for the canonical environment path."""

    canonical = environment.expanduser().resolve()
    identity = os.path.normcase(str(canonical)).encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()
    git_dir = _repository_git_directory(REPO_ROOT)
    if git_dir is not None:
        # Git metadata is persistent but never dirties the checkout.
        return git_dir / f"{LOCK_NAME_PREFIX}{suffix}.lock"
    # A source archive has no Git status to dirty. Keep the lock outside the
    # not-yet-created environment so validation cannot mistake it for a venv.
    return REPO_ROOT / f".{LOCK_NAME_PREFIX}{suffix}.lock"


@contextmanager
def _bootstrap_lock(environment: Path) -> Iterator[None]:
    """Serialize every inspection and mutation of one resolved environment."""

    path = _bootstrap_lock_path(environment)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise RuntimeError(f"Could not open bootstrap lock {path}: {exc}") from exc
    locked = False
    try:
        # Windows byte-range locks require the locked byte to exist.
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        try:
            _lock_descriptor(descriptor)
            locked = True
        except OSError as exc:
            raise RuntimeError(f"Could not acquire bootstrap lock {path}: {exc}") from exc
        yield
    finally:
        if locked:
            try:
                _unlock_descriptor(descriptor)
            except OSError:
                # Closing also releases the OS lock.
                pass
        os.close(descriptor)


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _site_packages(environment: Path) -> Path | None:
    if os.name == "nt":
        candidate = environment / "Lib" / "site-packages"
        return candidate if candidate.is_dir() else None
    for candidate in sorted((environment / "lib").glob("python*/site-packages")):
        if candidate.is_dir():
            return candidate
    return None


def _venv_evidence(environment: Path) -> dict[str, object] | None:
    """Cheap proof that nobody has changed this environment behind our back.

    The full validation costs two subprocesses -- roughly 1.9 s on Windows,
    where every interpreter start is two ``CreateProcess`` calls plus antivirus
    -- and it runs on *every* launch. What it is really defending against is
    somebody pip-installing into ``.venv`` out of band, because the manifest
    fingerprint alone cannot see that.

    This sees it cheaply: one ``scandir`` enumerates the set of ``*.dist-info``
    directories, and four small ``direct_url.json`` reads capture the identity
    of the Git-pinned distributions. Pair that with the interpreter's own size
    and mtime and an environment that still matches has not been touched.
    Anything that does not match falls through to the full check, so this can
    only ever save time, never approve a broken venv.

    Returns ``None`` when the evidence cannot be gathered, which is itself a
    reason to take the slow path.
    """

    python = _venv_python(environment)
    site_packages = _site_packages(environment)
    if site_packages is None:
        return None
    try:
        stat = python.stat()
        names = sorted(
            entry.name
            for entry in os.scandir(site_packages)
            if entry.name.endswith(".dist-info")
        )
        direct_urls: list[tuple[str, bytes]] = []
        for distribution in sorted(_locked_git_commits()):
            normalized = re.sub(r"[-_.]+", "_", distribution).casefold()
            matches = [
                name
                for name in names
                if name.casefold().startswith(f"{normalized}-")
            ]
            if len(matches) != 1:
                return None
            direct_urls.append(
                (
                    distribution,
                    (site_packages / matches[0] / "direct_url.json").read_bytes(),
                )
            )
    except OSError:
        return None
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update(b"\0")
    git_digest = hashlib.sha256()
    for distribution, direct_url in direct_urls:
        git_digest.update(distribution.encode())
        git_digest.update(b"\0")
        git_digest.update(direct_url)
        git_digest.update(b"\0")
    return {
        "pythonSize": stat.st_size,
        "pythonMtimeNs": stat.st_mtime_ns,
        "distributions": digest.hexdigest(),
        "distributionCount": len(names),
        "gitDirectUrls": git_digest.hexdigest(),
    }


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


def _locked_versions() -> dict[str, tuple[str, str | None]]:
    """Map every locked distribution to its version and its PEP 508 marker."""

    versions: dict[str, tuple[str, str | None]] = {"pip": (PIP_VERSION, None)}
    lock_path = REPO_ROOT / "server" / "requirements-lock.txt"
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirement, _, marker = line.partition(";")
        name, separator, version = requirement.strip().partition("==")
        if not separator or not name or not version:
            raise RuntimeError(f"Invalid exact constraint in {lock_path}: {raw_line!r}")
        versions[name] = (version, marker.strip() or None)
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


def _validate(
    environment: Path,
    expected_fingerprint: str,
    allow_fast_path: bool = True,
    *,
    record_evidence: bool = True,
) -> tuple[bool, str]:
    """Is this environment ready to serve?

    ``allow_fast_path`` exists for the one caller that must not trust a stamp:
    the verification immediately after installing. That check is what caught
    Windows never being able to satisfy the uvloop lock entry, and a stamp
    written seconds earlier by the same run would tell it nothing.
    """

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

    # An environment this bootstrap itself installed, whose interpreter,
    # installed-distribution set, and pinned Git provenance are unchanged since,
    # cannot have drifted from what the slow path would find. A stamp written by
    # an older bootstrap has no evidence recorded and correctly falls through.
    recorded_evidence = stamp.get("venvEvidence")
    if (
        allow_fast_path
        and recorded_evidence is not None
        and recorded_evidence == _venv_evidence(environment)
    ):
        return True, "ready"

    expected_versions = _locked_versions()
    expected_git_commits = _locked_git_commits()
    probe = (
        "import json, sys; from importlib.metadata import distribution, version; "
        "from packaging.markers import Marker; "
        f"assert sys.version_info[:2] == {PYTHON_SERIES!r}; "
        f"expected = {expected_versions!r}; "
        # A marked lock entry is only required where its marker applies: uvloop
        # is POSIX-only, so demanding it everywhere fails every Windows install.
        # Markers are evaluated in the environment under test because the
        # interpreter running this bootstrap has no third-party packages.
        "assert all(version(name) == wanted for name, (wanted, marker) in expected.items() "
        "if marker is None or Marker(marker).evaluate()); "
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
    # Remember that *this* environment state passed, so the next launch can
    # answer from the stamp. Without this an environment installed by an
    # earlier bootstrap -- or simply one that was already correct -- would pay
    # the two subprocesses forever, because nothing else rewrites the stamp on
    # a successful check.
    if record_evidence:
        _record_validated_evidence(environment, expected_fingerprint)
    return True, "ready"


def _record_validated_evidence(environment: Path, fingerprint: str) -> None:
    """Note that this exact environment passed the full check. Best-effort.

    A read-only or otherwise unwritable environment simply keeps taking the
    slow path; failing a successful validation over a bookkeeping write would
    be absurd.
    """

    evidence = _venv_evidence(environment)
    if evidence is None:
        return
    try:
        _write_stamp(environment, fingerprint, evidence=evidence)
    except OSError:
        pass


def _require_supported_python() -> None:
    current = sys.version_info[:2]
    if current != PYTHON_SERIES:
        expected = ".".join(map(str, PYTHON_SERIES))
        actual = ".".join(map(str, current))
        raise RuntimeError(
            f"CPython {expected} is required to bootstrap Waveguide Generator "
            f"(running {actual}). "
            f"Run this script with python{expected}."
        )


def _write_stamp(
    environment: Path, fingerprint: str, *, evidence: dict[str, object] | None = None
) -> None:
    stamp = {
        "bootstrapVersion": BOOTSTRAP_VERSION,
        "fingerprint": fingerprint,
        "python": f"{PYTHON_SERIES[0]}.{PYTHON_SERIES[1]}",
        "venvEvidence": _venv_evidence(environment) if evidence is None else evidence,
    }
    temporary = environment / f"{STAMP_NAME}.tmp"
    temporary.write_text(json.dumps(stamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(environment / STAMP_NAME)


def bootstrap(environment: Path, *, force: bool = False) -> None:
    environment = environment.expanduser().resolve()
    with _bootstrap_lock(environment):
        _bootstrap_locked(environment, force=force)


def _bootstrap_locked(environment: Path, *, force: bool = False) -> None:
    _require_supported_python()
    fingerprint = _fingerprint()
    valid, reason = _validate(environment, fingerprint)
    if valid and not force:
        print(f"Waveguide Generator environment is already ready: {environment}")
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

    # Once pip starts mutating an environment, its previous proof is no longer
    # valid. In particular, a failed --force reinstall uses the same manifest
    # fingerprint; leaving the old stamp behind could let the cheap evidence
    # path approve the half-reinstalled environment on the next launch.
    (environment / STAMP_NAME).unlink(missing_ok=True)
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
    valid, reason = _validate(environment, fingerprint, False)
    if not valid:
        (environment / STAMP_NAME).unlink(missing_ok=True)
        raise RuntimeError(f"The environment was installed but validation failed: {reason}.")
    print(f"Waveguide Generator environment is ready: {environment}")


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
            with _bootstrap_lock(environment):
                valid, reason = _validate(environment, fingerprint)
            if valid:
                print(f"Waveguide Generator environment is ready: {environment}")
                return 0
            print(f"Waveguide Generator environment needs bootstrap: {reason}", file=sys.stderr)
            return 1
        bootstrap(environment, force=args.force)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Could not bootstrap Waveguide Generator: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
