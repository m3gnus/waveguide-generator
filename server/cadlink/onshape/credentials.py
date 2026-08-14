"""Locate and read the Onshape API key pair.

The pair is created by the account owner under My account > Developer > API keys
and pasted into a ``KEY=VALUE`` file that lives outside every git repository, so
it cannot be committed by accident.  WG only ever reads it: nothing here writes
the file, echoes a secret into a log, or returns one across the API boundary.
``OnshapeCredentials`` deliberately has no useful ``repr`` for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import platform
import stat
from typing import Mapping


ACCESS_KEY_ENV = "ONSHAPE_ACCESS_KEY"
SECRET_KEY_ENV = "ONSHAPE_SECRET_KEY"
BASE_URL_ENV = "ONSHAPE_BASE_URL"
DEFAULT_BASE_URL = "https://cad.onshape.com/api"

# Same location the O1 spike used, so a key pair pasted for the spike keeps
# working without being moved.
CREDENTIALS_RELATIVE = Path(".config") / "hornlab" / "onshape.env"


class OnshapeCredentialsError(RuntimeError):
    """No usable key pair was found."""


@dataclass(frozen=True)
class OnshapeCredentials:
    """An Onshape API key pair and the base URL it authenticates against."""

    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    source: str = "environment"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"OnshapeCredentials(source={self.source!r})"


def credentials_path(home: str | os.PathLike[str] | None = None) -> Path:
    """Return where WG looks for the key pair, without requiring it to exist."""

    root = Path.home() if home is None else Path(home)
    return root / CREDENTIALS_RELATIVE


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` file, ignoring comments and blank lines."""

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def file_is_world_readable(path: Path, *, system: str | None = None) -> bool:
    """Report a key file that other local accounts can read.

    Advisory only: a loose mode is worth telling the owner about, but refusing
    to work would strand a user who cannot easily chmod (a synced folder, a
    Windows filesystem with no POSIX bits).
    """

    # Windows synthesizes POSIX read bits as 0o666; ACL inspection needs a
    # platform-specific implementation, and recommending chmod there is wrong.
    if (platform.system() if system is None else system) == "Windows":
        return False
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH))


def load_credentials(
    *,
    environ: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> OnshapeCredentials:
    """Read the key pair, preferring real environment variables over the file.

    Raises ``OnshapeCredentialsError`` with instructions the user can act on,
    never a partial or placeholder credential.
    """

    env = os.environ if environ is None else environ
    path = credentials_path(home)
    from_file: dict[str, str] = {}
    if path.is_file():
        try:
            from_file = _parse_env_file(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise OnshapeCredentialsError(
                f"Could not read the Onshape key file at {path}: {exc}"
            ) from exc

    access = env.get(ACCESS_KEY_ENV) or from_file.get(ACCESS_KEY_ENV) or ""
    secret = env.get(SECRET_KEY_ENV) or from_file.get(SECRET_KEY_ENV) or ""
    base_url = (
        env.get(BASE_URL_ENV) or from_file.get(BASE_URL_ENV) or DEFAULT_BASE_URL
    ).rstrip("/")
    if not access.strip() or not secret.strip():
        raise OnshapeCredentialsError(
            "No Onshape API key pair was found. In Onshape, open My account > "
            "Developer > API keys, create one, and save it as two lines in "
            f"{path}:\n"
            f"  {ACCESS_KEY_ENV}=...\n"
            f"  {SECRET_KEY_ENV}=...\n"
            "Environment variables of the same names take precedence."
        )
    if not base_url.startswith("https://"):
        raise OnshapeCredentialsError(
            f"{BASE_URL_ENV} must be an https:// URL; refusing to send API keys "
            f"to {base_url!r}."
        )
    source = (
        "environment"
        if env.get(ACCESS_KEY_ENV) and env.get(SECRET_KEY_ENV)
        else str(path)
    )
    return OnshapeCredentials(
        access_key=access.strip(),
        secret_key=secret.strip(),
        base_url=base_url,
        source=source,
    )


__all__ = [
    "ACCESS_KEY_ENV",
    "BASE_URL_ENV",
    "DEFAULT_BASE_URL",
    "OnshapeCredentials",
    "OnshapeCredentialsError",
    "SECRET_KEY_ENV",
    "credentials_path",
    "file_is_world_readable",
    "load_credentials",
]
