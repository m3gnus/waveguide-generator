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

import logging

from server.cadlink.wglink_protocol import blocking, finding_text, manifest_draft, preflight_findings
from server.platform.incidents import record_incident

logger = logging.getLogger(__name__)


def blocking_findings(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The ``block`` findings of a delivered, structurally valid manifest."""

    return blocking(preflight_findings(manifest_draft(manifest)))


def admission_refusal(manifest: Mapping[str, Any]) -> str | None:
    """The refusal a delivered return is owed, in the rules' words; None to admit it."""

    findings = blocking_findings(manifest)
    return " ".join(finding_text(finding) for finding in findings) if findings else None


def judged_refusal(manifest: Mapping[str, Any], *, operation_id: str, job_id: str | None = None) -> str | None:
    """:func:`admission_refusal`, never raising: the one judgement of a delivered return.

    Admission (``joblane.preparation.refuse_blocked_return``) and, for a
    delivery admitted before WG held its return, the first preparation
    (``jobs.cad_preparation``) both judge through this. A failure of the
    rules themselves is WG's own: recorded for the developers, and the return
    is admitted (None).
    """

    try:
        return admission_refusal(manifest)
    except Exception as exc:  # noqa: BLE001 - a rule failure never refuses a model
        record_incident("preflight-rules-failed", "cad-admission",
                        ids={"operation_id": operation_id, **({"job_id": job_id} if job_id else {})},
                        exception=exc)
        logger.warning("The preflight rules failed on CAD operation %s; admitted.", operation_id,
                       exc_info=True)
        return None
