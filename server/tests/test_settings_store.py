"""Durable interface settings: the copy that outlives the browser."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.settings.store import (
    MAX_DELETED,
    MAX_NAMESPACES,
    MAX_NAMESPACE_BYTES,
    SCHEMA_VERSION,
    SettingsError,
    SettingsStore,
    StaleWriteError,
)
from server.settings import cad_drafts


def store_for(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path, settings_path=tmp_path / "ui_settings.json")


def test_absent_file_reads_as_empty(tmp_path: Path) -> None:
    assert store_for(tmp_path).envelope() == {
        "schemaVersion": SCHEMA_VERSION,
        "namespaces": {},
        "deleted": [],
    }


def test_written_settings_survive_a_new_process(tmp_path: Path) -> None:
    store_for(tmp_path).put("preferences", '{"exportFormats":["csv"]}')
    # A second store is what the next launch sees: no shared in-memory state,
    # and no dependence on which origin the window was opened from.
    assert store_for(tmp_path).get("preferences") == '{"exportFormats":["csv"]}'


def test_namespaces_are_independent(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.put("preferences", "a")
    store.put("solveOptions", "b")
    assert store_for(tmp_path).all() == {"preferences": "a", "solveOptions": "b"}


def test_delete_removes_only_its_own_namespace(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.put("preferences", "a")
    store.put("theme", "light")
    store.delete("preferences")
    assert store_for(tmp_path).all() == {"theme": "light"}


@pytest.mark.parametrize("namespace", ["", "../escape", "has space", "1leading", "a" * 65])
def test_unusable_namespaces_are_refused(tmp_path: Path, namespace: str) -> None:
    with pytest.raises(SettingsError):
        store_for(tmp_path).put(namespace, "value")


def test_oversized_payloads_are_refused(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    with pytest.raises(SettingsError):
        store.put("preferences", "x" * (MAX_NAMESPACE_BYTES + 1))
    assert store_for(tmp_path).all() == {}


def test_namespace_count_is_bounded(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    for index in range(MAX_NAMESPACES):
        store.put(f"ns{index}", "v")
    with pytest.raises(SettingsError):
        store.put("overflow", "v")
    # An existing namespace is still writable once the ceiling is reached.
    store.put("ns0", "updated")
    assert store.get("ns0") == "updated"


def test_a_corrupt_file_is_preserved_and_falls_back_to_defaults(tmp_path: Path) -> None:
    """Unreadable settings must not stop WG from starting."""

    path = tmp_path / "ui_settings.json"
    corrupt_contents = "{ not json"
    path.write_text(corrupt_contents, encoding="utf-8")
    store = SettingsStore(tmp_path, settings_path=path)
    assert store.all() == {}
    corrupt_path = tmp_path / "ui_settings.json.corrupt"
    assert corrupt_path.read_text(encoding="utf-8") == corrupt_contents
    store.put("theme", "dark")
    assert store_for(tmp_path).get("theme") == "dark"
    assert corrupt_path.read_text(encoding="utf-8") == corrupt_contents


def test_a_transient_read_error_cannot_wipe_existing_namespaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ui_settings.json"
    original = {
        "schemaVersion": SCHEMA_VERSION,
        "namespaces": {"dockLayout": {"panels": ["design", "results"]}, "preferences": "saved"},
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    real_read_text = Path.read_text
    attempts = 0

    def fail_first_read(target: Path, *args: object, **kwargs: object) -> str:
        nonlocal attempts
        if target == path and attempts == 0:
            attempts += 1
            raise OSError("transient read failure")
        return real_read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_first_read)
    store = SettingsStore(tmp_path, settings_path=path)

    with pytest.raises(SettingsError, match="Could not read settings"):
        store.put("theme", "dark")
    assert json.loads(real_read_text(path, encoding="utf-8")) == original

    store.put("theme", "dark")
    assert json.loads(real_read_text(path, encoding="utf-8"))["namespaces"] == {
        **original["namespaces"],
        "theme": "dark",
    }


def test_entries_with_unusable_names_are_dropped_on_read(tmp_path: Path) -> None:
    path = tmp_path / "ui_settings.json"
    path.write_text(
        json.dumps({"schemaVersion": 1, "namespaces": {"theme": "dark", "../evil": "x"}}),
        encoding="utf-8",
    )
    assert SettingsStore(tmp_path, settings_path=path).all() == {"theme": "dark"}


def test_a_failed_write_leaves_the_previous_settings_readable(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.put("theme", "dark")
    with pytest.raises(SettingsError):
        store.put("theme", "x" * (MAX_NAMESPACE_BYTES + 1))
    assert store_for(tmp_path).get("theme") == "dark"


def test_routes_read_and_write_the_same_store(tmp_path: Path) -> None:
    from server.settings.api import create_settings_router

    store = store_for(tmp_path)
    routes = {
        (route.path, tuple(sorted(route.methods - {"HEAD", "OPTIONS"}))): route
        for route in create_settings_router(store).routes
    }
    write = routes[("/api/settings/{namespace}", ("PUT",))]
    read = routes[("/api/settings", ("GET",))]

    asyncio.run(write.endpoint(namespace="theme", value="light"))
    assert asyncio.run(read.endpoint()) == {
        "schemaVersion": SCHEMA_VERSION,
        "namespaces": {"theme": "light"},
        "deleted": [],
    }
    assert store_for(tmp_path).get("theme") == "light"


def test_a_deleted_namespace_is_recorded_rather_than_merely_absent(tmp_path: Path) -> None:
    """An absent namespace is what a deletion and a never-migrated setting both
    look like, and the browser answers those two cases in opposite ways: it
    republishes its own copy of the second. Only the server can tell them
    apart, so it says which names were deleted."""

    store = store_for(tmp_path)
    store.put("designDraft", "old-draft")
    store.delete("designDraft")

    next_launch = store_for(tmp_path)
    assert next_launch.get("designDraft") is None
    assert next_launch.deleted() == ["designDraft"]
    assert next_launch.envelope()["deleted"] == ["designDraft"]


def test_deleting_a_namespace_the_server_never_had_still_records_it(tmp_path: Path) -> None:
    store_for(tmp_path).delete("designDraft")
    assert store_for(tmp_path).deleted() == ["designDraft"]


def test_writing_a_namespace_revives_it(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.delete("designDraft")
    store.put("designDraft", "new-draft")

    next_launch = store_for(tmp_path)
    assert next_launch.get("designDraft") == "new-draft"
    assert next_launch.deleted() == []


def test_tombstones_are_bounded(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    for index in range(MAX_DELETED + 3):
        store.delete(f"ns{index}")
    recorded = store_for(tmp_path).deleted()
    assert len(recorded) == MAX_DELETED
    # Oldest dropped first, so the most recent deletions are the ones honoured.
    assert recorded[-1] == f"ns{MAX_DELETED + 2}"
    assert "ns0" not in recorded


def test_settings_written_before_tombstones_existed_still_load(tmp_path: Path) -> None:
    """Backward compatibility with a file an older build wrote: no ``deleted``
    key at all, which means exactly what it meant then -- nothing is known to
    have been deleted."""

    path = tmp_path / "ui_settings.json"
    path.write_text(
        json.dumps({"schemaVersion": 1, "namespaces": {"theme": "dark"}}),
        encoding="utf-8",
    )
    store = SettingsStore(tmp_path, settings_path=path)
    assert store.envelope() == {
        "schemaVersion": SCHEMA_VERSION,
        "namespaces": {"theme": "dark"},
        "deleted": [],
    }


def test_unusable_tombstone_names_are_dropped_on_read(tmp_path: Path) -> None:
    path = tmp_path / "ui_settings.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "namespaces": {"theme": "dark"},
                "deleted": ["../evil", "designDraft", "theme", "designDraft"],
            }
        ),
        encoding="utf-8",
    )
    # A name that is also stored is not deleted, and one that could never have
    # been stored has nothing to say.
    assert SettingsStore(tmp_path, settings_path=path).deleted() == ["designDraft"]


def test_a_write_its_own_successor_overtook_is_refused(tmp_path: Path) -> None:
    """One page can have two writes for a namespace in flight at once, because
    closing the window sends the pending value ahead of the request queue. If
    the network delivers them in the other order the older one arrives last."""

    store = store_for(tmp_path)
    store.put("theme", "A", writer="page-1", sequence=1)
    store.put("theme", "B", writer="page-1", sequence=2)

    with pytest.raises(StaleWriteError):
        store.put("theme", "A", writer="page-1", sequence=1)
    assert store_for(tmp_path).get("theme") == "B"

    # A delete is a write like any other and is ordered the same way.
    with pytest.raises(StaleWriteError):
        store.delete("theme", writer="page-1", sequence=2)
    assert store_for(tmp_path).get("theme") == "B"


def test_another_page_is_never_refused(tmp_path: Path) -> None:
    """Sequence numbers are only comparable within the page that issued them.
    A second window starting from 1 is not stale; it is a different writer."""

    store = store_for(tmp_path)
    store.put("theme", "B", writer="page-1", sequence=7)
    store.put("theme", "C", writer="page-2", sequence=1)
    assert store_for(tmp_path).get("theme") == "C"


def test_a_client_that_sends_no_ordering_hints_is_written_as_before(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.put("theme", "B", writer="page-1", sequence=9)
    store.put("theme", "C")
    assert store_for(tmp_path).get("theme") == "C"
    # The old client cleared the order, so its successor's numbering stands.
    store.put("theme", "D", writer="page-1", sequence=1)
    assert store_for(tmp_path).get("theme") == "D"


def test_a_refused_write_leaves_the_ordering_state_alone(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.put("theme", "B", writer="page-1", sequence=2)
    with pytest.raises(StaleWriteError):
        store.put("theme", "A", writer="page-1", sequence=1)
    store.put("theme", "C", writer="page-1", sequence=3)
    assert store_for(tmp_path).get("theme") == "C"


def test_routes_carry_the_ordering_headers_and_the_tombstone(tmp_path: Path) -> None:
    from server.settings.api import SEQUENCE_HEADER, WRITER_HEADER, create_settings_router
    from server.tests.test_app_batch_e import TestClient

    from fastapi import FastAPI

    store = store_for(tmp_path)
    application = FastAPI()
    application.include_router(create_settings_router(store))
    client = TestClient(application)

    def write(value: str, sequence: int) -> int:
        return client.request(
            "PUT",
            "/api/settings/theme",
            headers={
                "content-type": "application/json",
                WRITER_HEADER: "page-1",
                SEQUENCE_HEADER: str(sequence),
            },
            body=json.dumps(value).encode("utf-8"),
        ).status_code

    assert write("A", 1) == 200
    assert write("B", 2) == 200
    # The older request, delivered last: refused as a conflict rather than as a
    # malformed body, because nothing about it was malformed.
    assert write("A", 1) == 409
    assert client.get("/api/settings").json()["namespaces"]["theme"] == "B"

    removed = client.request(
        "DELETE",
        "/api/settings/theme",
        headers={WRITER_HEADER: "page-1", SEQUENCE_HEADER: "3"},
    )
    assert removed.status_code == 200
    envelope = client.get("/api/settings").json()
    assert envelope["namespaces"] == {}
    assert envelope["deleted"] == ["theme"]


def test_driver_library_namespace_round_trips(tmp_path: Path) -> None:
    """CADLINK-CROSSOVER-DRIVERS.md §4: saved drivers live in their own opaque
    namespace, with no server-side schema of their own to keep in step."""

    from server.settings.api import create_settings_router

    store = store_for(tmp_path)
    routes = {
        (route.path, tuple(sorted(route.methods - {"HEAD", "OPTIONS"}))): route
        for route in create_settings_router(store).routes
    }
    write = routes[("/api/settings/{namespace}", ("PUT",))]
    read = routes[("/api/settings", ("GET",))]

    payload = {
        "drivers": [
            {
                "id": "manual-1",
                "based_on": "manual",
                "overrides": {"sd_cm2": 210.0, "bl_t_m": 10.5, "re_ohm": 5.3},
            }
        ]
    }
    asyncio.run(write.endpoint(namespace="driverLibrary", value=payload))
    assert asyncio.run(read.endpoint()) == {
        "schemaVersion": SCHEMA_VERSION,
        "namespaces": {"driverLibrary": payload},
        "deleted": [],
    }
    assert store_for(tmp_path).get("driverLibrary") == payload



def draft_key(model: str) -> str:
    from server.settings.cad_drafts import draft_hash
    return f"{draft_hash(model)}_{draft_hash('inventory')}"


def draft_entry(timestamp: int, value="draft", base="revision"):
    return {"draft": None if value is None else {"value": value}, "updatedAt": timestamp, "baseRevisionId": base}


def test_cad_journal_merges_each_key_and_keeps_newer_clears_across_windows(tmp_path):
    store = store_for(tmp_path)
    namespace = "cadSetupDrafts_v2"
    a, b = draft_key("a"), draft_key("b")
    store.put(namespace, json.dumps({a: draft_entry(1), b: draft_entry(2)}), writer="a", sequence=1)
    store.put(namespace, json.dumps({a: draft_entry(3, "new")}), writer="b", sequence=1)
    store.put(namespace, json.dumps({a: draft_entry(1), b: draft_entry(4, None)}), writer="a", sequence=2)
    store.put(namespace, json.dumps({b: draft_entry(2)}), writer="b", sequence=2)
    assert json.loads(store_for(tmp_path).get(namespace)) == {a: draft_entry(3, "new"), b: draft_entry(4, None)}
    assert store.deleted() == []


def test_cad_journal_bounds_25_models_and_each_entry(tmp_path):
    store = store_for(tmp_path)
    namespace = "cadSetupDrafts_v2"
    for i in range(25):
        store.put(namespace, json.dumps({draft_key(str(i)): draft_entry(i, "x" * 1000)}))
    entries = json.loads(store.get(namespace))
    assert len(entries) == 20
    assert set(entries) == {draft_key(str(i)) for i in range(5, 25)}
    store.put(namespace, json.dumps({draft_key("huge"): draft_entry(100, "x" * 65_536)}))
    assert json.loads(store.get(namespace)) == entries


def test_newer_cad_markers_never_evict_a_live_draft_or_revive_a_stale_cache(tmp_path):
    store = store_for(tmp_path)
    live, cleared = draft_key("incomplete"), draft_key("published")
    store.put(cad_drafts.NAMESPACE, {live: draft_entry(0), cleared: draft_entry(1, None)})
    for i in range(63):
        store.put(cad_drafts.NAMESPACE, {draft_key(str(i)): draft_entry(i + 2, None)})
    store.put(cad_drafts.NAMESPACE, {cleared: draft_entry(0)})
    journal = json.loads(store_for(tmp_path).get(cad_drafts.NAMESPACE))
    assert journal[live] == draft_entry(0)
    assert journal[cleared] == draft_entry(1, None)
    assert sum(entry["draft"] is None for entry in journal.values()) == 64


def test_full_cad_journal_leaves_room_for_a_growing_design_draft(tmp_path):
    store = store_for(tmp_path)
    # Other growing namespaces put the envelope close enough to its total cap
    # that the old journal allowance would starve designDraft's next write.
    for i in range(3):
        store.put(f"other{i}", "x" * (MAX_NAMESPACE_BYTES - 2))
    store.put("designDraft", "small")
    store.put(cad_drafts.NAMESPACE, {
        draft_key(str(i)): draft_entry(i, "x" * 60_000) for i in range(20)
    })
    journal = store.get(cad_drafts.NAMESPACE)
    assert len(json.dumps(journal).encode()) <= cad_drafts.MAX_NAMESPACE_BYTES
    entries = json.loads(journal)
    assert 0 < len(entries) < 20
    assert draft_key("19") in entries and draft_key("0") not in entries
    design = "x" * (MAX_NAMESPACE_BYTES - 2)
    store.put("designDraft", design)
    assert store_for(tmp_path).get("designDraft") == design


_JOURNAL_PARITY_CASES = json.loads(
    (Path(__file__).parent / "fixtures/cad_draft_journal_parity.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("fixture", _JOURNAL_PARITY_CASES, ids=lambda case: case["name"])
def test_cad_journal_retention_matches_typescript(fixture):
    def key(index):
        return f"{index:016x}_{0:016x}"

    journals = []
    for groups in fixture["journals"]:
        journal = {}
        for group in groups:
            for i in range(group["count"]):
                value = group["value"]
                journal[key(group["start"] + i * group.get("step", 1))] = {
                    "draft": None if value is None else {"value": value * group.get("repeat", 1)},
                    "updatedAt": group["timestamp"] + i * group.get("timestampStep", 0),
                    "baseRevisionId": "r" * group.get("baseRepeat", 1),
                }
        journals.append(journal)
    merged = json.loads(cad_drafts.merge(*journals))
    assert list(merged) == [
        key(group["start"] + i * group.get("step", 1))
        for group in fixture["expected"] for i in range(group["count"])
    ]
    for index, value in fixture.get("values", {}).items():
        assert merged[key(int(index))]["draft"] == (None if value is None else {"value": value})


def test_migration_write_error_reports_write_failure_and_retries_without_losing_settings(tmp_path, monkeypatch):
    from server.settings import store as settings_store

    path = tmp_path / "ui_settings.json"
    original = {"namespaces": {"theme": "dark", "cadSetupDrafts": json.dumps({
        json.dumps(["model", "inventory"]): {"settings": {"driver": {}}, "updatedAt": 1},
    })}}
    path.write_text(json.dumps(original))
    real_write = settings_store._write_json_atomic

    def fail_write(*args, **kwargs):
        raise OSError("migration disk failure")

    monkeypatch.setattr(settings_store, "_write_json_atomic", fail_write)
    store = store_for(tmp_path)
    for _ in range(2):
        with pytest.raises(SettingsError, match="Could not write settings file.*migration disk failure"):
            store.put("designDraft", "new")
        assert json.loads(path.read_text()) == original
    monkeypatch.setattr(settings_store, "_write_json_atomic", real_write)
    store.put("designDraft", "new")
    reloaded = store_for(tmp_path)
    assert reloaded.get("theme") == "dark"
    assert reloaded.get("designDraft") == "new"
    assert json.loads(reloaded.get(cad_drafts.NAMESPACE))[draft_key("model")]["draft"] == {
        "settings": {"driver": {}}, "legacy": True,
    }
    assert reloaded.get("cadSetupDrafts") is None


def test_publishing_cad_drafts_does_not_churn_namespace_tombstones(tmp_path):
    store = store_for(tmp_path)
    retired = ["cadSolveProfiles", "cadProject", "designDraft"]
    for name in retired:
        store.delete(name)
    for i in range(100):
        key = draft_key(str(i))
        store.put("cadSetupDrafts_v2", json.dumps({key: draft_entry(i * 2)}))
        store.put("cadSetupDrafts_v2", json.dumps({key: draft_entry(i * 2 + 1, None)}))
    assert list(store.all()) == ["cadSetupDrafts_v2"]
    assert store_for(tmp_path).deleted() == retired


def test_migrates_both_cad_journals_once_before_filtering_legacy_names(tmp_path):
    from server.settings.cad_drafts import draft_hash

    model = "unclaimed:/" + "long/" * 1500
    key = json.dumps([model, "inventory"])
    old_name = "cadSetupDraft_" + "a" * 5000
    shared_key = json.dumps(["shared", "inventory"])
    expected_key = f"{draft_hash(model)}_{draft_hash('inventory')}"
    original = {"schemaVersion": SCHEMA_VERSION, "namespaces": {
        old_name: json.dumps({key: {"settings": {"driveVoltageV": 7}, "baseRevisionId": "base", "updatedAt": 10}}),
        "cadSetupDrafts": json.dumps({shared_key: {"settings": {"driveVoltageV": 9}}}),
        "theme": "dark",
    }, "deleted": ["designDraft"]}
    path = tmp_path / "ui_settings.json"
    path.write_text(json.dumps(original))
    store = store_for(tmp_path)
    entries = json.loads(store.get("cadSetupDrafts_v2"))
    assert len(entries) == 2
    assert entries[expected_key] == {"draft": {"settings": {"driveVoltageV": 7}}, "baseRevisionId": "base", "updatedAt": 10}
    shared = f"{draft_hash('shared')}_{draft_hash('inventory')}"
    assert entries[shared]["draft"]["legacy"] is True
    assert entries[shared]["baseRevisionId"] is None
    assert set(store.all()) == {"theme", "cadSetupDrafts_v2"}
    assert store.deleted() == ["designDraft", "cadSetupDrafts"]
    # The invalid legacy name is removed atomically, without relaxing validation or adding an unusable tombstone.
    persisted = path.read_bytes()
    assert old_name not in json.loads(persisted)["namespaces"]
    assert store_for(tmp_path).envelope() == store.envelope()
    assert path.read_bytes() == persisted
