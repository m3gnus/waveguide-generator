"""Stable identity for a relocatable Waveguide Generator runtime layer."""

from __future__ import annotations

import hashlib
from pathlib import Path


def _update_field(digest: hashlib._Hash, name: str, value: bytes) -> None:
    """Add one unambiguous, length-delimited field to a runtime identity."""

    encoded_name = name.encode("ascii")
    digest.update(len(encoded_name).to_bytes(4, "big"))
    digest.update(encoded_name)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def compute_runtime_id(
    runtime_requirements: bytes,
    pinned_requirements: bytes,
    locked_requirements: bytes,
    python_version: str,
    python_build: str,
    runtime_recipe: str,
) -> str:
    """Hash every platform-neutral input that defines runtime compatibility."""

    digest = hashlib.sha256()
    fields = (
        ("identity-schema", b"wg2-runtime-id-v2"),
        ("runtime-requirements", runtime_requirements),
        ("pinned-requirements", pinned_requirements),
        ("locked-requirements", locked_requirements),
        ("python-version", python_version.encode("utf-8")),
        ("python-build", python_build.encode("utf-8")),
        ("runtime-recipe", runtime_recipe.encode("utf-8")),
    )
    for name, value in fields:
        _update_field(digest, name, value)
    # Installed clients currently validate and compare 12-hex compatibility
    # identifiers. Archive sidecars carry the full content digest.
    return digest.hexdigest()[:12]


def runtime_id_from_files(
    runtime_requirements: Path,
    pinned_requirements: Path,
    locked_requirements: Path,
    python_version: str,
    python_build: str,
    runtime_recipe: str,
) -> str:
    """Compute a runtime id from all three release requirement files."""

    return compute_runtime_id(
        runtime_requirements.read_bytes(),
        pinned_requirements.read_bytes(),
        locked_requirements.read_bytes(),
        python_version,
        python_build,
        runtime_recipe,
    )
