"""How far a running solve has got through its frequencies. One owner.

Every full-3D engine (``server/solver/beat.py``, ``metal.py``, ``bempp.py``,
``bempp_imported.py``, ``beat_imported.py``, ``beat_transducer_imported.py``,
``official_beat.py``) reports its own per-frequency stage message: "Solving
frequency i/N …", "Solved frequency i/N", or "Solved i of N frequencies".
Reading the count back out of that text is deliberate: it is the one place the
count and the index already exist together, so a parallel numeric field on every
engine adapter would duplicate data the message already carries.

The printed number is the completed count, for every engine: each adapter's
stage callback fires after a frequency finishes. The imported adapters report
one drive channel at a time ("… of drive channel c/C (id) …"); that count is
channel-local and can restart within a channel, so overall work comes from the
job's ``progress`` field, never from it.

The runtime keeps the last count through a solve's setup chatter
(:func:`advance_frequency_progress`) and stores it on the job, so the job read
model, the jobs socket and a Fusion request's status file all read the same
value (:func:`job_frequency_progress`). Nothing else parses the message.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

#: The public stage whose messages carry a frequency count.
FREQUENCY_STAGE = "solve"

#: Running stages that keep the last count. BEAT reports ``assemble`` (setup)
#: and count-less ``solve`` lines between sweep checkpoints, and radiation
#: impedance follows the sweep under the same "Solving" word; none of them may
#: rewind the count. Every other stage ends it.
_KEEP_STAGES = frozenset({"assemble", FREQUENCY_STAGE, "radiation_impedance"})

#: Where the runtime stores the count, in ``task_metadata``.
METADATA_KEY = "frequency_progress"

_CHANNEL = re.compile(r"(\d+)\s*/\s*(\d+)\s*of\s*drive\s*channel\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
_OF_FREQUENCIES = re.compile(r"(\d+)\s+of\s+(\d+)\s+frequenc", re.IGNORECASE)
_PLAIN = re.compile(r"(\d+)\s*/\s*(\d+)")


def frequency_progress(stage_message: str | None) -> dict[str, Any] | None:
    """The frequency count one solve-stage message reports, or None.

    ``{"done", "total"}``, plus ``"channel": {"index", "count"}`` for an
    imported multi-channel solve. A message with no count (a batched engine,
    a status line) is None: the signal for an indeterminate display.
    """

    if not stage_message:
        return None
    channel = _CHANNEL.search(stage_message)
    if channel:
        done, total, index, count = (int(group) for group in channel.groups())
        if total <= 0 or count <= 0:
            return None
        return {"done": min(done, total), "total": total, "channel": {"index": index, "count": count}}
    match = _OF_FREQUENCIES.search(stage_message) or _PLAIN.search(stage_message)
    if not match:
        return None
    done, total = int(match.group(1)), int(match.group(2))
    if total <= 0:
        return None
    return {"done": min(done, total), "total": total}


def advance_frequency_progress(
    previous: Mapping[str, Any] | None, stage: str | None, stage_message: str | None,
) -> dict[str, Any] | None:
    """The count to keep after one running stage update.

    A solve-stage message with a count replaces it; setup chatter, a
    count-less solve line and radiation impedance keep the last one; any other
    stage clears it.
    """

    if stage == FREQUENCY_STAGE:
        parsed = frequency_progress(stage_message)
        if parsed is not None:
            return parsed
    if stage in _KEEP_STAGES and previous is not None:
        return dict(previous)
    return None


def _count(value: object, minimum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def job_frequency_progress(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The frequency count of a job row, as every reader sees it.

    Only a running job in a stage that keeps a count has one; a stored value
    from an earlier stage or run never shows.
    """

    if row.get("status") != "running" or row.get("stage") not in _KEEP_STAGES:
        return None
    metadata = row.get("task_metadata")
    stored = metadata.get(METADATA_KEY) if isinstance(metadata, Mapping) else None
    if not isinstance(stored, Mapping):
        return None
    done, total = _count(stored.get("done"), 0), _count(stored.get("total"), 1)
    if done is None or total is None or done > total:
        return None
    value: dict[str, Any] = {"done": done, "total": total}
    channel = stored.get("channel")
    if isinstance(channel, Mapping):
        index, count = _count(channel.get("index"), 1), _count(channel.get("count"), 1)
        if index is not None and count is not None and index <= count:
            value["channel"] = {"index": index, "count": count}
    return value


__all__ = [
    "FREQUENCY_STAGE",
    "METADATA_KEY",
    "advance_frequency_progress",
    "frequency_progress",
    "job_frequency_progress",
]
