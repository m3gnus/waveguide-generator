"""Backend dependency preflight and doctor checks for install/startup workflows."""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from importlib.util import find_spec
from typing import Any, Dict, List, Tuple

from services.solve_readiness import read_bounded_solve_readiness
from services.solver_runtime import get_dependency_status
from solver.device_interface import selected_device_metadata

DOCTOR_SCHEMA_VERSION = 1
DOCTOR_STATUS_INSTALLED = "installed"
DOCTOR_STATUS_MISSING = "missing"
DOCTOR_STATUS_UNSUPPORTED = "unsupported"
DOCTOR_CATEGORY_REQUIRED = "required"
DOCTOR_CATEGORY_OPTIONAL = "optional"


def _read_package_runtime(distribution_name: str, module_name: str) -> Dict[str, Any]:
    available = find_spec(module_name) is not None
    version = None
    if available:
        try:
            version = metadata.version(distribution_name)
        except metadata.PackageNotFoundError:
            version = None
    return {
        "available": bool(available),
        "version": version,
    }


def read_fastapi_runtime() -> Dict[str, Any]:
    return _read_package_runtime("fastapi", "fastapi")


def read_matplotlib_runtime() -> Dict[str, Any]:
    return _read_package_runtime("matplotlib", "matplotlib")


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


def _collect_runtime_snapshot(preferred_mode: str = "auto") -> Dict[str, Any]:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "interpreter": {
            "path": sys.executable,
            "version": platform.python_version(),
            "source": str(os.environ.get("WG_BACKEND_PYTHON_SOURCE") or "unknown"),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": get_dependency_status(),
        "fastapi": read_fastapi_runtime(),
        "matplotlib": read_matplotlib_runtime(),
        "deviceInterface": read_opencl_device_metadata(preferred_mode),
        "solveReadiness": read_bounded_solve_readiness(preferred_mode=preferred_mode),
    }


def _build_required_checks(
    dependency_status: Dict[str, Any],
    fastapi_runtime: Dict[str, Any],
    device_metadata: Dict[str, Any],
    solve_readiness: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    runtime = dependency_status.get("runtime") if isinstance(dependency_status, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else {}

    gmsh_runtime = runtime.get("gmsh_python") if isinstance(runtime.get("gmsh_python"), dict) else {}
    bempp_runtime = runtime.get("bempp") if isinstance(runtime.get("bempp"), dict) else {}
    supported_modes = (
        list(device_metadata.get("supported_modes"))
        if isinstance(device_metadata.get("supported_modes"), list)
        else []
    )
    selection_policy = str(device_metadata.get("selection_policy") or "unknown")

    fastapi_ok = bool(fastapi_runtime.get("available"))
    gmsh_ok = bool(gmsh_runtime.get("ready"))
    bempp_ok = bool(bempp_runtime.get("ready"))
    opencl_ok = bool(device_metadata.get("opencl_available"))
    solve_validation_ok = bool(solve_readiness.get("ready"))

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
                f"device={device_metadata.get('device_name') or 'unknown'} "
                f"supported_modes={','.join(supported_modes) or 'none'} "
                f"policy={selection_policy}"
                if opencl_ok
                else str(
                    device_metadata.get("fallback_reason")
                    or device_metadata.get("warning")
                    or "No OpenCL runtime available."
                )
            ),
        },
        "bounded_solve_validation": {
            "ok": solve_validation_ok,
            "requiredFor": "/api/solve",
            "detail": str(
                solve_readiness.get("detail")
                or "No bounded solve validation evidence available.",
            ),
        },
    }


def _resolve_doctor_status(*, available: bool, supported: bool, ready: bool) -> str:
    if not available:
        return DOCTOR_STATUS_MISSING
    if available and not supported:
        return DOCTOR_STATUS_UNSUPPORTED
    if ready:
        return DOCTOR_STATUS_INSTALLED
    return DOCTOR_STATUS_MISSING


def _guidance_for_component(component_id: str, status: str, system_name: str, machine_name: str) -> List[str]:
    if status == DOCTOR_STATUS_INSTALLED:
        return []

    os_name = (system_name or "").lower()
    if component_id == "fastapi":
        return [
            "Install backend requirements: pip install -r server/requirements.txt",
            "Verify with selected interpreter: python -c \"import fastapi; print(fastapi.__version__)\"",
        ]
    if component_id == "gmsh_python":
        guidance = [
            "Install gmsh package: pip install -r server/requirements-gmsh.txt",
        ]
        if status == DOCTOR_STATUS_UNSUPPORTED:
            guidance.append("Use supported gmsh range: >=4.11,<5.0.")
        if os_name == "linux":
            guidance.append(
                "If default wheels are unavailable on headless Linux: "
                "pip install --pre --extra-index-url https://gmsh.info/python-packages-dev-nox -r server/requirements-gmsh.txt"
            )
        return guidance
    if component_id == "bempp_cl":
        guidance = [
            "Install bempp-cl: pip install git+https://github.com/bempp/bempp-cl.git",
        ]
        if status == DOCTOR_STATUS_UNSUPPORTED:
            guidance.append("Use supported bempp-cl range: >=0.4,<0.5.")
        return guidance
    if component_id == "opencl_runtime":
        if os_name == "darwin":
            if (machine_name or "").lower() in {"arm64", "aarch64"}:
                return [
                    "Apple Silicon OpenCL solve is currently unsupported for /api/solve.",
                    "Do not treat ./scripts/setup-opencl-backend.sh or a pocl CPU runtime as a production-readiness fix on Apple Silicon.",
                ]
            return [
                "Apple Silicon CPU fallback: ./scripts/setup-opencl-backend.sh",
                "Or install OpenCL runtime manually (for example: brew install pocl).",
            ]
        if os_name == "windows":
            return [
                "Install/update vendor GPU drivers (NVIDIA/AMD/Intel) that provide OpenCL ICDs.",
                "CPU-only Intel fallback: install Intel OpenCL Runtime for CPU.",
            ]
        return [
            "Install Linux OpenCL ICD runtime (CPU fallback): pocl-opencl-icd/pocl.",
            "Or install vendor GPU OpenCL drivers (NVIDIA/AMD/Intel).",
        ]
    if component_id == "bounded_solve_validation":
        return [
            "Run bounded solve validation: cd server && python3 scripts/benchmark_tritonia.py --freq 1000 --device auto --precision single --timeout 30",
            "Validation evidence is only recorded when solve runs (do not use --no-solve).",
        ]
    if component_id == "matplotlib":
        return [
            "Install matplotlib: pip install matplotlib",
            "Or install full backend requirements: pip install -r server/requirements.txt",
        ]
    return []


def _build_doctor_components(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    dependency_status = snapshot.get("dependencies") if isinstance(snapshot, dict) else {}
    dependency_status = dependency_status if isinstance(dependency_status, dict) else {}
    runtime = dependency_status.get("runtime") if isinstance(dependency_status.get("runtime"), dict) else {}

    system_name = ""
    machine_name = ""
    platform_payload = snapshot.get("platform")
    if isinstance(platform_payload, dict):
        system_name = str(platform_payload.get("system") or "")
        machine_name = str(platform_payload.get("machine") or "")

    fastapi_runtime = snapshot.get("fastapi") if isinstance(snapshot.get("fastapi"), dict) else {}
    matplotlib_runtime = snapshot.get("matplotlib") if isinstance(snapshot.get("matplotlib"), dict) else {}
    device_metadata = snapshot.get("deviceInterface") if isinstance(snapshot.get("deviceInterface"), dict) else {}
    solve_readiness = snapshot.get("solveReadiness") if isinstance(snapshot.get("solveReadiness"), dict) else {}
    supported_modes = (
        list(device_metadata.get("supported_modes"))
        if isinstance(device_metadata.get("supported_modes"), list)
        else []
    )
    selection_policy = str(device_metadata.get("selection_policy") or "unknown")

    gmsh_runtime = runtime.get("gmsh_python") if isinstance(runtime.get("gmsh_python"), dict) else {}
    bempp_runtime = runtime.get("bempp") if isinstance(runtime.get("bempp"), dict) else {}

    fastapi_available = bool(fastapi_runtime.get("available"))
    fastapi_status = _resolve_doctor_status(
        available=fastapi_available,
        supported=fastapi_available,
        ready=fastapi_available,
    )

    gmsh_status = _resolve_doctor_status(
        available=bool(gmsh_runtime.get("available")),
        supported=bool(gmsh_runtime.get("supported")),
        ready=bool(gmsh_runtime.get("ready")),
    )

    bempp_status = _resolve_doctor_status(
        available=bool(bempp_runtime.get("available")),
        supported=bool(bempp_runtime.get("supported")),
        ready=bool(bempp_runtime.get("ready")),
    )

    opencl_available = bool(device_metadata.get("opencl_available"))
    opencl_status = _resolve_doctor_status(
        available=opencl_available,
        supported=opencl_available,
        ready=opencl_available,
    )

    matplotlib_available = bool(matplotlib_runtime.get("available"))
    matplotlib_status = _resolve_doctor_status(
        available=matplotlib_available,
        supported=matplotlib_available,
        ready=matplotlib_available,
    )

    solve_readiness_ready = bool(solve_readiness.get("ready"))
    solve_readiness_status = (
        DOCTOR_STATUS_INSTALLED if solve_readiness_ready else DOCTOR_STATUS_MISSING
    )

    components = [
        {
            "id": "fastapi",
            "name": "FastAPI",
            "category": DOCTOR_CATEGORY_REQUIRED,
            "requiredFor": "backend startup",
            "featureImpact": (
                "Backend API process cannot start."
                if fastapi_status != DOCTOR_STATUS_INSTALLED
                else "Backend API startup is available."
            ),
            "status": fastapi_status,
            "available": fastapi_available,
            "supported": fastapi_available,
            "ready": fastapi_available,
            "version": fastapi_runtime.get("version"),
            "detail": (
                f"version={fastapi_runtime.get('version') or 'unknown'}"
                if fastapi_available
                else "fastapi import failed."
            ),
        },
        {
            "id": "gmsh_python",
            "name": "Gmsh Python API",
            "category": DOCTOR_CATEGORY_REQUIRED,
            "requiredFor": "/api/mesh/build",
            "featureImpact": (
                "/api/mesh/build and adaptive OCC meshing are unavailable."
                if gmsh_status != DOCTOR_STATUS_INSTALLED
                else "OCC mesh build path is available."
            ),
            "status": gmsh_status,
            "available": bool(gmsh_runtime.get("available")),
            "supported": bool(gmsh_runtime.get("supported")),
            "ready": bool(gmsh_runtime.get("ready")),
            "version": gmsh_runtime.get("version"),
            "detail": (
                f"version={gmsh_runtime.get('version') or 'unknown'} supported={gmsh_runtime.get('supported')}"
                if gmsh_runtime
                else "gmsh runtime status unavailable."
            ),
        },
        {
            "id": "bempp_cl",
            "name": "bempp-cl",
            "category": DOCTOR_CATEGORY_REQUIRED,
            "requiredFor": "/api/solve",
            "featureImpact": (
                "/api/solve BEM simulation is unavailable."
                if bempp_status != DOCTOR_STATUS_INSTALLED
                else "BEM simulation backend path is available."
            ),
            "status": bempp_status,
            "available": bool(bempp_runtime.get("available")),
            "supported": bool(bempp_runtime.get("supported")),
            "ready": bool(bempp_runtime.get("ready")),
            "version": bempp_runtime.get("version"),
            "detail": (
                "variant="
                f"{bempp_runtime.get('variant') or 'unknown'} "
                f"version={bempp_runtime.get('version') or 'unknown'} "
                f"supported={bempp_runtime.get('supported')}"
                if bempp_runtime
                else "bempp runtime status unavailable."
            ),
        },
        {
            "id": "opencl_runtime",
            "name": "OpenCL Runtime",
            "category": DOCTOR_CATEGORY_REQUIRED,
            "requiredFor": "/api/solve",
            "featureImpact": (
                "/api/solve BEM simulation is unavailable because bempp-cl requires OpenCL."
                if opencl_status != DOCTOR_STATUS_INSTALLED
                else "OpenCL runtime is available for bempp-cl."
            ),
            "status": opencl_status,
            "available": opencl_available,
            "supported": opencl_available,
            "ready": opencl_available,
            "version": None,
            "detail": (
                "selected_mode="
                f"{device_metadata.get('selected_mode') or 'none'} "
                f"device={device_metadata.get('device_name') or 'unknown'} "
                f"supported_modes={','.join(supported_modes) or 'none'} "
                f"policy={selection_policy}"
                if opencl_available
                else str(
                    device_metadata.get("fallback_reason")
                    or device_metadata.get("warning")
                    or "No OpenCL runtime available."
                )
            ),
        },
        {
            "id": "bounded_solve_validation",
            "name": "Bounded solve validation",
            "category": DOCTOR_CATEGORY_REQUIRED,
            "requiredFor": "/api/solve",
            "featureImpact": (
                "/api/solve readiness is unvalidated on this host/runtime."
                if solve_readiness_status != DOCTOR_STATUS_INSTALLED
                else "Bounded /api/solve runtime validation passed."
            ),
            "status": solve_readiness_status,
            "available": bool(solve_readiness.get("status") not in {"missing", "invalid"}),
            "supported": bool(solve_readiness.get("status") not in {"invalid", "stale_host", "mode_mismatch"}),
            "ready": solve_readiness_ready,
            "version": None,
            "detail": str(
                solve_readiness.get("detail")
                or "No bounded solve validation evidence available."
            ),
        },
        {
            "id": "matplotlib",
            "name": "Matplotlib",
            "category": DOCTOR_CATEGORY_OPTIONAL,
            "requiredFor": "chart/directivity image render endpoints",
            "featureImpact": (
                "Chart/directivity image render endpoints are unavailable; solver core paths still work."
                if matplotlib_status != DOCTOR_STATUS_INSTALLED
                else "Chart/directivity image rendering endpoints are available."
            ),
            "status": matplotlib_status,
            "available": matplotlib_available,
            "supported": matplotlib_available,
            "ready": matplotlib_available,
            "version": matplotlib_runtime.get("version"),
            "detail": (
                f"version={matplotlib_runtime.get('version') or 'unknown'}"
                if matplotlib_available
                else "matplotlib import failed."
            ),
        },
    ]

    for component in components:
        component["guidance"] = _guidance_for_component(
            component_id=str(component.get("id") or ""),
            status=str(component.get("status") or DOCTOR_STATUS_MISSING),
            system_name=system_name,
            machine_name=machine_name,
        )
    return components


def _build_doctor_summary(components: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {
        DOCTOR_STATUS_INSTALLED: 0,
        DOCTOR_STATUS_MISSING: 0,
        DOCTOR_STATUS_UNSUPPORTED: 0,
        "optional": 0,
    }
    required_issues: List[str] = []
    optional_issues: List[str] = []
    solve_issues: List[str] = []
    mesh_build_issues: List[str] = []

    for component in components:
        status = str(component.get("status") or DOCTOR_STATUS_MISSING)
        category = str(component.get("category") or DOCTOR_CATEGORY_REQUIRED)
        component_id = str(component.get("id") or "unknown")
        required_for = str(component.get("requiredFor") or "")

        if status in counts:
            counts[status] += 1
        if category == DOCTOR_CATEGORY_OPTIONAL:
            counts["optional"] += 1

        if category == DOCTOR_CATEGORY_REQUIRED and status != DOCTOR_STATUS_INSTALLED:
            required_issues.append(component_id)
            if required_for == "/api/solve":
                solve_issues.append(component_id)
            if required_for == "/api/mesh/build":
                mesh_build_issues.append(component_id)
        if category == DOCTOR_CATEGORY_OPTIONAL and status != DOCTOR_STATUS_INSTALLED:
            optional_issues.append(component_id)

    return {
        "requiredReady": len(required_issues) == 0,
        "requiredIssues": required_issues,
        "optionalIssues": optional_issues,
        "solveReady": len(solve_issues) == 0,
        "solveIssues": solve_issues,
        "meshBuildReady": len(mesh_build_issues) == 0,
        "meshBuildIssues": mesh_build_issues,
        "counts": counts,
    }


def collect_runtime_preflight(preferred_mode: str = "auto") -> Dict[str, Any]:
    snapshot = _collect_runtime_snapshot(preferred_mode=preferred_mode)
    required_checks = _build_required_checks(
        dependency_status=snapshot.get("dependencies", {}),
        fastapi_runtime=snapshot.get("fastapi", {}),
        device_metadata=snapshot.get("deviceInterface", {}),
        solve_readiness=snapshot.get("solveReadiness", {}),
    )
    all_required_ok = all(bool(check.get("ok")) for check in required_checks.values())

    return {
        "generatedAt": snapshot.get("generatedAt"),
        "interpreter": snapshot.get("interpreter"),
        "dependencies": snapshot.get("dependencies"),
        "fastapi": snapshot.get("fastapi"),
        "deviceInterface": snapshot.get("deviceInterface"),
        "solveReadiness": snapshot.get("solveReadiness"),
        "requiredChecks": required_checks,
        "allRequiredReady": all_required_ok,
    }


def collect_runtime_doctor_report(preferred_mode: str = "auto") -> Dict[str, Any]:
    snapshot = _collect_runtime_snapshot(preferred_mode=preferred_mode)
    components = _build_doctor_components(snapshot)
    summary = _build_doctor_summary(components)
    return {
        "schemaVersion": DOCTOR_SCHEMA_VERSION,
        "generatedAt": snapshot.get("generatedAt"),
        "interpreter": snapshot.get("interpreter"),
        "platform": snapshot.get("platform"),
        "components": components,
        "summary": summary,
        "dependencies": snapshot.get("dependencies"),
        "deviceInterface": snapshot.get("deviceInterface"),
        "solveReadiness": snapshot.get("solveReadiness"),
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


def evaluate_runtime_doctor(report: Dict[str, Any]) -> Tuple[bool, List[str]]:
    components = report.get("components") if isinstance(report, dict) else []
    components = components if isinstance(components, list) else []

    failing: List[str] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        if str(component.get("category")) != DOCTOR_CATEGORY_REQUIRED:
            continue
        status = str(component.get("status") or DOCTOR_STATUS_MISSING)
        if status != DOCTOR_STATUS_INSTALLED:
            detail = str(component.get("detail") or "missing detail")
            failing.append(f"{component.get('id')}: {status} ({detail})")
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

    for check_id in ("fastapi", "gmsh_python", "bempp_cl", "opencl_runtime", "bounded_solve_validation"):
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


def render_runtime_doctor_text(report: Dict[str, Any]) -> str:
    interpreter = report.get("interpreter") if isinstance(report, dict) else {}
    interpreter = interpreter if isinstance(interpreter, dict) else {}
    platform_payload = report.get("platform") if isinstance(report, dict) else {}
    platform_payload = platform_payload if isinstance(platform_payload, dict) else {}
    components = report.get("components") if isinstance(report, dict) else []
    components = components if isinstance(components, list) else []
    summary = report.get("summary") if isinstance(report, dict) else {}
    summary = summary if isinstance(summary, dict) else {}

    lines = [
        "Waveguide backend dependency doctor",
        f"Interpreter: {interpreter.get('path') or 'unknown'}",
        f"Source: {interpreter.get('source') or 'unknown'}",
        f"Version: {interpreter.get('version') or 'unknown'}",
        (
            "Platform: "
            f"{platform_payload.get('system') or 'unknown'} "
            f"{platform_payload.get('release') or ''} "
            f"{platform_payload.get('machine') or ''}"
        ).strip(),
        "",
    ]

    for component in components:
        if not isinstance(component, dict):
            continue
        lines.append(
            "- "
            f"{component.get('id')}: "
            f"{str(component.get('status') or DOCTOR_STATUS_MISSING).upper()} "
            f"[{component.get('category')}] "
            f"(requiredFor={component.get('requiredFor')})"
        )
        lines.append(f"  detail: {component.get('detail') or 'n/a'}")
        lines.append(f"  impact: {component.get('featureImpact') or 'n/a'}")
        guidance = component.get("guidance") if isinstance(component.get("guidance"), list) else []
        for line in guidance:
            lines.append(f"  guidance: {line}")

    lines.append("")
    lines.append(
        "Required dependency status: "
        + ("READY" if bool(summary.get("requiredReady")) else "NOT READY")
    )
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    lines.append(
        "Counts: "
        f"installed={counts.get('installed', 0)} "
        f"missing={counts.get('missing', 0)} "
        f"unsupported={counts.get('unsupported', 0)} "
        f"optional={counts.get('optional', 0)}"
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


def run_runtime_doctor(*, strict: bool, json_output: bool, preferred_mode: str = "auto") -> int:
    report = collect_runtime_doctor_report(preferred_mode=preferred_mode)
    if json_output:
        print(json.dumps(report, indent=2))
    else:
        print(render_runtime_doctor_text(report))

    ok, failing = evaluate_runtime_doctor(report)
    if strict and not ok:
        if not json_output:
            print("")
            print("Failing required doctor checks:")
            for item in failing:
                print(f"  - {item}")
        return 1
    return 0
