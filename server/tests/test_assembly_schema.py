"""The design's integrated Sources and baffle assembly: typed identity and persistence."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from server.contracts.assembly import (
    HF_RADIATOR_ID,
    is_radiator_uuid,
    patch_id,
    source_role,
    split_source_id,
)
from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize

W1 = "6f1c1f0e-2b7a-4c55-9a51-0d9f2f6b8a01"
W2 = "0b8f5f2c-8c1d-4f2b-a7a8-9b9c5c3d2e02"
G_HF = "a1b2c3d4-0000-4000-8000-000000000001"
G_LF = "a1b2c3d4-0000-4000-8000-000000000002"

DRIVER = {
    "version": 1, "re_ohm": 6.0, "le_h": 0.0005, "bl_n_per_a": 7.0,
    "mmd_kg": 0.02, "cms_m_per_n": 0.001, "rms_n_s_per_m": 1.0,
}


def _assembly(**changes):
    assembly = {
        "woofers": [
            {"id": W1, "label": "Woofer 1", "radius_mm": 80,
             "shape": {"kind": "piston", "recess_mm": 2},
             "placement": {"anchor": "below", "gap_mm": 10}},
            {"id": W2, "label": "Woofer 2", "radius_mm": 80, "enabled": False,
             "shape": {"kind": "ath_cone"},
             "placement": {"anchor": "below", "gap_mm": 10,
                           "spacing_to": {"radiator_id": W1, "distance_mm": 180}}},
        ],
        "drivers": {W1: {"exterior_transducer": DRIVER, "reference": {"driver_id": "lib:1", "source": "mine"}}},
        "signal_groups": [
            {"id": G_HF, "label": "HF", "members": [{"radiator_id": HF_RADIATOR_ID}]},
            {"id": G_LF, "label": "LF", "members": [
                {"radiator_id": W1}, {"radiator_id": W2, "delay_ms": 0.5, "invert": True, "weight": 0.5}]},
        ],
    }
    assembly.update(changes)
    return assembly


def _osse(**extra):
    return {"formula": "OSSE", "L": 120, "a": 45, "a0": 10, "r0": 12.7, "k": 7,
            "s": 0.8, "n": 4, "q": 0.99, "enclosure": {"depth": 200}, **extra}


def test_identity_contract_names_patches_by_radiator_uuid() -> None:
    assert is_radiator_uuid(W1) and not is_radiator_uuid(W1.upper()) and not is_radiator_uuid("LF 1")
    assert patch_id(W1, "piston") == f"{W1}/piston"
    assert split_source_id(f"{W1}/cap") == (W1, "cap")
    assert split_source_id(HF_RADIATOR_ID) == (HF_RADIATOR_ID, None)
    for bad in ("LF 1/piston", f"{W1}", f"{W1}/Cap", "rigid"):
        with pytest.raises(ValueError):
            split_source_id(bad)
    assert source_role("primary") == "HF" and source_role("secondary") == "LF"
    with pytest.raises(ValueError):
        source_role("rigid")


def test_horn_only_design_has_no_assembly_and_writes_nothing_new() -> None:
    design = DesignConfig.model_validate(_osse())
    assert design.root.assembly is None
    assert "assembly" not in design.model_dump(mode="json")
    text = serialize(design)
    assert "WG.Geometry" not in text
    reopened = parse(text)
    assert reopened.design.root.assembly is None
    assert serialize(reopened) == text
    assert "WG.Geometry" not in serialize(reopened.design)


def test_assembly_round_trips_api_and_cfg_with_identity_and_disabled_data() -> None:
    design = DesignConfig.model_validate(_osse(assembly=_assembly()))
    wire = design.model_dump(mode="json")
    assembly = wire["assembly"]
    # Defaults stay out of the wire; disabled woofers keep every setting.
    assert assembly["hf_source"] == {"radiator_id": HF_RADIATOR_ID, "kind": "classic"}
    assert assembly["woofers"][1]["enabled"] is False
    assert assembly["woofers"][1]["placement"]["spacing_to"]["radiator_id"] == W1
    assert "drive_voltage_v" not in assembly and "crossover" not in assembly
    assert DesignConfig.model_validate(wire).model_dump(mode="json") == wire
    text = serialize(design)
    assert "WG.Geometry" in text
    reopened = parse(text)
    assert reopened.design.model_dump(mode="json")["assembly"] == assembly
    assert serialize(reopened) == text
    assert "WG.Geometry" in serialize(reopened.design)


@pytest.mark.parametrize("formula", ["OSSE-NATIVE", "LOOKUP"])
def test_native_payload_designs_carry_the_assembly_in_their_json(formula: str) -> None:
    payload = ({"formula": "LOOKUP", "lookup_profile": [[0, 12.7], [120, 140]], "a0": 0}
               if formula == "LOOKUP" else
               {"formula": formula, "L": 120, "r0": 12.7, "a": 40, "a0": 15.5, "k": 1, "s": 0,
                "axial_scale": 1.2, "mesh": {"mouth_roundover_radius_mm": 25, "wall_thickness": 5}})
    design = DesignConfig.model_validate({**payload, "assembly": _assembly()})
    text = serialize(design)
    assert parse(text).design.model_dump(mode="json") == design.model_dump(mode="json")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"woofers": [{**_assembly()["woofers"][0]}, {**_assembly()["woofers"][0]}]}, "unique"),
        ({"drivers": {W2: {"exterior_transducer": DRIVER}, "f" * 8: {"exterior_transducer": DRIVER}}}, "keyed"),
        ({"signal_groups": [{"id": G_HF, "label": "HF", "members": [{"radiator_id": HF_RADIATOR_ID}]}]}, "exactly one"),
        ({"signal_groups": [
            {"id": G_HF, "label": "HF", "members": [{"radiator_id": HF_RADIATOR_ID}, {"radiator_id": W1}]},
            {"id": G_LF, "label": "LF", "members": [{"radiator_id": W1}, {"radiator_id": W2}]}]}, "exactly one"),
        ({"signal_groups": [
            {"id": W1, "label": "HF", "members": [{"radiator_id": HF_RADIATOR_ID}]},
            {"id": G_LF, "label": "LF", "members": [{"radiator_id": W1}, {"radiator_id": W2}]}]}, "distinct"),
    ],
)
def test_assembly_refuses_reused_or_dangling_identities(change, message) -> None:
    with pytest.raises(ValidationError, match=message):
        DesignConfig.model_validate(_osse(assembly=_assembly(**change)))


def test_spacing_relations_are_by_identity_and_acyclic() -> None:
    woofers = _assembly()["woofers"]
    woofers[0]["placement"]["spacing_to"] = {"radiator_id": W2, "distance_mm": 180}
    with pytest.raises(ValidationError, match="cycle"):
        DesignConfig.model_validate(_osse(assembly=_assembly(woofers=woofers)))
    woofers = _assembly()["woofers"]
    woofers[1]["placement"]["spacing_to"]["radiator_id"] = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(ValidationError, match="spacing_to"):
        DesignConfig.model_validate(_osse(assembly=_assembly(woofers=woofers)))


@pytest.mark.parametrize(
    ("woofer", "message"),
    [
        ({"id": "LF 1"}, "UUID"),
        ({"id": W1.upper()}, "UUID"),
        ({"label": " "}, "label"),
        ({"radius_mm": 0}, "greater than 0"),
        ({"shape": {"kind": "ath_cone", "recess_mm": 2}}, "piston-only"),
        ({"shape": {"kind": "piston", "recess_mm": 0.005}}, "weld"),
        ({"placement": {"anchor": "free", "gap_mm": 10}}, "free placement"),
        ({"placement": {"anchor": "above", "x_mm": 0, "y_mm": 1}}, "above placement"),
        ({"drive": "voltage"}, "drive"),
    ],
)
def test_woofer_fields_are_typed(woofer, message) -> None:
    woofers = _assembly()["woofers"]
    woofers[0] = {**woofers[0], **woofer}
    with pytest.raises(ValidationError, match=message):
        DesignConfig.model_validate(_osse(assembly=_assembly(woofers=woofers)))


def test_driver_blobs_are_bounded_finite_and_axial() -> None:
    with pytest.raises(ValidationError, match="axial Z"):
        DesignConfig.model_validate(_osse(assembly=_assembly(
            drivers={W1: {"exterior_transducer": {**DRIVER, "motion_axis": [0, 0, 1]}}})))
    with pytest.raises(ValidationError, match="finite"):
        DesignConfig.model_validate(_osse(assembly=_assembly(
            drivers={W1: {"exterior_transducer": {**DRIVER, "re_ohm": float("inf")}}})))
    crossover = {"enabled": True, "spec": {"members": [G_HF, G_LF], "reference": G_HF}}
    design = DesignConfig.model_validate(_osse(assembly=_assembly(crossover=crossover)))
    assert design.model_dump(mode="json")["assembly"]["crossover"] == crossover


def test_ath_provenance_is_kept_without_inventing_weights() -> None:
    woofers = _assembly()["woofers"]
    woofers[0]["ath"] = {"key": "B", "sid": 2}
    design = DesignConfig.model_validate(_osse(assembly=_assembly(woofers=woofers)))
    assert design.model_dump(mode="json")["assembly"]["woofers"][0]["ath"] == {"key": "B", "sid": 2}
    woofers[0]["ath"] = {"key": "B", "sid": 0}
    with pytest.raises(ValidationError):
        DesignConfig.model_validate(_osse(assembly=_assembly(woofers=woofers)))


def test_existing_controls_block_keeps_its_keys_beside_the_assembly() -> None:
    design = DesignConfig.model_validate(_osse(
        assembly=_assembly(), enclosure={"depth": 200, "depth_margin_mm": 3}))
    text = serialize(design)
    payload = next(line for line in text.splitlines() if line.strip().startswith("Payload"))
    controls = json.loads(payload.split("=", 1)[1].strip())
    assert set(controls) == {"assembly", "depth_margin_mm"}
    reopened = parse(text).design
    assert reopened.root.enclosure.depth_margin_mm == 3
    assert reopened.model_dump(mode="json")["assembly"] == design.model_dump(mode="json")["assembly"]
