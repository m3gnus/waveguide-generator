# ATH Horn BEM Solver Backend

This is the Python backend for running BEM (Boundary Element Method) acoustic simulations using bempp-cl.

## Prerequisites

Before installing the BEM solver, you need to install the following prerequisites:

```bash
pip install plotly numpy scipy numba meshio>=4.0.16
```

## Installing Bempp

These instructions are for installing Bempp version 0.2.3, which is the recommended version for this project.

### Using pip

```bash
pip install git+https://github.com/bempp/bempp-cl.git
```

**Note:** While bempp-cl can be installed from PyPI (`pip install bempp-cl`) or conda-forge (`conda install bempp-cl`), these currently install version 0.1.0 which is missing features and bugfixes needed for this project. The git installation above ensures you get version 0.2.3.

## Installation

1. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
cd server
pip install -r requirements.txt
```

3. Install bempp-cl:
```bash
pip install git+https://github.com/bempp/bempp-cl.git
```

## Running the Server

Start the BEM solver backend:

```bash
python app.py
```

Or using uvicorn directly:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

The server will start on `http://localhost:8000`

## API Endpoints

### Health Check
```
GET /health
```
Returns server status.

### Submit Simulation
```
POST /api/solve
Content-Type: application/json

{
  "mesh": {
    "vertices": [...],
    "indices": [...],
    "format": "stl"
  },
  "frequency_range": [100, 10000],
  "num_frequencies": 50,
  "sim_type": "1",
  "options": {}
}
```

Returns: `{ "job_id": "uuid" }`

### Check Job Status
```
GET /api/status/{job_id}
```

Returns: `{ "status": "running|complete|error", "progress": 0.0-1.0 }`

### Get Results
```
GET /api/results/{job_id}
```

Returns simulation results including:
- Frequency response
- Directivity patterns (horizontal, vertical, diagonal)
- Impedance data
- SPL on-axis
- Directivity Index (DI)

## Troubleshooting

### OpenCL Issues

Bempp-cl requires OpenCL. If you encounter OpenCL-related errors:

1. **Check OpenCL availability:**
```python
import pyopencl as cl
print(cl.get_platforms())
```

2. **Install OpenCL drivers:**
   - **NVIDIA GPUs:** Install CUDA toolkit
   - **AMD GPUs:** Install ROCm or AMD APP SDK
   - **Intel CPUs/GPUs:** Install Intel OpenCL runtime
   - **macOS:** OpenCL is built-in

3. **CPU fallback:** If no GPU is available, bempp-cl can use CPU-based OpenCL implementations like PoCL.

### Import Errors

If you get import errors for bempp:

```bash
pip uninstall bempp-cl
pip install git+https://github.com/bempp/bempp-cl.git
```

### Memory Issues

For large meshes, you may need to increase available memory or reduce mesh density. The solver will estimate memory requirements before starting.

## Development

The backend consists of:

- `app.py` - FastAPI application and API endpoints
- `solver.py` - BEM solver implementation using bempp-cl
- `mesh_io.py` - Mesh import/export utilities

## Testing

Test the server is running:

```bash
curl http://localhost:8000/health
```

Expected response: `{"status": "ok", "solver": "bempp-cl"}`
