from __future__ import annotations

import pytest

from server.cadlink.roles import inventory_sha256, normalize_source_role, prepared_inventory_sha256


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
def test_band_mode_only_rewrites_driver_bands(
    role: str, expected: str
) -> None:
    assert normalize_source_role(role, mode="band") == expected


@pytest.mark.parametrize(
    ("role", "source", "band", "protocol"),
    [
        (" mf ", "MF", "MF", " mf "),
        ("PORT_EXIT", "PASSIVE_CARDIOID", "PORT_EXIT", "PASSIVE_CARDIOID"),
        (" port_exit ", "PASSIVE_CARDIOID", " port_exit ", " port_exit "),
        (" passive_cardioid ", "PASSIVE_CARDIOID", " passive_cardioid ", " passive_cardioid "),
        (" Rigid ", " Rigid ", " Rigid ", " Rigid "),
        ("", "", "", ""),
    ],
)
def test_role_modes_preserve_each_caller_contract(role, source, band, protocol):
    assert normalize_source_role(role) == source
    assert normalize_source_role(role, mode="band") == band
    assert normalize_source_role(role, mode="protocol") == protocol


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


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("PORT_EXIT", "sha256:313033e8e0d80f6eb66c5efdc5a582fb7da25b08b4184a8ee7aa27ab5b43cd7a"),
        ("PASSIVE_CARDIOID", "sha256:1a14916be1f71c36f19c366dd6db2c1d155cdd5424a38effb0eb4f9d269a9aad"),
        (" port_exit ", "sha256:c628f5744032ef9660437e0f4f80c67d38c6b0fe2f42889ab91da0e3f7c551c5"),
        ("hf", "sha256:bf2f9413502fa8ff691fc83d6794deff9c6456a8a2c3c0d5a489033924ae09d9"),
        (" HF ", "sha256:bf2f9413502fa8ff691fc83d6794deff9c6456a8a2c3c0d5a489033924ae09d9"),
    ],
)
def test_inventory_preserves_legacy_role_identity(role, expected):
    assert inventory_sha256([{"id": "port", "role": role, "required": True}]) == expected


@pytest.mark.parametrize("role", ["mf", " MF ", "port_exit", " PORT_EXIT ", "passive_cardioid"])
def test_file_preparation_keeps_protocol_role_validation_strict(role):
    from server.jobs.models_api import FilePreparation

    with pytest.raises(ValueError, match="Choose a driver"):
        FilePreparation(units="m", forward="+z", up="+y", roles={1: role})
