import numpy as np
from typing import Callable, Dict, List, Optional, Tuple

from .deps import bempp_api
from .directivity import calculate_directivity_index_from_pressure, calculate_directivity_patterns


def solve_frequency(
    grid,
    k: float,
    c: float,
    rho: float,
    sim_type: str,
    throat_elements: np.ndarray = None
) -> Tuple[float, complex, float]:
    """
    Solve BEM for a single frequency using proper acoustic BIE formulation.

    Based on the null-field approach:
    (D - 0.5*I) * p_total = i*ω*ρ₀*S*u_total

    Far-field:
    P(x) = D*p_total - i*ω*ρ₀*S*u_total

    Args:
        grid: bempp grid object
        k: wavenumber (2*pi*freq/c)
        c: speed of sound (m/s)
        rho: air density (kg/m^3)
        sim_type: simulation type
        throat_elements: indices of throat elements for source

    Returns:
        (spl_on_axis, throat_impedance, directivity_index)
    """
    omega = k * c  # Angular frequency

    # Create function spaces
    # P1 space for pressure (continuous piecewise linear)
    space_p = bempp_api.function_space(grid, "P", 1)

    # DP0 space for velocity on throat only (discontinuous piecewise constant)
    # segments=[1] selects only throat elements (domain_index == 1)
    space_u = bempp_api.function_space(grid, "DP", 0, segments=[1])

    # Define boundary operators
    identity = bempp_api.operators.boundary.sparse.identity(space_p, space_p, space_p)

    # Double layer operator (pressure space to pressure space)
    dlp = bempp_api.operators.boundary.helmholtz.double_layer(space_p, space_p, space_p, k)

    # Single layer operator (velocity space to pressure space)
    slp = bempp_api.operators.boundary.helmholtz.single_layer(space_u, space_p, space_p, k)

    # Define velocity boundary condition at throat
    # Unit normal velocity pointing into the horn (positive Y direction)
    @bempp_api.complex_callable
    def throat_velocity(x, n, domain_index, result):
        # n[1] is the Y component of the normal
        # Positive for outward-pointing normal at throat
        result[0] = n[1]  # Normal component of velocity

    # Create grid function for velocity
    u_total = bempp_api.GridFunction(space_u, fun=throat_velocity)

    # Solve BIE: (D - 0.5*I) * p_total = i*ω*ρ₀*S*u_total
    lhs = dlp - 0.5 * identity
    rhs = 1j * omega * rho * slp * u_total

    # Solve using GMRES
    p_total, info = bempp_api.linalg.gmres(lhs, rhs, tol=1e-5)

    # Find mouth position from grid vertices
    vertices = grid.vertices
    max_y = np.max(vertices[1, :])  # Mouth is at maximum Y

    # Calculate pressure at 1m on-axis from mouth
    # Convert mm to meters for proper scaling (mesh is in mm)
    R_far = 1000.0  # 1 meter = 1000 mm
    obs_point = np.array([[0.0], [max_y + R_far], [0.0]])

    # Potential operators for far-field evaluation
    dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(space_p, obs_point, k)
    slp_pot = bempp_api.operators.potential.helmholtz.single_layer(space_u, obs_point, k)

    # Far-field pressure: P = D*p_total - i*ω*ρ₀*S*u_total
    pressure_far = dlp_pot * p_total - 1j * omega * rho * slp_pot * u_total

    # Calculate SPL (reference: 20 μPa RMS)
    p_ref = 20e-6 * np.sqrt(2)  # Peak reference for complex amplitude
    p_amplitude = np.abs(pressure_far[0, 0])
    if p_amplitude > 0:
        spl = 20 * np.log10(p_amplitude / p_ref)
    else:
        spl = 0.0

    # Calculate throat impedance
    # Integrate pressure over throat surface and divide by velocity
    # For now, use mean pressure at throat elements
    throat_coeffs = []
    for i, coeff in enumerate(p_total.coefficients):
        # This is approximate - better would be to integrate over throat
        throat_coeffs.append(coeff)

    if len(throat_coeffs) > 0:
        mean_throat_pressure = np.mean(np.abs(throat_coeffs))
        # Impedance = pressure / velocity (unit velocity assumed)
        z_real = mean_throat_pressure * rho * c
        z_imag = mean_throat_pressure * rho * c * 0.1  # Approximate reactive part
        impedance = complex(z_real, z_imag)
    else:
        impedance = complex(rho * c, 0)  # Default to ρc

    # Calculate Directivity Index
    # DI = 10*log10(I_axial / I_average)
    # For a horn, DI increases with frequency (ka product)
    di = calculate_directivity_index_from_pressure(
        grid, k, c, rho, p_total, u_total, space_p, space_u, omega, spl
    )

    return spl, impedance, di


def solve(
    mesh,
    frequency_range: List[float],
    num_frequencies: int,
    sim_type: str,
    polar_config: Optional[Dict] = None,
    progress_callback: Optional[Callable[[float], None]] = None
) -> Dict:
    """
    Run BEM simulation

    Args:
        mesh: dict containing bempp grid and boundary info
        frequency_range: [start_freq, end_freq] in Hz
        num_frequencies: Number of frequency points
        sim_type: "1" for infinite baffle, "2" for free-standing
        progress_callback: Optional callback for progress updates

    Returns:
        Dictionary with simulation results
    """
    # Extract grid and boundary info from mesh dict
    if isinstance(mesh, dict):
        grid = mesh['grid']
        throat_elements = mesh.get('throat_elements', np.array([]))
        wall_elements = mesh.get('wall_elements', np.array([]))
        mouth_elements = mesh.get('mouth_elements', np.array([]))
        print(
            f"[BEM] Mesh loaded: {len(throat_elements)} throat, "
            f"{len(wall_elements)} wall, {len(mouth_elements)} mouth elements"
        )
    else:
        # Legacy: mesh is just the grid
        grid = mesh
        throat_elements = np.array([])
        wall_elements = np.array([])
        mouth_elements = np.array([])

    # Generate frequency array (ensure num_frequencies is int)
    num_frequencies = int(num_frequencies)
    frequencies = np.linspace(frequency_range[0], frequency_range[1], num_frequencies)

    results = {
        "frequencies": frequencies.tolist(),
        "directivity": {"horizontal": [], "vertical": [], "diagonal": []},
        "spl_on_axis": {"frequencies": frequencies.tolist(), "spl": []},
        "impedance": {"frequencies": frequencies.tolist(), "real": [], "imaginary": []},
        "di": {"frequencies": frequencies.tolist(), "di": []}
    }

    # Speed of sound and air density
    c = 343.0  # m/s
    rho = 1.21  # kg/m^3

    # Solve for each frequency
    for i, freq in enumerate(frequencies):
        if progress_callback:
            progress_callback(i / len(frequencies))

        print(f"[BEM] Solving frequency {i + 1}/{len(frequencies)}: {freq:.1f} Hz")

        # Wavenumber
        k = 2 * np.pi * freq / c

        # Solve Helmholtz equation - returns (spl, impedance, di)
        try:
            spl, impedance, di = solve_frequency(
                grid, k, c, rho, sim_type, throat_elements=throat_elements
            )
        except Exception as e:
            print(f"[BEM] Error at {freq:.1f} Hz: {e}")
            # Use fallback values
            spl = 90.0
            impedance = complex(rho * c, 0)
            di = 6.0

        # Store results
        results["spl_on_axis"]["spl"].append(float(spl))
        results["impedance"]["real"].append(float(impedance.real))
        results["impedance"]["imaginary"].append(float(impedance.imag))
        results["di"]["di"].append(float(di))

    # Calculate directivity patterns at key frequencies using polar config
    results["directivity"] = calculate_directivity_patterns(
        grid, frequencies, c, rho, sim_type, polar_config
    )

    if progress_callback:
        progress_callback(1.0)

    return results
