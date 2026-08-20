"""Deterministic request identity attached to every public solve result."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from server.jobs.models import SolveRequest

from .contracts import PROVENANCE_CONTRACT_VERSION


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    """Hash one JSON value using the public request-identity encoding."""

    return hashlib.sha256(_canonical_json(value)).hexdigest()


@lru_cache(maxsize=1)
def _release_identity() -> tuple[str, dict[str, str]]:
    version = json.loads(
        (_REPOSITORY_ROOT / "shared" / "version.json").read_text(encoding="utf-8")
    )["version"]
    pins = json.loads(
        (_REPOSITORY_ROOT / "pins.json").read_text(encoding="utf-8")
    )["modules"]
    dependency_shas = {
        str(name): str(entry["sha"])
        for name, entry in sorted(pins.items())
    }
    return str(version), dependency_shas


def enrich_result_contract(
    results: Mapping[str, Any],
    request: "SolveRequest",
    *,
    effective_request: "SolveRequest | None" = None,
) -> dict[str, Any]:
    """Add only backward-compatible top-level integration fields.

    ``request`` is the execution-shaped request passed to the solver. Jobs may
    additionally provide the normalized request they durably persisted before
    symmetry resolution rewrote the execution mesh domain.
    """

    enriched = dict(results)
    effective_request = effective_request or request
    parametric = request.geometry.type == "parametric"
    result_kind = "parametric" if parametric else "multi_channel"
    result_version = 1 if parametric else 2
    request_wire = request.model_dump(mode="json")
    geometry_wire = request.geometry.model_dump(mode="json")
    options_wire = request.options.model_dump(mode="json")
    request_sha256 = canonical_json_sha256(request_wire)
    geometry_sha256 = canonical_json_sha256(geometry_wire)
    solve_options_sha256 = canonical_json_sha256(options_wire)
    effective_request_sha256 = canonical_json_sha256(
        effective_request.model_dump(mode="json")
    )
    effective_geometry_sha256 = canonical_json_sha256(
        effective_request.geometry.model_dump(mode="json")
    )
    effective_solve_options_sha256 = canonical_json_sha256(
        effective_request.options.model_dump(mode="json")
    )
    wg_version, dependency_shas = _release_identity()

    enriched["result_kind"] = result_kind
    enriched["result_contract_version"] = result_version
    enriched["client_request_id"] = request.client_request_id
    enriched["client_metadata"] = request.client_metadata
    enriched["provenance"] = {
        "schema_version": PROVENANCE_CONTRACT_VERSION,
        "wg_version": wg_version,
        "dependency_shas": dependency_shas,
        # The original v1 names hash the execution-shaped request, including
        # the symmetry-resolved mesh domain. Keep those aliases stable while
        # naming both that identity and the normalized, durably stored request.
        "request_identity": "execution",
        "execution_request_sha256": request_sha256,
        "execution_geometry_sha256": geometry_sha256,
        "execution_solve_options_sha256": solve_options_sha256,
        "effective_request_sha256": effective_request_sha256,
        "effective_geometry_sha256": effective_geometry_sha256,
        "effective_solve_options_sha256": effective_solve_options_sha256,
        "request_sha256": request_sha256,
        "geometry_sha256": geometry_sha256,
        "solve_options_sha256": solve_options_sha256,
        "resolved_engine": request.options.engine,
    }
    metadata = dict(enriched.get("metadata") or {})
    metadata.setdefault("result_contract_version", result_version)
    enriched["metadata"] = metadata
    return enriched


__all__ = ["canonical_json_sha256", "enrich_result_contract"]
