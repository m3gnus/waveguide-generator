"""Shared, Fusion-free CAD Link protocol definitions.

The two validation profiles preserve their endpoint contracts and first-error
order. WG_INGRESS mirrors the bare reader's structural validator; its disk
inventory and member-integrity boundary stays in WG's read_wgreturn. The
ADDIN_WRITER profile mirrors the writer preflight. Neither profile derives a
reduced domain from geometry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any, Iterable
import unicodedata

WG_INGRESS = "WG_INGRESS"
ADDIN_WRITER = "ADDIN_WRITER"
UTF8_STRICT = "utf8_strict"
ASCII_STRICT = "ascii_strict"
ASCII_NORMALIZED = "ascii_normalized"
ASCII_NAN_PERMITTED = "ascii_nan_permitted"

# Delivery requests and Solve/Send inbox files have distinct versions.
FUSION_REQUEST_DELIVERY_VERSION = 3
SOLVE_COMMAND_SCHEMA_VERSION = 3
WG_REQUEST_SCHEMA_VERSION = 4

SOURCE_ROLES = ("LF", "MF", "HF", "PASSIVE_CARDIOID")
LEGACY_SOURCE_ROLE_ALIASES = {"PORT_EXIT": "PASSIVE_CARDIOID"}
RECOGNISED_SOURCE_ROLES = SOURCE_ROLES + tuple(LEGACY_SOURCE_ROLE_ALIASES)
EXTERIOR_ROLES = frozenset({"waveguide", "enclosure"})

# With source-identity-v1, each sources[].id is a CAD-authored identity that WG
# bounds: trimmed, at most 25 UTF-8 bytes, and a complete gmsh physical name
# (including the largest physical tag) at most 128 bytes.
# A domain declaration says the exported bodies are already the reduced domain:
# CAD made the cut and the solver supplies the missing half by mirroring. Only
# planes supported by that mirror are declarable, and the retained side is
# positive to match hornlab_mesher.step_prepare, which keeps x >= 0 and y >= 0.
# A reader that does not know this vocabulary must refuse the bundle rather
# than solve a half as an open full-domain shell.
# The export frame names the component coordinates used for assembly.step;
# every coordinate in the manifest is expressed in that frame.


class ProtocolValidationError(ValueError):
    """An endpoint-specific structural refusal; str() is the endpoint message."""


def source_physical_name(tag: int, source_id: str, instance_id: object, role: str) -> str:
    instance = "null" if instance_id is None else str(instance_id)
    return (f"wg-import-v1|tag={tag}|source_id={source_id}|"
            f"instance_id={instance}|role={role}")


def _normalize_json_value(value: Any) -> Any:
    """Match WG's geometry identity normalization for ascii_normalized."""
    if is_dataclass(value):
        return _normalize_json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _normalize_json_value(value.tolist())
    return value


def canonical_json(value: Any, profile: str) -> str:
    """Serialize using the exact named bytes profile of each existing site."""
    if profile == UTF8_STRICT:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    if profile == ASCII_STRICT:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    if profile == ASCII_NORMALIZED:
        return json.dumps(_normalize_json_value(value), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    if profile == ASCII_NAN_PERMITTED:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=True)
    raise ValueError(f"unknown canonical JSON profile: {profile!r}")


# Add-in writer structural profile.
_addin_SUPPORTED_RETURN_FEATURES = frozenset({'checksummed-files-v1', 'assembly-frame-v1', 'instance-records-v1', 'fem-air-volume-v1', 'reduced-domain-v1', 'source-identity-v1', 'document-up-v1', 'domain-automatic-v1'})
_addin_SOURCE_IDENTITY_FEATURE = 'source-identity-v1'
_addin_DOCUMENT_UP_FEATURE = 'document-up-v1'
_addin_DOCUMENT_UP_AXES = ('+y', '+z')
_addin_DOMAIN_AUTOMATIC_FEATURE = 'domain-automatic-v1'
_addin_DOMAIN_AUTOMATIC = 'automatic'
_addin_SOURCE_IDENTITY_MAX_BYTES = 25
_addin_GMSH_PHYSICAL_NAME_MAX_BYTES = 128
_addin_BASE_RETURN_FEATURES = ('checksummed-files-v1', 'assembly-frame-v1', 'instance-records-v1')
_addin_EXPORT_FRAMES = ('root-component', 'selected-occurrence-component')
_addin_DOMAIN_PLANES = ('x0', 'y0')
_addin_DOMAIN_KIND_FOR_PLANES = {(): 'full', ('x0',): 'half', ('y0',): 'half', ('x0', 'y0'): 'quarter'}
_addin_DOMAIN_KINDS = ('full', 'half', 'quarter')
_addin_REDUCED_DOMAIN_FEATURE = 'reduced-domain-v1'
_addin_CUT_FEATURE_KINDS = ('split-body', 'extrude-cut', 'other')
_addin_CUT_TOOL_KINDS = ('origin-plane', 'construction-plane')
_addin_CUT_ORIGIN_PLANES = {'YZ': 'x0', 'XZ': 'y0', 'XY': 'z0'}
_addin_CUT_KEPT_SIDES = ('positive', 'negative')
_addin__ULID = re.compile('^wgr_[0-9A-HJKMNP-TV-Z]{26}$')
_addin__VERSION = re.compile('^(\\d+)\\.(\\d+)$')
_addin__SHA256 = re.compile('^sha256:[0-9A-Fa-f]+$')
_addin__FORBIDDEN_EVIDENCE_KEYS = frozenset({'freshness', 'freshness_verdict', 'healing', 'healing_record', 'physical_tag', 'physical_tags', 'solver_readiness', 'solver_ready', 'symmetry', 'tag', 'tag_map', 'tag_namespace', 'tags'})

def _addin_canonical_domain_planes(planes: Sequence[Any]) -> tuple[str, ...]:
    """Order and check declared planes so one domain has one spelling."""
    names = [str(plane) for plane in planes]
    unknown = [name for name in names if name not in _addin_DOMAIN_PLANES]
    if unknown:
        raise ProtocolValidationError('assembly.domain.cut_planes may only name ' + ', '.join(_addin_DOMAIN_PLANES) + '; got ' + ', '.join((repr(name) for name in unknown)))
    if len(set(names)) != len(names):
        raise ProtocolValidationError('assembly.domain.cut_planes must not repeat a plane')
    return tuple((plane for plane in _addin_DOMAIN_PLANES if plane in set(names)))

def _addin__mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolValidationError(f'{label} must be an object')
    return value

def _addin__list(value: object, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolValidationError(f'{label} must be a list')
    return value

def _addin__required(record: Mapping[str, Any], keys: Sequence[str], *, label: str) -> None:
    missing = [key for key in keys if key not in record]
    if missing:
        fields = ', '.join(missing)
        raise ProtocolValidationError(f'{label} is missing required field(s): {fields}')

def _addin__string(value: object, *, label: str, nullable: bool=False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        suffix = ' or null' if nullable else ''
        raise ProtocolValidationError(f'{label} must be a non-empty string{suffix}')
    return value

def _addin__integer(value: object, *, label: str, minimum: int=0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolValidationError(f'{label} must be an integer >= {minimum}')
    return value

def _addin__number(value: object, *, label: str, minimum: float | None=None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolValidationError(f'{label} must be a finite number')
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ProtocolValidationError(f'{label} must be a finite number') from exc
    if not math.isfinite(number):
        raise ProtocolValidationError(f'{label} must be a finite number')
    if minimum is not None and number < minimum:
        raise ProtocolValidationError(f'{label} must be >= {minimum}')
    return number

def _addin__point(value: object, *, label: str) -> list[Any]:
    point = _addin__list(value, label=label)
    if len(point) != 3:
        raise ProtocolValidationError(f'{label} must contain three coordinates')
    for axis, coordinate in enumerate(point):
        _addin__number(coordinate, label=f'{label}[{axis}]')
    return point

def _addin__matrix(value: object, *, label: str) -> None:
    rows = _addin__list(value, label=label)
    if len(rows) != 4:
        raise ProtocolValidationError(f'{label} must be a finite 4x4 matrix in millimetres')
    converted: list[list[float]] = []
    for row_index, raw_row in enumerate(rows):
        row = _addin__list(raw_row, label=f'{label}[{row_index}]')
        if len(row) != 4:
            raise ProtocolValidationError(f'{label}[{row_index}] must contain four values')
        converted.append([_addin__number(value, label=f'{label}[{row_index}][{column}]') for column, value in enumerate(row)])
    if converted[3] != [0.0, 0.0, 0.0, 1.0]:
        raise ProtocolValidationError(f'{label} last row must be [0, 0, 0, 1]')

def _addin__timestamp(value: object, *, label: str) -> None:
    text = _addin__string(value, label=label)
    if not text.endswith('Z'):
        raise ProtocolValidationError(f'{label} must be an RFC-3339 UTC timestamp ending in Z')
    try:
        datetime.fromisoformat(text[:-1] + '+00:00')
    except ValueError as exc:
        raise ProtocolValidationError(f'{label} must be an RFC-3339 UTC timestamp') from exc

def _addin__member_name(value: object, *, label: str) -> str:
    name = _addin__string(value, label=label)
    if '\\' in name:
        raise ProtocolValidationError(f'{label} must use forward slashes')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or name != path.as_posix():
        raise ProtocolValidationError(f'{label} must be a normalized relative path')
    return name

def _addin__check_forbidden_fields(value: object, *, path: str='wgreturn') -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == 'config' and path.startswith('wgreturn.instances['):
                continue
            if isinstance(key, str) and key.lower() in _addin__FORBIDDEN_EVIDENCE_KEYS:
                raise ProtocolValidationError(f'{path}.{key} is WG-authored verdict data and must not appear in CAD evidence')
            _addin__check_forbidden_fields(child, path=f'{path}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _addin__check_forbidden_fields(child, path=f'{path}[{index}]')

def _addin__validate_fingerprint(value: object, *, label: str) -> None:
    record = _addin__mapping(value, label=label)
    _addin__required(record, ('is_solid', 'volume_mm3', 'bbox_mm'), label=label)
    if not isinstance(record['is_solid'], bool):
        raise ProtocolValidationError(f'{label}.is_solid must be boolean')
    if record['is_solid']:
        _addin__number(record['volume_mm3'], label=f'{label}.volume_mm3', minimum=0.0)
    elif record['volume_mm3'] is not None:
        raise ProtocolValidationError(f'{label}.volume_mm3 must be null for a surface body')
    bbox = _addin__list(record['bbox_mm'], label=f'{label}.bbox_mm')
    if len(bbox) != 6:
        raise ProtocolValidationError(f'{label}.bbox_mm must contain six values')
    for index, coordinate in enumerate(bbox):
        _addin__number(coordinate, label=f'{label}.bbox_mm[{index}]')

def _addin__validate_instance(value: object, *, index: int) -> str:
    label = f'instances[{index}]'
    record = _addin__mapping(value, label=label)
    _addin__required(record, ('instance_id', 'design_id', 'export_id', 'export_sequence', 'build_mode', 'parameter_prefix', 'assembly_from_link', 'chirality', 'body_evidence'), label=label)
    instance_id = _addin__string(record['instance_id'], label=f'{label}.instance_id')
    _addin__string(record['design_id'], label=f'{label}.design_id')
    _addin__string(record['export_id'], label=f'{label}.export_id')
    _addin__integer(record['export_sequence'], label=f'{label}.export_sequence', minimum=1)
    if record['build_mode'] not in {'enclosure', 'freestanding'}:
        raise ProtocolValidationError(f"{label}.build_mode must be 'enclosure' or 'freestanding'")
    _addin__string(record['parameter_prefix'], label=f'{label}.parameter_prefix')
    _addin__matrix(record['assembly_from_link'], label=f'{label}.assembly_from_link')
    if record['chirality'] != 'original':
        chirality = record['chirality']
        raise ProtocolValidationError(f"{label}.chirality {chirality!r} is unsupported; mirrored links have no producer and must be recreated without mirroring")
    optional_strings = ('lineage_id', 'design_hash', 'geometry_hash', 'origin_bundle_id', 'occurrence_path')
    for key in optional_strings:
        if key in record:
            _addin__string(record[key], label=f'{label}.{key}', nullable=True)
    if 'formula' in record:
        _addin__string(record['formula'], label=f'{label}.formula', nullable=True)
    # instances[].config is WG-authored provenance echoed by CAD, not a CAD
    # verdict. Its schema may use names forbidden in the surrounding evidence.
    if 'config' in record and record['config'] is not None:
        _addin__mapping(record['config'], label=f'{label}.config')
    if 'edit_version' in record and record['edit_version'] is not None:
        _addin__integer(record['edit_version'], label=f'{label}.edit_version', minimum=1)
    evidence = _addin__mapping(record['body_evidence'], label=f'{label}.body_evidence')
    _addin__required(evidence, ('local_body_state', 'observed_at'), label=f'{label}.body_evidence')
    if evidence['local_body_state'] not in {'unmodified', 'modified', 'missing', 'unknown'}:
        raise ProtocolValidationError(f'{label}.body_evidence.local_body_state is invalid')
    _addin__timestamp(evidence['observed_at'], label=f'{label}.body_evidence.observed_at')
    for key in ('baseline_fingerprint', 'observed_fingerprint'):
        if key in evidence and evidence[key] is not None:
            _addin__validate_fingerprint(evidence[key], label=f'{label}.body_evidence.{key}')
    if 'source_contract' in record and record['source_contract'] is not None:
        contract = _addin__mapping(record['source_contract'], label=f'{label}.source_contract')
        _addin__required(contract, ('role', 'throat_z_mm', 'throat_plane_link', 'axis_link', 'throat_diameter_mm', 'expected_disc_area_mm2'), label=f'{label}.source_contract')
        _addin__string(contract['role'], label=f'{label}.source_contract.role')
        _addin__number(contract['throat_z_mm'], label=f'{label}.source_contract.throat_z_mm')
        plane = _addin__mapping(contract['throat_plane_link'], label=f'{label}.source_contract.throat_plane_link')
        _addin__required(plane, ('origin_mm', 'normal'), label=f'{label}.source_contract.throat_plane_link')
        _addin__point(plane['origin_mm'], label=f'{label}.source_contract.throat_plane_link.origin_mm')
        _addin__point(plane['normal'], label=f'{label}.source_contract.throat_plane_link.normal')
        axis = _addin__mapping(contract['axis_link'], label=f'{label}.source_contract.axis_link')
        _addin__required(axis, ('origin_mm', 'direction'), label=f'{label}.source_contract.axis_link')
        _addin__point(axis['origin_mm'], label=f'{label}.source_contract.axis_link.origin_mm')
        _addin__point(axis['direction'], label=f'{label}.source_contract.axis_link.direction')
        _addin__number(contract['throat_diameter_mm'], label=f'{label}.source_contract.throat_diameter_mm', minimum=0.0)
        _addin__number(contract['expected_disc_area_mm2'], label=f'{label}.source_contract.expected_disc_area_mm2', minimum=0.0)
    return instance_id

def _addin__validate_source(value: object, *, index: int, instance_ids: set[str]) -> tuple[str, str]:
    label = f'sources[{index}]'
    record = _addin__mapping(value, label=label)
    _addin__required(record, ('id', 'role', 'required', 'default_drive_channel_id', 'patch_policy', 'expected_connected_components', 'selectors', 'observed'), label=label)
    source_id = _addin__string(record['id'], label=f'{label}.id')
    _addin__string(record['role'], label=f'{label}.role')
    if 'instance_id' in record and record['instance_id'] is not None:
        linked_instance = _addin__string(record['instance_id'], label=f'{label}.instance_id')
        if linked_instance not in instance_ids:
            raise ProtocolValidationError(f'{label}.instance_id {linked_instance!r} does not name an instance')
    if not isinstance(record['required'], bool):
        raise ProtocolValidationError(f'{label}.required must be boolean')
    channel_id = _addin__string(record['default_drive_channel_id'], label=f'{label}.default_drive_channel_id')
    if record['patch_policy'] not in {'single-connected', 'explicit-disconnected'}:
        raise ProtocolValidationError(f'{label}.patch_policy is invalid')
    _addin__integer(record['expected_connected_components'], label=f'{label}.expected_connected_components', minimum=1)
    selectors = _addin__mapping(record['selectors'], label=f'{label}.selectors')
    mechanisms = 0
    linked = selectors.get('linked_throat')
    if linked is not None:
        linked_record = _addin__mapping(linked, label=f'{label}.selectors.linked_throat')
        _addin__required(linked_record, ('instance_id',), label=f'{label}.selectors.linked_throat')
        linked_id = _addin__string(linked_record['instance_id'], label=f'{label}.selectors.linked_throat.instance_id')
        if linked_id not in instance_ids:
            raise ProtocolValidationError(f'{label}.selectors.linked_throat.instance_id {linked_id!r} does not name an instance')
        mechanisms += 1
    for key in ('appearance_labels', 'shell_names'):
        if key in selectors:
            values = _addin__list(selectors[key], label=f'{label}.selectors.{key}')
            if not all((isinstance(item, str) and item for item in values)):
                raise ProtocolValidationError(f'{label}.selectors.{key} must contain strings')
            mechanisms += bool(values)
    if 'advanced_face_indices' in selectors:
        indices = _addin__list(selectors['advanced_face_indices'], label=f'{label}.selectors.advanced_face_indices')
        for face_index, value in enumerate(indices):
            _addin__integer(value, label=f'{label}.selectors.advanced_face_indices[{face_index}]')
        mechanisms += bool(indices)
    if not mechanisms:
        raise ProtocolValidationError(f'{label}.selectors must contain at least one mechanism')
    observed = _addin__mapping(record['observed'], label=f'{label}.observed')
    _addin__required(observed, ('face_count', 'total_area_mm2', 'per_face_area_mm2', 'bodies'), label=f'{label}.observed')
    face_count = _addin__integer(observed['face_count'], label=f'{label}.observed.face_count', minimum=1)
    _addin__number(observed['total_area_mm2'], label=f'{label}.observed.total_area_mm2', minimum=0.0)
    areas = _addin__list(observed['per_face_area_mm2'], label=f'{label}.observed.per_face_area_mm2')
    if len(areas) != face_count:
        raise ProtocolValidationError(f'{label}.observed.per_face_area_mm2 must contain face_count entries')
    for area_index, area in enumerate(areas):
        _addin__number(area, label=f'{label}.observed.per_face_area_mm2[{area_index}]', minimum=0.0)
    bodies = _addin__list(observed['bodies'], label=f'{label}.observed.bodies')
    if not bodies or not all((isinstance(body, str) and body for body in bodies)):
        raise ProtocolValidationError(f'{label}.observed.bodies must contain body names')
    if 'suggested_resolution_mm' in record and record['suggested_resolution_mm'] is not None:
        _addin__number(record['suggested_resolution_mm'], label=f'{label}.suggested_resolution_mm', minimum=0.0)
    return (source_id, channel_id)

def _addin_validate_domain_record(value: object, *, automatic_feature: bool=False) -> tuple[str, ...]:
    """Check ``assembly.domain`` and return the planes it declares.

    A missing member is the full domain, so every bundle written before this
    member existed keeps validating unchanged. ``kind`` is redundant with
    ``cut_planes`` on purpose: it is the human-readable half of the declaration,
    and a disagreement between the two is a broken writer rather than something
    to resolve by preferring one.

    ``evidence`` carries the measurement the declaration was accepted on, per
    plane, in millimetres: the extent of the exported bodies along that axis.
    It is evidence, not a verdict -- WG re-derives the domain from the meshed
    boundary and is free to refuse this declaration.
    """
    if value is None:
        if automatic_feature:
            raise ProtocolValidationError(f"{_addin_DOMAIN_AUTOMATIC_FEATURE} is required exactly when assembly.domain.kind is 'automatic'")
        return ()
    domain = _addin__mapping(value, label='assembly.domain')
    kind = _addin__string(domain.get('kind'), label='assembly.domain.kind')
    if (kind == _addin_DOMAIN_AUTOMATIC) != automatic_feature:
        raise ProtocolValidationError(f"{_addin_DOMAIN_AUTOMATIC_FEATURE} is required exactly when assembly.domain.kind is 'automatic'")
    if kind == _addin_DOMAIN_AUTOMATIC:
        extra = sorted(set(domain) - {'kind'})
        if extra:
            raise ProtocolValidationError('assembly.domain automatic states nothing else; got ' + ', '.join(extra))
        return ()
    _addin__required(domain, ('kind', 'cut_planes', 'declared_by'), label='assembly.domain')
    if kind not in _addin_DOMAIN_KINDS:
        raise ProtocolValidationError('assembly.domain.kind must be one of ' + ', '.join(_addin_DOMAIN_KINDS))
    planes = _addin_canonical_domain_planes(_addin__list(domain['cut_planes'], label='assembly.domain.cut_planes'))
    if _addin_DOMAIN_KIND_FOR_PLANES[planes] != kind:
        raise ProtocolValidationError(f'assembly.domain.kind {kind!r} does not match cut_planes {list(planes)!r}')
    declared_by = _addin__string(domain['declared_by'], label='assembly.domain.declared_by')
    if declared_by != 'cad-author':
        raise ProtocolValidationError("assembly.domain.declared_by must be 'cad-author'; a domain nobody declared is not a domain")
    evidence = _addin__mapping(domain.get('evidence', {}), label='assembly.domain.evidence')
    if set(evidence) != set(planes):
        raise ProtocolValidationError('assembly.domain.evidence must measure exactly the declared planes')
    for plane in planes:
        record = _addin__mapping(evidence[plane], label=f'assembly.domain.evidence[{plane!r}]')
        _addin__required(record, ('min_mm', 'max_mm', 'tolerance_mm'), label=f'assembly.domain.evidence[{plane!r}]')
        label = f'assembly.domain.evidence[{plane!r}]'
        minimum = _addin__number(record['min_mm'], label=f'{label}.min_mm')
        maximum = _addin__number(record['max_mm'], label=f'{label}.max_mm')
        tolerance = _addin__number(record['tolerance_mm'], label=f'{label}.tolerance_mm', minimum=0.0)
        if minimum > maximum:
            raise ProtocolValidationError(f'{label}.min_mm exceeds max_mm')
        if minimum < -tolerance:
            raise ProtocolValidationError(f'{label} shows geometry {abs(minimum):.6g} mm on the negative side of {plane}, beyond the {tolerance:.6g} mm tolerance')
        if maximum <= tolerance:
            raise ProtocolValidationError(f'{label} shows no geometry on the positive side of {plane}')
    return planes

def _addin__validate_cut_provenance(value: object, included_ids: set[str]) -> None:
    entries = _addin__list(value, label='assembly.cut_provenance')
    for index, raw in enumerate(entries):
        label = f'assembly.cut_provenance[{index}]'
        entry = _addin__mapping(raw, label=label)
        allowed = {'body_object_id', 'feature', 'tool', 'plane', 'kept_side', 'export_frame'}
        extra = sorted(set(entry) - allowed)
        if extra:
            members = ', '.join(extra)
            raise ProtocolValidationError(f'{label} has unknown members: {members}')
        _addin__required(entry, tuple(allowed), label=label)
        body_id = _addin__string(entry['body_object_id'], label=f'{label}.body_object_id')
        if body_id not in included_ids:
            raise ProtocolValidationError(f'{label}.body_object_id must name a scope.included body')
        feature = _addin__mapping(entry['feature'], label=f'{label}.feature')
        _addin__required(feature, ('kind', 'name'), label=f'{label}.feature')
        if feature['kind'] not in _addin_CUT_FEATURE_KINDS:
            raise ProtocolValidationError(f'{label}.feature.kind is invalid')
        name = _addin__string(feature['name'], label=f'{label}.feature.name')
        if name is None or not name.strip() or len(name) > 200:
            raise ProtocolValidationError(f'{label}.feature.name must be at most 200 characters')
        tool = _addin__mapping(entry['tool'], label=f'{label}.tool')
        _addin__required(tool, ('kind', 'origin_plane'), label=f'{label}.tool')
        if tool['kind'] not in _addin_CUT_TOOL_KINDS:
            raise ProtocolValidationError(f'{label}.tool.kind is invalid')
        origin = _addin__string(tool['origin_plane'], label=f'{label}.tool.origin_plane')
        if origin not in _addin_CUT_ORIGIN_PLANES:
            raise ProtocolValidationError(f'{label}.tool.origin_plane is invalid')
        if entry['plane'] != _addin_CUT_ORIGIN_PLANES[origin]:
            raise ProtocolValidationError(f'{label}.plane must be {_addin_CUT_ORIGIN_PLANES[origin]} for {origin}')
        if entry['kept_side'] not in _addin_CUT_KEPT_SIDES:
            raise ProtocolValidationError(f'{label}.kept_side is invalid')
        if entry['export_frame'] not in _addin_EXPORT_FRAMES:
            raise ProtocolValidationError(f'{label}.export_frame is invalid')

def _addin_validate_return_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate the §2 schema and evidence/verdict ownership boundary."""
    root = _addin__mapping(manifest, label='wgreturn')
    _addin__check_forbidden_fields(root)
    _addin__required(root, ('wgreturn_version', 'required_features', 'return', 'generator', 'document', 'coordinate_system', 'assembly', 'files', 'scope', 'instances', 'sources'), label='wgreturn')
    version = root['wgreturn_version']
    match = _addin__VERSION.fullmatch(version) if isinstance(version, str) else None
    if match is None or int(match.group(1)) != 1:
        raise ProtocolValidationError(f'unsupported wgreturn_version {version!r}; reader supports major 1')
    features = _addin__list(root['required_features'], label='required_features')
    if not all((isinstance(feature, str) and feature for feature in features)):
        raise ProtocolValidationError('required_features must contain feature names')
    if len(features) != len(set(features)):
        raise ProtocolValidationError('required_features must contain unique feature names')
    missing_features = sorted(set(_addin_BASE_RETURN_FEATURES).difference(features))
    if missing_features:
        raise ProtocolValidationError('required_features is missing launch feature(s): ' + ', '.join(missing_features))
    unknown = sorted(set(features).difference(_addin_SUPPORTED_RETURN_FEATURES))
    if unknown:
        raise ProtocolValidationError('unsupported required_features: ' + ', '.join((repr(item) for item in unknown)))
    return_record = _addin__mapping(root['return'], label='return')
    _addin__required(return_record, ('id', 'created_at'), label='return')
    return_id = _addin__string(return_record['id'], label='return.id')
    if not _addin__ULID.fullmatch(return_id):
        raise ProtocolValidationError('return.id must be a wgr_-prefixed ULID')
    _addin__timestamp(return_record['created_at'], label='return.created_at')
    generator = _addin__mapping(root['generator'], label='generator')
    _addin__required(generator, ('adapter', 'adapter_version', 'cad_app', 'cad_version'), label='generator')
    for key in ('adapter', 'adapter_version', 'cad_app', 'cad_version'):
        _addin__string(generator[key], label=f'generator.{key}')
    document = _addin__mapping(root['document'], label='document')
    _addin__required(document, ('name',), label='document')
    _addin__string(document['name'], label='document.name')
    if 'native_id' in document:
        _addin__string(document['native_id'], label='document.native_id', nullable=True)
    if 'request_id' in document:
        _addin__string(document['request_id'], label='document.request_id', nullable=True)
    coordinate = _addin__mapping(root['coordinate_system'], label='coordinate_system')
    _addin__required(coordinate, ('length_unit', 'handedness', 'matrix_convention'), label='coordinate_system')
    expected_coordinate = {'length_unit': 'mm', 'handedness': 'right', 'matrix_convention': 'row-major-local-to-parent'}
    for key, expected in expected_coordinate.items():
        if coordinate[key] != expected:
            raise ProtocolValidationError(f'coordinate_system.{key} must be {expected!r}, got {coordinate[key]!r}')
    if 'export_frame' in coordinate:
        frame = _addin__string(coordinate['export_frame'], label='coordinate_system.export_frame')
        if frame not in _addin_EXPORT_FRAMES:
            raise ProtocolValidationError('coordinate_system.export_frame must be one of ' + ', '.join(_addin_EXPORT_FRAMES))
    has_document_up = _addin_DOCUMENT_UP_FEATURE in features
    if ('document_up' in coordinate) != has_document_up:
        raise ProtocolValidationError(f'{_addin_DOCUMENT_UP_FEATURE} is required exactly when coordinate_system.document_up is present')
    if 'document_up' in coordinate and coordinate['document_up'] not in _addin_DOCUMENT_UP_AXES:
        raise ProtocolValidationError('coordinate_system.document_up must be one of ' + ', '.join(_addin_DOCUMENT_UP_AXES))
    assembly = _addin__mapping(root['assembly'], label='assembly')
    _addin__required(assembly, ('file', 'n_bodies_expected', 'bbox_mm'), label='assembly')
    assembly_file = _addin__member_name(assembly['file'], label='assembly.file')
    expected_bodies = _addin__integer(assembly['n_bodies_expected'], label='assembly.n_bodies_expected')
    bbox = _addin__list(assembly['bbox_mm'], label='assembly.bbox_mm')
    if len(bbox) != 2:
        raise ProtocolValidationError('assembly.bbox_mm must contain minimum and maximum points')
    low = _addin__point(bbox[0], label='assembly.bbox_mm[0]')
    high = _addin__point(bbox[1], label='assembly.bbox_mm[1]')
    if any((float(left) > float(right) for left, right in zip(low, high))):
        raise ProtocolValidationError('assembly.bbox_mm minimum exceeds maximum')
    automatic_feature = _addin_DOMAIN_AUTOMATIC_FEATURE in features
    domain_planes = _addin_validate_domain_record(assembly.get('domain'), automatic_feature=automatic_feature)
    files = _addin__mapping(root['files'], label='files')
    if not files:
        raise ProtocolValidationError('files must contain the complete artifact inventory')
    file_names: set[str] = set()
    for raw_name, raw_record in files.items():
        name = _addin__member_name(raw_name, label='files key')
        if name in file_names:
            raise ProtocolValidationError(f'files contains duplicate path {name!r}')
        file_names.add(name)
        record = _addin__mapping(raw_record, label=f'files[{name!r}]')
        _addin__required(record, ('sha256', 'size_bytes', 'media_type', 'purpose'), label=f'files[{name!r}]')
        digest = _addin__string(record['sha256'], label=f'files[{name!r}].sha256')
        if not _addin__SHA256.fullmatch(digest):
            raise ProtocolValidationError(f'files[{name!r}].sha256 must use sha256:<hex>')
        _addin__integer(record['size_bytes'], label=f'files[{name!r}].size_bytes')
        _addin__string(record['media_type'], label=f'files[{name!r}].media_type')
        if record['purpose'] not in {'exterior-assembly', 'fem-air-volume', 'cad-document'}:
            raise ProtocolValidationError(f'files[{name!r}].purpose is invalid')
    if assembly_file not in files:
        raise ProtocolValidationError('assembly.file is not present in files')
    if files[assembly_file].get('purpose') != 'exterior-assembly':
        raise ProtocolValidationError("assembly.file must have purpose 'exterior-assembly'")
    scope = _addin__mapping(root['scope'], label='scope')
    _addin__required(scope, ('selection', 'included', 'skipped', 'fem_air_volumes', 'status'), label='scope')
    _addin__string(scope['selection'], label='scope.selection')
    included = _addin__list(scope['included'], label='scope.included')
    skipped = _addin__list(scope['skipped'], label='scope.skipped')
    fem_volumes = _addin__list(scope['fem_air_volumes'], label='scope.fem_air_volumes')
    if scope['status'] not in {'clean', 'degraded'}:
        raise ProtocolValidationError("scope.status must be 'clean' or 'degraded'")
    for index, raw_record in enumerate(included):
        record = _addin__mapping(raw_record, label=f'scope.included[{index}]')
        _addin__required(record, ('object_id', 'name', 'component', 'body_kind', 'visible', 'external_reference'), label=f'scope.included[{index}]')
        if record['body_kind'] not in {'solid', 'surface'}:
            raise ProtocolValidationError(f'scope.included[{index}].body_kind is invalid')
        if record['visible'] is not True:
            raise ProtocolValidationError(f'scope.included[{index}].visible must be true')
        if record['external_reference'] not in {'none', 'resolved-stale', 'resolved-current'}:
            raise ProtocolValidationError(f'scope.included[{index}].external_reference is invalid')
    if len(included) != expected_bodies:
        raise ProtocolValidationError('scope.included count must equal assembly.n_bodies_expected')
    degraded_reasons = 0
    for index, raw_record in enumerate(skipped):
        record = _addin__mapping(raw_record, label=f'scope.skipped[{index}]')
        _addin__required(record, ('kind', 'reason', 'severity'), label=f'scope.skipped[{index}]')
        _addin__string(record['reason'], label=f'scope.skipped[{index}].reason')
        if record['severity'] not in {'info', 'degraded'}:
            raise ProtocolValidationError(f'scope.skipped[{index}].severity is invalid')
        degraded_reasons += record['severity'] == 'degraded'
    for record in included:
        degraded_reasons += record.get('severity') == 'degraded'
        degraded_reasons += record.get('external_reference') == 'resolved-stale'
    if bool(degraded_reasons) != (scope['status'] == 'degraded'):
        raise ProtocolValidationError('scope.status must be degraded exactly when a recorded scope reason is degraded')
    fem_files: set[str] = set()
    for index, raw_record in enumerate(fem_volumes):
        record = _addin__mapping(raw_record, label=f'scope.fem_air_volumes[{index}]')
        _addin__required(record, ('file', 'n_bodies_expected'), label=f'scope.fem_air_volumes[{index}]')
        name = _addin__member_name(record['file'], label=f'scope.fem_air_volumes[{index}].file')
        if record['n_bodies_expected'] != 1:
            raise ProtocolValidationError(f'scope.fem_air_volumes[{index}] must expect exactly one solid')
        if name not in files or files[name].get('purpose') != 'fem-air-volume':
            raise ProtocolValidationError(f'scope.fem_air_volumes[{index}].file must name a FEM file inventory entry')
        fem_files.add(name)
    inventoried_fem = {name for name, record in files.items() if record.get('purpose') == 'fem-air-volume'}
    if fem_files != inventoried_fem:
        raise ProtocolValidationError('scope FEM volumes and files inventory must agree exactly')
    has_fem_feature = 'fem-air-volume-v1' in features
    if bool(fem_volumes) != has_fem_feature:
        raise ProtocolValidationError('fem-air-volume-v1 is required exactly when a FEM air volume is present')
    has_domain_feature = _addin_REDUCED_DOMAIN_FEATURE in features
    if bool(domain_planes) != has_domain_feature:
        raise ProtocolValidationError(f'{_addin_REDUCED_DOMAIN_FEATURE} is required exactly when assembly.domain declares a reduced domain')
    if 'cut_provenance' in assembly:
        if not automatic_feature:
            raise ProtocolValidationError(f'assembly.cut_provenance is accepted only with {_addin_DOMAIN_AUTOMATIC_FEATURE}')
        _addin__validate_cut_provenance(assembly['cut_provenance'], {str(item['object_id']) for item in included if isinstance(item, Mapping)})
    instances = _addin__list(root['instances'], label='instances')
    instance_ids: list[str] = []
    for index, instance in enumerate(instances):
        instance_ids.append(_addin__validate_instance(instance, index=index))
    if len(instance_ids) != len(set(instance_ids)):
        raise ProtocolValidationError('instances must have unique instance_id values')
    anchor = coordinate.get('solver_anchor_instance_id')
    if anchor is not None:
        anchor = _addin__string(anchor, label='coordinate_system.solver_anchor_instance_id')
    if not instances and anchor is not None:
        raise ProtocolValidationError('an unlinked return must not name a solver anchor instance')
    if len(instances) > 1 and anchor is None:
        raise ProtocolValidationError('multiple linked instances require solver_anchor_instance_id')
    if anchor is not None and anchor not in instance_ids:
        raise ProtocolValidationError('coordinate_system.solver_anchor_instance_id does not name an instance')
    sources = _addin__list(root['sources'], label='sources')
    if not sources:
        raise ProtocolValidationError('sources must contain at least one drivable patch')
    source_keys = [_addin__validate_source(source, index=index, instance_ids=set(instance_ids)) for index, source in enumerate(sources)]
    source_ids = [source_id for source_id, _channel in source_keys]
    channel_ids = [channel for _source_id, channel in source_keys]
    if _addin_SOURCE_IDENTITY_FEATURE in features:
        for index, source in enumerate(sources):
            source_id = str(source['id'])
            if source_id != source_id.strip():
                raise ProtocolValidationError(f'sources[{index}].id must be trimmed under {_addin_SOURCE_IDENTITY_FEATURE}')
            if len(source_id.encode('utf-8')) > _addin_SOURCE_IDENTITY_MAX_BYTES:
                raise ProtocolValidationError(f'sources[{index}].id must be at most {_addin_SOURCE_IDENTITY_MAX_BYTES} UTF-8 bytes under {_addin_SOURCE_IDENTITY_FEATURE}')
            instance = source.get('instance_id')
            instance_text = 'null' if instance is None else instance
            name = f"wg-import-v1|tag=9999|source_id={source_id}|instance_id={instance_text}|role={source['role']}"
            if len(name.encode('utf-8')) > _addin_GMSH_PHYSICAL_NAME_MAX_BYTES:
                raise ProtocolValidationError(f'sources[{index}] would need a mesh physical name longer than {_addin_GMSH_PHYSICAL_NAME_MAX_BYTES} UTF-8 bytes under {_addin_SOURCE_IDENTITY_FEATURE}')
    if len(source_ids) != len(set(source_ids)):
        raise ProtocolValidationError('sources must have unique id values')
    if len(channel_ids) != len(set(channel_ids)):
        raise ProtocolValidationError('sources must have unique default_drive_channel_id values')
    if root.get('acoustics') is not None:
        raise ProtocolValidationError('acoustics must be null in wgreturn_version 1.0')

# WG ingress structural profile.
_wg_SUPPORTED_MAJOR = 1
_wg_SUPPORTED_VERSION = '1.1'
_wg_SUPPORTED_FEATURES = frozenset({'checksummed-files-v1', 'assembly-frame-v1', 'instance-records-v1', 'fem-air-volume-v1', 'reduced-domain-v1', 'source-identity-v1', 'document-up-v1', 'domain-automatic-v1'})
_wg_EXPORT_FRAMES = ('root-component', 'selected-occurrence-component')
_wg_DOCUMENT_UP_FEATURE = 'document-up-v1'
_wg_DOCUMENT_UP_AXES = ('+y', '+z')
_wg_DOMAIN_PLANES = ('x0', 'y0')
_wg_DOMAIN_KIND_FOR_PLANES = {(): 'full', ('x0',): 'half', ('y0',): 'half', ('x0', 'y0'): 'quarter'}
_wg_REDUCED_DOMAIN_FEATURE = 'reduced-domain-v1'
_wg_DOMAIN_AUTOMATIC_FEATURE = 'domain-automatic-v1'
_wg_DOMAIN_AUTOMATIC = 'automatic'
_wg_CUT_FEATURE_KINDS = ('split-body', 'extrude-cut', 'other')
_wg_CUT_TOOL_KINDS = ('origin-plane', 'construction-plane')
_wg_CUT_ORIGIN_PLANES = {'YZ': 'x0', 'XZ': 'y0', 'XY': 'z0'}
_wg_CUT_KEPT_SIDES = ('positive', 'negative')
_wg_SOURCE_IDENTITY_FEATURE = 'source-identity-v1'
_wg_GMSH_PHYSICAL_NAME_MAX_BYTES = 128
_wg_SOURCE_IDENTITY_MAX_BYTES = 25
_wg_WORST_CASE_SOURCE_TAG = 9999
_wg_REQUIRED_BASE_FEATURES = frozenset({'checksummed-files-v1', 'assembly-frame-v1', 'instance-records-v1'})
_wg_FORBIDDEN_VERDICT_KEYS = frozenset({'physical_tag', 'tag', 'tags', 'tag_map', 'tag_namespace', 'freshness', 'healing', 'solver_ready', 'symmetry'})
_wg__VERSION = re.compile('^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$')
_wg__RETURN_ID = re.compile('^wgr_[0-9A-HJKMNP-TV-Z]{26}$')
_wg__WINDOWS_DRIVE = re.compile('^[A-Za-z]:')
_wg__RIGID_TOLERANCE = 1e-06

def _wg__fail(path: str, message: str) -> None:
    raise ProtocolValidationError(f'{path}: {message}')

def _wg__walk(value: Any, path: str='$') -> Iterable[tuple[str, Any]]:
    yield (path, value)
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f'{path}.{key}'
            if key == 'config' and re.fullmatch('\\$\\.instances\\[\\d+\\]', path):
                yield (child_path, child)
                continue
            if key in _wg_FORBIDDEN_VERDICT_KEYS:
                _wg__fail(child_path, 'CAD-authored verdict keys are forbidden')
            yield from _wg__walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _wg__walk(child, f'{path}[{index}]')

def _wg__validate_finite_tree(manifest: Mapping[str, Any]) -> None:
    for path, value in _wg__walk(manifest):
        if isinstance(value, float) and (not math.isfinite(value)):
            _wg__fail(path, 'must be finite')

def _wg__mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _wg__fail(path, 'must be an object')
    return value

def _wg__list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _wg__fail(path, 'must be an array')
    return value

def _wg__string(value: Any, path: str, *, nullable: bool=False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        _wg__fail(path, 'must be a non-empty string' + (' or null' if nullable else ''))
    return value

def _wg__integer(value: Any, path: str, *, minimum: int=0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _wg__fail(path, f'must be an integer >= {minimum}')
    return value

def _wg__number(value: Any, path: str, *, positive: bool=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _wg__fail(path, 'must be a finite number')
    number = float(value)
    if not math.isfinite(number):
        _wg__fail(path, 'must be finite')
    if positive and number <= 0.0:
        _wg__fail(path, 'must be greater than zero')
    return number

def _wg__required(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        _wg__fail(f'{path}.{key}', 'is required')
    return obj[key]

def _wg__timestamp(value: Any, path: str) -> str:
    text = _wg__string(value, path)
    assert text is not None
    if not text.endswith('Z'):
        _wg__fail(path, 'must be an RFC-3339 UTC timestamp ending in Z')
    try:
        parsed = datetime.fromisoformat(text[:-1] + '+00:00')
    except ValueError as exc:
        raise ProtocolValidationError(f'{path}: must be an RFC-3339 timestamp') from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _wg__fail(path, 'must be UTC')
    return text

def _wg__vector(value: Any, path: str, length: int) -> list[float]:
    items = _wg__list(value, path)
    if len(items) != length:
        _wg__fail(path, f'must contain exactly {length} numbers')
    return [_wg__number(item, f'{path}[{index}]') for index, item in enumerate(items)]

def _wg__domain(value: Any, *, automatic_feature: bool=False) -> tuple[str, ...]:
    """Validate ``assembly.domain`` and return the planes it declares.

    Absent means the full domain, so every bundle written before the member
    existed validates unchanged. ``{"kind": "automatic"}`` declares no plane
    and is accepted only under ``domain-automatic-v1`` (and required by it).
    """
    path = '$.assembly.domain'
    if value is None:
        if automatic_feature:
            _wg__fail('$.required_features', f"{_wg_DOMAIN_AUTOMATIC_FEATURE} is required exactly when $.assembly.domain.kind is 'automatic'")
        return ()
    domain = _wg__mapping(value, path)
    kind = _wg__string(_wg__required(domain, 'kind', path), f'{path}.kind')
    if (kind == _wg_DOMAIN_AUTOMATIC) != automatic_feature:
        _wg__fail('$.required_features', f"{_wg_DOMAIN_AUTOMATIC_FEATURE} is required exactly when $.assembly.domain.kind is 'automatic'")
    if kind == _wg_DOMAIN_AUTOMATIC:
        extra = sorted(set(domain) - {'kind'})
        if extra:
            _wg__fail(path, f"an automatic domain states nothing else, got {', '.join(extra)}")
        return ()
    names = [_wg__string(item, f'{path}.cut_planes[{index}]') for index, item in enumerate(_wg__list(_wg__required(domain, 'cut_planes', path), f'{path}.cut_planes'))]
    unknown = [name for name in names if name not in _wg_DOMAIN_PLANES]
    if unknown:
        _wg__fail(f'{path}.cut_planes', f"may only name {', '.join(_wg_DOMAIN_PLANES)}")
    if len(set(names)) != len(names):
        _wg__fail(f'{path}.cut_planes', 'must not repeat a plane')
    planes = tuple((plane for plane in _wg_DOMAIN_PLANES if plane in set(names)))
    if _wg_DOMAIN_KIND_FOR_PLANES[planes] != kind:
        _wg__fail(f'{path}.kind', f'must be {_wg_DOMAIN_KIND_FOR_PLANES[planes]!r} for {list(planes)!r}')
    if _wg__string(_wg__required(domain, 'declared_by', path), f'{path}.declared_by') != 'cad-author':
        _wg__fail(f'{path}.declared_by', "must be 'cad-author'")
    evidence = _wg__mapping(domain.get('evidence', {}), f'{path}.evidence')
    if set(evidence) != set(planes):
        _wg__fail(f'{path}.evidence', 'must measure exactly the declared planes')
    for plane in planes:
        entry_path = f'{path}.evidence.{plane}'
        entry = _wg__mapping(evidence[plane], entry_path)
        minimum = _wg__number(_wg__required(entry, 'min_mm', entry_path), f'{entry_path}.min_mm')
        maximum = _wg__number(_wg__required(entry, 'max_mm', entry_path), f'{entry_path}.max_mm')
        tolerance = _wg__number(_wg__required(entry, 'tolerance_mm', entry_path), f'{entry_path}.tolerance_mm')
        if tolerance < 0.0:
            _wg__fail(f'{entry_path}.tolerance_mm', 'must not be negative')
        if minimum > maximum:
            _wg__fail(entry_path, 'min_mm must not exceed max_mm')
        if minimum < -tolerance:
            _wg__fail(entry_path, f'declares a reduced domain the measurement contradicts on {plane}')
        if maximum <= tolerance:
            _wg__fail(entry_path, f'declares a reduced domain with no extent on the positive side of {plane}')
    return planes

def _wg__cut_provenance(value: Any, included_ids: set[str]) -> None:
    """Validate ``assembly.cut_provenance``: the cuts the CAD timeline recorded.

    One entry per recorded cut of one exported body. Schema only: whether an
    entry is usable -- its body and frame are this snapshot's, its side is
    supported, the meshed geometry agrees -- is WG's revalidation, not the
    reader's (``domain_interpretation.py``).
    """
    path = '$.assembly.cut_provenance'
    entries = _wg__list(value, path)
    for index, item in enumerate(entries):
        entry_path = f'{path}[{index}]'
        entry = _wg__mapping(item, entry_path)
        allowed = {'body_object_id', 'feature', 'tool', 'plane', 'kept_side', 'export_frame'}
        extra = sorted(set(entry) - allowed)
        if extra:
            _wg__fail(entry_path, f"unknown member(s): {', '.join(extra)}")
        body = _wg__string(_wg__required(entry, 'body_object_id', entry_path), f'{entry_path}.body_object_id')
        if body not in included_ids:
            _wg__fail(f'{entry_path}.body_object_id', 'must name a $.scope.included body')
        feature = _wg__mapping(_wg__required(entry, 'feature', entry_path), f'{entry_path}.feature')
        if _wg__string(_wg__required(feature, 'kind', f'{entry_path}.feature'), f'{entry_path}.feature.kind') not in _wg_CUT_FEATURE_KINDS:
            _wg__fail(f'{entry_path}.feature.kind', f"must be one of {', '.join(_wg_CUT_FEATURE_KINDS)}")
        name = _wg__string(_wg__required(feature, 'name', f'{entry_path}.feature'), f'{entry_path}.feature.name')
        if not name or not name.strip() or len(name) > 200:
            _wg__fail(f'{entry_path}.feature.name', 'must be a non-empty name of at most 200 characters')
        tool = _wg__mapping(_wg__required(entry, 'tool', entry_path), f'{entry_path}.tool')
        if _wg__string(_wg__required(tool, 'kind', f'{entry_path}.tool'), f'{entry_path}.tool.kind') not in _wg_CUT_TOOL_KINDS:
            _wg__fail(f'{entry_path}.tool.kind', f"must be one of {', '.join(_wg_CUT_TOOL_KINDS)}")
        origin = _wg__string(_wg__required(tool, 'origin_plane', f'{entry_path}.tool'), f'{entry_path}.tool.origin_plane')
        if origin not in _wg_CUT_ORIGIN_PLANES:
            _wg__fail(f'{entry_path}.tool.origin_plane', f"must be one of {', '.join(_wg_CUT_ORIGIN_PLANES)}")
        plane = _wg__string(_wg__required(entry, 'plane', entry_path), f'{entry_path}.plane')
        if plane not in _wg_CUT_ORIGIN_PLANES.values():
            _wg__fail(f'{entry_path}.plane', 'must be one of x0, y0, z0')
        if _wg_CUT_ORIGIN_PLANES[str(origin)] != plane:
            _wg__fail(f'{entry_path}.plane', f'the {origin} plane is {_wg_CUT_ORIGIN_PLANES[str(origin)]}, not {plane}')
        if _wg__string(_wg__required(entry, 'kept_side', entry_path), f'{entry_path}.kept_side') not in _wg_CUT_KEPT_SIDES:
            _wg__fail(f'{entry_path}.kept_side', f"must be one of {', '.join(_wg_CUT_KEPT_SIDES)}")
        if _wg__string(_wg__required(entry, 'export_frame', entry_path), f'{entry_path}.export_frame') not in _wg_EXPORT_FRAMES:
            _wg__fail(f'{entry_path}.export_frame', f"must be one of {', '.join(_wg_EXPORT_FRAMES)}")

def _wg__bbox(value: Any, path: str) -> list[list[float]]:
    rows = _wg__list(value, path)
    if len(rows) != 2:
        _wg__fail(path, 'must be [[min_x,min_y,min_z],[max_x,max_y,max_z]]')
    low = _wg__vector(rows[0], f'{path}[0]', 3)
    high = _wg__vector(rows[1], f'{path}[1]', 3)
    if any((a > b for a, b in zip(low, high, strict=True))):
        _wg__fail(path, 'minimum coordinates must not exceed maximum coordinates')
    return [low, high]

def _wg__matrix(value: Any, path: str) -> list[list[float]]:
    rows = _wg__list(value, path)
    if len(rows) != 4:
        _wg__fail(path, 'must be a 4x4 row-major matrix')
    matrix = [_wg__vector(row, f'{path}[{index}]', 4) for index, row in enumerate(rows)]
    if matrix[3] != [0.0, 0.0, 0.0, 1.0]:
        _wg__fail(f'{path}[3]', 'last row must equal [0, 0, 0, 1]')
    return matrix

def _wg__validate_rotation_chirality(matrix: list[list[float]], path: str, instance_id: str) -> None:
    rotation = [row[:3] for row in matrix[:3]]
    product = [[sum((rotation[k][i] * rotation[k][j] for k in range(3))) for j in range(3)] for i in range(3)]
    orthonormal = all((abs(product[i][j] - (1.0 if i == j else 0.0)) <= _wg__RIGID_TOLERANCE for i in range(3) for j in range(3)))
    if not orthonormal:
        _wg__fail(path, f'rotation is not orthonormal within 1e-6 (instance_id={instance_id!r})')
    a, b, c = rotation[0]
    d, e, f = rotation[1]
    g, h, i = rotation[2]
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if determinant < 0.0:
        _wg__fail(path, f'mirrored placement; chirality is unsupported (instance_id={instance_id!r})')
    if abs(determinant - 1.0) > _wg__RIGID_TOLERANCE:
        _wg__fail(path, f'determinant {determinant:.9g} is not +1 within 1e-6 (instance_id={instance_id!r})')

def _wg__portable_member_name(raw: str, path: str) -> tuple[str, tuple[str, ...]]:
    if raw.startswith(('/', '\\')) or _wg__WINDOWS_DRIVE.match(raw):
        _wg__fail(path, 'must be a relative bundle member path')
    normalized = raw.replace('\\', '/')
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if not parts or any((part in {'', '.', '..'} for part in parts)):
        _wg__fail(path, "must not contain empty, '.', or '..' path segments")
    if str(pure) != normalized:
        _wg__fail(path, 'must be a normalized relative path')
    portable = tuple((unicodedata.normalize('NFKC', part).casefold() for part in parts))
    return (normalized, portable)

def _wg__validate_fingerprint(value: Any, path: str) -> None:
    if value is None:
        return
    obj = _wg__mapping(value, path)
    is_solid = _wg__required(obj, 'is_solid', path)
    if not isinstance(is_solid, bool):
        _wg__fail(f'{path}.is_solid', 'must be boolean')
    volume_mm3 = _wg__required(obj, 'volume_mm3', path)
    if is_solid:
        _wg__number(volume_mm3, f'{path}.volume_mm3')
    elif volume_mm3 is not None:
        _wg__fail(f'{path}.volume_mm3', 'must be null for a surface body')
    _wg__vector(_wg__required(obj, 'bbox_mm', path), f'{path}.bbox_mm', 6)

def _wg__validate_instance(value: Any, path: str) -> str:
    obj = _wg__mapping(value, path)
    instance_id = _wg__string(_wg__required(obj, 'instance_id', path), f'{path}.instance_id')
    assert instance_id is not None
    _wg__string(_wg__required(obj, 'design_id', path), f'{path}.design_id')
    _wg__string(obj.get('lineage_id'), f'{path}.lineage_id', nullable=True)
    if obj.get('edit_version') is not None:
        _wg__integer(obj['edit_version'], f'{path}.edit_version', minimum=1)
    if obj.get('design_hash') is not None:
        _wg__string(obj['design_hash'], f'{path}.design_hash')
    if obj.get('formula') is not None:
        _wg__string(obj['formula'], f'{path}.formula')
    if obj.get('config') is not None:
        _wg__mapping(obj['config'], f'{path}.config')
    _wg__string(_wg__required(obj, 'export_id', path), f'{path}.export_id')
    _wg__integer(_wg__required(obj, 'export_sequence', path), f'{path}.export_sequence', minimum=1)
    for name in ('geometry_hash', 'origin_bundle_id', 'occurrence_path'):
        _wg__string(obj.get(name), f'{path}.{name}', nullable=True)
    mode = _wg__string(_wg__required(obj, 'build_mode', path), f'{path}.build_mode')
    if mode not in {'enclosure', 'freestanding'}:
        _wg__fail(f'{path}.build_mode', "must be 'enclosure' or 'freestanding'")
    _wg__string(_wg__required(obj, 'parameter_prefix', path), f'{path}.parameter_prefix')
    assembly_from_link = _wg__matrix(_wg__required(obj, 'assembly_from_link', path), f'{path}.assembly_from_link')
    _wg__validate_rotation_chirality(assembly_from_link, f'{path}.assembly_from_link', instance_id)
    chirality = _wg__string(_wg__required(obj, 'chirality', path), f'{path}.chirality')
    if chirality != 'original':
        _wg__fail(f'{path}.chirality', "Phase 2 accepts only 'original'")
    evidence = _wg__mapping(_wg__required(obj, 'body_evidence', path), f'{path}.body_evidence')
    state = _wg__string(_wg__required(evidence, 'local_body_state', f'{path}.body_evidence'), f'{path}.body_evidence.local_body_state')
    if state not in {'unmodified', 'modified', 'missing', 'unknown'}:
        _wg__fail(f'{path}.body_evidence.local_body_state', 'must be unmodified, modified, missing, or unknown')
    _wg__validate_fingerprint(evidence.get('baseline_fingerprint'), f'{path}.body_evidence.baseline_fingerprint')
    _wg__validate_fingerprint(evidence.get('observed_fingerprint'), f'{path}.body_evidence.observed_fingerprint')
    _wg__timestamp(_wg__required(evidence, 'observed_at', f'{path}.body_evidence'), f'{path}.body_evidence.observed_at')
    contract = obj.get('source_contract')
    if contract is not None:
        source = _wg__mapping(contract, f'{path}.source_contract')
        _wg__string(_wg__required(source, 'role', f'{path}.source_contract'), f'{path}.source_contract.role')
        _wg__number(_wg__required(source, 'throat_z_mm', f'{path}.source_contract'), f'{path}.source_contract.throat_z_mm')
        plane = _wg__mapping(_wg__required(source, 'throat_plane_link', f'{path}.source_contract'), f'{path}.source_contract.throat_plane_link')
        _wg__vector(_wg__required(plane, 'origin_mm', f'{path}.source_contract.throat_plane_link'), f'{path}.source_contract.throat_plane_link.origin_mm', 3)
        normal = _wg__vector(_wg__required(plane, 'normal', f'{path}.source_contract.throat_plane_link'), f'{path}.source_contract.throat_plane_link.normal', 3)
        if math.sqrt(sum((item * item for item in normal))) <= 0.0:
            _wg__fail(f'{path}.source_contract.throat_plane_link.normal', 'must be non-zero')
        axis = _wg__mapping(_wg__required(source, 'axis_link', f'{path}.source_contract'), f'{path}.source_contract.axis_link')
        _wg__vector(_wg__required(axis, 'origin_mm', f'{path}.source_contract.axis_link'), f'{path}.source_contract.axis_link.origin_mm', 3)
        direction = _wg__vector(_wg__required(axis, 'direction', f'{path}.source_contract.axis_link'), f'{path}.source_contract.axis_link.direction', 3)
        if math.sqrt(sum((item * item for item in direction))) <= 0.0:
            _wg__fail(f'{path}.source_contract.axis_link.direction', 'must be non-zero')
        diameter = _wg__number(_wg__required(source, 'throat_diameter_mm', f'{path}.source_contract'), f'{path}.source_contract.throat_diameter_mm', positive=True)
        expected = _wg__number(_wg__required(source, 'expected_disc_area_mm2', f'{path}.source_contract'), f'{path}.source_contract.expected_disc_area_mm2', positive=True)
        disc = math.pi * diameter * diameter / 4.0
        if abs(expected - disc) / disc > 0.01:
            _wg__fail(f'{path}.source_contract.expected_disc_area_mm2', 'differs from pi*throat_diameter_mm^2/4 by more than 1%')
    return instance_id

def _wg__validate_source(value: Any, path: str, instance_ids: set[str], *, source_identity: bool=False) -> str:
    obj = _wg__mapping(value, path)
    source_id = _wg__string(_wg__required(obj, 'id', path), f'{path}.id')
    assert source_id is not None
    if source_identity:
        if source_id != source_id.strip():
            _wg__fail(f'{path}.id', f'{_wg_SOURCE_IDENTITY_FEATURE} source identity must be trimmed')
        if len(source_id.encode('utf-8')) > _wg_SOURCE_IDENTITY_MAX_BYTES:
            _wg__fail(f'{path}.id', f'{_wg_SOURCE_IDENTITY_FEATURE} source identity must be at most {_wg_SOURCE_IDENTITY_MAX_BYTES} UTF-8 bytes')
    role = _wg__string(_wg__required(obj, 'role', path), f'{path}.role')
    assert role is not None
    instance_id = _wg__string(obj.get('instance_id'), f'{path}.instance_id', nullable=True)
    if instance_id is not None and instance_id not in instance_ids:
        _wg__fail(f'{path}.instance_id', f'does not name an instances[] record: {instance_id!r}')
    if source_identity:
        name_bytes = len(source_physical_name(_wg_WORST_CASE_SOURCE_TAG, source_id, instance_id, role).encode('utf-8'))
        if name_bytes > _wg_GMSH_PHYSICAL_NAME_MAX_BYTES:
            _wg__fail(path, f'{_wg_SOURCE_IDENTITY_FEATURE} source {source_id!r} would need a {name_bytes}-byte mesh physical name (its id, instance_id and role together); the mesh keeps at most {_wg_GMSH_PHYSICAL_NAME_MAX_BYTES} UTF-8 bytes, so shorten the role or instance_id')
    if not isinstance(_wg__required(obj, 'required', path), bool):
        _wg__fail(f'{path}.required', 'must be boolean')
    _wg__string(_wg__required(obj, 'default_drive_channel_id', path), f'{path}.default_drive_channel_id')
    policy = _wg__string(_wg__required(obj, 'patch_policy', path), f'{path}.patch_policy')
    if policy not in {'single-connected', 'explicit-disconnected'}:
        _wg__fail(f'{path}.patch_policy', "must be 'single-connected' or 'explicit-disconnected'")
    components = _wg__integer(_wg__required(obj, 'expected_connected_components', path), f'{path}.expected_connected_components', minimum=1)
    if policy == 'single-connected' and components != 1:
        _wg__fail(f'{path}.expected_connected_components', 'must be 1 for single-connected')
    selectors = _wg__mapping(_wg__required(obj, 'selectors', path), f'{path}.selectors')
    mechanisms = 0
    linked = selectors.get('linked_throat')
    if linked is not None:
        linked_obj = _wg__mapping(linked, f'{path}.selectors.linked_throat')
        linked_id = _wg__string(_wg__required(linked_obj, 'instance_id', f'{path}.selectors.linked_throat'), f'{path}.selectors.linked_throat.instance_id')
        if linked_id not in instance_ids:
            _wg__fail(f'{path}.selectors.linked_throat.instance_id', f'does not name an instances[] record: {linked_id!r}')
        if instance_id != linked_id:
            _wg__fail(f'{path}.selectors.linked_throat.instance_id', 'must equal source.instance_id')
        mechanisms += 1
    for key in ('appearance_labels', 'shell_names'):
        if key in selectors:
            values = _wg__list(selectors[key], f'{path}.selectors.{key}')
            for index, item in enumerate(values):
                _wg__string(item, f'{path}.selectors.{key}[{index}]')
            if values:
                mechanisms += 1
    if 'advanced_face_indices' in selectors:
        indices = _wg__list(selectors['advanced_face_indices'], f'{path}.selectors.advanced_face_indices')
        for index, item in enumerate(indices):
            _wg__integer(item, f'{path}.selectors.advanced_face_indices[{index}]', minimum=0)
        if indices:
            mechanisms += 1
    if mechanisms == 0:
        _wg__fail(f'{path}.selectors', 'must contain at least one non-empty selector mechanism')
    observed = _wg__mapping(_wg__required(obj, 'observed', path), f'{path}.observed')
    face_count = _wg__integer(_wg__required(observed, 'face_count', f'{path}.observed'), f'{path}.observed.face_count', minimum=1)
    _wg__number(_wg__required(observed, 'total_area_mm2', f'{path}.observed'), f'{path}.observed.total_area_mm2', positive=True)
    per_face = _wg__list(_wg__required(observed, 'per_face_area_mm2', f'{path}.observed'), f'{path}.observed.per_face_area_mm2')
    if len(per_face) != face_count:
        _wg__fail(f'{path}.observed.per_face_area_mm2', 'length must equal observed.face_count')
    for index, item in enumerate(per_face):
        _wg__number(item, f'{path}.observed.per_face_area_mm2[{index}]', positive=True)
    bodies = _wg__list(_wg__required(observed, 'bodies', f'{path}.observed'), f'{path}.observed.bodies')
    for index, item in enumerate(bodies):
        _wg__string(item, f'{path}.observed.bodies[{index}]')
    if obj.get('suggested_resolution_mm') is not None:
        _wg__number(obj['suggested_resolution_mm'], f'{path}.suggested_resolution_mm', positive=True)
    return source_id

def _wg_validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate all schema fields consumed by Phase-2 ingestion."""
    _wg__validate_finite_tree(manifest)
    version = _wg__string(_wg__required(manifest, 'wgreturn_version', '$'), '$.wgreturn_version')
    assert version is not None
    match = _wg__VERSION.fullmatch(version)
    if match is None:
        _wg__fail('$.wgreturn_version', 'must be exactly major.minor')
    if int(match.group(1)) != _wg_SUPPORTED_MAJOR:
        _wg__fail('$.wgreturn_version', f'unsupported major {match.group(1)}; reader supports {_wg_SUPPORTED_VERSION}')
    minor_version = int(match.group(2))
    features = _wg__list(_wg__required(manifest, 'required_features', '$'), '$.required_features')
    feature_names = []
    for index, item in enumerate(features):
        name = _wg__string(item, f'$.required_features[{index}]')
        assert name is not None
        feature_names.append(name)
    if len(set(feature_names)) != len(feature_names):
        _wg__fail('$.required_features', 'feature names must be unique')
    unknown = sorted(set(feature_names) - _wg_SUPPORTED_FEATURES)
    if unknown:
        _wg__fail('$.required_features', f"unknown required feature(s): {', '.join(unknown)}")
    missing_features = sorted(_wg_REQUIRED_BASE_FEATURES - set(feature_names))
    if missing_features:
        _wg__fail('$.required_features', f"missing required feature(s): {', '.join(missing_features)}")
    returned = _wg__mapping(_wg__required(manifest, 'return', '$'), '$.return')
    return_id = _wg__string(_wg__required(returned, 'id', '$.return'), '$.return.id')
    if return_id is None or _wg__RETURN_ID.fullmatch(return_id) is None:
        _wg__fail('$.return.id', 'must be a wgr_ ULID')
    _wg__timestamp(_wg__required(returned, 'created_at', '$.return'), '$.return.created_at')
    generator = _wg__mapping(_wg__required(manifest, 'generator', '$'), '$.generator')
    for key in ('adapter', 'adapter_version', 'cad_app', 'cad_version'):
        _wg__string(_wg__required(generator, key, '$.generator'), f'$.generator.{key}')
    document = _wg__mapping(_wg__required(manifest, 'document', '$'), '$.document')
    _wg__string(_wg__required(document, 'name', '$.document'), '$.document.name')
    _wg__string(document.get('native_id'), '$.document.native_id', nullable=True)
    _wg__string(document.get('request_id'), '$.document.request_id', nullable=True)
    coordinates = _wg__mapping(_wg__required(manifest, 'coordinate_system', '$'), '$.coordinate_system')
    fixed = {'length_unit': 'mm', 'handedness': 'right', 'matrix_convention': 'row-major-local-to-parent'}
    for key, expected in fixed.items():
        if _wg__required(coordinates, key, '$.coordinate_system') != expected:
            _wg__fail(f'$.coordinate_system.{key}', f'must equal {expected!r}')
    if 'export_frame' in coordinates:
        frame = _wg__string(coordinates['export_frame'], '$.coordinate_system.export_frame')
        if frame not in _wg_EXPORT_FRAMES:
            _wg__fail('$.coordinate_system.export_frame', f"must be one of {', '.join(_wg_EXPORT_FRAMES)}")
    if ('document_up' in coordinates) != (_wg_DOCUMENT_UP_FEATURE in feature_names):
        _wg__fail('$.required_features', f'{_wg_DOCUMENT_UP_FEATURE} is required exactly when $.coordinate_system.document_up is present')
    if 'document_up' in coordinates:
        up = _wg__string(coordinates['document_up'], '$.coordinate_system.document_up')
        if up not in _wg_DOCUMENT_UP_AXES:
            _wg__fail('$.coordinate_system.document_up', f"must be one of {', '.join(_wg_DOCUMENT_UP_AXES)}")
    assembly = _wg__mapping(_wg__required(manifest, 'assembly', '$'), '$.assembly')
    _wg__string(_wg__required(assembly, 'file', '$.assembly'), '$.assembly.file')
    _wg__integer(_wg__required(assembly, 'n_bodies_expected', '$.assembly'), '$.assembly.n_bodies_expected', minimum=1)
    _wg__bbox(_wg__required(assembly, 'bbox_mm', '$.assembly'), '$.assembly.bbox_mm')
    if minor_version >= 1:
        _wg__string(_wg__required(assembly, 'signature_hash', '$.assembly'), '$.assembly.signature_hash')
    elif assembly.get('signature_hash') is not None:
        _wg__string(assembly['signature_hash'], '$.assembly.signature_hash')
    domain_planes = _wg__domain(assembly.get('domain'), automatic_feature=_wg_DOMAIN_AUTOMATIC_FEATURE in feature_names)
    if bool(domain_planes) != (_wg_REDUCED_DOMAIN_FEATURE in feature_names):
        _wg__fail('$.required_features', f'{_wg_REDUCED_DOMAIN_FEATURE} is required exactly when $.assembly.domain declares a reduced domain')
    scope = _wg__mapping(_wg__required(manifest, 'scope', '$'), '$.scope')
    _wg__string(_wg__required(scope, 'selection', '$.scope'), '$.scope.selection')
    included = _wg__list(_wg__required(scope, 'included', '$.scope'), '$.scope.included')
    for index, item in enumerate(included):
        entry_path = f'$.scope.included[{index}]'
        entry = _wg__mapping(item, entry_path)
        for key in ('object_id', 'name', 'body_kind', 'external_reference'):
            _wg__string(_wg__required(entry, key, entry_path), f'{entry_path}.{key}')
        if entry['body_kind'] not in {'solid', 'surface'}:
            _wg__fail(f'{entry_path}.body_kind', "must be 'solid' or 'surface'")
        if not isinstance(_wg__required(entry, 'visible', entry_path), bool):
            _wg__fail(f'{entry_path}.visible', 'must be boolean')
        _wg__string(entry.get('wglink_instance_id'), f'{entry_path}.wglink_instance_id', nullable=True)
    if 'cut_provenance' in assembly:
        if _wg_DOMAIN_AUTOMATIC_FEATURE not in feature_names:
            _wg__fail('$.assembly.cut_provenance', f'is accepted only with {_wg_DOMAIN_AUTOMATIC_FEATURE} and an automatic domain')
        _wg__cut_provenance(assembly['cut_provenance'], {str(item['object_id']) for item in included if isinstance(item, Mapping)})
    skipped = _wg__list(_wg__required(scope, 'skipped', '$.scope'), '$.scope.skipped')
    degraded = False
    for index, item in enumerate(skipped):
        skip = _wg__mapping(item, f'$.scope.skipped[{index}]')
        for key in ('object_id', 'kind', 'reason'):
            _wg__string(_wg__required(skip, key, f'$.scope.skipped[{index}]'), f'$.scope.skipped[{index}].{key}')
        severity = _wg__string(_wg__required(skip, 'severity', f'$.scope.skipped[{index}]'), f'$.scope.skipped[{index}].severity')
        if severity not in {'info', 'degraded'}:
            _wg__fail(f'$.scope.skipped[{index}].severity', "must be 'info' or 'degraded'")
        degraded = degraded or severity == 'degraded'
    status = _wg__string(_wg__required(scope, 'status', '$.scope'), '$.scope.status')
    if status not in {'clean', 'degraded'}:
        _wg__fail('$.scope.status', "must be 'clean' or 'degraded'")
    if (status == 'degraded') != degraded:
        _wg__fail('$.scope.status', "must be 'degraded' iff scope.skipped contains a degraded entry")
    fem = _wg__list(_wg__required(scope, 'fem_air_volumes', '$.scope'), '$.scope.fem_air_volumes')
    if fem and 'fem-air-volume-v1' not in feature_names:
        _wg__fail('$.required_features', 'fem-air-volume-v1 is required when FEM air volumes are present')
    for index, item in enumerate(fem):
        volume = _wg__mapping(item, f'$.scope.fem_air_volumes[{index}]')
        _wg__string(_wg__required(volume, 'file', f'$.scope.fem_air_volumes[{index}]'), f'$.scope.fem_air_volumes[{index}].file')
        expected = volume.get('n_bodies_expected', volume.get('n_solids_expected', volume.get('expected_solids')))
        if expected != 1:
            _wg__fail(f'$.scope.fem_air_volumes[{index}]', 'must declare exactly one expected solid')
    instances = _wg__list(_wg__required(manifest, 'instances', '$'), '$.instances')
    instance_ids = [_wg__validate_instance(item, f'$.instances[{index}]') for index, item in enumerate(instances)]
    if len(set(instance_ids)) != len(instance_ids):
        _wg__fail('$.instances', 'instance_id values must be unique')
    object_ids = [str(item['object_id']) for item in included]
    if len(set(object_ids)) != len(object_ids):
        _wg__fail('$.scope.included', 'object_id values must be unique')
    instance_id_set = set(instance_ids)
    for index, item in enumerate(included):
        owner = item.get('wglink_instance_id')
        if owner is not None and owner not in instance_id_set:
            _wg__fail(f'$.scope.included[{index}].wglink_instance_id', f'does not name an instances[] record: {owner!r}')
    anchor = coordinates.get('solver_anchor_instance_id')
    if len(instances) == 1:
        if anchor is not None and anchor != instance_ids[0]:
            _wg__fail('$.coordinate_system.solver_anchor_instance_id', 'must name the sole instance')
    elif len(instances) > 1:
        if anchor not in set(instance_ids):
            _wg__fail('$.coordinate_system.solver_anchor_instance_id', 'is required and must name an instance when multiple instances exist')
    elif anchor is not None:
        _wg__fail('$.coordinate_system.solver_anchor_instance_id', 'must be null or absent when instances is empty')
    sources = _wg__list(_wg__required(manifest, 'sources', '$'), '$.sources')
    if not sources:
        _wg__fail('$.sources', 'must contain at least one source')
    source_identity = _wg_SOURCE_IDENTITY_FEATURE in feature_names
    source_ids = [_wg__validate_source(item, f'$.sources[{index}]', set(instance_ids), source_identity=source_identity) for index, item in enumerate(sources)]
    if len(set(source_ids)) != len(source_ids):
        if source_identity:
            _wg__fail('$.sources', f'{_wg_SOURCE_IDENTITY_FEATURE} source identities must be unique within the return')
        _wg__fail('$.sources', 'source ids must be unique')
    channel_owners: dict[str, set[str]] = {}
    for source in sources:
        source_record = _wg__mapping(source, '$.sources')
        owner = source_record.get('instance_id')
        if not isinstance(owner, str) or not owner:
            continue
        channel = str(source_record['default_drive_channel_id'])
        channel_owners.setdefault(channel, set()).add(owner)
    reused_channels = sorted((channel for channel, owners in channel_owners.items() if len(owners) > 1))
    if reused_channels:
        _wg__fail('$.sources', 'default_drive_channel_id values must not span linked instances: ' + ', '.join(reused_channels))
    if _wg__required(manifest, 'acoustics', '$') is not None:
        _wg__fail('$.acoustics', 'must be null in Phase 2')
    return manifest

def validate_structure(manifest: Mapping[str, Any], profile: str) -> Mapping[str, Any] | None:
    """Validate using one endpoint's exact structural verdict and error order."""
    if profile == ADDIN_WRITER:
        return _addin_validate_return_manifest(manifest)
    if profile == WG_INGRESS:
        return _wg_validate_manifest(manifest)
    raise ValueError(f"unknown structure profile: {profile!r}")

# Public vocabulary used by both loose-module and package-style add-in imports.
SUPPORTED_RETURN_FEATURES = _addin_SUPPORTED_RETURN_FEATURES
SOURCE_IDENTITY_FEATURE = _addin_SOURCE_IDENTITY_FEATURE
DOCUMENT_UP_FEATURE = _addin_DOCUMENT_UP_FEATURE
DOCUMENT_UP_AXES = _addin_DOCUMENT_UP_AXES
DOMAIN_AUTOMATIC_FEATURE = _addin_DOMAIN_AUTOMATIC_FEATURE
DOMAIN_AUTOMATIC = _addin_DOMAIN_AUTOMATIC
SOURCE_IDENTITY_MAX_BYTES = _addin_SOURCE_IDENTITY_MAX_BYTES
GMSH_PHYSICAL_NAME_MAX_BYTES = _addin_GMSH_PHYSICAL_NAME_MAX_BYTES
BASE_RETURN_FEATURES = _addin_BASE_RETURN_FEATURES
EXPORT_FRAMES = _addin_EXPORT_FRAMES
DOMAIN_PLANES = _addin_DOMAIN_PLANES
DOMAIN_KIND_FOR_PLANES = _addin_DOMAIN_KIND_FOR_PLANES
DOMAIN_KINDS = _addin_DOMAIN_KINDS
REDUCED_DOMAIN_FEATURE = _addin_REDUCED_DOMAIN_FEATURE
CUT_FEATURE_KINDS = _addin_CUT_FEATURE_KINDS
CUT_TOOL_KINDS = _addin_CUT_TOOL_KINDS
CUT_ORIGIN_PLANES = _addin_CUT_ORIGIN_PLANES
CUT_KEPT_SIDES = _addin_CUT_KEPT_SIDES


def canonical_domain_planes(planes: Sequence[Any]) -> tuple[str, ...]:
    return _addin_canonical_domain_planes(planes)


def validate_domain_record(value: object, *, automatic_feature: bool = False) -> tuple[str, ...]:
    return _addin_validate_domain_record(value, automatic_feature=automatic_feature)
