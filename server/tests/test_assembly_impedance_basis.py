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


def test_the_old_double_conversion_was_off_by_j_omega(monkeypatch, tmp_path):
    """Pins the bug's size: the replay shown before the fix was j*omega times the true value."""

    once = np.asarray(specific_impedance_z_over_rho_c(SimpleNamespace(frequencies_hz=FREQUENCIES, impedance=RAW)))
    from server.solver.excitation_basis import pressure_per_velocity_scale

    twice = np.asarray(specific_impedance_z_over_rho_c(SimpleNamespace(
        frequencies_hz=FREQUENCIES, impedance=RAW * pressure_per_velocity_scale(FREQUENCIES))))
    ratio = twice / once
    np.testing.assert_allclose(np.abs(ratio), 2 * np.pi * FREQUENCIES, rtol=1e-12)
    assert abs(ratio[0]) == pytest.approx(628.3185307179587)
    np.testing.assert_allclose(_z(_solve(monkeypatch, tmp_path, None)), once, rtol=1e-12)
