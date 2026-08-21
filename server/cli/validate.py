"""Validate one text design against the local solve stack without persisting it."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
import sys
from typing import Any

from pydantic import ValidationError

from server.design.schema import Expr
from server.design.solve_block import has_solve_blocks
from server.design.textcfg import TextConfigError, parse
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.models import ImportedGeometrySource, SolveRequest
from server.jobs.runtime import (
    EngineUnavailableError,
    ImportedSolveRefusal,
    SymmetryValidationError,
    UnknownEngineError,
    resolve_submission,
)
from server.mesh.builder import build_solver_mesh
from server.mesh.gmsh_worker import prewarm_gmsh_worker, shutdown_gmsh_worker
from server.solver.context import SolverContext

from .render import render_validation
from .request import build_request, load_request_document


MeshBuilder = Callable[..., Awaitable[dict[str, Any]]]
WorkerLifecycle = Callable[[], Awaitable[None]]


def _base_payload(
    path: Path,
    requested_engine: str,
    *,
    request_document: bool,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "inputKind": "solve_request" if request_document else "design_text",
        "file": str(path),
        "dialect": None,
        "migrationsApplied": [],
        "settingsSource": "defaults",
        "clientRequestId": None,
        "frequencies": None,
        "engine": {
            "requested": requested_engine,
            "resolved": None,
            "available": False,
            "reason": None,
        },
        "symmetry": None,
        "mesh": None,
        "refusals": [],
        "errors": [],
    }


def _refuse(
    payload: dict[str, Any],
    message: str,
    *,
    code: str = "invalid_request",
    stage: str = "validation",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> None:
    payload["refusals"].append(message)
    payload["errors"].append(
        {
            "schema_version": 1,
            "code": code,
            "stage": stage,
            "message": message,
            "retryable": retryable,
            "details": details or {},
            "client_request_id": payload["clientRequestId"],
        }
    )


def _validation_messages(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        prefix = f"{location}: " if location else ""
        messages.append(prefix + str(error["msg"]))
    return messages


def _frequency_summary(request: SolveRequest) -> dict[str, Any]:
    solver_mode = request.options.solver_mode or "auto"
    context = SolverContext.from_request(request, solver_mode=solver_mode)
    simulation = request.design.root.simulation
    if request.options.frequencies_hz is not None:
        source = "flags"
        spacing = "explicit"
    elif (
        request.options.frequency_range is not None
        or request.options.num_frequencies is not None
    ):
        source = "flags"
        spacing = request.options.frequency_spacing
    elif any(
        value is not None
        for value in (simulation.f1, simulation.f2, simulation.num_frequencies)
    ):
        source = "design"
        spacing = request.options.frequency_spacing
    else:
        source = "defaults"
        spacing = request.options.frequency_spacing
    return {
        "start": context.frequency_range[0],
        "end": context.frequency_range[1],
        "count": context.num_frequencies,
        "spacing": spacing,
        "source": source,
    }


def _symmetry_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    quadrants = int(metadata["resolved_quadrants"])
    resolved_mode = {
        1: "quarter",
        12: "half_xz",
        14: "half_yz",
        1234: "full",
    }.get(quadrants, str(quadrants))
    automatic = dict(metadata["auto_resolution"])
    return {
        "requested": metadata["requested"],
        "resolved": resolved_mode,
        "resolvedQuadrants": quadrants,
        "designQuadrants": metadata["design_quadrants"],
        "xz": bool(automatic["xz"]),
        "yz": bool(automatic["yz"]),
        "reasons": automatic["reasons"],
        "toleranceMm": automatic["tolerance_mm"],
    }


def _solve_path_summary(symmetry_metadata: dict[str, Any]) -> dict[str, Any]:
    """Render the formulation decision already made by the solve planner."""

    plan = symmetry_metadata.get("solver_plan")
    plan = plan if isinstance(plan, dict) else {}
    reasons = [str(reason) for reason in plan.get("eligibility_reasons") or []]
    return {
        "predicted": (
            "axisymmetric-meridian"
            if plan.get("formulation") == "axisymmetric"
            else "full-3d"
        ),
        "reasons": reasons,
    }


def _engine_reason(
    capabilities: tuple[EngineInfo, ...], resolved_name: str | None
) -> str | None:
    if resolved_name is None:
        return None
    item = next((item for item in capabilities if item.name == resolved_name), None)
    return item.reason if item is not None else None


async def validate_path(
    path: Path,
    *,
    engine_registry: EngineRegistry,
    engine: str | None = None,
    overlay: Path | None = None,
    no_mesh: bool = False,
    request_document: bool = False,
    mesh_builder: MeshBuilder | None = None,
    prewarm_worker: WorkerLifecycle | None = None,
    shutdown_worker: WorkerLifecycle | None = None,
) -> tuple[dict[str, Any], int]:
    requested_engine = str(engine or "auto").strip().lower()
    payload = _base_payload(
        path,
        requested_engine,
        request_document=request_document,
    )
    refusals: list[str] = payload["refusals"]

    try:
        if request_document:
            payload["dialect"] = "solve-request-json"
            built = load_request_document(path, overlay=overlay, engine=engine)
        else:
            if path.suffix.lower() not in {".mwg", ".cfg"}:
                _refuse(
                    payload,
                    "design file must use the .mwg or .cfg extension",
                    code="unsupported_input",
                    stage="input",
                )
                return payload, 1
            try:
                source = path.read_text(encoding="utf-8")
            except OSError as exc:
                _refuse(
                    payload,
                    f"could not read design file: {exc}",
                    code="input_read_failed",
                    stage="input",
                )
                return payload, 1
            try:
                parsed = parse(source)
            except (TextConfigError, TypeError, ValueError) as exc:
                _refuse(
                    payload,
                    str(exc),
                    code="invalid_design",
                    stage="input",
                )
                return payload, 1
            payload["dialect"] = parsed.dialect
            payload["migrationsApplied"] = [
                {"name": application.name, "note": application.note}
                for application in parsed.migrations
            ]
            file_settings = has_solve_blocks(parsed.extra_blocks)
            if overlay is not None:
                payload["settingsSource"] = (
                    "file+overlay" if file_settings else "defaults+overlay"
                )
            elif file_settings:
                payload["settingsSource"] = "file"
            built = build_request(parsed, overlay=overlay, engine=engine)
        request = built.request
        payload["settingsSource"] = built.settings_source
        payload["clientRequestId"] = request.client_request_id
    except ValidationError as exc:
        for message in _validation_messages(exc):
            _refuse(payload, message)
        return payload, 1
    except (TypeError, ValueError) as exc:
        _refuse(payload, str(exc), code="invalid_input", stage="input")
        return payload, 1
    requested_engine = request.options.engine
    payload["engine"]["requested"] = requested_engine
    if isinstance(request.geometry, ImportedGeometrySource):
        _refuse(
            payload,
            "imported-geometry designs are not supported by the CLI yet",
            code="unsupported_geometry",
            stage="submission",
        )
        return payload, 1

    # Probe before resolving so even a refusal can tell an operator what this
    # host can run. The registry memoizes this exact snapshot, preventing AUTO
    # and the availability check from observing two different native states.
    try:
        capabilities = await engine_registry.capabilities()
    except Exception as exc:  # a broken injected/native probe is a validation refusal
        message = f"engine capability probe failed: {exc}"
        payload["engine"]["reason"] = message
        _refuse(
            payload,
            message,
            code="capability_probe_failed",
            stage="capabilities",
            retryable=True,
        )
        return payload, 1
    try:
        resolved = await resolve_submission(request, engine_registry)
    except (EngineUnavailableError, UnknownEngineError) as exc:
        if requested_engine != "auto":
            payload["engine"]["resolved"] = requested_engine
        selected = next(
            (
                item
                for item in capabilities
                if item.name == payload["engine"]["resolved"]
            ),
            None,
        )
        payload["engine"]["reason"] = (
            selected.reason
            if selected is not None and not selected.available
            else str(exc)
        )
        _refuse(
            payload,
            str(exc),
            code=(
                "engine_unavailable"
                if isinstance(exc, EngineUnavailableError)
                else "unknown_engine"
            ),
            stage="submission",
        )
        return payload, 1
    except SymmetryValidationError as exc:
        _refuse(payload, str(exc), code="invalid_symmetry", stage="submission")
        return payload, 1
    except ImportedSolveRefusal as exc:
        _refuse(
            payload,
            str(exc),
            code=exc.reason_code,
            stage="submission",
            details=exc.details,
        )
        return payload, 1
    except Exception as exc:
        message = f"submission validation failed: {exc}"
        payload["engine"]["reason"] = message
        _refuse(payload, message, code="submission_failed", stage="submission")
        return payload, 1

    request = resolved.request
    payload["engine"]["resolved"] = resolved.engine_name
    payload["engine"]["available"] = True
    payload["engine"]["reason"] = _engine_reason(
        capabilities, resolved.engine_name
    )
    payload["symmetry"] = _symmetry_summary(resolved.symmetry_metadata)
    try:
        payload["frequencies"] = _frequency_summary(request)
    except (TypeError, ValueError) as exc:
        _refuse(
            payload,
            f"frequency validation failed: {exc}",
            code="invalid_frequency_sweep",
            stage="validation",
        )
        return payload, 1

    # The persisted request keeps the user's design intact, then the job runner
    # installs the validated quadrant mask on a deep copy immediately before
    # either mesh construction or path selection. The validator must make both
    # predictions from that same execution-shaped request.
    execution_request = request.model_copy(deep=True)
    execution_request.design.root.mesh.quadrants = Expr(
        value=float(resolved.symmetry_metadata["resolved_quadrants"])
    )
    payload["solvePath"] = _solve_path_summary(resolved.symmetry_metadata)

    if no_mesh:
        return payload, 0

    compile_mesh = mesh_builder or build_solver_mesh
    prewarm = prewarm_worker or prewarm_gmsh_worker
    shutdown = shutdown_worker or shutdown_gmsh_worker

    try:
        await prewarm()
        mesh_result = await compile_mesh(
            execution_request.design, execution_request.options
        )
        stats = mesh_result["stats"]
        payload["mesh"] = {
            "triangles": int(stats["triangle_count"]),
            "vertices": int(stats["vertex_count"]),
            "integrity": mesh_result["integrity"],
            "warnings": list(stats.get("warnings") or []),
        }
    except Exception as exc:
        _refuse(
            payload,
            f"solver mesh compilation failed: {exc}",
            code="mesh_refused",
            stage="mesh",
        )
    finally:
        try:
            await shutdown()
        except Exception as exc:
            _refuse(
                payload,
                f"gmsh worker shutdown failed: {exc}",
                code="mesh_runtime_cleanup_failed",
                stage="cleanup",
                retryable=True,
            )
    return payload, 1 if refusals else 0


def validate_command(
    args: argparse.Namespace,
    *,
    engine_registry: EngineRegistry,
) -> int:
    payload, exit_code = asyncio.run(
        validate_path(
            args.request or args.file,
            engine_registry=engine_registry,
            engine=args.engine,
            overlay=args.overlay,
            no_mesh=args.no_mesh,
            request_document=args.request is not None,
        )
    )
    render_validation(payload, json_output=args.json, stream=sys.stdout)
    return exit_code


__all__ = ["validate_command", "validate_path"]
