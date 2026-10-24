"""Where frozen mesh and STEP bytes are evidence.

Gmsh, OpenCASCADE and NumPy are not bit-reproducible across platforms. The
last bits of a coordinate depend on the operating system's libm and on the
SIMD paths a CPU selects, and Gmsh's meshing decisions follow those bits: the
same FREEFORM design meshes to 1116 nodes on macOS arm64 and to 1110 on one
Linux x86-64 host with identical package versions. A byte fixture therefore proves
compatibility only on the platform that froze it. There it is exact; anywhere
else a comparison would report the platform, not a change. Callers still check
what is portable there (a design still builds, a refusal stays the same
refusal, equivalent options give equal bytes) and skip only the byte identity.
"""

from __future__ import annotations

import platform


def current_platform() -> str:
    return f"{platform.system()}-{platform.machine()}"


def off_frozen_platform(reference: str) -> str | None:
    """Return the skip reason when this is not the platform that froze the bytes."""
    here = current_platform()
    if here == reference:
        return None
    return f"byte fixture frozen on {reference}; {here} meshes differ by platform"
