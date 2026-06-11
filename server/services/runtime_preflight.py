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

from services.solver_runtime import (
    bempp_backend_status,
    get_dependency_status,
    metal_backend_status,
    opencl_runtime_status,
)

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
        "metalBackend": metal_backend_status(),
        "bemppBackend": bempp_backend_status(),
        "openclRuntime": opencl_runtime_status(),
    }


def _build_required_checks(
    dependency_status: Dict[str, Any],
    fastapi_runtime: Dict[str, Any],
    metal_backend: Dict[str, Any],
    bempp_backend: Dict[str, Any],
    platform_payload: Dict[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    runtime = dependency_status.get("runtime") if isinstance(dependency_status, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else {}

    mesher_runtime = (
        runtime.get("hornlab_waveguide_mesher")
        if isinstance(runtime.get("hornlab_waveguide_mesher"), dict)
        else {}
    )
    gmsh_runtime = runtime.get("gmsh_python") if isinstance(runtime.get("gmsh_python"), dict) else {}
    metal_bem_runtime = (
        runtime.get("hornlab_metal_bem")
        if isinstance(runtime.get("hornlab_metal_bem"), dict)
        else {}
    )
    bempp_bem_runtime = (
        runtime.get("hornlab_bempp_bem")
        if isinstance(runtime.get("hornlab_bempp_bem"), dict)
        else {}
    )

    fastapi_ok = bool(fastapi_runtime.get("available"))
    mesher_ok = bool(mesher_runtime.get("ready"))
    gmsh_ok = bool(gmsh_runtime.get("ready"))
    metal_ok = bool(metal_backend.get("available"))
    bempp_ok = bool(bempp_backend.get("available"))
    apple_silicon = _is_apple_silicon(platform_payload)

    if metal_ok:
        metal_detail = (
            f"version={metal_bem_runtime.get('version') or 'unknown'} "
            f"native_helper={metal_backend.get('nativeHelperBuild') or 'unknown'}"
        )
    else:
        metal_detail = str(metal_backend.get("reason") or "Metal BEM backend is unavailable.")

    bempp_detail = (
        f"version={bempp_bem_runtime.get('version') or 'unknown'} "
        f"assembly_backend={bempp_backend.get('assemblyBackend') or 'unknown'}"
        if bempp_ok
        else str(bempp_backend.get("reason") or "hornlab-bempp-bem is not installed.")
    )

    checks = {
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
        "hornlab_waveguide_mesher": {
            "ok": mesher_ok,
            "requiredFor": "/api/mesh/build",
            "detail": (
                f"version={mesher_runtime.get('version') or 'unknown'} supported={mesher_runtime.get('supported')}"
                if mesher_runtime
                else "hornlab-waveguide-mesher runtime status unavailable."
            ),
        },
    }
    if apple_silicon:
        checks["hornlab_metal_bem"] = {
            "ok": metal_ok,
            "requiredFor": "/api/solve on Apple-Silicon hosts",
            "detail": metal_detail,
        }
    else:
        checks["hornlab_bempp_bem"] = {
            "ok": bempp_ok,
            "requiredFor": "/api/solve on non-Apple-Silicon hosts",
            "detail": bempp_detail,
        }
    metal_release_check = _build_metal_release_helper_required_check(
        metal_backend=metal_backend,
        platform_payload=platform_payload,
    )
    if metal_release_check is not None:
        checks["metal_release_helper"] = metal_release_check
    return checks


def _metal_path_ready(metal_backend: Dict[str, Any]) -> bool:
    return bool(metal_backend.get("available"))


def _is_apple_silicon(platform_payload: Dict[str, Any] | None = None) -> bool:
    payload = platform_payload if isinstance(platform_payload, dict) else {}
    system_name = str(payload.get("system") or platform.system())
    machine_name = str(payload.get("machine") or platform.machine())
    return system_name == "Darwin" and machine_name == "arm64"


def _metal_release_helper_ready(metal_backend: Dict[str, Any]) -> bool:
    return bool(
        metal_backend.get("available")
        and metal_backend.get("nativeHelperAvailable")
        and metal_backend.get("nativeHelperBuild") == "release"
    )


def _metal_release_helper_detail(metal_backend: Dict[str, Any]) -> str:
    helper_build = str(metal_backend.get("nativeHelperBuild") or "missing")
    helper_path = str(metal_backend.get("nativeHelperPath") or "unknown")
    reason = str(metal_backend.get("reason") or "")
    if _metal_release_helper_ready(metal_backend):
        return f"build=release path={helper_path}"
    if reason:
        return f"build={helper_build} path={helper_path} reason={reason}"
    return f"build={helper_build} path={helper_path}; expected release helper for fastest Metal solves."


def _build_metal_release_helper_required_check(
    *,
    metal_backend: Dict[str, Any],
    platform_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    if not _is_apple_silicon(platform_payload):
        return None
    return {
        "ok": _metal_release_helper_ready(metal_backend),
        "requiredFor": "/api/solve",
        "detail": _metal_release_helper_detail(metal_backend),
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
    if component_id == "hornlab_waveguide_mesher":
        return [
            "Install backend requirements: pip install -r server/requirements.txt",
            "Verify with selected interpreter: python -c \"import hornlab_mesher; print(hornlab_mesher.__file__)\"",
        ]
    if component_id == "hornlab_metal_bem":
        return [
            "Install backend requirements: pip install -r server/requirements.txt",
            "Verify with selected interpreter: python -c \"import hornlab_metal_bem; print(hornlab_metal_bem.__file__)\"",
            "Metal BEM solves require an Apple Silicon Mac.",
        ]
    if component_id == "hornlab_bempp_bem":
        return [
            "Install BEMPP fallback requirements: pip install -r server/requirements-bempp.txt",
            "Verify with selected interpreter: python -c \"import hornlab_bempp_bem; print(hornlab_bempp_bem.__file__)\"",
        ]
    if component_id == "opencl_runtime":
        guidance = [
            "BEMPP solves work without OpenCL through the numba backend, but solves are slower.",
        ]
        if os_name == "linux":
            guidance.append("For faster OpenCL solves on Linux, install pocl from the distro package manager.")
        elif os_name == "windows":
            guidance.append(
                "For faster OpenCL solves on Windows, install GPU drivers or the Intel CPU OpenCL runtime."
            )
        return guidance
    if component_id == "metal_release_helper":
        return [
            "Build the release Metal helper: npm run build:metal-helper",
            "If swift is missing, install Xcode Command Line Tools: xcode-select --install",
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
    metal_backend = snapshot.get("metalBackend") if isinstance(snapshot.get("metalBackend"), dict) else {}
    bempp_backend = snapshot.get("bemppBackend") if isinstance(snapshot.get("bemppBackend"), dict) else {}
    opencl_runtime = snapshot.get("openclRuntime") if isinstance(snapshot.get("openclRuntime"), dict) else {}

    gmsh_runtime = runtime.get("gmsh_python") if isinstance(runtime.get("gmsh_python"), dict) else {}
    mesher_runtime = (
        runtime.get("hornlab_waveguide_mesher")
        if isinstance(runtime.get("hornlab_waveguide_mesher"), dict)
        else {}
    )
    metal_bem_runtime = (
        runtime.get("hornlab_metal_bem")
        if isinstance(runtime.get("hornlab_metal_bem"), dict)
        else {}
    )
    bempp_bem_runtime = (
        runtime.get("hornlab_bempp_bem")
        if isinstance(runtime.get("hornlab_bempp_bem"), dict)
        else {}
    )

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
    mesher_status = _resolve_doctor_status(
        available=bool(mesher_runtime.get("available")),
        supported=bool(mesher_runtime.get("supported")),
        ready=bool(mesher_runtime.get("ready")),
    )

    metal_ready = _metal_path_ready(metal_backend)
    bempp_ready = bool(bempp_backend.get("available"))
    apple_silicon = _is_apple_silicon(platform_payload)
    metal_status = _resolve_doctor_status(
        available=bool(metal_bem_runtime.get("available")),
        supported=bool(metal_bem_runtime.get("supported")),
        ready=metal_ready,
    )
    bempp_status = _resolve_doctor_status(
        available=bool(bempp_bem_runtime.get("available")),
        supported=bool(bempp_bem_runtime.get("supported")),
        ready=bempp_ready,
    )
    opencl_available = bool(opencl_runtime.get("available"))
    opencl_status = DOCTOR_STATUS_INSTALLED if opencl_available else DOCTOR_STATUS_MISSING
    metal_release_helper_ready = _metal_release_helper_ready(metal_backend)
    metal_release_helper_status = (
        DOCTOR_STATUS_INSTALLED if metal_release_helper_ready else DOCTOR_STATUS_MISSING
    )

    matplotlib_available = bool(matplotlib_runtime.get("available"))
    matplotlib_status = _resolve_doctor_status(
        available=matplotlib_available,
        supported=matplotlib_available,
        ready=matplotlib_available,
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
                "/api/mesh/build and HornLab mesher jobs are unavailable."
                if gmsh_status != DOCTOR_STATUS_INSTALLED
                else "HornLab mesher build path is available."
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
            "id": "hornlab_waveguide_mesher",
            "name": "HornLab waveguide mesher",
            "category": DOCTOR_CATEGORY_REQUIRED,
            "requiredFor": "/api/mesh/build",
            "featureImpact": (
                "/api/mesh/build, viewport meshing, and HornLab mesher jobs are unavailable."
                if mesher_status != DOCTOR_STATUS_INSTALLED
                else "HornLab waveguide mesher package is available."
            ),
            "status": mesher_status,
            "available": bool(mesher_runtime.get("available")),
            "supported": bool(mesher_runtime.get("supported")),
            "ready": bool(mesher_runtime.get("ready")),
            "version": mesher_runtime.get("version"),
            "detail": (
                f"version={mesher_runtime.get('version') or 'unknown'} supported={mesher_runtime.get('supported')}"
                if mesher_runtime
                else "hornlab-waveguide-mesher runtime status unavailable."
            ),
        },
        {
            "id": "hornlab_metal_bem",
            "name": "HornLab Metal BEM",
            "category": DOCTOR_CATEGORY_REQUIRED if apple_silicon else DOCTOR_CATEGORY_OPTIONAL,
            "requiredFor": (
                "/api/solve on Apple-Silicon hosts"
                if apple_silicon
                else "/api/solve acceleration on Apple-Silicon hosts"
            ),
            "featureImpact": (
                "Metal BEM simulation path is unavailable."
                if metal_status != DOCTOR_STATUS_INSTALLED
                else "Metal BEM solve backend is available."
            ),
            "status": metal_status,
            "available": bool(metal_bem_runtime.get("available")),
            "supported": bool(metal_bem_runtime.get("supported")),
            "ready": metal_ready,
            "version": metal_bem_runtime.get("version"),
            "detail": (
                f"version={metal_bem_runtime.get('version') or 'unknown'} "
                f"backend_available={metal_ready}"
                if metal_bem_runtime
                else str(metal_backend.get("reason") or "hornlab-metal-bem runtime status unavailable.")
            ),
        },
        {
            "id": "hornlab_bempp_bem",
            "name": "HornLab BEMPP BEM",
            "category": DOCTOR_CATEGORY_OPTIONAL if apple_silicon else DOCTOR_CATEGORY_REQUIRED,
            "requiredFor": "/api/solve on non-Apple-Silicon hosts",
            "featureImpact": (
                "BEMPP fallback solves are unavailable."
                if bempp_status != DOCTOR_STATUS_INSTALLED
                else "BEMPP fallback solve backend is available."
            ),
            "status": bempp_status,
            "available": bool(bempp_bem_runtime.get("available")),
            "supported": bool(bempp_bem_runtime.get("supported")),
            "ready": bempp_ready,
            "version": bempp_bem_runtime.get("version"),
            "detail": (
                f"version={bempp_bem_runtime.get('version') or 'unknown'} "
                f"assembly_backend={bempp_backend.get('assemblyBackend') or 'unknown'}"
                if bempp_bem_runtime
                else str(bempp_backend.get("reason") or "hornlab-bempp-bem runtime status unavailable.")
            ),
        },
    ]

    if _is_apple_silicon(platform_payload):
        components.append(
            {
                "id": "metal_release_helper",
                "name": "Metal native helper (release)",
                "category": DOCTOR_CATEGORY_REQUIRED,
                "requiredFor": "/api/solve",
                "featureImpact": (
                    "Fast Metal BEM solves are using the release native helper."
                    if metal_release_helper_ready
                    else "Metal BEM may use a slower debug helper or be unavailable."
                ),
                "status": metal_release_helper_status,
                "available": bool(metal_backend.get("nativeHelperAvailable")),
                "supported": bool(metal_backend.get("supportedPlatform") or metal_backend.get("available")),
                "ready": metal_release_helper_ready,
                "version": None,
                "detail": _metal_release_helper_detail(metal_backend),
            }
        )

    components.append(
        {
            "id": "opencl_runtime",
            "name": "OpenCL runtime",
            "category": DOCTOR_CATEGORY_OPTIONAL,
            "requiredFor": "faster BEMPP solves",
            "featureImpact": (
                "BEMPP solves will use the numba backend, which is slower but complete."
                if opencl_status != DOCTOR_STATUS_INSTALLED
                else "OpenCL acceleration is available for BEMPP solves."
            ),
            "status": opencl_status,
            "available": opencl_available,
            "supported": opencl_available,
            "ready": opencl_available,
            "version": None,
            "detail": str(opencl_runtime.get("reason") or "OpenCL runtime status unavailable."),
        }
    )

    components.append(
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
        }
    )

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
    failed_solve_components: List[str] = []
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
            if required_for.startswith("/api/solve"):
                failed_solve_components.append(component_id)
            if required_for == "/api/mesh/build":
                mesh_build_issues.append(component_id)
        if category == DOCTOR_CATEGORY_OPTIONAL and status != DOCTOR_STATUS_INSTALLED:
            optional_issues.append(component_id)

    ready_by_id = {
        str(component.get("id") or ""): bool(component.get("ready"))
        for component in components
        if isinstance(component, dict)
    }
    solve_ready = bool(
        ready_by_id.get("hornlab_metal_bem") or ready_by_id.get("hornlab_bempp_bem")
    )
    solve_issues = [] if solve_ready else failed_solve_components

    return {
        "requiredReady": len(required_issues) == 0,
        "requiredIssues": required_issues,
        "optionalIssues": optional_issues,
        "solveReady": solve_ready,
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
        metal_backend=snapshot.get("metalBackend", {}),
        bempp_backend=snapshot.get("bemppBackend", {}),
        platform_payload=snapshot.get("platform", {}),
    )
    all_required_ok = all(bool(check.get("ok")) for check in required_checks.values())

    return {
        "generatedAt": snapshot.get("generatedAt"),
        "interpreter": snapshot.get("interpreter"),
        "dependencies": snapshot.get("dependencies"),
        "fastapi": snapshot.get("fastapi"),
        "metalBackend": snapshot.get("metalBackend"),
        "bemppBackend": snapshot.get("bemppBackend"),
        "openclRuntime": snapshot.get("openclRuntime"),
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
        "solveReadiness": {
            "ready": bool(summary.get("solveReady")),
            "backends": {
                "metal": bool((snapshot.get("metalBackend") or {}).get("available"))
                if isinstance(snapshot.get("metalBackend"), dict)
                else False,
                "bempp": bool((snapshot.get("bemppBackend") or {}).get("available"))
                if isinstance(snapshot.get("bemppBackend"), dict)
                else False,
            },
        },
        "dependencies": snapshot.get("dependencies"),
        "metalBackend": snapshot.get("metalBackend"),
        "bemppBackend": snapshot.get("bemppBackend"),
        "openclRuntime": snapshot.get("openclRuntime"),
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

    for check_id in (
        "fastapi",
        "gmsh_python",
        "hornlab_waveguide_mesher",
        "hornlab_metal_bem",
        "hornlab_bempp_bem",
        "metal_release_helper",
    ):
        if check_id not in required:
            continue
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
