"""WGLink with its automatic coordination off: a quiet heartbeat is not an offline add-in.

The add-in (``automatic_coordination: false``) publishes status at start-up,
after each command and on removal at shutdown -- never on a clock. Between
commands its heartbeat ages past ``FUSION_STATUS_TTL`` while Fusion and the
add-in are both fine. WG must then say it has not observed Fusion since that
report, not "add-in offline" or "Fusion closed", and must not claim the report
still describes the document. The controls: the same quiet heartbeat from an
add-in that does coordinate is offline, and so is nothing once Fusion is closed.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest

from server.cadlink.fusion_status import FUSION_STATUS_TTL
from server.tests.test_fusion_status import NOW, _link, _read, _write_status

QUIET = NOW - FUSION_STATUS_TTL - timedelta(minutes=5)


def _with_activation(marker: Path, coordinating: bool | None) -> None:
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if coordinating is not None:
        payload["diagnostics"] = {"activation": {"automaticCoordination": coordinating, "setting": "settings"}}
    marker.write_text(json.dumps(payload), encoding="utf-8")


def test_a_quiet_command_driven_add_in_is_reported_as_not_observed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _with_activation(_write_status(workspace, links=[_link()], updated_at=QUIET), coordinating=False)

    status = _read(workspace, process_running=True)

    assert status["state"] not in {"addin_offline", "closed", "addin_outdated"}
    assert status["running"] is True
    assert status["statusObserved"] is False
    assert status["observedAt"] == QUIET.isoformat().replace("+00:00", "Z")
    # The last report's link is kept, so Send keeps its expected-document guard...
    assert status["link"]["instanceId"] == "instance-a"
    # ...but nothing it says is claimed for now.
    assert status["state"] != "current"
    assert status["observationFreshness"] == "stale"
    assert status["documentChangeDetectable"] is False
    assert "automatic coordination is off" in status["staleDetectionExplanation"]


def test_the_same_report_while_fresh_is_an_ordinary_current_status(tmp_path: Path) -> None:
    """Positive control on the classification: fresh, it is simply current."""

    workspace = tmp_path / "workspace"
    _with_activation(_write_status(workspace, links=[_link()]), coordinating=False)
    status = _read(workspace, process_running=True)
    assert status["state"] == "current"
    assert "statusObserved" not in status


@pytest.mark.parametrize("coordinating", [True, None], ids=["coordinating", "older-addin"])
def test_a_quiet_add_in_that_coordinates_is_still_offline(tmp_path: Path, coordinating: bool | None) -> None:
    workspace = tmp_path / "workspace"
    _with_activation(_write_status(workspace, links=[_link()], updated_at=QUIET), coordinating=coordinating)
    status = _read(workspace, process_running=True)
    assert status["state"] == "addin_offline"
    assert "statusObserved" not in status


def test_a_quiet_report_with_fusion_closed_is_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _with_activation(_write_status(workspace, links=[_link()], updated_at=QUIET), coordinating=False)
    assert _read(workspace, process_running=False)["state"] == "closed"


def test_a_report_from_the_future_is_never_trusted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _with_activation(
        _write_status(workspace, links=[_link()], updated_at=NOW + timedelta(hours=1)), coordinating=False
    )
    assert _read(workspace, process_running=True)["state"] == "addin_offline"
