"""Cursor-based read model for Send arrivals; does not drive operations."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import threading
from typing import Any

from fastapi import APIRouter, Query, Request

from server.cadlink.intake import returns_folder

from .receipts import receipt_page, with_source_outcome

_SOURCE_LOCK = threading.RLock()


router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


@router.get("/receipts")
async def list_receipts(request: Request, after: int = Query(0, ge=0),
                        limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        state = request.app.state
        page = receipt_page(state.cadlink_store, after=after, limit=limit)
        if not any(item["outcome"] == "accepted" for item in page["receipts"]):
            return page
        root = returns_folder.selected_workspace_root(state)
        try:
            sources = {}
            wanted = {item["bundle_path"] for item in page["receipts"] if item["outcome"] == "accepted"}
            if root:
                # Retain the inventory across cursor pages; only this page's
                # sources need fresh stat evidence. Hashes follow manifest
                # path/size/mtime, never a cursor or request id.
                with _SOURCE_LOCK:
                    directory = root / "wgreturn"
                    signature = (str(root.resolve()), directory.stat().st_mtime_ns) if directory.exists() else (str(root.resolve()), None)
                    cached = getattr(state, "cad_receipt_sources", None)
                    paths = {bundle: root / bundle / "wgreturn.json" for bundle in wanted if isinstance(bundle, str)}
                    def revision(path: Path) -> tuple[str, int, int] | None:
                        try:
                            info = path.stat()
                            return (str(path.resolve()), info.st_size, info.st_mtime_ns)
                        except OSError:
                            return None
                    revisions = {bundle: revision(path) for bundle, path in paths.items()}
                    if cached is None or cached["directory"] != signature or any(
                        cached["revisions"].get(bundle) != rev for bundle, rev in revisions.items()
                    ):
                        old_hashes = cached["hashes"] if cached else {}
                        inventory = returns_folder.return_inventory(root)
                        cached = {"directory": signature, "items": {}, "revisions": {}, "hashes": {}}
                        for item, _manifest, path in inventory:
                            bundle = item["bundlePath"]
                            cached["items"][bundle] = dict(item)
                            cached["revisions"][bundle] = revision(path / "wgreturn.json")
                        active = set(cached["revisions"].values())
                        cached["hashes"] = {key: value for key, value in old_hashes.items() if key in active}
                        state.cad_receipt_sources = cached
                    for bundle, path in paths.items():
                        item = cached["items"].get(bundle)
                        if item is None:
                            continue
                        source = dict(item)
                        rev = revisions[bundle]
                        try:
                            if rev not in cached["hashes"]:
                                digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                                if revision(path) != rev:
                                    source.update(readable=False, reason="source_changed")
                                    sources[bundle] = source
                                    continue
                                cached["hashes"][rev] = digest
                            source["manifest_sha256"] = cached["hashes"][rev]
                        except OSError:
                            source.update(readable=False, reason="source_unreadable")
                        sources[bundle] = source
        except OSError:
            sources = {item["bundle_path"]: {"readable": False, "reason": "folder_unreadable"}
                       for item in page["receipts"]}
        page["receipts"] = [with_source_outcome(item, configured=root is not None,
                            source=sources.get(item["bundle_path"])) for item in page["receipts"]]
        return page

    return await asyncio.to_thread(read)
