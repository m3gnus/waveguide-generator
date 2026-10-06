"""Independent executable, installed-engine and dispatched-device observations."""

from __future__ import annotations

import hashlib
import importlib
from importlib import metadata
import json
from pathlib import Path
import subprocess
from typing import Any

from server.solver.beat_runtime.assets import engine_assets
from server.solver.beat_runtime.identity import engine_fingerprint


def _run(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=60).stdout.strip()


def _installed_source_revision(package_path: Path, source: Path) -> str:
    """Compare every tracked package file and refuse extra installed payloads."""
    source = source.resolve(strict=True)
    prefix = "src/beat_engine/"
    if _run(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"]):
        raise ValueError("Engine source tree has uncommitted changes")
    names = _run(["git", "-C", str(source), "ls-files", prefix]).splitlines()
    if prefix + "__init__.py" not in names:
        raise ValueError("Source tree does not track the engine package")
    expected = {name.removeprefix(prefix) for name in names}
    installed = {p.relative_to(package_path).as_posix() for p in package_path.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    if installed != expected:
        raise ValueError("Installed/source engine file inventories differ")
    for name in names:
        if (source / name).read_bytes() != (package_path / name.removeprefix(prefix)).read_bytes():
            raise ValueError(f"Installed/source engine bytes differ: {name}")
    return _run(["git", "-C", str(source), "rev-parse", "HEAD"])


def verify_runtime(julia_executable: str, backend: str, *, engine_source: Path | None = None) -> dict[str, Any]:
    """Run the selected binary and inspect the loaded distribution, never provision."""
    executable = Path(julia_executable).resolve(strict=True)
    version = _run([str(executable), "--startup-file=no", "--version"])
    if not version.startswith("julia version "):
        raise ValueError("Executable did not identify itself as Julia")
    binary_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    package = importlib.import_module("beat_engine")
    distribution = metadata.distribution("beat-engine")
    package_path = Path(package.__file__).resolve().parent
    assets = engine_assets(backend)
    if package_path != assets.root:
        raise ValueError("Loaded engine and selected assets differ")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    installed = not direct_url.get("dir_info", {}).get("editable", False)
    revision = direct_url.get("vcs_info", {}).get("commit_id", "")
    if installed and (revision or engine_source):
        recorded_package = Path(distribution.locate_file("beat_engine/__init__.py")).resolve().parent
        if recorded_package != package_path:
            raise ValueError("Loaded engine is not the recorded installed distribution")
    matched_source = installed and engine_source is not None
    if matched_source:
        matched_revision = _installed_source_revision(package_path, engine_source)
        if revision and matched_revision != revision:
            raise ValueError("Installed metadata/source revisions differ")
        revision = matched_revision
    if not revision or not installed:
        # Refuse an unrelated parent repository (for example a venv inside WG).
        _run(["git", "-C", str(package_path), "ls-files", "--error-unmatch", str(package_path / "__init__.py")])
        revision = _run(["git", "-C", str(package_path), "rev-parse", "HEAD"])
        if _run(["git", "-C", str(package_path), "status", "--porcelain", "--untracked-files=all"]):
            raise ValueError("Engine source tree has uncommitted changes")
        installed = False
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Engine has no exact observed revision")
    script = Path(__file__).with_name("assert_metal_device.jl")
    if backend == "metal":
        output = _run([str(executable), "--startup-file=no", f"--project={assets.project}", str(script)])
        if "WG_KERNEL_VERIFIED=true" not in output.splitlines():
            raise ValueError("Metal requires a verified dispatched device kernel")
        device = next((line.removeprefix("WG_DEVICE=") for line in output.splitlines()
                       if line.startswith("WG_DEVICE=")), "")
    else:
        device = _run([str(executable), "--startup-file=no", "-e", "print(Sys.CPU_NAME)"])
    if not device:
        raise ValueError("Independent device identity is missing")
    return {"backend": backend, "julia_executable": str(executable), "julia_version": version,
            "julia_sha256": binary_hash, "engine_path": str(package_path),
            "engine_revision": revision,
            "engine_revision_status": "attested" if installed and not matched_source else "observed",
            "engine_revision_source": "installed_source_byte_match" if matched_source else "installed_vcs_metadata" if installed else "clean_source_git",
            "engine_distribution": "beat-engine",
            "engine_fingerprint": engine_fingerprint(assets),
            "artifact_kind": "installed" if installed else "source",
            "device_class": "gpu" if backend == "metal" else "cpu", "device_name": device,
            "device_kernel_verified": backend == "metal", "project": str(assets.project),
            "solver_script": str(assets.system_solver)}
