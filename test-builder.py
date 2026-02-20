import sys
import os
import json

sys.path.insert(0, os.path.abspath('server'))

from server.solver.waveguide_builder import build_waveguide_mesh
from server.app import WaveguideParamsRequest

# Manually create the request for Tritonia-M
payload_data = {
    "formula_type": "OSSE",
    "L": "135",
    "a": "45.0",  # Approximation for test
    "r0": 18.0,
    "a0": 10.0,
    "k": 2.1,
    "q": 0.992,
    "n": 3.7,
    "s": "0.7",
    "quadrants": 14,  # IMPORTANT: Half symmetry
    "enc_depth": 100.0,
    "enc_edge": 20.0,
    "n_angular": 80,
    "n_length": 20,
    "throat_res": 5.0,
    "mouth_res": 10.0
}

request = WaveguideParamsRequest(**payload_data)

try:
    print(f"Building mesh with quadrants={request.quadrants}...")
    result = build_waveguide_mesh(request.model_dump(), include_canonical=True)
    print("Success! Mesh built.")
    print("Stats:", result["stats"])
    
    # Save the msh to a file so we can inspect it
    with open('test_tritonia.msh', 'w') as f:
        f.write(result["msh_text"])
    print("Saved to test_tritonia.msh")
    
except Exception as e:
    print("Failed!")
    import traceback
    traceback.print_exc()
