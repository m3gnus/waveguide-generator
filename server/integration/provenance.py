"""Deterministic request identity attached to every public solve result."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Mapping

from server.contracts.canonical import canonical_utf8_strict_bytes as _canonical_json

from server.platform.paths import app_root

if TYPE_CHECKING:
    from server.jobs.models import SolveRequest

from .contracts import PROVENANCE_CONTRACT_VERSION
from .installed import measure_installed_stack


_REPOSITORY_ROOT = app_root()
# The distribution name comes from ``#egg=``: a pin's repository name may differ
# (beat-engine lives in m3gnus/BEAT_Engine; see scripts/gen_requirements.py).
# Packaged apps carry no pins.json, so this fallback is their only source.
_PIN_REQUIREMENT_RE = re.compile(
    r"^git\+https://github\.com/m3gnus/[A-Za-z0-9._-]+\.git@"
    r"(?P<sha>[0-9a-f]{40})#egg=(?P<name>[A-Za-z0-9._-]+)$"
)


def canonical_json_sha256(value: Any) -> str:
    """Hash one JSON value using the public request-identity encoding."""

    return hashlib.sha256(_canonical_json(value)).hexdigest()


@lru_cache(maxsize=1)
def _release_identity() -> tuple[str, dict[str, str]]:
    version = json.loads(
        (_REPOSITORY_ROOT / "shared" / "version.json").read_text(encoding="utf-8")
    )["version"]
    pins_path = _REPOSITORY_ROOT / "pins.json"
    if pins_path.is_file():
        pins = json.loads(pins_path.read_text(encoding="utf-8"))["modules"]
        dependency_shas = {
            str(name): str(entry["sha"]) for name, entry in sorted(pins.items())
        }
    else:
        dependency_shas = {}
        requirements = _REPOSITORY_ROOT / "server" / "requirements-pins.txt"
        for line in requirements.read_text(encoding="utf-8").splitlines():
            match = _PIN_REQUIREMENT_RE.fullmatch(line.strip())
            if match is not None:
                dependency_shas[match.group("name")] = match.group("sha")
    return str(version), dependency_shas


def pinned_dependency_shas() -> dict[str, str]:
    """The SHAs this checkout declares it should run, from ``pins.json``.

    This is a declaration, not a measurement. Pair it with
    :func:`server.integration.installed.measure_installed_stack` before
    treating it as a description of the environment.
    """

    return dict(_release_identity()[1])


def _text(*values: Any) -> str | None:
    return next((value for value in values if isinstance(value, str) and value), None)


def _beat_fingerprints(results: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[dict[str, str | None]]:
    """Reuse the engine diagnostics carried by BEAT's existing result logs."""
    sources = [metadata]
    channels = results.get("channels")
    if isinstance(channels, Mapping):
        sources.extend(item.get("metadata", {}) for item in channels.values() if isinstance(item, Mapping))
    fingerprints: list[dict[str, str | None]] = []
    for entry in sources:
        if not isinstance(entry, Mapping):
            continue
        beat = entry.get("beat")
        if not isinstance(beat, Mapping) or not isinstance(beat.get("solver_log"), list):
            continue
        for line in beat["solver_log"]:
            diag = line.get("native_diagnostics") if isinstance(line, Mapping) else None
            run = diag.get("engine_provenance") if isinstance(diag, Mapping) else None
            if not isinstance(run, Mapping):
                continue
            engine, runtime, execution = (run.get(key) for key in ("engine", "runtime", "execution"))
            engine = engine if isinstance(engine, Mapping) else {}
            runtime = runtime if isinstance(runtime, Mapping) else {}
            execution = execution if isinstance(execution, Mapping) else {}
            fingerprint = {
                "package": "beat-engine", "version": _text(engine.get("version")),
                "commit": _text(engine.get("repository_revision")),
                "source_sha256": _text(engine.get("source_sha256")),
                "build_sha256": _text(runtime.get("sysimage_sha256")),
                "backend": _text(execution.get("backend")),
            }
            if fingerprint not in fingerprints:
                fingerprints.append(fingerprint)
    return fingerprints


def enrich_result_contract(
    results: Mapping[str, Any],
    request: "SolveRequest",
    *,
    effective_request: "SolveRequest | None" = None,
    cad_identity: Mapping[str, Any] | None = None,
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
    installed_dependency_shas, dependency_drift = measure_installed_stack(
        dependency_shas
    )

    enriched["result_kind"] = result_kind
    enriched["result_contract_version"] = result_version
    enriched["client_request_id"] = request.client_request_id
    enriched["client_metadata"] = request.client_metadata
    metadata = dict(enriched.get("metadata") or {})
    fingerprints = metadata.pop("_solver_fingerprints", None)
    if not isinstance(fingerprints, list) or not fingerprints:
        fingerprints = _beat_fingerprints(enriched, metadata)
    if not fingerprints:
        # An older adapter may not expose its executing source/build. Do not
        # replace the missing observation with a declared or installed pin.
        solver = metadata.get("solver_engine")
        solver = solver if isinstance(solver, Mapping) else {}
        fingerprints = [{
            "package": _text(solver.get("package"), metadata.get("engine")),
            "version": _text(solver.get("package_version")),
            "commit": None, "source_sha256": None, "build_sha256": None,
            "backend": _text(solver.get("device"), metadata.get("solver_backend"), request.options.engine),
        }]
    provenance = {
        "schema_version": PROVENANCE_CONTRACT_VERSION,
        "wg_version": wg_version,
        "dependency_shas": dependency_shas,
        # ``dependency_shas`` is what the checkout declares. These two are what
        # ran: the commit pip actually installed for each pinned module (null
        # where it could not be measured), and the sorted names where the two
        # disagree. An empty ``dependency_drift`` is the only positive claim
        # that this result came off the pinned stack; a consumer reading a
        # result that predates the measurement sees the field absent, not empty.
        "installed_dependency_shas": installed_dependency_shas,
        "dependency_drift": dependency_drift,
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
        "solver_fingerprints": deepcopy(fingerprints),
    }
    if cad_identity is not None:
        provenance["cad_identity"] = deepcopy(dict(cad_identity))
    enriched["provenance"] = provenance
    metadata.setdefault("result_contract_version", result_version)
    if cad_identity is not None:
        metadata["cad_identity"] = deepcopy(dict(cad_identity))
        channels = enriched.get("channels")
        if isinstance(channels, Mapping):
            copied_channels: dict[str, Any] = {}
            for channel_id, raw_channel in channels.items():
                if not isinstance(raw_channel, Mapping):
                    copied_channels[str(channel_id)] = raw_channel
                    continue
                channel = dict(raw_channel)
                channel_metadata = dict(channel.get("metadata") or {})
                channel_metadata["cad_identity"] = deepcopy(dict(cad_identity))
                channel["metadata"] = channel_metadata
                copied_channels[str(channel_id)] = channel
            enriched["channels"] = copied_channels
    enriched["metadata"] = metadata
    return enriched


__all__ = [
    "canonical_json_sha256",
    "enrich_result_contract",
    "pinned_dependency_shas",
]
