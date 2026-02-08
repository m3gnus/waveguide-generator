export function generateBemppStarterScript({ meshFileName, sourceTag = 2 }) {
  const mesh = meshFileName || 'mesh.msh';
  return `import numpy as np
import bempp.api

grid = bempp.api.import_grid("${mesh}")
space_p = bempp.api.function_space(grid, "P", 1)
space_u = bempp.api.function_space(grid, "DP", 0, segments=[${sourceTag}])

k = 2 * np.pi * 1000.0 / 343.0
rho = 1.21
omega = k * 343.0

identity = bempp.api.operators.boundary.sparse.identity(space_p, space_p, space_p)
dlp = bempp.api.operators.boundary.helmholtz.double_layer(space_p, space_p, space_p, k)
slp = bempp.api.operators.boundary.helmholtz.single_layer(space_u, space_p, space_p, k)

@bempp.api.complex_callable
def velocity(x, n, domain_index, result):
    result[0] = n[1]

u_total = bempp.api.GridFunction(space_u, fun=velocity)
lhs = dlp - 0.5 * identity
rhs = 1j * omega * rho * slp * u_total
p_total, info = bempp.api.linalg.gmres(lhs, rhs, tol=1e-5)

angles = np.linspace(0, 180, 37)
radius = 2.0  # meters
for theta_deg in angles:
    theta = np.deg2rad(theta_deg)
    point_mm = np.array([[1000.0 * radius * np.sin(theta)],
                         [1000.0 * radius * np.cos(theta)],
                         [0.0]])
    dlp_pot = bempp.api.operators.potential.helmholtz.double_layer(space_p, point_mm, k)
    slp_pot = bempp.api.operators.potential.helmholtz.single_layer(space_u, point_mm, k)
    pressure = dlp_pot * p_total - 1j * omega * rho * slp_pot * u_total
    print(theta_deg, np.abs(pressure[0, 0]))
`;
}
