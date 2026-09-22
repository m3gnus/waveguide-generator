"""CAD-return ingestion orchestration, freshness verdicts, and CAS storage."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import stat
import time
from typing import Any

from server.cadlink.isolated import (
    build_imported_mesh_isolated,
    build_imported_viewport_mesh_isolated,
)
from server.cadlink.isolation import ChildRefusal
from server.cadlink.store import CadLinkStore
from server.design.textcfg import parse
from server.exports.geometry_identity import geometry_hash_for_design, normalize_json_value
from server.mesh.imported import (
    ImportedMeshDependencyError,
    RoleResolutionError,
    validate_imported_sizes,
    verify_artifact_reduced_orientation,
)
from server.mesh.artifact import mesh_text_sha256
from server.platform.paths import data_paths
from server.platform.staging import publish_staging_directory
from server.solver.imported import imported_domain_planes

from .solver_frame import (
    AS_MODELLED,
    allowed_axes,
    frame_matrix,
    is_unlinked_manifest,
    record_solver_frame,
    resolve_for_manifest,
)
from .wgreturn import (
    WgReturnBundle,
    WgReturnError,
    WgReturnIntegrityError,
    declared_domain_planes,
    read_wgreturn,
)


logger = logging.getLogger(__name__)


# v4 verifies the auto-cut against the meshed boundary and recentres a
# vertically offset return onto its mirror plane, so a v3 mesh is a different
# artifact for the same inputs and must not be served from the cache.
# v5: the meshing stage now records a geometric self-intersection report, so a
# v4 sidecar would be reused without one and its mesh would go unchecked.
# Element sizing moved from a segments-per-2pi curvature rule to a constant-
# sagitta field (``IMPORTED_SURFACE_DEVIATION_MM``) WITHOUT a contract bump, and
# that is a deliberate decision rather than an oversight.
#
# A bump would be the conservative choice: it would invalidate every cached mesh
# so that no design could hold one mesh from each rule. It would also silently
# re-mesh every existing CAD project on its next ingest and move its acoustics,
# for projects whose owners did not ask for a new mesh. the maintainer chose to leave
# existing projects alone and have the new rule govern from the next ingest
# onwards (2026-08-27).
#
# What makes that safe rather than merely cheap is that the two rules are
# already distinguishable per mesh: ``build_imported_mesh`` records its OCC
# settings under ``occ_tessellation``, where a segments mesh carries
# ``curvature_segments`` and a sagitta mesh carries ``surface_deviation_mm``.
# The mix is visible in the artifact, not hidden behind a shared version string.
# Anything that changes a project's inputs re-keys it into the new rule anyway.
IMPORT_MESH_PIPELINE_CONTRACT = "wg-import-solve-v5"
IMPORT_VIEWPORT_PIPELINE_CONTRACT = "wg-import-viewport-v1"
# The semantics every v5 key so far was made under: the fingerprint of
# ``meshing_semantics()`` as those constants stand. A mesh key names the
# semantics only when they differ from these. So no existing key changes and no
# project re-meshes (the 2026-08-27 decision above still holds), while any later
# change of a WG-internal sizing constant, with every keyed input unchanged, is
# a distinct preparation identity (docs/architecture/CAD-OPERATIONS.md,
# "Preparation identity"). Retained runs keep the mesh their record names.
_BASELINE_MESHING_SEMANTICS = "sha256:79aa14dff0812302be2bd64c494913fd39f370466ca80458ce285f6a63c173c8"


def meshing_semantics() -> dict[str, Any]:
    """The WG-internal constants that decide what a solver mesh is for given inputs."""

    from server.mesh import imported as meshing

    semantics = {
        "surface_deviation_mm": meshing.IMPORTED_SURFACE_DEVIATION_MM,
        "surface_deviation_min_mm": meshing.IMPORTED_SURFACE_DEVIATION_MIN_MM,
        "surface_deviation_max_mm": meshing.IMPORTED_SURFACE_DEVIATION_MAX_MM,
        "sagitta_flat_curvature": meshing._SAGITTA_FLAT_CURVATURE,
        "sagitta_grid_samples": meshing._SAGITTA_GRID_SAMPLES,
        "sagitta_quantise_log": meshing._SAGITTA_QUANTISE_LOG,
    }
    # Rounded: a constant computed at import (``math.log``) may differ in its
    # last bit between platforms' maths libraries, and the fingerprint must not.
    return {
        name: round(value, 12) if isinstance(value, float) else value
        for name, value in semantics.items()
    }


def meshing_semantics_fingerprint() -> str:
    return "sha256:" + hashlib.sha256(_canonical(meshing_semantics())).hexdigest()


def _semantics_key_entry() -> dict[str, str]:
    fingerprint = meshing_semantics_fingerprint()
    return {} if fingerprint == _BASELINE_MESHING_SEMANTICS else {"meshing_semantics": fingerprint}


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


def instance_identity_inventory(
    manifest: Mapping[str, Any], *, selected_instance_id: str | None
) -> dict[str, Any]:
    """Project the return's body/placement/source/channel addressing graph.

    ``design_id`` and ``export_id`` intentionally do not appear as join keys:
    repeated placements can share both.  Every downstream identity is rooted
    at the CAD-authored ``instance_id`` and keeps the artifact's native body,
    source, and default-channel addresses intact.
    """

    raw_sources = list(manifest["sources"])
    included = list(manifest["scope"]["included"])
    inventory = []
    for instance in manifest["instances"]:
        instance_id = str(instance["instance_id"])
        sources = [
            source
            for source in raw_sources
            if source.get("instance_id") == instance_id
        ]
        inventory.append(
            {
                "instance_id": instance_id,
                "design_id": instance.get("design_id"),
                "body_object_ids": sorted(
                    str(body["object_id"])
                    for body in included
                    if body.get("wglink_instance_id") == instance_id
                ),
                "assembly_from_link": instance["assembly_from_link"],
                "source_ids": sorted(str(source["id"]) for source in sources),
                "default_drive_channel_ids": sorted(
                    {str(source["default_drive_channel_id"]) for source in sources}
                ),
            }
        )
    return {
        "schema_version": 1,
        "selected_instance_id": selected_instance_id,
        "solver_anchor_instance_id": manifest["coordinate_system"].get(
            "solver_anchor_instance_id"
        ),
        "instances": sorted(inventory, key=lambda item: item["instance_id"]),
    }


#: Bodies left out on purpose -- hidden, or excluded with Declare Body -- are
#: information, never a blocking finding, whatever severity an older add-in
#: states. One carrying a painted source is refused by the add-in itself.
_DELIBERATE_SKIPS = frozenset({"hidden_body", "excluded_body"})


def _scope_findings(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for index, skip in enumerate(manifest["scope"]["skipped"]):
        if skip.get("severity") != "degraded" or skip.get("kind") in _DELIBERATE_SKIPS:
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


CAD_DOCUMENT_PURPOSE = "cad-document"


def cad_document_member(manifest: Mapping[str, Any]) -> str | None:
    """The captured native CAD file in a return bundle, if the add-in wrote one."""

    files = manifest.get("files")
    if not isinstance(files, Mapping):
        return None
    for name, record in files.items():
        if isinstance(record, Mapping) and record.get("purpose") == CAD_DOCUMENT_PURPOSE:
            return str(name)
    return None


#: How long a `.wg2-import-bundle-*` directory must have gone untouched before
#: it counts as abandoned. A staging directory is only ever a copy of one
#: bundle, whose largest member is capped at `MAX_STEP_INPUT_BYTES`, so no live
#: copy can still be running a day after its directory was last written to --
#: while a copy that a kill or a power cut left behind never will be again.
_ABANDONED_STAGING_AGE = 24 * 60 * 60
_STAGING_PREFIX = ".wg2-import-bundle-"
#: A retained copy that was replaced waits here, beside the copy that replaced
#: it, until the publication it was moved aside for has gone through.
_SUPERSEDED_SUFFIX = ".superseded-"


def _is_the_bundle(copy: Path, bundle: WgReturnBundle) -> bool:
    """Whether the tree at `copy` still is the bundle whose digest names it.

    Re-reading is what makes this a check rather than a hope: `read_wgreturn`
    hashes every member against the manifest's own table, and derives
    `manifest_sha256` from the manifest bytes. So an equal `manifest_sha256`
    says the whole tree is byte-for-byte what was accepted -- apart from the
    captured CAD document, which WG deliberately does not keep.

    The `artifact_sha256` comparison below is *implied* by that equality rather
    than independent of it -- it is the assembly's declared hash, read out of
    the same manifest (`wgreturn.py`, `read_wgreturn`). It is kept as a cheap
    restatement of what this copy is for, not as a second check.
    """

    try:
        found = read_wgreturn(copy, absent_purposes=frozenset({CAD_DOCUMENT_PURPOSE}))
    except (WgReturnError, OSError, ValueError):
        return False
    return (
        found.manifest_sha256 == bundle.manifest_sha256
        and found.artifact_sha256 == bundle.artifact_sha256
    )


def _copy_member(source: str, destination: str) -> None:
    """`shutil.copy2`, and then get the bytes it wrote onto the disk.

    `os.replace` orders the copy against concurrent *readers*; it is not a
    durability barrier. On APFS, and on ext4's default `data=ordered`, the
    rename can reach stable storage while the file data has not, so an unclean
    shutdown can leave a tree of holes under a name that asserts a manifest
    digest -- the one corruption content addressing cannot express.

    The flush is on a descriptor opened for *writing*, as every other flush in
    this application is. `os.fsync` is `_commit()` on Windows, which calls
    `FlushFileBuffers`; that API wants `GENERIC_WRITE` on the handle and fails
    with `ERROR_ACCESS_DENIED` otherwise, so flushing the read descriptor a
    verification pass would open is documented to fail there.

    A filesystem that refuses the flush anyway costs this copy its durability
    across a crash, and nothing else: a torn copy is caught and replaced when
    it is read (`_is_the_bundle`, `preparation._retained_manifest`). Failing
    the retention instead would take CAD Link down for the sake of hardening.
    """

    shutil.copy2(source, destination)
    # `copy2` preserves the source's mode, and a member the user or Fusion
    # marked read-only cannot then be opened for writing -- on Windows that is
    # the read-only attribute, and the open fails there too. The write bit goes
    # back before the copy is published, so the member keeps the mode it came
    # with; without this the flush is silently skipped for exactly those files.
    restore: int | None = None
    try:
        mode = os.stat(destination).st_mode
        if not mode & stat.S_IWUSR:
            os.chmod(destination, mode | stat.S_IWUSR)
            restore = mode
        descriptor = os.open(destination, os.O_WRONLY)
    except OSError as exc:
        # Everything here is best effort, including finding out the mode: the
        # docstring above promises that a flush WG cannot perform costs this
        # copy its durability and nothing else, and raising out of the copy
        # would cost the retention instead.
        logger.warning("Could not open %s to flush it: %s", Path(destination).name, exc)
        _restore_mode(destination, restore)
        return
    try:
        os.fsync(descriptor)
    except OSError as exc:
        logger.warning(
            "This filesystem refused to flush %s, so WG's copy of it is not "
            "crash-durable; a torn copy is replaced when it is read. (%s)",
            Path(destination).name,
            exc,
        )
    finally:
        os.close(descriptor)
        _restore_mode(destination, restore)


def _restore_mode(path: str, mode: int | None) -> None:
    """Put back a mode `_copy_member` widened so it could flush the file."""

    if mode is None:
        return
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def _flush_tree(staged: Path) -> None:
    """Flush the staged tree's directories; `_copy_member` flushed its files.

    The directory entries matter as much as the bytes: a member name may carry
    path segments (`wgreturn.py`, `_portable_member_name`), and a rename that
    is durable while an intermediate directory's entry is not leaves exactly
    the tree-of-holes state the file flushes exist to prevent.
    """

    for member in sorted(staged.rglob("*")):
        if member.is_dir() and not member.is_symlink():
            _flush_directory(member)
    _flush_directory(staged)


def _flush_directory(path: Path) -> None:
    """Best effort: a directory cannot be opened for `fsync` on Windows."""

    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _sweep_abandoned_staging(parent: Path, keep: Path) -> None:
    """Remove staging leftovers nothing can still be using.

    Only a staging directory or a superseded copy, only directories, and only
    by the directory's own modification time, so a copy another attempt is
    making right now is left alone -- and a retained `<digest>.wgreturn` copy,
    however old, is never a candidate.
    """

    cutoff = time.time() - _ABANDONED_STAGING_AGE
    try:
        candidates = list(parent.iterdir())
    except OSError:
        return
    for candidate in candidates:
        leftover = (
            candidate.name.startswith(_STAGING_PREFIX) or _SUPERSEDED_SUFFIX in candidate.name
        )
        if candidate == keep or not leftover:
            continue
        try:
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            if candidate.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        logger.info("Removing an abandoned import staging leftover: %s", candidate.name)
        shutil.rmtree(candidate, ignore_errors=True)


def _stage_bundle_cas(
    bundle: WgReturnBundle, imports_root: Path
) -> tuple[Path, Path | None, Path | None, bool]:
    """Copy a verified bundle completely before entering the registry transaction."""

    destination = imports_root / "bundles" / f"{bundle.manifest_sha256.removeprefix('sha256:')}.wgreturn"
    replacing = False
    if destination.is_dir():
        if _is_the_bundle(destination, bundle):
            # Content addressing doing its job: the same return is kept once.
            return destination, None, None, False
        # The copy under this digest is not the bundle the digest names -- a
        # torn write, a truncated member, an edit. The path is the only claim
        # anything downstream has about the content, so WG does not hand it out
        # again; it has verified bytes in its hands right now, and puts those
        # there instead.
        logger.warning(
            "The retained copy %s is not the bundle it is named for; replacing it.",
            destination.name,
        )
        replacing = True
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = publish_staging_directory(destination.parent, _STAGING_PREFIX)
    staged = temporary / destination.name
    # A captured Fusion archive is tens of megabytes and is not geometry WG
    # solves from -- it is there for the user's own archive. Copying it here too
    # would put a second copy in the hidden data directory for every return.
    document = cad_document_member(bundle.manifest)
    excluded = {Path(document).name} if document else set()
    try:
        shutil.copytree(
            bundle.path,
            staged,
            symlinks=False,
            ignore=lambda _directory, names: [name for name in names if name in excluded],
            copy_function=_copy_member,
        )
        if not _is_the_bundle(staged, bundle):
            # `copytree` re-reads the source, so the bundle verified above is
            # not necessarily the bundle that was copied: it can have been
            # replaced in between by a different, internally valid one. Those
            # bytes are not what this digest names, and are refused rather than
            # published under it.
            raise WgReturnIntegrityError(
                "The return bundle changed while WG was retaining it. "
                "Send it again from Fusion."
            )
        _flush_tree(staged)
        _sweep_abandoned_staging(destination.parent, temporary)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, staged, temporary, replacing


def retained_snapshot_path(data_dir: str | Path, manifest_sha256: str) -> Path:
    """Where WG keeps its copy of the return whose manifest hashes to this."""

    digest = str(manifest_sha256).removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"not a manifest hash: {manifest_sha256!r}")
    return data_paths(data_dir).root / "imports" / "bundles" / f"{digest}.wgreturn"


def read_snapshot(bundle_path: str | Path, *, retained: bool = False) -> WgReturnBundle:
    """Read a return, or WG's retained copy of one, which has no captured CAD document."""

    if not retained:
        return read_wgreturn(bundle_path)
    return read_wgreturn(bundle_path, absent_purposes=frozenset({CAD_DOCUMENT_PURPOSE}))


def retain_snapshot(
    bundle_path: str | Path,
    data_dir: str | Path,
    *,
    expected_manifest_sha256: str | None = None,
) -> dict[str, str]:
    """Verify a return bundle and keep its bytes in WG's own storage.

    A snapshot is accepted only once this returns (CAD-OPERATIONS.md, "Artifact
    acceptance"): the copy under ``<data>/imports/bundles`` is content-addressed
    by the manifest, so the same snapshot is kept once, and preparation reads
    the copy, never the exchange folder. A bundle whose manifest no longer
    hashes to ``expected_manifest_sha256`` is refused.
    """

    bundle = read_wgreturn(bundle_path)
    if expected_manifest_sha256 is not None:
        expected = expected_manifest_sha256
        if not expected.startswith("sha256:"):
            expected = f"sha256:{expected}"
        if bundle.manifest_sha256 != expected:
            raise WgReturnError(
                "The return bundle changed after Fusion asked WG to solve it. "
                "Send it again from Fusion."
            )
    imports_root = data_paths(data_dir).root / "imports"
    destination, staged, staged_root, replacing = _stage_bundle_cas(bundle, imports_root)
    try:
        _publish_staged_bundle(destination, staged, replace_existing=replacing)
    finally:
        if staged_root is not None:
            shutil.rmtree(staged_root, ignore_errors=True)
    return {
        "manifest_sha256": bundle.manifest_sha256,
        "artifact_sha256": bundle.artifact_sha256,
        "retained_path": str(destination),
    }


def _publish_staged_bundle(
    destination: Path, staged: Path | None, *, replace_existing: bool = False
) -> None:
    if staged is None:
        return
    superseded: Path | None = None
    if destination.is_dir():
        if not replace_existing:
            # Another attempt published the same digest while this one copied.
            # Both copies verified, so either is the bundle: the first one wins
            # and this one is thrown away with its staging directory.
            return
        # A directory cannot be renamed over on either platform, so the copy WG
        # could not verify moves aside first. It moves to a *sibling*, not into
        # the staging directory: that directory is removed unconditionally when
        # retention ends, and `_is_the_bundle` also reports False for a copy it
        # merely could not open, so a transiently unreadable but intact copy
        # would otherwise be destroyed along with the replacement that failed
        # to land. Here a stop between the two renames leaves it for the sweep.
        superseded = destination.parent / (
            f"{destination.name}{_SUPERSEDED_SUFFIX}{secrets.token_hex(6)}"
        )
        try:
            os.replace(destination, superseded)
        except FileNotFoundError:
            superseded = None  # another attempt has already dealt with it
        else:
            # A rename does not touch the inode's mtime, so without this the
            # copy arrives here carrying however old the retained copy was --
            # and the sweep, which goes by mtime, would be free to take it
            # from a concurrent attempt still between these two renames.
            with contextlib.suppress(OSError):
                os.utime(superseded, None)
    try:
        os.replace(staged, destination)
    except OSError:
        if not destination.is_dir():
            raise
    _flush_directory(destination.parent)
    if superseded is not None:
        # Only now: the copy it replaced is on disk until the replacement is.
        shutil.rmtree(superseded, ignore_errors=True)


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

    solver_frame_axis = options.get("solver_frame")
    transform = (
        rigid_inverse(anchor["assembly_from_link"]).tolist()
        if anchor is not None
        else frame_matrix(str(solver_frame_axis)).tolist()
        if solver_frame_axis is not None
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
        **_semantics_key_entry(),
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
                **_semantics_key_entry(),
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


def _judge_cached_reduced_winding(
    built: Mapping[str, Any], cache_key: str
) -> dict[str, Any] | None:
    """Accept a cached reduced mesh only once its winding has been judged.

    ``verify_reduced_orientation`` runs where a reduced mesh is built, and
    nothing in the cache key names the rule the cached mesh was wound by: a
    quarter an earlier build cached -- wound from a rear-facing source, inside
    out -- sits under the very key a current build computes. Bumping the
    contract would re-mesh every project (the 2026-08-27 decision above), so
    the cached arrays are judged here instead. An inverted or unreadable one is
    a miss and is rebuilt; a sound one that predates the verdict gains it.
    """

    planes = imported_domain_planes({"symmetry": built.get("symmetry")})
    if not planes:
        return dict(built)
    try:
        orientation = verify_artifact_reduced_orientation(
            str(built["msh_text"]), cut_planes=planes
        )
    except ValueError as exc:
        logger.warning("Cached imported mesh %s is unreadable (%s); rebuilding it", cache_key, exc)
        return None
    if orientation["inverted_component_count"]:
        logger.warning(
            "Cached imported mesh %s has %s of %s reduced component(s) wound against "
            "the model they mirror; rebuilding it",
            cache_key,
            orientation["inverted_component_count"],
            orientation["component_count"],
        )
        return None
    accepted = dict(built)
    verification = accepted.get("symmetry_verification")
    if isinstance(verification, Mapping) and "reduced_orientation" not in verification:
        accepted["symmetry_verification"] = {
            **verification,
            "reduced_orientation": orientation,
        }
    return accepted


def _load_cached_mesh(mesh_path: Path, metadata_path: Path) -> dict[str, Any] | None:
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


def _write_cache(mesh_path: Path, metadata_path: Path, result: Mapping[str, Any]) -> None:
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = publish_staging_directory(mesh_path.parent, ".wg2-import-mesh-")
    try:
        staged_mesh = temporary / mesh_path.name
        staged_meta = temporary / metadata_path.name
        msh_text = str(result["msh_text"])
        staged_mesh.write_text(msh_text, encoding="utf-8")
        sidecar = {
            key: value
            for key, value in result.items()
            if key not in {"msh_text", "viewport_msh_text"}
        }
        # The sidecar is the cache commit marker. Binding it to the exact mesh
        # bytes makes a replaced/truncated artifact a miss instead of minting a
        # new ingestion record that blesses the altered bytes as solver input.
        sidecar["content_sha256"] = mesh_text_sha256(msh_text)
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
    temporary = publish_staging_directory(mesh_path.parent, ".wg2-import-viewport-")
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


def _viewport_paths(imports_root: Path, cache_key: str) -> tuple[Path, Path]:
    root = imports_root / "viewports"
    return root / f"{cache_key}.msh", root / f"{cache_key}.json"


def _viewport_index_path(imports_root: Path, lookup_key: str) -> Path:
    return imports_root / "viewports" / "index" / f"{lookup_key}.txt"


def _read_cas_index(index_path: Path) -> str | None:
    """The 64-hex content key an index entry names, or None if unusable."""

    try:
        indexed = index_path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if len(indexed) == 64 and all(character in "0123456789abcdef" for character in indexed):
        return indexed
    return None


def _write_cas_index(index_path: Path, cache_key: str) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")
    temporary.write_text(cache_key + "\n", encoding="ascii")
    os.replace(temporary, index_path)


def _publish_viewport_artifact(
    imports_root: Path,
    generated: Mapping[str, Any],
    *,
    transformed_geometry_hash: str,
    healing: Mapping[str, Any],
    lookup_key: str,
) -> tuple[dict[str, Any], str, Path]:
    """Store one display tessellation under its own content digest.

    The digest is the file name, so a reader that finds this artifact through
    the lookup index can prove it read the bytes that were written without
    consulting any record. That is what lets the deferred build publish into a
    record that was already sealed.
    """

    digest = mesh_text_sha256(str(generated["msh_text"]))
    cache_key = digest.removeprefix("sha256:")
    mesh_path, metadata_path = _viewport_paths(imports_root, cache_key)
    artifact = _write_viewport_cache(
        mesh_path,
        metadata_path,
        generated,
        transformed_geometry_hash=transformed_geometry_hash,
        healing=healing,
    )
    try:
        _write_cas_index(_viewport_index_path(imports_root, lookup_key), cache_key)
    except OSError:
        # The content-addressed artifact and record remain valid; a later
        # ingest can simply regenerate the lookup index.
        pass
    return artifact, cache_key, mesh_path


def _resolve_project(
    store: CadLinkStore,
    design_id: str | None,
    native_id: str | None,
    document_name: str | None,
) -> dict[str, Any] | None:
    """Which project this return belongs to, claiming one if it has none.

    A WG-originated return already has a project: the lineage of the design it
    was exported from. Geometry authored in CAD has no design at all, and used
    to end up in no project either -- so its runs carried no lineage and were
    dropped from the very history they were listed above, and its captured
    document was filed under a folder no run ever wrote to. For those, the
    Fusion document itself is the project.
    """

    lineage_id: str | None = None
    if design_id:
        row = store.get_design(design_id)
        lineage_id = str((row or {}).get("lineage_id") or "").strip() or None
        if lineage_id:
            store.record_cad_document(lineage_id, native_id, document_name)
    elif native_id:
        lineage_id = store.claim_cad_document_lineage(native_id, document_name)
    if not lineage_id:
        return None
    return {
        "lineage_id": lineage_id,
        "design_id": design_id,
        "document_native_id": native_id,
        "document_name": document_name,
        # The one folder the captured document and every run of it share.
        "archive_stem": store.claim_archive_stem(
            lineage_id, preferred=document_name
        ),
    }


def _design_target_hint(store: CadLinkStore, design_ids: set[str]) -> str:
    """Name the design a return targets, as the user would recognise it.

    The registry filename is what the CAD-linked designs list shows, so it is
    what a refusal has to say. A design id that this workspace has never seen
    is the other actionable case and must not read as a mere identifier: the
    user's next move is to open that workspace, not to hunt for the design in
    this one.
    """

    described: list[str] = []
    for design_id in sorted(design_ids):
        filename = ""
        try:
            row = store.get_design(design_id)
        except Exception:  # pragma: no cover - a lookup must not mask the gate
            row = None
        if isinstance(row, Mapping):
            filename = str(row.get("filename") or "").strip()
        described.append(
            f"{filename} · {design_id}" if filename
            else f"{design_id}, which is not in this workspace"
        )
    return "; ".join(described)


def _requested_solver_frame(
    manifest: Mapping[str, Any], prep_options: Mapping[str, Any] | None
) -> str:
    """The solver frame axis this preparation meshes an unlinked return in."""

    requested = (prep_options or {}).get("solver_frame")
    if requested is None:
        return AS_MODELLED
    try:
        frame_matrix(requested)
    except ValueError as exc:
        raise IngestRefusal("stage 4 STEP import + normalisation", f"solver frame: {exc}") from exc
    if not is_unlinked_manifest(manifest):
        if requested == AS_MODELLED:
            return AS_MODELLED
        raise IngestRefusal(
            "stage 4 STEP import + normalisation",
            "solver frame: this return is linked to a WG design and is solved in its "
            "design's frame; a solver frame applies only to a model authored in CAD",
        )
    if requested not in allowed_axes(manifest):
        raise IngestRefusal(
            "stage 4 STEP import + normalisation",
            "solver frame: this return is declared as a half or quarter model and is "
            f"solved only in the frame it was modelled in ({AS_MODELLED})",
        )
    return str(requested)


def ingest_bundle(
    bundle_path: str | Path,
    mesh: Mapping[str, Any],
    skipped_source_ids: list[str],
    store: CadLinkStore,
    data_dir: str | Path,
    *,
    prep_options: Mapping[str, Any] | None = None,
    expected_design_id: str | None = None,
    expected_instance_id: str | None = None,
    recompute_freshness: Callable[[Mapping[str, Any]], str] | None = None,
    defer_viewport: bool = False,
    commit_guard: Callable[[Any], bool] | None = None,
    retained_copy: bool = False,
    resolve_confirmed_frame: bool = False,
) -> dict[str, Any]:
    """Run the nine ingestion stages and persist the immutable WG verdict.

    ``resolve_confirmed_frame`` meshes a return with no WG instance in the
    solver frame its project confirmed (``solver_frame.resolve_for_manifest``),
    as modelled until one is; it is how a UI's ingest shows what would be
    solved without the request naming a frame. Backend preparation resolves the
    frame itself and passes it in ``prep_options``.

    ``commit_guard`` is a preparation attempt's fence
    (``CadLinkStore.attempt_is_current``): it runs inside the transaction that
    publishes the record, so an attempt that lost its operation commits nothing.
    ``retained_copy`` says ``bundle_path`` is WG's retained copy of a return
    (``retain_snapshot``), which carries no captured CAD document.

    ``defer_viewport`` publishes the record as soon as the solver mesh exists,
    leaving the display tessellation to :func:`build_deferred_viewport`. That
    tessellation is two thirds of a cold ingestion's wall clock and nothing
    downstream of the record needs it, so waiting for it only delays the moment
    the user can see the geometry they asked WG to solve. A cached display
    artifact is still adopted here: it costs a file read, not a mesh.
    """

    try:
        bundle = read_snapshot(bundle_path, retained=retained_copy)
    except Exception as exc:
        raise IngestRefusal("stage 1 bundle validation", str(exc)) from exc
    manifest = bundle.manifest
    # The solver frame of a return with no WG instance (server/cadlink/
    # solver_frame.py). Checked before any gate: it is a statement about which
    # frame this preparation meshes in, and a linked return is always meshed in
    # its anchor's frame. The modelled frame is today's and is never written
    # into the options, so every mesh cached before the contract keeps its key.
    if resolve_confirmed_frame and is_unlinked_manifest(manifest):
        resolved = resolve_for_manifest(store, manifest, bundle.manifest_sha256)
        prep_options = {
            **dict(prep_options or {}),
            "solver_frame": resolved.axis if resolved is not None else AS_MODELLED,
        }
    solver_frame_axis = _requested_solver_frame(manifest, prep_options)
    returned_design_ids = {
        str(instance["design_id"])
        for instance in manifest["instances"]
        if isinstance(instance, Mapping) and instance.get("design_id")
    }
    # A return that names its design names its target, and that name is the
    # only thing here that can. This gate used to fall open whenever the caller
    # named no design -- which is exactly what an unlinked open model sends --
    # so a return exported from design A was prepared into whatever the app
    # happened to have open: the right geometry, silently, in the wrong
    # project. A named target is required whenever the bundle carries one, and
    # the fallback is now a refusal the user can act on rather than a build.
    #
    # A bundle that names no design at all is CAD-authored geometry, which has
    # no other project to belong to. That half stays open on purpose: closing
    # it would break the whole author-in-CAD workflow.
    if returned_design_ids:
        if expected_design_id is None:
            raise IngestRefusal(
                "stage 2 project gate",
                "this CAD return belongs to the WG design it was exported from "
                f"({_design_target_hint(store, returned_design_ids)}); open that "
                "design from File → CAD-linked designs before preparing it",
            )
        if expected_design_id not in returned_design_ids:
            raise IngestRefusal(
                "stage 2 project gate",
                "the selected CAD return belongs to another CAD-linked project "
                f"({_design_target_hint(store, returned_design_ids)}); "
                "open that project from File → CAD-linked designs before preparing it",
            )
    matching_design_instances = [
        instance
        for instance in manifest["instances"]
        if expected_design_id is not None
        and instance.get("design_id") == expected_design_id
    ]
    if expected_instance_id is None and len(matching_design_instances) > 1:
        raise IngestRefusal(
            "stage 2 instance gate",
            "the selected CAD return contains more than one instance of this "
            "design; choose the linked instance before preparing it",
        )
    resolved_instance_id = expected_instance_id
    if resolved_instance_id is None and len(matching_design_instances) == 1:
        resolved_instance_id = str(matching_design_instances[0]["instance_id"])
    elif (
        resolved_instance_id is None
        and expected_design_id is None
        and len(manifest["instances"]) == 1
    ):
        resolved_instance_id = str(manifest["instances"][0]["instance_id"])
    if resolved_instance_id is not None:
        selected_instances = [
            instance
            for instance in manifest["instances"]
            if instance.get("instance_id") == resolved_instance_id
        ]
        if len(selected_instances) != 1:
            raise IngestRefusal(
                "stage 2 instance gate",
                f"selected instance {resolved_instance_id!r} is not present exactly once",
            )
        selected_instance = selected_instances[0]
        if (
            expected_design_id is not None
            and selected_instance.get("design_id") != expected_design_id
        ):
            raise IngestRefusal(
                "stage 2 instance gate",
                f"selected instance {resolved_instance_id!r} belongs to another design",
            )
        anchor_instance_id = manifest["coordinate_system"].get(
            "solver_anchor_instance_id"
        )
        if anchor_instance_id is not None and anchor_instance_id != resolved_instance_id:
            raise IngestRefusal(
                "stage 2 instance gate",
                f"selected instance {resolved_instance_id!r} is not the return's "
                f"solver anchor {anchor_instance_id!r}",
            )
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
    options.pop("solver_frame", None)
    if solver_frame_axis != AS_MODELLED:
        options["solver_frame"] = solver_frame_axis
    # The domain declaration comes from the CAD bundle, never from the request:
    # it is a statement about the geometry that arrived, and a caller must not
    # be able to assert it over the top of one. It joins the options here so it
    # is part of the mesh cache key -- the same STEP declared differently is a
    # different solve.
    options["declared_cut_planes"] = list(declared_domain_planes(manifest))
    imports_root = data_paths(data_dir).root / "imports"
    viewport_lookup_key = _viewport_cache_lookup_key(
        bundle, manifest, skipped_source_ids, options
    )
    viewport_index_path = _viewport_index_path(imports_root, viewport_lookup_key)
    viewport_cache_key = _read_cas_index(viewport_index_path)
    viewport_mesh_path, viewport_metadata_path = _viewport_paths(
        imports_root, viewport_cache_key or ".pending"
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
    cache_key = _read_cas_index(index_path)
    mesh_path = imports_root / "meshes" / f"{cache_key}.msh" if cache_key else imports_root / "meshes" / ".pending.msh"
    metadata_path = imports_root / "meshes" / f"{cache_key}.json" if cache_key else imports_root / "meshes" / ".pending.json"
    built = _load_cached_mesh(mesh_path, metadata_path) if cache_key else None
    if built is not None:
        try:
            expected_cache_key = _cache_key(
                bundle,
                manifest,
                normalized_mesh,
                skipped_source_ids,
                options,
                str(built["transformed_geometry_hash"]),
            )
        except (KeyError, TypeError, ValueError):
            built = None
        else:
            # The lookup index is only a shortcut from immutable bundle inputs
            # to measured geometry. It is not itself evidence: require the
            # sidecar's stored geometry hash to reproduce the indexed CAS key.
            if expected_cache_key != cache_key:
                built = None
    if built is not None:
        built = _judge_cached_reduced_winding(built, str(cache_key))
    cache_hit = built is not None
    if built is None:
        try:
            # A returned bundle's assembly.step is external CAD, so it is
            # opened in a disposable child with its own deadline and memory
            # budget (``docs/plans/STEP-PARSER-ISOLATION.md``). A crash, hang,
            # or over-budget parse in there is this refusal, and there is no
            # in-process retry behind it.
            built = build_imported_mesh_isolated(
                bundle.assembly_path,
                manifest,
                normalized_mesh,
                skipped_source_ids=skipped_source_ids,
                options=options,
                include_viewport_mesh=viewport_artifact is None and not defer_viewport,
                expected_sha256=bundle.artifact_sha256,
                expected_size_bytes=bundle.artifact_size_bytes,
            )
        except ImportedMeshDependencyError:
            raise
        except ChildRefusal as exc:
            # A refusal from a child that ran is already in the server log with
            # its exit and output tail; the user's refusal keeps stage and wording.
            raise IngestRefusal(exc.stage, exc.detail) from exc
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
        _write_cas_index(index_path, cache_key)
    assert built is not None
    assert cache_key is not None

    viewport_failure_reason: str | None = None
    viewport_deferred = False
    if viewport_artifact is None and defer_viewport:
        viewport_deferred = True
    elif viewport_artifact is None:
        generated_viewport: dict[str, Any] | None = None
        if built.get("viewport_msh_text") is not None:
            generated_viewport = {
                "msh_text": built["viewport_msh_text"],
                "stats": built["viewport_mesh"]["stats"],
                "metadata": built["viewport_mesh"]["metadata"],
            }
        elif cache_hit and isinstance(built.get("viewport_recipe"), Mapping):
            try:
                generated_viewport = build_imported_viewport_mesh_isolated(
                    bundle.assembly_path,
                    manifest,
                    built["viewport_recipe"],
                    expected_geometry_hash=str(built["transformed_geometry_hash"]),
                    tag_allocation=built["tag_allocation"],
                    expected_sha256=bundle.artifact_sha256,
                    expected_size_bytes=bundle.artifact_size_bytes,
                )
            except Exception as exc:
                viewport_failure_reason = f"{type(exc).__name__}: {exc}"
        if generated_viewport is not None:
            try:
                (
                    viewport_artifact,
                    viewport_cache_key,
                    viewport_mesh_path,
                ) = _publish_viewport_artifact(
                    imports_root,
                    generated_viewport,
                    transformed_geometry_hash=str(built["transformed_geometry_hash"]),
                    healing=built["healing"],
                    lookup_key=viewport_lookup_key,
                )
            except Exception as exc:
                viewport_artifact = None
                viewport_failure_reason = f"{type(exc).__name__}: {exc}"
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
        findings.append({"id": freshness["finding_id"], "kind": "freshness", "blocking": False, "verdict": "unlinked"})
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
    verification = built.get("symmetry_verification")
    verification = verification if isinstance(verification, Mapping) else {}
    declared_planes = list(verification.get("declared_cut_planes") or [])
    if declared_planes:
        # Not a warning: the reduction was asked for, checked against the mesh,
        # and granted. It is recorded so the run says which domain it solved.
        findings.append(
            {
                "id": _finding_id(
                    "declared-reduced-domain",
                    {"cache_key": cache_key, "planes": declared_planes},
                ),
                "kind": "declared-reduced-domain",
                "blocking": False,
                "declared_cut_planes": declared_planes,
                "detail": (
                    "the return declares it was already cut on "
                    + ", ".join(declared_planes)
                    + "; the meshed boundary confirms each plane is open, and "
                    "the solver mirrors it rather than solving a partial model."
                ),
            }
        )
    undeclared = [
        str(plane) for plane in (verification.get("undeclared_open_planes") or [])
    ]
    if undeclared:
        # Blocking, because nothing else in the pipeline can tell the difference
        # and the wrong answer is silent: an already-cut model solved whole
        # radiates through the open cut face. The remedy is one dropdown in CAD.
        findings.append(
            {
                "id": _finding_id(
                    "undeclared-reduced-domain",
                    {"cache_key": cache_key, "planes": undeclared},
                ),
                "kind": "undeclared-reduced-domain",
                "blocking": True,
                "detected_planes": undeclared,
                "detail": (
                    "this model is open on "
                    + ", ".join(undeclared)
                    + " with all of its geometry on one side, which is what a "
                    "model already cut in half looks like. It was returned as a "
                    "full model, so WG will solve it whole and the open face "
                    "will radiate. If it is a half, set Model domain in the "
                    "Fusion Send dialog and return it again."
                ),
            }
        )
    fallback = verification.get("fallback")
    if isinstance(fallback, Mapping):
        # Blocking, like every other finding that changes what is solved: the
        # solve is honest but slower than the design deserves, and the user is
        # the only one who can decide whether to re-export or accept the cost.
        planes = ", ".join(str(plane) for plane in fallback.get("rejected_cut_planes") or [])
        findings.append(
            {
                "id": _finding_id(
                    "symmetry-cut-unverified",
                    {"cache_key": cache_key, "reason": fallback.get("reason")},
                ),
                "kind": "symmetry-cut-unverified",
                "blocking": True,
                "rejected_cut_planes": list(fallback.get("rejected_cut_planes") or []),
                "detected_planes": list(fallback.get("detected_planes") or []),
                "capped_planes": list(fallback.get("capped_planes") or []),
                "off_plane_free_edge_count": fallback.get("off_plane_free_edge_count"),
                "detail": (
                    f"the {planes or 'symmetry'} reduction was discarded because "
                    f"{fallback.get('reason')}. The full domain was meshed and will be "
                    "solved instead, at 2-4x the cost. "
                    + str(
                        fallback.get("remedy")
                        or "Re-export a stitched, watertight body to regain the reduction."
                    )
                ),
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

    (
        bundle_destination,
        staged_bundle,
        staged_bundle_root,
        replacing_bundle,
    ) = _stage_bundle_cas(bundle, imports_root)
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

    anchor_instance_id = built["normalisation"].get("anchor_instance_id")
    anchor_design_id = next(
        (
            str(instance["design_id"])
            for instance in manifest["instances"]
            if instance["instance_id"] == anchor_instance_id
            and instance.get("design_id")
        ),
        None,
    )
    document_meta = manifest.get("document") or {}
    document_native_id = str(document_meta.get("native_id") or "").strip() or None
    document_name = str(document_meta.get("name") or "").strip() or None
    # Resolved out here rather than inside ``publish``: that callback already
    # runs inside ``allocate_ingest``'s transaction, and a claim written from
    # within it would be a nested write on the same connection.
    project = _resolve_project(
        store, anchor_design_id, document_native_id, document_name
    )

    def publish(ingest_id: str, created_at: str) -> str:
        _publish_staged_bundle(
            bundle_destination, staged_bundle, replace_existing=replacing_bundle
        )
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
                    # The lookup key is derived from the bundle and the prep
                    # options alone, so it is knowable before the tessellation
                    # exists. Recording it is what lets a sealed record point at
                    # an artifact built after it was sealed, without the record
                    # ever being rewritten.
                    "pending": True,
                    "lookup_key": viewport_lookup_key,
                    "reason": "visual tessellation is still being prepared",
                }
                if viewport_deferred
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
            "identity": instance_identity_inventory(
                manifest, selected_instance_id=resolved_instance_id
            ),
            "consistency": {"status": "accepted", "checked_instances": len(manifest["instances"])},
            "normalisation": (
                {
                    **built["normalisation"],
                    "solver_frame": record_solver_frame(manifest, solver_frame_axis),
                }
                if is_unlinked_manifest(manifest)
                else built["normalisation"]
            ),
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
            # The archive names a captured Fusion document by the return state
            # it belongs to, and a run record has to say which document it came
            # from. Both facts are in the manifest; neither survived into the
            # record, so a solved run could not be traced back to its document.
            "document": {
                "name": str((manifest.get("document") or {}).get("name") or ""),
                "native_id": (manifest.get("document") or {}).get("native_id"),
                "return_state_hash": (manifest.get("assembly") or {}).get(
                    "signature_hash"
                ),
                "file": cad_document_member(manifest),
            },
            # Which project this return belongs to. Without it a CAD-authored
            # document has no lineage anywhere downstream, so its runs cannot
            # be grouped with it and its archive folder is derived twice.
            "project": project,
            "sources": manifest["sources"],
            "mesh_sizes": effective_mesh,
            "skipped_source_ids": effective_skipped_source_ids,
            "role_resolution": built["role_resolution"],
            "role_findings": built.get("role_findings", []),
            "symmetry": built["symmetry"],
            "symmetry_verification": built.get("symmetry_verification"),
            # What each source actually kept through the cut, measured rather
            # than assumed. It was computed and dropped before, which left the
            # reduction with no observable evidence at all outside the mesher.
            "post_cut_source_areas": built.get("post_cut_source_areas") or {},
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
            commit_guard=commit_guard,
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


def deferred_viewport_lookup_key(record: Mapping[str, Any]) -> str | None:
    """The lookup key of a record whose display artifact is still coming."""

    viewport = record.get("viewport_mesh")
    if not isinstance(viewport, Mapping) or viewport.get("pending") is not True:
        return None
    key = viewport.get("lookup_key")
    return str(key) if isinstance(key, str) and len(key) == 64 else None


def resolve_deferred_viewport(
    record: Mapping[str, Any], data_dir: str | Path
) -> dict[str, Any] | None:
    """Read a deferred display artifact, or None while it does not exist yet.

    The record cannot vouch for this artifact -- it was sealed before the
    artifact was built -- so the two independent facts that make it trustworthy
    are checked here instead: the file name is the digest of its own bytes, and
    its sidecar names the same normalized geometry the solve mesh was cut from.
    """

    lookup_key = deferred_viewport_lookup_key(record)
    if lookup_key is None:
        return None
    imports_root = data_paths(data_dir).root / "imports"
    cache_key = _read_cas_index(_viewport_index_path(imports_root, lookup_key))
    if cache_key is None:
        return None
    artifact = _load_cached_viewport_mesh(*_viewport_paths(imports_root, cache_key))
    if artifact is None:
        return None
    if artifact.get("content_sha256") != f"sha256:{cache_key}":
        return None
    expected_geometry = str(record.get("transformed_geometry_hash") or "")
    if not expected_geometry or artifact.get("transformed_geometry_hash") != expected_geometry:
        return None
    return {**artifact, "cache_key": cache_key}


def build_deferred_viewport(
    record: Mapping[str, Any], data_dir: str | Path
) -> dict[str, Any] | None:
    """Tessellate the display mesh for an already-published record.

    Everything this needs was written down before the record was sealed: the
    staged bundle holds the STEP and its manifest, and the solve mesh's sidecar
    holds the recipe, the tag allocation and the geometry hash the tessellation
    must reproduce. So this reads the same inputs the in-line path used and
    produces a byte-identical artifact -- it is the same build, moved.
    """

    lookup_key = deferred_viewport_lookup_key(record)
    if lookup_key is None:
        return None
    imports_root = data_paths(data_dir).root / "imports"
    existing = resolve_deferred_viewport(record, data_dir)
    if existing is not None:
        return existing

    bundle_root = Path(str(record.get("bundle_store_path") or ""))
    try:
        manifest = json.loads((bundle_root / "wgreturn.json").read_text(encoding="utf-8"))
        sidecar = json.loads(
            Path(str(record["mesh_store_path"])).with_suffix(".json").read_text(encoding="utf-8")
        )
    except (KeyError, OSError, ValueError) as exc:
        raise IngestRefusal("stage 7 meshing", f"deferred viewport inputs are unreadable: {exc}") from exc
    recipe = sidecar.get("viewport_recipe")
    if not isinstance(recipe, Mapping):
        raise IngestRefusal(
            "stage 7 meshing",
            "the cached solve mesh carries no viewport recipe; re-ingest the CAD return",
        )
    assembly_path = bundle_root / str(manifest["assembly"]["file"])
    assembly_record = manifest["files"][str(manifest["assembly"]["file"])]
    generated = build_imported_viewport_mesh_isolated(
        assembly_path,
        manifest,
        recipe,
        expected_geometry_hash=str(record["transformed_geometry_hash"]),
        tag_allocation=sidecar["tag_allocation"],
        expected_sha256=str(assembly_record["sha256"]),
        expected_size_bytes=int(assembly_record["size_bytes"]),
    )
    artifact, cache_key, _ = _publish_viewport_artifact(
        imports_root,
        generated,
        transformed_geometry_hash=str(record["transformed_geometry_hash"]),
        healing=sidecar.get("healing") or {},
        lookup_key=lookup_key,
    )
    return {**artifact, "cache_key": cache_key}


__all__ = [
    "IngestRefusal",
    "build_deferred_viewport",
    "compute_freshness",
    "deferred_viewport_lookup_key",
    "evaluate_instance_freshness",
    "get_ingestion_record",
    "ingest_bundle",
    "resolve_deferred_viewport",
    "validate_registry_echoes",
    "validate_scope_inventory",
]
