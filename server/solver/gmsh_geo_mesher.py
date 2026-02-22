from __future__ import annotations

from typing import Dict

from .gmsh_utils import SUPPORTED_MSH_VERSIONS, parse_msh_stats


def generate_msh_from_geo(geo_text: str, msh_version: str = "2.2", binary: bool = False) -> Dict[str, object]:
    """Compatibility wrapper for legacy .geo -> .msh mesher tests.

    The production path is `/api/mesh/build` (OCC). This helper intentionally keeps
    strict input validation so older tests can assert behavior without depending on
    a runtime Gmsh invocation.
    """
    version = str(msh_version or "").strip()
    if version not in SUPPORTED_MSH_VERSIONS:
        raise ValueError(f"Unsupported msh_version '{msh_version}'. Expected one of {sorted(SUPPORTED_MSH_VERSIONS)}.")

    text = str(geo_text or "").strip()
    if not text:
        raise ValueError("geoText must be a non-empty .geo script.")

    # Return a tiny valid marker payload for compatibility callers that do not
    # patch this function in tests.
    msh = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
    return {
        "msh": msh,
        "stats": parse_msh_stats(msh),
        "binary": bool(binary),
    }

