"""Default-off selection for additive official BEAT lifecycle hooks."""

from __future__ import annotations

from collections.abc import Mapping
import os


def official_provider_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Only the explicit official value opts into WG's runtime."""
    return (os.environ if environ is None else environ).get("WG2_BEAT_PROVIDER") == "official"
