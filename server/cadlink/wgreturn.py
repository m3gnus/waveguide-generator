"""Strict reader for immutable CAD-return bundles.

The return manifest is evidence authored by CAD.  This module deliberately
does not add server verdicts to that evidence; it only validates the contract
and verifies the complete member inventory before any geometry engine sees it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping
import unicodedata


SUPPORTED_MAJOR = 1
SUPPORTED_VERSION = "1.1"
SUPPORTED_FEATURES = frozenset(
    {
        "checksummed-files-v1",
        "assembly-frame-v1",
        "instance-records-v1",
        "fem-air-volume-v1",
    }
)
REQUIRED_BASE_FEATURES = frozenset(
    {"checksummed-files-v1", "assembly-frame-v1", "instance-records-v1"}
)
FORBIDDEN_VERDICT_KEYS = frozenset(
    {
        "physical_tag",
        "tag",
        "tags",
        "tag_map",
        "tag_namespace",
        "freshness",
        "healing",
        "solver_ready",
        "symmetry",
    }
)
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_RETURN_ID = re.compile(r"^wgr_[0-9A-HJKMNP-TV-Z]{26}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_LEGACY_SIGNATURE_DEGRADATION = (
    "stale detection unavailable: this returned bundle predates wgreturn 1.1 "
    "and carries no document signature"
)


class WgReturnError(ValueError):
    """Base class for actionable CAD-return refusals."""


class WgReturnValidationError(WgReturnError):
    """The manifest structure or a declared value is invalid."""


class WgReturnIntegrityError(WgReturnError):
    """The on-disk bundle contradicts its checksummed member table."""


@dataclass(frozen=True)
class WgReturnBundle:
    """A verified bundle and the hashes used by ingestion provenance."""

    path: Path
    manifest_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    assembly_path: Path
    artifact_sha256: str
    members: dict[str, Path]
    degradations: tuple[str, ...] = ()


def _fail(path: str, message: str) -> None:
    raise WgReturnValidationError(f"{path}: {message}")


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WgReturnValidationError(f"wgreturn.json: duplicate JSON key {key!r}")
        result[key] = value
    return result


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WgReturnValidationError("wgreturn.json: must be UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                WgReturnValidationError(
                    f"wgreturn.json: non-finite JSON number {token!r} is forbidden"
                )
            ),
        )
    except WgReturnValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise WgReturnValidationError(
            f"wgreturn.json: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        _fail("$", "must be an object")
    return value


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            # This is WG-authored config provenance echoed by Fusion. It is
            # opaque to the CAD evidence policy and may legitimately contain
            # keys such as "symmetry" or "tags".
            if key == "config" and path.startswith("$.instances["):
                yield child_path, child
                continue
            if key in FORBIDDEN_VERDICT_KEYS:
                _fail(child_path, "CAD-authored verdict keys are forbidden")
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _validate_finite_tree(manifest: Mapping[str, Any]) -> None:
    for path, value in _walk(manifest):
        if isinstance(value, float) and not math.isfinite(value):
            _fail(path, "must be finite")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _string(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        _fail(path, "must be a non-empty string" + (" or null" if nullable else ""))
    return value


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(path, f"must be an integer >= {minimum}")
    return value


def _number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        _fail(path, "must be finite")
    if positive and number <= 0.0:
        _fail(path, "must be greater than zero")
    return number


def _required(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        _fail(f"{path}.{key}", "is required")
    return obj[key]


def _timestamp(value: Any, path: str) -> str:
    text = _string(value, path)
    assert text is not None
    if not text.endswith("Z"):
        _fail(path, "must be an RFC-3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise WgReturnValidationError(f"{path}: must be an RFC-3339 timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _fail(path, "must be UTC")
    return text


def _vector(value: Any, path: str, length: int) -> list[float]:
    items = _list(value, path)
    if len(items) != length:
        _fail(path, f"must contain exactly {length} numbers")
    return [_number(item, f"{path}[{index}]") for index, item in enumerate(items)]


def _bbox(value: Any, path: str) -> list[list[float]]:
    rows = _list(value, path)
    if len(rows) != 2:
        _fail(path, "must be [[min_x,min_y,min_z],[max_x,max_y,max_z]]")
    low = _vector(rows[0], f"{path}[0]", 3)
    high = _vector(rows[1], f"{path}[1]", 3)
    if any(a > b for a, b in zip(low, high, strict=True)):
        _fail(path, "minimum coordinates must not exceed maximum coordinates")
    return [low, high]


def _matrix(value: Any, path: str) -> list[list[float]]:
    rows = _list(value, path)
    if len(rows) != 4:
        _fail(path, "must be a 4x4 row-major matrix")
    matrix = [_vector(row, f"{path}[{index}]", 4) for index, row in enumerate(rows)]
    if matrix[3] != [0.0, 0.0, 0.0, 1.0]:
        _fail(f"{path}[3]", "last row must equal [0, 0, 0, 1]")
    return matrix


def _portable_member_name(raw: str, path: str) -> tuple[str, tuple[str, ...]]:
    if raw.startswith(("/", "\\")) or _WINDOWS_DRIVE.match(raw):
        _fail(path, "must be a relative bundle member path")
    normalized = raw.replace("\\", "/")
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _fail(path, "must not contain empty, '.', or '..' path segments")
    if str(pure) != normalized:
        _fail(path, "must be a normalized relative path")
    portable = tuple(unicodedata.normalize("NFKC", part).casefold() for part in parts)
    return normalized, portable


def _validate_fingerprint(value: Any, path: str) -> None:
    if value is None:
        return
    obj = _mapping(value, path)
    is_solid = _required(obj, "is_solid", path)
    if not isinstance(is_solid, bool):
        _fail(f"{path}.is_solid", "must be boolean")
    volume_mm3 = _required(obj, "volume_mm3", path)
    if is_solid:
        _number(volume_mm3, f"{path}.volume_mm3")
    elif volume_mm3 is not None:
        _fail(f"{path}.volume_mm3", "must be null for a surface body")
    _vector(_required(obj, "bbox_mm", path), f"{path}.bbox_mm", 6)


def _validate_instance(value: Any, path: str) -> str:
    obj = _mapping(value, path)
    instance_id = _string(_required(obj, "instance_id", path), f"{path}.instance_id")
    assert instance_id is not None
    _string(_required(obj, "design_id", path), f"{path}.design_id")
    _string(obj.get("lineage_id"), f"{path}.lineage_id", nullable=True)
    if obj.get("edit_version") is not None:
        _integer(obj["edit_version"], f"{path}.edit_version", minimum=1)
    if obj.get("design_hash") is not None:
        _string(obj["design_hash"], f"{path}.design_hash")
    if obj.get("formula") is not None:
        _string(obj["formula"], f"{path}.formula")
    if obj.get("config") is not None:
        _mapping(obj["config"], f"{path}.config")
    _string(_required(obj, "export_id", path), f"{path}.export_id")
    _integer(_required(obj, "export_sequence", path), f"{path}.export_sequence", minimum=1)
    for name in ("geometry_hash", "origin_bundle_id", "occurrence_path"):
        _string(obj.get(name), f"{path}.{name}", nullable=True)
    mode = _string(_required(obj, "build_mode", path), f"{path}.build_mode")
    if mode not in {"enclosure", "freestanding"}:
        _fail(f"{path}.build_mode", "must be 'enclosure' or 'freestanding'")
    _string(_required(obj, "parameter_prefix", path), f"{path}.parameter_prefix")
    _matrix(_required(obj, "assembly_from_link", path), f"{path}.assembly_from_link")
    chirality = _string(_required(obj, "chirality", path), f"{path}.chirality")
    if chirality != "original":
        _fail(f"{path}.chirality", "Phase 2 accepts only 'original'")

    evidence = _mapping(_required(obj, "body_evidence", path), f"{path}.body_evidence")
    state = _string(
        _required(evidence, "local_body_state", f"{path}.body_evidence"),
        f"{path}.body_evidence.local_body_state",
    )
    if state not in {"unmodified", "modified", "missing", "unknown"}:
        _fail(
            f"{path}.body_evidence.local_body_state",
            "must be unmodified, modified, missing, or unknown",
        )
    _validate_fingerprint(evidence.get("baseline_fingerprint"), f"{path}.body_evidence.baseline_fingerprint")
    _validate_fingerprint(evidence.get("observed_fingerprint"), f"{path}.body_evidence.observed_fingerprint")
    _timestamp(_required(evidence, "observed_at", f"{path}.body_evidence"), f"{path}.body_evidence.observed_at")

    contract = obj.get("source_contract")
    if contract is not None:
        source = _mapping(contract, f"{path}.source_contract")
        _string(_required(source, "role", f"{path}.source_contract"), f"{path}.source_contract.role")
        _number(_required(source, "throat_z_mm", f"{path}.source_contract"), f"{path}.source_contract.throat_z_mm")
        plane = _mapping(_required(source, "throat_plane_link", f"{path}.source_contract"), f"{path}.source_contract.throat_plane_link")
        _vector(_required(plane, "origin_mm", f"{path}.source_contract.throat_plane_link"), f"{path}.source_contract.throat_plane_link.origin_mm", 3)
        normal = _vector(_required(plane, "normal", f"{path}.source_contract.throat_plane_link"), f"{path}.source_contract.throat_plane_link.normal", 3)
        if math.sqrt(sum(item * item for item in normal)) <= 0.0:
            _fail(f"{path}.source_contract.throat_plane_link.normal", "must be non-zero")
        axis = _mapping(_required(source, "axis_link", f"{path}.source_contract"), f"{path}.source_contract.axis_link")
        _vector(_required(axis, "origin_mm", f"{path}.source_contract.axis_link"), f"{path}.source_contract.axis_link.origin_mm", 3)
        direction = _vector(_required(axis, "direction", f"{path}.source_contract.axis_link"), f"{path}.source_contract.axis_link.direction", 3)
        if math.sqrt(sum(item * item for item in direction)) <= 0.0:
            _fail(f"{path}.source_contract.axis_link.direction", "must be non-zero")
        diameter = _number(_required(source, "throat_diameter_mm", f"{path}.source_contract"), f"{path}.source_contract.throat_diameter_mm", positive=True)
        expected = _number(_required(source, "expected_disc_area_mm2", f"{path}.source_contract"), f"{path}.source_contract.expected_disc_area_mm2", positive=True)
        disc = math.pi * diameter * diameter / 4.0
        if abs(expected - disc) / disc > 0.01:
            _fail(f"{path}.source_contract.expected_disc_area_mm2", "differs from pi*throat_diameter_mm^2/4 by more than 1%")
    return instance_id


def _validate_source(value: Any, path: str, instance_ids: set[str]) -> str:
    obj = _mapping(value, path)
    source_id = _string(_required(obj, "id", path), f"{path}.id")
    assert source_id is not None
    _string(_required(obj, "role", path), f"{path}.role")
    instance_id = _string(obj.get("instance_id"), f"{path}.instance_id", nullable=True)
    if instance_id is not None and instance_id not in instance_ids:
        _fail(f"{path}.instance_id", f"does not name an instances[] record: {instance_id!r}")
    if not isinstance(_required(obj, "required", path), bool):
        _fail(f"{path}.required", "must be boolean")
    _string(_required(obj, "default_drive_channel_id", path), f"{path}.default_drive_channel_id")
    policy = _string(_required(obj, "patch_policy", path), f"{path}.patch_policy")
    if policy not in {"single-connected", "explicit-disconnected"}:
        _fail(f"{path}.patch_policy", "must be 'single-connected' or 'explicit-disconnected'")
    components = _integer(_required(obj, "expected_connected_components", path), f"{path}.expected_connected_components", minimum=1)
    if policy == "single-connected" and components != 1:
        _fail(f"{path}.expected_connected_components", "must be 1 for single-connected")
    selectors = _mapping(_required(obj, "selectors", path), f"{path}.selectors")
    mechanisms = 0
    linked = selectors.get("linked_throat")
    if linked is not None:
        linked_obj = _mapping(linked, f"{path}.selectors.linked_throat")
        linked_id = _string(_required(linked_obj, "instance_id", f"{path}.selectors.linked_throat"), f"{path}.selectors.linked_throat.instance_id")
        if linked_id not in instance_ids:
            _fail(f"{path}.selectors.linked_throat.instance_id", f"does not name an instances[] record: {linked_id!r}")
        if instance_id != linked_id:
            _fail(f"{path}.selectors.linked_throat.instance_id", "must equal source.instance_id")
        mechanisms += 1
    for key in ("appearance_labels", "shell_names"):
        if key in selectors:
            values = _list(selectors[key], f"{path}.selectors.{key}")
            for index, item in enumerate(values):
                _string(item, f"{path}.selectors.{key}[{index}]")
            if values:
                mechanisms += 1
    if "advanced_face_indices" in selectors:
        indices = _list(selectors["advanced_face_indices"], f"{path}.selectors.advanced_face_indices")
        for index, item in enumerate(indices):
            _integer(item, f"{path}.selectors.advanced_face_indices[{index}]", minimum=0)
        if indices:
            mechanisms += 1
    if mechanisms == 0:
        _fail(f"{path}.selectors", "must contain at least one non-empty selector mechanism")
    observed = _mapping(_required(obj, "observed", path), f"{path}.observed")
    face_count = _integer(_required(observed, "face_count", f"{path}.observed"), f"{path}.observed.face_count", minimum=1)
    _number(_required(observed, "total_area_mm2", f"{path}.observed"), f"{path}.observed.total_area_mm2", positive=True)
    per_face = _list(_required(observed, "per_face_area_mm2", f"{path}.observed"), f"{path}.observed.per_face_area_mm2")
    if len(per_face) != face_count:
        _fail(f"{path}.observed.per_face_area_mm2", "length must equal observed.face_count")
    for index, item in enumerate(per_face):
        _number(item, f"{path}.observed.per_face_area_mm2[{index}]", positive=True)
    bodies = _list(_required(observed, "bodies", f"{path}.observed"), f"{path}.observed.bodies")
    for index, item in enumerate(bodies):
        _string(item, f"{path}.observed.bodies[{index}]")
    if obj.get("suggested_resolution_mm") is not None:
        _number(obj["suggested_resolution_mm"], f"{path}.suggested_resolution_mm", positive=True)
    return source_id


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate all schema fields consumed by Phase-2 ingestion."""

    _validate_finite_tree(manifest)
    version = _string(_required(manifest, "wgreturn_version", "$"), "$.wgreturn_version")
    assert version is not None
    match = _VERSION.fullmatch(version)
    if match is None:
        _fail("$.wgreturn_version", "must be exactly major.minor")
    if int(match.group(1)) != SUPPORTED_MAJOR:
        _fail("$.wgreturn_version", f"unsupported major {match.group(1)}; reader supports {SUPPORTED_VERSION}")
    minor_version = int(match.group(2))
    features = _list(_required(manifest, "required_features", "$"), "$.required_features")
    feature_names = []
    for index, item in enumerate(features):
        name = _string(item, f"$.required_features[{index}]")
        assert name is not None
        feature_names.append(name)
    if len(set(feature_names)) != len(feature_names):
        _fail("$.required_features", "feature names must be unique")
    unknown = sorted(set(feature_names) - SUPPORTED_FEATURES)
    if unknown:
        _fail("$.required_features", f"unknown required feature(s): {', '.join(unknown)}")
    missing_features = sorted(REQUIRED_BASE_FEATURES - set(feature_names))
    if missing_features:
        _fail("$.required_features", f"missing required feature(s): {', '.join(missing_features)}")

    returned = _mapping(_required(manifest, "return", "$"), "$.return")
    return_id = _string(_required(returned, "id", "$.return"), "$.return.id")
    if return_id is None or _RETURN_ID.fullmatch(return_id) is None:
        _fail("$.return.id", "must be a wgr_ ULID")
    _timestamp(_required(returned, "created_at", "$.return"), "$.return.created_at")
    generator = _mapping(_required(manifest, "generator", "$"), "$.generator")
    for key in ("adapter", "adapter_version", "cad_app", "cad_version"):
        _string(_required(generator, key, "$.generator"), f"$.generator.{key}")
    document = _mapping(_required(manifest, "document", "$"), "$.document")
    _string(_required(document, "name", "$.document"), "$.document.name")
    _string(document.get("native_id"), "$.document.native_id", nullable=True)
    _string(document.get("request_id"), "$.document.request_id", nullable=True)
    coordinates = _mapping(_required(manifest, "coordinate_system", "$"), "$.coordinate_system")
    fixed = {
        "length_unit": "mm",
        "handedness": "right",
        "matrix_convention": "row-major-local-to-parent",
    }
    for key, expected in fixed.items():
        if _required(coordinates, key, "$.coordinate_system") != expected:
            _fail(f"$.coordinate_system.{key}", f"must equal {expected!r}")

    assembly = _mapping(_required(manifest, "assembly", "$"), "$.assembly")
    _string(_required(assembly, "file", "$.assembly"), "$.assembly.file")
    _integer(_required(assembly, "n_bodies_expected", "$.assembly"), "$.assembly.n_bodies_expected", minimum=1)
    _bbox(_required(assembly, "bbox_mm", "$.assembly"), "$.assembly.bbox_mm")
    # Version 1.1 makes the document signature mandatory so a missing value
    # cannot silently turn an unknown freshness comparison into "unchanged".
    if minor_version >= 1:
        _string(
            _required(assembly, "signature_hash", "$.assembly"),
            "$.assembly.signature_hash",
        )
    elif assembly.get("signature_hash") is not None:
        _string(assembly["signature_hash"], "$.assembly.signature_hash")

    scope = _mapping(_required(manifest, "scope", "$"), "$.scope")
    _string(_required(scope, "selection", "$.scope"), "$.scope.selection")
    included = _list(_required(scope, "included", "$.scope"), "$.scope.included")
    for index, item in enumerate(included):
        entry_path = f"$.scope.included[{index}]"
        entry = _mapping(item, entry_path)
        for key in ("object_id", "name", "body_kind", "external_reference"):
            _string(_required(entry, key, entry_path), f"{entry_path}.{key}")
        if entry["body_kind"] not in {"solid", "surface"}:
            _fail(f"{entry_path}.body_kind", "must be 'solid' or 'surface'")
        if not isinstance(_required(entry, "visible", entry_path), bool):
            _fail(f"{entry_path}.visible", "must be boolean")
        _string(entry.get("wglink_instance_id"), f"{entry_path}.wglink_instance_id", nullable=True)
    skipped = _list(_required(scope, "skipped", "$.scope"), "$.scope.skipped")
    degraded = False
    for index, item in enumerate(skipped):
        skip = _mapping(item, f"$.scope.skipped[{index}]")
        for key in ("object_id", "kind", "reason"):
            _string(_required(skip, key, f"$.scope.skipped[{index}]"), f"$.scope.skipped[{index}].{key}")
        severity = _string(_required(skip, "severity", f"$.scope.skipped[{index}]"), f"$.scope.skipped[{index}].severity")
        if severity not in {"info", "degraded"}:
            _fail(f"$.scope.skipped[{index}].severity", "must be 'info' or 'degraded'")
        degraded = degraded or severity == "degraded"
    status = _string(_required(scope, "status", "$.scope"), "$.scope.status")
    if status not in {"clean", "degraded"}:
        _fail("$.scope.status", "must be 'clean' or 'degraded'")
    if (status == "degraded") != degraded:
        _fail("$.scope.status", "must be 'degraded' iff scope.skipped contains a degraded entry")
    fem = _list(_required(scope, "fem_air_volumes", "$.scope"), "$.scope.fem_air_volumes")
    if fem and "fem-air-volume-v1" not in feature_names:
        _fail("$.required_features", "fem-air-volume-v1 is required when FEM air volumes are present")
    for index, item in enumerate(fem):
        volume = _mapping(item, f"$.scope.fem_air_volumes[{index}]")
        _string(_required(volume, "file", f"$.scope.fem_air_volumes[{index}]"), f"$.scope.fem_air_volumes[{index}].file")
        expected = volume.get(
            "n_bodies_expected",
            volume.get("n_solids_expected", volume.get("expected_solids")),
        )
        if expected != 1:
            _fail(f"$.scope.fem_air_volumes[{index}]", "must declare exactly one expected solid")

    instances = _list(_required(manifest, "instances", "$"), "$.instances")
    instance_ids = [_validate_instance(item, f"$.instances[{index}]") for index, item in enumerate(instances)]
    if len(set(instance_ids)) != len(instance_ids):
        _fail("$.instances", "instance_id values must be unique")
    anchor = coordinates.get("solver_anchor_instance_id")
    if len(instances) == 1:
        if anchor is not None and anchor != instance_ids[0]:
            _fail("$.coordinate_system.solver_anchor_instance_id", "must name the sole instance")
    elif len(instances) > 1:
        if anchor not in set(instance_ids):
            _fail("$.coordinate_system.solver_anchor_instance_id", "is required and must name an instance when multiple instances exist")
    elif anchor is not None:
        _fail("$.coordinate_system.solver_anchor_instance_id", "must be null or absent when instances is empty")

    sources = _list(_required(manifest, "sources", "$"), "$.sources")
    if not sources:
        _fail("$.sources", "must contain at least one source")
    source_ids = [_validate_source(item, f"$.sources[{index}]", set(instance_ids)) for index, item in enumerate(sources)]
    if len(set(source_ids)) != len(source_ids):
        _fail("$.sources", "source ids must be unique")
    if _required(manifest, "acoustics", "$") is not None:
        _fail("$.acoustics", "must be null in Phase 2")
    return manifest


def read_wgreturn(path: str | Path) -> WgReturnBundle:
    """Read and verify a directory-form ``.wgreturn`` bundle."""

    bundle_path = Path(path)
    if bundle_path.is_symlink():
        raise WgReturnIntegrityError(f"bundle path is a symlink: {bundle_path}")
    if not bundle_path.is_dir():
        raise WgReturnValidationError(f"CAD-return bundle is not a directory: {bundle_path}")
    manifest_path = bundle_path / "wgreturn.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise WgReturnIntegrityError("wgreturn.json: missing or is a symlink")
    raw_manifest = manifest_path.read_bytes()
    manifest = validate_manifest(_parse_manifest(raw_manifest))

    table = _mapping(_required(manifest, "files", "$"), "$.files")
    declared: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    portable_names: dict[tuple[str, ...], str] = {}
    for raw_name, raw_record in table.items():
        if not isinstance(raw_name, str) or not raw_name:
            _fail("$.files", "member names must be non-empty strings")
        name, portable = _portable_member_name(raw_name, f"$.files[{raw_name!r}]")
        previous = portable_names.get(portable)
        if previous is not None:
            _fail("$.files", f"duplicate normalized member names {previous!r} and {raw_name!r}")
        portable_names[portable] = raw_name
        record = _mapping(raw_record, f"$.files[{raw_name!r}]")
        checksum = _string(_required(record, "sha256", f"$.files[{raw_name!r}]"), f"$.files[{raw_name!r}].sha256")
        if checksum is None or _SHA256.fullmatch(checksum) is None:
            _fail(f"$.files[{raw_name!r}].sha256", "must be sha256: plus 64 lowercase hex digits")
        _integer(_required(record, "size_bytes", f"$.files[{raw_name!r}]"), f"$.files[{raw_name!r}].size_bytes", minimum=0)
        _string(_required(record, "media_type", f"$.files[{raw_name!r}]"), f"$.files[{raw_name!r}].media_type")
        _string(_required(record, "purpose", f"$.files[{raw_name!r}]"), f"$.files[{raw_name!r}].purpose")
        declared[name] = (bundle_path.joinpath(*PurePosixPath(name).parts), record)

    actual: set[str] = set()
    for candidate in bundle_path.rglob("*"):
        if candidate.is_symlink():
            raise WgReturnIntegrityError(f"bundle member is a symlink: {candidate.relative_to(bundle_path).as_posix()}")
        if candidate.is_file():
            relative = candidate.relative_to(bundle_path).as_posix()
            if relative != "wgreturn.json":
                actual.add(relative)
    undeclared = sorted(actual - set(declared))
    if undeclared:
        raise WgReturnIntegrityError(f"undeclared bundle member: {undeclared[0]}")
    missing = sorted(set(declared) - actual)
    if missing:
        raise WgReturnIntegrityError(f"declared bundle member is missing: {missing[0]}")

    members: dict[str, Path] = {}
    for name, (member_path, record) in declared.items():
        size = member_path.stat().st_size
        expected_size = int(record["size_bytes"])
        if size != expected_size:
            raise WgReturnIntegrityError(
                f"bundle member {name!r} size mismatch: declared {expected_size}, actual {size}"
            )
        digest = "sha256:" + hashlib.sha256(member_path.read_bytes()).hexdigest()
        if digest != record["sha256"]:
            raise WgReturnIntegrityError(
                f"bundle member {name!r} checksum mismatch: declared {record['sha256']}, actual {digest}"
            )
        members[name] = member_path

    assembly_name = str(manifest["assembly"]["file"])
    if assembly_name not in members:
        _fail("$.assembly.file", f"does not name a declared bundle member: {assembly_name!r}")
    if table[assembly_name].get("purpose") != "exterior-assembly":
        _fail(f"$.files[{assembly_name!r}].purpose", "must be 'exterior-assembly'")
    for index, volume in enumerate(manifest["scope"]["fem_air_volumes"]):
        name = str(volume["file"])
        if name not in members:
            _fail(f"$.scope.fem_air_volumes[{index}].file", f"does not name a declared member: {name!r}")
        if table[name].get("purpose") != "fem-air-volume":
            _fail(f"$.files[{name!r}].purpose", "must be 'fem-air-volume'")
    fem_members = {
        str(name)
        for name, record in table.items()
        if isinstance(record, Mapping) and record.get("purpose") == "fem-air-volume"
    }
    referenced_fem_members = {
        str(volume["file"]) for volume in manifest["scope"]["fem_air_volumes"]
    }
    if fem_members != referenced_fem_members:
        extras = sorted(fem_members - referenced_fem_members)
        missing = sorted(referenced_fem_members - fem_members)
        detail = []
        if extras:
            detail.append(f"unreferenced FEM members {extras}")
        if missing:
            detail.append(f"FEM records without matching purpose {missing}")
        _fail("$.files", "; ".join(detail))

    return WgReturnBundle(
        path=bundle_path.resolve(),
        manifest_path=manifest_path.resolve(),
        manifest=manifest,
        manifest_sha256="sha256:" + hashlib.sha256(raw_manifest).hexdigest(),
        assembly_path=members[assembly_name].resolve(),
        artifact_sha256=str(table[assembly_name]["sha256"]),
        members={name: member.resolve() for name, member in members.items()},
        degradations=(
            (_LEGACY_SIGNATURE_DEGRADATION,)
            if manifest["wgreturn_version"] == "1.0"
            and manifest["assembly"].get("signature_hash") is None
            else ()
        ),
    )


__all__ = [
    "SUPPORTED_FEATURES",
    "WgReturnBundle",
    "WgReturnError",
    "WgReturnIntegrityError",
    "WgReturnValidationError",
    "read_wgreturn",
    "validate_manifest",
]
