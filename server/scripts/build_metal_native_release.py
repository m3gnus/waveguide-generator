"""Build the hornlab-metal-bem native helper in release mode when available."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _payload(**values: Any) -> dict[str, Any]:
    return {
        "available": False,
        "built": False,
        "skipped": False,
        "reason": None,
        "helperPath": None,
        "helperSource": None,
        "helperBuild": None,
        **values,
    }


def _print(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if payload.get("built"):
        print(f"Metal native release helper ready: {payload.get('helperPath')}")
    elif payload.get("available"):
        print(f"Metal native helper already ready: {payload.get('helperPath')}")
    elif payload.get("skipped"):
        print(f"Metal native release helper skipped: {payload.get('reason')}")
    else:
        print(f"Metal native release helper unavailable: {payload.get('reason')}")


def _helper_build(path: Path | None) -> str | None:
    if path is None:
        return None
    parts = path.parts
    if "release" in parts:
        return "release"
    if "debug" in parts:
        return "debug"
    return "custom"


def _status_payload(status: Any, *, built: bool = False) -> dict[str, Any]:
    helper_path = getattr(status, "helper_executable_path", None)
    helper = Path(helper_path) if helper_path else None
    return _payload(
        available=bool(getattr(status, "available", False)),
        built=built,
        helperPath=str(helper) if helper else None,
        helperSource=getattr(status, "helper_source", None),
        helperBuild=_helper_build(helper),
    )


def build_release_helper(*, json_output: bool = False) -> int:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        _print(
            _payload(skipped=True, reason="Metal native helper is only used on Apple Silicon."),
            json_output=json_output,
        )
        return 0

    try:
        import hornlab_metal_bem
        from hornlab_metal_bem.metal.native import discover_native_runtime
    except Exception as exc:
        _print(
            _payload(reason=f"hornlab-metal-bem is unavailable: {exc}"),
            json_output=json_output,
        )
        return 1

    probe_error = None
    try:
        status = discover_native_runtime(run_smoke_test=True)
    except Exception as exc:
        probe_error = str(exc)
    else:
        current = _status_payload(status)
        if current.get("helperBuild") == "release":
            _print(current, json_output=json_output)
            return 0

    package_dir = Path(hornlab_metal_bem.__file__).resolve().parent / "metal" / "native_helper"
    if not (package_dir / "Package.swift").is_file():
        reason = f"native helper Package.swift missing under {package_dir}"
        if probe_error:
            reason = f"{reason}; initial native runtime probe failed: {probe_error}"
        _print(
            _payload(reason=reason),
            json_output=json_output,
        )
        return 1

    swift = shutil.which("swift")
    if not swift:
        reason = "swift executable is unavailable."
        if probe_error:
            reason = f"{reason} Initial native runtime probe failed: {probe_error}"
        _print(
            _payload(reason=reason),
            json_output=json_output,
        )
        return 1

    try:
        subprocess.run([swift, "build", "-c", "release"], cwd=package_dir, check=True)
    except subprocess.CalledProcessError as exc:
        _print(
            _payload(reason=f"swift release build failed with exit code {exc.returncode}"),
            json_output=json_output,
        )
        return int(exc.returncode) or 1

    try:
        next_status = discover_native_runtime(run_smoke_test=True)
    except Exception as exc:
        _print(
            _payload(reason=f"release build completed but native runtime probe failed: {exc}"),
            json_output=json_output,
        )
        return 1
    payload = _status_payload(next_status, built=True)
    if payload.get("helperBuild") != "release":
        payload["reason"] = "release build completed but runtime did not select the release helper."
        _print(payload, json_output=json_output)
        return 1

    _print(payload, json_output=json_output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()
    return build_release_helper(json_output=bool(args.json))


if __name__ == "__main__":
    raise SystemExit(main())
