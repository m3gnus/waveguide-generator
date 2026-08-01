"""Stored-directivity gate for the solve backends, on a real horn.

`test_bempp_feature_parity` compares the two backends only on synthetic
geometry (near-unit spheres) and only for specific features. Nothing checked
that they produce the same *directivity* on an actual waveguide, which is the
thing users see.

BE PRECISE ABOUT WHAT ONE RUN OF THIS PROVES. It solves with ONE backend --
whichever this host would actually use -- and compares against one stored
answer. On Apple Silicon that is Metal against a Metal-generated reference: a
golden regression test, not a cross-backend comparison. The comparison only
becomes cross-backend when the *other* backend runs it, which on a single
machine means forcing it:

    WG_PARITY_BACKEND=bempp   # or 'metal'; default picks what the host uses

Cross-backend was verified that way on 2026-08-01 (macOS arm64, Bempp on the
numba assembly backend, 182 s against Metal's 3 s). Until this repo has a
non-Mac runner, nothing re-checks that automatically -- so if you change either
backend's numerics, run the bempp side by hand.

The mesh is an ATH export read byte for byte from the ATH reference archive --
never regenerated, because re-meshing would silently change what is being
compared. Point `ATH_REFERENCE_ROOT` at the archive to enable this test:

    ATH_REFERENCE_ROOT="/path/to/ath reference" \\
        node scripts/run-backend-python.js --cwd server -m unittest \\
        tests.test_cross_backend_asro2_parity

Measured against the stored reference, this mesh, all 40 frequencies:

                            main-lobe rms   main-lobe worst   all-points rms
    Bempp (cross-backend)      0.000357         0.002286         0.003323
    Metal at the older pin     0.000004         0.000038         0.000151

The thresholds below sit well above those on purpose -- they are meant to catch
a backend genuinely diverging, not to pin numerical noise. If one starts
failing, find out which backend moved and why; do NOT raise the threshold to
accommodate the new number.
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

MAIN_LOBE_RMS_MAX_DB = 0.010  # measured 0.000357
MAIN_LOBE_WORST_MAX_DB = 0.050  # measured 0.002286
# RMS over every angle, including the nulls. An rms is robust to one noisy null
# in a way a worst-of-all-angles bound is not -- an ungated worst-point cap
# would contradict the gate rationale directly above and fail on a harmless
# platform-dependent shift at -70 dB.
ALL_POINTS_RMS_MAX_DB = 0.050  # measured 0.003323

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


def _mesh_path() -> Path:
    """Locate the pinned ATH export, or say precisely why it is unusable.

    Skipping when the variable is unset is correct -- the archive is external
    and most checkouts will not have it. Skipping when it IS set but the fixture
    is missing is not: that is a mistyped path or a broken archive, and silently
    passing is how a gate stops gating.
    """
    root = os.environ.get("ATH_REFERENCE_ROOT")
    if not root:
        raise unittest.SkipTest(
            "ATH_REFERENCE_ROOT is unset; point it at the ATH reference archive "
            "to run the stored-directivity gate"
        )
    candidate = Path(root) / ASRO2_RELATIVE
    if not candidate.is_file():
        raise AssertionError(
            f"ATH_REFERENCE_ROOT is set to {root!r} but {ASRO2_RELATIVE.as_posix()} "
            "is not there. Fix the path or the archive rather than leaving the "
            "gate silently skipped."
        )
    return candidate


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

    # No try/except around this. The repo pins one exact hornlab-bempp-bem
    # revision, so a SolveConfig that will not accept these fields is a real
    # dependency regression, and turning it into a skip would hide exactly the
    # breakage this test exists to catch.
    config = SolveConfig(
        observation=_observation(ObservationConfig),
        assembly_backend=_chosen_assembly_backend(),
        **_sweep_kwargs(),
    )
    return solve(str(msh), config)


def _metal_ready() -> bool:
    from solver.metal_solver import is_metal_fast_solve_ready

    return bool(is_metal_fast_solve_ready())


def _available_backend():
    """Pick the backend to exercise.

    Defaults to the one this host would actually use for a solve, so the gate
    reflects reality. `WG_PARITY_BACKEND` forces the other one, which is the
    only way to make this a genuine cross-backend comparison on a single
    machine -- see the module docstring.

    A forced backend that cannot run is an error, not a skip: the caller asked
    for it explicitly.
    """
    forced = (os.environ.get("WG_PARITY_BACKEND") or "").strip().lower()
    if forced == "metal":
        if not _metal_ready():
            raise AssertionError("WG_PARITY_BACKEND=metal but the Metal backend is not ready")
        return "metal", _solve_metal
    if forced == "bempp":
        import hornlab_bempp_bem  # noqa: F401  - ImportError here is the point

        return "bempp", _solve_bempp
    if forced:
        raise AssertionError(f"WG_PARITY_BACKEND={forced!r}; expected 'metal' or 'bempp'")

    try:
        if _metal_ready():
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
        # The artifact is committed, so its absence is a broken checkout, not a
        # reason to pass quietly.
        self.assertTrue(
            REFERENCE_NPZ.is_file(),
            f"committed reference artifact is missing: {REFERENCE_NPZ}",
        )

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

        # Check the axes the numbers are indexed by, not just the frequencies.
        # A backend can return a correct pressure array while mislabelling its
        # planes or angles, and result mapping would then plot the right data
        # under the wrong curve.
        np.testing.assert_allclose(
            np.asarray(result.frequencies_hz), stored["frequency_hz"], rtol=1e-9
        )
        np.testing.assert_allclose(
            np.asarray(result.observation_angles_deg), stored["angles_deg"], atol=1e-9
        )
        self.assertEqual(list(result.observation_planes), list(stored["planes"]))
        self.assertEqual(actual.shape, reference.shape)

        diff = actual - reference
        gate = reference > MAIN_LOBE_DB
        main_lobe_rms = float(np.sqrt(np.mean(np.square(diff[gate]))))
        main_lobe_worst = float(np.abs(diff[gate]).max())
        all_points_rms = float(np.sqrt(np.mean(np.square(diff))))

        detail = (
            f"backend={name} main_lobe_rms={main_lobe_rms:.6f} "
            f"main_lobe_worst={main_lobe_worst:.6f} "
            f"all_points_rms={all_points_rms:.6f} dB"
        )
        self.assertLess(main_lobe_rms, MAIN_LOBE_RMS_MAX_DB, detail)
        self.assertLess(main_lobe_worst, MAIN_LOBE_WORST_MAX_DB, detail)
        self.assertLess(all_points_rms, ALL_POINTS_RMS_MAX_DB, detail)


if __name__ == "__main__":
    unittest.main()
