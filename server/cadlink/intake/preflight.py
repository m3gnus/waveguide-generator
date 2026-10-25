"""WG's ingress half of the shared preflight rules.

The rules and their words are the vendored protocol's
(``wglink_protocol.preflight_findings``), which Fusion's Send/Solve dialog and
its Sources & Mesh dialog call too; nothing here restates one. Fusion keeps
Send and Solve disabled on a ``block`` finding, so WG meets one only from an
older or another writer, and refuses that return at admission
(``operations.PREFLIGHT_BLOCKED``) in the same words rather than failing it
later in preparation. ``ask`` and ``note`` findings never stop WG: an
unanswered question is asked by WG's own hold, as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.cadlink.wglink_protocol import blocking, finding_text, manifest_draft, preflight_findings


def blocking_findings(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The ``block`` findings of a delivered, structurally valid manifest."""

    return blocking(preflight_findings(manifest_draft(manifest)))


def admission_refusal(manifest: Mapping[str, Any]) -> str | None:
    """The refusal a delivered return is owed, in the rules' words; None to admit it."""

    findings = blocking_findings(manifest)
    return " ".join(finding_text(finding) for finding in findings) if findings else None
