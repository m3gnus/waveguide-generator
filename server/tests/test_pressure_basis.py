from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest

from server.solver.combine import serialize_channel_bases
from server.tests.npz_assertions import assert_npz_contents_equal
from server.solver.pressure_basis import (
    PRESSURE_PHASE_CONVENTION,
    export_pressure_basis,
)


def _native_basis() -> SimpleNamespace:
    pressure = np.asarray([[[1.0 + 2.0j, 3.0 - 4.0j]]], dtype=np.complex128)
    return SimpleNamespace(
        frequencies_hz=np.asarray([100.0]),
        observation_angles_deg=np.asarray([0.0, 30.0]),
        observation_planes=["horizontal"],
        pressure_complex=pressure,
        sphere_pressure_complex=np.asarray([[5.0 + 6.0j]], dtype=np.complex128),
        sphere_theta_deg=np.asarray([0.0]),
        sphere_phi_deg=np.asarray([0.0]),
    )


def test_pressure_basis_export_conjugates_and_tags_retained_solver_fields() -> None:
    native = _native_basis()
    artifact = serialize_channel_bases(
        {"mf drive": native},
        metadata_by_id={
            "mf drive": {
                "source_ids": ["source-mf"],
                "source_tags": [102],
                "source_areas_m2": [0.021],
                "source_motion": "axial",
                "source_normalization": "voltage_driven_driver_lem",
            }
        },
    )

    exported = export_pressure_basis(
        artifact,
        {"channels": {"mf drive": {"metadata": {"impedance_drive": "voltage"}}}},
    )
    assert_npz_contents_equal(
        exported.content,
        export_pressure_basis(
            artifact,
            {"channels": {"mf drive": {"metadata": {"impedance_drive": "voltage"}}}},
        ).content,
    )

    assert exported.channel_id == "mf drive"
    with np.load(io.BytesIO(exported.content), allow_pickle=False) as data:
        assert str(data["phase_convention"].item()) == PRESSURE_PHASE_CONVENTION
        assert str(data["source_normalization"].item()) == "voltage_driven_driver_lem"
        assert str(data["source_motion"].item()) == "axial"
        assert int(data["source_tag"]) == 102
        assert float(data["source_area_m2"]) == pytest.approx(0.021)
        assert data["source_ids"].tolist() == ["source-mf"]
        assert np.array_equal(data["pressure_complex"], np.conjugate(native.pressure_complex))
        assert np.array_equal(
            data["sphere_pressure_complex"],
            np.conjugate(native.sphere_pressure_complex),
        )
        assert bool(data["surface_pressure_avg_available"]) is False


def test_pressure_basis_export_requires_a_channel_when_artifact_has_many() -> None:
    artifact = serialize_channel_bases({"left": _native_basis(), "right": _native_basis()})

    with pytest.raises(ValueError, match="choose a pressure-basis channel"):
        export_pressure_basis(artifact, {"channels": {}})
    with pytest.raises(ValueError, match="not available"):
        export_pressure_basis(artifact, {"channels": {}}, "missing")


@pytest.mark.parametrize("metadata,expected", [
    ({"source_normalization": "native_rms_velocity"}, "native_rms_velocity"),
    ({"impedance_drive": "rms_voltage"}, "native_rms_voltage"),
])
def test_existing_mixed_archives_use_the_public_normalization_contract(metadata, expected):
    artifact = serialize_channel_bases({"drive": _native_basis()}, metadata_by_id={"drive": {"source_motion": "normal"}})
    exported = export_pressure_basis(artifact, {"channels": {"drive": {"metadata": metadata}}})
    with np.load(io.BytesIO(exported.content), allow_pickle=False) as archive:
        assert archive["source_normalization"].item() == expected


def test_pressure_basis_export_discloses_reconstructed_rows_only_when_flagged():
    native = _native_basis()
    native.frequency_status = ["interpolated"]
    artifact = serialize_channel_bases(
        {"drive": native},
        metadata_by_id={
            "drive": {"source_motion": "normal"},
        },
    )
    exported = export_pressure_basis(artifact, {})
    with np.load(io.BytesIO(exported.content), allow_pickle=False) as data:
        assert data["frequency_status"].tolist() == ["interpolated"]
        assert data["solved_count"].item() == 0
        assert data["interpolated_count"].item() == 1
        assert data["solved_frequencies_hz"].size == 0
        assert "narrow resonances can be missed" in data["warning"].item()
    del native.frequency_status
    legacy = export_pressure_basis(
        serialize_channel_bases(
            {"drive": native},
            metadata_by_id={
                "drive": {"source_motion": "normal"},
            },
        ),
        {},
    )
    with np.load(io.BytesIO(legacy.content), allow_pickle=False) as data:
        assert "frequency_status" not in data and "warning" not in data


def test_unflagged_pressure_basis_matches_frozen_base_44226173_contents():
    artifact = serialize_channel_bases(
        {"drive": _native_basis()},
        metadata_by_id={
            "drive": {"source_motion": "normal"},
        },
    )
    expected = (
        Path(__file__).parent / "fixtures" / "adaptive-legacy-pressure-basis.npz"
    ).read_bytes()
    assert_npz_contents_equal(export_pressure_basis(artifact, {}).content, expected)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_frozen_pressure_basis_comparison_ignores_zip_packaging(compression):
    expected = (
        Path(__file__).parent / "fixtures" / "adaptive-legacy-pressure-basis.npz"
    ).read_bytes()
    repacked = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(expected)) as source, zipfile.ZipFile(repacked, "w") as target:
        for name in reversed(source.namelist()):
            member = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 12, 34, 56))
            member.compress_type = compression
            member.create_system = 0  # Windows rather than the frozen macOS ZIP attributes.
            target.writestr(member, source.read(name))
    assert_npz_contents_equal(repacked.getvalue(), expected)


@pytest.mark.parametrize("change", ["member", "dtype", "shape", "value", "metadata"])
def test_pressure_basis_content_comparison_rejects_payload_changes(change):
    expected = (
        Path(__file__).parent / "fixtures" / "adaptive-legacy-pressure-basis.npz"
    ).read_bytes()
    with np.load(io.BytesIO(expected), allow_pickle=False) as source:
        arrays = {name: source[name] for name in source.files}
    if change == "member":
        del arrays["pressure_complex"]
    elif change == "dtype":
        arrays["pressure_complex"] = arrays["pressure_complex"].astype(np.complex64)
    elif change == "shape":
        arrays["pressure_complex"] = arrays["pressure_complex"].reshape(-1)
    elif change == "value":
        arrays["pressure_complex"] = arrays["pressure_complex"] + 1
    else:
        arrays["source_motion"] = np.asarray("axial!")  # Same dtype/shape, changed metadata.
    changed = io.BytesIO()
    np.savez(changed, **arrays)
    with pytest.raises(AssertionError):
        assert_npz_contents_equal(changed.getvalue(), expected)


def multichannel_basis(*, spherical=True):
    native = _native_basis()
    native.frequency_status = ["interpolated"]
    if not spherical:
        native.sphere_pressure_complex = None
    artifact = serialize_channel_bases(
        {name: native for name in ("normal", "axial", "request")},
        metadata_by_id={"normal": {
            "source_motion": "normal", "source_ids": ["stored"],
            "source_tags": [101], "source_areas_m2": [0.021],
            "source_normalization": "native_rms_velocity",
        }},
    )
    results = {"channels": {
        "normal": {"metadata": {"source_motion": "axial", "source_ids": ["ignored"]}},
        "axial": {"metadata": {
            "source_motion": "axial", "impedance_drive": "voltage",
            "axial_contract": "per-source-axis-v2",
            "source_axes": [{"axis": [0, 0, 1], "raw_axis": [0, 0, 2]}],
        }},
    }}
    return artifact, results, {"request": "normal"}


@pytest.mark.parametrize("spherical", [False, True])
def test_batch_pressure_bases_decode_once_and_match_single_exports(monkeypatch, spherical):
    from server.solver import pressure_basis

    artifact, results, motions = multichannel_basis(spherical=spherical)
    expected = {name: export_pressure_basis(artifact, results, name, motions).content
                for name in ("normal", "axial", "request")}
    calls = []
    original = pressure_basis.deserialize_channel_bases

    def decode(data):
        calls.append(data)
        return original(data)

    monkeypatch.setattr(pressure_basis, "deserialize_channel_bases", decode)
    exports = pressure_basis.export_pressure_bases(artifact, results, request_motions=motions)
    assert len(calls) == 1
    assert [item.channel_id for item in exports] == list(expected)
    for item in exports:
        assert item.content == expected[item.channel_id]
        assert_npz_contents_equal(item.content, expected[item.channel_id])
        with np.load(io.BytesIO(item.content), allow_pickle=False) as data:
            assert data["source_motion"].item() == ("axial" if item.channel_id == "axial" else "normal")
            assert data["frequency_status"].tolist() == ["interpolated"]
            assert ("sphere_pressure_complex" in data) == spherical
            np.testing.assert_array_equal(data["pressure_complex"], _native_basis().pressure_complex.conjugate())


def test_batch_pressure_bases_selection_and_missing_motion_refusals():
    from server.solver.pressure_basis import export_pressure_bases

    artifact, results, motions = multichannel_basis()
    selected = export_pressure_bases(artifact, results, ["axial", "normal"], motions)
    assert [item.channel_id for item in selected] == ["axial", "normal"]
    with pytest.raises(ValueError, match="not available"):
        export_pressure_bases(artifact, results, ["missing"], motions)
    with pytest.raises(ValueError, match="cannot be established"):
        export_pressure_bases(artifact, results)
    with pytest.raises(ValueError, match="cannot be established"):
        export_pressure_basis(artifact, results, "request")
