import json
import sys
from server.solver.waveguide_builder import build_canonical_mesh_payload

with open('_references/testconfigs/tritonia.txt', 'r') as f:
    text = f.read()

# I need to parse the tritonia.txt in python. Wait, the frontend parses the config and sends the payload.
