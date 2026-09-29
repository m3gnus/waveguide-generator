"""Build-time feature flags for the CAD link.

Onshape is parked behind a flag until the Fusion CAD Link path is stable
(owner decision, 2026-09-29). The code stays; the flag decides whether it is
reachable. There is exactly one source of truth: :func:`onshape_enabled`. The
server mounts the Onshape routes from it and advertises it in
``/api/capabilities``; the frontend reads only that advertisement.

Default, with no override: **off in a packaged build, on in a source checkout.**
A packaged build is recognised by the ``APP-MANIFEST.json`` that
``scripts/build_bundle.py`` writes into every app layer and no source checkout
has -- the same marker the updater and the build identity already use, so the
release build needs no separate switch to remember.

``WG_ENABLE_ONSHAPE`` overrides either way (``1``/``true``/``on`` or
``0``/``false``/``off``); an unrecognised value is ignored rather than guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

from server.platform.paths import app_root

ONSHAPE_ENV = "WG_ENABLE_ONSHAPE"
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def is_packaged_build(root: Path | None = None) -> bool:
    """True when the app layer carries the manifest only a built bundle has."""

    return ((root if root is not None else app_root()) / "APP-MANIFEST.json").is_file()


def onshape_enabled(
    *, environ: Mapping[str, str] | None = None, root: Path | None = None
) -> bool:
    """Whether the Onshape adapter is reachable in this run."""

    env = os.environ if environ is None else environ
    override = env.get(ONSHAPE_ENV, "").strip().lower()
    if override in _TRUE:
        return True
    if override in _FALSE:
        return False
    return not is_packaged_build(root)


__all__ = ["ONSHAPE_ENV", "is_packaged_build", "onshape_enabled"]
