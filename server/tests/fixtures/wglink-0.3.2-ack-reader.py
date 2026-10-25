"""Frozen evidence: how WGLink 0.3.2 (tag v0.3.2, 44aecee) reads WG's receipt.

Copied verbatim from that release's fusion-addins/WGLink/wglink_inbox.py --
the constants, ``_read_json``, ``wg_acknowledges`` and ``read_acknowledgement``
-- so WG's tests can prove a released add-in still reads what WG writes.
Never edit it to follow WG: it stands for add-ins already installed.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

CAPABILITIES_FILENAME = "wg-capabilities.json"
CAPABILITIES_SCHEMA_VERSION = 1
SOLVE_COMMAND_DELIVERY = "solveCommandDelivery"
# WG writes one acknowledgement file per request when it advertises this as an
# integer of at least 1: ``<ipc>/.wg-solve-acks/<requestId>.json``.
SOLVE_ACKNOWLEDGEMENT = "solveAcknowledgement"
ACK_DIRECTORY = ".wg-solve-acks"
ACK_SCHEMA_VERSION = 1
ACK_ACCEPTED = "accepted"
ACK_REFUSED = "refused"

_PLAIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def wg_acknowledges(ipc_folder: Path) -> bool:
    """Whether WG advertises that it writes an acknowledgement for each request."""

    payload = _read_json(Path(ipc_folder) / CAPABILITIES_FILENAME)
    if not isinstance(payload, Mapping):
        return False
    schema = payload.get("schemaVersion")
    if isinstance(schema, bool) or schema != CAPABILITIES_SCHEMA_VERSION:
        return False
    if SOLVE_COMMAND_DELIVERY not in payload:
        return False
    value = payload.get(SOLVE_ACKNOWLEDGEMENT)
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def read_acknowledgement(
    ipc_folder: Path,
    request_id: str,
    *,
    kind: str | None = None,
    manifest_sha256: str | None = None,
) -> tuple[str, str | None] | None:
    """WG's outcome for one request: ``(outcome, reason)``, or None for "not yet".

    Opens, reads and closes the file at once (a held handle delays WG's own
    replace on Windows). A missing folder or file, a parse failure, another
    schema version, an outcome this add-in does not know, ids that are not this
    request's, or a ``kind`` / ``manifestSha256`` that names another request
    all read as "not yet". Every other field is ignored.
    """

    if not _PLAIN_ID.fullmatch(str(request_id)):
        return None
    payload = _read_json(Path(ipc_folder) / ACK_DIRECTORY / f"{request_id}.json")
    if not isinstance(payload, Mapping):
        return None
    schema = payload.get("schemaVersion")
    if isinstance(schema, bool) or schema != ACK_SCHEMA_VERSION:
        return None
    for field in ("commandId", "operationId"):
        if payload.get(field, request_id) != request_id:
            return None
    if kind is not None and payload.get("kind", kind) != kind:
        return None
    if manifest_sha256 is not None and payload.get("manifestSha256", manifest_sha256) != manifest_sha256:
        return None
    outcome = payload.get("outcome")
    if outcome not in (ACK_ACCEPTED, ACK_REFUSED):
        return None
    reason = payload.get("reason")
    return outcome, reason if isinstance(reason, str) and reason.strip() else None




def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
