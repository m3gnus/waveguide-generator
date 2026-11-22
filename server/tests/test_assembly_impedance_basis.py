"""An old design-assembly replay converts its impedance to the velocity basis once.

Requests written before ``excitation_normalization`` existed decode to RMS velocity
for assemblies (``normalization_for_engine``) without tagging the result. The
impedance used to be converted there and again in the response mapping, which put
the shown Z/(rho*c) off by exactly j*omega (628j at 100 Hz).
"""

from types import SimpleNamespace

import numpy as np
import pytest

from server.solver import metal
from server.solver.result_mapping import specific_impedance_z_over_rho_c
from server.tests.test_engines_adapters import _cabinet_msh
from server.tests.test_imported_jobs import _native_result as imported_result, _request, _roled_record

FREQUENCIES = np.array([100.0, 200.0])
RAW = np.array([0.3 + 2.0j, 0.5 + 1.0j])  # pressure per unit normal acceleration, as the native helper returns


def _solve(monkeypatch, tmp_path, normalization):
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))

    def native_multi_source(path, specs, config, *, frequencies_hz=None):
        result = imported_result()
        result.frequencies_hz = FREQUENCIES.copy()
        result.impedance = RAW.copy()
        return [result]

    monkeypatch.setattr(metal, "native_solve_multi_source", native_multi_source)
    geometry = dict(drive_channels=[{"id": "drive", "source_ids": ["source-a"], "patch_weights": {"source-a": 1.0}}],
                    required_features=["wg-design-assembly-v1"],
                    mesh={"rigid_size_mm": 8., "transition_mm": 20., "source_size_mm": {"source-a": 3.}})
    if normalization is not None:
        geometry["excitation_normalization"] = normalization
    request = _request("wgi_" + "0" * 26, **geometry)
    request.options.frequencies_hz = FREQUENCIES.tolist()
    msh = _cabinet_msh()
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text(msh)
    record = _roled_record(mesh_path)
    record["source_tags"]["source-a"] = 2
    record["required_features"] = ["wg-design-assembly-v1"]
    record["excitations"] = [{"id": "drive", "source_ids": ["source-a"], "motion": "normal",
                              "patch_weights": {"source-a": 1.0}}]
    return metal.solve_imported_metal_from_msh_text(msh, request, record)["channels"]["drive"]["impedance"]


def _z(impedance):
    return np.asarray(impedance["real"]) + 1j * np.asarray(impedance["imaginary"])


@pytest.mark.parametrize("normalization", [None, "rms_velocity", "acceleration_legacy"])
def test_assembly_impedance_is_converted_to_velocity_once(monkeypatch, tmp_path, normalization):
    shown = _z(_solve(monkeypatch, tmp_path, normalization))
    once = np.asarray(specific_impedance_z_over_rho_c(SimpleNamespace(frequencies_hz=FREQUENCIES, impedance=RAW)))
    # The basis is bookkeeping: Z/(rho*c) is p/v whichever basis the replay decodes.
    np.testing.assert_allclose(shown, once, rtol=1e-12)


def _normalize_before_the_fix(result, geometry, *, engine=None):
    """A frozen copy of ``normalize_prescribed_result`` as it was before the fix."""

    from server.solver.excitation_basis import pressure_per_velocity_scale
    from server.solver.imported_channels import scale_channel_result

    normalization = geometry.normalization_for_engine(engine)
    explicit = (geometry.excitation_normalization is not None
                or geometry.excitation_normalization_by_engine is not None)
    if explicit:
        result.excitation_normalization = normalization
    if normalization != "rms_velocity":
        return
    scale = pressure_per_velocity_scale(result.frequencies_hz)
    scale_channel_result(result, scale)
    impedance = getattr(result, "impedance", None)
    if impedance is not None:
        result.impedance = np.asarray(impedance) * scale


def test_the_old_double_conversion_was_off_by_j_omega(monkeypatch, tmp_path):
    """The old code's shown value, from a frozen copy of it, was j*omega times today's."""

    from server.solver import imported_channels

    fixed = _z(_solve(monkeypatch, tmp_path, None))
    monkeypatch.setattr(metal, "normalize_prescribed_result", _normalize_before_the_fix)
    monkeypatch.setattr(imported_channels, "normalize_prescribed_result", _normalize_before_the_fix)
    old = _z(_solve(monkeypatch, tmp_path, None))
    ratio = old / fixed
    np.testing.assert_allclose(ratio, 2j * np.pi * FREQUENCIES, rtol=1e-12)
    assert ratio[0] == pytest.approx(628.3185307179587j)
    once = np.asarray(specific_impedance_z_over_rho_c(SimpleNamespace(frequencies_hz=FREQUENCIES, impedance=RAW)))
    np.testing.assert_allclose(fixed, once, rtol=1e-12)


def test_an_assembly_channel_can_never_carry_a_driver():
    """So no driver-derived number (ohms, excursion, driver-scaled SPL) of an
    untagged assembly replay can move with this fix: the contract refuses them."""

    from server.contracts.source_contour import validate_contour_request
    driver = {"sd_cm2": 210.0, "bl_t_m": 10.5, "re_ohm": 5.3, "mms_g": 20.0, "cms_m_per_n": 4e-4,
              "rms_kg_per_s": 1.2}
    request = _request("wgi_" + "0" * 26, drive_channels=[{"id": "drive", "source_ids": ["source-a"],
                                                           "driver": driver}],
                       required_features=["wg-design-assembly-v1"],
                       mesh={"rigid_size_mm": 8., "transition_mm": 20., "source_size_mm": {"source-a": 3.}})
    record = {"required_features": ["wg-design-assembly-v1"], "skipped_source_ids": [],
              "excitations": [{"id": "drive", "source_ids": ["source-a"], "motion": "normal"}]}
    with pytest.raises(ValueError, match="contradicts the compiled radiator excitation"):
        validate_contour_request(request.geometry, record)
