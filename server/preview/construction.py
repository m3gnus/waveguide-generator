"""Transient native construction handoff before consumer density changes."""

from __future__ import annotations

from typing import Any, Mapping


def retain_mesher_construction(config: Mapping[str, Any]) -> dict[str, Any]:
    """Retain the authored ATH wall through the native construction owner.

    Use only on execution configs, never on saved designs or geometry stamps.
    The old 866f pin has no handoff and keeps its existing behavior until the
    coupled native pin lands. Other geometry interpretations stay unchanged.
    """

    if config.get("_textImportVersion") != "ath-2026-09a-v2":
        return dict(config)
    from hornlab_mesher import config_builder

    retain = getattr(config_builder, "retain_ath_import_construction", None)
    if retain is None:
        return dict(config)
    # A planner may receive a config already frozen by export preparation.
    # Do not rebuild its authored construction from the overridden counts.
    from hornlab_mesher.profile_sampling import ACOUSTIC_MORPH_ANGLE_MAP_KEY

    if ACOUSTIC_MORPH_ANGLE_MAP_KEY in config:
        return dict(config)
    return retain(config)
