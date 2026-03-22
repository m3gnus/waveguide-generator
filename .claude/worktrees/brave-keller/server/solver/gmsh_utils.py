from __future__ import annotations

import re
import shutil
import threading
from typing import Dict, Optional

from .deps import GMSH_AVAILABLE, gmsh

SUPPORTED_MSH_VERSIONS = {'2.2', '4.1'}
gmsh_lock = threading.Lock()


class GmshMeshingError(RuntimeError):
    pass


def gmsh_mesher_available() -> bool:
    if GMSH_AVAILABLE:
        return True
    return shutil.which('gmsh') is not None


def _parse_count_from_section(lines, section_name: str) -> int:
    marker = f'${section_name}'
    for i, line in enumerate(lines):
        if line.strip() != marker:
            continue
        if i + 1 >= len(lines):
            break
        tokens = lines[i + 1].strip().split()
        if not tokens:
            break
        # MSH2: single token count; MSH4: second token is total count.
        try:
            if len(tokens) == 1:
                return int(tokens[0])
            return int(tokens[1])
        except (ValueError, TypeError):
            return 0
    return 0


def parse_msh_stats(msh_text: str) -> Dict[str, int]:
    lines = msh_text.splitlines()
    return {
        'nodeCount': _parse_count_from_section(lines, 'Nodes'),
        'elementCount': _parse_count_from_section(lines, 'Elements')
    }
