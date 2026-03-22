"""
Mesh extraction and validation utilities for waveguide canonical meshes.

Extracted from waveguide_builder.py — these functions handle extracting
triangle data from Gmsh models, building surface identity maps, and
orienting/validating canonical mesh topology.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .deps import gmsh
from .gmsh_utils import GmshMeshingError

logger = logging.getLogger(__name__)


def _extract_triangle_block(
    elem_types: List[int], elem_tags: List[List[int]], elem_nodes: List[List[int]]
) -> Tuple[List[int], List[int]]:
    for i, etype in enumerate(elem_types):
        if int(etype) == 2:
            return [int(tag) for tag in elem_tags[i]], [int(node) for node in elem_nodes[i]]
    return [], []


def _build_surface_identity_by_entity(
    surface_groups: Dict[str, List[int]],
    *,
    has_enclosure: bool,
) -> Dict[int, str]:
    """Map gmsh surface entities to stable frontend face-identity keys."""
    group_to_identity = {
        "throat_disc": "throat_disc",
    }
    if has_enclosure:
        group_to_identity.update(
            {
                "inner": "horn_wall",
                "enclosure_front": "enc_front",
                "enclosure_back": "enc_rear",
                "enclosure_sides": "enc_side",
                "enclosure_edges": "enc_edge",
            }
        )
    else:
        group_to_identity.update(
            {
                "inner": "inner_wall",
                "outer": "outer_wall",
                "throat_return": "throat_return",
                "rear_cap": "rear_cap",
                "mouth": "mouth_rim",
            }
        )

    surface_identity_by_entity: Dict[int, str] = {}
    for group_name, identity in group_to_identity.items():
        for entity_tag in surface_groups.get(group_name, []):
            surface_identity_by_entity[int(entity_tag)] = identity
    return surface_identity_by_entity


def _count_triangle_identities(triangle_identities: List[Any]) -> Dict[str, int]:
    from .waveguide_builder import FACE_IDENTITY_ORDER

    counts = {identity: 0 for identity in FACE_IDENTITY_ORDER}
    for raw_identity in triangle_identities:
        identity = str(raw_identity or "").strip()
        if identity in counts:
            counts[identity] += 1
    return counts


def _extract_canonical_mesh_from_model(
    default_surface_tag: int = 1,
    *,
    surface_identity_by_entity: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """Extract flat canonical mesh arrays from current gmsh model."""
    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    if len(node_tags) == 0 or len(node_coords) == 0:
        return {"vertices": [], "indices": [], "surfaceTags": []}

    node_tags_i = [int(tag) for tag in node_tags]
    node_coords_f = [float(v) for v in node_coords]
    node_to_index = {tag: i for i, tag in enumerate(node_tags_i)}

    tri_elem_types_all, tri_elem_tags_all, tri_elem_nodes_all = gmsh.model.mesh.getElements(2)
    tri_tags, tri_nodes = _extract_triangle_block(tri_elem_types_all, tri_elem_tags_all, tri_elem_nodes_all)
    if not tri_tags:
        return {"vertices": node_coords_f, "indices": [], "surfaceTags": []}
    if len(tri_nodes) != len(tri_tags) * 3:
        raise GmshMeshingError("Unexpected triangle node buffer size while extracting canonical mesh.")

    element_surface_tag: Dict[int, int] = {}
    element_identity: Dict[int, str] = {}
    for _, physical_tag in gmsh.model.getPhysicalGroups(2):
        entities = gmsh.model.getEntitiesForPhysicalGroup(2, physical_tag)
        for entity in entities:
            entity_i = int(entity)
            identity = (
                surface_identity_by_entity.get(entity_i)
                if isinstance(surface_identity_by_entity, dict)
                else None
            )
            etypes_e, etags_e, enodes_e = gmsh.model.mesh.getElements(2, entity)
            tri_tags_e, _ = _extract_triangle_block(etypes_e, etags_e, enodes_e)
            for elem_tag in tri_tags_e:
                elem_tag_i = int(elem_tag)
                element_surface_tag[elem_tag_i] = int(physical_tag)
                if identity:
                    element_identity[elem_tag_i] = identity

    indices: List[int] = []
    surface_tags: List[int] = []
    element_tags: List[int] = []
    triangle_identities: List[Optional[str]] = []
    for tri_idx, elem_tag in enumerate(tri_tags):
        n0 = tri_nodes[tri_idx * 3]
        n1 = tri_nodes[tri_idx * 3 + 1]
        n2 = tri_nodes[tri_idx * 3 + 2]
        try:
            indices.extend([
                node_to_index[int(n0)],
                node_to_index[int(n1)],
                node_to_index[int(n2)],
            ])
        except KeyError as exc:
            raise GmshMeshingError(
                f"Missing node mapping for triangle element tag {elem_tag}."
            ) from exc
        surface_tags.append(int(element_surface_tag.get(int(elem_tag), default_surface_tag)))
        element_tags.append(int(elem_tag))
        triangle_identities.append(element_identity.get(int(elem_tag)))

    # Compact: strip vertices not referenced by any triangle.
    # After a symmetry cut, getNodes() returns orphan nodes from curves on
    # the removed side.  Keeping them inflates the vertex array and can
    # confuse downstream consumers (e.g. symmetry-plane guards).
    used_indices = set(indices)
    n_total = len(node_tags_i)
    if len(used_indices) < n_total:
        old_to_new_idx = {}
        compacted_coords: List[float] = []
        new_idx = 0
        for old_idx in range(n_total):
            if old_idx in used_indices:
                old_to_new_idx[old_idx] = new_idx
                compacted_coords.extend(node_coords_f[old_idx * 3 : old_idx * 3 + 3])
                new_idx += 1
        indices = [old_to_new_idx[i] for i in indices]
        node_coords_f = compacted_coords

    canonical_mesh: Dict[str, Any] = {
        "vertices": node_coords_f,
        "indices": indices,
        "surfaceTags": surface_tags,
        "elementTags": element_tags,
    }
    if triangle_identities:
        canonical_mesh["triangleIdentities"] = triangle_identities
    return canonical_mesh


def _orient_and_validate_canonical_mesh(
    canonical_mesh: Dict[str, List[float]],
    *,
    require_watertight: bool,
    require_single_boundary_loop: bool,
    allow_tagged_loop_bridge: bool = False,
    flip_surface_tags: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """Orient canonical triangles consistently and validate topology."""
    vertices = canonical_mesh.get("vertices", [])
    indices = list(canonical_mesh.get("indices", []))
    surface_tags = list(canonical_mesh.get("surfaceTags", []))
    element_tags = list(canonical_mesh.get("elementTags", []))
    triangle_identities = list(canonical_mesh.get("triangleIdentities", []))

    if len(vertices) % 3 != 0:
        raise GmshMeshingError("Canonical mesh has invalid vertex buffer length.")
    if len(indices) % 3 != 0:
        raise GmshMeshingError("Canonical mesh has invalid triangle index buffer length.")

    vertex_count = len(vertices) // 3
    tri_count = len(indices) // 3
    if tri_count == 0:
        result = {"vertices": vertices, "indices": indices, "surfaceTags": surface_tags}
        if triangle_identities:
            result["triangleIdentities"] = triangle_identities
        return result

    # OCC seams can retain numerically-near duplicate nodes after meshing.
    # Weld by position before topology checks so connectivity/watertightness
    # are evaluated on the effective simulation mesh.
    weld_tol = 1e-6
    coords = np.asarray(vertices, dtype=float).reshape((-1, 3))
    quantized = np.round(coords / weld_tol).astype(np.int64)
    key_to_new: Dict[Tuple[int, int, int], int] = {}
    old_to_new = np.empty(vertex_count, dtype=np.int64)
    welded_points: List[np.ndarray] = []
    for old_idx, key_arr in enumerate(quantized):
        key = (int(key_arr[0]), int(key_arr[1]), int(key_arr[2]))
        mapped = key_to_new.get(key)
        if mapped is None:
            mapped = len(welded_points)
            key_to_new[key] = mapped
            welded_points.append(coords[old_idx])
        old_to_new[old_idx] = mapped

    welded_indices: List[int] = []
    welded_surface_tags: List[int] = []
    welded_element_tags: List[int] = []
    welded_triangle_identities: List[Optional[str]] = []
    for tri_idx in range(tri_count):
        a = int(old_to_new[int(indices[tri_idx * 3])])
        b = int(old_to_new[int(indices[tri_idx * 3 + 1])])
        c = int(old_to_new[int(indices[tri_idx * 3 + 2])])
        if a == b or b == c or c == a:
            continue
        welded_indices.extend([a, b, c])
        welded_surface_tags.append(int(surface_tags[tri_idx]))
        if element_tags:
            welded_element_tags.append(int(element_tags[tri_idx]))
        if triangle_identities:
            welded_triangle_identities.append(triangle_identities[tri_idx])

    vertices = np.asarray(welded_points, dtype=float).reshape(-1).tolist()
    indices = welded_indices
    surface_tags = welded_surface_tags
    element_tags = welded_element_tags
    triangle_identities = welded_triangle_identities
    vertex_count = len(vertices) // 3
    tri_count = len(indices) // 3
    if tri_count == 0:
        raise GmshMeshingError("Canonical mesh collapsed after node welding.")
    # Tracks element tags that should be flipped in the underlying Gmsh model.
    flipped_mask = np.zeros(tri_count, dtype=bool)

    def build_edge_uses(
        in_indices: List[int],
        in_vertex_count: int,
    ) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
        out: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        in_tri_count = len(in_indices) // 3
        for tri_idx_local in range(in_tri_count):
            a = int(in_indices[tri_idx_local * 3])
            b = int(in_indices[tri_idx_local * 3 + 1])
            c = int(in_indices[tri_idx_local * 3 + 2])
            if (
                a < 0 or b < 0 or c < 0
                or a >= in_vertex_count or b >= in_vertex_count or c >= in_vertex_count
            ):
                raise GmshMeshingError(
                    f"Canonical mesh triangle {tri_idx_local} references out-of-range vertex indices."
                )
            if a == b or b == c or c == a:
                raise GmshMeshingError(f"Canonical mesh triangle {tri_idx_local} is degenerate.")
            for u, v in ((a, b), (b, c), (c, a)):
                lo, hi = (u, v) if u < v else (v, u)
                direction = 1 if (u == lo and v == hi) else -1
                out.setdefault((lo, hi), []).append((tri_idx_local, direction))
        return out

    def extract_boundary_loops_with_tags(
        in_edge_uses: Dict[Tuple[int, int], List[Tuple[int, int]]],
        in_surface_tags: List[int],
    ) -> List[Tuple[List[int], int]]:
        boundary_adj: Dict[int, List[int]] = {}
        boundary_edge_tag: Dict[Tuple[int, int], int] = {}
        for (u, v), uses in in_edge_uses.items():
            if len(uses) != 1:
                continue
            tri_idx_local = int(uses[0][0])
            tri_tag = int(in_surface_tags[tri_idx_local]) if tri_idx_local < len(in_surface_tags) else 1
            boundary_adj.setdefault(int(u), []).append(int(v))
            boundary_adj.setdefault(int(v), []).append(int(u))
            boundary_edge_tag[(int(u), int(v)) if u < v else (int(v), int(u))] = tri_tag

        loops: List[Tuple[List[int], int]] = []
        visited_edges: Set[Tuple[int, int]] = set()
        for start, neighbors in boundary_adj.items():
            for nb in neighbors:
                e0 = (start, nb) if start < nb else (nb, start)
                if e0 in visited_edges:
                    continue

                loop: List[int] = [int(start)]
                prev = -1
                cur = int(start)
                while True:
                    nbrs = boundary_adj.get(cur, [])
                    if len(nbrs) == 0:
                        break
                    if len(nbrs) == 1:
                        nxt = int(nbrs[0])
                    else:
                        nxt = int(nbrs[0] if nbrs[0] != prev else nbrs[1])
                    ek = (cur, nxt) if cur < nxt else (nxt, cur)
                    if ek in visited_edges and nxt == start:
                        break
                    visited_edges.add(ek)
                    prev, cur = cur, nxt
                    if cur == start:
                        break
                    loop.append(cur)

                if len(loop) < 3:
                    continue

                tag_counts: Dict[int, int] = {}
                m = len(loop)
                for i in range(m):
                    u = int(loop[i])
                    v = int(loop[(i + 1) % m])
                    ek = (u, v) if u < v else (v, u)
                    tag_i = int(boundary_edge_tag.get(ek, 1))
                    tag_counts[tag_i] = int(tag_counts.get(tag_i, 0) + 1)
                dominant_tag = max(tag_counts.items(), key=lambda kv: kv[1])[0]
                loops.append((loop, int(dominant_tag)))
        return loops

    def stitch_tagged_boundary_loops(
        in_vertices: List[float],
        in_indices: List[int],
        in_surface_tags: List[int],
        in_edge_uses: Dict[Tuple[int, int], List[Tuple[int, int]]],
        *,
        tag_a: int,
        tag_b: int,
        bridge_tag: int,
    ) -> List[int]:
        loops_with_tags = extract_boundary_loops_with_tags(in_edge_uses, in_surface_tags)
        loop_a: Optional[List[int]] = None
        loop_b: Optional[List[int]] = None

        if int(tag_a) == int(tag_b):
            same_tag_loops = sorted(
                [loop for loop, tag in loops_with_tags if tag == int(tag_a)],
                key=len,
                reverse=True,
            )
            if len(same_tag_loops) >= 2:
                loop_a = list(same_tag_loops[0])
                loop_b = list(same_tag_loops[1])
        else:
            loops_a = [loop for loop, tag in loops_with_tags if tag == int(tag_a)]
            loops_b = [loop for loop, tag in loops_with_tags if tag == int(tag_b)]
            if len(loops_a) > 0 and len(loops_b) > 0:
                loop_a = list(max(loops_a, key=len))
                loop_b = list(max(loops_b, key=len))

        # Fallback: if tags do not split loops cleanly, connect the two largest loops.
        if loop_a is None or loop_b is None:
            all_loops = sorted([list(loop) for loop, _ in loops_with_tags], key=len, reverse=True)
            if len(all_loops) < 2:
                return False
            loop_a, loop_b = all_loops[0], all_loops[1]

        if len(loop_a) < 3 or len(loop_b) < 3:
            return False

        xyz = np.asarray(in_vertices, dtype=float).reshape((-1, 3))

        a0 = xyz[int(loop_a[0])]
        dists = [float(np.linalg.norm(xyz[int(vb)] - a0)) for vb in loop_b]
        if len(dists) == 0:
            return False
        start_b = int(np.argmin(np.asarray(dists, dtype=float)))
        loop_b = loop_b[start_b:] + loop_b[:start_b]

        def alignment_cost(candidate_b: List[int]) -> float:
            m = len(loop_a)
            n = len(candidate_b)
            if n == 0:
                return float("inf")
            total = 0.0
            for k in range(m):
                idx_b = int(round((k * n) / max(m, 1))) % n
                total += float(np.linalg.norm(xyz[int(loop_a[k])] - xyz[int(candidate_b[idx_b])]))
            return total

        rev_b = [loop_b[0]] + list(reversed(loop_b[1:])) if len(loop_b) > 1 else list(loop_b)
        if alignment_cost(rev_b) < alignment_cost(loop_b):
            loop_b = rev_b

        m = len(loop_a)
        n = len(loop_b)
        i = 0
        j = 0
        added = 0
        while i < m or j < n:
            can_i = i < m
            can_j = j < n
            if not can_i and not can_j:
                break

            ai = int(loop_a[i % m])
            bi = int(loop_b[j % n])
            a_next = int(loop_a[(i + 1) % m])
            b_next = int(loop_b[(j + 1) % n])

            if can_i and not can_j:
                tri = (ai, a_next, bi)
                i += 1
            elif can_j and not can_i:
                tri = (ai, b_next, bi)
                j += 1
            else:
                da = float(np.linalg.norm(xyz[a_next] - xyz[bi]))
                db = float(np.linalg.norm(xyz[ai] - xyz[b_next]))
                if da <= db:
                    tri = (ai, a_next, bi)
                    i += 1
                else:
                    tri = (ai, b_next, bi)
                    j += 1

            if len({int(tri[0]), int(tri[1]), int(tri[2])}) != 3:
                continue
            in_indices.extend([int(tri[0]), int(tri[1]), int(tri[2])])
            in_surface_tags.append(int(bridge_tag))
            added += 1

        return [added]  # Legacy placeholder

    edge_uses = build_edge_uses(indices, vertex_count)
    if require_watertight and allow_tagged_loop_bridge:
        bridge_result = stitch_tagged_boundary_loops(
            vertices,
            indices,
            surface_tags,
            edge_uses,
            tag_a=1,
            tag_b=1,
            bridge_tag=1,
        )
        if bridge_result:
            tri_count = len(indices) // 3
            # If we stitch, the new triangles don't have element tags matching
            # the Gmsh model yet, so we don't track them in flipped_mask for reverseElements.
            old_mask_len = len(flipped_mask)
            if tri_count > old_mask_len:
                flipped_mask = np.append(flipped_mask, np.zeros(tri_count - old_mask_len, dtype=bool))
            if triangle_identities:
                triangle_identities.extend([None] * int(bridge_result[0]))
            edge_uses = build_edge_uses(indices, vertex_count)

    adjacency: Dict[int, List[Tuple[int, int]]] = {i: [] for i in range(tri_count)}
    boundary_edges = 0
    non_manifold_edges = 0
    for uses in edge_uses.values():
        if len(uses) == 1:
            boundary_edges += 1
            continue
        if len(uses) > 2:
            non_manifold_edges += 1
            continue
        (t0, d0), (t1, d1) = uses
        flip_needed = 1 if d0 == d1 else 0
        adjacency[t0].append((t1, flip_needed))
        adjacency[t1].append((t0, flip_needed))

    if non_manifold_edges > 0:
        raise GmshMeshingError(
            f"Canonical mesh is non-manifold ({non_manifold_edges} edges shared by >2 triangles)."
        )
    if require_watertight and boundary_edges > 0:
        raise GmshMeshingError(
            f"Canonical mesh is not watertight ({boundary_edges} boundary edges)."
        )
    if require_single_boundary_loop and boundary_edges > 0:
        boundary_adj: Dict[int, Set[int]] = {}
        for (u, v), uses in edge_uses.items():
            if len(uses) != 1:
                continue
            boundary_adj.setdefault(int(u), set()).add(int(v))
            boundary_adj.setdefault(int(v), set()).add(int(u))
        if not boundary_adj:
            raise GmshMeshingError("Canonical mesh boundary loop analysis failed: no boundary adjacency.")
        degree_errors = [vid for vid, nbrs in boundary_adj.items() if len(nbrs) != 2]
        if degree_errors:
            raise GmshMeshingError(
                "Canonical mesh has cracked aperture boundary (boundary vertices not degree-2)."
            )
        start = next(iter(boundary_adj))
        visited_v: Set[int] = set()
        stack_v = [start]
        while stack_v:
            vid = stack_v.pop()
            if vid in visited_v:
                continue
            visited_v.add(vid)
            stack_v.extend(n for n in boundary_adj[vid] if n not in visited_v)
        if len(visited_v) != len(boundary_adj):
            raise GmshMeshingError(
                "Canonical mesh has multiple disjoint aperture boundaries; expected one continuous loop."
            )

    flips = [-1] * tri_count
    component_count = 0
    for start in range(tri_count):
        if flips[start] != -1:
            continue
        component_count += 1
        flips[start] = 0
        stack = [start]
        while stack:
            tri = stack.pop()
            tri_flip = flips[tri]
            for nbr, flip_needed in adjacency.get(tri, []):
                expected = tri_flip ^ flip_needed
                if flips[nbr] == -1:
                    flips[nbr] = expected
                    stack.append(nbr)
                elif flips[nbr] != expected:
                    raise GmshMeshingError(
                        "Canonical mesh has inconsistent triangle winding constraints."
                    )

    if component_count != 1:
        logger.warning("[MWG] Canonical mesh has %d disconnected components.", component_count)
    if require_watertight and component_count != 1:
        raise GmshMeshingError(
            f"Canonical mesh is disconnected ({component_count} triangle components)."
        )

    for tri_idx, flip in enumerate(flips):
        if flip != 1:
            continue
        i0 = tri_idx * 3
        indices[i0 + 1], indices[i0 + 2] = indices[i0 + 2], indices[i0 + 1]
        flipped_mask[tri_idx] = not flipped_mask[tri_idx]

    if require_watertight:
        coords = np.asarray(vertices, dtype=float).reshape((-1, 3))
        signed_six_volume = 0.0
        for tri_idx in range(tri_count):
            i0 = tri_idx * 3
            p0 = coords[int(indices[i0])]
            p1 = coords[int(indices[i0 + 1])]
            p2 = coords[int(indices[i0 + 2])]
            signed_six_volume += float(np.dot(p0, np.cross(p1, p2)))

        if not math.isfinite(signed_six_volume) or abs(signed_six_volume) <= 1e-12:
            raise GmshMeshingError("Canonical mesh has invalid enclosed volume for outward orientation.")
        if signed_six_volume < 0.0:
            for tri_idx in range(tri_count):
                i0 = tri_idx * 3
                indices[i0 + 1], indices[i0 + 2] = indices[i0 + 2], indices[i0 + 1]
                flipped_mask[tri_idx] = not flipped_mask[tri_idx]
    else:
        coords = np.asarray(vertices, dtype=float).reshape((-1, 3))
        center = np.mean(coords, axis=0)
        outward_score = 0.0
        for tri_idx in range(tri_count):
            i0 = tri_idx * 3
            p0 = coords[int(indices[i0])]
            p1 = coords[int(indices[i0 + 1])]
            p2 = coords[int(indices[i0 + 2])]
            tri_normal = np.cross(p1 - p0, p2 - p0)
            tri_centroid = (p0 + p1 + p2) / 3.0
            outward_score += float(np.dot(tri_normal, tri_centroid - center))
        if math.isfinite(outward_score) and outward_score < 0.0:
            for tri_idx in range(tri_count):
                i0 = tri_idx * 3
                indices[i0 + 1], indices[i0 + 2] = indices[i0 + 2], indices[i0 + 1]
                flipped_mask[tri_idx] = not flipped_mask[tri_idx]

    if flip_surface_tags:
        flip_tags = {int(tag) for tag in flip_surface_tags}
        for tri_idx in range(tri_count):
            if int(surface_tags[tri_idx]) not in flip_tags:
                continue
            i0 = tri_idx * 3
            indices[i0 + 1], indices[i0 + 2] = indices[i0 + 2], indices[i0 + 1]
            flipped_mask[tri_idx] = not flipped_mask[tri_idx]

    flipped_element_tags = []
    if element_tags:
        # We only return tags that exist in the original Gmsh model.
        # Stitched triangles (if any) won't have matching tags.
        for tri_idx, is_flipped in enumerate(flipped_mask):
            if is_flipped and tri_idx < len(element_tags):
                flipped_element_tags.append(element_tags[tri_idx])

    result = {
        "vertices": vertices,
        "indices": indices,
        "surfaceTags": surface_tags,
        "flippedElementTags": flipped_element_tags,
    }
    if triangle_identities:
        result["triangleIdentities"] = triangle_identities
    return result
