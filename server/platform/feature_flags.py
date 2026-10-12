"""Shared feature flags used by CAD admission and engine capability detection."""
from __future__ import annotations

from collections.abc import Mapping
import os

FEM_REGIONS_ENV = "WG2_FEM_REGIONS"


def fem_regions_enabled(*, environ: Mapping[str, str] | None = None) -> bool:
    """Enable declared FEM regions unless the operator disables the feature."""
    env = os.environ if environ is None else environ
    return env.get(FEM_REGIONS_ENV, "").strip().lower() not in {"0", "false", "no", "off"}
