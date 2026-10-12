"""Shared feature flags used by CAD admission and engine capability detection."""
from __future__ import annotations

from collections.abc import Mapping
import logging
import os
import threading

FEM_REGIONS_ENV = "WG2_FEM_REGIONS"
logger = logging.getLogger(__name__)
_warned_fem_values: set[str] = set()
_fem_warning_lock = threading.Lock()


def fem_regions_enabled(*, environ: Mapping[str, str] | None = None) -> bool:
    """Enable declared FEM regions unless the operator disables the feature."""
    env = os.environ if environ is None else environ
    value = env.get(FEM_REGIONS_ENV, "")
    normalized = value.strip().lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized not in {"", "1", "true", "yes", "on"}:
        with _fem_warning_lock:
            if value not in _warned_fem_values:
                _warned_fem_values.add(value)
                logger.warning("Unknown %s value %r; FEM stays enabled", FEM_REGIONS_ENV, value)
    return True
