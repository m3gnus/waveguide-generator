"""Build-time feature flags for the CAD link.

Onshape is parked until the Fusion CAD Link path is stable (owner decision,
2026-09-29). The code stays; the flag decides whether it is reachable. There is
exactly one source of truth: :func:`onshape_enabled`. The server mounts the
Onshape routes from it and advertises it in ``/api/capabilities``; the frontend
reads only that advertisement.

**Off everywhere unless ``WG2_ENABLE_ONSHAPE=1``.** A git-checkout install is a
release channel too, so a source tree is not treated as a development build;
developers opt in explicitly (see ``docs/DEVELOPMENT.md``). Any other value,
including unset, means off.
"""

from __future__ import annotations

from collections.abc import Mapping
import os

ONSHAPE_ENV = "WG2_ENABLE_ONSHAPE"


def onshape_enabled(*, environ: Mapping[str, str] | None = None) -> bool:
    """Whether the Onshape adapter is reachable in this run."""

    env = os.environ if environ is None else environ
    return env.get(ONSHAPE_ENV, "").strip() == "1"


__all__ = ["ONSHAPE_ENV", "onshape_enabled"]
