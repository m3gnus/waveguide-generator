"""Copy committed source bytes and their identity as one vendoring operation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from server.platform.json_file import write_json_atomic


def vendor_source(source: Path, revision: str, source_path: str, destination: Path,
                  manifest_path: Path, *, repository: str | None = None, note: str | None = None) -> dict:
    commit = subprocess.check_output(
        ["git", "-C", str(source.resolve()), "rev-parse", "--verify", f"{revision}^{{commit}}"], text=True,
    ).strip()
    data = subprocess.check_output(["git", "-C", str(source.resolve()), "show", f"{commit}:{source_path}"])
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update(commit=commit, path=source_path, sha256=hashlib.sha256(data).hexdigest())
    if repository is not None:
        manifest["repository"] = repository
    manifest["note"] = note or f"Vendored byte for byte from source commit {commit[:7]}."
    destination.write_bytes(data)
    write_json_atomic(manifest_path, manifest)
    return manifest
