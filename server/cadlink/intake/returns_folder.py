"""Inventory and selection of the workspace's received CAD return folders."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
from typing import Any, Mapping

from server.contracts.canonical import canonical_ascii_strict_json
from server.workspace.api import _strictly_inside

from server.cadlink.roles import canonical_source_role
from server.cadlink.wgreturn import declared_domain_planes


_RETURN_INVENTORY_CACHE: dict[
    tuple[str, int, int], tuple[dict[str, Any], Mapping[str, Any] | None]
] = {}
_RETURN_INVENTORY_CACHE_LOCK = threading.Lock()


def parse_return_manifest(path: Path) -> Mapping[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, Mapping):
        raise ValueError("manifest inventory has the wrong shape")
    return parsed



def return_inventory(
    workspace_root: Path,
) -> list[tuple[dict[str, Any], Mapping[str, Any] | None, Path]]:
    returns_root = workspace_root / "wgreturn"
    if not returns_root.is_dir():
        return []
    items: list[tuple[dict[str, Any], Mapping[str, Any] | None, Path]] = []
    active_cache_keys: set[tuple[str, int, int]] = set()
    for candidate in sorted(returns_root.glob("*.wgreturn"), key=lambda item: item.name.casefold()):
        try:
            resolved = candidate.resolve()
            _strictly_inside(resolved, workspace_root, "bundlePath")
            if not resolved.is_dir():
                continue
            candidate_stat = candidate.stat()
            modified_at = datetime.fromtimestamp(
                candidate_stat.st_mtime, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z")
        except (OSError, ValueError):
            continue
        item: dict[str, Any] = {
            "name": candidate.name,
            "bundlePath": f"wgreturn/{candidate.name}",
            "modifiedAt": modified_at,
            "readable": False,
            "documentName": None,
            "documentNativeId": None,
            "declaredCutPlanes": [],
            "requestId": None,
            "sourceCount": None,
            "instanceCount": None,
            "designIds": [],
            "solverAnchorInstanceId": None,
            "instances": [],
            "sources": [],
        }
        manifest: Mapping[str, Any] | None = None
        cache_key: tuple[str, int, int] | None = None
        try:
            manifest_path = resolved / "wgreturn.json"
            manifest_stat = manifest_path.stat()
            cache_key = (
                str(manifest_path),
                manifest_stat.st_mtime_ns,
                manifest_stat.st_size,
            )
            active_cache_keys.add(cache_key)
            with _RETURN_INVENTORY_CACHE_LOCK:
                cached = _RETURN_INVENTORY_CACHE.get(cache_key)
            if cached is not None:
                cached_item = dict(cached[0])
                cached_item["modifiedAt"] = modified_at
                items.append((cached_item, cached[1], resolved))
                continue
            manifest = parse_return_manifest(manifest_path)
            document = manifest.get("document")
            sources = manifest.get("sources")
            instances = manifest.get("instances")
            coordinates = manifest.get("coordinate_system")
            scope = manifest.get("scope")
            if (
                not isinstance(document, dict)
                or not isinstance(sources, list)
                or not isinstance(instances, list)
            ):
                raise ValueError("manifest inventory has the wrong shape")
            coordinates = coordinates if isinstance(coordinates, dict) else {}
            scope = scope if isinstance(scope, dict) else {}
            included = scope.get("included")
            included = included if isinstance(included, list) else []
            source_summaries = []
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError("manifest source has the wrong shape")
                raw_suggested_resolution = source.get("suggested_resolution_mm")
                suggested_resolution = (
                    None
                    if raw_suggested_resolution is None
                    else float(raw_suggested_resolution)
                )
                if suggested_resolution is not None and (
                    not math.isfinite(suggested_resolution)
                    or suggested_resolution <= 0
                ):
                    raise ValueError("manifest source suggestion is not finite and positive")
                source_summaries.append(
                    {
                        "id": str(source["id"]),
                        "role": canonical_source_role(
                            str(source.get("role") or "source")
                        ),
                        "instanceId": (
                            str(source["instance_id"])
                            if source.get("instance_id")
                            else None
                        ),
                        "required": bool(source.get("required", True)),
                        "suggestedResolutionMm": suggested_resolution,
                        "defaultDriveChannelId": str(
                            source["default_drive_channel_id"]
                        ),
                    }
                )
            instance_summaries = []
            for instance in instances:
                if not isinstance(instance, dict):
                    raise ValueError("manifest instance has the wrong shape")
                instance_id = str(instance["instance_id"])
                body_evidence = instance.get("body_evidence")
                observed = (
                    body_evidence.get("observed_fingerprint")
                    if isinstance(body_evidence, Mapping)
                    else None
                )
                matrix = instance.get("assembly_from_link")
                source_records = [
                    source
                    for source in source_summaries
                    if source["instanceId"] == instance_id
                ]
                body_object_ids = sorted(
                    str(body["object_id"])
                    for body in included
                    if isinstance(body, dict)
                    and body.get("wglink_instance_id") == instance_id
                    and body.get("object_id")
                )
                instance_summaries.append(
                    {
                        "instanceId": instance_id,
                        "designId": str(instance.get("design_id") or "") or None,
                        "occurrencePath": (
                            str(instance["occurrence_path"])
                            if instance.get("occurrence_path")
                            else None
                        ),
                        "bodyObjectIds": body_object_ids,
                        "bodyFingerprintHash": (
                            "sha256:"
                            + hashlib.sha256(
                                canonical_ascii_strict_json(observed).encode("utf-8")
                            ).hexdigest()
                            if isinstance(observed, Mapping)
                            else None
                        ),
                        "transformHash": (
                            "sha256:"
                            + hashlib.sha256(
                                canonical_ascii_strict_json(matrix).encode("utf-8")
                            ).hexdigest()
                            if isinstance(matrix, list)
                            else None
                        ),
                        "sourceIds": sorted(
                            str(source["id"]) for source in source_records
                        ),
                        "driveChannelIds": sorted(
                            {str(source["defaultDriveChannelId"]) for source in source_records}
                        ),
                    }
                )
            item.update(
                {
                    "readable": True,
                    "documentName": str(document.get("name") or candidate.stem),
                    # The CAD document this is a return of, when the adapter
                    # recorded it: a newer return of the same document is the
                    # only one that may supersede a solve request made for it.
                    "documentNativeId": (
                        str(document.get("native_id"))
                        if document.get("native_id")
                        else None
                    ),
                    "requestId": (
                        str(document.get("request_id"))
                        if document.get("request_id")
                        else None
                    ),
                    "sourceCount": len(sources),
                    "instanceCount": len(instances),
                    # The domain the CAD author declared, so the panel can say
                    # which model is about to be solved before anyone presses
                    # anything. Empty for a full model, which is every bundle
                    # written before the declaration existed.
                    "declaredCutPlanes": list(declared_domain_planes(manifest)),
                    "solverAnchorInstanceId": (
                        str(coordinates["solver_anchor_instance_id"])
                        if coordinates.get("solver_anchor_instance_id")
                        else None
                    ),
                    "designIds": sorted({
                        str(instance["design_id"])
                        for instance in instances
                        if isinstance(instance, dict) and instance.get("design_id")
                    }),
                    "instances": sorted(
                        instance_summaries, key=lambda value: value["instanceId"]
                    ),
                    "sources": source_summaries,
                }
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            item["reason"] = str(exc) or "Manifest is unreadable"
        if cache_key is not None:
            with _RETURN_INVENTORY_CACHE_LOCK:
                _RETURN_INVENTORY_CACHE[cache_key] = (dict(item), manifest)
        items.append((item, manifest, resolved))
    root_prefix = str(returns_root.resolve()) + "/"
    with _RETURN_INVENTORY_CACHE_LOCK:
        stale = [
            key
            for key in _RETURN_INVENTORY_CACHE
            if key[0].startswith(root_prefix) and key not in active_cache_keys
        ]
        for key in stale:
            del _RETURN_INVENTORY_CACHE[key]
    return sorted(items, key=lambda entry: entry[0]["modifiedAt"], reverse=True)



def selected_workspace_root(state: Any) -> Path | None:
    workspace = getattr(state, "cad_workspace", None)
    selected = workspace.selected_path() if workspace is not None else None
    return selected.resolve() if selected is not None else None



__all__ = ["parse_return_manifest", "return_inventory", "selected_workspace_root"]
