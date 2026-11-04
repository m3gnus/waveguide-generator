"""Explicit saved-design readers; schema validation never fits geometry."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from typing import Any, Mapping

from server.design.migrate import MigrationApplication, apply_migrations
from server.design.schema import DesignConfig
from server.design.textcfg import ParsedDesign, parse as parse_text
from server.jobs.freeform_conversion import convert_design, conversion_notice, legacy_lookup_source


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
        applied.append(MigrationApplication("007_custom_profile_import", note))
    return migrated, applied


def read_design(payload: Any) -> DesignConfig:
    if isinstance(payload, Mapping):
        payload, _applications = import_payload(payload)
    return DesignConfig.model_validate(payload)


def parse(text: str, *, migrate: bool = True) -> ParsedDesign:
    return parse_text(text, migrate=migrate, import_payload=import_payload)
