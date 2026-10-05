"""Default-off selection for WG-owned BEAT readiness and provisioning."""

from __future__ import annotations

from collections.abc import Mapping
import os

PROVIDER_ENV = "WG2_BEAT_PROVIDER"


def official_selected(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(PROVIDER_ENV, "").strip().lower() == "official"
