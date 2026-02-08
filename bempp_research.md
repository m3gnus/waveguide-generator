# BEMPP (Boundary Element Method Python Package) Research

This document provides a comprehensive overview of BEMPP's capabilities for acoustic simulations, based on research of the beminfo folder and BEMPP tutorial materials.

---

## 1. Physics & Equations

BEMPP solves partial differential equations using boundary element methods. The primary PDEs supported are:

### Helmholtz Equation (Time-Harmonic Acoustics)
The most commonly used equation in the examples:
```
Δp + k²p = 0
```
where `p` is the pressure field and `k = ω/c` is the wavenumber.

**Applications in loudspeaker/acoustic physics:**
- Sound radiation from vibrating surfaces (loudspeakers, drivers)
- Scattering from objects
- Far-field directivity calculations

### Laplace Equation (Steady-State Potential)
```
Δp = 0
```
Used for steady-state potential problems without time dependence.

### Modified Helmholtz Equation
```
Δp - k²p = 0
```
Used for diffusion-reaction type problems.

### Boundary Integral Formulation

For exterior acoustic problems, the boundary integral equation takes the form:
```
(D - 0.5 I)p = iωρ S u
```

Where:
- `D` = Double-layer potential operator
- `S` = Single-layer potential operator  
- `I` = Identity operator
- `p` = Boundary pressure (unknown)
- `u` = Normal velocity (boundary condition)
- `ρ` = Air density
- `ω` = Angular frequency

### Null Field Approach

For radiation problems, BEMPP uses the null field approach where:
1. Assume total pressure is zero inside the domain
2. Solve for boundary pressure using exterior potential representation
3. Evaluate pressure at exterior points using the representation formula

---

## 2. Simulation Workflow

The typical workflow from mesh import to solution:

### Step 1: Import Mesh
```python
grid = bempp.api.import_grid("Loudspeaker.msh")
```

Supported formats include `.msh` (Gmsh) and other standard mesh formats.

### Step 2: Define Function Spaces
```python
# Pressure on entire boundary (continuous piecewise linear)
spaceP = bempp.api.function_space(grid, "P", 1)

# Normal velocity on specific segment (discontinuous piecewise constant)
spaceU = bempp.api.function_space(grid, "DP", 0, segments=[2])
```

### Step 3: Define Boundary Velocity/Conditions
```python
@bempp.api.complex_callable
def u_total_callable(x, n, domain_index, result):
    if domain_index == 2:
        result[0] = n[2] * U_cone  # Cone velocity with direction correction
    else:
        result[0] = 0.0

u_total = bempp.api.GridFunction(spaceU, fun=u_total_callable)
```

### Step 4: Construct Boundary Operators
```python
from bempp.api.operators.boundary import helmholtz, sparse

identity = sparse.identity(spaceP, spaceP, spaceP)
double_layer = helmholtz.double_layer(spaceP, spaceP, spaceP, k)
single_layer = helmholtz.single_layer(spaceU, spaceP, spaceP, k)
```

### Step 5: Assemble and Solve BIE
```python
rhs = 1j * omega * rho_0 * (single_layer * u_total)
lhs = double_layer - 0.5 * identity

p_total, info = gmres(lhs, rhs, tol=1e-5)
```

### Step 6: Evaluate Far-Field Pressure
```python
# At arbitrary points using potential operators
points = np.array([x_coords, y_coords, z_coords])
pressure = helmholtz_potential.double_layer(spaceP, points, k) * p_total \
           - 1j * omega * rho_0 * helmholtz_potential.single_layer(spaceU, points, k) * u_total
```

---

## 3. Mesh Requirements

### Triangular Mesh Specifications
- BEMPP requires triangular surface meshes (2D manifolds in 3D space)
- Meshes must be watertight for exterior problems
- Surface normals must be consistently oriented (outward-pointing)

### Resolution Guidelines
While specific element-per-wavelength guidelines aren't explicitly documented in the examples, best practices for BEM suggest:

| Frequency Range | Elements Per Wavelength | Notes |
|-----------------|------------------------|-------|
| Low (≤500 Hz)   | 6-8                    | Coarser meshes may suffice |
| Mid (500-2000 Hz) | 10-12                 | Standard accuracy |
| High (>2000 Hz) | 15-20+                 | Finer resolution for accurate directivity |

### Supported Import Formats
- **Gmsh (.msh)** - Primary format used in examples
- **VTK/VTU** - VTK format support available via `import_grid`
- **STL** - Triangle mesh format (requires conversion to surface mesh)

### Grid Generation Methods
The tutorials reference Gmsh for mesh generation:
```python
# In .geo file, tag physical entities:
Physical Surface("Radiator", 2) = {surface_ids};
```

Export from Gmsh and import into BEMPP:
```python
grid = bempp.api.import_grid("mesh_file.msh")
```

---

## 4. Simulation Types

### Freestanding Radiator (Null Field Approach)
The most common configuration demonstrated in tutorials:

**Setup:**
- Single radiator surface with prescribed normal velocity
- Rigid boundaries elsewhere on the object
- Null field assumed inside the enclosure

**Use Case:** Loudspeaker radiation into free space

### Infinite Baffle Configuration
Multiple source surfaces can be simulated:

```python
SOURCESURFACES = [2, 3]    # Two source surfaces
SOURCEGAIN = np.array([1., -1.])  # Opposite phases
DIRECTION = [[0,0,1], [0,0,-1]]   # Different radiation directions
```

**Use Case:** Loudspeakers in baffle configurations with front and back radiation.

### Impedance Boundary Conditions
BEMPP supports impedance boundary conditions:

```python
# Robin BC: ∂p/∂n + i k Y_n p = 0
y_n = DiagonalOperator(y_n_callable(x))  # Spatially varying admittance
```

**Use Case:** Absorbing boundaries, acoustic linings, complex material interfaces.

### FEM-BEM Coupling (Multi-Physics)
For problems requiring volume discretization:

```python
# FEM for interior domain with inhomogeneous properties
fenics_space = dolfinx.FunctionSpace(mesh, ("CG", 1))
# BEM for exterior domain
bempp_space = bempp.api.function_space(trace_space.grid, "DP", 0)
```

**Use Case:** Transmission problems, multi-material systems.

---

## 5. Model Preparation

### Function Space Selection

| Space Type | Description | Use For |
|------------|-------------|---------|
| **P** (Continuous Piecewise Linear) | C⁰ continuous, order 1 | Pressure, potential variables |
| **DP** (Discontinuous Piecewise Constant) | Discontinuous, order 0 | Normal velocity, flux variables |
| **Raviart-Thomas** | H(div)-conforming | Vector field quantities |

### Polynomial Orders
- **Order 1 (P1)**: Linear interpolation - most common for acoustic BEM
- **Order 0 (DP0)**: Piecewise constant - used for normal velocity

```python
spaceP = bempp.api.function_space(grid, "P", 1)    # Order 1 pressure
spaceU = bempp.api.function_space(grid, "DP", 0)   # Order 0 velocity
```

### Boundary Operator Construction

```python
# Identity operator
I = sparse.identity(spaceP, spaceP, spaceP)

# Single layer potential: S[u](x) = ∫_Γ G(x,y) u(y) dy
S = helmholtz.single_layer(spaceU, spaceP, spaceP, k)

# Double layer potential: D[p](x) = ∫_Γ ∂G(x,y)/∂n_y p(y) dy
D = helmholtz.double_layer(spaceP, spaceP, spaceP, k)
```

Where `G(x,y)` is the Green's function for the Helmholtz equation.

### Boundary Condition Implementation

**Neumann Problem (Prescribed Normal Velocity):**
```python
# u_total = boundary normal velocity
rhs = 1j * omega * rho_0 * S * u_total
lhs = D - 0.5 * I
```

**Dirichlet Problem (Prescribed Pressure):**
```python
# p_inc = incident pressure
lhs = 0.5 * I + D.T  # Adjoint double layer formulation
rhs = p_inc.projections(space)
```

**Impedance Boundary Condition:**
```python
# (∂/∂n + i k Y_n) p = 0 on Γ
Y_n_op = DiagonalOperator(y_n_values)
lhs = (D - 0.5 * I) + 1j * k * S * Y_n_op
```

---

## 6. Solver Options

### Direct Solvers
- **LU Decomposition**: Available via sparse matrix conversion
```python
lhs_matrix = lhs.weak_form().to_sparse()
lu = scipy.sparse.linalg.splu(lhs_matrix)
p_total = lu.solve(rhs_vector)
```

### Iterative Solvers
**GMRES (Generalized Minimal Residual):**
```python
from bempp.api.linalg import gmres

p_total, info, num_iter = gmres(
    lhs, rhs,
    tol=1e-5,
    return_iteration_count=True
)
```

### Preconditioning
For large problems, preconditioning is essential:

```python
# Block diagonal preconditioner for FEM-BEM coupling
P1 = InverseSparseDiscreteBoundaryOperator(blocked[0,0].to_sparse())
P2 = InverseSparseDiscreteBoundaryOperator(
    bempp.api.operators.boundary.sparse.identity(space, space, space).weak_form()
)

def apply_prec(x):
    m1 = P1.shape[0]
    res1 = P1.dot(x[:m1])
    res2 = P2.dot(x[m1:])
    return np.concatenate([res1, res2])

P = LinearOperator(shape, apply_prec)
soln, info = gmres(blocked, rhs, M=P)
```

### FMM Acceleration
The Fast Multipole Method (FMM) provides O(N) complexity for far-field calculations:
- Available through `potential` operators
- Automatically applied when evaluating at many exterior points

```python
# FMM acceleration applied automatically in potential evaluation
DL = helmholtz_potential.double_layer(spaceP, points, k)
SL = helmholtz_potential.single_layer(spaceU, points, k)
```

### H-Matrix Compression
H-matrices provide low-rank approximation for dense matrices:
- Reduces memory from O(N²) to O(N log N)
- Available in commercial BEM++ implementations

---

## 7. Output & Export

### Grid Function Visualization

**Jupyter Interactive Plotting:**
```python
# On-boundary solution
p_total.plot(transformation="abs")    # Magnitude
p_total.plot(transformation="real")   # Real part
p_total.plot(transformation="imag")   # Imaginary part
```

**External Visualization (Gmsh, VTK):**
```python
# Export to Gmsh format
grid.write("output.msh", output_format="gmsh")

# Export grid functions as data
coefficients = p_total.coefficients
```

### Potential Evaluation at Arbitrary Points

```python
# Define observation points
z = np.linspace(-0.2, 0.2, 41)
points = np.array([np.zeros_like(z), np.zeros_like(z), z])

# Evaluate total pressure
pZ = helmholtz_potential.double_layer(spaceP, points, k) * p_total \
     - 1j * omega * rho_0 * helmholtz_potential.single_layer(spaceU, points, k) * u_total
```

### Far-Field Calculations

**Directivity Patterns:**
```python
theta = np.linspace(0, 2 * np.pi, 361)
R_far = 5.0  # Far-field distance (meters)

# XZ plane (horizontal)
points_XZ = R_far * np.array([
    np.sin(theta), np.zeros_like(theta), np.cos(theta)
])

p_XZ = DL * p_total - 1j * omega * rho_0 * SL * u_total
SPL_XZ = 20 * np.log10(np.abs(p_XZ.ravel()) / (2e-5 * np.sqrt(2)))
```

**Polar Plots:**
```python
plt.polar(theta, SPL_XZ, label="Horizontal")
plt.polar(theta, SPL_YZ, label="Vertical")
plt.ylim(85, 150)
plt.show()
```

### SPL Computation

Sound Pressure Level in dB:
```python
p_ref = 2e-5 * np.sqrt(2)  # Reference pressure (Pa RMS)
SPL = 20 * np.log10(np.abs(pressure) / p_ref)
```

---

## 8. Advanced Features

### FEM-BEM Coupling with FEniCSx

For problems requiring volume discretization:

```python
import dolfinx
from bempp.api.external import fenicsx

# FEM mesh
mesh = dolfinx.UnitCubeMesh(MPI.COMM_WORLD, 10, 10, 10)
fenics_space = dolfinx.FunctionSpace(mesh, ("CG", 1))

# Trace space for coupling
trace_space, trace_matrix = \
    fenicsx.fenics_to_bempp_trace_data(fenics_space)

# Blocked operator system
A_fem = fenicsx.FenicsOperator((ufl.inner(ufl.grad(u), ufl.grad(v)) 
                                - k**2 * n**2 * ufl.inner(u, v)) * ufl.dx)
A_bem = (.5 * identity - double_layer).weak_form() * trace_op

blocked = BlockedDiscreteOperator(np.array([[A_fem, -trace_matrix.T * mass],
                                            [A_bem, single_layer]]))
```

**Use Cases:**
- Transmission problems through materials
- Multi-physics coupling (thermoacoustics, magneto-acoustics)
- Inhomogeneous media with spatially varying properties

### Blocked Operators for Multi-Physics

```python
from bempp.api.assembly.blocked_operator import BlockedDiscreteOperator

# 2x2 blocked system for FEM-BEM coupling
blocked_system = BlockedDiscreteOperator(np.array([
    [A_fem, B_coupling],
    [C_coupling, D_bem]
]))

solution = gmres(blocked_system, rhs_vector)
```

### Transmission Problems

Solving across material interfaces:

```python
# Interior (Ω): Δp + n²k²p = 0
# Exterior (ℝ³\Ω): Δp + k²p = 0
# Transmission: [p] = 0, [∂/∂n] = 0 on Γ

# FEM in interior, BEM in exterior
# Coupled via trace operators on boundary Γ
```

---

## 9. Version Capabilities (bempp-cl)

### bempp-cl Features

The `bempp-cl` version provides:

| Capability | Status |
|------------|--------|
| **Operators** | |
| Sparse identity | ✓ Implemented |
| Single layer (Helmholtz) | ✓ Implemented |
| Double layer (Helmholtz) | ✓ Implemented |
| Adjoint double layer | ✓ Implemented |
| Hypersingular | ✓ Implemented |
| **Potential Operators** | |
| Single layer potential | ✓ Implemented |
| Double layer potential | ✓ Implemented |
| Far-field operators | ✓ Implemented |
| **Solvers** | |
| GMRES iterative | ✓ Implemented |
| Direct LU solve | ✓ Via scipy.sparse |
| Preconditioning | ✓ Available |
| FMM acceleration | ✓ Automatic in potentials |
| **Function Spaces** | |
| P (Continuous) | ✓ Implemented |
| DP (Discontinuous) | ✓ Implemented |
| Raviart-Thomas | ✓ Implemented |
| **Input/Output** | |
| Gmsh import | ✓ `import_grid()` |
| VTK/VTU export | ✓ Supported |
| Grid visualization | ✓ `.plot()` |

### Limitations

1. **No built-in mesh generation** - Requires external tools (Gmsh)
2. **Single frequency at a time** - Must loop for multi-frequency analysis
3. **Memory-intensive for large problems** - Dense matrices O(N²)
4. **Complex preconditioning required for large k** - High frequencies need advanced preconditioners

---

## 10. Example: Complete Loudspeaker Radiation Workflow

```python
import bempp.api
from bempp.api.operators.boundary import helmholtz, sparse
from bempp.api.operators.potential import helmholtz as helmholtz_potential
from bempp.api.linalg import gmres
import numpy as np

# Parameters
rho_0 = 1.21      # kg/m³
c_0 = 343.0       # m/s
f = 1000.0        # Hz
omega = 2 * np.pi * f
k = omega / c_0
U_cone = 0.001    # m/s amplitude

# Import mesh
grid = bempp.api.import_grid("Loudspeaker.msh")
spaceP = bempp.api.function_space(grid, "P", 1)
spaceU = bempp.api.function_space(grid, "DP", 0, segments=[2])

# Boundary velocity with direction correction
@bempp.api.complex_callable
def u_total_callable(x, n, domain_index, result):
    if domain_index == 2:
        result[0] = n[2] * U_cone  # z-direction motion
    else:
        result[0] = 0.0

u_total = bempp.api.GridFunction(spaceU, fun=u_total_callable)

# Operators
I = sparse.identity(spaceP, spaceP, spaceP)
D = helmholtz.double_layer(spaceP, spaceP, spaceP, k)
S = helmholtz.single_layer(spaceU, spaceP, spaceP, k)

# Solve BIE: (D - 0.5 I) p = i ω ρ S u
rhs = 1j * omega * rho_0 * (S * u_total)
p_total, info = gmres(D - 0.5 * I, rhs, tol=1e-5)

# Far-field evaluation
theta = np.linspace(0, 2 * np.pi, 361)
R_far = 5.0
points = R_far * np.array([np.sin(theta), np.zeros_like(theta), np.cos(theta)])

p_far = helmholtz_potential.double_layer(spaceP, points, k) * p_total \
        - 1j * omega * rho_0 * helmholtz_potential.single_layer(spaceU, points, k) * u_total

SPL = 20 * np.log10(np.abs(p_far.ravel()) / (2e-5 * np.sqrt(2)))
```

---

## References & Resources

1. **BEMPP Documentation**: https://bempp-cl.readthedocs.io/
2. **BEMPP Acoustic Tutorials**: https://github.com/mscroggs/bempp-acoustic-tutorials
3. **Gmsh Manual**: https://gmsh.info/doc/texinfo/gmsh.html
4. **Boundary Element Methods** by S. A. Sauter and C. Schwab

---

*This document was compiled from research of the beminfo folder and BEMPP tutorial materials.*