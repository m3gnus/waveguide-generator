import sys
import os
import argparse

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..', '..'))
OUTPUT_DIR = os.path.join(THIS_DIR, 'out')

sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'server'))

from contracts import WaveguideParamsRequest
from solver.mesher_adapter import build_waveguide_mesh

# Reference horn: freestanding R-OSSE with default parameters and 6mm wall thickness
payload_data = {
    "formula_type": "R-OSSE",
    "R": "140",
    "a": "25",
    "a0": 15.5,
    "r0": 12.7,
    "k": 2.0,
    "q": 3.4,
    "r": 0.4,
    "b": 0.2,
    "m": 0.85,
    "tmax": 1.0,
    "quadrants": 1234,
    "enc_depth": 0,
    "wall_thickness": 6.0,
    "n_angular": 100,
    "n_length": 20,
    "throat_res": 6.0,
    "mouth_res": 15.0,
    "rear_res": 40.0,
}

parser = argparse.ArgumentParser()
parser.add_argument("--quadrants", type=int, default=1234)
parser.add_argument("--output")
args = parser.parse_args()
payload_data["quadrants"] = args.quadrants
output_path = args.output or os.path.join(
    OUTPUT_DIR,
    (
        "test_reference_horn.msh"
        if args.quadrants == 1234
        else f"test_reference_horn_q{args.quadrants}.msh"
    ),
)
request = WaveguideParamsRequest(**payload_data)

try:
    print(f"Building mesh with quadrants={request.quadrants}...")
    result = build_waveguide_mesh(request.model_dump(), include_canonical=True)
    print("Success! Mesh built.")
    print("Stats:", result["stats"])

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(result["msh_text"])
    print(f"Saved to {output_path}")

except Exception as e:
    print("Failed!")
    import traceback
    traceback.print_exc()
