"""Warn once about obsolete BEAT provider settings."""

from collections.abc import Mapping
import logging
import os

log = logging.getLogger(__name__)
_warned_values: set[str] = set()
PROVIDER_ENV = "WG2_BEAT_PROVIDER"


def warn_removed_provider(environ: Mapping[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    value = env.get(PROVIDER_ENV, "").strip().casefold()
    if not value or value == "official" or value in _warned_values:
        return
    _warned_values.add(value)
    if value in {"hbb", "legacy"}:
        log.warning("%s=%r: HBB was removed; using the official BEAT engine", PROVIDER_ENV, value)
    else:
        log.warning("Unknown %s=%r; using the default official provider", PROVIDER_ENV, value)
