"""Stable identity for a relocatable Waveguide Generator runtime layer."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_runtime_id(
    runtime_requirements: bytes,
    pinned_requirements: bytes,
    python_version: str,
) -> str:
    """Hash the exact two requirement files and Python version, without separators."""

    digest = hashlib.sha256()
    digest.update(runtime_requirements)
    digest.update(pinned_requirements)
    digest.update(python_version.encode("utf-8"))
    return digest.hexdigest()[:12]


def runtime_id_from_files(
    runtime_requirements: Path,
    pinned_requirements: Path,
    python_version: str,
) -> str:
    """Compute a runtime id from the two release requirement files."""

    return compute_runtime_id(
        runtime_requirements.read_bytes(),
        pinned_requirements.read_bytes(),
        python_version,
    )
