"""Cursor-based read model for Send arrivals; does not drive operations."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi import APIRouter, Query, Request

from .receipts import receipt_page, with_source_outcome

router = APIRouter(prefix="/api/cadlink", tags=["cadlink"])


@router.get("/receipts")
async def list_receipts(request: Request, after: int = Query(0, ge=0),
                        limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    # Lazy import: api mounts this router. Share the existing folder reader.
    from .api import _return_inventory, _selected_workspace_root

    def read() -> dict[str, Any]:
        state = request.app.state
        page = receipt_page(state.cadlink_store, after=after, limit=limit)
        if not any(item["outcome"] == "accepted" for item in page["receipts"]):
            return page
        root = _selected_workspace_root(state)
        try:
            sources = {}
            wanted = {item["bundle_path"] for item in page["receipts"]}
            for item, _manifest, path in _return_inventory(root) if root else []:
                if item["bundlePath"] not in wanted:
                    continue
                source = dict(item)
                try:
                    source["manifest_sha256"] = "sha256:" + hashlib.sha256(
                        (path / "wgreturn.json").read_bytes()).hexdigest()
                except OSError:
                    source.update(readable=False, reason="source_unreadable")
                sources[item["bundlePath"]] = source
        except OSError:
            sources = {item["bundle_path"]: {"readable": False, "reason": "folder_unreadable"}
                       for item in page["receipts"]}
        page["receipts"] = [with_source_outcome(item, configured=root is not None,
                            source=sources.get(item["bundle_path"])) for item in page["receipts"]]
        return page

    return await asyncio.to_thread(read)
