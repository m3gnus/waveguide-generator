"""Strict reader for immutable CAD-return bundles.

The return manifest is evidence authored by CAD.  This module deliberately
does not add server verdicts to that evidence; it only validates the contract
and verifies the complete member inventory before any geometry engine sees it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping
import unicodedata

from server.cadlink import wglink_protocol as protocol
from server.cadlink.limits import MAX_STEP_INPUT_BYTES, MAX_WGRETURN_JSON_BYTES


# Every contract value below is the vendored WG ingress profile's own object,
# not a copy: ``validate_manifest`` enforces the profile, and WG's other readers
# (``solver_frame``, ``domain_interpretation``, ``ingest``, the mesher's
# physical-name bound) must mean the same thing by construction. Change a value
# in the add-in and re-vendor; ``test_wglink_shared_protocol`` fails if one of
# these is ever redefined here.
SUPPORTED_MAJOR = protocol._wg_SUPPORTED_MAJOR
SUPPORTED_VERSION = protocol._wg_SUPPORTED_VERSION
SUPPORTED_FEATURES = protocol._wg_SUPPORTED_FEATURES
# The CAD author's statement that the exported bodies ARE the reduced domain:
# the model was cut before it left CAD, and the missing half is the solver's
# mirror rather than something WG has to remove. Only the two planes the
# imported-symmetry vocabulary can express are declarable, and the retained
# side is the positive one, matching what WG's own cutter keeps.
#
# It is a declaration, not a verdict. Ingestion re-derives the same fact from
# the meshed boundary (``server/mesh/imported.py``) and refuses a declaration
# the mesh denies -- which is why the writer's evidence is checked for internal
# consistency here and believed no further.
# Which component's own frame ``assembly.step`` is written in. Fusion exports a
# Component in its own coordinates and cannot export an occurrence in its
# assembly placement, so the export scope decides the file's frame and the
# writer states it. Absent means the root component, which is what every bundle
# written before the member existed exported.
EXPORT_FRAMES = protocol._wg_EXPORT_FRAMES
# The CAD document's up axis, in the coordinates the STEP is written in.
# Fusion documents are Y-up or Z-up. A writer states it under this feature,
# and only when WG advertises ``documentUp`` (``fusion_delivery.py``); WG then
# takes the horizontal polar plane perpendicular to it
# (``solver_frame.py``, contract v2). Without the feature WG assumes CAD +Z up,
# or +Y when the model radiates along +-Z.
DOCUMENT_UP_FEATURE = protocol._wg_DOCUMENT_UP_FEATURE
DOCUMENT_UP_AXES = protocol._wg_DOCUMENT_UP_AXES
DOMAIN_PLANES = protocol._wg_DOMAIN_PLANES
DOMAIN_KIND_FOR_PLANES = protocol._wg_DOMAIN_KIND_FOR_PLANES
REDUCED_DOMAIN_FEATURE = protocol._wg_REDUCED_DOMAIN_FEATURE
# M1c-auto (PLAN.md, "Automatic domain"). A writer that requires this feature
# writes ``"domain": {"kind": "automatic"}``: it declares nothing about the
# domain and leaves the interpretation to WG. Only under it may the return
# carry ``assembly.cut_provenance``, the cuts the CAD timeline recorded during
# the explicit export. The writer states it only when WG advertises
# ``automaticDomain`` (``fusion_delivery.py``); a WG without it refuses the
# unknown required feature, so a new writer is never misread by an old reader.
#
# The provenance is evidence, not a verdict: WG revalidates every entry
# against the body, frame and meshed geometry before it mirrors anything
# (``server/cadlink/domain_interpretation.py``).
DOMAIN_AUTOMATIC_FEATURE = protocol._wg_DOMAIN_AUTOMATIC_FEATURE
DOMAIN_AUTOMATIC = protocol._wg_DOMAIN_AUTOMATIC
CUT_FEATURE_KINDS = protocol._wg_CUT_FEATURE_KINDS
CUT_TOOL_KINDS = protocol._wg_CUT_TOOL_KINDS
# The origin plane a cut tool is (or is coincident with), and the coordinate
# plane it is in the exported frame.
CUT_ORIGIN_PLANES = protocol._wg_CUT_ORIGIN_PLANES
CUT_KEPT_SIDES = protocol._wg_CUT_KEPT_SIDES
# A return that requires this feature states that every ``sources[].id`` is the
# CAD-authored identity of that logical source, the same across exports. The
# field and its uniqueness are unchanged; the feature adds only a canonical form
# (trimmed, bounded) so the identity survives WG's own whitespace-normalising
# consumers. Resolving the identity to faces -- and refusing a removed, split or
# ambiguous mapping -- is the CAD writer's job before the bundle exists: an
# opaque string cannot show WG which face it should have been, so WG never picks.
# Without the feature a source id keeps its legacy meaning and legacy checks.
SOURCE_IDENTITY_FEATURE = protocol._wg_SOURCE_IDENTITY_FEATURE
# Both bounds come from the mesh. Ingestion writes each source into a gmsh
# physical name,
#   wg-import-v1|tag=<tag>|source_id=<id>|instance_id=<instance>|role=<role>
# (``server/mesh/imported.py``, ``_physical_name``), and gmsh 4.15 writes at
# most 128 UTF-8 bytes of a physical name to MSH 2.2, silently cutting the rest
# (even inside a multi-byte character), after which ingestion refuses the mesh
# for a missing physical name -- after meshing. Under the feature WG therefore
# checks the WHOLE name at validation, with the worst-case tag 9999 (tags start
# at 101, and 9,899 sources would not fit in a 1 MiB manifest: a minimal source
# is 262 bytes), and refuses the source there, before any work.
#
# The identity additionally has a fixed ceiling, so a CAD writer can size its
# identities without knowing the rest of the name. With WGLink's own values the
# rest takes at most 103 bytes:
#   47  fixed text ("wg-import-v1|tag=", "|source_id=", "|instance_id=", "|role=")
#    4  tag digits (9999)
#   36  instance id: WGLink mints a UUID; WG's Onshape ids are 30
#   16  role: PASSIVE_CARDIOID, WGLink's longest
# 128 - 103 = 25 bytes, counted in UTF-8 bytes because that is what gmsh counts.
# A longer role or instance id is refused by the whole-name check, not assumed
# away. No bound applies without the feature.
GMSH_PHYSICAL_NAME_MAX_BYTES = protocol._wg_GMSH_PHYSICAL_NAME_MAX_BYTES
SOURCE_IDENTITY_MAX_BYTES = protocol._wg_SOURCE_IDENTITY_MAX_BYTES
WORST_CASE_SOURCE_TAG = protocol._wg_WORST_CASE_SOURCE_TAG


source_physical_name = protocol.source_physical_name


REQUIRED_BASE_FEATURES = protocol._wg_REQUIRED_BASE_FEATURES
FORBIDDEN_VERDICT_KEYS = protocol._wg_FORBIDDEN_VERDICT_KEYS
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_WINDOWS_DRIVE = protocol._wg__WINDOWS_DRIVE
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
    artifact_size_bytes: int
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
    except RecursionError as exc:
        # Deep nesting fits well inside the size limit and exhausts the parser.
        raise WgReturnValidationError("wgreturn.json: nested too deeply to read") from exc
    if not isinstance(value, dict):
        _fail("$", "must be an object")
    return value


def _read_manifest_bytes(path: Path) -> bytes:
    """Read the manifest under its limit even if it grows after ``stat``."""

    raw = bytearray()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(min(64 * 1024, MAX_WGRETURN_JSON_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_WGRETURN_JSON_BYTES:
                raise WgReturnValidationError(
                    f"wgreturn.json: exceeds the {MAX_WGRETURN_JSON_BYTES:,} byte "
                    "limit for a return manifest"
                )
    return bytes(raw)


def _sha256_file(path: Path, *, maximum_bytes: int | None = None) -> tuple[str, int]:
    """Hash a CAD-authored member with a bounded streaming allocation."""

    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            total += len(chunk)
            if maximum_bytes is not None and total > maximum_bytes:
                raise WgReturnIntegrityError(
                    f"bundle member {path.name!r} grew beyond its "
                    f"{maximum_bytes:,} byte limit while it was being verified"
                )
            digest.update(chunk)
    return "sha256:" + digest.hexdigest(), total


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


def _required(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        _fail(f"{path}.{key}", "is required")
    return obj[key]


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


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate the unchanged WG ingress contract through the shared profile."""
    try:
        protocol.validate_structure(manifest, protocol.WG_INGRESS)
    except protocol.ProtocolValidationError as exc:
        raise WgReturnValidationError(str(exc)) from exc
    return manifest


def read_wgreturn(
    path: str | Path, *, absent_purposes: frozenset[str] = frozenset()
) -> WgReturnBundle:
    """Read and verify a directory-form ``.wgreturn`` bundle.

    ``absent_purposes`` names member purposes that may be missing. WG's own
    retained copy of a return leaves out the captured CAD document, which is
    not geometry (``ingest._stage_bundle_cas``); every member that is present
    is still verified against the manifest.
    """

    bundle_path = Path(path)
    if bundle_path.is_symlink():
        raise WgReturnIntegrityError(f"bundle path is a symlink: {bundle_path}")
    if not bundle_path.is_dir():
        raise WgReturnValidationError(f"CAD-return bundle is not a directory: {bundle_path}")
    manifest_path = bundle_path / "wgreturn.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise WgReturnIntegrityError("wgreturn.json: missing or is a symlink")
    # The manifest is CAD-authored, so its size is checked before it is read
    # rather than after: ``json.loads`` on an arbitrarily large document is an
    # allocation an attacker chooses (``docs/plans/STEP-PARSER-ISOLATION.md``).
    manifest_size = manifest_path.stat().st_size
    if manifest_size > MAX_WGRETURN_JSON_BYTES:
        raise WgReturnValidationError(
            f"wgreturn.json: {manifest_size:,} bytes exceeds the "
            f"{MAX_WGRETURN_JSON_BYTES:,} byte limit for a return manifest"
        )
    raw_manifest = _read_manifest_bytes(manifest_path)
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
    absent = {
        name
        for name in set(declared) - actual
        if declared[name][1].get("purpose") in absent_purposes
    }
    missing = sorted(set(declared) - actual - absent)
    if missing:
        raise WgReturnIntegrityError(f"declared bundle member is missing: {missing[0]}")

    members: dict[str, Path] = {}
    for name, (member_path, record) in declared.items():
        if name in absent:
            continue
        size = member_path.stat().st_size
        expected_size = int(record["size_bytes"])
        if size != expected_size:
            raise WgReturnIntegrityError(
                f"bundle member {name!r} size mismatch: declared {expected_size}, actual {size}"
            )
        is_step = (
            (
                record.get("media_type") == "model/step"
                or record.get("purpose") in {"exterior-assembly", "fem-air-volume"}
            )
        )
        if is_step and size > MAX_STEP_INPUT_BYTES:
            raise WgReturnIntegrityError(
                f"bundle member {name!r} is {size:,} bytes, over the "
                f"{MAX_STEP_INPUT_BYTES:,} byte limit for one STEP input"
            )
        digest, hashed_size = _sha256_file(
            member_path,
            maximum_bytes=MAX_STEP_INPUT_BYTES if is_step else expected_size,
        )
        if hashed_size != size:
            raise WgReturnIntegrityError(
                f"bundle member {name!r} changed size while it was being verified"
            )
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
        artifact_size_bytes=int(table[assembly_name]["size_bytes"]),
        members={name: member.resolve() for name, member in members.items()},
        degradations=(
            (_LEGACY_SIGNATURE_DEGRADATION,)
            if manifest["wgreturn_version"] == "1.0"
            and manifest["assembly"].get("signature_hash") is None
            else ()
        ),
    )


def declared_domain_planes(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """The cut planes a validated manifest declares, in canonical order."""

    assembly = manifest.get("assembly")
    domain = assembly.get("domain") if isinstance(assembly, Mapping) else None
    if not isinstance(domain, Mapping):
        return ()
    names = {str(plane) for plane in (domain.get("cut_planes") or [])}
    return tuple(plane for plane in DOMAIN_PLANES if plane in names)


def domain_kind(manifest: Mapping[str, Any]) -> str:
    """How a validated manifest states its domain: absent, automatic or declared."""

    assembly = manifest.get("assembly")
    domain = assembly.get("domain") if isinstance(assembly, Mapping) else None
    if not isinstance(domain, Mapping):
        return "absent"
    return DOMAIN_AUTOMATIC if domain.get("kind") == DOMAIN_AUTOMATIC else "declared"


def cut_provenance(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The recorded cuts of a validated manifest (empty when it carries none)."""

    assembly = manifest.get("assembly")
    entries = assembly.get("cut_provenance") if isinstance(assembly, Mapping) else None
    return [dict(entry) for entry in entries or [] if isinstance(entry, Mapping)]


__all__ = [
    "CUT_ORIGIN_PLANES",
    "DOCUMENT_UP_AXES",
    "DOMAIN_AUTOMATIC",
    "DOMAIN_AUTOMATIC_FEATURE",
    "DOCUMENT_UP_FEATURE",
    "DOMAIN_PLANES",
    "EXPORT_FRAMES",
    "REDUCED_DOMAIN_FEATURE",
    "SOURCE_IDENTITY_FEATURE",
    "GMSH_PHYSICAL_NAME_MAX_BYTES",
    "SOURCE_IDENTITY_MAX_BYTES",
    "WORST_CASE_SOURCE_TAG",
    "source_physical_name",
    "SUPPORTED_FEATURES",
    "cut_provenance",
    "declared_domain_planes",
    "domain_kind",
    "WgReturnBundle",
    "WgReturnError",
    "WgReturnIntegrityError",
    "WgReturnValidationError",
    "read_wgreturn",
    "validate_manifest",
]
