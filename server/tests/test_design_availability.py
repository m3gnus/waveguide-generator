"""What a job's stored design says about being reopened."""

from __future__ import annotations

import pytest
import asyncio
from copy import deepcopy

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


@pytest.mark.parametrize('versioned', [False, True])
def test_lookup_snapshot_is_preserved_without_conversion(monkeypatch, versioned):
    import server.jobs.freeform_conversion as conversion
    def unexpected(*args, **kwargs):
        pytest.fail('History classification must not fit geometry')
    monkeypatch.setattr(conversion, 'convert_design', unexpected)
    wire = {'formula': 'LOOKUP', 'lookup_profile': [[0, 12.7], [100, 80]]}
    snapshot = {'version': 1, 'design': wire} if versioned else wire
    original = deepcopy(snapshot)
    resolved = resolve_job_design(snapshot)
    assert resolved.reopenable
    assert resolved.snapshot == original == snapshot
    assert resolved.note is None


def test_listing_200_lookup_runs_and_websocket_snapshot_do_no_conversion(tmp_path, monkeypatch):
    import server.jobs.freeform_conversion as conversion
    import server.design.migrate as migration
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore
    from server.tests.test_jobs_response_contract import _row
    def unexpected(*args, **kwargs):
        pytest.fail('Reading history must not import or fit geometry')
    monkeypatch.setattr(conversion, 'convert_design', unexpected)
    monkeypatch.setattr(migration, 'apply_migrations', unexpected)
    store = JobStore(tmp_path / 'jobs.db')
    store.initialize()
    snapshots = {}
    for index in range(200):
        row = _row()
        row['id'] = f'lookup-{index}'
        row['config_summary_json'] = {'formula_type': 'LOOKUP'}
        row['script_snapshot'] = {'version': 1, 'design': {'formula': 'LOOKUP',
            'lookup_profile': [[0, 12.7], [100, 80+index]]}}
        snapshots[row['id']] = deepcopy(row['script_snapshot'])
        store.create_job(row)
    runtime = JobRuntime(store)
    runtime._started = True
    async def read():
        listed, total = await runtime.list_jobs(status=None, limit=200, offset=0)
        assert total == len(listed) == 200
        snapshot = await runtime.snapshot_async()
        for item in [*listed, *snapshot['jobs']]:
            assert item['script_snapshot'] == snapshots[item['id']]
            assert item['design_availability']['reopenable']
    asyncio.run(read())
    for key, snapshot in snapshots.items():
        assert store.get_job_row(key)['script_snapshot'] == snapshot
