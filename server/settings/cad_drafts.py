"""CAD draft journal, mirrored by frontend/src/stores/cadDraftJournal.ts."""

from __future__ import annotations

import json
import math
import re
from typing import Any

NAMESPACE = "cadSetupDrafts_v2"
MAX_ENTRIES = 20
MAX_MARKERS = 64
MAX_ENTRY_BYTES = 64 * 1024
MAX_NAMESPACE_BYTES = 512 * 1024


def draft_hash(value: str) -> str:
    result = 0xCBF29CE484222325
    for byte in value.encode("utf-8"):
        result = ((result ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{result:016x}"


def parse(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    entries = {}
    for key, entry in value.items():
        if not re.fullmatch(r"[a-f0-9]{16}_[a-f0-9]{16}", key) or not isinstance(entry, dict):
            continue
        timestamp = entry.get("updatedAt")
        if ("draft" not in entry or (entry["draft"] is not None and not isinstance(entry["draft"], dict))
                or "baseRevisionId" not in entry
                or (entry["baseRevisionId"] is not None and not isinstance(entry["baseRevisionId"], str))
                or isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp) or timestamp < 0):
            continue
        if len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= MAX_ENTRY_BYTES:
            entries[key] = entry
    return entries


def merge(*journals: Any) -> str:
    entries: dict[str, Any] = {}
    for raw in journals:
        for key, entry in parse(raw).items():
            if key not in entries or entry["updatedAt"] >= entries[key]["updatedAt"]:
                entries[key] = entry
    newest = sorted(entries, key=lambda key: (-entries[key]["updatedAt"], key))
    live = [key for key in newest if entries[key]["draft"] is not None][:MAX_ENTRIES]
    markers = [key for key in newest if entries[key]["draft"] is None][:MAX_MARKERS]
    retained = set(live + markers)
    while True:
        result = json.dumps({key: entries[key] for key in newest if key in retained},
                            ensure_ascii=False, separators=(",", ":"))
        # The namespace is itself a JSON string in the settings envelope.
        if len(json.dumps(result).encode("utf-8")) <= MAX_NAMESPACE_BYTES:
            return result
        # Preserve clears while trimming drafts; oversized clears are bounded too.
        retained.remove((live if live else markers).pop())


def migrate(namespaces: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Retire both old journal forms once, before namespace validation drops long names."""
    sources = [name for name in namespaces if name == "cadSetupDrafts" or name.startswith("cadSetupDraft_")]
    if not sources:
        return namespaces, []
    additions = {}
    for name in sources:
        try:
            old = json.loads(namespaces[name])
        except (TypeError, ValueError):
            continue
        if not isinstance(old, dict):
            continue
        for key, value in old.items():
            try:
                model, inventory = json.loads(key)
                if not isinstance(model, str) or not isinstance(inventory, str) or not isinstance(value, dict):
                    continue
                draft = {k: v for k, v in value.items() if k not in {"baseRevisionId", "updatedAt"}}
                if "baseRevisionId" not in value:
                    draft["legacy"] = True
                entry = {"draft": draft, "baseRevisionId": value.get("baseRevisionId"),
                         "updatedAt": value.get("updatedAt", 0)}
                short = f"{draft_hash(model)}_{draft_hash(inventory)}"
                additions = parse(merge(additions, {short: entry}))
            except (TypeError, ValueError):
                continue
    result = {name: value for name, value in namespaces.items() if name not in sources}
    result[NAMESPACE] = merge(additions, namespaces.get(NAMESPACE))
    return result, sources
