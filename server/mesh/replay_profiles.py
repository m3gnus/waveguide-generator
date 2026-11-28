"""How each symmetry-rules version built an imported mesh: the one place that knows.

A record names the rules it was built under (``symmetry["symmetry_rules"]``;
none before the shared check). A portable replay of an exported package
(``server/jobs/mesh_replay.py``) must rebuild that package's mesh byte for
byte, so it rebuilds under the package's rules. Everything an earlier
version did differently lives in its :class:`ReplayProfile` here; the live
build reads :data:`LIVE` and has no version branches of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

#: Exported before the shared reduced-mesh check (no ``symmetry_rules``):
#: declared planes stay among the cut candidates, one fallback to the whole
#: model, every mirror sample projected by OCC, the face-by-face cut.
LEGACY_SYMMETRY_RULES = "pre-shared-check"
#: Stage 4: the shared check and per-plane degrade, every mirror sample
#: projected by OCC (``fast_check=False``), the face-by-face cut.
STAGE4_SYMMETRY_RULES = "shared-check-per-plane-v1"
#: Stage 4b: the mesher's fast mirror check and its one-sided small-feature
#: check, the face-by-face cut (``solid_cut=False``).
FACE_CUT_SYMMETRY_RULES = "shared-check-per-plane-v2"
#: As v2, a model of solids cut as its solids (renumbered faces).
SOLID_CUT_SYMMETRY_RULES = "shared-check-per-plane-v3"


@dataclass(frozen=True)
class ReplayProfile:
    """What one rules version did. ``rules`` is what the record carries
    (``None``: nothing, as before the shared check)."""

    rules: str | None
    #: Pre-shared-check: declared planes stay cut candidates and a failed
    #: reduced domain falls back once, to the whole model.
    legacy: bool
    #: Keyword arguments for ``hornlab_mesher.auto_cut_occ_geometry``.
    auto_cut_options: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: A replay tests every plane itself and neither reads nor writes the
    #: verdict store.
    replay: bool = True


#: What this build does.
LIVE = ReplayProfile(rules=SOLID_CUT_SYMMETRY_RULES, legacy=False,
                     auto_cut_options=MappingProxyType({"solid_cut": True}), replay=False)

_EARLIER: Mapping[str, ReplayProfile] = MappingProxyType({
    LEGACY_SYMMETRY_RULES: ReplayProfile(
        rules=None, legacy=True, auto_cut_options=MappingProxyType({"fast_check": False})),
    STAGE4_SYMMETRY_RULES: ReplayProfile(
        rules=STAGE4_SYMMETRY_RULES, legacy=False,
        auto_cut_options=MappingProxyType({"fast_check": False})),
    FACE_CUT_SYMMETRY_RULES: ReplayProfile(
        rules=FACE_CUT_SYMMETRY_RULES, legacy=False,
        auto_cut_options=MappingProxyType({"solid_cut": False})),
})


def replay_symmetry_rules() -> frozenset[str]:
    """The earlier rules a portable replay may ask to rebuild under."""
    return frozenset(_EARLIER)


def profile_for(replay_rules: str | None) -> ReplayProfile:
    """The profile a build runs under: :data:`LIVE` without replay rules.
    Raises ``ValueError`` for rules no build ever wrote."""
    if replay_rules is None:
        return LIVE
    try:
        return _EARLIER[replay_rules]
    except KeyError:
        raise ValueError(f"unknown replay symmetry rules {replay_rules!r}") from None


def replay_rules_for_export(exported_rules: str | None) -> str | None:
    """The replay rules to rebuild a package exported under
    ``exported_rules`` with; ``None`` when this build's rules are its rules."""
    if exported_rules is None:
        return LEGACY_SYMMETRY_RULES
    return exported_rules if exported_rules in _EARLIER else None
