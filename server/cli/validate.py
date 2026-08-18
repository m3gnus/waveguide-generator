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
from server.design.textcfg import ParsedDesign, TextConfigError, parse
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
from server.solver.metal import circsym_eligibility_reasons

from .render import render_validation
from .request import build_request


MeshBuilder = Callable[..., Awaitable[dict[str, Any]]]
WorkerLifecycle = Callable[[], Awaitable[None]]


def _base_payload(path: Path, requested_engine: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "file": str(path),
        "dialect": None,
        "migrationsApplied": [],
        "settingsSource": "defaults",
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
    }


def _validation_messages(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        prefix = f"{location}: " if location else ""
        messages.append(prefix + str(error["msg"]))
    return messages


def _frequency_summary(request: SolveRequest) -> dict[str, Any]:
    solver_mode = request.design.root.simulation.solver_mode or "auto"
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


async def _solve_path_summary(request: SolveRequest, engine_name: str) -> dict[str, Any]:
    if engine_name != "metal":
        return {
            "predicted": "full-3d",
            "reasons": [
                f"engine '{engine_name}' does not use Metal's axisymmetric-meridian path"
            ],
        }

    mode = request.design.root.simulation.solver_mode or "auto"
    if mode == "full_3d":
        return {
            "predicted": "full-3d",
            "reasons": [
                "solver_mode='full_3d' explicitly opts out of the meridian fast path"
            ],
        }
    try:
        reasons = await asyncio.to_thread(circsym_eligibility_reasons, request)
    except Exception as exc:  # eligibility is advisory unless CircSym was forced
        reasons = [f"axisymmetric-meridian eligibility check failed: {exc}"]
    return {
        "predicted": "full-3d" if reasons else "axisymmetric-meridian",
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
    mesh_builder: MeshBuilder | None = None,
    prewarm_worker: WorkerLifecycle | None = None,
    shutdown_worker: WorkerLifecycle | None = None,
) -> tuple[dict[str, Any], int]:
    requested_engine = str(engine or "auto").strip().lower()
    payload = _base_payload(path, requested_engine)
    refusals: list[str] = payload["refusals"]

    if path.suffix.lower() not in {".mwg", ".cfg"}:
        refusals.append("design file must use the .mwg or .cfg extension")
        return payload, 1
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        refusals.append(f"could not read design file: {exc}")
        return payload, 1

    try:
        parsed = parse(source)
    except (TextConfigError, TypeError, ValueError) as exc:
        refusals.append(str(exc))
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

    try:
        built = build_request(parsed, overlay=overlay, engine=engine)
        request = built.request
        payload["settingsSource"] = built.settings_source
    except ValidationError as exc:
        refusals.extend(_validation_messages(exc))
        return payload, 1
    except (TypeError, ValueError) as exc:
        refusals.append(str(exc))
        return payload, 1
    requested_engine = request.options.engine
    payload["engine"]["requested"] = requested_engine
    if isinstance(request.geometry, ImportedGeometrySource):
        refusals.append("imported-geometry designs are not supported by the CLI yet")
        return payload, 1

    # Probe before resolving so even a refusal can tell an operator what this
    # host can run. The registry memoizes this exact snapshot, preventing AUTO
    # and the availability check from observing two different native states.
    try:
        capabilities = await engine_registry.capabilities()
    except Exception as exc:  # a broken injected/native probe is a validation refusal
        message = f"engine capability probe failed: {exc}"
        payload["engine"]["reason"] = message
        refusals.append(message)
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
        refusals.append(str(exc))
        return payload, 1
    except SymmetryValidationError as exc:
        refusals.append(str(exc))
        return payload, 1
    except ImportedSolveRefusal as exc:
        refusals.append(str(exc))
        return payload, 1
    except Exception as exc:
        message = f"submission validation failed: {exc}"
        payload["engine"]["reason"] = message
        refusals.append(message)
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
        refusals.append(f"frequency validation failed: {exc}")
        return payload, 1

    # The persisted request keeps the user's design intact, then the job runner
    # installs the validated quadrant mask on a deep copy immediately before
    # either mesh construction or path selection. The validator must make both
    # predictions from that same execution-shaped request.
    execution_request = request.model_copy(deep=True)
    execution_request.design.root.mesh.quadrants = Expr(
        value=float(resolved.symmetry_metadata["resolved_quadrants"])
    )
    payload["solvePath"] = await _solve_path_summary(
        execution_request, resolved.engine_name
    )
    if (
        request.design.root.simulation.solver_mode == "circsym"
        and payload["solvePath"]["reasons"]
    ):
        refusals.append(
            "Forced axisymmetric solver mode is not eligible: "
            + "; ".join(payload["solvePath"]["reasons"])
        )
        return payload, 1

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
        refusals.append(f"solver mesh compilation failed: {exc}")
    finally:
        try:
            await shutdown()
        except Exception as exc:
            refusals.append(f"gmsh worker shutdown failed: {exc}")
    return payload, 1 if refusals else 0


def validate_command(
    args: argparse.Namespace,
    *,
    engine_registry: EngineRegistry,
) -> int:
    payload, exit_code = asyncio.run(
        validate_path(
            args.file,
            engine_registry=engine_registry,
            engine=args.engine,
            overlay=args.overlay,
            no_mesh=args.no_mesh,
        )
    )
    render_validation(payload, json_output=args.json, stream=sys.stdout)
    return exit_code


__all__ = ["validate_command", "validate_path"]
