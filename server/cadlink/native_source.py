"""Native source ingestion via the general imported STEP mesher.

This adapter authors its own evidence; it never impersonates a CAD return or
weakens linked_throat. Patch identity is independent of temporary CAD selectors.
"""

import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

FEATURE = "native-source-contour-v1"
BAFFLE_FEATURE = "native-front-baffle-woofer-v1"
ASSEMBLY_FEATURE = "native-shared-horn-woofer-v1"
PLUG_FEATURE = "native-phase-plug-passages-v1"
GENERAL_FEATURE = "native-general-horn-attachment-v1"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read_native_source(directory):
    from hornlab_mesher.source_contour import SourceContour, ContourDrive, digest

    directory = Path(directory)
    raw = (directory / "source.json").read_bytes()

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("native source metadata contains duplicate keys")
            result[key] = value
        return result

    manifest = json.loads(raw, object_pairs_hook=unique_pairs)
    if (
        manifest.get("version") != 1
        or manifest.get("required_features")
        not in (
            [FEATURE],
            [FEATURE, BAFFLE_FEATURE],
            [FEATURE, ASSEMBLY_FEATURE],
            [FEATURE, ASSEMBLY_FEATURE, PLUG_FEATURE],
            [FEATURE, ASSEMBLY_FEATURE, GENERAL_FEATURE],
            [FEATURE, ASSEMBLY_FEATURE, PLUG_FEATURE, GENERAL_FEATURE],
        )
        or manifest.get("producer") != "native"
        or manifest.get("units") != "mm"
    ):
        raise ValueError("unsupported native source version, required feature, producer or units")
    if set(manifest["members"]) != {"geometry.step", "preview.msh"}:
        raise ValueError("native source members must be the exact supported artifacts")
    for name, expected in manifest["members"].items():
        if _digest((directory / name).read_bytes()) != expected:
            raise ValueError(f"tampered or stale native source member {name}")
    recipe = manifest["recipe"]
    if ASSEMBLY_FEATURE in manifest["required_features"]:
        from .native_assembly import read_assembly

        return manifest, read_assembly(manifest), None, _digest(raw)
    contour = SourceContour.from_dict(recipe["contour"])
    drive = ContourDrive(**manifest["drive"])
    drive.validate(contour)
    woofer = BAFFLE_FEATURE in manifest["required_features"]
    if recipe["frame"] != "aligned-z-mm-v1":
        raise ValueError("native source attachment/frame mismatch")
    if woofer:
        from hornlab_mesher.front_baffle import FrontBaffle

        if set(recipe) != {"contour", "baffle", "frame"}:
            raise ValueError("native woofer recipe must declare only contour, baffle and frame")
        baffle = FrontBaffle.from_dict(recipe["baffle"])
        baffle.validate(contour)
        specs = baffle.surfaces(contour)
        declared = manifest["baffle_faces"]
        if len(declared) != len(specs) or {f["role"] for f in declared} != set(specs):
            raise ValueError("native baffle rigid roles must exactly cover the enclosure")
        extra_faces = [
            f["advanced_face_indices"][0] for f in declared if len(f["advanced_face_indices"]) == 1
        ]
        if len(extra_faces) != len(specs) or len(set(extra_faces)) != len(extra_faces):
            raise ValueError("native baffle selectors must be unique single faces")
        for f in declared:
            area = f["area_mm2"]
            expected = specs[f["role"]][-1]
            if (
                type(area) not in (int, float)
                or not math.isfinite(area)
                or abs(area - expected) > 1e-7 * max(1, expected)
            ):
                raise ValueError("native baffle area contradicts canonical geometry")
    elif (
        "horn" not in recipe
        or "baffle" in recipe
        or recipe["horn"]["rim_id"] != contour.rim_id
        or recipe["horn"]["radius_mm"] != contour.points[-1].r_mm
    ):
        raise ValueError("native source attachment/frame mismatch")
    if (
        digest(recipe) != manifest["geometry_sha256"]
        or drive.excitation_sha256 != manifest["excitation_sha256"]
        or digest(manifest["density"]) != manifest["mesh_density_sha256"]
    ):
        raise ValueError("native source identity mismatch")
    patches = manifest["patches"]
    if any(
        not isinstance(p.get("area_mm2"), (int, float))
        or not math.isfinite(p["area_mm2"])
        or p["area_mm2"] <= 0
        for p in patches
    ):
        raise ValueError("native patch areas must be finite and positive")
    if [(p["id"], p["role"]) for p in patches] != [(s.id, s.role) for s in contour.segments]:
        raise ValueError("native patch identities or roles contradict geometry")
    owned = [face for p in patches for face in p["advanced_face_indices"]]
    all_faces = manifest["all_face_indices"]
    rigid = manifest["rigid_face_indices"]
    moving = [face for p in patches if p["role"] == "moving" for face in p["advanced_face_indices"]]
    if (
        any(len(p["advanced_face_indices"]) != 1 for p in patches)
        or len(set(owned)) != len(owned)
        or len(set(all_faces)) != len(all_faces)
        or len(set(rigid)) != len(rigid)
        or set(moving) & set(rigid)
        or set(moving) | set(rigid) != set(all_faces)
        or len(all_faces) != len(patches) + (len(specs) if woofer else 4)
    ):
        raise ValueError("ambiguous, overlapping or incomplete native face mapping")
    if not set(owned) <= set(all_faces) or any(
        p["role"] == "rigid" and not set(p["advanced_face_indices"]) <= set(rigid) for p in patches
    ):
        raise ValueError("native patch role coverage mismatch")
    if woofer and (
        set(extra_faces) & set(owned)
        or set(extra_faces) | set(owned) != set(all_faces)
        or not set(extra_faces) <= set(rigid)
    ):
        raise ValueError("native baffle selectors overlap source patches or leave gaps")
    return manifest, contour, drive, _digest(raw)


def ingest_native_source(directory, *, store, data_dir, sizes):
    """Publish one immutable native ingestion accepted by the imported job route."""
    from server.jobs.models import ImportedMeshSizes
    from server.cadlink.ingest import solve_model_sha256
    from server.mesh.artifact import mesh_text_sha256

    manifest, contour, drive, manifest_hash = read_native_source(directory)
    sizes = ImportedMeshSizes.model_validate(sizes).model_dump(mode="json")
    moving = [p for p in manifest["patches"] if p["role"] == "moving"]
    if set(sizes["source_size_mm"]) != {p["id"] for p in moving}:
        raise ValueError("native source sizes must cover exactly moving patches")
    requested_sizes = {**sizes, "source_size_mm": dict(sizes["source_size_mm"])}
    assembly = ASSEMBLY_FEATURE in manifest["required_features"]
    role_sizes = {}
    if assembly:
        from hornlab_mesher.phase_plug import surface_targets

        role_sizes = surface_targets(
            contour, sizes["rigid_size_mm"], manifest["density"].get("passage_refinement", 1)
        )
    deviation_mm = (
        min(0.1, manifest["passage_contract"]["surface_tolerance_mm"] / 2)
        if PLUG_FEATURE in manifest["required_features"]
        else 0.1
    )
    sizes["rigid_size_mm"] = (
        sizes["rigid_size_mm"]
        if assembly
        else min(
            sizes["rigid_size_mm"],
            math.sqrt(
                8
                * 0.03
                * (
                    manifest["recipe"]["baffle"]["aperture_radius_mm"]
                    if "baffle" in manifest["recipe"]
                    else manifest["recipe"]["horn"]["housing_radius_mm"]
                )
            ),
        )
    )
    segments = (
        [(key, c, i) for key, c, i, _, _ in contour.patches]
        if assembly
        else [(s.id, contour, i) for i, s in enumerate(contour.segments)]
    )
    for key, model, i in segments:
        segment = model.segments[i]
        if segment.role == "moving" and segment.kind == "arc":
            sizes["source_size_mm"][key] = min(
                sizes["source_size_mm"][key],
                math.sqrt(8 * min(0.02, deviation_mm) * model.arc(i)[0]),
            )
        if segment.role == "moving" and (
            PLUG_FEATURE in manifest["required_features"]
            or (assembly and model.points[i].z_mm == model.points[i + 1].z_mm)
        ):
            a, b = model.points[i : i + 2]
            radii = [p.r_mm for p in (a, b) if p.r_mm > 0]
            radius = model.arc(i)[0] if segment.kind == "arc" else min(radii)
            if a.r_mm > 0:
                radius = min(radius, a.r_mm)
            # A flat diaphragm still has a circular boundary. Its polygonal
            # rim bounds the front opening, which must not intrude across it.
            target = min(deviation_mm, 0.03) if a.z_mm == b.z_mm else deviation_mm
            sizes["source_size_mm"][key] = min(
                sizes["source_size_mm"][key], math.sqrt(8 * target * radius)
            )
    channel = (
        manifest["channels"]
        if assembly
        else {
            "id": drive.channel_id,
            "source_ids": [p["id"] for p in moving],
            "physical_source_id": contour.physical_source_id,
            "patch_weights": dict(drive.weights),
            "motion": drive.motion,
        }
    )
    sources = [
        {
            "id": p["id"],
            "role": p["band"] if assembly else ("LF" if "baffle" in manifest["recipe"] else "HF"),
            "instance_id": None,
            "label": p["id"],
            "required": True,
            "patch_policy": "single-connected",
            "expected_connected_components": 1,
            "selectors": {"advanced_face_indices": p["advanced_face_indices"]},
            "observed": {"face_count": 1, "total_area_mm2": p["area_mm2"]},
        }
        for p in moving
    ]
    imported_manifest = {
        "sources": sources,
        "instances": [],
        "coordinate_system": {"solver_anchor_instance_id": None},
        "assembly": {"n_bodies_expected": 1 + len(contour.phase_plugs) if assembly else 1},
    }
    root = Path(data_dir) / "imports" / "native-source"
    root.mkdir(parents=True, exist_ok=True)
    published = None
    with tempfile.TemporaryDirectory(dir=root, prefix=".stage-") as temporary:
        stage = Path(temporary)
        # Copy then reverify: mutable caller files cannot change under the worker.
        for name in ["source.json", *manifest["members"]]:
            shutil.copyfile(Path(directory) / name, stage / name)
        copied, _, _, copied_hash = read_native_source(stage)
        if copied_hash != manifest_hash:
            raise ValueError("native source changed during capture")
        request = {"manifest": imported_manifest, "sizes": sizes}
        (stage / "request.json").write_bytes(_canonical(request))
        run = subprocess.run(
            [sys.executable, "-m", "server.cadlink.native_source", str(stage)],
            capture_output=True,
            text=True,
        )
        if run.returncode:
            raise ValueError("native source ingestion failed: " + run.stderr)
        built = json.loads((stage / "built.json").read_text())
        mesh_text = built.pop("msh_text")
        (stage / "solve.msh").write_bytes(mesh_text.encode())

        def publish(ingest_id, created_at):
            nonlocal published
            published = root / ingest_id
            stage.rename(published)
            frame = {
                "axis": [0, 0, 1],
                "u": [1, 0, 0],
                "v": [0, 1, 0],
                "origin_m": [0, 0, 0],
                "source_center_m": [0, 0, 0],
                "mouth_center_m": [
                    0,
                    0,
                    manifest["recipe"].get("horn", {}).get("length_mm", 0) * 0.001,
                ],
            }
            woofer = "baffle" in manifest["recipe"]
            if assembly:
                origin = [0, 0, contour.front_z_mm * 0.001]
                frame = {
                    "axis": [0, 0, 1],
                    "u": [1, 0, 0],
                    "v": [0, 1, 0],
                    "origin_m": origin,
                    "source_center_m": origin,
                    "mouth_center_m": origin,
                }
            if woofer:
                origin = [float(x) * 0.001 for x in manifest["recipe"]["baffle"]["center_mm"]]
                frame = {
                    "axis": [0, 0, 1],
                    "u": [1, 0, 0],
                    "v": [0, 1, 0],
                    "origin_m": origin,
                    "source_center_m": origin,
                }
            record = {
                "ingest_id": ingest_id,
                "created_at": created_at,
                "producer": "native",
                "return_id": "native:" + ingest_id,
                "acoustic_domain": "free-space",
                "scope": {"status": "full", "degraded_skip_count": 0},
                "freshness": {"verdict": "unlinked", "instances": []},
                "manifest_sha256": manifest_hash,
                "artifact_sha256": manifest["members"]["geometry.step"],
                "mesh_store_path": str(published / "solve.msh"),
                "bundle_store_path": str(published),
                "mesh_content_sha256": mesh_text_sha256(mesh_text),
                "mesh_sizes": sizes,
                "requested_mesh_sizes": requested_sizes,
                "sources": sources,
                "source_tags": built["tag_allocation"]["source_tags"],
                "tag_map": built["tag_allocation"]["tag_map"],
                "role_resolution": built["role_resolution"],
                "symmetry": built["symmetry"],
                "polar_grid_derivation": built["polar_grid_derivation"],
                "post_cut_source_areas": built.get("post_cut_source_areas", {}),
                "sizing_estimate": built["sizing_estimate"],
                "healing": built["healing"],
                "skipped_source_ids": [],
                "findings": [],
                "finding_ids": [],
                "normalisation": {
                    "source_frame" if woofer or assembly else "anchor_throat_frame": frame,
                    "matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                },
                "identity": {
                    "schema_version": 1,
                    "instances": [],
                    "selected_instance_id": None,
                    "solver_anchor_instance_id": None,
                },
                "domain_interpretation": {
                    "type": "native-shared-horn-woofer-closed-enclosure"
                    if assembly
                    else "native-front-baffle-woofer-closed-enclosure"
                    if woofer
                    else "native-full-circle-closed-housing"
                },
                "mesh": {
                    "stats": built["stats"],
                    "metadata": built["metadata"],
                    "integrity": built["integrity"],
                    "geometric_quality": built["geometric_quality"],
                },
                "transformed_geometry_hash": built["transformed_geometry_hash"],
                "native_source": {
                    "channels" if assembly else "channel": channel,
                    **(
                        {
                            "recipe": manifest["recipe"],
                            **(
                                {
                                    "passage_contract": manifest["passage_contract"],
                                    "passage_quality": built["geometric_quality"]["passages"],
                                }
                                if contour.phase_plugs
                                else {}
                            ),
                            "observation_frame": frame,
                            "source_frames": {
                                c.physical_source_id: {
                                    "origin_m": [x * 0.001 for x in origin],
                                    "axis": [0, 0, 1],
                                }
                                for c, origin, _ in contour.parts
                            },
                        }
                        if assembly
                        else {}
                    ),
                    "geometry_sha256": manifest["geometry_sha256"],
                    "excitation_sha256": manifest["excitation_sha256"],
                    "patches": manifest["patches"],
                    "required_features": manifest["required_features"],
                    "mesh_density_sha256": _digest(
                        _canonical(
                            {
                                "sizes": sizes,
                                "surface_deviation_mm": 0.1,
                                **(
                                    {"native_source_target_deviation_mm": deviation_mm}
                                    if PLUG_FEATURE in manifest["required_features"]
                                    else {}
                                ),
                                **({"rigid_role_sizes_mm": role_sizes} if assembly else {}),
                            }
                        )
                    ),
                },
            }
            record["solve_model_sha256"] = solve_model_sha256(record)
            record["report_sha256"] = _digest(_canonical(record))
            return _canonical(record).decode()

        try:
            row = store.allocate_ingest(
                manifest_sha256=manifest_hash,
                artifact_sha256=manifest["members"]["geometry.step"],
                record_builder=publish,
            )
        except BaseException:
            if published is not None and published.exists():
                shutil.rmtree(published)
            raise
    return json.loads(row["record_json"])


def _worker(stage):
    from server.mesh.imported import build_imported_mesh
    import numpy as np
    import gmsh

    request = json.loads((stage / "request.json").read_text())
    gmsh.initialize()
    try:
        _verify_recipe_faces(stage, gmsh)
        manifest, model, _, _ = read_native_source(stage)
        options = {"symmetry_mode": "full", "surface_deviation_mm": 0.1}
        if ASSEMBLY_FEATURE in manifest["required_features"]:
            options["native_source_size_limits"] = True
        if ASSEMBLY_FEATURE in manifest["required_features"]:
            from hornlab_mesher.phase_plug import surface_targets

            targets = surface_targets(
                model,
                request["sizes"]["rigid_size_mm"],
                manifest["density"].get("passage_refinement", 1),
            )
            options["rigid_face_sizes_mm"] = {
                p["advanced_face_indices"][0]: targets[p["role"]]
                for p in manifest["rigid_faces"]
                if p["role"] in targets
            }
        built = build_imported_mesh(
            stage / "geometry.step",
            request["manifest"],
            request["sizes"],
            options=options,
            include_viewport_mesh=False,
        )
    finally:
        gmsh.finalize()
    # The contour's declared forward normal and aligned axis are an admission
    # gate, separate from the general imported route's orientation repair.
    import meshio

    # gmsh22_triangle_arrays supplies geometry; physical tags come from the mesh.
    (stage / "worker.msh").write_text(built["msh_text"], encoding="utf-8")
    mesh = meshio.read(stage / "worker.msh")
    tri = mesh.get_cells_type("triangle")
    tags = mesh.get_cell_data("gmsh:physical", "triangle")
    xyz = mesh.points[tri]
    n = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    active = np.isin(tags, list(built["tag_allocation"]["source_tags"].values()))
    if not active.any() or np.any(n[active, 2] < -1e-12):
        raise ValueError("native moving normals must face forward")
    if built["integrity"].get("open_edge_count") != 0:
        raise ValueError("native contour/horn/housing mesh must be watertight")
    if set(tags) != {1, *built["tag_allocation"]["source_tags"].values()}:
        raise ValueError("native mesh has incomplete source/rigid role coverage")
    manifest, contour, _, _ = read_native_source(stage)
    if len(tri) > manifest["density"]["triangle_limit"]:
        raise ValueError("native imported mesh exceeds triangle budget")
    built["geometric_quality"] = _certify_mesh(
        mesh.points[tri] * 1000,
        tri,
        tags,
        built["tag_allocation"]["source_tags"],
        contour,
        manifest["recipe"].get("horn"),
        baffle=manifest["recipe"].get("baffle"),
        assembly=contour if ASSEMBLY_FEATURE in manifest["required_features"] else None,
    )
    if ASSEMBLY_FEATURE in manifest["required_features"] and contour.phase_plugs:
        from hornlab_mesher.passage_mesh import certify_passage_mesh

        built["geometric_quality"]["passages"] = certify_passage_mesh(
            contour, mesh.points * 1000, tri, active
        )
    (stage / "built.json").write_bytes(_canonical(built))


def _certify_mesh(
    xyz, triangles, tags, source_tags, contour, horn=None, *, baffle=None, assembly=None
):
    """Certify every facet against finite surfaces, or refuse publication.

    A barycentric lattice covers a triangle within diameter/n. Distance to a
    closed finite surface is 1-Lipschitz, so sample maximum plus that radius
    bounds the entire facet. Refinement tightens the bound, never the tolerance.
    """
    import numpy as np

    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    _, inverse, counts = np.unique(
        np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    winding = np.bincount(inverse, weights=np.where(edges[:, 0] < edges[:, 1], 1, -1))
    if np.any(counts != 2) or np.any(winding != 0):
        raise ValueError("native imported mesh must be closed and consistently oriented")
    if np.any(
        np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1) <= 1e-12
    ):
        raise ValueError("native imported mesh has degenerate facets")

    if baffle is not None:
        from hornlab_mesher.front_baffle import FrontBaffle

        baffle = FrontBaffle.from_dict(baffle)
        baffle.validate(contour)
    origin = np.asarray(baffle.center_mm if baffle else (0, 0, 0))

    def radial(distance):
        def wrapped(samples):
            local = samples - origin
            return distance(np.linalg.norm(local[..., :2], axis=-1), local[..., 2])

        return wrapped

    def line(a, b):
        ar, az = a
        dr, dz = b[0] - ar, b[1] - az

        def distance(r, z):
            t = np.clip(((r - ar) * dr + (z - az) * dz) / (dr * dr + dz * dz), 0, 1)
            return np.hypot(r - ar - t * dr, z - az - t * dz)

        return distance

    def meridian(i):
        a, b = contour.points[i : i + 2]
        s = contour.segments[i]
        if s.kind == "line":
            return line((a.r_mm, a.z_mm), (b.r_mm, b.z_mm))
        radius, start, sweep = contour.arc(i)
        cr, cz = s.center_mm

        def distance(r, z):
            angle = np.arctan2(z - cz, r - cr)
            progress = np.mod((angle - start) * math.copysign(1, sweep), 2 * math.pi)
            progress = np.where(abs(progress - 2 * math.pi) < 1e-10, 0, progress)
            ends = np.minimum(np.hypot(r - a.r_mm, z - a.z_mm), np.hypot(r - b.r_mm, z - b.z_mm))
            return np.where(
                progress <= abs(sweep) + 1e-10, abs(np.hypot(r - cr, z - cz) - radius), ends
            )

        return distance

    def certify(facets, distance):
        if not len(facets):
            raise ValueError("native mesh geometric patch is empty")
        worst = 0.0
        for n in (8, 32, 128):
            uv = np.array([(a / n, b / n) for a in range(n + 1) for b in range(n + 1 - a)])
            pending = []
            for batch in np.array_split(facets, max(1, len(facets) // 32 + 1)):
                samples = (
                    batch[:, 0, None, :]
                    + uv[None, :, 0, None] * (batch[:, 1, None, :] - batch[:, 0, None, :])
                    + uv[None, :, 1, None] * (batch[:, 2, None, :] - batch[:, 0, None, :])
                )
                sampled = distance(samples).max(axis=1)
                if np.any(sampled > 0.15):
                    raise ValueError("native mesh facet exceeds 0.15 mm geometric tolerance")
                diameter = np.linalg.norm(batch - np.roll(batch, 1, axis=1), axis=2).max(axis=1)
                bound = sampled + diameter / n
                good = bound <= 0.15
                if good.any():
                    worst = max(worst, float(bound[good].max()))
                pending.extend(batch[~good])
            if not pending:
                return worst
            facets = np.asarray(pending)
        raise ValueError("native mesh whole-facet tolerance could not be certified")

    if assembly is not None:
        bounds = {
            key: certify(
                xyz[tags == source_tags[key]], lambda p, key=key: assembly.patch_distance(key, p)
            )
            for key, c, i, _, _ in assembly.patches
            if c.segments[i].role == "moving"
        }
        rigid = [
            lambda p, role=role: assembly.rigid_distance(role, p) for role in assembly.rigid_areas()
        ]
        rigid += [
            lambda p, key=key: assembly.patch_distance(key, p)
            for key, c, i, _, _ in assembly.patches
            if c.segments[i].role == "rigid"
        ]
        rigid_bound = certify(xyz[tags == 1], lambda p: np.minimum.reduce([f(p) for f in rigid]))
        return {
            "tolerance_mm": 0.15,
            "moving_patch_bounds_mm": bounds,
            "rigid_bound_mm": rigid_bound,
            "method": "finite-shared-assembly-lipschitz-v1",
        }
    bounds = {
        s.id: certify(xyz[tags == source_tags[s.id]], radial(meridian(i)))
        for i, s in enumerate(contour.segments)
        if s.role == "moving"
    }
    rigid_meridians = [
        radial(meridian(i)) for i, s in enumerate(contour.segments) if s.role == "rigid"
    ]
    if baffle:
        rigid = rigid_meridians + [
            lambda xyz, role=role: baffle.distance(contour, role, xyz)
            for role in baffle.surfaces(contour)
        ]
    else:
        points = [
            (horn["radius_mm"], 0),
            (horn["mouth_radius_mm"], horn["length_mm"]),
            (horn["housing_radius_mm"], horn["length_mm"]),
            (horn["housing_radius_mm"], -horn["backing_depth_mm"]),
            (0, -horn["backing_depth_mm"]),
        ]
        rigid = [radial(line(a, b)) for a, b in zip(points, points[1:])] + rigid_meridians
    rigid_bound = certify(xyz[tags == 1], lambda xyz: np.minimum.reduce([f(xyz) for f in rigid]))
    return {
        "tolerance_mm": 0.15,
        "moving_patch_bounds_mm": bounds,
        "rigid_bound_mm": rigid_bound,
        "method": "finite-baffle-meridian-lipschitz-v1"
        if baffle
        else "finite-meridian-lipschitz-v1",
    }


def _verify_recipe_faces(stage, gmsh):
    """Refuse valid-looking selectors rebound to another geometric patch."""
    import numpy as np
    from hornlab_mesher.step_mapping import advanced_face_order_for_surfaces
    from server.mesh.imported import _parametric_trim_mask

    manifest, contour, _, _ = read_native_source(stage)
    gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
    gmsh.model.occ.importShapes(str(stage / "geometry.step"))
    gmsh.model.occ.synchronize()
    faces = [tag for _, tag in gmsh.model.getEntities(2)]
    ids = advanced_face_order_for_surfaces(
        stage / "geometry.step", faces, addressed_faces=manifest["all_face_indices"]
    )
    if sorted(ids) != sorted(manifest["all_face_indices"]):
        raise ValueError("native STEP face coverage is stale")
    mapped = dict(zip(ids, faces))
    if ASSEMBLY_FEATURE in manifest["required_features"]:
        from .native_assembly import verify_faces

        verify_faces(gmsh, manifest, contour, mapped)
        return
    origin = np.asarray(manifest["recipe"].get("baffle", {}).get("center_mm", (0, 0, 0)))
    for i, patch in enumerate(manifest["patches"]):
        face = mapped[patch["advanced_face_indices"][0]]
        a, b = contour.points[i : i + 2]
        segment = contour.segments[i]
        if segment.kind == "line":
            expected_area = (
                math.pi * (a.r_mm + b.r_mm) * math.hypot(b.r_mm - a.r_mm, b.z_mm - a.z_mm)
            )
        else:
            R, start, sweep = contour.arc(i)
            cr, cz = segment.center_mm
            expected_area = (
                2
                * math.pi
                * R
                * (
                    cr * abs(sweep)
                    + R * math.copysign(1, sweep) * (math.sin(start + sweep) - math.sin(start))
                )
            )
        if abs(gmsh.model.occ.getMass(2, face) - expected_area) > 1e-7 * max(1, expected_area):
            raise ValueError("native STEP selector does not match canonical patch area")
        if abs(patch["area_mm2"] - expected_area) > 1e-7 * max(1, expected_area):
            raise ValueError("native declared patch area contradicts canonical geometry")
        lo, hi = gmsh.model.getParametrizationBounds(2, face)
        uv = [
            x
            for u in np.linspace(lo[0], hi[0], 7)
            for v in np.linspace(lo[1], hi[1], 7)
            for x in (u, v)
        ]
        uv = np.asarray(uv).reshape(-1, 2)
        uv = uv[_parametric_trim_mask(gmsh, face, uv.reshape(-1))]
        if not len(uv):
            raise ValueError("native STEP patch has no valid trimmed samples")
        xyz = np.asarray(gmsh.model.getValue(2, face, uv.reshape(-1))).reshape(-1, 3)
        xyz = xyz - origin
        r = np.linalg.norm(xyz[:, :2], axis=1)
        z = xyz[:, 2]
        if segment.kind == "line":
            residual = abs(
                (z - a.z_mm) * (b.r_mm - a.r_mm) - (r - a.r_mm) * (b.z_mm - a.z_mm)
            ) / math.hypot(b.r_mm - a.r_mm, b.z_mm - a.z_mm)
        else:
            residual = abs(np.hypot(r - cr, z - cz) - R)
            theta = np.arctan2(z - cz, r - cr)
            progress = np.mod((theta - start) * math.copysign(1, sweep), 2 * math.pi)
            progress = np.where(abs(progress - 2 * math.pi) < 1e-9, 0, progress)
            if np.any(progress > abs(sweep) + 1e-8):
                raise ValueError("native STEP selector uses the wrong circular arc branch")
        if np.max(residual) > 1e-6 or np.min(r) < a.r_mm - 1e-6 or np.max(r) > b.r_mm + 1e-6:
            raise ValueError("native STEP selector contradicts the canonical meridian")
    if "baffle" in manifest["recipe"]:
        from hornlab_mesher.front_baffle import FrontBaffle

        baffle = FrontBaffle.from_dict(manifest["recipe"]["baffle"])
        for patch in manifest["baffle_faces"]:
            face = mapped[patch["advanced_face_indices"][0]]
            expected_area = baffle.surfaces(contour)[patch["role"]][-1]
            if abs(gmsh.model.occ.getMass(2, face) - expected_area) > 1e-7 * max(1, expected_area):
                raise ValueError("native rigid STEP selector contradicts baffle area")
            lo, hi = gmsh.model.getParametrizationBounds(2, face)
            uv = np.asarray(
                [(u, v) for u in np.linspace(lo[0], hi[0], 7) for v in np.linspace(lo[1], hi[1], 7)]
            )
            uv = uv[_parametric_trim_mask(gmsh, face, uv.reshape(-1))]
            if not len(uv):
                raise ValueError("native rigid STEP face has no valid trimmed samples")
            xyz = np.asarray(gmsh.model.getValue(2, face, uv.reshape(-1))).reshape(-1, 3)
            if np.max(baffle.distance(contour, patch["role"], xyz)) > 1e-6:
                raise ValueError("native rigid STEP selector contradicts finite baffle geometry")
        return
    horn = manifest["recipe"]["horn"]
    paths = [
        ((horn["radius_mm"], 0), (horn["mouth_radius_mm"], horn["length_mm"])),
        (
            (horn["mouth_radius_mm"], horn["length_mm"]),
            (horn["housing_radius_mm"], horn["length_mm"]),
        ),
        (
            (horn["housing_radius_mm"], horn["length_mm"]),
            (horn["housing_radius_mm"], -horn["backing_depth_mm"]),
        ),
        ((horn["housing_radius_mm"], -horn["backing_depth_mm"]), (0, -horn["backing_depth_mm"])),
    ]
    remaining = set(ids) - {p["advanced_face_indices"][0] for p in manifest["patches"]}
    unmatched = set(range(4))
    for identifier in remaining:
        face = mapped[identifier]
        lo, hi = gmsh.model.getParametrizationBounds(2, face)
        uv = [
            x
            for u in np.linspace(lo[0], hi[0], 7)
            for v in np.linspace(lo[1], hi[1], 7)
            for x in (u, v)
        ]
        uv = np.asarray(uv).reshape(-1, 2)
        uv = uv[_parametric_trim_mask(gmsh, face, uv.reshape(-1))]
        if not len(uv):
            raise ValueError("native rigid STEP face has no valid trimmed samples")
        xyz = np.asarray(gmsh.model.getValue(2, face, uv.reshape(-1))).reshape(-1, 3)
        r, z = np.linalg.norm(xyz[:, :2], axis=1), xyz[:, 2]
        matches = []
        for i, ((ar, az), (br, bz)) in enumerate(paths):
            dr, dz = br - ar, bz - az
            t = np.clip(((r - ar) * dr + (z - az) * dz) / (dr * dr + dz * dz), 0, 1)
            residual = np.hypot(r - (ar + t * dr), z - (az + t * dz))
            area = math.pi * (ar + br) * math.hypot(dr, dz)
            if np.max(residual) < 1e-6 and abs(gmsh.model.occ.getMass(2, face) - area) < 1e-7 * max(
                1, area
            ):
                matches.append(i)
        if len(matches) != 1 or matches[0] not in unmatched:
            raise ValueError("native rigid STEP coverage contradicts the horn/housing recipe")
        unmatched.remove(matches[0])
    if unmatched:
        raise ValueError("native horn/housing STEP coverage is incomplete")


if __name__ == "__main__":
    _worker(Path(sys.argv[1]))
