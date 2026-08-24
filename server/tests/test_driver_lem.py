"""Driver LEM seam: unit conversion, z_self math, and voltage scaling."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from server.jobs.models import DriveChannel, DriverSpec
from server.solver.driver_lem import (
    channel_drive_scaling,
    hornlab_driver,
    one_way_peak_excursion_mm,
    self_impedance_from_surface_average,
)


def _spec(**overrides: object) -> DriverSpec:
    base: dict[str, object] = {
        "sd_cm2": 210.0,
        "bl_t_m": 10.5,
        "re_ohm": 5.3,
        "le_mh": 0.5,
        "mmd_g": 12.0,
        "cms_m_per_n": 4.0e-4,
        "rms_kg_per_s": 1.2,
    }
    base.update(overrides)
    return DriverSpec.model_validate(base)


def test_hornresp_units_convert_to_si() -> None:
    driver = hornlab_driver(_spec(vas_l=20.0, cms_m_per_n=None))
    assert driver.Sd == pytest.approx(0.021)
    assert driver.Le == pytest.approx(5.0e-4)
    assert driver.Mmd == pytest.approx(0.012)
    assert driver.Vas == pytest.approx(0.020)
    assert driver.Re == pytest.approx(5.3)
    derived = driver.derive()
    assert derived.Cms is not None and derived.Fs is not None


def test_spec_requires_exactly_one_mass_and_one_stiffness() -> None:
    with pytest.raises(ValidationError, match="exactly one of mmd_g or mms_g"):
        _spec(mms_g=13.0)
    with pytest.raises(ValidationError, match="cms_m_per_n, vas_l, or fs_hz"):
        _spec(cms_m_per_n=None)


def test_driver_requires_single_source_normal_channel() -> None:
    with pytest.raises(ValidationError, match="single-source channel"):
        DriveChannel.model_validate(
            {
                "id": "pair",
                "source_ids": ["a", "b"],
                "driver": _spec().model_dump(mode="json", exclude_none=True),
            }
        )
    with pytest.raises(ValidationError, match="normal source motion"):
        DriveChannel.model_validate(
            {
                "id": "ax",
                "source_ids": ["a"],
                "motion": "axial",
                "driver": _spec().model_dump(mode="json", exclude_none=True),
            }
        )


def test_self_impedance_matches_the_addin_seam_by_hand() -> None:
    # z_self_eng = conj(-jω·⟨p⟩_raw)/S at 100 Hz, ⟨p⟩ = 2+1j, S = 0.01 m²:
    # -j·(2+1j) = 1-2j; ×ω; conjugate; /S.
    omega = 2.0 * np.pi * 100.0
    z = self_impedance_from_surface_average(
        np.asarray([100.0]), np.asarray([2.0 + 1.0j]), 0.01
    )
    expected = np.conjugate(omega * (1.0 - 2.0j)) / 0.01
    assert z[0] == pytest.approx(expected)


def test_channel_scaling_produces_electrical_picture_and_warnings() -> None:
    freqs = np.geomspace(20.0, 2_000.0, 48)
    p_avg = np.full(freqs.size, 0.1 + 0.05j, dtype=np.complex128)
    scale_raw, payload = channel_drive_scaling(
        freqs,
        p_avg,
        0.021,
        _spec(xmax_mm=0.001),
        drive_voltage_v=2.83,
        rg_ohm=0.0,
    )
    assert scale_raw.shape == freqs.shape
    assert np.all(np.isfinite(scale_raw))
    electrical = payload["electrical_impedance_ohm"]
    z = np.asarray(electrical["real"]) + 1j * np.asarray(electrical["imaginary"])
    # Off resonance the input impedance approaches the voice coil; at the
    # resonance the motional term dominates and the magnitude peaks well
    # above Re.
    assert z.real.min() >= 5.3 * 0.9
    assert np.abs(z).max() > 2.0 * 5.3
    # The deliberately tiny Xmax must surface as a warning, never silently.
    assert any("exceeds Xmax" in warning for warning in payload["warnings"])
    assert payload["cone_excursion_mm"]["peak_mm"] > 0.001
    assert payload["cone_excursion_mm"]["quantity"] == "one_way_peak_displacement"


def test_driver_label_passes_through_to_the_metadata_payload() -> None:
    freqs = np.geomspace(20.0, 2_000.0, 8)
    p_avg = np.full(freqs.size, 0.1 + 0.05j, dtype=np.complex128)
    _scale_raw, payload = channel_drive_scaling(
        freqs,
        p_avg,
        0.021,
        _spec(label="  Acme 12ND  "),
        drive_voltage_v=2.83,
        rg_ohm=0.0,
    )
    assert payload["label"] == "Acme 12ND"
    assert payload["spec"]["label"] == "Acme 12ND"


def test_driver_label_defaults_to_none_and_strips_blank_to_none() -> None:
    assert _spec().label is None
    assert _spec(label="   ").label is None


def test_excursion_converts_rms_phasor_magnitude_to_one_way_peak_mm() -> None:
    converted = one_way_peak_excursion_mm(np.asarray([0.001, 0.002]))

    assert converted == pytest.approx([np.sqrt(2.0), 2.0 * np.sqrt(2.0)])


def test_scaling_is_the_conjugated_engineering_acceleration() -> None:
    freqs = np.asarray([100.0, 400.0])
    p_avg = np.asarray([0.2 + 0.1j, 0.15 + 0.02j])
    area = 0.021
    spec = _spec()
    scale_raw, _payload = channel_drive_scaling(
        freqs, p_avg, area, spec, drive_voltage_v=2.83, rg_ohm=0.0
    )
    from hornlab_sim.methods import driver_coupling

    coupled = driver_coupling.coupled_direct_radiator_response(
        freqs,
        driver=hornlab_driver(spec),
        z_self=self_impedance_from_surface_average(freqs, p_avg, area),
        drive_voltage_v=2.83,
        rg_ohm=0.0,
    )
    expected_eng = 1j * 2.0 * np.pi * freqs * coupled.cone_volume_velocity / area
    assert np.allclose(scale_raw, np.conjugate(expected_eng))
