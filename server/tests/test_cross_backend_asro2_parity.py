"""Cross-backend gate: Metal and Bempp must agree on a real horn.

`test_bempp_feature_parity` compares the two backends only on synthetic
geometry (near-unit spheres) and only for specific features. Nothing checked
that they produce the same *directivity* on an actual waveguide, which is the
thing users see. This test closes that gap, and because it runs whichever
backend the host has, it doubles as a cross-platform gate: Apple Silicon
exercises Metal, everything else exercises Bempp, both against one stored
reference.

The mesh is an ATH export read byte for byte from the ATH reference archive --
never regenerated, because re-meshing would silently change what is being
compared. Point `ATH_REFERENCE_ROOT` at the archive to enable this test:

    ATH_REFERENCE_ROOT="/path/to/ath reference" \\
        node scripts/run-backend-python.js --cwd server -m unittest \\
        tests.test_cross_backend_asro2_parity

Measured on 2026-08-01, macOS arm64, this mesh, all 40 frequencies:

    Metal vs Bempp, main-lobe rms   0.0004 dB
    Metal vs Bempp, all points rms  0.0035 dB
    Metal vs Bempp, worst point     0.078  dB  (at -70 dB, in a rear null)

The thresholds below sit well above those figures on purpose -- they are meant
to catch a backend genuinely diverging, not to pin numerical noise. If one of
them starts failing, find out which backend moved and why; do NOT raise the
threshold to accommodate the new number.
"""
from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

import numpy as np

# Main-lobe gate. Below -20 dB an on-axis-normalised polar is dominated by deep
# rear nulls, where a fraction of a dB of absolute pressure reads as tens of dB
# relative and neither backend is converged.
MAIN_LOBE_DB = -20.0

MAIN_LOBE_RMS_MAX_DB = 0.010  # measured 0.0004
ALL_POINTS_RMS_MAX_DB = 0.050  # measured 0.0035
WORST_POINT_MAX_DB = 0.500  # measured 0.078

ASRO2_RELATIVE = Path("asro2") / "ABEC_FreeStanding" / "asro2.msh"
ASRO2_MD5 = "1c74051f05cee2f66bfe73897b3e6421"

REFERENCE_NPZ = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "cross-backend"
    / "asro2-quarter-reference-2026-08-01.npz"
)

# ATH exports are in millimetres; both backends take metres.
MESH_SCALE = 0.001
# Physical groups in this export: 1 = SD1G0 (wall), 2 = SD1D1001 (driver).
SOURCE_TAG = 2


def _mesh_path() -> Path | None:
    root = os.environ.get("ATH_REFERENCE_ROOT")
    if not root:
        return None
    candidate = Path(root) / ASRO2_RELATIVE
    return candidate if candidate.is_file() else None


def _observation(cls):
    return cls(
        planes=["horizontal", "vertical", "diagonal"],
        distance_m=2.0,
        angle_min_deg=0.0,
        angle_max_deg=180.0,
        angle_count=37,
        origin="throat",
    )


def _sweep_kwargs() -> dict:
    return {
        "mesh_scale": MESH_SCALE,
        "freq_min_hz": 100.0,
        "freq_max_hz": 20000.0,
        "freq_count": 40,
        "freq_spacing": "log",
        "native_symmetry_plane": "yz+xz",
        "velocity_sources": {SOURCE_TAG: 1.0},
    }


def _solve_metal(msh: Path):
    from hornlab_metal_bem import ObservationConfig, native_config, solve

    config = native_config(observation=_observation(ObservationConfig), **_sweep_kwargs())
    return solve(str(msh), config)


def _solve_bempp(msh: Path):
    from hornlab_bempp_bem import ObservationConfig, SolveConfig, solve

    # Select the assembly backend the way a real solve does, rather than
    # letting SolveConfig default to OpenCL: a host without pyopencl (any
    # Apple Silicon install, since Metal short-circuits the bempp extra) would
    # otherwise raise OpenCLError instead of falling back to numba.
    from solver.bempp_solver import _chosen_assembly_backend

    kwargs = _sweep_kwargs()
    try:
        config = SolveConfig(
            observation=_observation(ObservationConfig),
            assembly_backend=_chosen_assembly_backend(),
            **kwargs,
        )
    except TypeError:  # older pin without one of the sweep fields
        raise unittest.SkipTest("installed hornlab-bempp-bem does not accept the sweep config")
    return solve(str(msh), config)


def _available_backend():
    """Prefer the backend this host would actually use for a solve."""
    try:
        from solver.metal_solver import is_metal_fast_solve_ready

        if is_metal_fast_solve_ready():
            return "metal", _solve_metal
    except Exception:
        pass
    try:
        import hornlab_bempp_bem  # noqa: F401

        return "bempp", _solve_bempp
    except Exception:
        return None, None


def _normalised_spl(result) -> np.ndarray:
    pressure = np.asarray(result.pressure_complex)
    return 20 * np.log10(np.abs(pressure / pressure[:, :, 0:1]) + 1e-300)


class CrossBackendAsro2Parity(unittest.TestCase):
    def test_backend_reproduces_stored_asro2_directivity(self):
        msh = _mesh_path()
        if msh is None:
            self.skipTest(
                "ATH_REFERENCE_ROOT is unset or does not contain "
                f"{ASRO2_RELATIVE.as_posix()}"
            )
        if not REFERENCE_NPZ.is_file():
            self.skipTest(f"reference artifact missing: {REFERENCE_NPZ}")

        digest = hashlib.md5(msh.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            ASRO2_MD5,
            f"{msh} is not the pinned ATH export; comparing against it would be meaningless",
        )

        name, solver = _available_backend()
        if solver is None:
            self.skipTest("neither hornlab-metal-bem nor hornlab-bempp-bem can solve here")

        stored = np.load(REFERENCE_NPZ, allow_pickle=False)
        reference = stored["spl_db"]

        result = _solve_metal(msh) if name == "metal" else _solve_bempp(msh)
        actual = _normalised_spl(result)

        np.testing.assert_allclose(
            np.asarray(result.frequencies_hz), stored["frequency_hz"], rtol=1e-9
        )
        self.assertEqual(actual.shape, reference.shape)

        diff = actual - reference
        gate = reference > MAIN_LOBE_DB
        main_lobe_rms = float(np.sqrt(np.mean(np.square(diff[gate]))))
        all_points_rms = float(np.sqrt(np.mean(np.square(diff))))
        worst = float(np.abs(diff).max())

        detail = (
            f"backend={name} main_lobe_rms={main_lobe_rms:.5f} "
            f"all_points_rms={all_points_rms:.5f} worst={worst:.4f} dB"
        )
        self.assertLess(main_lobe_rms, MAIN_LOBE_RMS_MAX_DB, detail)
        self.assertLess(all_points_rms, ALL_POINTS_RMS_MAX_DB, detail)
        self.assertLess(worst, WORST_POINT_MAX_DB, detail)


if __name__ == "__main__":
    unittest.main()
