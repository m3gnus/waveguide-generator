"""The solve settings a Fusion Solve changes, applied to the model's own setup.

docs/architecture/CAD-OPERATIONS.md, "Project setups". WG owns every solve
setting. Fusion's request panel shows the sweep the model's last solve used
(the status record's ``settings``, :func:`sweep_settings`) and a Solve carries
only the fields the user changed there (``solveSettings``, checked by the
shared protocol's ``request_solve_settings``). The delivery's first
preparation applies them to the setup it would otherwise use and records the
result as the model's setup -- the user's own choice, exactly like an edit in
WG's CAD workspace -- so WG's settings show them from then on and Fusion keeps
no copy.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from typing import Any

from server.cadlink.intake.readings import snapshot_project
from server.cadlink.joblane.default_setup import solve_defaults, sweep_points
from server.cadlink.roles import inventory_sha256
from server.cadlink.store import UNFILED_MODEL_ID, CadLinkStore
from server.jobs.imported_setup import CadSolveSetup, setup_content, setup_digest, validate_setup

logger = logging.getLogger(__name__)

_SPACINGS = ("log", "linear")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def sweep_settings(options: Mapping[str, Any]) -> dict[str, Any]:
    """A setup's frequency sweep in the shared protocol's words; what it cannot state is left out.

    An explicit frequency list states its first and last frequency and its
    length, but no spacing.
    """

    found: dict[str, Any] = {}
    explicit = options.get("frequencies_hz")
    if isinstance(explicit, list) and explicit:
        values = [number for number in (_number(item) for item in explicit) if number is not None]
        if values:
            found.update(frequencyStartHz=min(values), frequencyEndHz=max(values),
                         frequencyCount=len(values))
        return found
    band = options.get("frequency_range")
    if isinstance(band, list) and len(band) == 2:
        start, end = _number(band[0]), _number(band[1])
        if start is not None and end is not None:
            found.update(frequencyStartHz=start, frequencyEndHz=end)
    count = options.get("num_frequencies")
    if isinstance(count, int) and not isinstance(count, bool):
        found["frequencyCount"] = count
    spacing = options.get("frequency_spacing", "log")
    if spacing in _SPACINGS:
        found["frequencySpacing"] = spacing
    return found


def apply_solve_settings(setup: CadSolveSetup, settings: Mapping[str, Any]) -> CadSolveSetup:
    """``setup`` with the sweep fields in ``settings`` (canonical) replacing its own.

    A field ``settings`` leaves out keeps the setup's value, else WG's default.
    A change to any sweep field replaces an explicit frequency list.
    The result is the user's own (no ``origin``). Raises ``ValueError`` when
    the combined sweep cannot be solved.
    """

    defaults = solve_defaults()["sweep"]
    current = sweep_settings(setup.options)
    start = settings.get("frequencyStartHz", current.get("frequencyStartHz", defaults["start_hz"]))
    end = settings.get("frequencyEndHz", current.get("frequencyEndHz", defaults["end_hz"]))
    if float(end) <= float(start):
        raise ValueError("the end frequency must be above the start frequency")
    count = settings.get("frequencyCount", current.get("frequencyCount"))
    if count is None:
        count = sweep_points(start, end, defaults["points_per_octave"])
    spacing = settings.get("frequencySpacing", current.get("frequencySpacing", defaults["spacing"]))
    options = dict(setup.options)
    options.pop("frequencies_hz", None)
    options.update(frequency_range=[float(start), float(end)], num_frequencies=int(count),
                   frequency_spacing=spacing)
    content = json.loads(setup_content(setup))
    content["options"] = options
    content.pop("origin", None)
    return validate_setup(content)


def record_settings_setup(
    store: CadLinkStore,
    manifest: Mapping[str, Any],
    revision_id: str,
    *,
    project: Mapping[str, Any] | None = None,
) -> bool:
    """Make ``revision_id`` the model's setup, as the user's choice; False when the
    snapshot names no model yet (a first-time model before its ingest files it,
    whose ``project`` record then names it)."""

    lineage_id = snapshot_project(store, manifest) or (
        str(project.get("lineage_id") or "").strip() if isinstance(project, Mapping) else ""
    )
    if not lineage_id or lineage_id == UNFILED_MODEL_ID:
        return False
    sources = [source for source in manifest.get("sources") or [] if isinstance(source, Mapping)]
    store.record_model_setup(lineage_id, inventory_sha256(sources), revision_id)
    return True


def settings_revision(
    store: CadLinkStore, setup: CadSolveSetup, revision_id: str, settings: Mapping[str, Any],
) -> tuple[CadSolveSetup, str]:
    """The setup and revision a Solve with ``settings`` prepares with (unchanged when they change nothing)."""

    updated = apply_solve_settings(setup, settings)
    if setup_digest(updated) == setup_digest(setup):
        return setup, revision_id
    revision = store.create_setup_revision(setup_content(updated), setup_digest(updated))
    logger.info("Applied the solve settings sent from Fusion: setup %s -> %s (%s).",
                revision_id, revision["revision_id"], dict(settings))
    return updated, str(revision["revision_id"])


def revision_sweep(store: CadLinkStore, revision_id: object) -> dict[str, Any] | None:
    """The sweep a recorded setup revision solves, for the request status; None when unknown."""

    if not isinstance(revision_id, str) or not revision_id:
        return None
    row = store.get_setup_revision(revision_id)
    if row is None:
        return None
    try:
        options = json.loads(row["setup_json"]).get("options")
    except (TypeError, ValueError, AttributeError):
        return None
    return sweep_settings(options) if isinstance(options, Mapping) else None


__all__ = [
    "apply_solve_settings",
    "record_settings_setup",
    "revision_sweep",
    "settings_revision",
    "sweep_settings",
]
