"""The one saved-design reader: imports retired LOOKUP tables where designs load.

Schema validation never fits geometry; it refuses an unconverted LOOKUP payload
with ``lookup_import_required``. Every boundary that loads a saved design (files,
autosaves, CAD Link snapshots, stored run requests, CLI and API request bodies)
reads it through this module instead, which runs the Custom profile import on a
defensive copy. History reads never come here: a stored run keeps its historical
design and fingerprint.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from typing import Annotated, Any, Mapping

from pydantic import BeforeValidator

from server.design.migrate import MigrationApplication, apply_migrations
from server.design.schema import DesignConfig
from server.design.textcfg import ParsedDesign, parse as parse_text
from server.jobs.freeform_conversion import convert_design, conversion_notice, legacy_lookup_source
from server.jobs.models import SolveRequest

CUSTOM_PROFILE_IMPORT = "007_custom_profile_import"


@lru_cache(maxsize=32)
def _custom_profile_import(encoded: str) -> tuple[dict, str]:
    converted = convert_design(legacy_lookup_source(json.loads(encoded)), '', importing=True)
    return converted.design.model_dump(mode="json"), conversion_notice(converted)


def import_payload(payload: Mapping[str, Any]) -> tuple[dict, list[MigrationApplication]]:
    """Migrate a saved wire document and import retired tables on a defensive copy."""
    migrated, applied = apply_migrations(payload)
    if migrated.get("formula") == "LOOKUP":
        converted, note = _custom_profile_import(json.dumps(migrated, sort_keys=True, allow_nan=False))
        migrated = deepcopy(converted)
        applied.append(MigrationApplication(CUSTOM_PROFILE_IMPORT, note))
    return migrated, applied


def read_design(payload: Any) -> DesignConfig:
    if isinstance(payload, Mapping):
        payload, _applications = import_payload(payload)
    return DesignConfig.model_validate(payload)


def parse(text: str, *, migrate: bool = True) -> ParsedDesign:
    return parse_text(text, migrate=migrate, import_payload=import_payload)


def _imported_design(design: Mapping[str, Any]) -> Mapping[str, Any]:
    # Only a retired table is replaced; every other design keeps its exact wire
    # form so request validation and fingerprints see what was submitted.
    migrated, applied = import_payload(design)
    return migrated if any(item.name == CUSTOM_PROFILE_IMPORT for item in applied) else design


def import_request_payload(payload: Any) -> Any:
    """Import a saved design inside either solve-request wire shape.

    Returns the payload itself when nothing needed importing, otherwise a copy;
    archived input is never changed in place.
    """
    if not isinstance(payload, Mapping):
        return payload
    geometry = payload.get("geometry")
    nested = isinstance(geometry, Mapping) and geometry.get("type") == "parametric"
    owner = geometry if nested else payload
    design = owner.get("design")
    snapshot = owner.get("design_snapshot")
    snapshot_design = snapshot.get("design") if isinstance(snapshot, Mapping) else None
    imported = _imported_design(design) if isinstance(design, Mapping) else design
    imported_snapshot = (
        _imported_design(snapshot_design) if isinstance(snapshot_design, Mapping) else snapshot_design
    )
    if imported is design and imported_snapshot is snapshot_design:
        return payload
    result = deepcopy(dict(payload))
    target = result["geometry"] if nested else result
    if imported is not design:
        target["design"] = imported
    if imported_snapshot is not snapshot_design:
        target["design_snapshot"]["design"] = imported_snapshot
    return result


def read_request(payload: Any) -> SolveRequest:
    return SolveRequest.model_validate(import_request_payload(payload))


# A request body that may carry a saved design (the solve and plan APIs).
SavedSolveRequest = Annotated[SolveRequest, BeforeValidator(import_request_payload)]
