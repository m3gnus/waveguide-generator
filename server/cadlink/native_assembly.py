"""Strict additive admission for a shared native horn/woofer scattering shell."""

import math

FEATURE = "native-shared-horn-woofer-v1"


def patch_area(contour, index):
    a, b = contour.points[index : index + 2]
    if contour.segments[index].kind == "line":
        return math.pi * (a.r_mm + b.r_mm) * math.hypot(b.r_mm - a.r_mm, b.z_mm - a.z_mm)
    radius, start, sweep = contour.arc(index)
    cr, _ = contour.segments[index].center_mm
    return (
        2
        * math.pi
        * radius
        * (
            cr * abs(sweep)
            + radius * math.copysign(1, sweep) * (math.sin(start + sweep) - math.sin(start))
        )
    )


def read_assembly(manifest):
    from hornlab_mesher.phase_plug import FEATURE as PLUG_FEATURE
    from hornlab_mesher.phase_plug import passage_contract, surface_targets
    from hornlab_mesher.source_assembly import SourceAssembly, assembly_channels
    from hornlab_mesher.source_contour import ContourDrive, digest

    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("unsupported native assembly version")
    assembly = SourceAssembly.from_dict(manifest["recipe"])
    if bool(assembly.phase_plugs) != (PLUG_FEATURE in manifest["required_features"]):
        raise ValueError("passive topology requires explicit phase-plug feature negotiation")
    if manifest.get("passage_contract") != passage_contract(assembly):
        raise ValueError("native passage contract contradicts canonical geometry")
    patches = manifest["patches"]
    expected = [
        (key, c.physical_source_id, c.segments[i].id, c.segments[i].role, band)
        for key, c, i, _, band in assembly.patches
    ]
    if [
        (p["id"], p["physical_source_id"], p["local_patch_id"], p["role"], p["band"])
        for p in patches
    ] != expected:
        raise ValueError("native assembly patch identity/role contradicts geometry")
    if len(manifest["channels"]) != 2:
        raise ValueError("native assembly requires two physical driver channels")
    drives = []
    for (c, _, _), channel in zip(assembly.parts, manifest["channels"]):
        weights = channel["patch_weights"]
        from hornlab_mesher.source_assembly import patch_id

        drives.append(
            ContourDrive(
                channel["id"],
                tuple((s.id, weights[patch_id(c, s)]) for s in c.segments if s.role == "moving"),
                channel["motion"],
            )
        )
    if assembly_channels(assembly, drives) != manifest["channels"]:
        raise ValueError("native assembly channel membership contradicts physical source")
    if (
        assembly.geometry_sha256 != manifest["geometry_sha256"]
        or digest(manifest["channels"]) != manifest["excitation_sha256"]
        or digest(manifest["density"]) != manifest["mesh_density_sha256"]
    ):
        raise ValueError("native assembly identity mismatch")
    rigid = manifest["rigid_faces"]
    areas = assembly.rigid_areas()
    if len(rigid) != len(areas) or {p["role"] for p in rigid} != set(areas):
        raise ValueError("native assembly rigid coverage mismatch")
    for p, (_, c, i, _, _) in zip(patches, assembly.patches):
        check_area(p["area_mm2"], patch_area(c, i))
    for p in rigid:
        check_area(p["area_mm2"], areas[p["role"]])
    owned = []
    for p in patches + rigid:
        indices = p["advanced_face_indices"]
        if len(indices) != 1 or type(indices[0]) is not int or indices[0] < 1:
            raise ValueError("native assembly selectors must be single positive face IDs")
        owned.extend(indices)
    all_faces, rigid_faces = manifest["all_face_indices"], manifest["rigid_face_indices"]
    moving = [p["advanced_face_indices"][0] for p in patches if p["role"] == "moving"]
    expected_rigid = [
        p["advanced_face_indices"][0] for p in rigid + patches if p.get("role") != "moving"
    ]
    if (
        len(set(owned)) != len(owned)
        or sorted(owned) != sorted(all_faces)
        or sorted(expected_rigid) != sorted(rigid_faces)
        or set(moving) & set(rigid_faces)
    ):
        raise ValueError("native assembly selectors overlap or leave gaps")
    density = manifest["density"]
    if assembly.phase_plugs and density.get("rigid_role_sizes_mm") != surface_targets(
        assembly, density["mesh_size_mm"], density.get("passage_refinement")
    ):
        raise ValueError("native passage density targets contradict its refinement recipe")
    if (
        type(density["triangle_limit"]) is not int
        or not 1 <= density["triangle_limit"] <= 250000
        or type(density["triangle_count"]) is not int
        or not 1 <= density["triangle_count"] <= density["triangle_limit"]
        or type(density["mesh_size_mm"]) not in (int, float)
        or not math.isfinite(density["mesh_size_mm"])
        or density["mesh_size_mm"] <= 0
    ):
        raise ValueError("native assembly density bounds are invalid")
    return assembly


def check_area(actual, expected):
    if (
        type(actual) not in (int, float)
        or not math.isfinite(actual)
        or actual <= 0
        or abs(actual - expected) > 1e-7 * max(1, expected)
    ):
        raise ValueError("native assembly face area contradicts canonical geometry")


def verify_faces(gmsh, manifest, assembly, mapped):
    import numpy as np
    from server.mesh.imported import _parametric_trim_mask

    if gmsh.model.getEntities(3):
        raise ValueError("native assembly STEP must be a surface shell")
    for p in manifest["patches"] + manifest["rigid_faces"]:
        face = mapped[p["advanced_face_indices"][0]]
        check_area(gmsh.model.occ.getMass(2, face), p["area_mm2"])
        lo, hi = gmsh.model.getParametrizationBounds(2, face)
        uv = np.asarray(
            [(u, v) for u in np.linspace(lo[0], hi[0], 9) for v in np.linspace(lo[1], hi[1], 9)]
        )
        uv = uv[_parametric_trim_mask(gmsh, face, uv.reshape(-1))]
        if not len(uv):
            raise ValueError("native assembly STEP face has no trimmed samples")
        xyz = np.asarray(gmsh.model.getValue(2, face, uv.reshape(-1))).reshape(-1, 3)
        distance = (
            assembly.patch_distance(p["id"], xyz)
            if "id" in p
            else assembly.rigid_distance(p["role"], xyz)
        )
        if distance.max() > 1e-6:
            raise ValueError("native assembly STEP selector contradicts finite canonical surface")
    # Reopened STEP must retain actual shared edges, not coincident duplicates.
    from hornlab_mesher.source_assembly import patch_id
    from hornlab_mesher.phase_plug import wall_edges

    by_id = {p["id"]: mapped[p["advanced_face_indices"][0]] for p in manifest["patches"]}
    by_role = {p["role"]: mapped[p["advanced_face_indices"][0]] for p in manifest["rigid_faces"]}
    for c, _, band in assembly.parts:
        chain = [by_id[patch_id(c, s)] for s in c.segments]
        if band == "HF":
            chain.extend(by_role[key] for key in wall_edges(assembly))
        elif "collar" in by_role:
            chain.append(by_role["collar"])
        chain.append(by_role["front"])
        edges = [
            {t for dim, t in gmsh.model.getBoundary([(2, f)], oriented=False) if dim == 1}
            for f in chain
        ]
        if any(not a & b for a, b in zip(edges, edges[1:])):
            raise ValueError("native assembly STEP source joins are disconnected")
    if assembly.phase_plugs:
        from server.mesh.imported import occ_body_groups

        groups = occ_body_groups(gmsh, gmsh.model.getEntities(2))
        if len(groups) != 1 + len(assembly.phase_plugs):
            raise ValueError("native passage STEP component inventory contradicts recipe")
        owned = []
        for plug in assembly.phase_plugs:
            faces = {by_role[key] for key in plug.edges}
            matches = [g for g in groups if {t for dim, t in g if dim == 2} == faces]
            if len(matches) != 1:
                raise ValueError(
                    "native passage STEP body is disconnected or joined to another shell"
                )
            owned.extend(faces)
        if len(set(owned)) != len(owned):
            raise ValueError("native passive STEP faces overlap")
