"""The two isolated CAD tasks, as ordinary functions the parent can call.

:mod:`server.cadlink.isolation` knows how to run *a* child.  This module knows
the two the gate actually names -- inspect and mesh -- and what each one is
allowed to send back.  Callers see plain functions that either return a result
or raise; the process boundary is an implementation detail of how the answer
was obtained, not something an ingest stage has to reason about.

Both entry points are for **external** STEP: bytes that came back from
Onshape, Fusion, or a user's own CAD tool.  WG-generated STEP that has never
crossed an external editor still goes down the in-process export path, which
is unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from server.cadlink.isolation import (
    INSPECT_BUDGET,
    MESH_BUDGET,
    ChildRefusal,
    isolated_step_task,
)
from server.mesh.imported import (
    ImportedMeshDependencyError,
    ImportedMeshError,
    RoleResolutionError,
)


#: Names the mesh child may stage. Anything else in its staging directory is a
#: refusal, so this tuple is the whole of what the boundary will carry out.
MESH_ARTIFACTS = ("mesh.msh", "viewport.msh")
VIEWPORT_ARTIFACTS = ("viewport.msh",)

INSPECT_STAGE = "stage 3 STEP inspection"
MESH_STAGE = "stage 7 meshing"

# This is deliberately context-local and private: ordinary ingest callers have
# no argument, request field, or environment variable that can select it. The
# real-geometry regression uses the context manager below to ask the disposable
# child to install one named fixture before importing the mesher's function.
_MESH_CHILD_FAULT_FIXTURE: ContextVar[str | None] = ContextVar(
    "mesh_child_fault_fixture", default=None
)
_KNOWN_MESH_CHILD_FAULT_FIXTURES = frozenset({"leaking-reduced-domain"})


@contextmanager
def _inject_mesh_child_fault(fixture: str) -> Iterator[None]:
    """Enable one explicit test-only fault in the real mesh child."""

    if fixture not in _KNOWN_MESH_CHILD_FAULT_FIXTURES:
        raise ValueError(f"unknown mesh child fault fixture {fixture!r}")
    token = _MESH_CHILD_FAULT_FIXTURE.set(fixture)
    try:
        yield
    finally:
        _MESH_CHILD_FAULT_FIXTURE.reset(token)


def _reraise_typed(exc: ChildRefusal) -> None:
    """Rebuild the mesher's own exception type on this side of the boundary.

    Ingestion maps refusals to stages by matching markers in the message and
    reads ``area_drift_sources`` off a role-resolution failure to offer the
    override.  A child refusal that arrived as a generic error would quietly
    downgrade both, so the few types that carry meaning are reconstructed and
    everything else stays a :class:`ChildRefusal` -- which is the honest answer
    for a timeout or a native crash, because those are not geometry verdicts.
    """

    if exc.error_type == "ImportedMeshDependencyError":
        raise ImportedMeshDependencyError(exc.detail) from exc
    if exc.error_type == "RoleResolutionError":
        drift = exc.details.get("area_drift_sources")
        raise RoleResolutionError(
            exc.detail,
            area_drift_sources=[str(value) for value in drift] if isinstance(drift, list) else (),
        ) from exc
    if exc.error_type in {"ImportedMeshError", "StepTextError", "StepBudgetExceeded"}:
        raise ImportedMeshError(exc.detail) from exc


def inspect_returned_step(
    step_path: str | Path,
    contract: Mapping[str, Any],
    baseline_fingerprint: Mapping[str, Any] | None = None,
    *,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
) -> dict[str, Any]:
    """Observe a returned STEP in a fresh child and return bounded evidence.

    The child scans the STEP text under the record and label budgets, opens it
    in OCC, and reports the body inventory, bounds, signature hash, and the one
    resolved source face.  It stages no artifacts at all: everything the parent
    needs fits in the structured result, which is exactly why inspection is a
    separate, cheaper, shorter-lived invocation than meshing.
    """

    with isolated_step_task(
        "inspect",
        {
            "contract": dict(contract),
            "baseline_fingerprint": (
                dict(baseline_fingerprint) if baseline_fingerprint is not None else None
            ),
        },
        step_path=step_path,
        budget=INSPECT_BUDGET,
        allowed_artifacts=(),
        stage=INSPECT_STAGE,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
    ) as outcome:
        evidence = outcome.result.get("evidence")
        if not isinstance(evidence, dict):
            raise ChildRefusal(INSPECT_STAGE, "the inspect child returned no evidence")
        return evidence


def build_imported_mesh_isolated(
    assembly_path: str | Path,
    manifest: Mapping[str, Any],
    sizes: Mapping[str, Any],
    *,
    skipped_source_ids: Iterable[str] = (),
    options: Mapping[str, Any] | None = None,
    include_viewport_mesh: bool = True,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
) -> dict[str, Any]:
    """Mesh a returned STEP in a fresh child, signature-compatible with the
    in-process :func:`server.mesh.imported.build_imported_mesh`.

    The mesh text is far larger than a child result is allowed to be, so it
    travels as a staged artifact and is read back here, inside the harness
    context, before staging is destroyed.  The caller gets the same dictionary
    it always got.
    """

    payload = {
        "manifest": dict(manifest),
        "sizes": dict(sizes),
        "skipped_source_ids": sorted(str(value) for value in skipped_source_ids),
        "options": dict(options or {}),
        "include_viewport_mesh": bool(include_viewport_mesh),
    }
    fault_fixture = _MESH_CHILD_FAULT_FIXTURE.get()
    if fault_fixture is not None:
        payload["_test_fault_fixture"] = fault_fixture
    try:
        with isolated_step_task(
            "mesh",
            payload,
            step_path=assembly_path,
            budget=MESH_BUDGET,
            allowed_artifacts=MESH_ARTIFACTS,
            stage=MESH_STAGE,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        ) as outcome:
            result = outcome.result.get("built")
            if not isinstance(result, dict):
                raise ChildRefusal(MESH_STAGE, "the mesh child returned no mesh record")
            mesh_artifact = outcome.artifacts.get("mesh.msh")
            if mesh_artifact is None:
                raise ChildRefusal(MESH_STAGE, "the mesh child staged no solver mesh")
            result["msh_text"] = mesh_artifact.read_text(encoding="utf-8")
            viewport_artifact = outcome.artifacts.get("viewport.msh")
            if viewport_artifact is not None:
                result["viewport_msh_text"] = viewport_artifact.read_text(encoding="utf-8")
            return result
    except ChildRefusal as exc:
        _reraise_typed(exc)
        raise


def build_imported_viewport_mesh_isolated(
    assembly_path: str | Path,
    manifest: Mapping[str, Any],
    recipe: Mapping[str, Any],
    *,
    expected_geometry_hash: str,
    tag_allocation: Mapping[str, Any],
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
) -> dict[str, Any]:
    """Re-tessellate the display mesh in a fresh child.

    Reached only when the solver mesh came from cache but the viewport did not.
    A cached solver mesh is not permission to reopen the STEP in-process, so
    this gets its own invocation and its own budget.
    """

    payload = {
        "manifest": dict(manifest),
        "recipe": dict(recipe),
        "expected_geometry_hash": str(expected_geometry_hash),
        "tag_allocation": dict(tag_allocation),
    }
    try:
        with isolated_step_task(
            "viewport",
            payload,
            step_path=assembly_path,
            budget=MESH_BUDGET,
            allowed_artifacts=VIEWPORT_ARTIFACTS,
            stage=MESH_STAGE,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        ) as outcome:
            viewport = outcome.result.get("viewport")
            artifact = outcome.artifacts.get("viewport.msh")
            if not isinstance(viewport, dict) or artifact is None:
                raise ChildRefusal(MESH_STAGE, "the viewport child staged no display mesh")
            viewport["msh_text"] = artifact.read_text(encoding="utf-8")
            return viewport
    except ChildRefusal as exc:
        _reraise_typed(exc)
        raise


__all__ = [
    "INSPECT_STAGE",
    "MESH_ARTIFACTS",
    "MESH_STAGE",
    "VIEWPORT_ARTIFACTS",
    "build_imported_mesh_isolated",
    "build_imported_viewport_mesh_isolated",
    "inspect_returned_step",
]
