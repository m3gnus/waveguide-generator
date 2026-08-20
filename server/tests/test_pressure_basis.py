from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pytest

from server.solver.combine import serialize_channel_bases
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
    assert exported.content == export_pressure_basis(
        artifact,
        {"channels": {"mf drive": {"metadata": {"impedance_drive": "voltage"}}}},
    ).content

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
