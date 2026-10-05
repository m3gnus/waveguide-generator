"""Default-off selection for WG-owned BEAT readiness and provisioning."""

from __future__ import annotations

from collections.abc import Mapping
import os
import logging

log = logging.getLogger(__name__)
_warned_values: set[str] = set()

PROVIDER_ENV = "WG2_BEAT_PROVIDER"


def official_selected(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    value = env.get(PROVIDER_ENV, "").strip()
    if value and value != "official" and value not in _warned_values:
        _warned_values.add(value)
        log.warning("Unknown %s=%r; using the default HBB provider", PROVIDER_ENV, value)
    return value == "official"


# One selector for every hook: lifecycle callers (warm-up, Quit, qualifier)
# use this name, readiness and provisioning use official_selected.
official_provider_enabled = official_selected
