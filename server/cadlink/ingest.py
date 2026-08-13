"""CAD-return ingestion orchestration, freshness verdicts, and CAS storage."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from server.cadlink.store import CadLinkStore
from server.design.textcfg import parse
from server.exports.geometry_identity import geometry_hash_for_design, normalize_json_value
from server.mesh.imported import (
    ImportedMeshDependencyError,
    RoleResolutionError,
    build_imported_mesh,
    build_imported_viewport_mesh,
    validate_imported_sizes,
)
from server.mesh.artifact import mesh_text_sha256
from server.platform.paths import data_paths

from .wgreturn import WgReturnBundle, read_wgreturn


IMPORT_MESH_PIPELINE_CONTRACT = "wg-import-solve-v3"
IMPORT_VIEWPORT_PIPELINE_CONTRACT = "wg-import-viewport-v1"


class IngestRefusal(ValueError):
    """A stage-labelled validation or consistency refusal."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        corruption: bool = False,
        area_drift_sources: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.stage = stage
        self.corruption = corruption
        self.area_drift_sources = tuple(area_drift_sources)
        super().__init__(f"{stage}: {message}")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        normalize_json_value(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _finding_id(kind: str, identity: Any) -> str:
    digest = hashlib.sha256(_canonical({"kind": kind, "identity": identity})).hexdigest()[:16]
    return f"finding-{kind}-{digest}"


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def _fingerprints_match(first: Any, second: Any) -> bool | None:
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return None
    try:
        if bool(first["is_solid"]) != bool(second["is_solid"]):
            return False
        volume_a = float(first["volume_mm3"])
        volume_b = float(second["volume_mm3"])
        volume_tolerance = max(1.0e-3, 1.0e-6 * max(abs(volume_a), abs(volume_b)))
        if abs(volume_a - volume_b) > volume_tolerance:
            return False
        bbox_a = [float(item) for item in first["bbox_mm"]]
        bbox_b = [float(item) for item in second["bbox_mm"]]
        if len(bbox_a) != 6 or len(bbox_b) != 6:
            return None
        for left, right in zip(bbox_a, bbox_b, strict=True):
            tolerance = max(1.0e-4, 1.0e-7 * max(abs(left), abs(right)))
            if abs(left - right) > tolerance:
                return False
    except (KeyError, TypeError, ValueError):
        return None
    return True


def validate_registry_echoes(manifest: Mapping[str, Any], store: CadLinkStore) -> None:
    """Refuse a local ledger row that contradicts CAD's immutable echoes."""

    for instance in manifest["instances"]:
        row = store.get_export(str(instance["export_id"]))
        if row is None:
            continue
        comparisons = {
            "design_id": (row["design_id"], instance.get("design_id")),
            "sequence": (row["sequence"], instance.get("export_sequence")),
            "design_hash": (row["design_hash"], instance.get("design_hash")),
            "geometry_hash": (row["geometry_hash"], instance.get("geometry_hash")),
            "bundle_id": (row["bundle_id"], instance.get("origin_bundle_id")),
        }
        for field, (registered, echoed) in comparisons.items():
            if registered != echoed:
                raise IngestRefusal(
                    "stage 3 consistency gate",
                    f"export {instance['export_id']!r} contradicts {field}: "
                    f"registry={registered!r}, return={echoed!r}",
                    corruption=True,
                )


def validate_scope_inventory(manifest: Mapping[str, Any]) -> None:
    """Re-check CAD's exterior inventory before importing its STEP member."""

    included = list(manifest["scope"]["included"])
    expected = int(manifest["assembly"]["n_bodies_expected"])
    if len(included) != expected:
        raise IngestRefusal(
            "stage 2 scope gate",
            f"scope.included has {len(included)} exterior bodies but "
            f"assembly.n_bodies_expected is {expected}",
        )


def evaluate_instance_freshness(
    instance: Mapping[str, Any],
    store: CadLinkStore,
    *,
    recompute: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Apply freshness precedence rows 1-6 and retain supporting findings."""

    instance_id = str(instance["instance_id"])
    evidence = instance["body_evidence"]
    body_state = str(evidence["local_body_state"])
    fingerprint_match = _fingerprints_match(
        evidence.get("baseline_fingerprint"), evidence.get("observed_fingerprint")
    )
    facts: list[dict[str, Any]] = []
    body_diverged = body_state in {"modified", "missing"} or (
        body_state == "unmodified" and fingerprint_match is False
    )
    if body_diverged:
        facts.append({"kind": "body-evidence-diverged", "local_body_state": body_state, "fingerprints_match": fingerprint_match})

    head = store.get_design(str(instance["design_id"]))
    design_changed = False
    generator_changed = False
    evidence_errors: list[str] = []
    recompute_error: str | None = None
    regenerated: str | None = None
    if head is None:
        facts.append({"kind": "registry-design-missing", "design_id": instance["design_id"]})
    else:
        if instance.get("design_hash") is None:
            evidence_errors.append("return is missing design_hash evidence")
        else:
            design_changed = str(head["design_hash"]) != str(instance["design_hash"])
            if design_changed:
                facts.append({"kind": "design-hash-changed", "registry": head["design_hash"], "return": instance["design_hash"]})
        if instance.get("geometry_hash") is None:
            evidence_errors.append("return is missing geometry_hash evidence")
        else:
            try:
                if recompute is not None:
                    regenerated = recompute(head)
                else:
                    regenerated = geometry_hash_for_design(parse(str(head["snapshot_text"])).design)
                generator_changed = regenerated != str(instance["geometry_hash"])
                if generator_changed:
                    facts.append({"kind": "geometry-hash-changed", "regenerated": regenerated, "return": instance["geometry_hash"]})
            except Exception as exc:  # freshness must never make ingestion crash
                recompute_error = f"{type(exc).__name__}: {exc}"
                evidence_errors.append(recompute_error)
        if body_state != "unmodified" or fingerprint_match is not True:
            evidence_errors.append("body evidence is missing or unreadable")
        for evidence_error in dict.fromkeys(evidence_errors):
            facts.append({"kind": "freshness-evidence-unreadable", "error": evidence_error})

    # Headline precedence is independent of the retained simultaneous facts.
    if body_diverged:
        verdict = "body_modified"
        error = None
    elif head is None:
        verdict = "missing_design"
        error = None
    elif design_changed:
        verdict = "design_changed"
        error = None
    elif generator_changed:
        verdict = "generator_changed"
        error = None
    elif evidence_errors:
        verdict = "unknown"
        error = "; ".join(dict.fromkeys(evidence_errors))
    else:
        verdict = "current"
        error = None
    result = {
        "instance_id": instance_id,
        "verdict": verdict,
        "local_body_state": body_state,
        "fingerprints_match": fingerprint_match,
        "error": error,
        "facts": facts,
        "finding_id": None,
    }
    if verdict != "current":
        result["finding_id"] = _finding_id("freshness", {"instance_id": instance_id, "verdict": verdict})
    return result


def compute_freshness(
    manifest: Mapping[str, Any],
    store: CadLinkStore,
    *,
    recompute: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, Any]:
    if not manifest["instances"]:
        finding_id = _finding_id("freshness", {"verdict": "unlinked"})
        return {"verdict": "unlinked", "instances": [], "finding_id": finding_id}
    results = [
        evaluate_instance_freshness(instance, store, recompute=recompute)
        for instance in manifest["instances"]
    ]
    return {"verdict": "per-instance", "instances": results}


def _scope_findings(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for index, skip in enumerate(manifest["scope"]["skipped"]):
        if skip.get("severity") != "degraded":
            continue
        identity = {
            "index": index,
            "object_id": skip.get("object_id"),
            "kind": skip.get("kind"),
            "reason": skip.get("reason"),
        }
        findings.append(
            {
                "id": _finding_id("scope-degradation", identity),
                "kind": "scope-degradation",
                "blocking": True,
                "evidence_path": f"$.scope.skipped[{index}]",
                "object_id": skip.get("object_id"),
                "reason": skip.get("reason"),
            }
        )
    return findings


def _stage_bundle_cas(
    bundle: WgReturnBundle, imports_root: Path
) -> tuple[Path, Path | None, Path | None]:
    """Copy a verified bundle completely before entering the registry transaction."""

    destination = imports_root / "bundles" / f"{bundle.manifest_sha256.removeprefix('sha256:')}.wgreturn"
    if destination.is_dir():
        return destination, None, None
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".wg2-import-bundle-", dir=destination.parent))
    staged = temporary / destination.name
    try:
        shutil.copytree(bundle.path, staged, symlinks=False)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, staged, temporary


def _publish_staged_bundle(destination: Path, staged: Path | None) -> None:
    if staged is None or destination.is_dir():
        return
    try:
        os.replace(staged, destination)
    except OSError:
        if not destination.is_dir():
            raise


def _cache_key(
    bundle: WgReturnBundle,
    manifest: Mapping[str, Any],
    sizes: Mapping[str, Any],
    skipped_source_ids: list[str],
    options: Mapping[str, Any],
    transformed_geometry_hash: str,
) -> str:
    instances = list(manifest["instances"])
    anchor_id = manifest["coordinate_system"].get("solver_anchor_instance_id")
    if anchor_id is None and len(instances) == 1:
        anchor_id = instances[0]["instance_id"]
    anchor = next((item for item in instances if item["instance_id"] == anchor_id), None)
    from server.mesh.imported import allocate_imported_tags, rigid_inverse

    transform = (
        rigid_inverse(anchor["assembly_from_link"]).tolist()
        if anchor is not None
        else [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    )

    payload = {
        "import_pipeline_contract": IMPORT_MESH_PIPELINE_CONTRACT,
        "artifact_sha256": bundle.artifact_sha256,
        "manifest_sha256": bundle.manifest_sha256,
        "normalisation_transform": transform,
        "transformed_geometry_hash": transformed_geometry_hash,
        "source_table": manifest["sources"],
        "sizes": sizes,
        "skipped_source_ids": sorted(skipped_source_ids),
        "prep_options": options,
        "tags": allocate_imported_tags(
            manifest["sources"], skipped_source_ids=skipped_source_ids
        ),
        "mesher_version": _package_version("hornlab-waveguide-mesher"),
        "gmsh_version": _package_version("gmsh"),
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _cache_lookup_key(
    bundle: WgReturnBundle,
    manifest: Mapping[str, Any],
    sizes: Mapping[str, Any],
    skipped_source_ids: list[str],
    options: Mapping[str, Any],
) -> str:
    """Address the measured-hash index without pretending it is geometry."""

    return hashlib.sha256(
        _canonical(
            {
                "import_pipeline_contract": IMPORT_MESH_PIPELINE_CONTRACT,
                "artifact_sha256": bundle.artifact_sha256,
                "manifest_sha256": bundle.manifest_sha256,
                "sources": manifest["sources"],
                "instances": manifest["instances"],
                "sizes": sizes,
                "skipped_source_ids": sorted(skipped_source_ids),
                "options": options,
                "mesher_version": _package_version("hornlab-waveguide-mesher"),
                "gmsh_version": _package_version("gmsh"),
            }
        )
    ).hexdigest()


def _viewport_cache_lookup_key(
    bundle: WgReturnBundle,
    manifest: Mapping[str, Any],
    skipped_source_ids: list[str],
    options: Mapping[str, Any],
) -> str:
    """Index visual tessellation independently of acoustic mesh sizing."""

    from server.mesh.imported import allocate_imported_tags

    return hashlib.sha256(
        _canonical(
            {
                "viewport_pipeline_contract": IMPORT_VIEWPORT_PIPELINE_CONTRACT,
                "artifact_sha256": bundle.artifact_sha256,
                "manifest_sha256": bundle.manifest_sha256,
                "sources": manifest["sources"],
                "instances": manifest["instances"],
                "skipped_source_ids": sorted(skipped_source_ids),
                "prep_options": options,
                "tags": allocate_imported_tags(
                    manifest["sources"], skipped_source_ids=skipped_source_ids
                ),
                "mesher_version": _package_version("hornlab-waveguide-mesher"),
                "gmsh_version": _package_version("gmsh"),
            }
        )
    ).hexdigest()


def _load_cached_mesh(mesh_path: Path, metadata_path: Path) -> dict[str, Any] | None:
    if not mesh_path.is_file() or not metadata_path.is_file():
        return None
    try:
        result = json.loads(metadata_path.read_text(encoding="utf-8"))
        result["msh_text"] = mesh_path.read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return result


def _write_cache(mesh_path: Path, metadata_path: Path, result: Mapping[str, Any]) -> None:
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".wg2-import-mesh-", dir=mesh_path.parent))
    try:
        staged_mesh = temporary / mesh_path.name
        staged_meta = temporary / metadata_path.name
        staged_mesh.write_text(str(result["msh_text"]), encoding="utf-8")
        sidecar = {
            key: value
            for key, value in result.items()
            if key not in {"msh_text", "viewport_msh_text"}
        }
        staged_meta.write_bytes(_canonical(sidecar) + b"\n")
        # Metadata is the commit marker: publish mesh first, metadata last.
        os.replace(staged_mesh, mesh_path)
        os.replace(staged_meta, metadata_path)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _load_cached_viewport_mesh(
    mesh_path: Path, metadata_path: Path
) -> dict[str, Any] | None:
    if not mesh_path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        msh_text = mesh_path.read_text(encoding="utf-8")
        expected = str(metadata["content_sha256"])
        if mesh_text_sha256(msh_text) != expected:
            return None
        return {**metadata, "msh_text": msh_text}
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_viewport_cache(
    mesh_path: Path,
    metadata_path: Path,
    viewport: Mapping[str, Any],
    *,
    transformed_geometry_hash: str,
    healing: Mapping[str, Any],
) -> dict[str, Any]:
    msh_text = str(viewport["msh_text"])
    metadata = {
        "content_sha256": mesh_text_sha256(msh_text),
        "transformed_geometry_hash": transformed_geometry_hash,
        "healing_mode": str(healing.get("mode") or "none"),
        "healing_options": list(healing.get("options") or ()),
        "stats": viewport["stats"],
        "metadata": viewport["metadata"],
    }
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".wg2-import-viewport-", dir=mesh_path.parent))
    try:
        staged_mesh = temporary / mesh_path.name
        staged_meta = temporary / metadata_path.name
        staged_mesh.write_text(msh_text, encoding="utf-8")
        staged_meta.write_bytes(_canonical(metadata) + b"\n")
        os.replace(staged_mesh, mesh_path)
        # Metadata is the commit marker and carries the digest for later reads.
        os.replace(staged_meta, metadata_path)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return {**metadata, "msh_text": msh_text}


def ingest_bundle(
    bundle_path: str | Path,
    mesh: Mapping[str, Any],
    skipped_source_ids: list[str],
    store: CadLinkStore,
    data_dir: str | Path,
    *,
    prep_options: Mapping[str, Any] | None = None,
    recompute_freshness: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Run the nine ingestion stages and persist the immutable WG verdict."""

    try:
        bundle = read_wgreturn(bundle_path)
    except Exception as exc:
        raise IngestRefusal("stage 1 bundle validation", str(exc)) from exc
    manifest = bundle.manifest
    normalized_mesh = validate_imported_sizes(
        manifest["sources"], mesh, skipped_source_ids=skipped_source_ids
    )
    scope_findings = _scope_findings(manifest)
    validate_scope_inventory(manifest)
    try:
        validate_registry_echoes(manifest, store)
    except IngestRefusal:
        raise
    options = dict(prep_options or {})
    imports_root = data_paths(data_dir).root / "imports"
    viewport_lookup_key = _viewport_cache_lookup_key(
        bundle, manifest, skipped_source_ids, options
    )
    viewport_index_path = (
        imports_root / "viewports" / "index" / f"{viewport_lookup_key}.txt"
    )
    viewport_cache_key: str | None = None
    try:
        indexed_viewport = viewport_index_path.read_text(encoding="ascii").strip()
        if len(indexed_viewport) == 64 and all(
            character in "0123456789abcdef" for character in indexed_viewport
        ):
            viewport_cache_key = indexed_viewport
    except OSError:
        pass
    viewport_mesh_path = (
        imports_root / "viewports" / f"{viewport_cache_key}.msh"
        if viewport_cache_key
        else imports_root / "viewports" / ".pending.msh"
    )
    viewport_metadata_path = (
        imports_root / "viewports" / f"{viewport_cache_key}.json"
        if viewport_cache_key
        else imports_root / "viewports" / ".pending.json"
    )
    viewport_artifact = (
        _load_cached_viewport_mesh(viewport_mesh_path, viewport_metadata_path)
        if viewport_cache_key
        else None
    )
    viewport_cache_hit = viewport_artifact is not None
    lookup_key = _cache_lookup_key(
        bundle, manifest, normalized_mesh, skipped_source_ids, options
    )
    index_path = imports_root / "meshes" / "index" / f"{lookup_key}.txt"
    cache_key: str | None = None
    try:
        indexed = index_path.read_text(encoding="ascii").strip()
        if len(indexed) == 64 and all(character in "0123456789abcdef" for character in indexed):
            cache_key = indexed
    except OSError:
        pass
    mesh_path = imports_root / "meshes" / f"{cache_key}.msh" if cache_key else imports_root / "meshes" / ".pending.msh"
    metadata_path = imports_root / "meshes" / f"{cache_key}.json" if cache_key else imports_root / "meshes" / ".pending.json"
    built = _load_cached_mesh(mesh_path, metadata_path) if cache_key else None
    cache_hit = built is not None
    if built is None:
        try:
            built = build_imported_mesh(
                bundle.assembly_path,
                manifest,
                normalized_mesh,
                skipped_source_ids=skipped_source_ids,
                options=options,
                include_viewport_mesh=viewport_artifact is None,
            )
        except ImportedMeshDependencyError:
            raise
        except Exception as exc:
            message = str(exc)
            stage = "stage 7 meshing"
            for marker, labelled in (
                ("scope gate:", "stage 2 scope gate"),
                ("STEP import + normalisation:", "stage 4 STEP import + normalisation"),
                ("normalisation:", "stage 4 STEP import + normalisation"),
                ("role resolution:", "stage 5 role resolution"),
                ("symmetry:", "stage 6 symmetry"),
            ):
                if marker in message:
                    stage = labelled
                    break
            raise IngestRefusal(
                stage,
                message,
                area_drift_sources=(
                    exc.area_drift_sources
                    if isinstance(exc, RoleResolutionError)
                    else ()
                ),
            ) from exc
        cache_key = _cache_key(
            bundle,
            manifest,
            normalized_mesh,
            skipped_source_ids,
            options,
            str(built["transformed_geometry_hash"]),
        )
        mesh_path = imports_root / "meshes" / f"{cache_key}.msh"
        metadata_path = imports_root / "meshes" / f"{cache_key}.json"
        _write_cache(mesh_path, metadata_path, built)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_index = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")
        temporary_index.write_text(cache_key + "\n", encoding="ascii")
        os.replace(temporary_index, index_path)
    assert built is not None
    assert cache_key is not None

    viewport_failure_reason: str | None = None
    if viewport_artifact is None:
        generated_viewport: dict[str, Any] | None = None
        if built.get("viewport_msh_text") is not None:
            generated_viewport = {
                "msh_text": built["viewport_msh_text"],
                "stats": built["viewport_mesh"]["stats"],
                "metadata": built["viewport_mesh"]["metadata"],
            }
        elif cache_hit and isinstance(built.get("viewport_recipe"), Mapping):
            try:
                generated_viewport = build_imported_viewport_mesh(
                    bundle.assembly_path,
                    manifest,
                    built["viewport_recipe"],
                    expected_geometry_hash=str(built["transformed_geometry_hash"]),
                    tag_allocation=built["tag_allocation"],
                )
            except Exception as exc:
                viewport_failure_reason = f"{type(exc).__name__}: {exc}"
        if generated_viewport is not None:
            viewport_digest = mesh_text_sha256(str(generated_viewport["msh_text"]))
            viewport_cache_key = viewport_digest.removeprefix("sha256:")
            viewport_mesh_path = imports_root / "viewports" / f"{viewport_cache_key}.msh"
            viewport_metadata_path = imports_root / "viewports" / f"{viewport_cache_key}.json"
            try:
                viewport_artifact = _write_viewport_cache(
                    viewport_mesh_path,
                    viewport_metadata_path,
                    generated_viewport,
                    transformed_geometry_hash=str(built["transformed_geometry_hash"]),
                    healing=built["healing"],
                )
            except Exception as exc:
                viewport_artifact = None
                viewport_failure_reason = f"{type(exc).__name__}: {exc}"
            if viewport_artifact is not None:
                try:
                    viewport_index_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary_viewport_index = viewport_index_path.with_name(
                        f".{viewport_index_path.name}.{os.getpid()}.tmp"
                    )
                    temporary_viewport_index.write_text(
                        viewport_cache_key + "\n", encoding="ascii"
                    )
                    os.replace(temporary_viewport_index, viewport_index_path)
                except OSError:
                    # The content-addressed artifact and record remain valid;
                    # a later ingest can simply regenerate the lookup index.
                    pass
        elif viewport_failure_reason is None:
            viewport_state = built.get("viewport_mesh")
            if isinstance(viewport_state, Mapping):
                viewport_failure_reason = str(
                    viewport_state.get("reason") or "visual tessellation unavailable"
                )
            else:
                viewport_failure_reason = "visual tessellation unavailable"
    freshness = compute_freshness(manifest, store, recompute=recompute_freshness)

    findings = list(scope_findings)
    freshness_records = freshness.get("instances") or []
    if freshness.get("verdict") == "unlinked":
        findings.append({"id": freshness["finding_id"], "kind": "freshness", "blocking": True, "verdict": "unlinked"})
    for item in freshness_records:
        if item["verdict"] != "current":
            findings.append({"id": item["finding_id"], "kind": "freshness", "blocking": True, "instance_id": item["instance_id"], "verdict": item["verdict"]})
    # A pre-1.1 bundle with no document signature cannot be compared for
    # staleness. Recording it on the bundle is not enough: an unsurfaced
    # degradation is the silent one this schema version exists to end, so it
    # becomes a finding like any other. Not blocking -- the geometry is fine and
    # solvable, it is only the freshness answer that is unavailable.
    for degradation in getattr(bundle, "degradations", ()):
        findings.append(
            {
                "id": _finding_id("stale-detection-unavailable", degradation),
                "kind": "stale-detection-unavailable",
                "blocking": False,
                "reason": degradation,
            }
        )
    if built["healing"]["performed"]:
        findings.append({"id": _finding_id("healing-performed", {"mode": built["healing"]["mode"], "cache_key": cache_key}), "kind": "healing-performed", "blocking": True, "mode": built["healing"]["mode"]})
    for item in built.get("role_findings", []):
        kind = str(item.get("kind"))
        if kind in {
            "geometry-overrode-paint",
            "source-paint-missing",
            "source-area-drift-override",
        }:
            findings.append({"id": _finding_id(kind, item), "kind": kind, "blocking": True, **{key: value for key, value in item.items() if key != "kind"}})
    for source_id in sorted(skipped_source_ids):
        findings.append({"id": _finding_id("source-skip", source_id), "kind": "source-skip", "blocking": True, "source_id": source_id})
    for source_id, resolution in built.get("role_resolution", {}).items():
        if resolution.get("skipped") and source_id not in skipped_source_ids:
            findings.append({"id": _finding_id("source-skip", {"source_id": source_id, "reason": resolution.get("reason")}), "kind": "source-skip", "blocking": True, "source_id": source_id, "reason": resolution.get("reason")})

    bundle_destination, staged_bundle, staged_bundle_root = _stage_bundle_cas(
        bundle, imports_root
    )
    effective_skipped_source_ids = sorted(
        set(skipped_source_ids)
        | {
            str(source_id)
            for source_id, resolution in built.get("role_resolution", {}).items()
            if resolution.get("skipped")
        }
    )
    effective_mesh = {
        **normalized_mesh,
        "source_size_mm": {
            source_id: size
            for source_id, size in normalized_mesh["source_size_mm"].items()
            if source_id not in effective_skipped_source_ids
        },
    }

    def publish(ingest_id: str, created_at: str) -> str:
        _publish_staged_bundle(bundle_destination, staged_bundle)
        anchor_instance_id = built["normalisation"].get("anchor_instance_id")
        anchor_instance = next(
            (
                instance
                for instance in manifest["instances"]
                if instance["instance_id"] == anchor_instance_id
            ),
            None,
        )
        record: dict[str, Any] = {
            "ingest_id": ingest_id,
            "created_at": created_at,
            "return_id": manifest["return"]["id"],
            "acoustic_domain": "free-space",
            "manifest_sha256": bundle.manifest_sha256,
            "artifact_sha256": bundle.artifact_sha256,
            "bundle_store_path": str(bundle_destination),
            "mesh_store_path": str(mesh_path),
            "mesh_cache_key": cache_key,
            "mesh_content_sha256": mesh_text_sha256(str(built["msh_text"])),
            "mesh_cache_hit": cache_hit,
            "viewport_mesh": (
                {
                    "available": True,
                    "store_path": str(viewport_mesh_path),
                    "cache_key": viewport_cache_key,
                    "content_sha256": viewport_artifact["content_sha256"],
                    "cache_hit": viewport_cache_hit,
                    "transformed_geometry_hash": viewport_artifact[
                        "transformed_geometry_hash"
                    ],
                    "stats": viewport_artifact["stats"],
                    "metadata": viewport_artifact["metadata"],
                }
                if viewport_artifact is not None
                else {
                    "available": False,
                    "reason": viewport_failure_reason
                    or "visual tessellation unavailable",
                }
            ),
            "scope": {
                "status": manifest["scope"]["status"],
                "degraded_skip_count": len(scope_findings),
                "included": manifest["scope"]["included"],
                "skipped": manifest["scope"]["skipped"],
            },
            "evidence": {
                "instances": [
                    {
                        "instance_id": instance["instance_id"],
                        "local_body_state": instance["body_evidence"]["local_body_state"],
                        "baseline_fingerprint": instance["body_evidence"].get("baseline_fingerprint"),
                        "observed_fingerprint": instance["body_evidence"].get("observed_fingerprint"),
                        "observed_at": instance["body_evidence"]["observed_at"],
                    }
                    for instance in manifest["instances"]
                ],
                "fem_air_volumes": manifest["scope"]["fem_air_volumes"],
            },
            "consistency": {"status": "accepted", "checked_instances": len(manifest["instances"])},
            "normalisation": built["normalisation"],
            "anchor": (
                {
                    "instance_id": anchor_instance["instance_id"],
                    "design_id": anchor_instance["design_id"],
                    "throat_frame": built["normalisation"].get(
                        "anchor_throat_frame"
                    ),
                }
                if anchor_instance is not None
                else {
                    "instance_id": None,
                    "design_id": None,
                    "throat_frame": None,
                }
            ),
            "sources": manifest["sources"],
            "mesh_sizes": effective_mesh,
            "skipped_source_ids": effective_skipped_source_ids,
            "role_resolution": built["role_resolution"],
            "role_findings": built.get("role_findings", []),
            "symmetry": built["symmetry"],
            "healing": built["healing"],
            "freshness": freshness,
            "polar_grid_derivation": built["polar_grid_derivation"],
            "sizing_estimate": built["sizing_estimate"],
            "tag_namespace": built["tag_allocation"]["tag_namespace"],
            "tag_map": built["tag_allocation"]["tag_map"],
            "source_tags": built["tag_allocation"]["source_tags"],
            "mesh": {"stats": built["stats"], "metadata": built["metadata"], "integrity": built["integrity"]},
            "transformed_geometry_hash": built["transformed_geometry_hash"],
            "findings": findings,
            "finding_ids": [item["id"] for item in findings],
        }
        record["report_sha256"] = "sha256:" + hashlib.sha256(_canonical(record)).hexdigest()
        return _canonical(record).decode("utf-8")

    try:
        row = store.allocate_ingest(
            manifest_sha256=bundle.manifest_sha256,
            artifact_sha256=bundle.artifact_sha256,
            record_builder=publish,
        )
    finally:
        if staged_bundle_root is not None:
            shutil.rmtree(staged_bundle_root, ignore_errors=True)
    return json.loads(str(row["record_json"]))


def get_ingestion_record(store: CadLinkStore, ingest_id: str) -> dict[str, Any] | None:
    row = store.get_ingest(ingest_id)
    if row is None:
        return None
    return json.loads(str(row["record_json"]))


__all__ = [
    "IngestRefusal",
    "compute_freshness",
    "evaluate_instance_freshness",
    "get_ingestion_record",
    "ingest_bundle",
    "validate_registry_echoes",
    "validate_scope_inventory",
]
