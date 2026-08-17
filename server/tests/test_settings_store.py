"""Durable interface settings: the copy that outlives the browser."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.settings.store import (
    MAX_NAMESPACES,
    MAX_NAMESPACE_BYTES,
    SCHEMA_VERSION,
    SettingsError,
    SettingsStore,
)


def store_for(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path, settings_path=tmp_path / "ui_settings.json")


def test_absent_file_reads_as_empty(tmp_path: Path) -> None:
    assert store_for(tmp_path).envelope() == {"schemaVersion": SCHEMA_VERSION, "namespaces": {}}


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


def test_a_corrupt_file_falls_back_to_defaults_without_raising(tmp_path: Path) -> None:
    """Unreadable settings must not stop WG from starting."""

    path = tmp_path / "ui_settings.json"
    path.write_text("{ not json", encoding="utf-8")
    store = SettingsStore(tmp_path, settings_path=path)
    assert store.all() == {}
    store.put("theme", "dark")
    assert store_for(tmp_path).get("theme") == "dark"


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
    }
    assert store_for(tmp_path).get("theme") == "light"
