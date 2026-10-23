"""Translate the validated CAD mesh snapshot into WG's stored solve inputs."""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from server.cadlink.wglink_protocol import MESH_SIZING_FEATURE


@dataclass(frozen=True)
class SnapshotMesh:
    sizes: dict[str, Any]
    symmetry_mode: Literal["auto", "full"]


def snapshot_mesh(
    manifest: Mapping[str, Any], *, skipped_source_ids: Iterable[str] = (),
) -> SnapshotMesh | None:
    """Read a validated v1 snapshot; legacy returns contribute no settings.

    Validation belongs to the shared protocol at bundle ingress. Keep its
    source inventory complete unless the caller intentionally skips sources.
    """

    if MESH_SIZING_FEATURE not in manifest.get("required_features", ()):
        return None
    mesh = manifest["mesh"]
    skipped = set(skipped_source_ids)
    return SnapshotMesh(
        sizes={
            "rigid_size_mm": mesh["wall_size_mm"],
            "transition_mm": mesh["grading_distance_mm"],
            "source_size_mm": {
                source_id: size for source_id, size in mesh["source_size_mm"].items()
                if source_id not in skipped
            },
            "surface_groups": deepcopy(mesh["surface_groups"]),
        },
        symmetry_mode="full" if mesh["symmetry"] == "off" else "auto",
    )
