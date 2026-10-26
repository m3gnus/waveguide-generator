from __future__ import annotations

import pytest

from server.cadlink.roles import canonical_source_role, inventory_sha256, prepared_inventory_sha256


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("LF", "LF"),
        ("mf", "MF"),
        (" hf ", "HF"),
        ("rigid", "rigid"),
        ("interface", "interface"),
        (" Rigid ", " Rigid "),
    ],
)
def test_canonical_source_role_only_rewrites_driver_bands(
    role: str, expected: str
) -> None:
    assert canonical_source_role(role) == expected


def test_legacy_file_inventory_retains_source_bytes_while_records_use_hf():
    sources = [{"id": "group_2", "role": "HF", "required": True}]
    old = [{**sources[0], "role": "SOURCE"}]
    record = {"producer": "mesh-file", "source_file": {"source_tags": [2]}, "sources": sources}
    assert prepared_inventory_sha256(record) == inventory_sha256(old)
    assert prepared_inventory_sha256({**record, "sources": old}) == inventory_sha256(old)
    assert prepared_inventory_sha256({**record, "source_file": {"roles": {"2": "HF"}}}) == inventory_sha256(sources)
    assert prepared_inventory_sha256({**record, "producer": "cad"}) == inventory_sha256(sources)
    assert sources[0]["role"] == "HF"


def test_saved_legacy_setup_is_found_after_repreparing_with_canonical_roles(tmp_path):
    from types import SimpleNamespace

    from server.cadlink.store import CadLinkStore
    from server.jobs.models_api import read_setup

    store = CadLinkStore.for_data_dir(tmp_path)
    sources = [{"id": "group_2", "role": "SOURCE", "required": True}]
    try:
        revision = store.create_setup_revision('{"saved":true}', "sha256:test")
        model_id = "mesh-file:sha256:example"
        store.record_model_setup(model_id, inventory_sha256(sources), revision["revision_id"])
        # The original preparation may be collected. Its rebuilt record uses
        # HF while the setup in the database still has the old SOURCE key.
        import json
        record = {"producer": "mesh-file", "source_file": {"source_tags": [2]},
                  "sources": [{**sources[0], "role": "HF"}]}
        reader = SimpleNamespace(
            get_ingest=lambda _id: {"model_id": model_id, "record_json": json.dumps(record)},
            get_model_setup=store.get_model_setup, get_setup_revision=store.get_setup_revision,
        )
        assert read_setup(reader, model_id, "rebuilt-preparation") == {
            "setup": {"saved": True}, "revision_id": revision["revision_id"],
        }
    finally:
        store.close()
