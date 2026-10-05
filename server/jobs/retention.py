"""Whether stored results are cleaned up automatically, and by how much.

Result payloads used to be pruned unconditionally: anything unrated older than
30 days, and anything past the newest 1,000 runs. That deleted reference runs a
user still needed, so it is now an opt-in preference, off by default.

The preference is the frontend's (``autoCleanupResults`` in the
``preferences`` settings namespace, ``frontend/src/prefs/preferences.ts``). The
settings store keeps that namespace opaque -- it is the exact string the
browser serialised, ``{"version": N, "preferences": {...}}`` -- so this module
reads the one field it needs and nothing else. Anything it cannot read as an
explicit ``true`` means off: a missing file, a profile written before the
setting existed, a corrupt value, or a shape from another version. Keeping
results is the answer that cannot lose anyone's data.

Only the age and count pruning of results depends on it. Transient artifacts
-- solve meshes past their grace period, pressure bases and traces of failed
runs, artifacts orphaned by a run whose results are already gone -- are
cleaned up either way; they are not results a user can open.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
from typing import Any, Protocol


logger = logging.getLogger(__name__)

#: Settings namespace and field the frontend writes the preference under.
PREFERENCES_NAMESPACE = "preferences"
AUTO_CLEANUP_RESULTS_KEY = "autoCleanupResults"

#: The cleanup applied when the preference is on. Rated runs are exempt from
#: both, and do not count toward the cap.
RESULT_RETENTION_DAYS = 30
MAX_RETAINED_RESULTS = 1000


class _SettingsReader(Protocol):
    def get(self, namespace: str) -> Any | None: ...


def auto_cleanup_enabled(stored: Any) -> bool:
    """Read the stored ``preferences`` namespace; only an explicit ``true`` is on."""

    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except (TypeError, ValueError):
            return False
    if not isinstance(stored, dict):
        return False
    preferences = stored.get("preferences")
    if not isinstance(preferences, dict):
        return False
    return preferences.get(AUTO_CLEANUP_RESULTS_KEY) is True


def settings_auto_cleanup_policy(settings: _SettingsReader) -> Callable[[], bool]:
    """A policy that re-reads the preference each time it is asked.

    The jobs runtime asks at startup -- before any client has connected, so
    this is the persisted value -- and again after every job, so a change in
    the preferences takes effect without a restart.
    """

    def enabled() -> bool:
        try:
            stored = settings.get(PREFERENCES_NAMESPACE)
        except Exception:  # noqa: BLE001 - an unreadable setting means keep results
            logger.warning(
                "Could not read the automatic result cleanup preference; keeping results",
                exc_info=True,
            )
            return False
        return auto_cleanup_enabled(stored)

    return enabled


__all__ = [
    "AUTO_CLEANUP_RESULTS_KEY",
    "MAX_RETAINED_RESULTS",
    "PREFERENCES_NAMESPACE",
    "RESULT_RETENTION_DAYS",
    "auto_cleanup_enabled",
    "settings_auto_cleanup_policy",
]
