import numpy as np
from typing import Dict, List


# Fallback mock solver if bempp is not available
class MockBEMSolver:
    """
    Mock solver for testing without bempp.

    Generates physically realistic data based on acoustic horn theory:
    - SPL: ~110 dB at 1kHz for 1W/1m, with proper low-freq rolloff
    - DI: 6-15 dB range, increasing with frequency
    - Impedance: Approaches ρc (415 Ω) at high frequencies
    """

    def prepare_mesh(
        self,
        vertices: List[float],
        indices: List[int],
        surface_tags: List[int] = None,
        boundary_conditions: Dict = None
    ):
        """Prepare mesh with boundary info (mock version)"""
        num_elements = len(indices) // 3
        if surface_tags is None:
            surface_tags = [2] * num_elements  # Default: all walls

        return {
            "vertices": vertices,
            "indices": indices,
            "surface_tags": surface_tags,
            "throat_elements": [i for i, t in enumerate(surface_tags) if t == 1],
            "wall_elements": [i for i, t in enumerate(surface_tags) if t == 2],
            "mouth_elements": [i for i, t in enumerate(surface_tags) if t == 3]
        }

    def solve(self, mesh, frequency_range, num_frequencies, sim_type, progress_callback=None):
        frequencies = np.linspace(frequency_range[0], frequency_range[1], num_frequencies)

        # Horn parameters (typical 1" throat exponential horn)
        cutoff_freq = 500.0  # Hz - horn cutoff frequency
        rho_c = 415.0  # Characteristic impedance of air (Pa·s/m)

        # Generate physically realistic SPL data
        # Reference: 110 dB at 1kHz for 1W/1m (typical horn sensitivity)
        base_spl = 110.0
        spl_data = []
        for f in frequencies:
            # Below cutoff: 12 dB/octave rolloff (horn + driver)
            # Above cutoff: relatively flat with slight HF rolloff
            if f < cutoff_freq:
                # Rolloff below cutoff: ~12 dB/octave
                rolloff = 12 * np.log2(cutoff_freq / f)
                spl = base_spl - rolloff
            else:
                # Above cutoff: flat with slight HF rolloff above 8kHz
                hf_rolloff = max(0, 3 * np.log2(f / 8000)) if f > 8000 else 0
                spl = base_spl - hf_rolloff

            # Add small random variation (measurement noise)
            spl += np.random.randn() * 0.5
            spl_data.append(spl)

        # Generate realistic DI data
        # DI increases with frequency: ~6 dB at 500Hz to ~15 dB at 10kHz
        di_data = []
        for f in frequencies:
            # DI formula based on ka (k=wavenumber, a=mouth radius)
            # Simplified model: DI increases ~6dB per octave above cutoff
            if f < cutoff_freq:
                di = 3.0 + 3.0 * (f / cutoff_freq)
            else:
                di = 6.0 + 4.5 * np.log2(f / cutoff_freq)

            # Clamp to realistic range and add noise
            di = np.clip(di, 3.0, 18.0)
            di += np.random.randn() * 0.2
            di_data.append(di)

        # Generate realistic impedance data
        # At high frequencies, throat impedance approaches ρc (415 Ω)
        # At low frequencies, impedance is reactive (mass-like)
        z_real = []
        z_imag = []
        for f in frequencies:
            # Real part: transitions from low to ρc
            # Based on horn impedance theory
            f_ratio = f / cutoff_freq
            if f_ratio < 1:
                # Below cutoff: low real part, high reactive
                real = rho_c * (f_ratio ** 2) / (1 + f_ratio ** 2)
                imag = rho_c * f_ratio / (1 + f_ratio ** 2)
            else:
                # Above cutoff: approaches ρc with small oscillations
                real = rho_c * (1 - 0.1 * np.exp(-f_ratio) * np.cos(2 * np.pi * f_ratio))
                imag = rho_c * 0.1 * np.exp(-f_ratio) * np.sin(2 * np.pi * f_ratio)

            # Add small noise
            real += np.random.randn() * 5
            imag += np.random.randn() * 5

            z_real.append(real)
            z_imag.append(imag)

        results = {
            "frequencies": frequencies.tolist(),
            "directivity": {"horizontal": [], "vertical": [], "diagonal": []},
            "spl_on_axis": {"frequencies": frequencies.tolist(), "spl": spl_data},
            "impedance": {"frequencies": frequencies.tolist(), "real": z_real, "imaginary": z_imag},
            "di": {"frequencies": frequencies.tolist(), "di": di_data}
        }

        # Generate mock directivity patterns (simplified)
        angles = np.linspace(0, 180, 37)
        for f in frequencies:
            # Higher frequency = narrower pattern
            beamwidth = max(20, 120 - (f / 200))
            pattern = []
            for angle in angles:
                # Simple cosine-based pattern
                value = 20 * np.log10(np.cos(np.radians(angle)) ** 2 + 0.1)
                value = max(-40, value)
                pattern.append([angle, value])

            results["directivity"]["horizontal"].append(pattern)
            results["directivity"]["vertical"].append(pattern)
            results["directivity"]["diagonal"].append(pattern)

        return results
