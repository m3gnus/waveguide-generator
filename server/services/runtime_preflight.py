"""Backend dependency preflight checks for install/startup workflows."""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from importlib.util import find_spec
from typing import Any, Dict, List, Tuple

from services.solver_runtime import get_dependency_status
from solver.device_interface import selected_device_metadata


def read_fastapi_runtime() -> Dict[str, Any]:
    available = find_spec("fastapi") is not None
    version = None
    if available:
        try:
            version = metadata.version("fastapi")
        except metadata.PackageNotFoundError:
            version = None
    return {
        "available": bool(available),
        "version": version,
    }


def read_opencl_device_metadata(preferred_mode: str = "auto") -> Dict[str, Any]:
    try:
        metadata_payload = selected_device_metadata(preferred_mode)
    except Exception as exc:  # pragma: no cover - runtime-specific
        return {
            "opencl_available": False,
            "fallback_reason": f"OpenCL probe failed: {exc}",
            "warning": f"OpenCL probe failed: {exc}",
        }
    return metadata_payload if isinstance(metadata_payload, dict) else {}


def _build_required_checks(
    dependency_status: Dict[str, Any],
    fastapi_runtime: Dict[str, Any],
    device_metadata: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    runtime = dependency_status.get("runtime") if isinstance(dependency_status, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else {}

    gmsh_runtime = runtime.get("gmsh_python") if isinstance(runtime.get("gmsh_python"), dict) else {}
    bempp_runtime = runtime.get("bempp") if isinstance(runtime.get("bempp"), dict) else {}

    fastapi_ok = bool(fastapi_runtime.get("available"))
    gmsh_ok = bool(gmsh_runtime.get("ready"))
    bempp_ok = bool(bempp_runtime.get("ready"))
    opencl_ok = bool(device_metadata.get("opencl_available"))

    return {
        "fastapi": {
            "ok": fastapi_ok,
            "requiredFor": "backend startup",
            "detail": (
                f"version={fastapi_runtime.get('version') or 'unknown'}"
                if fastapi_ok
                else "fastapi import failed."
            ),
        },
        "gmsh_python": {
            "ok": gmsh_ok,
            "requiredFor": "/api/mesh/build",
            "detail": (
                f"version={gmsh_runtime.get('version') or 'unknown'} supported={gmsh_runtime.get('supported')}"
                if gmsh_runtime
                else "gmsh runtime status unavailable."
            ),
        },
        "bempp_cl": {
            "ok": bempp_ok,
            "requiredFor": "/api/solve",
            "detail": (
                "variant="
                f"{bempp_runtime.get('variant') or 'unknown'} "
                f"version={bempp_runtime.get('version') or 'unknown'} "
                f"supported={bempp_runtime.get('supported')}"
                if bempp_runtime
                else "bempp runtime status unavailable."
            ),
        },
        "opencl_runtime": {
            "ok": opencl_ok,
            "requiredFor": "/api/solve",
            "detail": (
                "selected_mode="
                f"{device_metadata.get('selected_mode') or 'none'} "
                f"device={device_metadata.get('device_name') or 'unknown'}"
                if opencl_ok
                else str(
                    device_metadata.get("fallback_reason")
                    or device_metadata.get("warning")
                    or "No OpenCL runtime available."
                )
            ),
        },
    }


def collect_runtime_preflight(preferred_mode: str = "auto") -> Dict[str, Any]:
    dependency_status = get_dependency_status()
    fastapi_runtime = read_fastapi_runtime()
    device_metadata = read_opencl_device_metadata(preferred_mode)
    required_checks = _build_required_checks(
        dependency_status=dependency_status,
        fastapi_runtime=fastapi_runtime,
        device_metadata=device_metadata,
    )
    all_required_ok = all(bool(check.get("ok")) for check in required_checks.values())

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "interpreter": {
            "path": sys.executable,
            "version": platform.python_version(),
            "source": str(os.environ.get("WG_BACKEND_PYTHON_SOURCE") or "unknown"),
        },
        "dependencies": dependency_status,
        "fastapi": fastapi_runtime,
        "deviceInterface": device_metadata,
        "requiredChecks": required_checks,
        "allRequiredReady": all_required_ok,
    }


def evaluate_required_checks(report: Dict[str, Any]) -> Tuple[bool, List[str]]:
    required = report.get("requiredChecks") if isinstance(report, dict) else {}
    required = required if isinstance(required, dict) else {}

    failing: List[str] = []
    for check_id, payload in required.items():
        if not isinstance(payload, dict) or not bool(payload.get("ok")):
            detail = payload.get("detail") if isinstance(payload, dict) else "missing check payload"
            failing.append(f"{check_id}: {detail}")
    return len(failing) == 0, failing


def render_runtime_preflight_text(report: Dict[str, Any]) -> str:
    interpreter = report.get("interpreter") if isinstance(report, dict) else {}
    interpreter = interpreter if isinstance(interpreter, dict) else {}
    required = report.get("requiredChecks") if isinstance(report, dict) else {}
    required = required if isinstance(required, dict) else {}

    lines = [
        "Waveguide backend runtime preflight",
        f"Interpreter: {interpreter.get('path') or 'unknown'}",
        f"Source: {interpreter.get('source') or 'unknown'}",
        f"Version: {interpreter.get('version') or 'unknown'}",
        "",
    ]

    for check_id in ("fastapi", "gmsh_python", "bempp_cl", "opencl_runtime"):
        payload = required.get(check_id) if isinstance(required.get(check_id), dict) else {}
        ok = bool(payload.get("ok"))
        status = "OK" if ok else "MISSING"
        detail = str(payload.get("detail") or "n/a")
        lines.append(f"- {check_id}: {status} ({detail})")

    lines.append("")
    lines.append(
        "Overall required runtime status: "
        + ("READY" if bool(report.get("allRequiredReady")) else "NOT READY")
    )
    return "\n".join(lines)


def run_runtime_preflight(*, strict: bool, json_output: bool, preferred_mode: str = "auto") -> int:
    report = collect_runtime_preflight(preferred_mode=preferred_mode)
    if json_output:
        print(json.dumps(report, indent=2))
    else:
        print(render_runtime_preflight_text(report))

    ok, failing = evaluate_required_checks(report)
    if strict and not ok:
        if not json_output:
            print("")
            print("Failing required checks:")
            for item in failing:
                print(f"  - {item}")
        return 1
    return 0
