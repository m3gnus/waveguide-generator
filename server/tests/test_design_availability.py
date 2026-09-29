"""What a job's stored design says about being reopened."""

from __future__ import annotations

import pytest

from server.jobs.design_availability import resolve_job_design


@pytest.mark.parametrize(
    "snapshot",
    [
        {"version": 1, "design": {"formula": "OSSE"}},
        {"formula": "OSSE", "L": 120},
    ],
)
def test_v2_snapshot_shapes_are_reopenable(snapshot: dict) -> None:
    resolution = resolve_job_design(snapshot)
    assert resolution.reopenable is True
    assert (resolution.source, resolution.reason_code) == ("v2-snapshot", "ok")
    assert resolution.snapshot == snapshot


@pytest.mark.parametrize("snapshot", [None, {}])
def test_absent_snapshot_says_no_design_was_stored(snapshot: object) -> None:
    resolution = resolve_job_design(snapshot)  # type: ignore[arg-type]
    assert resolution.reopenable is False
    assert resolution.reason_code == "no_stored_design"
    assert resolution.reason


def test_unrecognised_snapshot_is_refused_with_a_reason() -> None:
    resolution = resolve_job_design({"params": {"type": "OSSE"}})
    assert resolution.reopenable is False
    assert resolution.reason_code == "unreadable_design"
    assert resolution.snapshot is None
    assert resolution.reason
