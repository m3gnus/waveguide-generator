"""Helpers for bounded solve readiness evidence used by runtime preflight/doctor."""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict

READINESS_SCHEMA_VERSION = 1
READINESS_PROBE_ID = "tritonia_bounded_solve.v1"


def _default_readiness_record_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "output" / "runtime" / "bounded_solve_validation.json"


def resolve_readiness_record_path() -> Path:
    raw = str(os.environ.get("WG_BOUNDED_SOLVE_RECORD_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _default_readiness_record_path()


def _current_host_fingerprint() -> Dict[str, str]:
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "python_executable": sys.executable,
    }


def _bool_value(payload: Dict[str, Any], key: str) -> bool:
    return bool(payload.get(key))


def write_bounded_solve_readiness_record(record: Dict[str, Any]) -> Path:
    target = resolve_readiness_record_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return target


def read_bounded_solve_readiness(*, preferred_mode: str = "auto") -> Dict[str, Any]:
    target = resolve_readiness_record_path()
    guidance = (
        "Run `cd server && python3 scripts/benchmark_tritonia.py --freq 1000 --device auto "
        "--precision single --timeout 30` (without --no-solve) to record bounded solve evidence."
    )

    if not target.exists():
        return {
            "status": "missing",
            "ready": False,
            "detail": f"No bounded solve validation record found at {target}. {guidance}",
            "path": str(target),
            "guidance": guidance,
        }

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "invalid",
            "ready": False,
            "detail": f"Failed to parse bounded solve validation record at {target}: {exc}",
            "path": str(target),
            "guidance": guidance,
        }

    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "ready": False,
            "detail": f"Bounded solve validation record at {target} is not a JSON object.",
            "path": str(target),
            "guidance": guidance,
        }

    schema_version = int(payload.get("schemaVersion") or 0)
    probe = str(payload.get("probe") or "").strip()
    if schema_version != READINESS_SCHEMA_VERSION or probe != READINESS_PROBE_ID:
        return {
            "status": "invalid",
            "ready": False,
            "detail": (
                f"Bounded solve validation record at {target} is incompatible "
                f"(schemaVersion={schema_version}, probe={probe or 'none'})."
            ),
            "path": str(target),
            "guidance": guidance,
        }

    host = payload.get("host") if isinstance(payload.get("host"), dict) else {}
    current_host = _current_host_fingerprint()
    host_mismatch = (
        str(host.get("system") or "") != str(current_host["system"])
        or str(host.get("machine") or "") != str(current_host["machine"])
        or str(host.get("python_executable") or "") != str(current_host["python_executable"])
    )
    if host_mismatch:
        return {
            "status": "stale_host",
            "ready": False,
            "detail": (
                "Bounded solve validation record was generated on a different host/runtime "
                f"(recorded={host}, current={current_host}). {guidance}"
            ),
            "path": str(target),
            "generatedAt": payload.get("generatedAt"),
            "guidance": guidance,
        }

    selected_mode = str(payload.get("selected_mode") or "").strip().lower()
    normalized_mode = str(preferred_mode or "auto").strip().lower() or "auto"
    if normalized_mode != "auto" and selected_mode and selected_mode != normalized_mode:
        return {
            "status": "mode_mismatch",
            "ready": False,
            "detail": (
                f"Bounded solve validation was recorded for selected_mode={selected_mode}, "
                f"but preferred_mode={normalized_mode} was requested."
            ),
            "path": str(target),
            "generatedAt": payload.get("generatedAt"),
            "selected_mode": selected_mode or None,
            "guidance": guidance,
        }

    attempted = _bool_value(payload, "attempted")
    success = _bool_value(payload, "success")
    failure = str(payload.get("failure") or "").strip()
    generated_at = str(payload.get("generatedAt") or "").strip() or None
    base = {
        "path": str(target),
        "generatedAt": generated_at,
        "requested_mode": str(payload.get("requested_mode") or "").strip() or None,
        "selected_mode": selected_mode or None,
        "device_name": str(payload.get("device_name") or "").strip() or None,
        "attempted": attempted,
        "success": success,
        "runtime_available": _bool_value(payload, "runtime_available"),
        "mesh_prep_success": _bool_value(payload, "mesh_prep_success"),
        "failure": failure or None,
        "guidance": guidance,
    }

    if attempted and success:
        return {
            **base,
            "status": "validated",
            "ready": True,
            "detail": (
                f"Bounded solve validation passed at {generated_at or 'unknown time'} "
                f"(selected_mode={selected_mode or 'unknown'}, device={base['device_name'] or 'unknown'})."
            ),
        }

    if attempted and not success:
        failure_detail = failure or "bounded solve attempt failed without an explicit error detail."
        return {
            **base,
            "status": "failed",
            "ready": False,
            "detail": (
                f"Bounded solve validation failed at {generated_at or 'unknown time'}: {failure_detail}"
            ),
        }

    return {
        **base,
        "status": "unvalidated",
        "ready": False,
        "detail": (
            "Bounded solve validation record exists but has no completed solve attempt. "
            + guidance
        ),
    }
