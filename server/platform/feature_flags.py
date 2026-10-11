"""Shared opt-in flags used by CAD admission and engine capability detection."""
from __future__ import annotations

from collections.abc import Mapping
import os

FEM_REGIONS_ENV = "WG2_FEM_REGIONS"


def fem_regions_enabled(*, environ: Mapping[str, str] | None = None) -> bool:
    """Opt in to declared FEM region intake and the official CPU coupled route."""
    env = os.environ if environ is None else environ
    return env.get(FEM_REGIONS_ENV, "").strip() == "1"
