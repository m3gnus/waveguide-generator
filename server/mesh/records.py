"""The immutable prepared-mesh boundary shared by file and CAD producers.

Storage keeps its existing ingestion table and IDs. This reader has no CAD
document, exchange-folder or connection requirement.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class MeshRecordStore(Protocol):
    def get_ingest(self, ingest_id: str) -> dict[str, Any] | None: ...


def get_prepared_mesh(store: MeshRecordStore, ingest_id: str) -> dict[str, Any] | None:
    row = store.get_ingest(ingest_id)
    if row is None:
        return None
    record = json.loads(str(row["record_json"]))
    if not isinstance(record, dict):
        raise ValueError("The retained mesh record is unreadable. Import the mesh again.")
    return record
