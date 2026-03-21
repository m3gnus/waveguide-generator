#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PREFERRED_PYTHON_FILE="$ROOT_DIR/.waveguide/backend-python.path"

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

# Patch bempp-cl's get_vector_width to handle non-standard native widths.
# pocl on Apple Silicon reports native_vector_width_float=2, which is not in
# bempp-cl's supported set {1,4,8,16}.  Clamp to the nearest valid width.
echo "Applying vector-width compatibility patch to bempp-cl..."
"$ENV_PREFIX/bin/python" - <<'PATCH'
import importlib, pathlib, re, sys

spec = importlib.util.find_spec("bempp_cl.core.opencl_kernels")
if spec is None or spec.origin is None:
    print("  bempp_cl.core.opencl_kernels not found — skipping patch.")
    sys.exit(0)

target = pathlib.Path(spec.origin)
src = target.read_text()

if "_SUPPORTED_VECTOR_WIDTHS" in src:
    print("  Patch already applied.")
    sys.exit(0)

# Insert the constant and rewrite get_vector_width's auto branch
old_fn = (
    'def get_vector_width(precision, device_type="cpu"):\n'
    '    """Return vector width."""\n'
    '    import bempp_cl.api\n'
    '\n'
    '    mode_to_length = {"novec": 1, "vec4": 4, "vec8": 8, "vec16": 16}\n'
    '\n'
    '    if device_type == "gpu":\n'
    '        return 1\n'
    '    if bempp_cl.api.VECTORIZATION_MODE == "auto":\n'
    '        return get_native_vector_width(default_device(device_type), precision)\n'
    '    else:\n'
    '        return mode_to_length[bempp_cl.api.VECTORIZATION_MODE]'
)

new_fn = (
    '_SUPPORTED_VECTOR_WIDTHS = {1, 4, 8, 16}\n'
    '\n'
    '\n'
    'def get_vector_width(precision, device_type="cpu"):\n'
    '    """Return vector width."""\n'
    '    import bempp_cl.api\n'
    '\n'
    '    mode_to_length = {"novec": 1, "vec4": 4, "vec8": 8, "vec16": 16}\n'
    '\n'
    '    if device_type == "gpu":\n'
    '        return 1\n'
    '    if bempp_cl.api.VECTORIZATION_MODE == "auto":\n'
    '        native = get_native_vector_width(default_device(device_type), precision)\n'
    '        if native not in _SUPPORTED_VECTOR_WIDTHS:\n'
    '            # Clamp unsupported widths (e.g. pocl reports 2 on Apple Silicon)\n'
    '            # to the largest supported width that does not exceed native width.\n'
    '            return max((w for w in _SUPPORTED_VECTOR_WIDTHS if w <= native), default=1)\n'
    '        return native\n'
    '    else:\n'
    '        return mode_to_length[bempp_cl.api.VECTORIZATION_MODE]'
)

if old_fn not in src:
    print("  WARNING: Could not locate unpatched get_vector_width — bempp-cl may have changed.")
    print("           Verify manually:", target)
    sys.exit(1)

target.write_text(src.replace(old_fn, new_fn))
print("  Patched:", target)
PATCH

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

echo "Recording backend interpreter contract..."
mkdir -p "$ROOT_DIR/.waveguide"
printf '%s\n' "$ENV_PREFIX/bin/python" > "$PREFERRED_PYTHON_FILE"
echo "  Marker file: $PREFERRED_PYTHON_FILE"

echo
echo "Running backend dependency preflight..."
if node "$ROOT_DIR/scripts/preflight-backend-runtime.js" --strict; then
    echo "  Backend preflight: required checks ready."
else
    echo "  WARNING: Backend preflight detected missing/unsupported required checks."
    echo "           Fix the reported items, then re-run:"
    echo "             npm run preflight:backend:strict"
fi

echo
echo "Setup complete."
echo "Backend interpreter:"
echo "  $ENV_PREFIX/bin/python"
echo
echo "npm start and server/start.sh will now use the marker above by default."
echo "Optional overrides:"
echo "  WG_BACKEND_PYTHON=\"$ENV_PREFIX/bin/python\" npm start"
echo "  PYTHON_BIN=\"$ENV_PREFIX/bin/python\" npm start"
