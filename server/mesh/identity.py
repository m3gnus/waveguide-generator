"""Installed mesher identity shared by geometry and mesh cache keys."""

from __future__ import annotations

from functools import lru_cache
import hashlib
from importlib.util import find_spec
from pathlib import Path
from uuid import uuid4

from server.integration.installed import measure_installed_commit


@lru_cache(maxsize=1)
def mesher_identity() -> str:
    """Measure once per process, matching the installed-provenance contract.

    Prefer pip's resolved Git commit, never the declared pin or version. For
    wheel/editable installs, hash the package's source and data instead. Such
    installs must also be restarted after changes, as Python keeps imported
    modules in memory. If measurement fails, isolate caches to this process
    rather than reusing persistent artifacts under a shared unknown identity.
    """

    commit = measure_installed_commit("hornlab-waveguide-mesher")
    if commit is not None:
        return f"git:{commit}"
    try:
        spec = find_spec("hornlab_mesher")
        if spec is not None and spec.origin is not None:
            root = Path(spec.origin).parent
            files = sorted(
                path for path in root.rglob("*")
                if path.is_file()
                and not {"__pycache__", ".git"}.intersection(path.relative_to(root).parts)
                and path.suffix not in {".pyc", ".pyo"}
            )
            if files:
                digest = hashlib.sha256()
                for path in files:
                    name = path.relative_to(root).as_posix().encode("utf-8")
                    content = path.read_bytes()
                    digest.update(len(name).to_bytes(4, "big"))
                    digest.update(name)
                    digest.update(len(content).to_bytes(8, "big"))
                    digest.update(content)
                return f"sha256:{digest.hexdigest()}"
    except (OSError, ImportError, ValueError):
        pass
    return f"unmeasured:{uuid4().hex}"
