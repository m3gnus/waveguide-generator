import sys
import os

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..', '..'))

sys.path.insert(0, os.path.join(REPO_ROOT, 'server'))

from server.solver.waveguide_builder import build_waveguide_mesh
from server.app import WaveguideParamsRequest

# Manually create the request for a fully closed model
payload_data = {
    "formula_type": "OSSE",
    "L": "100",
    "a": "45.0",
    "r0": 12.7,
    "a0": 15.0,
    "k": 2.0,
    "q": 3.4,
    "quadrants": 1234,  # Full circle
    "enc_depth": 50.0,
    "enc_edge": 10.0,
    "n_angular": 40,
    "n_length": 10,
}

request = WaveguideParamsRequest(**payload_data)

try:
    print(f"Building mesh with quadrants={request.quadrants}...")
    result = build_waveguide_mesh(request.model_dump(), include_canonical=True)
    print("Success! Closed mesh built.")
    print("Stats:", result["stats"])
except Exception as e:
    print("Failed!")
    import traceback
    traceback.print_exc()
