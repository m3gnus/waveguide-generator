#!/usr/bin/env python3
"""Verify a sanitized solver-neutral model identity manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel  # noqa: E402

from server.design.schema import DesignConfig, Expr  # noqa: E402


SCHEMA_VERSION = 2
NORMALIZATION_SCHEMA = "wg-design-physical-source-v2"
CANONICALIZATION = "json-domain-envelope-sort-keys-compact-utf8-ascii-v2"
PROFILE_KEYS = (
    "formula",
    "scale",
    "throat_ext_angle",
    "throat_ext_length",
    "slot_length",
    "length_mode",
    "coverage_mode",
    "morph",
    "R",
    "a",
    "a0",
    "b",
    "k",
    "m",
    "q",
    "r",
    "r0",
    "tmax",
)
EXCLUDED_ENCLOSURE_KEYS = frozenset({"front_resolution", "back_resolution"})


class IdentityError(ValueError):
    """The manifest or source snapshot violates the identity contract."""


def _typed(value: Any) -> Any:
    if isinstance(value, Expr):
        constant = value.constant_value()
        if constant is not None:
            return constant
        executable = value.execution_text()
        if executable is None:
            return None
        if not isinstance(executable, str):
            raise IdentityError("parameterized expressions must have executable text")
        return {"expression": executable}
    if isinstance(value, BaseModel):
        return {
            name: _typed(getattr(value, name))
            for name in value.__class__.model_fields
        }
    if isinstance(value, dict):
        return {key: _typed(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_typed(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise IdentityError("identity values must be finite")
    return value


def _extract_snapshot(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise IdentityError("snapshot document must be an object")
    geometry = document.get("geometry")
    if isinstance(geometry, dict) and isinstance(geometry.get("design_snapshot"), dict):
        return geometry["design_snapshot"]
    if isinstance(document.get("design_snapshot"), dict):
        return document["design_snapshot"]
    if isinstance(document.get("design"), dict):
        return document
    raise IdentityError("could not locate a design snapshot")


def normalize_design_snapshot(document: Any) -> dict[str, Any]:
    """Return the exact physical/source payload defined by the v2 schema."""

    snapshot = _extract_snapshot(document)
    try:
        design = DesignConfig.model_validate(snapshot["design"]).root
        enclosure = (
            None
            if design.enclosure is None
            else {
                key: getattr(design.enclosure, key)
                for key in design.enclosure.__class__.model_fields
                if key not in EXCLUDED_ENCLOSURE_KEYS
            }
        )
        selected = {
            "profile": {key: getattr(design, key) for key in PROFILE_KEYS},
            "geometry": {
                "vertical_offset": design.mesh.vertical_offset,
                "wall_thickness": design.mesh.wall_thickness,
                "enclosure": enclosure,
            },
            "source": design.source,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise IdentityError(f"snapshot does not satisfy the design schema: {exc}") from exc
    normalized = _typed(selected)
    if not isinstance(normalized, dict):
        raise AssertionError("normalization must produce an object")
    return normalized


def canonical_bytes(payload: Any) -> bytes:
    try:
        rendered = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IdentityError(f"identity payload is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def identity_sha256(payload: Any) -> str:
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "normalization_schema": NORMALIZATION_SCHEMA,
        "payload": payload,
    }
    return canonical_sha256(envelope)


def leaf_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(leaf_count(child) for child in value.values())
    if isinstance(value, list):
        return sum(leaf_count(child) for child in value)
    return 1


def verify_manifest(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise IdentityError("identity manifest must be an object")
    required = {
        "schema_version",
        "normalization_schema",
        "canonicalization",
        "leaf_count",
        "sha256",
        "payload",
    }
    if set(document) != required:
        raise IdentityError("identity manifest fields do not match schema v1")
    if document["schema_version"] != SCHEMA_VERSION:
        raise IdentityError("unsupported identity manifest schema")
    if document["normalization_schema"] != NORMALIZATION_SCHEMA:
        raise IdentityError("unsupported normalization schema")
    if document["canonicalization"] != CANONICALIZATION:
        raise IdentityError("unsupported canonicalization")
    payload = document["payload"]
    actual_leaves = leaf_count(payload)
    if document["leaf_count"] != actual_leaves:
        raise IdentityError(
            f"leaf count mismatch: expected {document['leaf_count']}, got {actual_leaves}"
        )
    actual_digest = identity_sha256(payload)
    if document["sha256"] != actual_digest:
        raise IdentityError(
            f"SHA-256 mismatch: expected {document['sha256']}, got {actual_digest}"
        )
    return payload


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityError(f"could not read JSON input {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--snapshot",
        action="append",
        default=[],
        type=Path,
        help="optional source snapshot/request to normalize and compare",
    )
    args = parser.parse_args(argv)
    try:
        manifest = _load(args.manifest)
        payload = verify_manifest(manifest)
        for snapshot_path in args.snapshot:
            normalized = normalize_design_snapshot(_load(snapshot_path))
            if canonical_bytes(normalized) != canonical_bytes(payload):
                raise IdentityError(
                    f"normalized snapshot does not match the manifest: {snapshot_path}"
                )
    except IdentityError as exc:
        parser.exit(1, f"identity verification failed: {exc}\n")
    print(
        "verified model identity "
        f"sha256={manifest['sha256']} leaves={manifest['leaf_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
