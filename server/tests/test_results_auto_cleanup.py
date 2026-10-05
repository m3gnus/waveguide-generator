"""Automatic result cleanup is a preference, off by default.

The server used to prune stored results unconditionally -- unrated runs older
than 30 days, and anything past the newest 1,000 -- and a user lost reference
runs that way. These pin the switch: the frontend's ``autoCleanupResults``
preference, read from the persisted settings at startup and after every job,
where anything but an explicit ``true`` keeps every result.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from server.jobs.retention import (
    MAX_RETAINED_RESULTS,
    RESULT_RETENTION_DAYS,
    auto_cleanup_enabled,
    settings_auto_cleanup_policy,
)
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.settings.store import SettingsStore


OLD = "2000-01-01T00:00:00"


def _profile(preferences: dict[str, Any], version: int = 14) -> str:
    """The exact string the frontend persists in the ``preferences`` namespace."""

    return json.dumps({"version": version, "preferences": preferences})


def _job(job_id: str, status: str, *, at: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "status": status,
        "created_at": at,
        "updated_at": at,
        "queued_at": at,
        "completed_at": at if status in {"complete", "error", "cancelled"} else None,
        "progress": 1.0 if status == "complete" else 0.0,
        "stage": status,
        "stage_message": status,
        "config_json": {"design": {"formula": "OSSE", "L": 120}},
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }


def _seed(store: JobStore) -> None:
    """One run of every kind the prune distinguishes."""

    store.initialize()
    store.create_job(_job("aged", "complete", at=OLD))
    store.store_results("aged", {"frequencies": [1000.0]})
    store.store_radiation_impedance("aged", b"matrix-npz")
    log_path = store._job_log_path("aged")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("log-aged\n", encoding="utf-8")
    recent = datetime.now().isoformat()
    for job_id in ("recent-1", "recent-2"):
        store.create_job(_job(job_id, "complete", at=recent))
        store.store_results(job_id, {"frequencies": [1000.0]})
    # Transient artifacts, not results: a failed run's pressure basis.
    store.create_job(_job("failed", "error", at=recent))
    store.store_channel_bases("failed", b"bases-failed")


def _has_results(store: JobStore, job_id: str) -> bool:
    return store.get_results(job_id) is not None


# ---- reading the preference ---------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "not json",
        "[]",
        json.dumps({"version": 14}),
        json.dumps({"version": 14, "preferences": "nope"}),
        _profile({}),
        _profile({"autoCleanupResults": False}),
        _profile({"autoCleanupResults": "true"}),
        _profile({"autoCleanupResults": 1}),
        42,
        ["autoCleanupResults"],
    ],
)
def test_anything_but_an_explicit_true_keeps_results(stored: Any) -> None:
    assert auto_cleanup_enabled(stored) is False


def test_an_explicit_true_turns_cleanup_on_in_either_stored_shape() -> None:
    assert auto_cleanup_enabled(_profile({"autoCleanupResults": True})) is True
    # The settings store keeps the frontend's string; a decoded object reads too.
    assert auto_cleanup_enabled({"version": 14, "preferences": {"autoCleanupResults": True}})


def test_the_policy_rereads_the_store_each_time(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path)
    enabled = settings_auto_cleanup_policy(settings)
    assert enabled() is False  # no settings file at all
    settings.put("preferences", _profile({"autoCleanupResults": True}))
    assert enabled() is True
    settings.put("preferences", _profile({"autoCleanupResults": False}))
    assert enabled() is False
    settings.delete("preferences")
    assert enabled() is False


def test_an_unreadable_store_keeps_results() -> None:
    class Broken:
        def get(self, _namespace: str) -> Any:
            raise OSError("settings unreadable")

    assert settings_auto_cleanup_policy(Broken())() is False


def test_a_corrupt_settings_file_keeps_results(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path)
    settings.settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings.settings_path.write_text("{ not json", encoding="utf-8")
    assert settings_auto_cleanup_policy(settings)() is False


# ---- the store ----------------------------------------------------------------


def test_store_keeps_every_result_when_pruning_results_is_off(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)

    removed = store.prune_terminal_jobs(
        retention_days=RESULT_RETENTION_DAYS, max_terminal_jobs=1, prune_results=False
    )

    assert removed == 0
    for job_id in ("aged", "recent-1", "recent-2"):
        assert _has_results(store, job_id), job_id
        assert "results_discarded_at" not in store.get_job_row(job_id)["task_metadata"]
    # A radiation matrix is a result too; it ages out only with them.
    assert store.get_radiation_impedance("aged") == b"matrix-npz"
    assert store._job_log_path("aged").read_text(encoding="utf-8") == "log-aged\n"
    # Transient artifacts are still cleaned up.
    assert store.get_channel_bases("failed") is None


def test_store_prunes_by_age_and_count_when_pruning_results_is_on(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)

    removed = store.prune_terminal_jobs(
        retention_days=RESULT_RETENTION_DAYS, max_terminal_jobs=1, prune_results=True
    )

    assert removed == 2  # "aged" by age, the older recent run by count
    assert not _has_results(store, "aged")
    assert store.get_radiation_impedance("aged") is None
    assert [_has_results(store, job_id) for job_id in ("recent-1", "recent-2")].count(True) == 1


def test_live_prune_with_results_off_announces_no_result_removal(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)

    affected, events = store.prune_terminal_jobs_with_events(
        retention_days=RESULT_RETENTION_DAYS,
        max_terminal_jobs=MAX_RETAINED_RESULTS,
        prune_results=False,
    )

    assert "aged" not in affected
    assert all("has_results" not in event["payload"]["changed"] for event in events)


def test_mesh_grace_pruning_does_not_depend_on_the_preference(tmp_path: Path) -> None:
    """A solve mesh is a transient artifact, not a result: it keeps its grace rule."""

    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    record = _job("meshed", "running", at=OLD)
    store.create_job(record)
    store.store_mesh_artifact("meshed", "mesh-meshed")
    store.store_results("meshed", {"frequencies": [1000.0]})
    store.update_job("meshed", status="complete", completed_at=OLD)

    store.prune_terminal_jobs(prune_results=False)

    assert store.get_job_row("meshed")["has_mesh_artifact"] is False
    assert _has_results(store, "meshed")


# ---- the runtime: startup -----------------------------------------------------


def _start_and_stop(runtime: JobRuntime) -> JobStore:
    """Start and shut down; return a fresh store, since shutdown closes the old one."""

    async def scenario() -> None:
        await runtime.start()
        await runtime.shutdown()

    asyncio.run(scenario())
    return JobStore(runtime.store.db_path)


def test_startup_keeps_results_when_the_runtime_has_no_preference(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)

    store = _start_and_stop(JobRuntime(store))

    assert _has_results(store, "aged")


def test_startup_keeps_results_when_no_preference_was_ever_saved(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)
    settings = SettingsStore(tmp_path / "data")

    store = _start_and_stop(
        JobRuntime(store, results_auto_cleanup=settings_auto_cleanup_policy(settings))
    )

    assert _has_results(store, "aged")


def test_startup_keeps_results_for_a_profile_written_before_the_setting(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)
    SettingsStore(tmp_path / "data").put(
        "preferences", _profile({"archiveRunsOnComplete": True}, version=13)
    )
    # A fresh instance: startup reads the persisted file, not a cached value.
    settings = SettingsStore(tmp_path / "data")

    store = _start_and_stop(
        JobRuntime(store, results_auto_cleanup=settings_auto_cleanup_policy(settings))
    )

    assert _has_results(store, "aged")


def test_startup_prunes_when_the_persisted_preference_is_on(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)
    SettingsStore(tmp_path / "data").put(
        "preferences", _profile({"autoCleanupResults": True})
    )
    settings = SettingsStore(tmp_path / "data")

    store = _start_and_stop(
        JobRuntime(store, results_auto_cleanup=settings_auto_cleanup_policy(settings))
    )

    assert not _has_results(store, "aged")
    assert _has_results(store, "recent-1") and _has_results(store, "recent-2")


def test_startup_keeps_results_when_the_policy_itself_fails(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)

    def broken() -> bool:
        raise RuntimeError("policy failed")

    store = _start_and_stop(JobRuntime(store, results_auto_cleanup=broken))

    assert _has_results(store, "aged")


# ---- the runtime: after a job -------------------------------------------------


async def _drain_one_job(
    store: JobStore, runtime: JobRuntime, job_id: str
) -> list[dict[str, Any]]:
    """Run the post-job path for one queued job and return what it published."""

    store.create_job(_job(job_id, "queued", at=datetime.now().isoformat()))
    runtime._started = True
    runtime._queue.append(job_id)
    subscriber = runtime.events.subscribe()

    async def finish(_job_id: str, _row: Any) -> None:
        return None

    runtime._run_job = finish  # type: ignore[method-assign]
    try:
        await runtime._drain_scheduler()
    finally:
        runtime.events.unsubscribe(subscriber)
    published = []
    while not subscriber.empty():
        published.append(subscriber.get_nowait())
    return published


def _announces_result_removal(published: list[dict[str, Any]], job_id: str) -> bool:
    return any(
        event.get("jobId") == job_id
        and event.get("payload", {}).get("changed", {}).get("has_results") is False
        for event in published
    )


def test_post_job_prune_keeps_results_when_cleanup_is_off(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)
    settings = SettingsStore(tmp_path / "data")
    runtime = JobRuntime(store, results_auto_cleanup=settings_auto_cleanup_policy(settings))

    published = asyncio.run(_drain_one_job(store, runtime, "next"))

    assert _has_results(store, "aged")
    assert not _announces_result_removal(published, "aged")
    # The transient cleanup still runs after a job.
    assert store.get_channel_bases("failed") is None


def test_post_job_prune_follows_a_preference_changed_while_running(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed(store)
    settings = SettingsStore(tmp_path / "data")
    runtime = JobRuntime(store, results_auto_cleanup=settings_auto_cleanup_policy(settings))

    async def scenario() -> None:
        published = await _drain_one_job(store, runtime, "first")
        assert _has_results(store, "aged")
        assert not _announces_result_removal(published, "aged")

        # Turned on in the preferences while WG runs: the next job's prune
        # uses it, with no restart, and tells connected clients.
        settings.put("preferences", _profile({"autoCleanupResults": True}))
        published = await _drain_one_job(store, runtime, "second")
        assert not _has_results(store, "aged")
        assert _announces_result_removal(published, "aged")

    asyncio.run(scenario())


# ---- the app wiring -----------------------------------------------------------


def test_the_app_runtime_reads_the_settings_the_routes_write(tmp_path: Path) -> None:
    from server.app import create_app

    store = JobStore.for_data_dir(tmp_path)
    _seed(store)
    SettingsStore(tmp_path).put("preferences", _profile({"autoCleanupResults": True}))

    application = create_app(data_dir=tmp_path)
    runtime: JobRuntime = application.state.jobs_runtime

    async def scenario() -> None:
        # Startup, before any client: the persisted value is on.
        await runtime.start()
        assert not _has_results(runtime.store, "aged")
        # The same instance the settings routes write.
        application.state.settings.put(
            "preferences", _profile({"autoCleanupResults": False})
        )
        assert runtime._results_cleanup_enabled() is False
        await runtime.shutdown()

    asyncio.run(scenario())
