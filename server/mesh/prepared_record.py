"""The one finalization of an immutable prepared-mesh record.

Three producers write prepared meshes the job runtime solves: an imported
file, a CAD return (``cad``, and its ``native`` source-contour variant) and a
design's compiled assembly. Each composes its own record -- evidence,
provenance and atomicity stay with the producer -- and then seals it here: the
shared invariants every reader (``server/mesh/records.py``, the job runtime,
collection) relies on are checked once, and the record is encoded in its
producer's established, identity-bearing form. The forms are not unified: a
file record has always been plain JSON, a CAD record a canonical encoding that
carries its own ``solve_model_sha256`` and ``report_sha256``, so retained
records and their identities stay byte-for-byte what they were.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import re
from typing import Any, Literal

from server.contracts.canonical import canonical_ascii_strict_bytes, canonical_normalized_bytes

#: Producers whose record names an already finished solver mesh ("ready"):
#: no CAD frame or preparation lane, no mesh sizes in the request, and their
#: own folder under ``imports/files`` for collection.
READY_MESH_PRODUCERS = frozenset({"mesh-file", "design-assembly"})

Seal = Literal["mesh-file", "cad", "native", "design-assembly"]

_INGEST_ID = re.compile(r"^wgi_[0-9A-Za-z]+$")
_DIGEST = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
_REQUIRED = (
    "ingest_id", "created_at", "manifest_sha256", "artifact_sha256", "mesh_store_path",
    "mesh_content_sha256", "acoustic_domain", "sources", "source_tags", "symmetry",
    "polar_grid_derivation", "normalisation", "findings", "skipped_source_ids", "mesh",
)


def is_ready_mesh(record: Mapping[str, Any]) -> bool:
    """Whether a record names a finished mesh rather than a CAD preparation."""

    return record.get("producer") in READY_MESH_PRODUCERS


def solve_model_sha256(record: Mapping[str, Any]) -> str:
    """Identity of prepared geometry and its effective solver interpretation."""

    normalisation = record["normalisation"]
    solve_model = {
        "version": 1,
        "mesh_content_sha256": record["mesh_content_sha256"],
        "solver_frame_matrix": normalisation.get("matrix"),
        "solver_frame": normalisation.get("solver_frame"),
        "symmetry": record["symmetry"],
        "domain_interpretation": record["domain_interpretation"],
        "source_tags": record["source_tags"],
        "tag_map": record["tag_map"],
        "identity": record["identity"],
    }
    if record.get("native_source") is not None:
        solve_model["native_source"] = record["native_source"]
    if record.get("fem_regions"):
        solve_model["fem_regions"] = [
            {key: region[key] for key in (
                "region_id", "mesh_content_sha256", "medium", "surface_groups",
            )}
            for region in record["fem_regions"]
        ]
    if record.get("coupled_exterior"):
        solve_model["coupled_exterior_sha256"] = record["coupled_exterior"]["mesh_content_sha256"]
    return "sha256:" + hashlib.sha256(canonical_normalized_bytes(solve_model)).hexdigest()


def check_prepared_record(record: Mapping[str, Any]) -> None:
    """The invariants every prepared-mesh reader relies on, whoever wrote the record."""

    missing = [key for key in _REQUIRED if key not in record]
    if missing:
        raise ValueError("prepared-mesh record is incomplete: " + ", ".join(missing))
    if not isinstance(record["ingest_id"], str) or _INGEST_ID.fullmatch(record["ingest_id"]) is None:
        raise ValueError("prepared-mesh record needs a wgi_ ingest id")
    for key in ("manifest_sha256", "artifact_sha256", "mesh_content_sha256"):
        if not isinstance(record[key], str) or _DIGEST.fullmatch(record[key]) is None:
            raise ValueError(f"prepared-mesh record {key} must be a sha256 digest")
    sources = record["sources"]
    if not isinstance(sources, list) or any(
        not isinstance(source, Mapping) or not isinstance(source.get("id"), str) or not source["id"]
        for source in sources
    ):
        raise ValueError("prepared-mesh record sources need nonempty string ids")
    ids = [source["id"] for source in sources]
    if len(set(ids)) != len(ids):
        raise ValueError("prepared-mesh record source ids must be unique")
    tags = record["source_tags"]
    if not isinstance(tags, Mapping) or set(tags) - set(ids):
        raise ValueError("prepared-mesh record source_tags must name recorded sources")
    if any(isinstance(tag, bool) or not isinstance(tag, int) or tag <= 0 for tag in tags.values()):
        raise ValueError("prepared-mesh record source tags must be positive integers")
    if not set(record["skipped_source_ids"]) <= set(ids):
        raise ValueError("prepared-mesh record skips unknown sources")
    if not isinstance(record["mesh"], Mapping) or not isinstance(record["polar_grid_derivation"], Mapping):
        raise ValueError("prepared-mesh record mesh and polar grid derivation must be objects")


def _sealed_canonical(record: dict[str, Any], encode: Callable[[Any], bytes]) -> str:
    record["solve_model_sha256"] = solve_model_sha256(record)
    record["report_sha256"] = "sha256:" + hashlib.sha256(encode(record)).hexdigest()
    return encode(record).decode("utf-8")


def seal_prepared_record(record: dict[str, Any], *, producer: Seal) -> str:
    """Check a composed record and return the exact text its row stores.

    ``cad``, ``native`` and ``design-assembly`` records gain their
    ``solve_model_sha256`` and ``report_sha256`` (mutating ``record``, as their
    producers always did); a file record is stored as plain JSON.
    """

    check_prepared_record(record)
    if producer == "mesh-file":
        return json.dumps(record, allow_nan=False)
    if producer == "cad":
        return _sealed_canonical(record, canonical_normalized_bytes)
    if producer in {"native", "design-assembly"}:
        return _sealed_canonical(record, canonical_ascii_strict_bytes)
    raise ValueError(f"unknown prepared-mesh producer {producer!r}")


__all__ = [
    "READY_MESH_PRODUCERS",
    "check_prepared_record",
    "is_ready_mesh",
    "seal_prepared_record",
    "solve_model_sha256",
]
