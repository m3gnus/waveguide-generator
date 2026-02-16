#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This helper currently targets macOS (Darwin) only."
    exit 1
fi

ENV_PREFIX="${WG_OPENCL_ENV_PREFIX:-$HOME/.waveguide-generator/opencl-cpu-env}"
TOOLS_DIR="$HOME/.waveguide-generator/tools"
MICROMAMBA_BIN="$TOOLS_DIR/micromamba"

mkdir -p "$TOOLS_DIR"

if [ ! -x "$MICROMAMBA_BIN" ]; then
    echo "Downloading micromamba..."
    curl -sL "https://micro.mamba.pm/api/micromamba/osx-arm64/latest" \
        | tar -xvj -C "$TOOLS_DIR" --strip-components=1 bin/micromamba >/dev/null
    chmod +x "$MICROMAMBA_BIN"
fi

echo "Creating/updating OpenCL CPU environment at: $ENV_PREFIX"
"$MICROMAMBA_BIN" create -y -p "$ENV_PREFIX" -c conda-forge \
    python=3.13 pip pocl pyopencl numpy scipy numba meshio

echo "Installing backend Python dependencies..."
"$ENV_PREFIX/bin/python" -m pip install --upgrade pip setuptools wheel
"$ENV_PREFIX/bin/python" -m pip install -r "$ROOT_DIR/server/requirements.txt"
"$ENV_PREFIX/bin/python" -m pip install git+https://github.com/bempp/bempp-cl.git

echo "Validating OpenCL CPU runtime..."
"$ENV_PREFIX/bin/python" - <<'PY'
import pyopencl as cl
import bempp_cl.api as bempp
import numpy as np

platforms = cl.get_platforms()
if not platforms:
    raise SystemExit("No OpenCL platforms found.")

cpu_found = False
for platform in platforms:
    for device in platform.get_devices():
        if device.type & cl.device_type.CPU:
            cpu_found = True
            print(f"OpenCL CPU device: {device.name} (platform: {platform.name})")

if not cpu_found:
    raise SystemExit("No OpenCL CPU device found. bempp-cl OpenCL backend will not work.")

grid = bempp.shapes.regular_sphere(1)
space = bempp.function_space(grid, "P", 1)
k = 2 * np.pi * 1000.0 / 343.0
op = bempp.operators.boundary.helmholtz.single_layer(
    space, space, space, k, device_interface="opencl"
)
_ = bempp.as_matrix(op.weak_form())
print("bempp-cl OpenCL boundary assembly test: OK")
PY

echo
echo "Setup complete."
echo "Backend interpreter:"
echo "  $ENV_PREFIX/bin/python"
echo
echo "Use one of:"
echo "  WG_BACKEND_PYTHON=\"$ENV_PREFIX/bin/python\" npm start"
echo "  PYTHON_BIN=\"$ENV_PREFIX/bin/python\" npm start"
